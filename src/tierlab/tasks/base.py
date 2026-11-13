"""Base classes for research tasks and verification ladders."""

from __future__ import annotations

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

    def run_ladder(
        self,
        candidate: Candidate,
    ) -> list[VerificationResult]:
        """Run development verifiers sequentially.

        If a prerequisite constraint fails, stops execution of subsequent verifiers.
        """
        results: list[VerificationResult] = []
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
