"""P2 Interfaces and Extension Stubs (Out of scope for hackathon MVP, defined for future extension).

1. Adaptive Ladder Ordering (e.g., dynamically reordering hint/diagnose/replan based on historical ROI)
2. Vector-based Lesson Retrieval (embedding-based semantic match instead of exact signature match)
3. Alpha Auto-Scanning (sweeping time-value parameter alpha across the Pareto frontier)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from tierlab.types import ConsultationRung, LessonRecord


class AdaptiveLadderScheduler(ABC):
    """P2: Re-orders consultation rungs dynamically based on empirical ROI in the ledger."""

    @abstractmethod
    def select_next_rung(
        self,
        failure_signature: str,
        historical_records: list[dict[str, Any]],
        remaining_budget: float,
    ) -> ConsultationRung:
        """Select the next best consultation rung according to estimated ROI per CNY."""
        pass


class VectorLessonRetriever(ABC):
    """P2: Vector embedding based semantic retrieval for lesson cards."""

    @abstractmethod
    async def index_lesson(self, lesson: LessonRecord, embedding_vector: list[float]) -> None:
        """Add lesson card embedding vector to index."""
        pass

    @abstractmethod
    async def search_similar_lessons(
        self,
        query_text: str,
        task_class: str,
        top_k: int = 3,
        threshold: float = 0.75,
    ) -> list[LessonRecord]:
        """Search lesson library via vector cosine similarity."""
        pass


class AlphaScanner(ABC):
    """P2: Automatically sweep alpha across multiple runs to map the Cost-Time-Score Pareto frontier."""

    @abstractmethod
    def generate_alpha_schedule(self, min_alpha: float, max_alpha: float, steps: int) -> list[float]:
        """Generate grid or log-spaced alpha values for Pareto frontier experiments."""
        pass
