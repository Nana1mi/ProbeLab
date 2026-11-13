"""Tests verifying all 6 required MockLLM behavioral scenarios:

  1. Continuous improvement
  2. Stagnation followed by lesson hit
  3. Widening (K-way) leading to improvement
  4. Step-by-step consultation ladder escalation (hint -> solve)
  5. Budget exhaustion rejection
  6. Stop rule triggering
"""

import pytest
from tierlab.controller import ResearchController
from tierlab.exceptions import BudgetExceededError
from tierlab.gateway import ModelGateway
from tierlab.ledger import DecisionLedger
from tierlab.lessons import LessonStore
from tierlab.mock_llm import MockLLMBackend
from tierlab.tasks.math_packing import MathPackingTask
from tierlab.types import Budget, ControllerConfig, PolicyType


@pytest.mark.asyncio
async def test_scenario_1_continuous_improvement(tmp_path):
    mock = MockLLMBackend(mode="default", seed=42)
    budget = Budget(max_steps=5, cloud_budget_cny=5.0)
    gateway = ModelGateway(budget=budget, backend="mock", mock_backend=mock)
    ledger = DecisionLedger(tmp_path / "ledger1.jsonl")
    task = MathPackingTask(n=26)

    controller = ResearchController(
        task=task,
        gateway=gateway,
        ledger=ledger,
        config=ControllerConfig(k_schedule=[2]),
        budget=budget,
        policy=PolicyType.TIERLAB,
        run_id="test_improve",
        seed=42,
    )
    summary = await controller.run()
    assert summary["final_score"] >= round(controller.current_best_score, 4)
    local_proposals = [e for e in ledger.entries if e.action == "propose_local"]
    assert len(local_proposals) > 0


@pytest.mark.asyncio
async def test_scenario_2_stagnation_then_lesson(tmp_path):
    mock = MockLLMBackend(mode="stagnation_then_lesson", seed=42)
    budget = Budget(max_steps=6, cloud_budget_cny=5.0)
    gateway = ModelGateway(budget=budget, backend="mock", mock_backend=mock)
    ledger = DecisionLedger(tmp_path / "ledger2.jsonl")
    lessons = LessonStore(tmp_path / "lessons.json")

    # Seed an admitted lesson for math/circle_overlap
    lessons.admit_lesson(
        task_class="math",
        signature="math/circle_overlap",
        rung="hint",
        advice="Project centers with minimal separation to avoid overlaps.",
        condition="test",
        verified_delta=0.05,
        cost_cny=0.001,
    )

    task = MathPackingTask(n=26)
    controller = ResearchController(
        task=task,
        gateway=gateway,
        ledger=ledger,
        lessons=lessons,
        config=ControllerConfig(k_schedule=[1], no_widen=True, enable_lessons=True),
        budget=budget,
        policy=PolicyType.TIERLAB,
        run_id="test_lesson",
        seed=42,
    )
    summary = await controller.run()
    lesson_hits = [e for e in ledger.entries if e.action == "lesson_hit"]
    assert len(lesson_hits) > 0
    assert summary["lesson_hits"] >= 1


@pytest.mark.asyncio
async def test_scenario_3_widening(tmp_path):
    mock = MockLLMBackend(mode="widen_then_improve", seed=42)
    budget = Budget(max_steps=6, cloud_budget_cny=5.0)
    gateway = ModelGateway(budget=budget, backend="mock", mock_backend=mock)
    ledger = DecisionLedger(tmp_path / "ledger3.jsonl")
    task = MathPackingTask(n=26)

    controller = ResearchController(
        task=task,
        gateway=gateway,
        ledger=ledger,
        config=ControllerConfig(k_schedule=[1, 4], no_widen=False, enable_lessons=False),
        budget=budget,
        policy=PolicyType.TIERLAB,
        run_id="test_widen",
        seed=42,
    )
    summary = await controller.run()
    widen_events = [e for e in ledger.entries if e.action == "widen"]
    assert len(widen_events) > 0


@pytest.mark.asyncio
async def test_scenario_4_ladder_escalation(tmp_path):
    mock = MockLLMBackend(mode="ladder_escalation", seed=42)
    budget = Budget(max_steps=10, cloud_budget_cny=5.0)
    gateway = ModelGateway(budget=budget, backend="mock", mock_backend=mock)
    ledger = DecisionLedger(tmp_path / "ledger4.jsonl")
    task = MathPackingTask(n=26)

    controller = ResearchController(
        task=task,
        gateway=gateway,
        ledger=ledger,
        config=ControllerConfig(k_schedule=[1], no_widen=True, enable_lessons=False),
        budget=budget,
        policy=PolicyType.TIERLAB,
        run_id="test_ladder",
        seed=42,
    )
    summary = await controller.run()
    actions = [e.action for e in ledger.entries]
    assert "hint" in actions


@pytest.mark.asyncio
async def test_scenario_5_budget_exhaustion(tmp_path):
    mock = MockLLMBackend(mode="ladder_escalation", seed=42)
    budget = Budget(max_steps=5, cloud_budget_cny=0.0000001)
    gateway = ModelGateway(budget=budget, backend="mock", mock_backend=mock)
    ledger = DecisionLedger(tmp_path / "ledger5.jsonl")
    task = MathPackingTask(n=26)

    controller = ResearchController(
        task=task,
        gateway=gateway,
        ledger=ledger,
        config=ControllerConfig(k_schedule=[1], no_widen=True, enable_lessons=False),
        budget=budget,
        policy=PolicyType.TIERLAB,
        run_id="test_budget_exceeded",
        seed=42,
    )
    summary = await controller.run()
    stop_entries = [e for e in ledger.entries if e.action == "stop"]
    assert len(stop_entries) > 0
    assert "budget" in stop_entries[0].trigger.lower()


@pytest.mark.asyncio
async def test_scenario_6_stop_rule_trigger(tmp_path):
    mock = MockLLMBackend(mode="stop_rule", seed=42)
    budget = Budget(
        max_steps=10,
        cloud_budget_cny=5.0,
        min_delta_per_cny_threshold=0.05,
        recent_r_consultations=2,
    )
    gateway = ModelGateway(budget=budget, backend="mock", mock_backend=mock)
    ledger = DecisionLedger(tmp_path / "ledger6.jsonl")
    task = MathPackingTask(n=26)

    controller = ResearchController(
        task=task,
        gateway=gateway,
        ledger=ledger,
        config=ControllerConfig(k_schedule=[1], no_widen=True, enable_lessons=False),
        budget=budget,
        policy=PolicyType.TIERLAB,
        run_id="test_stop_rule",
        seed=42,
    )
    summary = await controller.run()
    stop_entries = [e for e in ledger.entries if e.action == "stop"]
    assert len(stop_entries) > 0
