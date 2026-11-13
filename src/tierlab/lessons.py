"""Lessons library: storage, lifecycle statuses (provisional -> validated), and exact signature retrieval.

Per amended specifications:
  - #7: Lessons library default disabled, isolated per run by default.
  - #7: Lessons have two states: PROVISIONAL and VALIDATED.
        Only VALIDATED lessons can be retrieved and reused.
        Cross-run sharing requires explicit configuration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tierlab.types import LessonRecord, LessonStatus


class LessonStore:
    """Manages lessons lifecycle and persistence."""

    def __init__(
        self,
        storage_path: Path | str | None = None,
        share_across_runs: bool = False,
    ) -> None:
        self.share_across_runs = share_across_runs
        self.storage_path = Path(storage_path) if storage_path else Path("lessons.json")
        self.lessons: list[LessonRecord] = []
        self.load()

    def load(self) -> None:
        if self.storage_path.is_file():
            try:
                data = json.loads(self.storage_path.read_text(encoding="utf-8"))
                self.lessons = [LessonRecord(**item) for item in data]
            except Exception:
                self.lessons = []
        else:
            self.lessons = []

    def save(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = [l.model_dump() for l in self.lessons]
        self.storage_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def create_provisional_lesson(
        self,
        task_class: str,
        signature: str,
        rung: str,
        advice: str,
        condition: str,
        cost_cny: float,
    ) -> LessonRecord:
        """Create a new provisional lesson when consultation advice is received."""
        lesson_id = f"L-{len(self.lessons) + 1:03d}"
        rec = LessonRecord(
            lesson_id=lesson_id,
            signature=signature,
            task_class=task_class,
            rung=rung,
            advice=advice,
            condition=condition,
            verified_delta=0.0,
            cost_cny=round(cost_cny, 6),
            status=LessonStatus.PROVISIONAL,
            uses=0,
        )
        self.lessons.append(rec)
        self.save()
        return rec

    def validate_lesson(
        self,
        lesson_id: str,
        verified_delta: float,
    ) -> LessonRecord | None:
        """Promote a provisional lesson to validated once improvement is confirmed by verifier."""
        if verified_delta <= 0.0:
            return None

        for l in self.lessons:
            if l.lesson_id == lesson_id:
                l.status = LessonStatus.VALIDATED
                l.verified_delta = round(verified_delta, 5)
                self.save()
                return l
        return None

    def admit_lesson(
        self,
        task_class: str,
        signature: str,
        rung: str,
        advice: str,
        condition: str,
        verified_delta: float,
        cost_cny: float,
    ) -> LessonRecord | None:
        """Convenience method to directly admit a validated lesson if delta > 0."""
        if verified_delta <= 0.0:
            return None

        lesson_id = f"L-{len(self.lessons) + 1:03d}"
        rec = LessonRecord(
            lesson_id=lesson_id,
            signature=signature,
            task_class=task_class,
            rung=rung,
            advice=advice,
            condition=condition,
            verified_delta=round(verified_delta, 5),
            cost_cny=round(cost_cny, 6),
            status=LessonStatus.VALIDATED,
            uses=0,
        )
        self.lessons.append(rec)
        self.save()
        return rec

    def match(self, task_class: str, signature: str) -> LessonRecord | None:
        """Retrieve lesson by exact match on (task_class, failure_signature).

        CRITICAL (#7): Only VALIDATED lessons can be retrieved and injected!
        """
        if not signature or signature in ("none", "success"):
            return None

        matches = [
            l for l in self.lessons
            if l.task_class == task_class
            and l.signature == signature
            and l.status == LessonStatus.VALIDATED
        ]
        if not matches:
            return None

        matches.sort(key=lambda x: x.verified_delta, reverse=True)
        best = matches[0]
        best.uses += 1
        self.save()
        return best
