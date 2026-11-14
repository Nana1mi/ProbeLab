"""Core Research Agent Controller and Policy Dispatcher.

Incorporates all amended specifications:
  - #1: Prompt context: task.context_for_model passed to local and cloud solve prompts.
  - #2: Output extraction: task.extract_candidate strips <think> and extracts last code block.
  - #3: Format failure limit: consecutive format failures > max_format_failures escalates as normal stagnation;
        exact set match; syntax crash and timeout do not count as format failure.
  - #4: State card reads evidence and score from candidate.metadata.
  - #5: Initial score computed strictly via run_ladder; no hardcoded initial scores.
  - #7: Unified stopping rule: stop when consecutive stagnation reaches stagnation_patience; no score >= 1.0 stop.
  - #8: Weak model backend configurable as local or cloud_small.
  - #9: Accounting: cloud_small bills to cloud columns with gpu_s=0; verify_s recorded per row;
        run-level start/end times and total GPU lease cost reported.
  - #11: Hint attribution: credited_to recorded on subsequent local improvements, included in ROI & stats.
  - #12: local_patience governs consultation escalation, replacing alpha comparison.
  - #14: Candidate sandbox execution cached; configurable math timeout.
  - #15: Concurrent budget reservation before K-way calls; BudgetExceededError caught as normal stop.
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
from tierlab.gateway import GatewayCallRecord, ModelGateway
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

# Exact set matching for format failures (#3)
# Runtime crashes (syntax/crash, code/crash) and timeouts (format/timeout, code/timeout) DO NOT count as format failures.
FORMAT_FAILURE_SIGNATURES = {
    "format/json_parse_error",
    "format/output_parse_error",
    "format/invalid_format",
    "format/invalid_item_format",
    "format/schema_missing_answers",
    "format/schema_missing_key",
    "format/empty_answers",
    "format/wrong_circle_count",
    "format/non_positive_radius",
    "format/syntax_or_output_error",
    "paper/quote_too_short",
}


def is_exact_format_failure(signature: str | None) -> bool:
    """Exact set membership check for format failures (#3)."""
    if not signature:
        return False
    return signature in FORMAT_FAILURE_SIGNATURES


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
        # Allow k_init override (#6)
        if self.config.k_init in self.k_schedule:
            self.k_index = self.k_schedule.index(self.config.k_init)
        else:
            self.k_index = 0
        self.current_k = self.k_schedule[self.k_index]

        # Runtime trackers
        self.step: int = 0
        self.consecutive_failures: int = 0
        self.consecutive_format_failures: int = 0     # Track consecutive format failures (#3)
        self.consecutive_stagnation_steps: int = 0    # Unified stopping rule counter (#7)
        self.has_widened_in_stagnation: bool = False
        self.seen_candidate_hashes: set[str] = set()
        self.pending_hint_id: str | None = None       # Hint attribution pointer (#11)

        # Step 0: Compute initial candidate score strictly via run_ladder (#5)
        init_cand = self.task.initial_candidate()
        init_results = self.task.run_ladder(init_cand)
        init_score = (
            init_results[-1].score
            if (init_results and all(r.valid for r in init_results))
            else 0.0
        )
        init_cand.metadata["score"] = init_score
        init_cand.metadata["evidence"] = init_results[-1].evidence if init_results else {}

        self.current_best_candidate: Candidate = init_cand
        self.current_best_score: float = init_score
        self.recent_results: list[VerificationResult] = init_results

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

    async def run(self) -> dict[str, Any]:
        """Execute research loop with in-loop dev feedback, followed by post-run held-out evaluation."""
        self.ledger.run_start_time = time.time()
        start_perf = time.perf_counter()

        init_hash = hash_candidate(self.current_best_candidate.content)
        self.seen_candidate_hashes.add(init_hash)

        static_plan: str | None = None
        if self.policy == PolicyType.STATIC_SPLIT:
            try:
                static_plan = await self._execute_static_split_plan()
            except BudgetExceededError:
                self._record_stop("cloud_budget_exceeded_exception")
                self.ledger.run_end_time = time.time()
                return self.ledger.summary(
                    rho_gpu_cny_per_s=self.gateway.config.rho_gpu_cny_per_s,
                    budget_b=self.budget.cloud_budget_cny,
                )

        while self.step < self.budget.max_steps:
            elapsed_wall = time.perf_counter() - start_perf
            if elapsed_wall >= self.budget.max_wall_clock_s:
                self._record_stop("max_wall_clock_exceeded")
                break

            # Unified stopping rule check (#7)
            if self.consecutive_stagnation_steps >= self.config.stagnation_patience:
                self._record_stop("consecutive_stagnation_stopped")
                break

            self.step += 1

            try:
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
            except BudgetExceededError:
                self._record_stop("cloud_budget_exceeded_exception")
                break

            if stop:
                break

        # Record run end timestamp (#10)
        self.ledger.run_end_time = time.time()

        # Post-loop held-out final evaluation (#3):
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
                    verify_s=round(final_eval.elapsed_s, 3),
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

        return self.ledger.summary(
            rho_gpu_cny_per_s=self.gateway.config.rho_gpu_cny_per_s,
            budget_b=self.budget.cloud_budget_cny,
        )

    def _compute_weak_call_costs(
        self,
        wall_s: float,
        call_records: list[GatewayCallRecord],
    ) -> tuple[int, int, float, float, float]:
        """Compute token counts, cloud costs, and GPU costs for weak model calls (#8, #10)."""
        if self.config.weak_backend == "cloud_small":
            cloud_in = sum(r.prompt_tokens for r in call_records)
            cloud_out = sum(r.completion_tokens for r in call_records)
            cloud_cny = sum(r.cost_cny for r in call_records)
            gpu_s = 0.0
            gpu_cny = 0.0
        else:
            cloud_in = 0
            cloud_out = 0
            cloud_cny = 0.0
            gpu_s = round(wall_s, 3)
            gpu_cny = round(wall_s * self.gateway.config.rho_gpu_cny_per_s, 6)
        return cloud_in, cloud_out, cloud_cny, gpu_s, gpu_cny

    async def _step_tierlab(self) -> bool:
        """Core TierLab Adaptive Policy step."""
        state_card_raw = self.task.state_card(self.current_best_candidate, self.recent_results)
        state_card_res = format_and_truncate_state_card(state_card_raw)
        full_context = self.task.context_for_model(self.current_best_candidate)

        cands, wall_s, call_records = await self._generate_weak_k(
            k=self.current_k,
            state_card=state_card_res.content,
            full_context=full_context,
            cloud_guidance=self.last_consultation_guidance,
        )

        t_v0 = time.perf_counter()
        best_cand, best_score, best_results, any_duplicates = self._evaluate_candidate_batch(cands)
        verify_s = time.perf_counter() - t_v0

        # Accounting based on weak_backend (#8, #10)
        cloud_in, cloud_out, cloud_cny, gpu_s, gpu_cny = self._compute_weak_call_costs(wall_s, call_records)

        score_before = self.current_best_score
        improved = best_score > self.current_best_score

        if improved:
            # Accepted candidate: write evidence and score to metadata (#4)
            best_cand.metadata["score"] = best_score
            best_cand.metadata["evidence"] = best_results[-1].evidence if best_results else {}

            self.current_best_candidate = best_cand
            self.current_best_score = best_score
            self.recent_results = best_results
            self.consecutive_failures = 0
            self.consecutive_format_failures = 0
            self.consecutive_stagnation_steps = 0
            self.has_widened_in_stagnation = False

            # Hint attribution: record credited_to if this improvement followed a hint (#11)
            credited_to = self.pending_hint_id

            # Validate lesson if one was active (#7)
            admitted_id = None
            if self.config.enable_lessons and self.last_provisional_lesson_id:
                admitted = self.lessons.promote_provisional_lesson(
                    self.last_provisional_lesson_id,
                    score_delta=best_score - score_before,
                )
                if admitted:
                    admitted_id = admitted.lesson_id

            self.ledger.record(
                LedgerEntry(
                    run=self.run_id,
                    step=self.step,
                    action=ActionType.PROPOSE_LOCAL,
                    trigger="local_success",
                    cloud_in=cloud_in,
                    cloud_out=cloud_out,
                    cloud_cny=cloud_cny,
                    gpu_s=gpu_s,
                    gpu_cny=gpu_cny,
                    wall_s=round(wall_s, 3),
                    verify_s=round(verify_s, 3),
                    credited_to=credited_to,
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
            self.pending_hint_id = None
            return False

        # No improvement: Stagnation handling
        self.consecutive_failures += 1
        self.consecutive_stagnation_steps += 1
        sig = self.task.failure_signature(best_results)

        # Format failure handling (#3)
        if is_exact_format_failure(sig):
            self.consecutive_format_failures += 1
            if self.consecutive_format_failures <= self.config.max_format_failures:
                # Retry locally only, no cloud consultation allowed
                self.ledger.record(
                    LedgerEntry(
                        run=self.run_id,
                        step=self.step,
                        action=ActionType.PROPOSE_LOCAL,
                        trigger=f"format_local_retry({sig},attempt={self.consecutive_format_failures})",
                        cloud_in=cloud_in,
                        cloud_out=cloud_out,
                        cloud_cny=cloud_cny,
                        gpu_s=gpu_s,
                        gpu_cny=gpu_cny,
                        wall_s=round(wall_s, 3),
                        verify_s=round(verify_s, 3),
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
            # When consecutive format failures exceed max_format_failures, treat as ordinary stagnation (#3)
        else:
            self.consecutive_format_failures = 0

        # Check lessons library if enabled (#7)
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
                        verify_s=0.0,
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

        # Local patience check (#12):
        # Allow local model attempts up to local_patience before escalating to cloud consultation ladder!
        if self.consecutive_failures < self.config.local_patience:
            # Try widening along k_schedule if not widened yet (#11)
            if (
                not self.config.no_widen
                and not self.has_widened_in_stagnation
                and self.k_index < len(self.k_schedule) - 1
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
                        trigger=f"stagnation_local_patience({self.consecutive_failures}/{self.config.local_patience})",
                        cloud_in=0,
                        cloud_out=0,
                        cloud_cny=0.0,
                        gpu_s=0.0,
                        gpu_cny=0.0,
                        wall_s=0.0,
                        verify_s=0.0,
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
            else:
                # Normal local retry within patience
                self.ledger.record(
                    LedgerEntry(
                        run=self.run_id,
                        step=self.step,
                        action=ActionType.PROPOSE_LOCAL,
                        trigger=f"local_patience_retry({self.consecutive_failures}/{self.config.local_patience})",
                        cloud_in=cloud_in,
                        cloud_out=cloud_out,
                        cloud_cny=cloud_cny,
                        gpu_s=gpu_s,
                        gpu_cny=gpu_cny,
                        wall_s=round(wall_s, 3),
                        verify_s=round(verify_s, 3),
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

        # Local patience exhausted: Consultation Ladder (hint -> solve) (#4)
        if self.current_rung_idx >= len(self.rungs):
            self._record_stop("consultation_ladder_exhausted")
            return True

        current_rung = self.rungs[self.current_rung_idx]

        # Low ROI check with attribution (#11)
        if self._check_low_roi_stop_rule():
            self._record_stop("low_consultation_roi_threshold")
            return True

        consult_record = await self._execute_consultation(current_rung, state_card_res.content)
        self.current_rung_idx += 1

        # Solve output directly goes to verifier (#4)
        if current_rung == ConsultationRung.SOLVE:
            cleaned_code = self.task.extract_candidate(consult_record.content)
            solve_cand = Candidate(
                id=f"cand_cloud_solve_{self.step}",
                content=cleaned_code,
                metadata={"origin": "cloud_solve", "step": self.step},
            )
            h = hash_candidate(solve_cand.content)
            is_dup = h in self.seen_candidate_hashes
            self.seen_candidate_hashes.add(h)

            solve_score = score_before
            solve_verified = False
            t_solve_v = time.perf_counter()
            if not is_dup:
                results = self.task.run_ladder(solve_cand)
                if results and results[-1].valid and results[-1].score > score_before:
                    solve_cand.metadata["score"] = results[-1].score
                    solve_cand.metadata["evidence"] = results[-1].evidence
                    self.current_best_candidate = solve_cand
                    self.current_best_score = results[-1].score
                    solve_score = self.current_best_score
                    solve_verified = True
                    self.recent_results = results
                    self.consecutive_stagnation_steps = 0
            verify_s_solve = time.perf_counter() - t_solve_v

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
                    verify_s=round(verify_s_solve, 3),
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
        self.pending_hint_id = f"hint_step_{self.step}"  # Pointer for hint attribution (#11)

        # Record provisional lesson (#7)
        if self.config.enable_lessons:
            prov = self.lessons.create_provisional_lesson(
                task_class=self.task.task_class,
                signature=sig,
                rung=current_rung.value,
                advice=consult_record.content,
                condition="dev_stagnation",
                cost_cny=consult_record.cost_cny,
            )
            self.last_provisional_lesson_id = prov.lesson_id

        self.ledger.record(
            LedgerEntry(
                run=self.run_id,
                step=self.step,
                action=ActionType.HINT,
                trigger="escalation_hint",
                cloud_in=consult_record.prompt_tokens,
                cloud_out=consult_record.completion_tokens,
                cloud_cny=consult_record.cost_cny,
                gpu_s=0.0,
                gpu_cny=0.0,
                wall_s=round(consult_record.wall_clock_s, 3),
                verify_s=0.0,
                score_before=score_before,
                score_after=score_before,
                verified=False,
                seed=self.seed,
                policy=self.policy.value,
                config_hash=self.config_hash,
                extra={"guidance": consult_record.content},
            )
        )
        return False

    async def _step_all_cloud(self) -> bool:
        """Fair all_cloud baseline: K=1 iterative consultation, stop on pass or stagnation (#1)."""
        state_card_raw = self.task.state_card(self.current_best_candidate, self.recent_results)
        state_card_res = format_and_truncate_state_card(state_card_raw)
        full_context = self.task.context_for_model(self.current_best_candidate)

        messages = build_consultation_messages(
            rung=ConsultationRung.SOLVE,
            state_card=state_card_res.content,
            full_context=full_context,
        )

        record = await self.gateway.call(
            target="cloud",
            messages=messages,
            max_tokens=RUNG_MAX_TOKENS[ConsultationRung.SOLVE],
            temperature=0.7,
            task_class=self.task.task_class,
        )

        cleaned_code = self.task.extract_candidate(record.content)
        candidate = Candidate(
            id=f"cand_cloud_{self.step}",
            content=cleaned_code,
            metadata={"step": self.step, "origin": "cloud"},
        )

        h = hash_candidate(candidate.content)
        is_dup = h in self.seen_candidate_hashes
        self.seen_candidate_hashes.add(h)

        score_before = self.current_best_score
        score_after = score_before
        verified = False
        t_v0 = time.perf_counter()

        if not is_dup:
            results = self.task.run_ladder(candidate)
            self.recent_results = results
            if results and results[-1].valid and len(results) == len(self.task.ladder):
                if results[-1].score > self.current_best_score:
                    candidate.metadata["score"] = results[-1].score
                    candidate.metadata["evidence"] = results[-1].evidence
                    self.current_best_candidate = candidate
                    self.current_best_score = results[-1].score
                    score_after = self.current_best_score
                    verified = True
                    self.consecutive_stagnation_steps = 0
                else:
                    self.consecutive_stagnation_steps += 1
            else:
                self.consecutive_stagnation_steps += 1
        else:
            self.consecutive_stagnation_steps += 1
        verify_s = time.perf_counter() - t_v0

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
                verify_s=round(verify_s, 3),
                score_before=score_before,
                score_after=score_after,
                verified=verified,
                duplicate=is_dup,
                seed=self.seed,
                policy=self.policy.value,
                config_hash=self.config_hash,
            )
        )
        return False

    async def _step_no_advice_retry(self) -> bool:
        """Amendment #6: At trigger points, grant local model an extra round with zero consultation."""
        state_card_raw = self.task.state_card(self.current_best_candidate, self.recent_results)
        state_card_res = format_and_truncate_state_card(state_card_raw)
        full_context = self.task.context_for_model(self.current_best_candidate)

        cands, wall_s, call_records = await self._generate_weak_k(
            k=self.current_k,
            state_card=state_card_res.content,
            full_context=full_context,
            cloud_guidance=None,
        )
        t_v0 = time.perf_counter()
        best_cand, best_score, best_results, any_dups = self._evaluate_candidate_batch(cands)
        verify_s = time.perf_counter() - t_v0

        cloud_in, cloud_out, cloud_cny, gpu_s, gpu_cny = self._compute_weak_call_costs(wall_s, call_records)
        score_before = self.current_best_score
        improved = best_score > self.current_best_score

        if improved:
            best_cand.metadata["score"] = best_score
            best_cand.metadata["evidence"] = best_results[-1].evidence if best_results else {}
            self.current_best_candidate = best_cand
            self.current_best_score = best_score
            self.recent_results = best_results
            self.consecutive_stagnation_steps = 0
        else:
            self.consecutive_stagnation_steps += 1

        self.ledger.record(
            LedgerEntry(
                run=self.run_id,
                step=self.step,
                action=ActionType.NO_ADVICE_RETRY,
                trigger="attribution_local_retry",
                cloud_in=cloud_in,
                cloud_out=cloud_out,
                cloud_cny=cloud_cny,
                gpu_s=gpu_s,
                gpu_cny=gpu_cny,
                wall_s=round(wall_s, 3),
                verify_s=round(verify_s, 3),
                score_before=score_before,
                score_after=self.current_best_score,
                verified=improved,
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
        full_context = self.task.context_for_model(self.current_best_candidate)

        cands, wall_s, call_records = await self._generate_weak_k(
            k=self.current_k,
            state_card=state_card_res.content,
            full_context=full_context,
        )
        t_v0 = time.perf_counter()
        best_cand, best_score, best_results, any_dups = self._evaluate_candidate_batch(cands)
        verify_s = time.perf_counter() - t_v0

        cloud_in, cloud_out, cloud_cny, gpu_s, gpu_cny = self._compute_weak_call_costs(wall_s, call_records)
        score_before = self.current_best_score
        improved = best_score > self.current_best_score

        if improved:
            best_cand.metadata["score"] = best_score
            best_cand.metadata["evidence"] = best_results[-1].evidence if best_results else {}
            self.current_best_candidate = best_cand
            self.current_best_score = best_score
            self.recent_results = best_results
            self.consecutive_stagnation_steps = 0
        else:
            self.consecutive_stagnation_steps += 1

        self.ledger.record(
            LedgerEntry(
                run=self.run_id,
                step=self.step,
                action=ActionType.PROPOSE_LOCAL,
                trigger="all_local_step",
                cloud_in=cloud_in,
                cloud_out=cloud_out,
                cloud_cny=cloud_cny,
                gpu_s=gpu_s,
                gpu_cny=gpu_cny,
                wall_s=round(wall_s, 3),
                verify_s=round(verify_s, 3),
                score_before=score_before,
                score_after=self.current_best_score,
                verified=improved,
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
        full_context = self.task.context_for_model(self.current_best_candidate)

        if self.consecutive_failures < self.config.cascade_fail_threshold:
            cands, wall_s, call_records = await self._generate_weak_k(
                k=1,
                state_card=state_card_res.content,
                full_context=full_context,
            )
            t_v0 = time.perf_counter()
            best_cand, best_score, best_results, any_dups = self._evaluate_candidate_batch(cands)
            verify_s = time.perf_counter() - t_v0

            cloud_in, cloud_out, cloud_cny, gpu_s, gpu_cny = self._compute_weak_call_costs(wall_s, call_records)
            score_before = self.current_best_score
            improved = best_score > self.current_best_score

            if improved:
                best_cand.metadata["score"] = best_score
                best_cand.metadata["evidence"] = best_results[-1].evidence if best_results else {}
                self.current_best_candidate = best_cand
                self.current_best_score = best_score
                self.recent_results = best_results
                self.consecutive_failures = 0
                self.consecutive_stagnation_steps = 0
            else:
                self.consecutive_failures += 1
                self.consecutive_stagnation_steps += 1

            self.ledger.record(
                LedgerEntry(
                    run=self.run_id,
                    step=self.step,
                    action=ActionType.PROPOSE_LOCAL,
                    trigger="cascade_local",
                    cloud_in=cloud_in,
                    cloud_out=cloud_out,
                    cloud_cny=cloud_cny,
                    gpu_s=gpu_s,
                    gpu_cny=gpu_cny,
                    wall_s=round(wall_s, 3),
                    verify_s=round(verify_s, 3),
                    score_before=score_before,
                    score_after=self.current_best_score,
                    verified=improved,
                    duplicate=any_dups,
                    seed=self.seed,
                    policy=self.policy.value,
                    config_hash=self.config_hash,
                )
            )
            return False
        else:
            # Cloud takeover
            messages = build_consultation_messages(
                rung=ConsultationRung.SOLVE,
                state_card=state_card_res.content,
                full_context=full_context,
            )
            rec = await self.gateway.call(
                target="cloud",
                messages=messages,
                max_tokens=RUNG_MAX_TOKENS[ConsultationRung.SOLVE],
                temperature=0.7,
                task_class=self.task.task_class,
            )

            cleaned_code = self.task.extract_candidate(rec.content)
            cand = Candidate(id=f"cand_takeover_{self.step}", content=cleaned_code)
            h = hash_candidate(cand.content)
            is_dup = h in self.seen_candidate_hashes
            self.seen_candidate_hashes.add(h)

            score_before = self.current_best_score
            t_v0 = time.perf_counter()
            if not is_dup:
                results = self.task.run_ladder(cand)
                if results and results[-1].valid and results[-1].score > score_before:
                    cand.metadata["score"] = results[-1].score
                    cand.metadata["evidence"] = results[-1].evidence
                    self.current_best_candidate = cand
                    self.current_best_score = results[-1].score
                    self.consecutive_failures = 0
                    self.consecutive_stagnation_steps = 0
                else:
                    self.consecutive_stagnation_steps += 1
            else:
                self.consecutive_stagnation_steps += 1
            verify_s = time.perf_counter() - t_v0

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
                    verify_s=round(verify_s, 3),
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
                verify_s=0.0,
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
        full_context = self.task.context_for_model(self.current_best_candidate)

        cands, wall_s, call_records = await self._generate_weak_k(
            k=self.current_k,
            state_card=state_card_res.content,
            full_context=full_context,
            cloud_guidance=static_plan,
        )
        t_v0 = time.perf_counter()
        best_cand, best_score, best_results, any_dups = self._evaluate_candidate_batch(cands)
        verify_s = time.perf_counter() - t_v0

        cloud_in, cloud_out, cloud_cny, gpu_s, gpu_cny = self._compute_weak_call_costs(wall_s, call_records)
        score_before = self.current_best_score
        improved = best_score > self.current_best_score

        if improved:
            best_cand.metadata["score"] = best_score
            best_cand.metadata["evidence"] = best_results[-1].evidence if best_results else {}
            self.current_best_candidate = best_cand
            self.current_best_score = best_score
            self.recent_results = best_results
            self.consecutive_stagnation_steps = 0
        else:
            self.consecutive_stagnation_steps += 1

        self.ledger.record(
            LedgerEntry(
                run=self.run_id,
                step=self.step,
                action=ActionType.PROPOSE_LOCAL,
                trigger="static_split_local",
                cloud_in=cloud_in,
                cloud_out=cloud_out,
                cloud_cny=cloud_cny,
                gpu_s=gpu_s,
                gpu_cny=gpu_cny,
                wall_s=round(wall_s, 3),
                verify_s=round(verify_s, 3),
                score_before=score_before,
                score_after=self.current_best_score,
                verified=improved,
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
        full_context: str | None = None,
        cloud_guidance: str | None = None,
        lesson_advice: str | None = None,
    ) -> tuple[list[Candidate], float, list[GatewayCallRecord]]:
        """Launch K concurrent calls to weak model backend (local or cloud_small) (#8, #15)."""
        target = self.config.weak_backend
        t0 = time.perf_counter()

        # Pre-flight budget reservation for concurrent calls before creating coroutines (#15)
        is_cloud_billing = target == "cloud_small"
        reserved_amount = 0.0
        if is_cloud_billing:
            sample_messages = build_local_prompt(
                task_description=self.task.goal,
                state_card=state_card,
                full_context=full_context,
                lesson_advice=lesson_advice,
                cloud_guidance=cloud_guidance,
                temperature_angle="sample",
            )
            worst_single = self.gateway.estimate_call_worst_case("cloud_small", sample_messages)
            reserved_amount = worst_single * k
            # Raises BudgetExceededError before creating coroutines if budget is insufficient (#15)
            self.gateway.reserve_budget(reserved_amount)

        task_coroutines = []
        for i in range(k):
            angle = f"variation_{i+1}_seed_{self.seed}"
            temp = 0.6 + (0.1 * i)
            messages = build_local_prompt(
                task_description=self.task.goal,
                state_card=state_card,
                full_context=full_context,
                lesson_advice=lesson_advice,
                cloud_guidance=cloud_guidance,
                temperature_angle=angle,
            )
            task_coroutines.append(
                self.gateway.call(
                    target=target,
                    messages=messages,
                    temperature=temp,
                    task_class=self.task.task_class,
                )
            )

        try:
            # Concurrent dispatch; retain partial successes (#15)
            raw_results = await asyncio.gather(*task_coroutines, return_exceptions=True)
        finally:
            if is_cloud_billing:
                self.gateway.release_budget(reserved_amount)

        total_wall_s = time.perf_counter() - t0

        records: list[GatewayCallRecord] = []
        errors: list[Exception] = []
        for r in raw_results:
            if isinstance(r, GatewayCallRecord):
                records.append(r)
            elif isinstance(r, Exception):
                errors.append(r)

        # If zero calls succeeded and budget was exceeded in all, re-raise BudgetExceededError (#15)
        if not records:
            if any(isinstance(e, BudgetExceededError) for e in errors):
                raise BudgetExceededError("Concurrent weak calls failed due to budget exhaustion")
            elif errors:
                raise errors[0]

        cands: list[Candidate] = []
        for i, rec in enumerate(records):
            # Output extraction: strip <think> and extract code fence (#2)
            cleaned = self.task.extract_candidate(rec.content)
            cands.append(
                Candidate(
                    id=f"cand_{self.step}_{i}",
                    content=cleaned,
                    metadata={"step": self.step, "worker_idx": i},
                )
            )
        return cands, total_wall_s, records

    def _evaluate_candidate_batch(
        self,
        cands: list[Candidate],
    ) -> tuple[Candidate, float, list[VerificationResult], bool]:
        """Evaluate candidates using cheap-first screening (#2) and deduplication (#11)."""
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
            best_cand, best_res = max(valid_candidates, key=lambda p: p[1][-1].score if p[1] else 0.0)
            score = best_res[-1].score if best_res else 0.0
            return best_cand, score, best_res, any_duplicates

        # Pre-filter top m candidates before expensive verifier (#2)
        m = min(len(valid_candidates), self.config.top_m_for_expensive)
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

        # Hint includes state card and truncated candidate; solve includes full context (#1)
        full_ctx = self.task.context_for_model(self.current_best_candidate, rung=rung.value)
        messages = build_consultation_messages(
            rung=rung,
            state_card=state_card,
            failure_detail=failure_detail,
            full_context=full_ctx,
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
        total_cost = sum(e.cloud_cny for e in recent)
        if total_cost <= 0:
            return False

        # Attributed delta (#11): sum delta from consultations plus credited subsequent local steps
        total_delta = 0.0
        for c in recent:
            direct_d = max(0.0, c.score_after - c.score_before)
            total_delta += direct_d
            if c.action == "hint":
                hint_tag = f"hint_step_{c.step}"
                for e in self.ledger.entries:
                    if e.credited_to == hint_tag:
                        total_delta += max(0.0, e.score_after - e.score_before)

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
                verify_s=0.0,
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
