"""Paper Analysis Task with Quote Grounding, Citation Localization, and Separate Scoring.

Per amended specifications:
  - #2: VerificationResult(valid, score, feedback, evidence, elapsed_s)
  - #13: Saves citation localization (filename, paragraph_idx), separates factual and synthesis scores.
  - #3: Dev feedback separate from final hidden evaluation.
  - #8: Denominator is all questions; unanswered questions score 0; quote verification is exact
        normalized substring match with length >= min_quote_chars (default 15); no bag-of-words overlap;
        answer verification uses exact match or token F1 with bounded length; no unbounded substring.
"""

from __future__ import annotations

import collections
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


def locate_exact_quote(quote: str, paragraphs: list[str], min_quote_chars: int = 15) -> int | None:
    """Find the exact paragraph index containing the quote (#8).

    Requires whitespace-normalized exact substring match and length >= min_quote_chars.
    No bag-of-words fuzzy matching allowed.
    """
    norm_quote = normalize_whitespace(quote).lower()
    if len(norm_quote) < min_quote_chars:
        return None

    for idx, p in enumerate(paragraphs):
        norm_p = normalize_whitespace(p).lower()
        if norm_quote in norm_p:
            return idx
    return None


def compute_token_f1(pred: str, target: str) -> float:
    """Compute token-level F1 score between normalized strings (#8)."""
    pred_tokens = normalize_whitespace(pred).lower().split()
    target_tokens = normalize_whitespace(target).lower().split()
    if not pred_tokens or not target_tokens:
        return 0.0

    common = collections.Counter(pred_tokens) & collections.Counter(target_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0

    precision = num_same / len(pred_tokens)
    recall = num_same / len(target_tokens)
    return 2.0 * (precision * recall) / (precision + recall)


class PaperL0Verifier(Verifier):
    level = VerifierLevel.L0
    name = "paper_l0_schema"
    is_expensive = False

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        t0 = time.perf_counter()
        raw = candidate.content.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()

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

    def __init__(self, papers_dir: Path, questions: list[dict], min_quote_chars: int = 15) -> None:
        self.papers_dir = papers_dir
        self.questions = {q["qid"]: q for q in questions}
        self.min_quote_chars = min_quote_chars

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
        valid_qids = set()

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

            # Exact quote matching with min length check (#8)
            norm_q = normalize_whitespace(quote).lower()
            if len(norm_q) < self.min_quote_chars:
                return VerificationResult(
                    valid=False,
                    score=0.0,
                    feedback=f"Quote for {qid} is shorter than minimum {self.min_quote_chars} characters: '{quote}'",
                    evidence={"qid": qid, "quote": quote, "len": len(norm_q)},
                    elapsed_s=time.perf_counter() - t0,
                    failure_signature="paper/quote_too_short",
                )

            para_idx = locate_exact_quote(quote, paragraphs, self.min_quote_chars)
            if para_idx is None:
                return VerificationResult(
                    valid=False,
                    score=0.0,
                    feedback=f"Quote for {qid} not found in {fname}: '{quote[:50]}...'",
                    evidence={"unmatched_qid": qid, "quote": quote},
                    elapsed_s=time.perf_counter() - t0,
                    failure_signature="paper/quote_unmatched",
                )

            gold_paragraph = q_info.get('gold_paragraph')
            if gold_paragraph is not None and para_idx != gold_paragraph:
                return VerificationResult(valid=False, feedback=f"Quote for {qid} does not support this question.",
                                          failure_signature='paper/wrong_paragraph',
                                          evidence={'qid': qid}, elapsed_s=time.perf_counter() - t0)
            norm_answer = normalize_whitespace(str(ans.get('answer', ''))).casefold()
            if not norm_answer or norm_answer not in normalize_whitespace(quote).casefold():
                return VerificationResult(valid=False, feedback=f"Answer for {qid} must occur in its cited quote.",
                                          failure_signature='paper/answer_not_in_quote',
                                          evidence={'qid': qid}, elapsed_s=time.perf_counter() - t0)

            citations.append({
                "qid": qid,
                "source_file": fname,
                "paragraph_idx": para_idx,
                "quote": quote,
            })
            valid_qids.add(qid)

        return VerificationResult(
            valid=True,
            score=0.5,
            feedback=f"All {len(citations)} quotes verified and localized to paragraphs",
            evidence={"citations": citations, "parsed_answers": answers, "valid_qids": list(valid_qids)},
            elapsed_s=time.perf_counter() - t0,
        )


class PaperL2Verifier(Verifier):
    level = VerifierLevel.L2
    name = "paper_l2_accuracy"
    is_expensive = False

    def __init__(self, papers_dir: Path, questions: list[dict], min_quote_chars: int = 15, max_answer_chars: int = 100) -> None:
        self.papers_dir = papers_dir
        self.questions = {q["qid"]: q for q in questions}
        self.min_quote_chars = min_quote_chars
        self.max_answer_chars = max_answer_chars

    def verify(self, candidate: Candidate, context: dict | None = None) -> VerificationResult:
        t0 = time.perf_counter()
        l1 = PaperL1Verifier(self.papers_dir, list(self.questions.values()), min_quote_chars=self.min_quote_chars).verify(candidate)
        if not l1.valid:
            return VerificationResult(
                valid=False,
                score=0.0,
                feedback="L1 quote grounding failed",
                evidence=l1.evidence,
                elapsed_s=time.perf_counter() - t0,
                failure_signature=l1.failure_signature,
            )

        valid_quotes_by_qid = {c["qid"]: c for c in l1.evidence.get("citations", [])}
        answers_by_qid = {a["qid"]: a for a in l1.evidence.get("parsed_answers", [])}

        factual_correct = 0
        factual_total = 0
        synthesis_correct = 0
        synthesis_total = 0

        # Denominator is ALL questions in question set (#8)
        for qid, q_info in self.questions.items():
            q_type = q_info.get("type", "factual")
            if q_type == "factual":
                factual_total += 1
            else:
                synthesis_total += 1

            ans_obj = answers_by_qid.get(qid)
            if not ans_obj or qid not in valid_quotes_by_qid:
                # Unanswered or ungrounded question scores 0 (#8)
                continue

            given_raw = str(ans_obj.get("answer", ""))
            norm_given = normalize_whitespace(given_raw).lower()
            norm_target = normalize_whitespace(str(q_info.get("target_answer", ""))).lower()

            # Bound answer length to reject whole-paragraph dumping (#8)
            if len(norm_given) > self.max_answer_chars:
                continue

            # Compare using exact match or token F1 >= 0.8 (#8)
            is_correct = False
            if norm_given == norm_target:
                is_correct = True
            elif compute_token_f1(norm_given, norm_target) >= 0.8:
                is_correct = True

            if is_correct:
                if q_type == "factual":
                    factual_correct += 1
                else:
                    synthesis_correct += 1

        total_questions = max(1, len(self.questions))
        factual_score = factual_correct / max(1, factual_total)
        synthesis_score = synthesis_correct / max(1, synthesis_total)
        overall_score = (factual_correct + synthesis_correct) / total_questions

        return VerificationResult(
            valid=True,
            score=round(overall_score, 4),
            feedback=(
                f"Factual: {factual_correct}/{factual_total} ({factual_score*100:.1f}%), "
                f"Synthesis: {synthesis_correct}/{synthesis_total} ({synthesis_score*100:.1f}%), "
                f"Overall: {overall_score*100:.1f}% ({factual_correct + synthesis_correct}/{total_questions})"
            ),
            evidence={
                "factual_correct": factual_correct,
                "factual_total": factual_total,
                "factual_score": round(factual_score, 4),
                "synthesis_correct": synthesis_correct,
                "synthesis_total": synthesis_total,
                "synthesis_score": round(synthesis_score, 4),
                "overall_score": round(overall_score, 4),
                "total_questions": total_questions,
            },
            elapsed_s=time.perf_counter() - t0,
        )


class PaperFinalEvaluationVerifier(Verifier):
    level = VerifierLevel.L2
    name = "paper_final_eval"
    is_expensive = True

    def __init__(self, papers_dir: Path, questions: list[dict], min_quote_chars: int = 15) -> None:
        self.v = PaperL2Verifier(papers_dir=papers_dir, questions=questions, min_quote_chars=min_quote_chars)

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
        min_quote_chars: int = 15,
    ) -> None:
        self.min_quote_chars = min_quote_chars
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
        if any('split' in q for q in questions):
            dev_questions = [q for q in questions if q.get('split') == 'dev']
            if not dev_questions or not any(q.get('split') == 'held_out' for q in questions):
                raise ValueError('Paper questions need nonempty dev and held_out groups')
        else:
            # Legacy custom fixtures; bundled experiments always use explicit split metadata.
            dev_questions = [q for q in questions if q.get("qid") in ("Q1", "Q2")] or questions[:2]
        return [
            PaperL0Verifier(),
            PaperL1Verifier(papers_dir=papers_dir, questions=dev_questions, min_quote_chars=self.min_quote_chars),
            PaperL2Verifier(papers_dir=papers_dir, questions=dev_questions, min_quote_chars=self.min_quote_chars),
        ]

    def build_final_verifier(self) -> Verifier:
        papers_dir = self._get_papers_dir()
        questions = self._load_questions()
        return PaperFinalEvaluationVerifier(papers_dir=papers_dir, questions=questions, min_quote_chars=self.min_quote_chars)

    def state_card(
        self,
        current_best: Candidate | None,
        recent_results: list[VerificationResult],
    ) -> str:
        # Programmatic structured summary reading from candidate.metadata (#4, #10)
        best_score = 0.0
        factual_score = 0.0
        synthesis_score = 0.0
        if current_best and current_best.metadata:
            ev = current_best.metadata.get("evidence", {})
            best_score = current_best.metadata.get("score", 0.0)
            factual_score = ev.get("factual_score", 0.0)
            synthesis_score = ev.get("synthesis_score", 0.0)

        last_sig = "none"
        last_msg = "none"
        if recent_results:
            failed = [r for r in recent_results if not r.valid or r.failure_signature]
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
        content = json.dumps({'answers': [
            {'qid': q['qid'], 'answer': '', 'quote': ''} for q in self._load_questions()
        ]}, indent=2)
        # Item #5: No hardcoded score in metadata; computed via run_ladder
        return Candidate(
            id="cand_paper_initial",
            content=content,
            metadata={},
        )

    def context_for_model(
        self,
        current_best: Candidate | None = None,
        rung: str | None = None,
        recent_results: list[VerificationResult] | None = None,
    ) -> str:
        best_content = current_best.content if current_best else self.initial_candidate().content
        if rung == "hint":
            truncated_cand = best_content[:400] + ("..." if len(best_content) > 400 else "")
            card = self.state_card(current_best, recent_results or [])
            return f"STATE CARD:\n{card}\nCURRENT BEST CANDIDATE (TRUNCATED):\n{truncated_cand}"

        papers_dir = self._get_papers_dir()
        paper_texts = []
        for p_file in sorted(papers_dir.glob("*.txt")):
            paper_texts.append(f"--- PAPER FILE: {p_file.name} ---\n{p_file.read_text(encoding='utf-8')}")
        all_papers_str = "\n\n".join(paper_texts)

        questions = [{key: q[key] for key in ('qid', 'type', 'question')} for q in self._load_questions()]
        q_formatted = json.dumps(questions, indent=2, ensure_ascii=False)

        return (
            f"TASK SPECIFICATION:\n"
            f"- Goal: {self.goal}\n"
            f"- Return Format: Valid JSON with root object having 'answers' list of objects: [{{'qid': '...', 'answer': '...', 'quote': '...'}}]\n"
            f"- Quote Requirements: A relevant verbatim substring from the paragraph supporting each question, length >= {self.min_quote_chars} chars; the normalized answer must occur in that quote.\n"
            f"- Execution timeout limit: 10.0 seconds\n\n"
            f"FULL SOURCE PAPERS:\n{all_papers_str}\n\n"
            f"ALL QUESTIONS TO ANSWER:\n{q_formatted}\n\n"
            f"CURRENT BEST CANDIDATE JSON:\n```json\n{best_content}\n```"
        )
