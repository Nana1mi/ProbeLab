"""Consultation ladder prompt templates and token limits.

Per amendment #4:
  - First version activates 'hint' and 'solve' actions.
  - 'diagnose' and 'replan' templates are preserved but default disabled.
  - 'solve' directly produces candidate solution without local rewriting.
"""

from __future__ import annotations

from tierlab.types import ConsultationRung

RUNG_MAX_TOKENS = {
    ConsultationRung.HINT: 150,
    ConsultationRung.SOLVE: 2048,
    ConsultationRung.DIAGNOSE: 400,
    ConsultationRung.REPLAN: 800,
}

SYSTEM_PROMPT_PREFIX = (
    "You are an expert research scientist assisting an autonomous research agent. "
    "All outputs must be strictly actionable, scientifically precise, and within the length limit. "
    "Do not include conversational filler."
)

SYSTEM_PROMPTS = {
    ConsultationRung.HINT: (
        f"{SYSTEM_PROMPT_PREFIX} Provide a brief strategic direction or intuition (max 150 tokens). "
        "Do not write full code or detailed implementations."
    ),
    ConsultationRung.SOLVE: (
        f"{SYSTEM_PROMPT_PREFIX} Directly implement the complete solution to achieve verified improvement. "
        "Output complete, valid code or data matching the task specifications."
    ),
    ConsultationRung.DIAGNOSE: (
        f"{SYSTEM_PROMPT_PREFIX} Diagnose the root cause of failure and state exactly one reusable guideline rule (max 400 tokens)."
    ),
    ConsultationRung.REPLAN: (
        f"{SYSTEM_PROMPT_PREFIX} Propose an alternative algorithmic design or reformulated plan to escape stagnation (max 800 tokens)."
    ),
}


def build_consultation_messages(
    rung: ConsultationRung,
    state_card: str,
    failure_detail: str | None = None,
    experiment_history_summary: str | None = None,
    full_context: str | None = None,
) -> list[dict[str, str]]:
    system_content = SYSTEM_PROMPTS.get(rung, SYSTEM_PROMPTS[ConsultationRung.HINT])
    user_parts: list[str] = [f"### CURRENT STATE CARD\n{state_card}"]

    if rung == ConsultationRung.HINT:
        user_parts.append(
            "\n### INSTRUCTION\n"
            "The local solver is stalled. Provide a concise strategic hint or direction (<= 150 tokens) "
            "for the next iteration."
        )
    elif rung == ConsultationRung.SOLVE:
        ctx = full_context or state_card
        user_parts.append(f"\n### FULL CONTEXT\n{ctx}")
        user_parts.append(
            "\n### INSTRUCTION\n"
            "Directly generate the complete improved candidate solution code/JSON now."
        )
    elif rung == ConsultationRung.DIAGNOSE:
        detail = failure_detail or "No failure detail provided."
        user_parts.append(f"\n### FAILURE TRACE / MINIMAL SAMPLE\n{detail}")
        user_parts.append(
            "\n### INSTRUCTION\n"
            "Identify why the candidate failed and formulate one general reusable rule (<= 400 tokens)."
        )
    elif rung == ConsultationRung.REPLAN:
        history = experiment_history_summary or "Multiple iterations failed to yield improvement."
        user_parts.append(f"\n### RECENT EXPERIMENT ATTEMPTS\n{history}")
        user_parts.append(
            "\n### INSTRUCTION\n"
            "Provide an alternative structural reformulation or plan (<= 800 tokens)."
        )

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def build_local_prompt(
    task_description: str,
    state_card: str,
    lesson_advice: str | None = None,
    cloud_guidance: str | None = None,
    temperature_angle: str | None = None,
) -> list[dict[str, str]]:
    system = (
        "You are an autonomous code/solution generator. Output only the requested solution "
        "matching specifications without conversational commentary."
    )
    user_parts = [
        f"### TASK\n{task_description}",
        f"\n### CURRENT STATE\n{state_card}",
    ]

    if lesson_advice:
        user_parts.append(f"\n### REUSED PROVEN LESSON\n{lesson_advice}")

    if cloud_guidance:
        user_parts.append(f"\n### CLOUD ADVICE / GUIDANCE\n{cloud_guidance}")

    if temperature_angle:
        user_parts.append(f"\n### VARIATION ANGLE\n{temperature_angle}")

    user_parts.append("\n### INSTRUCTION\nGenerate the complete revised candidate solution now.")

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]
