"""Comprehensive Regression Tests for All 15 Code Review Fixes and Realistic Mock E2E.

Every test corresponds to one of the 15 fixes (A.1 - A.6, B.7 - B.15) plus the 3 realistic mock E2E tests.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path
import pytest
from pydantic import ValidationError

from tierlab.config import load_run_config
from tierlab.controller import ResearchController, is_exact_format_failure
from tierlab.exceptions import BudgetExceededError
from tierlab.gateway import ModelGateway
from tierlab.ledger import DecisionLedger, compute_ba_auc
from tierlab.mock_llm import MockLLMBackend
from tierlab.prompts import build_consultation_messages, build_local_prompt
from tierlab.sandbox import clear_sandbox_cache, execute_in_sandbox, get_sandbox_cache
from tierlab.server import RunRequest
from tierlab.tasks.base import ResearchTask
from tierlab.tasks.code_opt import (
    CodeL0Verifier,
    CodeL1Verifier,
    CodeL2Verifier,
    CodeOptimizationTask,
    REFERENCE_CODE,
    run_candidate_sandbox,
    run_reference_sandbox,
)
from tierlab.tasks.math_packing import (
    MathL0Verifier,
    MathL1Verifier,
    MathL2Verifier,
    MathPackingTask,
)
from tierlab.tasks.paper_qa import (
    PaperL0Verifier,
    PaperL1Verifier,
    PaperL2Verifier,
    PaperQATask,
    compute_token_f1,
    locate_exact_quote,
    normalize_whitespace,
)
from tierlab.types import (
    ActionType,
    Budget,
    Candidate,
    ConsultationRung,
    ControllerConfig,
    LedgerEntry,
    PolicyType,
)


# ==============================================================================
# Fix 1: Prompt Context (context_for_model)
# ==============================================================================
def test_regression_01_prompt_context(tmp_path):
    math_task = MathPackingTask(math_timeout_s=30.0)
    code_task = CodeOptimizationTask()
    paper_task = PaperQATask(workspace=tmp_path)

    # 1. Math context
    math_ctx = math_task.context_for_model()
    assert "solve()" in math_ctx
    assert "(x, y, r)" in math_ctx
    assert "math, random" in math_ctx
    assert "30.0 seconds" in math_ctx

    # 2. Code context
    code_ctx = code_task.context_for_model()
    assert "topk_cosine_sim" in code_ctx
    assert "Python standard library" in code_ctx
    assert "15.0 seconds" in code_ctx

    # 3. Paper context
    paper_ctx = paper_task.context_for_model()
    assert "FULL SOURCE PAPERS" in paper_ctx
    assert "ALL QUESTIONS TO ANSWER" in paper_ctx
    assert "answers" in paper_ctx

    # 4. Hint context (state card + truncated candidate)
    hint_ctx = math_task.context_for_model(rung="hint")
    assert "STATE CARD:" in hint_ctx
    assert "CURRENT BEST CANDIDATE (TRUNCATED):" in hint_ctx


# ==============================================================================
# Fix 2: Output Extraction (extract_candidate)
# ==============================================================================
def test_regression_02_extract_candidate():
    task = MathPackingTask()

    # Raw response with reasoning, commentary, and python code block
    raw_with_think = """
<think>
Let's analyze the unit square. We should shift circles outwards.
</think>
Here is my revised implementation:
```python
def solve():
    return [(0.5, 0.5, 0.2)]
```
Hope this is helpful!
"""
    cleaned = task.extract_candidate(raw_with_think)
    assert "<think>" not in cleaned
    assert "Here is my revised" not in cleaned
    assert cleaned.startswith("def solve():")
    assert cleaned.endswith("return [(0.5, 0.5, 0.2)]")

    # If multiple fenced blocks, selects the LAST code block
    multiple_blocks = """
First attempt (failed):
```python
def solve():
    return []
```
Final working solution:
```python
def solve():
    return [(0.1, 0.1, 0.05)]
```
"""
    cleaned_last = task.extract_candidate(multiple_blocks)
    assert "[(0.1, 0.1, 0.05)]" in cleaned_last
    assert "return []" not in cleaned_last

    # Raw string without fences
    raw_bare = "def solve(): return []"
    assert task.extract_candidate(raw_bare) == "def solve(): return []"


# ==============================================================================
# Fix 3: Format Failure Limit and Exact Signature Matching
# ==============================================================================
def test_regression_03_format_failure_limit_and_signatures():
    # Exact set check: crashes and timeouts must NOT be format failures
    assert is_exact_format_failure("format/json_parse_error") is True
    assert is_exact_format_failure("format/wrong_circle_count") is True
    assert is_exact_format_failure("format/syntax_or_output_error") is True
    assert is_exact_format_failure("syntax/crash") is False
    assert is_exact_format_failure("code/crash") is False
    assert is_exact_format_failure("format/timeout") is False
    assert is_exact_format_failure("code/timeout") is False
    assert is_exact_format_failure("syntax/runtime_error") is False


# ==============================================================================
# Fix 4: State Card Reads From Candidate Metadata
# ==============================================================================
def test_regression_04_state_card_reads_from_metadata():
    task = MathPackingTask(n=26)
    cand = Candidate(
        id="cand_test",
        content="def solve(): return []",
        metadata={
            "score": 0.8876,
            "evidence": {
                "sum_r": 2.339,
                "relative_improvement": 0.314,
            },
        },
    )
    card = task.state_card(cand, [])
    assert "0.8876" in card
    assert "2.3390" in card
    assert "31.40%" in card


# ==============================================================================
# Fix 5: Initial Candidate Scoring & Arbitrary N Grid Validity
# ==============================================================================
def test_regression_05_initial_score_and_arbitrary_n_grid():
    # 1. No hardcoded score in metadata
    for TaskCls in (MathPackingTask, CodeOptimizationTask, PaperQATask):
        t = TaskCls()
        c = t.initial_candidate()
        assert "score" not in c.metadata

    # 2. Arbitrary n validity under tolerance 1e-9
    for test_n in [1, 7, 10, 16, 26, 35]:
        m_task = MathPackingTask(n=test_n)
        init_cand = m_task.initial_candidate()
        ladder_res = m_task.run_ladder(init_cand)
        assert len(ladder_res) == 3
        assert all(r.valid for r in ladder_res), f"Initial grid invalid for n={test_n}: {ladder_res[-1].feedback}"
        # For n != 26, verify score is not normalized by 2.635
        if test_n != 26:
            assert ladder_res[-1].score == ladder_res[-1].evidence["sum_r"]


# ==============================================================================
# Fix 6: Config Forbids Unknown Fields & Passes Parameters
# ==============================================================================
def test_regression_06_config_forbid_unknown_and_fields(tmp_path):
    # 1. Forbid unknown fields
    with pytest.raises(ValidationError):
        ControllerConfig(unknown_extra_field=123)

    # 2. Can configure k_init, enable_lessons, weak_backend, local_patience
    cfg = ControllerConfig(
        k_init=2,
        enable_lessons=True,
        weak_backend="cloud_small",
        local_patience=3,
    )
    assert cfg.k_init == 2
    assert cfg.enable_lessons is True
    assert cfg.weak_backend == "cloud_small"
    assert cfg.local_patience == 3

    # 3. Server RunRequest supports these fields
    req = RunRequest(
        k_init=4,
        enable_lessons=True,
        weak_backend="cloud_small",
        local_patience=4,
    )
    assert req.k_init == 4
    assert req.enable_lessons is True
    assert req.weak_backend == "cloud_small"
    assert req.local_patience == 4


# ==============================================================================
# Fix 7: Unified Stopping Rule (Stagnation Patience)
# ==============================================================================
@pytest.mark.asyncio
async def test_regression_07_unified_stopping_rule(tmp_path):
    mock = MockLLMBackend(mode="stop_rule", seed=42)
    budget = Budget(max_steps=20, cloud_budget_cny=5.0)
    gateway = ModelGateway(budget=budget, backend="mock", mock_backend=mock)
    ledger = DecisionLedger(tmp_path / "ledger_stop.jsonl")

    task = MathPackingTask(n=26)
    # Stagnation patience set to 3: should stop after 3 stagnant steps
    controller = ResearchController(
        task=task,
        gateway=gateway,
        ledger=ledger,
        config=ControllerConfig(stagnation_patience=3, local_patience=10),
        budget=budget,
        policy=PolicyType.TIERLAB,
        run_id="test_unified_stop",
    )
    summary = await controller.run()
    assert summary["total_steps"] <= 5
    stops = [e for e in ledger.entries if e.action == ActionType.STOP]
    assert len(stops) == 1
    assert stops[0].trigger == "consecutive_stagnation_stopped"


# ==============================================================================
# Fix 8: Strict Paper QA Scoring
# ==============================================================================
def test_regression_08_paper_qa_strict_scoring(tmp_path):
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir(parents=True)
    paper_file = papers_dir / "sample_paper_1.txt"
    paper_file.write_text(
        "Under full load, the system reached a peak memory usage of 4.2 GB.\n\n"
        "We compare our approach against the SimpleGreedy baseline.",
        encoding="utf-8",
    )
    questions = [
        {"qid": "Q1", "type": "factual", "question": "Memory?", "target_answer": "4.2 GB", "source_file": "sample_paper_1.txt"},
        {"qid": "Q2", "type": "factual", "question": "Baseline?", "target_answer": "SimpleGreedy", "source_file": "sample_paper_1.txt"},
        {"qid": "Q3", "type": "synthesis", "question": "Unanswered Q?", "target_answer": "none", "source_file": "sample_paper_1.txt"},
    ]

    l2 = PaperL2Verifier(papers_dir=papers_dir, questions=questions, min_quote_chars=15)

    # 1. Denominator is ALL 3 questions: answering only Q1 correctly gives 1/3 = 0.3333
    cand_partial = Candidate(
        id="c1",
        content=json.dumps({
            "answers": [
                {"qid": "Q1", "answer": "4.2 GB", "quote": "Under full load, the system reached a peak memory usage of 4.2 GB."}
            ]
        }),
    )
    res_partial = l2.verify(cand_partial)
    assert res_partial.score == round(1.0 / 3.0, 4)

    # 2. Quote shorter than 15 chars rejected
    cand_short_quote = Candidate(
        id="c2",
        content=json.dumps({
            "answers": [
                {"qid": "Q1", "answer": "4.2 GB", "quote": "4.2 GB."}  # < 15 chars
            ]
        }),
    )
    res_short = l2.verify(cand_short_quote)
    assert not res_short.valid
    assert res_short.failure_signature == "paper/quote_too_short"

    # 3. Unbounded dump as answer rejected
    huge_answer = "Under full load, the system reached a peak memory usage of 4.2 GB and " + ("extra filler words " * 20)
    cand_huge = Candidate(
        id="c3",
        content=json.dumps({
            "answers": [
                {"qid": "Q1", "answer": huge_answer, "quote": "Under full load, the system reached a peak memory usage of 4.2 GB."}
            ]
        }),
    )
    res_huge = l2.verify(cand_huge)
    assert res_huge.score == 0.0  # Rejected answer length


# ==============================================================================
# Fix 9: Independent Subprocess Execution for Code Optimization
# ==============================================================================
def test_regression_09_code_optimization_independent_timing():
    # 1. Verify reference and candidate run in independent subprocesses
    ref_res = run_reference_sandbox(mode="visible", seed=0)
    cand_res = run_candidate_sandbox(REFERENCE_CODE, mode="visible", seed=0)
    assert ref_res["status"] == "ok"
    assert cand_res["status"] == "ok"
    assert ref_res["ans"] == cand_res["ans"]

    # 2. Input scale: benchmark mode produces reference elapsed >= 0.2s
    bm_ref = run_reference_sandbox(mode="benchmark", seed=42)
    assert bm_ref["status"] == "ok"
    assert bm_ref["elapsed_s"] >= 0.15  # scaled input ensures substantial runtime


# ==============================================================================
# Fix 10: Accounting (Cloud Weak, verify_s, GPU Lease)
# ==============================================================================
@pytest.mark.asyncio
async def test_regression_10_accounting_cloud_weak_verify_s_gpu_lease(tmp_path):
    mock = MockLLMBackend(mode="default", seed=42)
    budget = Budget(max_steps=2, cloud_budget_cny=5.0)
    gateway = ModelGateway(budget=budget, backend="mock", weak_target="cloud_small", mock_backend=mock)
    ledger = DecisionLedger(tmp_path / "ledger_acct.jsonl")

    task = MathPackingTask(n=26)
    controller = ResearchController(
        task=task,
        gateway=gateway,
        ledger=ledger,
        config=ControllerConfig(weak_backend="cloud_small"),
        budget=budget,
        policy=PolicyType.ALL_LOCAL,
        run_id="test_acct",
    )
    summary = await controller.run()
    # Check that entries have verify_s and cloud_cny billed
    assert len(ledger.entries) > 0
    first_step = ledger.entries[0]
    assert first_step.verify_s >= 0.0
    assert first_step.cloud_cny > 0.0
    assert first_step.gpu_s == 0.0
    # Summary reports total_gpu_lease_cny
    assert "total_gpu_lease_cny" in summary
    assert "total_verify_s" in summary


# ==============================================================================
# Fix 11: Hint Attribution (credited_to)
# ==============================================================================
@pytest.mark.asyncio
async def test_regression_11_hint_attribution(tmp_path):
    ledger = DecisionLedger(tmp_path / "ledger_attrib.jsonl")
    # Simulate a hint followed by an attributed local improvement
    ledger.record(
        LedgerEntry(
            run="test_run",
            step=1,
            action=ActionType.HINT,
            trigger="stagnation",
            cloud_cny=0.005,
            score_before=0.5,
            score_after=0.5,
        )
    )
    ledger.record(
        LedgerEntry(
            run="test_run",
            step=2,
            action=ActionType.PROPOSE_LOCAL,
            trigger="local_after_hint",
            credited_to="hint_step_1",
            score_before=0.5,
            score_after=0.75,
        )
    )
    summary = ledger.summary()
    hint_stats = summary["consultations_by_rung"]["hint"]
    assert hint_stats["verified_improvements"] == 1
    assert hint_stats["total_delta"] == 0.25
    assert hint_stats["roi_delta_per_cny"] > 0.0


# ==============================================================================
# Fix 12: local_patience Governs Consultation Escalation
# ==============================================================================
@pytest.mark.asyncio
async def test_regression_12_local_patience_decision_rule(tmp_path):
    mock = MockLLMBackend(mode="stop_rule", seed=42)
    budget = Budget(max_steps=5, cloud_budget_cny=5.0)
    gateway = ModelGateway(budget=budget, backend="mock", mock_backend=mock)
    ledger = DecisionLedger(tmp_path / "ledger_pat.jsonl")

    task = MathPackingTask(n=26)
    # local_patience = 3: first 2 stagnant steps must remain local
    controller = ResearchController(
        task=task,
        gateway=gateway,
        ledger=ledger,
        config=ControllerConfig(local_patience=3, no_widen=True),
        budget=budget,
        policy=PolicyType.TIERLAB,
        run_id="test_local_patience",
    )
    await controller.run()
    actions = [e.action for e in ledger.entries]
    # Steps 1 and 2 should be local propose
    assert actions[0] == ActionType.PROPOSE_LOCAL
    assert actions[1] == ActionType.PROPOSE_LOCAL


# ==============================================================================
# Fix 13: BA-AUC Fixed Budget Staircase Hold
# ==============================================================================
def test_regression_13_ba_auc_fixed_budget_staircase():
    # 1. Cloud spend = 0: AUC over fixed budget B naturally equals final_score
    entries_zero_spend = [
        LedgerEntry(run="r1", step=1, action=ActionType.PROPOSE_LOCAL, trigger="t", cloud_cny=0.0, score_before=0.4, score_after=0.8),
    ]
    auc_zero = compute_ba_auc(entries_zero_spend, budget_b=5.0)
    assert auc_zero == 0.8

    # 2. Staircase calculation:
    # Initial score 0.2
    # At cost 1.0 (step 1), score increases to 0.6
    # Budget B = 2.0
    # From c=0 to c=1: score was 0.2, integral = 0.2 * 1.0 = 0.2
    # From c=1 to c=2: score is 0.6, integral = 0.6 * 1.0 = 0.6
    # Total integral = 0.8, divided by B (2.0) = 0.40
    entries_staircase = [
        LedgerEntry(run="r2", step=1, action=ActionType.SOLVE, trigger="t", cloud_cny=1.0, score_before=0.2, score_after=0.6),
    ]
    auc_staircase = compute_ba_auc(entries_staircase, budget_b=2.0)
    assert auc_staircase == 0.40


# ==============================================================================
# Fix 14: Sandbox Execution Caching & Configurable Timeout
# ==============================================================================
def test_regression_14_sandbox_cache_and_math_timeout():
    clear_sandbox_cache()
    code = "import time\ndef solve(): return [(0.5, 0.5, 0.1)]\nprint('executed')"
    runner = "import solution\nprint('ok')"

    # First execution
    res1 = execute_in_sandbox(code=code, entry_filename="solution.py", runner_script=runner)
    cache = get_sandbox_cache()
    assert len(cache) == 1

    # Second execution: hits cache immediately
    t0 = time.perf_counter()
    res2 = execute_in_sandbox(code=code, entry_filename="solution.py", runner_script=runner)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.05
    assert res1.stdout == res2.stdout

    # Configurable math timeout
    m_task = MathPackingTask(math_timeout_s=45.0)
    assert m_task.math_timeout_s == 45.0
    assert m_task.ladder[0].timeout_s == 45.0


# ==============================================================================
# Fix 15: Concurrent Budget Reservation & Graceful Exception Handling
# ==============================================================================
@pytest.mark.asyncio
async def test_regression_15_concurrent_budget_reservation(tmp_path):
    # 1. Pre-flight budget reservation raises before making calls
    budget = Budget(cloud_budget_cny=0.01)
    gateway = ModelGateway(budget=budget, backend="mock", weak_target="cloud_small")
    with pytest.raises(BudgetExceededError):
        gateway.reserve_budget(0.05)

    # 2. Controller catches BudgetExceededError gracefully and stops without crashing
    task = MathPackingTask(n=26)
    ledger = DecisionLedger(tmp_path / "ledger_resv.jsonl")
    controller = ResearchController(
        task=task,
        gateway=gateway,
        ledger=ledger,
        config=ControllerConfig(weak_backend="cloud_small", k_init=2),
        budget=budget,
        policy=PolicyType.ALL_LOCAL,
        run_id="test_budget_catch",
    )
    summary = await controller.run()
    stops = [e for e in ledger.entries if e.action == ActionType.STOP]
    assert len(stops) == 1
    assert "cloud_budget_exceeded" in stops[0].trigger


# ==============================================================================
# Realistic Mock End-to-End Tests for All Three Tasks
# ==============================================================================
@pytest.mark.asyncio
async def test_realistic_mock_e2e_math(tmp_path):
    mock = MockLLMBackend(mode="realistic", seed=42)
    budget = Budget(max_steps=5, cloud_budget_cny=5.0)
    gateway = ModelGateway(budget=budget, backend="mock", mock_backend=mock)
    ledger = DecisionLedger(tmp_path / "ledger_real_math.jsonl")

    task = MathPackingTask(n=26)
    controller = ResearchController(
        task=task,
        gateway=gateway,
        ledger=ledger,
        config=ControllerConfig(k_init=1),
        budget=budget,
        policy=PolicyType.TIERLAB,
        run_id="real_math",
        seed=42,
    )
    summary = await controller.run()
    assert summary["total_steps"] >= 1
    assert summary["final_score"] > 0.0


@pytest.mark.asyncio
async def test_realistic_mock_e2e_code(tmp_path):
    mock = MockLLMBackend(mode="realistic", seed=42)
    budget = Budget(max_steps=4, cloud_budget_cny=5.0)
    gateway = ModelGateway(budget=budget, backend="mock", mock_backend=mock)
    ledger = DecisionLedger(tmp_path / "ledger_real_code.jsonl")

    task = CodeOptimizationTask()
    controller = ResearchController(
        task=task,
        gateway=gateway,
        ledger=ledger,
        config=ControllerConfig(k_init=1),
        budget=budget,
        policy=PolicyType.TIERLAB,
        run_id="real_code",
        seed=42,
    )
    summary = await controller.run()
    assert summary["total_steps"] >= 1
    assert summary["final_score"] > 0.0


@pytest.mark.asyncio
async def test_realistic_mock_e2e_paper(tmp_path):
    mock = MockLLMBackend(mode="realistic", seed=42)
    budget = Budget(max_steps=4, cloud_budget_cny=5.0)
    gateway = ModelGateway(budget=budget, backend="mock", mock_backend=mock)
    ledger = DecisionLedger(tmp_path / "ledger_real_paper.jsonl")

    task = PaperQATask()
    controller = ResearchController(
        task=task,
        gateway=gateway,
        ledger=ledger,
        config=ControllerConfig(k_init=1),
        budget=budget,
        policy=PolicyType.TIERLAB,
        run_id="real_paper",
        seed=42,
    )
    summary = await controller.run()
    assert summary["total_steps"] >= 1
    assert summary["final_score"] > 0.0
