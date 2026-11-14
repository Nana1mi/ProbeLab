"""Base classes for research tasks and verification ladders."""

from __future__ import annotations

import re
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from tierlab.types import (
    Budget,
    Candidate,
    VerificationResult,
    VerifierLevel,
)


class Verifier(ABC):
    """Abstract verifier step.

    Per amendment #2:
      - Returns VerificationResult(valid, score, feedback, evidence, elapsed_s)
      - Declares is_expensive: bool
        Only expensive verifiers undergo candidate pre-filtering;
        cheap scoring runs across all valid candidates.
    """

    level: VerifierLevel
    name: str
    is_expensive: bool = False

    @abstractmethod
    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        pass


class ResearchTask(ABC):
    """Abstract research task matching amended specifications #2 and #3."""

    task_class: str
    goal: str
    workspace: Path
    ladder: list[Verifier]            # Standard dev verification ladder
    final_verifier: Verifier | None   # Held-out hidden evaluation, run only once at end (#3)
    budget: Budget

    def __init__(
        self,
        workspace: Path | None = None,
        budget: Budget | None = None,
    ) -> None:
        self.workspace = workspace or Path.cwd()
        self.budget = budget or Budget()
        self.ladder = self.build_ladder()
        self.final_verifier = self.build_final_verifier()

    @abstractmethod
    def build_ladder(self) -> list[Verifier]:
        """Construct the development verification ladder (L0 -> L1 -> L2)."""
        pass

    def build_final_verifier(self) -> Verifier | None:
        """Construct the held-out final evaluation verifier (#3)."""
        return None

    @abstractmethod
    def state_card(
        self,
        current_best: Candidate | None,
        recent_results: list[VerificationResult],
    ) -> str:
        """Compress task state into <= 600 tokens equivalent, programmatically generated (#10)."""
        pass

    @abstractmethod
    def failure_signature(self, results: list[VerificationResult]) -> str:
        """Deterministic categorical failure signature string."""
        pass

    @abstractmethod
    def initial_candidate(self) -> Candidate:
        """Provide initial baseline candidate."""
        pass

    @abstractmethod
    def context_for_model(
        self,
        current_best: Candidate | None = None,
        rung: str | None = None,
        recent_results: list[VerificationResult] | None = None,
    ) -> str:
        """Provide complete prompt context for the model (Item #1).
        
        Must contain:
          - Current best candidate full content
          - Input/output conventions (signature, return types)
          - Available libraries
          - Execution timeout limit
          - For paper task: full paper text and all questions
          - For hint: state card and current candidate (can be truncated)
        """
        pass

    def extract_candidate(self, raw: str) -> str:
        """Extract clean candidate code or JSON from model response (Item #2).
        
        Rules:
          1. Strip <think>...</think> reasoning blocks.
          2. Extract the LAST fenced code block (e.g. ```python ... ``` or ```json ... ```).
          3. If no code fences are found, return stripped raw text.
        """
        if not raw:
            return ""
        # 1. Strip reasoning blocks
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE).strip()
        # 2. Find fenced code blocks
        fences = list(re.finditer(r"```(?:[a-zA-Z0-9_\-]+)?\s*\n?(.*?)```", cleaned, flags=re.DOTALL))
        if fences:
            cleaned = fences[-1].group(1).strip()
        try:
            envelope = json.loads(cleaned)
            if isinstance(envelope, dict) and isinstance(envelope.get('code'), str):
                return envelope['code'].strip()
        except (ValueError, TypeError):
            pass
        return cleaned

    def run_ladder(
        self,
        candidate: Candidate,
    ) -> list[VerificationResult]:
        """Run development verifiers sequentially.

        If a prerequisite constraint fails, stops execution of subsequent verifiers.
        """
        results: list[VerificationResult] = []
        if not candidate.content.strip():
            return [VerificationResult(valid=False, failure_signature="model/empty_response",
                                       feedback="Model returned no candidate content; retry or escalate.")]
        for verifier in self.ladder:
            res = verifier.verify(candidate)
            results.append(res)
            if not res.valid:
                break
        return results

    def run_final_evaluation(self, candidate: Candidate) -> VerificationResult | None:
        """Run held-out hidden evaluation once after loop completion (#3).

        Results are written directly to ledger and never returned to any model prompt.
        """
        if self.final_verifier:
            return self.final_verifier.verify(candidate)
        return None
