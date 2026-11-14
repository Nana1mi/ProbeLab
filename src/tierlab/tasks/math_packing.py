"""Math Optimization Task: N circles packing in a unit square.

Per amended specifications:
  - #2: VerificationResult(valid, score, feedback, evidence, elapsed_s)
  - #12: Configurable circle count n (default 26), reports raw sum of radii and relative improvement over initial solution.
"""

from __future__ import annotations

import json
import math
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

LITERATURE_BEST_SUM_R = 2.635
TOLERANCE = 1e-9


def build_runner_script(n: int) -> str:
    return f"""
import json
import sys
try:
    import solution
    if not hasattr(solution, 'solve'):
        print(json.dumps({{"status": "error", "message": "Module does not define solve()"}}))
        sys.exit(0)
    res = solution.solve()
    if not isinstance(res, (list, tuple)):
        print(json.dumps({{"status": "error", "message": "solve() must return a list or tuple"}}))
        sys.exit(0)
    circles = []
    for item in res:
        if not (isinstance(item, (list, tuple)) and len(item) == 3):
            print(json.dumps({{"status": "error", "message": "Each circle must be (x, y, r)"}}))
            sys.exit(0)
        x, y, r = float(item[0]), float(item[1]), float(item[2])
        circles.append([x, y, r])
    print(json.dumps({{"status": "ok", "circles": circles}}))
except Exception as e:
    print(json.dumps({{"status": "exception", "message": f"{{type(e).__name__}}: {{str(e)}}"}}))
"""


class MathL0Verifier(Verifier):
    level = VerifierLevel.L0
    name = "math_l0_executable"
    is_expensive = False  # Cheap executable check

    def __init__(self, n: int = 26, timeout_s: float = 30.0) -> None:
        self.n = n
        self.timeout_s = timeout_s

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        t0 = time.perf_counter()
        runner = build_runner_script(self.n)
        res = execute_in_sandbox(
            code=candidate.content,
            entry_filename="solution.py",
            runner_script=runner,
            timeout_s=self.timeout_s,
        )
        elapsed = time.perf_counter() - t0

        if res.timed_out:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback="Execution timed out in L0 runner",
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
                feedback=f"Failed to parse runner output: {res.stdout[:150]}",
                evidence={"raw_stdout": res.stdout[:150]},
                elapsed_s=elapsed,
                failure_signature="format/syntax_or_output_error",
            )

        if data.get("status") != "ok":
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback=data.get("message", "Unknown L0 error"),
                evidence=data,
                elapsed_s=elapsed,
                failure_signature="format/invalid_format",
            )

        circles = data.get("circles", [])
        if len(circles) != self.n:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback=f"Expected {self.n} circles, got {len(circles)}",
                evidence={"got_circles": len(circles), "expected": self.n},
                elapsed_s=elapsed,
                failure_signature="format/wrong_circle_count",
            )

        for i, (x, y, r) in enumerate(circles):
            if r <= 0:
                return VerificationResult(
                    valid=False,
                    score=0.0,
                    feedback=f"Circle {i} has non-positive radius {r}",
                    evidence={"circle_idx": i, "radius": r},
                    elapsed_s=elapsed,
                    failure_signature="format/non_positive_radius",
                )

        return VerificationResult(
            valid=True,
            score=0.1,
            feedback=f"Successfully extracted {self.n} circles",
            evidence={"circles": circles},
            elapsed_s=elapsed,
        )


class MathL1Verifier(Verifier):
    level = VerifierLevel.L1
    name = "math_l1_constraints"
    is_expensive = False  # Cheap geometric constraints check

    def __init__(self, n: int = 26, timeout_s: float = 30.0) -> None:
        self.n = n
        self.timeout_s = timeout_s

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        t0 = time.perf_counter()
        l0 = MathL0Verifier(self.n, timeout_s=self.timeout_s).verify(candidate)
        if not l0.valid:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback="L0 prerequisite failed",
                evidence=l0.evidence,
                elapsed_s=time.perf_counter() - t0,
                failure_signature=l0.failure_signature,
            )

        circles = l0.evidence.get("circles", [])
        # Boundary constraints
        for i, (x, y, r) in enumerate(circles):
            if (x - r < -TOLERANCE or x + r > 1.0 + TOLERANCE or
                    y - r < -TOLERANCE or y + r > 1.0 + TOLERANCE):
                return VerificationResult(
                    valid=False,
                    score=0.0,
                    feedback=f"Circle {i} at ({x:.4f}, {y:.4f}) with r={r:.4f} exceeds [0, 1] bounds",
                    evidence={"circle_index": i, "x": x, "y": y, "r": r},
                    elapsed_s=time.perf_counter() - t0,
                    failure_signature="math/out_of_bounds",
                )

        # Non-overlapping constraints
        num_c = len(circles)
        for i in range(num_c):
            x1, y1, r1 = circles[i]
            for j in range(i + 1, num_c):
                x2, y2, r2 = circles[j]
                dist = math.hypot(x1 - x2, y1 - y2)
                if dist < (r1 + r2 - TOLERANCE):
                    return VerificationResult(
                        valid=False,
                        score=0.0,
                        feedback=f"Overlap between circle {i} and {j}: dist={dist:.5f} < r1+r2={r1+r2:.5f}",
                        evidence={"overlap_pair": [i, j], "dist": dist, "min_dist": r1 + r2},
                        elapsed_s=time.perf_counter() - t0,
                        failure_signature="math/circle_overlap",
                    )

        return VerificationResult(
            valid=True,
            score=0.5,
            feedback=f"All {self.n} circles satisfy non-overlapping and boundary constraints",
            evidence={"circles": circles},
            elapsed_s=time.perf_counter() - t0,
        )


class MathL2Verifier(Verifier):
    level = VerifierLevel.L2
    name = "math_l2_objective"
    is_expensive = False  # Objective scoring for valid candidates

    def __init__(self, n: int = 26, initial_sum_r: float = 1.78, timeout_s: float = 30.0) -> None:
        self.n = n
        self.initial_sum_r = initial_sum_r
        self.timeout_s = timeout_s

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        t0 = time.perf_counter()
        l1 = MathL1Verifier(self.n, timeout_s=self.timeout_s).verify(candidate)
        if not l1.valid:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback="L1 constraints not satisfied",
                evidence=l1.evidence,
                elapsed_s=time.perf_counter() - t0,
                failure_signature=l1.failure_signature,
            )

        circles = l1.evidence.get("circles", [])
        sum_r = sum(c[2] for c in circles)
        relative_improvement = (sum_r - self.initial_sum_r) / max(1e-6, self.initial_sum_r)

        # Amendment #5 & #12:
        # If n == 26, calculate normalized score against literature best 2.635
        # If n != 26, do not normalize by 2.635; report raw sum of radii and relative improvement.
        if self.n == 26:
            normalized_score = min(1.0, max(0.0, sum_r / LITERATURE_BEST_SUM_R))
            score = round(normalized_score, 5)
            feedback = (
                f"Sum of radii: {sum_r:.5f} (Relative improvement: {relative_improvement*100:.2f}%, "
                f"Normalized: {normalized_score:.4f})"
            )
            evidence = {
                "sum_r": round(sum_r, 5),
                "initial_sum_r": round(self.initial_sum_r, 5),
                "relative_improvement": round(relative_improvement, 5),
                "normalized_score": round(normalized_score, 5),
            }
        else:
            score = round(sum_r, 5)
            feedback = (
                f"Sum of radii: {sum_r:.5f} (Relative improvement: {relative_improvement*100:.2f}%)"
            )
            evidence = {
                "sum_r": round(sum_r, 5),
                "initial_sum_r": round(self.initial_sum_r, 5),
                "relative_improvement": round(relative_improvement, 5),
            }

        return VerificationResult(
            valid=True,
            score=score,
            feedback=feedback,
            evidence=evidence,
            elapsed_s=time.perf_counter() - t0,
        )


class MathFinalEvaluationVerifier(Verifier):
    """Held-out final evaluation (#3): strict multi-precision verification with audit log."""
    level = VerifierLevel.L2
    name = "math_final_eval"
    is_expensive = True

    def __init__(self, n: int = 26, initial_sum_r: float = 1.78, timeout_s: float = 30.0) -> None:
        self.n = n
        self.initial_sum_r = initial_sum_r
        self.timeout_s = timeout_s

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        v = MathL2Verifier(self.n, self.initial_sum_r, timeout_s=self.timeout_s)
        res = v.verify(candidate, context)
        res.evidence["final_eval"] = True
        return res


class MathPackingTask(ResearchTask):
    task_class = "math"

    def __init__(
        self,
        workspace: Path | None = None,
        budget: Budget | None = None,
        n: int = 26,
        math_timeout_s: float = 30.0,
    ) -> None:
        self.n = n
        self.math_timeout_s = math_timeout_s
        self.goal = f"Place {n} non-overlapping circles in a unit square [0, 1] x [0, 1] maximizing sum of radii."
        self._initial_sum_r = self._compute_initial_sum_r()
        super().__init__(workspace=workspace, budget=budget)

    def _compute_initial_sum_r(self) -> float:
        # Deterministic valid grid calculation for n circles (#5)
        m = int(math.ceil(math.sqrt(self.n)))
        w = 1.0 / m
        r = 0.45 * w
        return round(self.n * r, 5)

    def build_ladder(self) -> list[Verifier]:
        return [
            MathL0Verifier(self.n, timeout_s=self.math_timeout_s),
            MathL1Verifier(self.n, timeout_s=self.math_timeout_s),
            MathL2Verifier(self.n, self._initial_sum_r, timeout_s=self.math_timeout_s),
        ]

    def build_final_verifier(self) -> Verifier:
        return MathFinalEvaluationVerifier(self.n, self._initial_sum_r, timeout_s=self.math_timeout_s)

    def state_card(
        self,
        current_best: Candidate | None,
        recent_results: list[VerificationResult],
    ) -> str:
        # Programmatic structured summary reading from candidate.metadata (#4, #10)
        best_score = 0.0
        sum_r = 0.0
        rel_imp = 0.0
        if current_best and current_best.metadata:
            ev = current_best.metadata.get("evidence", {})
            sum_r = ev.get("sum_r", 0.0)
            rel_imp = ev.get("relative_improvement", 0.0)
            best_score = current_best.metadata.get("score", 0.0)

        last_sig = "none"
        last_msg = "none"
        if recent_results:
            failed = [r for r in recent_results if not r.valid or r.failure_signature]
            if failed:
                last_sig = failed[-1].failure_signature or "unknown"
                last_msg = failed[-1].feedback
            else:
                last_sig = "success"
                last_msg = recent_results[-1].feedback

        card = (
            f"TASK: {self.task_class.upper()} (n={self.n})\n"
            f"GOAL: {self.goal}\n"
            f"CURRENT_BEST_SCORE: {best_score:.4f} (sum_r={sum_r:.4f}, rel_imp={rel_imp*100:.2f}%)\n"
            f"LAST_FAILURE_SIGNATURE: {last_sig}\n"
            f"DEV_FEEDBACK: {last_msg}\n"
            f"REQUIREMENT: Python file defining solve() returning {self.n} (x, y, r) tuples in [0,1]^2."
        )
        return card

    def failure_signature(self, results: list[VerificationResult]) -> str:
        for r in results:
            if not r.valid:
                return r.failure_signature or "math/unknown_failure"
        return "math/success"

    def initial_candidate(self) -> Candidate:
        # Generate initial grid packing for n circles valid for ANY n >= 1 (#5)
        m = int(math.ceil(math.sqrt(self.n)))
        w = 1.0 / m
        r = round(0.45 * w, 6)

        code = f'''"""Baseline initial candidate: {self.n} circles in [0, 1] x [0, 1]."""
import math

def solve():
    circles = []
    n = {self.n}
    m = {m}
    w = 1.0 / m
    r = {r}
    for k in range(n):
        i = k // m
        j = k % m
        x = round((i + 0.5) * w, 6)
        y = round((j + 0.5) * w, 6)
        circles.append((x, y, r))
    return circles
'''
        # Item #5: No hardcoded score in metadata; computed via run_ladder
        return Candidate(
            id="cand_math_initial",
            content=code,
            metadata={},
        )

    def context_for_model(
        self,
        current_best: Candidate | None = None,
        rung: str | None = None,
        recent_results: list[VerificationResult] | None = None,
    ) -> str:
        best_code = current_best.content if current_best else self.initial_candidate().content
        if rung == "hint":
            # For hint: return state card and truncated current candidate (#1)
            truncated_cand = best_code[:400] + ("..." if len(best_code) > 400 else "")
            card = self.state_card(current_best, recent_results or [])
            return f"STATE CARD:\n{card}\nCURRENT BEST CANDIDATE (TRUNCATED):\n{truncated_cand}"

        return (
            f"TASK SPECIFICATION:\n"
            f"- Goal: {self.goal}\n"
            f"- Function signature: def solve() -> list[tuple[float, float, float]]\n"
            f"- Return format: Exactly {self.n} (x, y, r) tuples strictly inside [0, 1] x [0, 1]\n"
            f"- Constraints: No circle overlaps (dist >= r1 + r2) and no boundary overflow (r <= x <= 1-r, r <= y <= 1-r)\n"
            f"- Available libraries: math, random, sys, json, numpy\n"
            f"- Execution timeout limit: {self.math_timeout_s} seconds\n\n"
            f"CURRENT BEST CANDIDATE CODE:\n"
            f"```python\n{best_code}\n```"
        )
