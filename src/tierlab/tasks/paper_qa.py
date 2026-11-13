"""Paper Analysis Task with Quote Grounding, Citation Localization, and Separate Scoring.

Per amended specifications:
  - #2: VerificationResult(valid, score, feedback, evidence, elapsed_s)
  - #13: Saves citation localization (filename, paragraph_idx), separates factual and synthesis scores.
  - #3: Dev feedback separate from final hidden evaluation.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from tierlab.tasks.base import ResearchTask, Verifier
from tierlab.types import (
    Budget,
    Candidate,
    VerificationResult,
    VerifierLevel,
)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def locate_quote_in_paragraphs(quote: str, paragraphs: list[str], threshold: float = 0.80) -> int | None:
    """Find the index of the paragraph containing the quote (or highest token overlap)."""
    norm_quote = normalize_whitespace(quote).lower()
    if not norm_quote:
        return None

    # Exact substring in a paragraph
    for idx, p in enumerate(paragraphs):
        norm_p = normalize_whitespace(p).lower()
        if norm_quote in norm_p:
            return idx

    # Fuzzy token overlap per paragraph
    quote_tokens = set(norm_quote.split())
    if not quote_tokens:
        return None

    best_idx = None
    best_overlap = 0.0
    for idx, p in enumerate(paragraphs):
        p_tokens = set(normalize_whitespace(p).lower().split())
        overlap = len(quote_tokens.intersection(p_tokens)) / len(quote_tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            best_idx = idx

    if best_overlap >= threshold:
        return best_idx
    return None


class PaperL0Verifier(Verifier):
    level = VerifierLevel.L0
    name = "paper_l0_schema"
    is_expensive = False

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        t0 = time.perf_counter()
        raw = candidate.content.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?", "", raw)
            raw = re.sub(r"```$", "", raw).strip()

        try:
            data = json.loads(raw)
        except Exception as e:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback=f"JSON decoding error: {e}",
                evidence={"error": str(e)},
                elapsed_s=time.perf_counter() - t0,
                failure_signature="format/json_parse_error",
            )

        if not isinstance(data, dict) or "answers" not in data:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback="JSON root must contain 'answers' list",
                evidence={"data": data},
                elapsed_s=time.perf_counter() - t0,
                failure_signature="format/schema_missing_answers",
            )

        answers = data.get("answers")
        if not isinstance(answers, list) or len(answers) == 0:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback="'answers' must be a non-empty list",
                evidence={},
                elapsed_s=time.perf_counter() - t0,
                failure_signature="format/empty_answers",
            )

        for i, item in enumerate(answers):
            if not isinstance(item, dict):
                return VerificationResult(
                    valid=False,
                    score=0.0,
                    feedback=f"Item {i} is not a valid JSON object",
                    evidence={"item": item},
                    elapsed_s=time.perf_counter() - t0,
                    failure_signature="format/invalid_item_format",
                )
            for req in ("qid", "answer", "quote"):
                if req not in item:
                    return VerificationResult(
                        valid=False,
                        score=0.0,
                        feedback=f"Item {i} missing required key '{req}'",
                        evidence={"item": item},
                        elapsed_s=time.perf_counter() - t0,
                        failure_signature="format/schema_missing_key",
                    )

        return VerificationResult(
            valid=True,
            score=0.2,
            feedback=f"Valid JSON schema with {len(answers)} answers",
            evidence={"parsed_answers": answers},
            elapsed_s=time.perf_counter() - t0,
        )


class PaperL1Verifier(Verifier):
    level = VerifierLevel.L1
    name = "paper_l1_grounding_and_localization"
    is_expensive = False

    def __init__(self, papers_dir: Path, questions: list[dict], threshold: float = 0.80) -> None:
        self.papers_dir = papers_dir
        self.questions = {q["qid"]: q for q in questions}
        self.threshold = threshold

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        t0 = time.perf_counter()
        l0 = PaperL0Verifier().verify(candidate)
        if not l0.valid:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback="L0 schema failed",
                evidence=l0.evidence,
                elapsed_s=time.perf_counter() - t0,
                failure_signature=l0.failure_signature,
            )

        answers = l0.evidence.get("parsed_answers", [])
        citations = []

        for ans in answers:
            qid = ans.get("qid")
            quote = ans.get("quote", "")
            q_info = self.questions.get(qid)
            if not q_info:
                continue

            fname = q_info.get("source_file", "sample_paper_1.txt")
            source_file = self.papers_dir / fname
            if not source_file.is_file():
                return VerificationResult(
                    valid=False,
                    score=0.0,
                    feedback=f"Source paper {source_file} not found",
                    evidence={"missing_file": str(source_file)},
                    elapsed_s=time.perf_counter() - t0,
                    failure_signature="paper/missing_source_file",
                )

            paper_text = source_file.read_text(encoding="utf-8")
            paragraphs = [p.strip() for p in paper_text.split("\n\n") if p.strip()]

            para_idx = locate_quote_in_paragraphs(quote, paragraphs, self.threshold)
            if para_idx is None:
                return VerificationResult(
                    valid=False,
                    score=0.0,
                    feedback=f"Quote for {qid} not found in {fname}: '{quote[:50]}...'",
                    evidence={"unmatched_qid": qid, "quote": quote},
                    elapsed_s=time.perf_counter() - t0,
                    failure_signature="paper/quote_unmatched",
                )

            # Record citation localization (amendment #13)
            citations.append({
                "qid": qid,
                "source_file": fname,
                "paragraph_idx": para_idx,
                "quote": quote,
            })

        return VerificationResult(
            valid=True,
            score=0.5,
            feedback=f"All {len(citations)} quotes verified and localized to paragraphs",
            evidence={"citations": citations, "parsed_answers": answers},
            elapsed_s=time.perf_counter() - t0,
        )


class PaperL2Verifier(Verifier):
    level = VerifierLevel.L2
    name = "paper_l2_accuracy"
    is_expensive = False

    def __init__(self, questions: list[dict]) -> None:
        self.questions = {q["qid"]: q for q in questions}

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        t0 = time.perf_counter()
        l0 = PaperL0Verifier().verify(candidate)
        if not l0.valid:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback="L0 schema failed",
                evidence=l0.evidence,
                elapsed_s=time.perf_counter() - t0,
                failure_signature=l0.failure_signature,
            )

        answers = l0.evidence.get("parsed_answers", [])
        factual_correct = 0
        factual_total = 0
        synthesis_correct = 0
        synthesis_total = 0

        for ans in answers:
            qid = ans.get("qid")
            given = normalize_whitespace(str(ans.get("answer", ""))).lower()
            q_info = self.questions.get(qid)
            if not q_info:
                continue

            q_type = q_info.get("type", "factual")
            target = normalize_whitespace(str(q_info.get("target_answer", ""))).lower()
            is_correct = (given == target or target in given)

            if q_type == "factual":
                factual_total += 1
                if is_correct:
                    factual_correct += 1
            else:
                synthesis_total += 1
                if is_correct:
                    synthesis_correct += 1

        factual_score = factual_correct / max(1, factual_total)
        synthesis_score = synthesis_correct / max(1, synthesis_total)
        overall_score = (factual_correct + synthesis_correct) / max(1, factual_total + synthesis_total)

        return VerificationResult(
            valid=True,
            score=round(overall_score, 4),
            feedback=(
                f"Factual: {factual_correct}/{factual_total} ({factual_score*100:.1f}%), "
                f"Synthesis: {synthesis_correct}/{synthesis_total} ({synthesis_score*100:.1f}%), "
                f"Overall: {overall_score*100:.1f}%"
            ),
            evidence={
                "factual_correct": factual_correct,
                "factual_total": factual_total,
                "factual_score": round(factual_score, 4),
                "synthesis_correct": synthesis_correct,
                "synthesis_total": synthesis_total,
                "synthesis_score": round(synthesis_score, 4),
                "overall_score": round(overall_score, 4),
            },
            elapsed_s=time.perf_counter() - t0,
        )


class PaperFinalEvaluationVerifier(Verifier):
    level = VerifierLevel.L2
    name = "paper_final_eval"
    is_expensive = True

    def __init__(self, questions: list[dict]) -> None:
        self.v = PaperL2Verifier(questions)

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        res = self.v.verify(candidate, context)
        res.evidence["final_eval"] = True
        return res


class PaperQATask(ResearchTask):
    task_class = "paper"
    goal = "Answer questions with verbatim quotes and paragraph localization, scoring factual and synthesis questions separately."

    def __init__(
        self,
        workspace: Path | None = None,
        budget: Budget | None = None,
        threshold: float = 0.80,
    ) -> None:
        self.threshold = threshold
        super().__init__(workspace=workspace, budget=budget)

    def _get_papers_dir(self) -> Path:
        candidates = [
            self.workspace / "data" / "papers",
            Path("data/papers"),
            Path(__file__).resolve().parent.parent.parent.parent / "data" / "papers",
        ]
        for c in candidates:
            if c.is_dir():
                return c
        return Path("data/papers")

    def _load_questions(self) -> list[dict]:
        papers_dir = self._get_papers_dir()
        q_file = papers_dir / "questions.json"
        if q_file.is_file():
            return json.loads(q_file.read_text(encoding="utf-8"))
        return [
            {
                "qid": "Q1",
                "type": "factual",
                "question": "What is the peak memory usage reported? [Placeholder Example]",
                "target_answer": "4.2 GB",
                "source_file": "sample_paper_1.txt",
            },
            {
                "qid": "Q2",
                "type": "factual",
                "question": "What baseline algorithm is compared against? [Placeholder Example]",
                "target_answer": "SimpleGreedy",
                "source_file": "sample_paper_1.txt",
            },
            {
                "qid": "Q3",
                "type": "synthesis",
                "question": "What two mechanisms govern overall efficiency? [Placeholder Example]",
                "target_answer": "local widening and escalation",
                "source_file": "sample_paper_1.txt",
            },
        ]

    def build_ladder(self) -> list[Verifier]:
        papers_dir = self._get_papers_dir()
        questions = self._load_questions()
        # Dev questions (Q1 and Q2)
        dev_questions = [q for q in questions if q.get("qid") in ("Q1", "Q2")] or questions[:2]
        return [
            PaperL0Verifier(),
            PaperL1Verifier(papers_dir=papers_dir, questions=dev_questions, threshold=self.threshold),
            PaperL2Verifier(questions=dev_questions),
        ]

    def build_final_verifier(self) -> Verifier:
        questions = self._load_questions()
        return PaperFinalEvaluationVerifier(questions=questions)

    def state_card(
        self,
        current_best: Candidate | None,
        recent_results: list[VerificationResult],
    ) -> str:
        best_score = 0.0
        factual_score = 0.0
        synthesis_score = 0.0
        if current_best and "evidence" in current_best.metadata:
            ev = current_best.metadata["evidence"]
            best_score = ev.get("overall_score", 0.0)
            factual_score = ev.get("factual_score", 0.0)
            synthesis_score = ev.get("synthesis_score", 0.0)

        last_sig = "none"
        last_msg = "none"
        if recent_results:
            failed = [r for r in recent_results if not r.valid]
            if failed:
                last_sig = failed[-1].failure_signature or "unknown"
                last_msg = failed[-1].feedback
            else:
                last_sig = "success"
                last_msg = recent_results[-1].feedback

        card = (
            f"TASK: {self.task_class.upper()}\n"
            f"GOAL: {self.goal}\n"
            f"CURRENT_BEST_SCORE: {best_score:.4f} (Factual: {factual_score:.2f}, Synthesis: {synthesis_score:.2f})\n"
            f"LAST_FAILURE_SIGNATURE: {last_sig}\n"
            f"DEV_FEEDBACK: {last_msg}\n"
            f"REQUIREMENT: JSON object with 'answers': list of objects with qid, answer, and verbatim quote."
        )
        return card

    def failure_signature(self, results: list[VerificationResult]) -> str:
        for r in results:
            if not r.valid:
                return r.failure_signature or "paper/unknown_failure"
        return "paper/success"

    def initial_candidate(self) -> Candidate:
        content = json.dumps(
            {
                "answers": [
                    {
                        "qid": "Q1",
                        "answer": "4.2 GB",
                        "quote": "Under full load, the system reached a peak memory usage of 4.2 GB.",
                    },
                    {
                        "qid": "Q2",
                        "answer": "UnknownBaseline",
                        "quote": "We compare our approach against the SimpleGreedy baseline.",
                    },
                ]
            },
            indent=2,
        )
        return Candidate(
            id="cand_paper_initial",
            content=content,
            metadata={"score": 0.5, "verified": True},
        )
