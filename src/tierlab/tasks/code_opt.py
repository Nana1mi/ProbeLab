"""Code Optimization Task: Batch Cosine Similarity Top-K.

Per amended specifications:
  - #2: VerificationResult(valid, score, feedback, evidence, elapsed_s)
  - #3: Dev feedback separate from final hidden evaluation
  - #9: Reference and candidate execute in independent subprocesses; fresh inputs per run;
        reference execution >= 0.2s; score reports direct uncapped speedup.
"""

from __future__ import annotations

import json
import statistics
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

REF_RUNNER_SCRIPT = '''
import argparse
import json
import random
import sys
import time

try:
    from reference import topk_cosine_sim

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, default="benchmark")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    if args.mode == "visible":
        A = [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]
        Q = [[1.0, 0.0]]
        k = 1
    elif args.mode == "hidden":
        M, N, D, k = 30, 8, 12, 3
        A = [[rng.uniform(-1.0, 1.0) for _ in range(D)] for _ in range(M)]
        Q = [[rng.uniform(-1.0, 1.0) for _ in range(D)] for _ in range(N)]
    else:  # benchmark with >= 0.2s runtime scale (#9)
        M, N, D, k = 800, 100, 64, 5
        A = [[rng.uniform(-1.0, 1.0) for _ in range(D)] for _ in range(M)]
        Q = [[rng.uniform(-1.0, 1.0) for _ in range(D)] for _ in range(N)]

    t0 = time.perf_counter()
    ans = topk_cosine_sim(A, Q, k)
    elapsed = time.perf_counter() - t0

    print(json.dumps({
        "status": "ok",
        "ans": ans,
        "elapsed_s": elapsed
    }))
except Exception as e:
    print(json.dumps({"status": "exception", "message": f"{type(e).__name__}: {str(e)}"}))
'''

CAND_RUNNER_SCRIPT = '''
import argparse
import json
import random
import sys
import time

try:
    import solution

    if not hasattr(solution, "topk_cosine_sim"):
        print(json.dumps({"status": "error", "message": "Module does not define topk_cosine_sim"}))
        sys.exit(0)

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, default="benchmark")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    if args.mode == "visible":
        A = [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]
        Q = [[1.0, 0.0]]
        k = 1
    elif args.mode == "hidden":
        M, N, D, k = 30, 8, 12, 3
        A = [[rng.uniform(-1.0, 1.0) for _ in range(D)] for _ in range(M)]
        Q = [[rng.uniform(-1.0, 1.0) for _ in range(D)] for _ in range(N)]
    else:  # benchmark with >= 0.2s runtime scale (#9)
        M, N, D, k = 800, 100, 64, 5
        A = [[rng.uniform(-1.0, 1.0) for _ in range(D)] for _ in range(M)]
        Q = [[rng.uniform(-1.0, 1.0) for _ in range(D)] for _ in range(N)]

    t0 = time.perf_counter()
    ans = solution.topk_cosine_sim(A, Q, k)
    elapsed = time.perf_counter() - t0

    print(json.dumps({
        "status": "ok",
        "ans": ans,
        "elapsed_s": elapsed
    }))
except Exception as e:
    print(json.dumps({"status": "exception", "message": f"{type(e).__name__}: {str(e)}"}))
'''


def run_reference_sandbox(mode: str = "benchmark", seed: int = 42, timeout_s: float = 30.0) -> dict[str, Any]:
    """Execute reference implementation in an independent subprocess (#9)."""
    res = execute_in_sandbox(
        code="",
        entry_filename="main.py",
        runner_script=REF_RUNNER_SCRIPT,
        extra_files={"reference.py": REFERENCE_CODE},
        timeout_s=timeout_s,
        args=["--mode", mode, "--seed", str(seed)],
    )
    if res.timed_out:
        return {"status": "timeout", "message": "Reference timed out"}
    if res.returncode != 0:
        return {"status": "crash", "message": res.stderr}
    try:
        return json.loads(res.stdout.strip())
    except Exception:
        return {"status": "error", "message": f"Invalid json: {res.stdout}"}


def run_candidate_sandbox(candidate_code: str, mode: str = "benchmark", seed: int = 42, timeout_s: float = 30.0) -> dict[str, Any]:
    """Execute candidate implementation in an independent subprocess (#9)."""
    res = execute_in_sandbox(
        code=candidate_code,
        entry_filename="solution.py",
        runner_script=CAND_RUNNER_SCRIPT,
        timeout_s=timeout_s,
        args=["--mode", mode, "--seed", str(seed)],
    )
    if res.timed_out:
        return {"status": "timeout", "message": "Candidate timed out"}
    if res.returncode != 0 and not res.stdout:
        return {"status": "crash", "message": res.stderr}
    try:
        return json.loads(res.stdout.strip())
    except Exception:
        return {"status": "error", "message": f"Invalid json: {res.stdout}"}


class CodeL0Verifier(Verifier):
    level = VerifierLevel.L0
    name = "code_l0_visible"
    is_expensive = False

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        t0 = time.perf_counter()
        data = run_candidate_sandbox(candidate.content, mode="visible", seed=0, timeout_s=10.0)
        elapsed = time.perf_counter() - t0

        if data.get("status") == "timeout":
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback="Execution timed out in code L0 runner",
                evidence={"error": "timeout"},
                elapsed_s=elapsed,
                failure_signature="format/timeout",
            )
        if data.get("status") == "crash":
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback=f"Process crashed: {data.get('message', '')[:200]}",
                evidence=data,
                elapsed_s=elapsed,
                failure_signature="syntax/crash",
            )
        if data.get("status") in ("error", "exception"):
            msg = data.get("message", "")
            sig = "format/output_parse_error" if "Invalid json" in msg else "syntax/runtime_error"
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback=f"Candidate failed visible test setup: {msg}",
                evidence=data,
                elapsed_s=elapsed,
                failure_signature=sig,
            )

        # Compare visible test output against expected [[0]]
        expected_ans = [[0]]
        cand_ans = data.get("ans")
        if cand_ans != expected_ans:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback=f"Visible test failed: expected {expected_ans}, got {cand_ans}",
                evidence={"expected": expected_ans, "got": cand_ans},
                elapsed_s=elapsed,
                failure_signature="code/visible_test_failure",
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

        # Independent subprocesses for reference and candidate (#9)
        ref_data = run_reference_sandbox(mode="hidden", seed=42, timeout_s=15.0)
        cand_data = run_candidate_sandbox(candidate.content, mode="hidden", seed=42, timeout_s=15.0)

        if ref_data.get("status") != "ok" or cand_data.get("status") != "ok":
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback=f"Hidden test execution failed: cand={cand_data.get('message')}, ref={ref_data.get('message')}",
                evidence={"ref": ref_data, "cand": cand_data},
                elapsed_s=time.perf_counter() - t0,
                failure_signature="code/hidden_test_failure",
            )

        if ref_data.get("ans") != cand_data.get("ans"):
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback="Candidate output does not match reference output on dev hidden inputs",
                evidence={"ref_ans": ref_data.get("ans"), "cand_ans": cand_data.get("ans")},
                elapsed_s=time.perf_counter() - t0,
                failure_signature="code/hidden_test_failure",
            )

        return VerificationResult(
            valid=True,
            score=0.5,
            feedback="Dev hidden input correctness test passed with reference equality",
            evidence={"status": "ok"},
            elapsed_s=time.perf_counter() - t0,
        )


class CodeL2Verifier(Verifier):
    level = VerifierLevel.L2
    name = "code_l2_speedup"
    is_expensive = True  # Benchmarking is declared expensive (#2)

    def __init__(self, noise_threshold: float = 0.08) -> None:
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

        # Benchmark: Separate subprocesses with fresh inputs per run (#9)
        ref_times: list[float] = []
        cand_times: list[float] = []

        # Run 3 benchmark iterations with fresh random inputs
        for iter_idx in range(3):
            fresh_seed = 1000 + iter_idx * 17
            ref_res = run_reference_sandbox(mode="benchmark", seed=fresh_seed, timeout_s=30.0)
            cand_res = run_candidate_sandbox(candidate.content, mode="benchmark", seed=fresh_seed, timeout_s=30.0)

            if ref_res.get("status") != "ok" or cand_res.get("status") != "ok":
                return VerificationResult(
                    valid=False,
                    score=0.0,
                    feedback="Benchmark execution failed in subprocess",
                    evidence={"ref": ref_res, "cand": cand_res},
                    elapsed_s=time.perf_counter() - t0,
                    failure_signature="code/benchmark_runtime_error",
                )
            ref_times.append(float(ref_res["elapsed_s"]))
            cand_times.append(float(cand_res["elapsed_s"]))

        ref_med = statistics.median(ref_times)
        cand_med = statistics.median(cand_times)
        speedup = ref_med / max(cand_med, 1e-9)

        if speedup < (1.0 + self.noise_threshold):
            return VerificationResult(
                valid=True,
                score=0.1,
                feedback=f"Speedup {speedup:.2f}x is within noise threshold ({1.0 + self.noise_threshold:.2f}x)",
                evidence={
                    "speedup": round(speedup, 4),
                    "ref_med_s": round(ref_med, 4),
                    "cand_med_s": round(cand_med, 4),
                    "significant": False,
                },
                elapsed_s=time.perf_counter() - t0,
                failure_signature="code/insufficient_speedup",
            )

        # Uncapped direct speedup report (#9)
        score = round(speedup, 4)
        return VerificationResult(
            valid=True,
            score=score,
            feedback=f"Verified speedup: {speedup:.2f}x (Candidate: {cand_med*1000:.1f}ms, Ref: {ref_med*1000:.1f}ms)",
            evidence={
                "speedup": round(speedup, 4),
                "ref_med_s": round(ref_med, 4),
                "cand_med_s": round(cand_med, 4),
                "significant": True,
            },
            elapsed_s=time.perf_counter() - t0,
        )


class CodeFinalEvaluationVerifier(Verifier):
    level = VerifierLevel.L2
    name = "code_final_eval"
    is_expensive = True

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        t0 = time.perf_counter()
        held_out_seed = 9999
        ref_res = run_reference_sandbox(mode="benchmark", seed=held_out_seed, timeout_s=30.0)
        cand_res = run_candidate_sandbox(candidate.content, mode="benchmark", seed=held_out_seed, timeout_s=30.0)
        elapsed = time.perf_counter() - t0

        if ref_res.get("status") == "ok" and cand_res.get("status") == "ok":
            # Check correctness on held-out seed
            if ref_res.get("ans") == cand_res.get("ans"):
                speedup = float(ref_res["elapsed_s"]) / max(float(cand_res["elapsed_s"]), 1e-9)
                return VerificationResult(
                    valid=True,
                    score=round(speedup, 4),
                    feedback=f"Final held-out speedup: {speedup:.2f}x",
                    evidence={
                        "final_eval": True,
                        "speedup": round(speedup, 4),
                        "ref_s": round(float(ref_res["elapsed_s"]), 4),
                        "cand_s": round(float(cand_res["elapsed_s"]), 4),
                    },
                    elapsed_s=elapsed,
                )

        return VerificationResult(
            valid=False,
            score=0.0,
            feedback="Final held-out evaluation failed",
            evidence={"ref": ref_res, "cand": cand_res, "final_eval": True},
            elapsed_s=elapsed,
        )


class CodeOptimizationTask(ResearchTask):
    task_class = "code"
    goal = "Optimize batch cosine similarity top-K algorithm against reference implementation."

    def __init__(
        self,
        workspace: Path | None = None,
        budget: Budget | None = None,
        noise_threshold: float = 0.08,
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
        # Programmatic structured summary reading from candidate.metadata (#4, #10)
        best_score = 0.0
        speedup = 1.0
        if current_best and current_best.metadata:
            ev = current_best.metadata.get("evidence", {})
            speedup = ev.get("speedup", 1.0)
            best_score = current_best.metadata.get("score", 0.0)

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
            f"CURRENT_BEST_SCORE: {best_score:.4f} (Speedup: {speedup:.2f}x)\n"
            f"LAST_FAILURE_SIGNATURE: {last_sig}\n"
            f"DEV_FEEDBACK: {last_msg}\n"
            f"REQUIREMENT: Python file defining topk_cosine_sim(A, Q, k) returning list of list of top-k indices."
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
        # Item #5: No hardcoded score in metadata; computed via run_ladder
        return Candidate(
            id="cand_code_initial",
            content=REFERENCE_CODE,
            metadata={},
        )

    def context_for_model(
        self,
        current_best: Candidate | None = None,
        rung: str | None = None,
    ) -> str:
        best_code = current_best.content if current_best else self.initial_candidate().content
        if rung == "hint":
            card = self.state_card(current_best, [])
            truncated_cand = best_code[:400] + ("..." if len(best_code) > 400 else "")
            return f"STATE CARD:\n{card}\n\nCURRENT BEST CANDIDATE (TRUNCATED):\n{truncated_cand}"

        return (
            f"TASK SPECIFICATION:\n"
            f"- Goal: {self.goal}\n"
            f"- Function signature: def topk_cosine_sim(A: list[list[float]], Q: list[list[float]], k: int) -> list[list[int]]\n"
            f"- Input/Output: A is database vectors (M x D), Q is query vectors (N x D). For each query in Q, return top-k indices in A with highest cosine similarity.\n"
            f"- Available libraries: Python standard library (math, random, collections, heapq)\n"
            f"- Execution timeout limit: 15.0 seconds\n\n"
            f"CURRENT BEST CANDIDATE CODE:\n"
            f"```python\n{best_code}\n```"
        )
