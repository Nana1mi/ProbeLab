"""Code Optimization Task: Batch Cosine Similarity Top-K.

Per amended specifications:
  - #2: VerificationResult(valid, score, feedback, evidence, elapsed_s)
  - #3: Dev feedback separate from final hidden evaluation
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from tierlab.sandbox import execute_in_sandbox
from tierlab.tasks.base import ResearchTask, Verifier
from tierlab.types import (
    Budget,
    Candidate,
    VerificationResult,
    VerifierLevel,
)

REFERENCE_CODE = '''"""Slow reference implementation of batch cosine similarity top-k."""
import math

def dot_product(v1, v2):
    s = 0.0
    for a, b in zip(v1, v2):
        s += a * b
    return s

def vector_norm(v):
    s = 0.0
    for a in v:
        s += a * a
    return math.sqrt(s)

def topk_cosine_sim(A, Q, k):
    results = []
    for q in Q:
        q_norm = vector_norm(q)
        sims = []
        for i, a in enumerate(A):
            a_norm = vector_norm(a)
            denom = q_norm * a_norm
            sim = dot_product(q, a) / (denom + 1e-12)
            sims.append((sim, i))
        for step in range(k):
            max_idx = step
            for j in range(step + 1, len(sims)):
                if sims[j][0] > sims[max_idx][0]:
                    max_idx = j
            sims[step], sims[max_idx] = sims[max_idx], sims[step]
        results.append([sims[j][1] for j in range(k)])
    return results
'''

DEV_RUNNER_SCRIPT = '''
import json
import sys
import time
import random

try:
    import reference
    import solution

    if not hasattr(solution, "topk_cosine_sim"):
        print(json.dumps({"status": "error", "message": "Module does not define topk_cosine_sim"}))
        sys.exit(0)

    # 1. Visible test
    A_vis = [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]
    Q_vis = [[1.0, 0.0]]
    ref_vis = reference.topk_cosine_sim(A_vis, Q_vis, 1)
    cand_vis = solution.topk_cosine_sim(A_vis, Q_vis, 1)
    if ref_vis != cand_vis:
        print(json.dumps({
            "status": "visible_fail",
            "message": f"Visible test failed: expected {ref_vis}, got {cand_vis}"
        }))
        sys.exit(0)

    # 2. Dev hidden tests
    rng = random.Random(42)
    M, N, D, k = 30, 8, 12, 3
    A_hid = [[rng.uniform(-1.0, 1.0) for _ in range(D)] for _ in range(M)]
    Q_hid = [[rng.uniform(-1.0, 1.0) for _ in range(D)] for _ in range(N)]

    ref_hid = reference.topk_cosine_sim(A_hid, Q_hid, k)
    cand_hid = solution.topk_cosine_sim(A_hid, Q_hid, k)
    if ref_hid != cand_hid:
        print(json.dumps({
            "status": "hidden_fail",
            "message": "Candidate output does not match reference output on dev hidden inputs"
        }))
        sys.exit(0)

    # 3. Dev benchmark timing (interleaved runs)
    M_bm, N_bm, D_bm, k_bm = 80, 25, 32, 5
    A_bm = [[rng.uniform(-1.0, 1.0) for _ in range(D_bm)] for _ in range(M_bm)]
    Q_bm = [[rng.uniform(-1.0, 1.0) for _ in range(D_bm)] for _ in range(N_bm)]

    # Warmup
    _ = reference.topk_cosine_sim(A_bm, Q_bm, k_bm)
    _ = solution.topk_cosine_sim(A_bm, Q_bm, k_bm)

    ref_times = []
    cand_times = []
    for _ in range(5):
        t0 = time.perf_counter()
        _ = reference.topk_cosine_sim(A_bm, Q_bm, k_bm)
        ref_times.append(time.perf_counter() - t0)

        t1 = time.perf_counter()
        _ = solution.topk_cosine_sim(A_bm, Q_bm, k_bm)
        cand_times.append(time.perf_counter() - t1)

    ref_times.sort()
    cand_times.sort()
    ref_med = ref_times[len(ref_times) // 2]
    cand_med = cand_times[len(cand_times) // 2]
    speedup = ref_med / max(cand_med, 1e-9)

    print(json.dumps({
        "status": "ok",
        "ref_med_s": ref_med,
        "cand_med_s": cand_med,
        "speedup": round(speedup, 4)
    }))
except Exception as e:
    print(json.dumps({"status": "exception", "message": f"{type(e).__name__}: {str(e)}"}))
'''

FINAL_EVAL_RUNNER_SCRIPT = '''
import json
import sys
import time
import random

try:
    import reference
    import solution

    rng = random.Random(9999)  # Held-out hidden evaluation seed (#3)
    M, N, D, k = 100, 30, 32, 5
    A = [[rng.uniform(-1.0, 1.0) for _ in range(D)] for _ in range(M)]
    Q = [[rng.uniform(-1.0, 1.0) for _ in range(D)] for _ in range(N)]

    ref_ans = reference.topk_cosine_sim(A, Q, k)
    cand_ans = solution.topk_cosine_sim(A, Q, k)
    if ref_ans != cand_ans:
        print(json.dumps({"status": "eval_fail", "message": "Failed hidden evaluation correctness"}))
        sys.exit(0)

    ref_times, cand_times = [], []
    for _ in range(5):
        t0 = time.perf_counter()
        _ = reference.topk_cosine_sim(A, Q, k)
        ref_times.append(time.perf_counter() - t0)

        t1 = time.perf_counter()
        _ = solution.topk_cosine_sim(A, Q, k)
        cand_times.append(time.perf_counter() - t1)

    ref_times.sort()
    cand_times.sort()
    ref_med = ref_times[len(ref_times) // 2]
    cand_med = cand_times[len(cand_times) // 2]
    speedup = ref_med / max(cand_med, 1e-9)

    print(json.dumps({
        "status": "ok",
        "final_speedup": round(speedup, 4),
        "ref_med_s": ref_med,
        "cand_med_s": cand_med
    }))
except Exception as e:
    print(json.dumps({"status": "exception", "message": str(e)}))
'''


class CodeL0Verifier(Verifier):
    level = VerifierLevel.L0
    name = "code_l0_visible"
    is_expensive = False

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        t0 = time.perf_counter()
        res = execute_in_sandbox(
            code=candidate.content,
            entry_filename="solution.py",
            runner_script=DEV_RUNNER_SCRIPT,
            extra_files={"reference.py": REFERENCE_CODE},
            timeout_s=10.0,
        )
        elapsed = time.perf_counter() - t0

        if res.timed_out:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback="Execution timed out in code L0 runner",
                evidence={"error": "timeout"},
                elapsed_s=elapsed,
                failure_signature="format/timeout",
            )
        if res.returncode != 0 and not res.stdout:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback=f"Process crashed: {res.stderr[:200]}",
                evidence={"stderr": res.stderr[:200]},
                elapsed_s=elapsed,
                failure_signature="syntax/crash",
            )

        try:
            data = json.loads(res.stdout.strip())
        except Exception:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback=f"Failed to parse runner JSON: {res.stdout[:150]}",
                evidence={"stdout": res.stdout[:150]},
                elapsed_s=elapsed,
                failure_signature="format/output_parse_error",
            )

        if data.get("status") == "visible_fail":
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback=data.get("message", "Visible test failed"),
                evidence=data,
                elapsed_s=elapsed,
                failure_signature="code/visible_test_failure",
            )

        if data.get("status") in ("error", "exception"):
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback=data.get("message", "Exception in candidate execution"),
                evidence=data,
                elapsed_s=elapsed,
                failure_signature="syntax/runtime_error",
            )

        return VerificationResult(
            valid=True,
            score=0.2,
            feedback="Visible test passed",
            evidence=data,
            elapsed_s=elapsed,
        )


class CodeL1Verifier(Verifier):
    level = VerifierLevel.L1
    name = "code_l1_correctness"
    is_expensive = False

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        t0 = time.perf_counter()
        l0 = CodeL0Verifier().verify(candidate)
        if not l0.valid:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback="L0 prerequisite failed",
                evidence=l0.evidence,
                elapsed_s=time.perf_counter() - t0,
                failure_signature=l0.failure_signature,
            )

        data = l0.evidence
        if data.get("status") == "hidden_fail":
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback=data.get("message", "Dev hidden test failed"),
                evidence=data,
                elapsed_s=time.perf_counter() - t0,
                failure_signature="code/hidden_test_failure",
            )

        return VerificationResult(
            valid=True,
            score=0.5,
            feedback="Dev hidden input correctness test passed with reference equality",
            evidence=data,
            elapsed_s=time.perf_counter() - t0,
        )


class CodeL2Verifier(Verifier):
    level = VerifierLevel.L2
    name = "code_l2_speedup"
    is_expensive = True  # Benchmarking is declared expensive (#2)

    def __init__(self, noise_threshold: float = 0.15) -> None:
        self.noise_threshold = noise_threshold

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        t0 = time.perf_counter()
        l1 = CodeL1Verifier().verify(candidate)
        if not l1.valid:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback="L1 correctness failed",
                evidence=l1.evidence,
                elapsed_s=time.perf_counter() - t0,
                failure_signature=l1.failure_signature,
            )

        data = l1.evidence
        speedup = float(data.get("speedup", 1.0))
        ref_med = float(data.get("ref_med_s", 0.0))
        cand_med = float(data.get("cand_med_s", 0.0))

        if speedup < (1.0 + self.noise_threshold):
            return VerificationResult(
                valid=True,
                score=0.1,
                feedback=f"Speedup {speedup:.2f}x is within noise threshold ({1.0 + self.noise_threshold:.2f}x)",
                evidence={
                    "speedup": speedup,
                    "ref_med_s": ref_med,
                    "cand_med_s": cand_med,
                    "significant": False,
                },
                elapsed_s=time.perf_counter() - t0,
                failure_signature="code/insufficient_speedup",
            )

        normalized_score = min(1.0, max(0.1, speedup / 6.0))
        return VerificationResult(
            valid=True,
            score=round(normalized_score, 4),
            feedback=f"Verified speedup: {speedup:.2f}x (Candidate: {cand_med*1000:.1f}ms, Ref: {ref_med*1000:.1f}ms)",
            evidence={
                "speedup": speedup,
                "ref_med_s": ref_med,
                "cand_med_s": cand_med,
                "significant": True,
                "normalized_score": round(normalized_score, 4),
            },
            elapsed_s=time.perf_counter() - t0,
        )


class CodeFinalEvaluationVerifier(Verifier):
    level = VerifierLevel.L2
    name = "code_final_eval"
    is_expensive = True

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        t0 = time.perf_counter()
        res = execute_in_sandbox(
            code=candidate.content,
            entry_filename="solution.py",
            runner_script=FINAL_EVAL_RUNNER_SCRIPT,
            extra_files={"reference.py": REFERENCE_CODE},
            timeout_s=15.0,
        )
        elapsed = time.perf_counter() - t0
        try:
            data = json.loads(res.stdout.strip())
            if data.get("status") == "ok":
                speedup = float(data.get("final_speedup", 1.0))
                score = min(1.0, speedup / 6.0)
                return VerificationResult(
                    valid=True,
                    score=round(score, 4),
                    feedback=f"Final held-out speedup: {speedup:.2f}x",
                    evidence=data,
                    elapsed_s=elapsed,
                )
        except Exception:
            pass
        return VerificationResult(
            valid=False,
            score=0.0,
            feedback="Final held-out evaluation failed",
            evidence={"stderr": res.stderr},
            elapsed_s=elapsed,
        )


class CodeOptimizationTask(ResearchTask):
    task_class = "code"
    goal = "Optimize batch cosine similarity top-K algorithm against reference implementation."

    def __init__(
        self,
        workspace: Path | None = None,
        budget: Budget | None = None,
        noise_threshold: float = 0.15,
    ) -> None:
        self.noise_threshold = noise_threshold
        super().__init__(workspace=workspace, budget=budget)

    def build_ladder(self) -> list[Verifier]:
        return [
            CodeL0Verifier(),
            CodeL1Verifier(),
            CodeL2Verifier(noise_threshold=self.noise_threshold),
        ]

    def build_final_verifier(self) -> Verifier:
        return CodeFinalEvaluationVerifier()

    def state_card(
        self,
        current_best: Candidate | None,
        recent_results: list[VerificationResult],
    ) -> str:
        best_score = 0.0
        if current_best and "score" in current_best.metadata:
            best_score = current_best.metadata["score"]

        last_sig = "none"
        last_msg = "none"
        if recent_results:
            failed = [r for r in recent_results if not r.valid]
            if failed:
                last_sig = failed[-1].failure_signature or "unknown"
                last_msg = failed[-1].feedback
            else:
                last_sig = "success"
                last_msg = recent_results[-1].feedback

        card = (
            f"TASK: {self.task_class.upper()}\n"
            f"GOAL: {self.goal}\n"
            f"CURRENT_BEST_SCORE: {best_score:.4f}\n"
            f"LAST_FAILURE_SIGNATURE: {last_sig}\n"
            f"DEV_FEEDBACK: {last_msg}\n"
            f"REQUIREMENT: Optimize topk_cosine_sim(A, Q, k) using vectorized or precomputed norms."
        )
        return card

    def failure_signature(self, results: list[VerificationResult]) -> str:
        for r in results:
            if not r.valid:
                return r.failure_signature or "code/unknown_failure"
            if r.failure_signature:
                return r.failure_signature
        return "code/success"

    def initial_candidate(self) -> Candidate:
        return Candidate(
            id="cand_code_initial",
            content=REFERENCE_CODE,
            metadata={"score": 0.1, "verified": True},
        )
