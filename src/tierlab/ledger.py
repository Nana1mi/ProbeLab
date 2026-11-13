"""Decision ledger writer, reader, and metrics computation.

Per amendment #9:
  - cloud_cny, gpu_s, gpu_cny (converted via rho_gpu), wall_s are recorded separately.
  - Zero synthetic blended "total price" output.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tierlab.types import LedgerEntry


class DecisionLedger:
    """Manages appending and loading run ledger entries in JSONL format."""

    def __init__(self, file_path: Path | str) -> None:
        self.file_path = Path(file_path)
        self.entries: list[LedgerEntry] = []
        if self.file_path.is_file():
            self.load()

    def load(self) -> list[LedgerEntry]:
        self.entries = []
        if not self.file_path.is_file():
            return self.entries
        with open(self.file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        self.entries.append(LedgerEntry(**json.loads(line)))
                    except Exception:
                        pass
        return self.entries

    def record(self, entry: LedgerEntry) -> None:
        """Append an entry to in-memory list and write to JSONL immediately."""
        if not entry.timestamp:
            entry.timestamp = datetime.now(timezone.utc).isoformat()
        self.entries.append(entry)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file_path, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def summary(self) -> dict[str, Any]:
        """Compute summary statistics for the ledger."""
        if not self.entries:
            return {
                "total_steps": 0,
                "final_score": 0.0,
                "total_cloud_cny": 0.0,
                "total_gpu_s": 0.0,
                "total_gpu_cny": 0.0,
                "total_wall_s": 0.0,
                "total_cloud_tokens": 0,
                "ba_auc": 0.0,
                "consultations_by_rung": {},
                "lesson_hits": 0,
                "duplicates_skipped": 0,
            }

        total_cloud_cny = sum(e.cloud_cny for e in self.entries)
        total_gpu_s = sum(e.gpu_s for e in self.entries)
        total_gpu_cny = sum(e.gpu_cny for e in self.entries)
        total_wall_s = sum(e.wall_s for e in self.entries)
        total_cloud_tokens = sum(e.cloud_in + e.cloud_out for e in self.entries)
        final_score = self.entries[-1].score_after
        lesson_hits = sum(1 for e in self.entries if e.action == "lesson_hit")
        duplicates_skipped = sum(1 for e in self.entries if e.duplicate)

        consultations_by_rung: dict[str, dict[str, Any]] = {}
        for rung in ("hint", "solve", "diagnose", "replan"):
            rung_entries = [e for e in self.entries if e.action == rung]
            count = len(rung_entries)
            verified_count = sum(1 for e in rung_entries if e.verified and e.score_after > e.score_before)
            total_delta = sum(max(0.0, e.score_after - e.score_before) for e in rung_entries)
            cost = sum(e.cloud_cny for e in rung_entries)
            consultations_by_rung[rung] = {
                "count": count,
                "verified_improvements": verified_count,
                "total_delta": round(total_delta, 4),
                "total_cost_cny": round(cost, 6),
                "roi_delta_per_cny": round(total_delta / max(cost, 1e-6), 2) if cost > 0 else 0.0,
            }

        ba_auc = compute_ba_auc(self.entries)

        return {
            "run": self.entries[0].run,
            "policy": self.entries[0].policy,
            "seed": self.entries[0].seed,
            "config_hash": self.entries[0].config_hash,
            "total_steps": len(self.entries),
            "final_score": round(final_score, 4),
            "total_cloud_cny": round(total_cloud_cny, 6),
            "total_gpu_s": round(total_gpu_s, 2),
            "total_gpu_cny": round(total_gpu_cny, 6),
            "total_wall_s": round(total_wall_s, 2),
            "total_cloud_tokens": total_cloud_tokens,
            "ba_auc": round(ba_auc, 4),
            "consultations_by_rung": consultations_by_rung,
            "lesson_hits": lesson_hits,
            "duplicates_skipped": duplicates_skipped,
        }


def compute_ba_auc(entries: list[LedgerEntry]) -> float:
    """Compute Area Under the Curve for Completion Score vs Cumulative Cloud Cost."""
    if not entries:
        return 0.0

    costs = [0.0]
    scores = [entries[0].score_before]

    cum_cost = 0.0
    for e in entries:
        cum_cost += e.cloud_cny
        costs.append(cum_cost)
        scores.append(e.score_after)

    if cum_cost == 0.0:
        return float(scores[-1])

    auc = 0.0
    for i in range(1, len(costs)):
        dx = costs[i] - costs[i - 1]
        avg_y = (scores[i] + scores[i - 1]) / 2.0
        auc += avg_y * dx

    return float(auc)
