"""Tests for CLI entry points: run, batch, and report."""

from pathlib import Path
from tierlab.cli import main


def test_cli_run_math(tmp_path: Path):
    out_dir = tmp_path / "cli_runs"
    ret = main([
        "run",
        "--task", "math",
        "--policy", "tierlab",
        "--backend", "mock",
        "--seed", "0",
        "--max-steps", "3",
        "--output-dir", str(out_dir),
    ])
    assert ret == 0
    assert out_dir.is_dir()
    ledgers = list(out_dir.rglob("*.jsonl"))
    assert len(ledgers) == 1


def test_cli_run_code(tmp_path: Path):
    out_dir = tmp_path / "cli_code_runs"
    ret = main([
        "run",
        "--task", "code",
        "--policy", "cascade",
        "--backend", "mock",
        "--seed", "0",
        "--max-steps", "2",
        "--output-dir", str(out_dir),
    ])
    assert ret == 0
    assert len(list(out_dir.rglob("*.jsonl"))) == 1


def test_cli_run_paper(tmp_path: Path):
    out_dir = tmp_path / "cli_paper_runs"
    ret = main([
        "run",
        "--task", "paper",
        "--policy", "all_local",
        "--backend", "mock",
        "--seed", "0",
        "--max-steps", "2",
        "--output-dir", str(out_dir),
    ])
    assert ret == 0
    assert len(list(out_dir.rglob("*.jsonl"))) == 1


def test_cli_batch(tmp_path: Path):
    batch_yaml = tmp_path / "test_batch.yaml"
    batch_yaml.write_text("""
tasks: ["math"]
policies: ["tierlab", "all_local"]
seeds: [0]
backend: "mock"
budget:
  cloud_budget_cny: 5.0
  max_steps: 2
  max_wall_clock_s: 60.0
controller:
  k_init: 1
""", encoding="utf-8")

    out_dir = tmp_path / "batch_out"
    ret = main([
        "batch",
        str(batch_yaml),
        "--output-dir", str(out_dir),
    ])
    assert ret == 0
    assert len(list(out_dir.glob("*.jsonl")) + list(out_dir.rglob("*.jsonl"))) >= 2


def test_cli_report(tmp_path: Path):
    out_dir = tmp_path / "runs_for_rep"
    main([
        "run",
        "--task", "math",
        "--backend", "mock",
        "--max-steps", "2",
        "--output-dir", str(out_dir),
    ])

    report_html = tmp_path / "my_report.html"
    ret = main([
        "report",
        str(out_dir),
        "--output", str(report_html),
    ])
    assert ret == 0
    assert report_html.is_file()
