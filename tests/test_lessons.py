"""Tests for verified lesson store: provisional vs validated statuses and retrieval."""

from pathlib import Path
from tierlab.lessons import LessonStore
from tierlab.types import LessonStatus


def test_lesson_provisional_and_validation_lifecycle(tmp_path: Path):
    store = LessonStore(tmp_path / "lessons.json")

    # 1. Create provisional lesson
    prov = store.create_provisional_lesson(
        task_class="math",
        signature="math/circle_overlap",
        rung="hint",
        advice="Project centers with minimum separation",
        condition="math test",
        cost_cny=0.002,
    )
    assert prov.status == LessonStatus.PROVISIONAL
    assert prov.lesson_id == "L-001"

    # Provisional lesson must NOT be matched or injected into prompts (#7)
    assert store.match("math", "math/circle_overlap") is None

    # 2. Promote to validated once verifier confirms delta > 0
    validated = store.validate_lesson(prov.lesson_id, verified_delta=0.04)
    assert validated is not None
    assert validated.status == LessonStatus.VALIDATED

    # Now it must be matched!
    hit = store.match("math", "math/circle_overlap")
    assert hit is not None
    assert hit.lesson_id == "L-001"
    assert hit.uses == 1


def test_lesson_admittance_rejection_on_zero_delta(tmp_path: Path):
    store = LessonStore(tmp_path / "lessons.json")

    # Zero or negative delta rejected
    assert store.admit_lesson(
        task_class="math",
        signature="math/circle_overlap",
        rung="hint",
        advice="Spread circles",
        condition="c",
        verified_delta=0.0,
        cost_cny=0.001,
    ) is None

    assert store.admit_lesson(
        task_class="math",
        signature="math/circle_overlap",
        rung="hint",
        advice="Spread circles",
        condition="c",
        verified_delta=-0.02,
        cost_cny=0.001,
    ) is None
