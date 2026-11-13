"""State card compression and token-budget enforcement.

Per Section 3.1 & 3.3: Compressed state representation constrained to <= 600 tokens
(approximately 2400 characters). Over-limit state cards are deterministically truncated
and recorded.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel

DEFAULT_MAX_STATE_CARD_CHARS = 2400  # Approx 600 tokens


class StateCardResult(BaseModel):
    content: str
    original_chars: int
    truncated: bool = False
    truncated_chars: int = 0


def format_and_truncate_state_card(
    raw_content: str,
    max_chars: int = DEFAULT_MAX_STATE_CARD_CHARS,
) -> StateCardResult:
    """Ensure state card adheres to the upper length budget.

    If it exceeds max_chars, truncates the middle or tail with an explicit audit notice.
    """
    orig_len = len(raw_content)
    if orig_len <= max_chars:
        return StateCardResult(
            content=raw_content,
            original_chars=orig_len,
            truncated=False,
            truncated_chars=0,
        )

    trunc_notice = f"\n[STATE_CARD TRUNCATED: Original {orig_len} chars exceeded limit {max_chars} chars]\n"
    allowed_body_chars = max_chars - len(trunc_notice)
    if allowed_body_chars <= 0:
        truncated_content = raw_content[:max_chars]
    else:
        truncated_content = raw_content[:allowed_body_chars] + trunc_notice

    return StateCardResult(
        content=truncated_content,
        original_chars=orig_len,
        truncated=True,
        truncated_chars=orig_len - len(truncated_content),
    )
