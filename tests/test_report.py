"""Tests for static HTML report and dashboard generation."""

from pathlib import Path
from tierlab.ledger import DecisionLedger
from tierlab.report import generate_html_report
from tierlab.types import LedgerEntry


def test_generate_html_report(tmp_path: Path):
    run_dir = tmp_path / "run_01"
    run_dir.mkdir()
    ledger_path = run_dir / "ledger.jsonl"
    ledger = DecisionLedger(ledger_path)

    ledger.record(
        LedgerEntry(
            run="run_01",
            step=1,
            action="propose_local",
            trigger="init",
            cloud_cny=0.0,
            wall_s=1.2,
            score_before=0.6,
            score_after=0.65,
            verified=True,
            seed=0,
            policy="tierlab",
        )
    )
    ledger.record(
        LedgerEntry(
            run="run_01",
            step=2,
            action="hint",
            trigger="stagnation",
            cloud_in=150,
            cloud_out=30,
            cloud_cny=0.0015,
            wall_s=2.0,
            score_before=0.65,
            score_after=0.70,
            verified=True,
            seed=0,
            policy="tierlab",
        )
    )

    out_file = tmp_path / "test_report.html"
    html = generate_html_report(tmp_path, out_file)

    assert "<!DOCTYPE html>" in html
    assert "TierLab Research Dashboard" in html
    assert "data:image/png;base64," in html
    assert out_file.is_file()
    assert len(out_file.read_text(encoding="utf-8")) > 500
