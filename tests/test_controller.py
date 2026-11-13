"""Controller tests: all policy types (including no_advice_retry), all 3 tasks, and deterministic rerun test."""

import pytest
from tierlab.controller import ResearchController
from tierlab.gateway import ModelGateway
from tierlab.ledger import DecisionLedger
from tierlab.tasks import get_task
from tierlab.types import Budget, ControllerConfig, PolicyType


@pytest.mark.asyncio
async def test_deterministic_rerun_identical_ledger(tmp_path):
    """CRITICAL REQUIREMENT: Running the exact same seed twice with mock backend

    produces identical decision ledgers (excluding timestamps and elapsed durations).
    """
    seed = 12345
    budget = Budget(max_steps=4, cloud_budget_cny=5.0)

    # Run 1
    dir1 = tmp_path / "run1"
    dir1.mkdir()
    ledger1 = DecisionLedger(dir1 / "ledger.jsonl")
    task1 = get_task("math", budget=budget)
    gw1 = ModelGateway(budget=budget, backend="mock")
    c1 = ResearchController(
        task=task1,
        gateway=gw1,
        ledger=ledger1,
        budget=budget,
        policy=PolicyType.TIERLAB,
        run_id="run_det_1",
        seed=seed,
    )
    await c1.run()

    # Run 2
    dir2 = tmp_path / "run2"
    dir2.mkdir()
    ledger2 = DecisionLedger(dir2 / "ledger.jsonl")
    task2 = get_task("math", budget=budget)
    gw2 = ModelGateway(budget=budget, backend="mock")
    c2 = ResearchController(
        task=task2,
        gateway=gw2,
        ledger=ledger2,
        budget=budget,
        policy=PolicyType.TIERLAB,
        run_id="run_det_2",
        seed=seed,
    )
    await c2.run()

    entries1 = ledger1.entries
    entries2 = ledger2.entries

    assert len(entries1) == len(entries2)
    assert len(entries1) > 0

    for e1, e2 in zip(entries1, entries2):
        assert e1.step == e2.step
        assert e1.action == e2.action
        assert e1.trigger == e2.trigger
        assert e1.score_before == e2.score_before
        assert e1.score_after == e2.score_after
        assert e1.cloud_in == e2.cloud_in
        assert e1.cloud_out == e2.cloud_out
        assert e1.verified == e2.verified
        assert e1.duplicate == e2.duplicate
        assert e1.seed == e2.seed
        assert e1.policy == e2.policy


@pytest.mark.parametrize(
    "policy",
    ["tierlab", "all_cloud", "all_local", "cascade", "static_split", "no_advice_retry"],
)
@pytest.mark.asyncio
async def test_all_policies_execute_successfully(tmp_path, policy):
    budget = Budget(max_steps=3, cloud_budget_cny=5.0)
    run_dir = tmp_path / f"policy_{policy}"
    run_dir.mkdir()
    ledger = DecisionLedger(run_dir / "ledger.jsonl")
    task = get_task("math", budget=budget)
    gw = ModelGateway(budget=budget, backend="mock")

    controller = ResearchController(
        task=task,
        gateway=gw,
        ledger=ledger,
        budget=budget,
        policy=PolicyType(policy),
        run_id=f"run_{policy}",
        seed=0,
    )
    summary = await controller.run()
    assert summary["total_steps"] > 0
    assert summary["policy"] == policy


@pytest.mark.parametrize("task_name", ["math", "code", "paper"])
@pytest.mark.asyncio
async def test_all_tasks_execute_successfully(tmp_path, task_name):
    budget = Budget(max_steps=3, cloud_budget_cny=5.0)
    run_dir = tmp_path / f"task_{task_name}"
    run_dir.mkdir()
    ledger = DecisionLedger(run_dir / "ledger.jsonl")
    task = get_task(task_name, budget=budget)
    gw = ModelGateway(budget=budget, backend="mock")

    controller = ResearchController(
        task=task,
        gateway=gw,
        ledger=ledger,
        budget=budget,
        policy=PolicyType.TIERLAB,
        run_id=f"run_{task_name}",
        seed=0,
    )
    summary = await controller.run()
    assert summary["total_steps"] > 0
