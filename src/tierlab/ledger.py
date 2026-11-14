"""Decision ledger writer, reader, and metrics computation.

Per amended specifications:
  - #9: cloud_cny, gpu_s, gpu_cny (converted via rho_gpu), wall_s are recorded separately.
  - #10: verify_s recorded per row; run-level start/end timestamps recorded, wall clock is end - start;
         total GPU lease cost (wall_s * rho_gpu) reported separately from actual busy GPU seconds.
  - #11: Hint attribution: ROI and statistics for consultation rungs compute delta including credited local steps.
  - #13: BA-AUC: staircase hold integration over fixed budget B divided by B; identical formula when cloud spend is 0.
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
        self.run_start_time: float | None = None
        self.run_end_time: float | None = None
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

    def summary(
        self,
        rho_gpu_cny_per_s: float = 0.00167,
        budget_b: float = 5.0,
    ) -> dict[str, Any]:
        """Compute summary statistics for the ledger (#9, #10, #11, #13)."""
        if not self.entries:
            return {
                "total_steps": 0,
                "final_score": 0.0,
                "total_cloud_cny": 0.0,
                "total_gpu_s": 0.0,
                "total_gpu_cny": 0.0,
                "total_wall_s": 0.0,
                "total_gpu_lease_cny": 0.0,
                "total_verify_s": 0.0,
                "total_cloud_tokens": 0,
                "ba_auc": 0.0,
                "consultations_by_rung": {},
                "lesson_hits": 0,
                "duplicates_skipped": 0,
            }

        total_cloud_cny = sum(e.cloud_cny for e in self.entries)
        total_gpu_s = sum(e.gpu_s for e in self.entries)
        total_gpu_cny = sum(e.gpu_cny for e in self.entries)
        total_verify_s = sum(e.verify_s for e in self.entries)

        # Run-level wall clock time (end - start) (#10)
        if self.run_start_time is not None and self.run_end_time is not None:
            total_wall_s = max(0.0, self.run_end_time - self.run_start_time)
        else:
            total_wall_s = sum(e.wall_s for e in self.entries)

        # Separate total GPU lease cost (#10)
        total_gpu_lease_cny = round(total_wall_s * rho_gpu_cny_per_s, 6)

        total_cloud_tokens = sum(e.cloud_in + e.cloud_out for e in self.entries)
        final_score = self.entries[-1].score_after
        lesson_hits = sum(1 for e in self.entries if e.action == "lesson_hit")
        duplicates_skipped = sum(1 for e in self.entries if e.duplicate)

        # Attributed consultation statistics (#11)
        consultations_by_rung: dict[str, dict[str, Any]] = {}
        for rung in ("hint", "solve", "diagnose", "replan"):
            rung_entries = [e for e in self.entries if e.action == rung]
            count = len(rung_entries)
            cost = sum(e.cloud_cny for e in rung_entries)

            # Direct deltas from the rung calls
            direct_delta = sum(max(0.0, e.score_after - e.score_before) for e in rung_entries)
            direct_verified = sum(1 for e in rung_entries if e.verified and e.score_after > e.score_before)

            # Plus deltas credited to this rung (#11)
            # Find steps that point to this rung via credited_to
            attributed_delta = direct_delta
            attributed_verified = direct_verified
            if rung == "hint":
                # Check all entries in ledger credited to any hint step
                for step_entry in self.entries:
                    if step_entry.credited_to and "hint" in step_entry.credited_to:
                        delta = max(0.0, step_entry.score_after - step_entry.score_before)
                        attributed_delta += delta
                        if delta > 0:
                            attributed_verified += 1

            consultations_by_rung[rung] = {
                "count": count,
                "verified_improvements": attributed_verified,
                "total_delta": round(attributed_delta, 4),
                "total_cost_cny": round(cost, 6),
                "roi_delta_per_cny": round(attributed_delta / max(cost, 1e-6), 2) if cost > 0 else 0.0,
            }

        ba_auc = compute_ba_auc(self.entries, budget_b=budget_b)

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
            "total_gpu_lease_cny": total_gpu_lease_cny,
            "total_verify_s": round(total_verify_s, 3),
            "total_cloud_tokens": total_cloud_tokens,
            "ba_auc": round(ba_auc, 4),
            "consultations_by_rung": consultations_by_rung,
            "lesson_hits": lesson_hits,
            "duplicates_skipped": duplicates_skipped,
            "cloud_calls": sum(e.extra.get('model_calls', {}).get('cloud', 0) for e in self.entries),
            "local_calls": sum(e.extra.get('model_calls', {}).get('local', 0) for e in self.entries),
            "small_model_calls": (sum(e.extra['model_calls_by_target']['local'] + e.extra['model_calls_by_target']['cloud_small']
                                      for e in self.entries) if all('model_calls_by_target' in e.extra for e in self.entries) else None),
            "strong_model_calls": (sum(e.extra['model_calls_by_target']['cloud'] for e in self.entries)
                                       if all('model_calls_by_target' in e.extra for e in self.entries) else None),
            "stop_reason": next((e.trigger for e in reversed(self.entries) if e.action == 'stop'), 'unknown'),
        }


def compute_ba_auc(entries: list[LedgerEntry], budget_b: float = 5.0) -> float:
    """Compute Area Under the Curve (BA-AUC) over fixed budget B using staircase hold (#13).

    Formula:
      Integrate score over cumulative cloud cost from 0 to B, divided by B.
      Each score achieved is held constant until the next cloud spend occurs.
      When cloud cost is 0, the integral over [0, B] divided by B naturally equals final_score.
    """
    if not entries:
        return 0.0

    b = max(1e-9, float(budget_b))
    cum_cost = 0.0
    current_score = entries[0].score_before
    total_integral = 0.0

    for e in entries:
        cost = max(0.0, e.cloud_cny)
        if cost > 0:
            next_cost = min(b, cum_cost + cost)
            dx = next_cost - cum_cost
            total_integral += current_score * dx
            cum_cost = next_cost
        current_score = e.score_after

    # Zero-order hold over remaining budget up to B (#13)
    if cum_cost < b:
        total_integral += current_score * (b - cum_cost)

    return round(float(total_integral / b), 4)
