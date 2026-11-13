"""Core Research Agent Controller and Policy Dispatcher.

Incorporates all amended specifications:
  - #1: Fair all_cloud baseline (K=1, iterative verifier feedback, stop on pass)
  - #2: Cheap vs expensive verifier screening
  - #3: Dev feedback in-loop; final held-out evaluation post-run recorded to ledger
  - #4: Consultation ladder runs 'hint' and 'solve' by default; 'solve' outputs directly sent to verifier
  - #5: Format / syntax / import failure signatures retry locally without escalating to cloud
  - #6: no_advice_retry policy for attribution
  - #7: Lessons library default disabled, isolated per run by default, provisional -> validated
  - #8: Weak model backend configurable as local vLLM or cloud_small
  - #9: Separate ledger columns (cloud_cny, gpu_s, gpu_cny, wall_s); alpha converted from CNY/min
  - #10: Programmatically generated state cards without LLM calls
  - #11: Candidate normalization and SHA256 hash deduplication; K schedule from config [1, 2, 4]
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from pathlib import Path
from typing import Any

from tierlab.config import compute_config_hash
from tierlab.exceptions import BudgetExceededError
from tierlab.gateway import ModelGateway
from tierlab.ledger import DecisionLedger
from tierlab.lessons import LessonStore
from tierlab.prompts import (
    RUNG_MAX_TOKENS,
    build_consultation_messages,
    build_local_prompt,
)
from tierlab.state_card import format_and_truncate_state_card
from tierlab.tasks.base import ResearchTask
from tierlab.types import (
    ActionType,
    Budget,
    Candidate,
    ConsultationRung,
    ControllerConfig,
    LedgerEntry,
    PolicyType,
    VerificationResult,
    alpha_to_cny_per_second,
)

SYNTAX_OR_FORMAT_SIGNATURES = {
    "format",
    "syntax",
    "import",
    "crash",
    "timeout",
    "json_parse_error",
    "output_parse_error",
    "invalid_format",
    "invalid_item_format",
    "schema_missing_answers",
    "schema_missing_key",
    "empty_answers",
    "wrong_circle_count",
    "non_positive_radius",
    "syntax_or_output_error",
    "syntax_or_runtime_error",
}


def is_syntax_or_format_failure(signature: str | None) -> bool:
    if not signature:
        return False
    lowered = signature.lower()
    for s in SYNTAX_OR_FORMAT_SIGNATURES:
        if s in lowered:
            return True
    return False


def hash_candidate(content: str) -> str:
    """Normalize whitespace and compute SHA256 candidate fingerprint for deduplication."""
    norm = re.sub(r"\s+", " ", content).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


class ResearchController:
    """Orchestrates the research loop balancing local and cloud compute."""

    def __init__(
        self,
        task: ResearchTask,
        gateway: ModelGateway,
        ledger: DecisionLedger,
        lessons: LessonStore | None = None,
        config: ControllerConfig | None = None,
        budget: Budget | None = None,
        policy: str | PolicyType = PolicyType.TIERLAB,
        run_id: str = "run_01",
        seed: int = 0,
    ) -> None:
        self.task = task
        self.gateway = gateway
        self.ledger = ledger
        self.config = config or ControllerConfig()
        self.budget = budget or Budget()
        self.policy = PolicyType(policy)
        self.run_id = run_id
        self.seed = seed

        # Lessons store setup (#7)
        if lessons:
            self.lessons = lessons
        else:
            storage_path = (
                ledger.file_path.parent / "lessons.json"
                if not self.config.share_lessons_across_runs
                else Path("lessons_shared.json")
            )
            self.lessons = LessonStore(storage_path=storage_path, share_across_runs=self.config.share_lessons_across_runs)

        # K schedule setup (#11)
        self.k_schedule = self.config.k_schedule or [1, 2, 4]
        self.k_index = 0
        self.current_k = self.k_schedule[self.k_index]

        # Runtime trackers
        self.step: int = 0
        self.consecutive_failures: int = 0
        self.has_widened_in_stagnation: bool = False
        self.seen_candidate_hashes: set[str] = set()

        # Candidates and feedback
        self.current_best_candidate: Candidate = self.task.initial_candidate()
        self.current_best_score: float = float(self.current_best_candidate.metadata.get("score", 0.0))
        self.recent_results: list[VerificationResult] = []

        # Consultation rungs setup (#4: hint and solve only by default)
        self.rungs: list[ConsultationRung] = [ConsultationRung.HINT, ConsultationRung.SOLVE]
        if self.config.enable_diagnose_rung:
            self.rungs.insert(1, ConsultationRung.DIAGNOSE)
        if self.config.enable_replan_rung:
            self.rungs.insert(-1, ConsultationRung.REPLAN)
        self.current_rung_idx: int = 0

        self.last_consultation_action: str | None = None
        self.last_consultation_guidance: str | None = None
        self.last_consultation_cost: float = 0.0
        self.last_provisional_lesson_id: str | None = None

        # Convert alpha from CNY/min to CNY/s (single place of conversion #9)
        self.alpha_cny_per_s = alpha_to_cny_per_second(self.config.alpha_cny_per_min)

        conf_dict = {
            "policy": self.policy.value,
            "seed": self.seed,
            "controller": self.config.model_dump(),
            "budget": self.budget.model_dump(),
            "k_schedule": self.k_schedule,
            "alpha_cny_per_s": self.alpha_cny_per_s,
        }
        self.config_hash = compute_config_hash(conf_dict)

    def _estimate_action_price(self, action: str) -> float:
        """Estimate price = cloud_cny + (rho_gpu + alpha) * duration."""
        rho_gpu = self.gateway.config.rho_gpu_cny_per_s
        alpha = self.alpha_cny_per_s

        if action == "widen":
            est_gpu_s = 1.5
            return (rho_gpu + alpha) * est_gpu_s
        elif action == "hint":
            est_cloud = (150 * self.gateway.config.cloud_model.pricing.completion_cny_per_m) / 1e6
            return est_cloud + (rho_gpu + alpha) * 2.0
        elif action == "solve":
            est_cloud = (1500 * self.gateway.config.cloud_model.pricing.completion_cny_per_m) / 1e6
            return est_cloud + (rho_gpu + alpha) * 4.0
        return 0.0

    async def run(self) -> dict[str, Any]:
        """Execute research loop with in-loop dev feedback, followed by post-run held-out evaluation."""
        start_time = time.perf_counter()

        # Step 0: Record initial candidate hash and dev evaluation
        init_hash = hash_candidate(self.current_best_candidate.content)
        self.seen_candidate_hashes.add(init_hash)

        init_results = self.task.run_ladder(self.current_best_candidate)
        if init_results:
            self.recent_results = init_results
            if init_results[-1].valid:
                self.current_best_score = max(self.current_best_score, init_results[-1].score)

        static_plan: str | None = None
        if self.policy == PolicyType.STATIC_SPLIT:
            static_plan = await self._execute_static_split_plan()

        while self.step < self.budget.max_steps:
            elapsed_wall = time.perf_counter() - start_time
            if elapsed_wall >= self.budget.max_wall_clock_s:
                self._record_stop("max_wall_clock_exceeded")
                break

            self.step += 1

            if self.policy == PolicyType.ALL_CLOUD:
                stop = await self._step_all_cloud()
            elif self.policy == PolicyType.ALL_LOCAL:
                stop = await self._step_all_local()
            elif self.policy == PolicyType.CASCADE:
                stop = await self._step_cascade()
            elif self.policy == PolicyType.STATIC_SPLIT:
                stop = await self._step_static_split(static_plan)
            elif self.policy == PolicyType.NO_ADVICE_RETRY:
                stop = await self._step_no_advice_retry()
            else:
                stop = await self._step_tierlab()

            if stop:
                break

        # Post-loop held-out final evaluation (#3):
        # Executed once after run completes, written directly to ledger, never sent to models!
        final_eval = self.task.run_final_evaluation(self.current_best_candidate)
        if final_eval:
            self.ledger.record(
                LedgerEntry(
                    run=self.run_id,
                    step=self.step + 1,
                    action=ActionType.FINAL_EVAL,
                    trigger="held_out_final_evaluation",
                    cloud_in=0,
                    cloud_out=0,
                    cloud_cny=0.0,
                    gpu_s=0.0,
                    gpu_cny=0.0,
                    wall_s=round(final_eval.elapsed_s, 3),
                    score_before=self.current_best_score,
                    score_after=final_eval.score,
                    verified=final_eval.valid,
                    duplicate=False,
                    seed=self.seed,
                    policy=self.policy.value,
                    config_hash=self.config_hash,
                    extra={
                        "final_feedback": final_eval.feedback,
                        "evidence": final_eval.evidence,
                    },
                )
            )

        return self.ledger.summary()

    async def _step_tierlab(self) -> bool:
        """Main TierLab policy step."""
        state_card_raw = self.task.state_card(self.current_best_candidate, self.recent_results)
        state_card_res = format_and_truncate_state_card(state_card_raw)

        # 1. Local K-way generation
        effective_k = 1 if self.config.no_widen else self.current_k
        cands, wall_s = await self._generate_weak_k(
            k=effective_k,
            state_card=state_card_res.content,
            cloud_guidance=self.last_consultation_guidance,
        )

        # 2. Evaluation with deduplication and cheap-first screening (#2, #11)
        best_cand, best_score, best_results, any_duplicates = self._evaluate_candidate_batch(cands)
        gpu_cny = round(wall_s * self.gateway.config.rho_gpu_cny_per_s, 6)

        score_before = self.current_best_score
        delta = best_score - score_before

        if delta > 0:
            # Verified improvement
            self.current_best_candidate = best_cand
            self.current_best_score = best_score
            self.recent_results = best_results
            self.consecutive_failures = 0
            self.has_widened_in_stagnation = False
            self.current_rung_idx = 0

            # Halve K: move left in k_schedule
            if not self.config.no_widen and self.k_index > 0:
                self.k_index = max(0, self.k_index - 1)
                self.current_k = self.k_schedule[self.k_index]

            # Validate provisional lesson if one was pending (#7)
            admitted_id: str | None = None
            if self.config.enable_lessons and self.last_provisional_lesson_id:
                validated = self.lessons.validate_lesson(self.last_provisional_lesson_id, delta)
                if validated:
                    admitted_id = validated.lesson_id

            self.ledger.record(
                LedgerEntry(
                    run=self.run_id,
                    step=self.step,
                    action=ActionType.PROPOSE_LOCAL,
                    trigger="improvement",
                    cloud_in=0,
                    cloud_out=0,
                    cloud_cny=0.0,
                    gpu_s=round(wall_s, 3),
                    gpu_cny=gpu_cny,
                    wall_s=round(wall_s, 3),
                    score_before=score_before,
                    score_after=best_score,
                    verified=True,
                    duplicate=any_duplicates,
                    lesson_id=admitted_id,
                    seed=self.seed,
                    policy=self.policy.value,
                    config_hash=self.config_hash,
                )
            )

            self.last_consultation_action = None
            self.last_consultation_guidance = None
            self.last_consultation_cost = 0.0
            self.last_provisional_lesson_id = None

            if self.current_best_score >= 1.0:
                self._record_stop("target_score_reached")
                return True
            return False

        # No improvement: Stagnation handling
        self.consecutive_failures += 1
        sig = self.task.failure_signature(best_results)

        # 3. Rule #5: Syntax, format, or import failures only retry locally
        if is_syntax_or_format_failure(sig):
            self.ledger.record(
                LedgerEntry(
                    run=self.run_id,
                    step=self.step,
                    action=ActionType.PROPOSE_LOCAL,
                    trigger=f"format_syntax_local_retry({sig})",
                    cloud_in=0,
                    cloud_out=0,
                    cloud_cny=0.0,
                    gpu_s=round(wall_s, 3),
                    gpu_cny=gpu_cny,
                    wall_s=round(wall_s, 3),
                    score_before=score_before,
                    score_after=score_before,
                    verified=False,
                    duplicate=any_duplicates,
                    seed=self.seed,
                    policy=self.policy.value,
                    config_hash=self.config_hash,
                )
            )
            return False

        # 4. Check lessons library if enabled (#7)
        if self.config.enable_lessons:
            lesson = self.lessons.match(self.task.task_class, sig)
            if lesson:
                self.ledger.record(
                    LedgerEntry(
                        run=self.run_id,
                        step=self.step,
                        action=ActionType.LESSON_HIT,
                        trigger=f"lesson_match({sig})",
                        cloud_in=0,
                        cloud_out=0,
                        cloud_cny=0.0,
                        gpu_s=0.0,
                        gpu_cny=0.0,
                        wall_s=0.0,
                        score_before=score_before,
                        score_after=score_before,
                        verified=False,
                        lesson_id=lesson.lesson_id,
                        seed=self.seed,
                        policy=self.policy.value,
                        config_hash=self.config_hash,
                        extra={"advice": lesson.advice},
                    )
                )
                self.last_consultation_guidance = lesson.advice
                return False

        # 5. Check widening along k_schedule [1, 2, 4] (#11)
        price_widen = self._estimate_action_price("widen")
        price_hint = self._estimate_action_price("hint")
        if (
            not self.config.no_widen
            and not self.has_widened_in_stagnation
            and self.k_index < len(self.k_schedule) - 1
            and price_widen < price_hint
        ):
            self.has_widened_in_stagnation = True
            old_k = self.current_k
            self.k_index += 1
            self.current_k = self.k_schedule[self.k_index]

            self.ledger.record(
                LedgerEntry(
                    run=self.run_id,
                    step=self.step,
                    action=ActionType.WIDEN,
                    trigger=f"stagnation({self.consecutive_failures})",
                    cloud_in=0,
                    cloud_out=0,
                    cloud_cny=0.0,
                    gpu_s=0.0,
                    gpu_cny=0.0,
                    wall_s=0.0,
                    score_before=score_before,
                    score_after=score_before,
                    verified=False,
                    seed=self.seed,
                    policy=self.policy.value,
                    config_hash=self.config_hash,
                    extra={"k_before": old_k, "k_after": self.current_k},
                )
            )
            return False

        # 6. Consultation Ladder (hint -> solve) (#4)
        if self.current_rung_idx >= len(self.rungs):
            self._record_stop("consultation_ladder_exhausted")
            return True

        current_rung = self.rungs[self.current_rung_idx]
        rung_price = self._estimate_action_price(current_rung.value)

        remaining_budget = self.budget.cloud_budget_cny - self.gateway.spent_cloud_cny
        if remaining_budget < rung_price or remaining_budget <= 0:
            self._record_stop("insufficient_cloud_budget")
            return True

        if self._check_low_roi_stop_rule():
            self._record_stop("low_consultation_roi_threshold")
            return True

        try:
            consult_record = await self._execute_consultation(current_rung, state_card_res.content)
        except BudgetExceededError:
            self._record_stop("cloud_budget_exceeded_exception")
            return True

        self.current_rung_idx += 1

        # Per amendment #4: 'solve' outputs candidate directly to verifier!
        if current_rung == ConsultationRung.SOLVE:
            solve_cand = Candidate(
                id=f"cand_cloud_solve_{self.step}",
                content=consult_record.content,
                metadata={"origin": "cloud_solve", "step": self.step},
            )
            h = hash_candidate(solve_cand.content)
            is_dup = h in self.seen_candidate_hashes
            self.seen_candidate_hashes.add(h)

            solve_score = score_before
            solve_verified = False
            if not is_dup:
                results = self.task.run_ladder(solve_cand)
                if results and results[-1].valid and results[-1].score > score_before:
                    self.current_best_candidate = solve_cand
                    self.current_best_score = results[-1].score
                    solve_score = self.current_best_score
                    solve_verified = True
                    self.recent_results = results

            self.ledger.record(
                LedgerEntry(
                    run=self.run_id,
                    step=self.step,
                    action=ActionType.SOLVE,
                    trigger="escalation_solve",
                    cloud_in=consult_record.prompt_tokens,
                    cloud_out=consult_record.completion_tokens,
                    cloud_cny=consult_record.cost_cny,
                    gpu_s=0.0,
                    gpu_cny=0.0,
                    wall_s=round(consult_record.wall_clock_s, 3),
                    score_before=score_before,
                    score_after=solve_score,
                    verified=solve_verified,
                    duplicate=is_dup,
                    seed=self.seed,
                    policy=self.policy.value,
                    config_hash=self.config_hash,
                )
            )
            return False

        # Hint action
        self.last_consultation_action = current_rung.value
        self.last_consultation_guidance = consult_record.content
        self.last_consultation_cost = consult_record.cost_cny

        # Record provisional lesson (#7)
        if self.config.enable_lessons:
            prov = self.lessons.create_provisional_lesson(
                task_class=self.task.task_class,
                signature=sig,
                rung=current_rung.value,
                advice=consult_record.content,
                condition=f"task={self.task.task_class}, score={score_before:.3f}",
                cost_cny=consult_record.cost_cny,
            )
            self.last_provisional_lesson_id = prov.lesson_id

        self.ledger.record(
            LedgerEntry(
                run=self.run_id,
                step=self.step,
                action=current_rung.value,
                trigger=f"escalation(rung_{current_rung.value})",
                cloud_in=consult_record.prompt_tokens,
                cloud_out=consult_record.completion_tokens,
                cloud_cny=consult_record.cost_cny,
                gpu_s=0.0,
                gpu_cny=0.0,
                wall_s=round(consult_record.wall_clock_s, 3),
                score_before=score_before,
                score_after=score_before,
                verified=False,
                seed=self.seed,
                policy=self.policy.value,
                config_hash=self.config_hash,
                extra={"guidance": consult_record.content[:100]},
            )
        )
        return False

    async def _step_all_cloud(self) -> bool:
        """Fair all_cloud baseline (#1): 1 candidate per step, iterative verifier feedback, stop on pass."""
        state_card_raw = self.task.state_card(self.current_best_candidate, self.recent_results)
        state_card_res = format_and_truncate_state_card(state_card_raw)

        messages = build_consultation_messages(
            rung=ConsultationRung.SOLVE,
            state_card=state_card_res.content,
        )

        try:
            record = await self.gateway.call(
                target="cloud",
                messages=messages,
                max_tokens=RUNG_MAX_TOKENS[ConsultationRung.SOLVE],
                temperature=0.7,
                task_class=self.task.task_class,
            )
        except BudgetExceededError:
            self._record_stop("cloud_budget_exceeded_exception")
            return True

        candidate = Candidate(
            id=f"cand_cloud_{self.step}",
            content=record.content,
            metadata={"step": self.step, "origin": "cloud"},
        )

        h = hash_candidate(candidate.content)
        is_dup = h in self.seen_candidate_hashes
        self.seen_candidate_hashes.add(h)

        score_before = self.current_best_score
        score_after = score_before
        verified = False

        if not is_dup:
            results = self.task.run_ladder(candidate)
            self.recent_results = results
            if results and results[-1].valid and len(results) == len(self.task.ladder):
                if results[-1].score > self.current_best_score:
                    self.current_best_candidate = candidate
                    self.current_best_score = results[-1].score
                    score_after = self.current_best_score
                    verified = True

        self.ledger.record(
            LedgerEntry(
                run=self.run_id,
                step=self.step,
                action=ActionType.CLOUD_PROPOSE,
                trigger="all_cloud_iterative",
                cloud_in=record.prompt_tokens,
                cloud_out=record.completion_tokens,
                cloud_cny=record.cost_cny,
                gpu_s=0.0,
                gpu_cny=0.0,
                wall_s=round(record.wall_clock_s, 3),
                score_before=score_before,
                score_after=score_after,
                verified=verified,
                duplicate=is_dup,
                seed=self.seed,
                policy=self.policy.value,
                config_hash=self.config_hash,
            )
        )

        # Fair stop on pass (#1)
        if self.current_best_score >= 1.0:
            self._record_stop("all_cloud_target_passed")
            return True
        return False

    async def _step_no_advice_retry(self) -> bool:
        """Amendment #6: At trigger points where advice would be sought, grant local model

        an identical extra round with zero consultation for pure attribution.
        """
        state_card_raw = self.task.state_card(self.current_best_candidate, self.recent_results)
        state_card_res = format_and_truncate_state_card(state_card_raw)

        cands, wall_s = await self._generate_weak_k(
            k=self.current_k,
            state_card=state_card_res.content,
            cloud_guidance=None,
        )
        best_cand, best_score, best_results, any_dups = self._evaluate_candidate_batch(cands)
        gpu_cny = round(wall_s * self.gateway.config.rho_gpu_cny_per_s, 6)

        score_before = self.current_best_score
        if best_score > self.current_best_score:
            self.current_best_candidate = best_cand
            self.current_best_score = best_score
            self.recent_results = best_results

        self.ledger.record(
            LedgerEntry(
                run=self.run_id,
                step=self.step,
                action=ActionType.NO_ADVICE_RETRY,
                trigger="attribution_local_retry",
                cloud_in=0,
                cloud_out=0,
                cloud_cny=0.0,
                gpu_s=round(wall_s, 3),
                gpu_cny=gpu_cny,
                wall_s=round(wall_s, 3),
                score_before=score_before,
                score_after=self.current_best_score,
                verified=(self.current_best_score > score_before),
                duplicate=any_dups,
                seed=self.seed,
                policy=self.policy.value,
                config_hash=self.config_hash,
            )
        )
        return False

    async def _step_all_local(self) -> bool:
        state_card_raw = self.task.state_card(self.current_best_candidate, self.recent_results)
        state_card_res = format_and_truncate_state_card(state_card_raw)

        cands, wall_s = await self._generate_weak_k(k=self.current_k, state_card=state_card_res.content)
        best_cand, best_score, best_results, any_dups = self._evaluate_candidate_batch(cands)
        gpu_cny = round(wall_s * self.gateway.config.rho_gpu_cny_per_s, 6)

        score_before = self.current_best_score
        if best_score > self.current_best_score:
            self.current_best_candidate = best_cand
            self.current_best_score = best_score
            self.recent_results = best_results

        self.ledger.record(
            LedgerEntry(
                run=self.run_id,
                step=self.step,
                action=ActionType.PROPOSE_LOCAL,
                trigger="all_local_step",
                cloud_in=0,
                cloud_out=0,
                cloud_cny=0.0,
                gpu_s=round(wall_s, 3),
                gpu_cny=gpu_cny,
                wall_s=round(wall_s, 3),
                score_before=score_before,
                score_after=self.current_best_score,
                verified=(self.current_best_score > score_before),
                duplicate=any_dups,
                seed=self.seed,
                policy=self.policy.value,
                config_hash=self.config_hash,
            )
        )
        return False

    async def _step_cascade(self) -> bool:
        state_card_raw = self.task.state_card(self.current_best_candidate, self.recent_results)
        state_card_res = format_and_truncate_state_card(state_card_raw)

        if self.consecutive_failures < self.config.cascade_fail_threshold:
            cands, wall_s = await self._generate_weak_k(k=1, state_card=state_card_res.content)
            best_cand, best_score, best_results, any_dups = self._evaluate_candidate_batch(cands)
            gpu_cny = round(wall_s * self.gateway.config.rho_gpu_cny_per_s, 6)

            score_before = self.current_best_score
            if best_score > self.current_best_score:
                self.current_best_candidate = best_cand
                self.current_best_score = best_score
                self.recent_results = best_results
                self.consecutive_failures = 0
            else:
                self.consecutive_failures += 1

            self.ledger.record(
                LedgerEntry(
                    run=self.run_id,
                    step=self.step,
                    action=ActionType.PROPOSE_LOCAL,
                    trigger="cascade_local",
                    cloud_in=0,
                    cloud_out=0,
                    cloud_cny=0.0,
                    gpu_s=round(wall_s, 3),
                    gpu_cny=gpu_cny,
                    wall_s=round(wall_s, 3),
                    score_before=score_before,
                    score_after=self.current_best_score,
                    verified=(self.current_best_score > score_before),
                    duplicate=any_dups,
                    seed=self.seed,
                    policy=self.policy.value,
                    config_hash=self.config_hash,
                )
            )
            return False
        else:
            messages = build_consultation_messages(
                rung=ConsultationRung.SOLVE,
                state_card=state_card_res.content,
            )
            try:
                rec = await self.gateway.call(
                    target="cloud",
                    messages=messages,
                    max_tokens=RUNG_MAX_TOKENS[ConsultationRung.SOLVE],
                    temperature=0.7,
                    task_class=self.task.task_class,
                )
            except BudgetExceededError:
                self._record_stop("cloud_budget_exceeded_exception")
                return True

            cand = Candidate(id=f"cand_takeover_{self.step}", content=rec.content)
            h = hash_candidate(cand.content)
            is_dup = h in self.seen_candidate_hashes
            self.seen_candidate_hashes.add(h)

            score_before = self.current_best_score
            if not is_dup:
                results = self.task.run_ladder(cand)
                if results and results[-1].valid and results[-1].score > score_before:
                    self.current_best_candidate = cand
                    self.current_best_score = results[-1].score
                    self.consecutive_failures = 0

            self.ledger.record(
                LedgerEntry(
                    run=self.run_id,
                    step=self.step,
                    action=ActionType.SOLVE,
                    trigger=f"cascade_threshold({self.config.cascade_fail_threshold})",
                    cloud_in=rec.prompt_tokens,
                    cloud_out=rec.completion_tokens,
                    cloud_cny=rec.cost_cny,
                    gpu_s=0.0,
                    gpu_cny=0.0,
                    wall_s=round(rec.wall_clock_s, 3),
                    score_before=score_before,
                    score_after=self.current_best_score,
                    verified=(self.current_best_score > score_before),
                    duplicate=is_dup,
                    seed=self.seed,
                    policy=self.policy.value,
                    config_hash=self.config_hash,
                )
            )
            return False

    async def _execute_static_split_plan(self) -> str:
        state_card_raw = self.task.state_card(self.current_best_candidate, [])
        state_card_res = format_and_truncate_state_card(state_card_raw)
        messages = [
            {"role": "system", "content": "You are a research planner. Generate a 3-stage optimization strategy."},
            {"role": "user", "content": f"Task: {self.task.goal}\nState:\n{state_card_res.content}"},
        ]
        rec = await self.gateway.call(
            target="cloud",
            messages=messages,
            max_tokens=500,
            temperature=0.7,
            task_class=self.task.task_class,
        )
        self.ledger.record(
            LedgerEntry(
                run=self.run_id,
                step=0,
                action=ActionType.STATIC_PLAN,
                trigger="initial_planning",
                cloud_in=rec.prompt_tokens,
                cloud_out=rec.completion_tokens,
                cloud_cny=rec.cost_cny,
                gpu_s=0.0,
                gpu_cny=0.0,
                wall_s=round(rec.wall_clock_s, 3),
                score_before=0.0,
                score_after=0.0,
                verified=False,
                seed=self.seed,
                policy=self.policy.value,
                config_hash=self.config_hash,
            )
        )
        return rec.content

    async def _step_static_split(self, static_plan: str | None) -> bool:
        state_card_raw = self.task.state_card(self.current_best_candidate, self.recent_results)
        state_card_res = format_and_truncate_state_card(state_card_raw)
        cands, wall_s = await self._generate_weak_k(
            k=self.current_k,
            state_card=state_card_res.content,
            cloud_guidance=static_plan,
        )
        best_cand, best_score, best_results, any_dups = self._evaluate_candidate_batch(cands)
        gpu_cny = round(wall_s * self.gateway.config.rho_gpu_cny_per_s, 6)

        score_before = self.current_best_score
        if best_score > self.current_best_score:
            self.current_best_candidate = best_cand
            self.current_best_score = best_score
            self.recent_results = best_results

        self.ledger.record(
            LedgerEntry(
                run=self.run_id,
                step=self.step,
                action=ActionType.PROPOSE_LOCAL,
                trigger="static_split_local",
                cloud_in=0,
                cloud_out=0,
                cloud_cny=0.0,
                gpu_s=round(wall_s, 3),
                gpu_cny=gpu_cny,
                wall_s=round(wall_s, 3),
                score_before=score_before,
                score_after=self.current_best_score,
                verified=(self.current_best_score > score_before),
                duplicate=any_dups,
                seed=self.seed,
                policy=self.policy.value,
                config_hash=self.config_hash,
            )
        )
        return False

    async def _generate_weak_k(
        self,
        k: int,
        state_card: str,
        cloud_guidance: str | None = None,
        lesson_advice: str | None = None,
    ) -> tuple[list[Candidate], float]:
        """Launch K concurrent calls to weak model backend (local or cloud_small) (#8)."""
        tasks = []
        target = self.config.weak_backend_target
        t0 = time.perf_counter()
        for i in range(k):
            angle = f"variation_{i+1}_seed_{self.seed}"
            temp = 0.6 + (0.1 * i)
            messages = build_local_prompt(
                task_description=self.task.goal,
                state_card=state_card,
                lesson_advice=lesson_advice,
                cloud_guidance=cloud_guidance,
                temperature_angle=angle,
            )
            tasks.append(
                self.gateway.call(
                    target=target,
                    messages=messages,
                    temperature=temp,
                    task_class=self.task.task_class,
                )
            )

        records = await asyncio.gather(*tasks)
        total_wall_s = time.perf_counter() - t0

        cands: list[Candidate] = []
        for i, rec in enumerate(records):
            cands.append(
                Candidate(
                    id=f"cand_{self.step}_{i}",
                    content=rec.content,
                    metadata={"step": self.step, "worker_idx": i},
                )
            )
        return cands, total_wall_s

    def _evaluate_candidate_batch(
        self,
        cands: list[Candidate],
    ) -> tuple[Candidate, float, list[VerificationResult], bool]:
        """Evaluate candidates using cheap-first screening (#2) and deduplication (#11).

        1. Filter duplicate candidates via hash (#11).
        2. Run cheap verifiers across ALL valid non-duplicate candidates.
        3. Only run expensive verifiers on top m candidates that passed cheap verifiers.
        """
        any_duplicates = False
        valid_candidates: list[tuple[Candidate, list[VerificationResult]]] = []
        evaluated_pairs: list[tuple[Candidate, list[VerificationResult]]] = []

        for cand in cands:
            h = hash_candidate(cand.content)
            if h in self.seen_candidate_hashes:
                any_duplicates = True
                continue
            self.seen_candidate_hashes.add(h)

            results: list[VerificationResult] = []
            passed_cheap = True
            for verifier in self.task.ladder:
                if verifier.is_expensive:
                    break
                v_res = verifier.verify(cand)
                results.append(v_res)
                if not v_res.valid:
                    passed_cheap = False
                    break

            evaluated_pairs.append((cand, results))
            if passed_cheap and results:
                valid_candidates.append((cand, results))

        if not valid_candidates:
            if evaluated_pairs:
                cand, res = evaluated_pairs[0]
                return cand, 0.0, res, any_duplicates
            return self.current_best_candidate, 0.0, [], any_duplicates

        # Separate expensive verifiers
        expensive_verifiers = [v for v in self.task.ladder if v.is_expensive]
        if not expensive_verifiers:
            # All verifiers were cheap: pick candidate with highest score
            best_cand, best_res = max(valid_candidates, key=lambda p: p[1][-1].score if p[1] else 0.0)
            score = best_res[-1].score if best_res else 0.0
            return best_cand, score, best_res, any_duplicates

        # Pre-filter top m candidates before expensive verifier (#2)
        m = min(len(valid_candidates), self.config.top_m_for_expensive)
        # Sort valid candidates by current cheap score descending
        valid_candidates.sort(key=lambda p: p[1][-1].score if p[1] else 0.0, reverse=True)
        top_m = valid_candidates[:m]

        best_cand: Candidate = top_m[0][0]
        best_score: float = 0.0
        best_results: list[VerificationResult] = top_m[0][1]

        for cand, res_list in top_m:
            full_results = list(res_list)
            for ev in expensive_verifiers:
                ev_res = ev.verify(cand)
                full_results.append(ev_res)
                if not ev_res.valid:
                    break

            last_score = full_results[-1].score if full_results[-1].valid else 0.0
            if last_score >= best_score:
                best_score = last_score
                best_cand = cand
                best_results = full_results

        return best_cand, best_score, best_results, any_duplicates

    async def _execute_consultation(self, rung: ConsultationRung, state_card: str) -> Any:
        failure_detail = None
        if self.recent_results:
            failed = [r for r in self.recent_results if not r.valid]
            if failed:
                failure_detail = f"{failed[-1].feedback} (Signature: {failed[-1].failure_signature})"

        messages = build_consultation_messages(
            rung=rung,
            state_card=state_card,
            failure_detail=failure_detail,
        )
        max_tokens = RUNG_MAX_TOKENS.get(rung, 500)
        rec = await self.gateway.call(
            target="cloud",
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.7,
            task_class=self.task.task_class,
        )
        return rec

    def _check_low_roi_stop_rule(self) -> bool:
        r = self.budget.recent_r_consultations
        consultations = [
            e for e in self.ledger.entries
            if e.action in ("hint", "solve", "diagnose", "replan")
        ]
        if len(consultations) < r:
            return False

        recent = consultations[-r:]
        total_delta = sum(max(0.0, e.score_after - e.score_before) for e in recent)
        total_cost = sum(e.cloud_cny for e in recent)
        if total_cost <= 0:
            return False

        roi = total_delta / total_cost
        return roi < self.budget.min_delta_per_cny_threshold

    def _record_stop(self, reason: str) -> None:
        self.ledger.record(
            LedgerEntry(
                run=self.run_id,
                step=self.step,
                action=ActionType.STOP,
                trigger=reason,
                cloud_in=0,
                cloud_out=0,
                cloud_cny=0.0,
                gpu_s=0.0,
                gpu_cny=0.0,
                wall_s=0.0,
                score_before=self.current_best_score,
                score_after=self.current_best_score,
                verified=False,
                duplicate=False,
                seed=self.seed,
                policy=self.policy.value,
                config_hash=self.config_hash,
                extra={"reason": reason},
            )
        )
