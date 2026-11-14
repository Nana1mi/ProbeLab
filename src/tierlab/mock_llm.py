"""Scriptable deterministic MockLLM backend.

Supports:
  - 'realistic' mode: returns <think>...</think>, explanatory text, and code fences;
    strictly inspects prompt context and returns invalid output if contract/code/timeout/paper/questions are missing.
  - hint and solve cloud actions (#4)
  - weak model target: local vLLM or cloud_small (#8)
  - continuous improvement, stagnation then lesson, widening, ladder escalation, budget exhaustion, stop rule.
"""

from __future__ import annotations

import json
import random
from typing import Any
from pydantic import BaseModel


class MockResponse(BaseModel):
    content: str
    prompt_tokens: int = 150
    completion_tokens: int = 60
    cached_tokens: int = 0


class MockLLMBackend:
    """Scriptable mock LLM generating deterministic responses for TierLab."""

    def __init__(self, mode: str = "default", seed: int = 0) -> None:
        self.mode = mode
        self.seed = seed
        self.rng = random.Random(seed)
        self.call_count: int = 0
        self.cloud_call_count: int = 0
        self.local_call_count: int = 0

    def generate(
        self,
        target: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float = 0.7,
        task_class: str = "math",
    ) -> MockResponse:
        self.call_count += 1
        if target in ("cloud", "cloud_flagship"):
            self.cloud_call_count += 1
            return self._generate_cloud(messages, max_tokens, task_class)
        else:
            self.local_call_count += 1
            return self._generate_local(messages, max_tokens, task_class)

    def _generate_cloud(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None,
        task_class: str,
    ) -> MockResponse:
        full_text = " ".join(m.get("content", "") for m in messages)

        # 1. Hint action (amendment #4)
        if "max 150 tokens" in full_text or "strategic hint" in full_text.lower():
            if self.mode == "realistic":
                content = (
                    "<think>\nLocal solver is stuck. Identifying high-leverage optimization direction.\n</think>\n"
                    "Strategic Hint: Shift circle centers along the boundary outward "
                    "to maximize interior radii clearance, or precompute vector norms."
                )
            else:
                content = (
                    "Strategic Hint: Shift circle centers along the boundary outward "
                    "to maximize interior radii clearance, or precompute vector norms."
                )
            return MockResponse(content=content, prompt_tokens=220, completion_tokens=35, cached_tokens=50)

        # 2. Diagnose / Replan (retained templates, default disabled)
        elif "diagnose" in full_text.lower():
            content = (
                "Diagnosis: Overlaps occur because radius growth precedes pairwise clearance checks.\n"
                "Rule: Project centers to minimum separation first."
            )
            return MockResponse(content=content, prompt_tokens=380, completion_tokens=55, cached_tokens=100)

        elif "replan" in full_text.lower():
            content = (
                "Plan: Transition from greedy placement to hexagonal lattice initialization with boundary descent."
            )
            return MockResponse(content=content, prompt_tokens=550, completion_tokens=80, cached_tokens=120)

        # 3. Solve / Takeover action (amendment #4): directly outputs improved candidate
        else:
            if self.mode == "realistic":
                return self._generate_realistic_solve(full_text, task_class)

            if task_class == "math":
                content = self._get_improved_math_code(radius_boost=0.015)
            elif task_class == "code":
                content = self._get_optimized_code()
            elif task_class == "paper":
                content = self._get_perfect_paper_json()
            else:
                content = "High quality candidate solution."

            return MockResponse(content=content, prompt_tokens=650, completion_tokens=280, cached_tokens=150)

    def _generate_local(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None,
        task_class: str,
    ) -> MockResponse:
        full_text = " ".join(m.get("content", "") for m in messages)

        if self.mode == "realistic":
            return self._generate_realistic_local(full_text, task_class)

        if self.mode == "stop_rule":
            if task_class == "math":
                content = self._get_baseline_math_code()
            elif task_class == "code":
                content = self._get_baseline_code()
            else:
                content = self._get_baseline_paper_json()
            return MockResponse(content=content, prompt_tokens=180, completion_tokens=120)

        lesson_present = "REUSED PROVEN LESSON" in full_text or "CLOUD ADVICE" in full_text

        if self.mode == "stagnation_then_lesson":
            if not lesson_present:
                if task_class == "math":
                    content = self._get_overlapping_math_code()
                elif task_class == "code":
                    content = self._get_baseline_code()
                else:
                    content = self._get_unmatched_quote_paper_json()
                return MockResponse(content=content, prompt_tokens=200, completion_tokens=110)
            else:
                if task_class == "math":
                    content = self._get_improved_math_code(radius_boost=0.01)
                elif task_class == "code":
                    content = self._get_optimized_code()
                else:
                    content = self._get_perfect_paper_json()
                return MockResponse(content=content, prompt_tokens=250, completion_tokens=160)

        elif self.mode == "widen_then_improve":
            if "variation_3" in full_text or "variation_4" in full_text:
                if task_class == "math":
                    content = self._get_improved_math_code(radius_boost=0.012)
                elif task_class == "code":
                    content = self._get_optimized_code()
                else:
                    content = self._get_perfect_paper_json()
            else:
                if task_class == "math":
                    content = self._get_overlapping_math_code()
                elif task_class == "code":
                    content = self._get_baseline_code()
                else:
                    content = self._get_unmatched_quote_paper_json()
            return MockResponse(content=content, prompt_tokens=200, completion_tokens=120)

        elif self.mode == "ladder_escalation":
            if "solve" in full_text.lower() or "takeover" in full_text.lower() or "lattice" in full_text.lower():
                if task_class == "math":
                    content = self._get_improved_math_code(radius_boost=0.02)
                elif task_class == "code":
                    content = self._get_optimized_code()
                else:
                    content = self._get_perfect_paper_json()
            else:
                if task_class == "math":
                    content = self._get_overlapping_math_code()
                elif task_class == "code":
                    content = self._get_baseline_code()
                else:
                    content = self._get_unmatched_quote_paper_json()
            return MockResponse(content=content, prompt_tokens=210, completion_tokens=120)

        # Default continuous improvement
        step_idx = self.local_call_count
        radius_boost = min(0.025, 0.002 * step_idx)
        if task_class == "math":
            content = self._get_improved_math_code(radius_boost=radius_boost)
        elif task_class == "code":
            if step_idx <= 2:
                content = self._get_baseline_code()
            else:
                content = self._get_optimized_code()
        elif task_class == "paper":
            if step_idx <= 1:
                content = self._get_unmatched_quote_paper_json()
            else:
                content = self._get_perfect_paper_json()
        else:
            content = "Standard candidate"

        return MockResponse(content=content, prompt_tokens=190, completion_tokens=130)

    def _generate_realistic_solve(self, full_text: str, task_class: str) -> MockResponse:
        """Realistic cloud solve mode: inspects prompt context and wraps in think + code block."""
        if not self._check_prompt_context(full_text, task_class):
            return MockResponse(
                content="<think>\nTask context incomplete.\n</think>\nCannot solve without complete task contract and code.",
                prompt_tokens=150,
                completion_tokens=30,
            )

        if task_class == "math":
            code = self._get_improved_math_code(radius_boost=0.015)
            wrapped = f"<think>\nOptimizing circle arrangement to increase radii sum.\n</think>\nHere is the improved solution:\n```python\n{code}\n```"
        elif task_class == "code":
            code = self._get_optimized_code()
            wrapped = f"<think>\nPrecomputing vector norms and sorting top-k.\n</think>\nHere is the optimized implementation:\n```python\n{code}\n```"
        elif task_class == "paper":
            code = self._get_perfect_paper_json()
            wrapped = f"<think>\nMatching questions to paper paragraphs with exact quotes.\n</think>\nHere is the grounded QA response:\n```json\n{code}\n```"
        else:
            wrapped = "```python\n# ok\n```"

        return MockResponse(content=wrapped, prompt_tokens=450, completion_tokens=250, cached_tokens=100)

    def _generate_realistic_local(self, full_text: str, task_class: str) -> MockResponse:
        """Realistic local mode: inspects prompt context and returns think + fenced code."""
        if not self._check_prompt_context(full_text, task_class):
            return MockResponse(
                content="<think>\nMissing required prompt context.\n</think>\nI cannot generate a valid solution because required problem contract or candidate is absent.",
                prompt_tokens=150,
                completion_tokens=30,
            )

        step_idx = self.local_call_count
        if task_class == "math":
            # Start with slight boost on later steps
            radius_boost = min(0.018, 0.003 * step_idx)
            code = self._get_improved_math_code(radius_boost=radius_boost)
            wrapped = (
                f"<think>\nRefining packing coordinates for n circles, expanding radii by {radius_boost:.4f}.\n</think>\n"
                f"I updated the circle coordinates to pack more efficiently:\n```python\n{code}\n```\nAll constraints verified."
            )
        elif task_class == "code":
            if step_idx <= 1:
                code = self._get_baseline_code()
            else:
                code = self._get_optimized_code()
            wrapped = (
                f"<think>\nAnalyzing algorithm complexity. Vectorizing norm computation.\n</think>\n"
                f"Here is the revised candidate code:\n```python\n{code}\n```\nReady for benchmarking."
            )
        elif task_class == "paper":
            if step_idx <= 1:
                code = self._get_unmatched_quote_paper_json()
            else:
                code = self._get_perfect_paper_json()
            wrapped = (
                f"<think>\nSearching source paper for exact verbatim quotes.\n</think>\n"
                f"Extracted answers with paragraph citations:\n```json\n{code}\n```"
            )
        else:
            wrapped = "```python\n# candidate\n```"

        return MockResponse(content=wrapped, prompt_tokens=350, completion_tokens=220)

    def _check_prompt_context(self, full_text: str, task_class: str) -> bool:
        """Inspect prompt: return True only if required task context is present (#1)."""
        lower = full_text.lower()
        if task_class == "math":
            has_contract = "solve()" in lower or "def solve" in lower
            has_spec = "circle" in lower or "(x, y, r)" in lower
            has_lib = "math" in lower
            has_timeout = "timeout" in lower or "seconds" in lower
            return has_contract and has_spec and has_lib and has_timeout
        elif task_class == "code":
            has_contract = "topk_cosine_sim" in lower
            has_params = "k" in lower and ("a" in lower or "database" in lower)
            has_lib = "standard library" in lower or "math" in lower
            has_timeout = "timeout" in lower or "seconds" in lower
            return has_contract and has_params and has_lib and has_timeout
        elif task_class == "paper":
            has_paper = "full source papers" in lower or "sample_paper_1.txt" in lower or "under full load" in lower
            has_questions = "all questions to answer" in lower or "q1" in lower or "q2" in lower
            has_contract = "answers" in lower and "quote" in lower
            has_timeout = "timeout" in lower or "seconds" in lower
            return has_paper and has_questions and has_contract and has_timeout
        return True

    def _get_baseline_math_code(self) -> str:
        return '''def solve():
    circles = []
    r = 0.07
    step = 0.18
    for i in range(5):
        for j in range(5):
            circles.append((round(0.12 + i * step, 4), round(0.12 + j * step, 4), r))
    circles.append((0.92, 0.92, 0.03))
    return circles
'''

    def _get_overlapping_math_code(self) -> str:
        return '''def solve():
    circles = []
    r = 0.10
    step = 0.15
    for i in range(5):
        for j in range(5):
            circles.append((round(0.15 + i * step, 4), round(0.15 + j * step, 4), r))
    circles.append((0.90, 0.90, 0.05))
    return circles
'''

    def _get_improved_math_code(self, radius_boost: float = 0.005) -> str:
        r_base = 0.075 + radius_boost
        step = 0.185
        return f'''def solve():
    circles = []
    r = {r_base:.5f}
    step = {step:.5f}
    for i in range(5):
        for j in range(5):
            x = round(0.10 + i * step, 5)
            y = round(0.10 + j * step, 5)
            circles.append((x, y, r))
    circles.append((0.94, 0.94, 0.03))
    return circles
'''

    def _get_baseline_code(self) -> str:
        from tierlab.tasks.code_opt import REFERENCE_CODE
        return REFERENCE_CODE

    def _get_optimized_code(self) -> str:
        return '''import math

def topk_cosine_sim(A, Q, k):
    A_norms = []
    for a in A:
        s = sum(x * x for x in a)
        A_norms.append(math.sqrt(s) + 1e-12)

    results = []
    for q in Q:
        q_norm = math.sqrt(sum(x * x for x in q)) + 1e-12
        sims = []
        for i, a in enumerate(A):
            dot = sum(x * y for x, y in zip(q, a))
            sims.append((dot / (q_norm * A_norms[i]), i))
        sims.sort(key=lambda item: item[0], reverse=True)
        results.append([idx for _, idx in sims[:k]])
    return results
'''

    def _get_baseline_paper_json(self) -> str:
        return json.dumps({
            "answers": [
                {
                    "qid": "Q1",
                    "answer": "4.2 GB",
                    "quote": "Under full load, the system reached a peak memory usage of 4.2 GB."
                }
            ]
        }, indent=2)

    def _get_unmatched_quote_paper_json(self) -> str:
        return json.dumps({
            "answers": [
                {
                    "qid": "Q1",
                    "answer": "4.2 GB",
                    "quote": "This sentence is completely fabricated and will not match any paragraph."
                },
                {
                    "qid": "Q2",
                    "answer": "SimpleGreedy",
                    "quote": "Another fabricated quotation for failure test in QA."
                }
            ]
        }, indent=2)

    def _get_perfect_paper_json(self) -> str:
        return json.dumps({
            "answers": [
                {
                    "qid": "Q1",
                    "answer": "4.2 GB",
                    "quote": "Under full load, the system reached a peak memory usage of 4.2 GB."
                },
                {
                    "qid": "Q2",
                    "answer": "SimpleGreedy",
                    "quote": "We compare our approach against the SimpleGreedy baseline."
                },
                {
                    "qid": "Q3",
                    "answer": "local widening and escalation",
                    "quote": "The trade-off between local widening and escalation governs overall efficiency."
                }
            ]
        }, indent=2)
