"""Tests for deterministic task verifiers (Math, Code, Paper QA) under amended specifications."""

from pathlib import Path
from tierlab.tasks.code_opt import CodeOptimizationTask
from tierlab.tasks.math_packing import MathPackingTask
from tierlab.tasks.paper_qa import PaperQATask
from tierlab.types import Candidate


# --- MATH TASK TESTS ---
def test_math_verifier_initial_candidate():
    task = MathPackingTask(n=26)
    cand = task.initial_candidate()
    results = task.run_ladder(cand)

    assert len(results) == 3
    assert results[0].valid  # L0
    assert results[1].valid  # L1
    assert results[2].valid  # L2
    assert results[2].score > 0.5
    assert "relative_improvement" in results[2].evidence


def test_math_verifier_circle_overlap():
    task = MathPackingTask(n=26)
    code = """
def solve():
    circles = []
    circles.append((0.2, 0.2, 0.1))
    circles.append((0.25, 0.2, 0.1))
    for i in range(2, 26):
        circles.append((0.8, 0.8, 0.001))
    return circles
"""
    cand = Candidate(id="cand_overlap", content=code)
    results = task.run_ladder(cand)
    assert results[0].valid
    assert not results[1].valid
    assert results[1].failure_signature == "math/circle_overlap"


def test_math_verifier_out_of_bounds():
    task = MathPackingTask(n=26)
    code = """
def solve():
    circles = [(1.05, 0.5, 0.1)] + [(0.5, 0.5, 0.001)] * 25
    return circles
"""
    cand = Candidate(id="cand_oob", content=code)
    results = task.run_ladder(cand)
    assert not results[1].valid
    assert results[1].failure_signature == "math/out_of_bounds"


# --- CODE TASK TESTS ---
def test_code_verifier_baseline_insufficient_speedup():
    task = CodeOptimizationTask(noise_threshold=0.15)
    cand = task.initial_candidate()
    results = task.run_ladder(cand)

    assert len(results) == 3
    assert results[0].valid
    assert results[1].valid
    assert results[2].failure_signature == "code/insufficient_speedup"


def test_code_verifier_optimized_speedup():
    task = CodeOptimizationTask(noise_threshold=0.08)
    from tierlab.mock_llm import MockLLMBackend
    mock = MockLLMBackend()
    cand = Candidate(id="cand_opt", content=mock._get_optimized_code())
    results = task.run_ladder(cand)

    assert len(results) == 3
    assert results[0].valid
    assert results[1].valid
    assert results[2].valid
    assert results[2].score >= 0.2


def test_code_verifier_syntax_error():
    task = CodeOptimizationTask()
    cand = Candidate(id="cand_err", content="def topk_cosine_sim(): broken syntax")
    results = task.run_ladder(cand)
    assert not results[0].valid
    assert "syntax" in results[0].failure_signature


# --- PAPER QA TASK TESTS ---
def test_paper_verifier_valid_answers_and_citation_localization():
    task = PaperQATask(workspace=Path("."))
    from tierlab.mock_llm import MockLLMBackend
    mock = MockLLMBackend()
    cand = Candidate(id="cand_paper_ok", content=mock._get_perfect_paper_json())
    results = task.run_ladder(cand)

    assert len(results) == 3
    assert results[0].valid  # L0 schema
    assert results[1].valid  # L1 quote grounding & localization
    assert "citations" in results[1].evidence
    citations = results[1].evidence["citations"]
    assert len(citations) >= 2
    assert "paragraph_idx" in citations[0]

    assert results[2].valid  # L2 accuracy
    assert results[2].evidence["factual_score"] == 1.0


def test_paper_verifier_unmatched_quote():
    task = PaperQATask(workspace=Path("."))
    from tierlab.mock_llm import MockLLMBackend
    mock = MockLLMBackend()
    cand = Candidate(id="cand_paper_bad_quote", content=mock._get_unmatched_quote_paper_json())
    results = task.run_ladder(cand)

    assert results[0].valid
    assert not results[1].valid
    assert results[1].failure_signature == "paper/quote_unmatched"
