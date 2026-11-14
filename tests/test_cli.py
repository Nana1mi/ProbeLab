"""Tests for CLI entry points: run, batch, and report."""

from pathlib import Path
from tierlab.cli import main
import json
import pytest


@pytest.mark.parametrize('use_explicit_flag', [True, False])
def test_cloud_weak_config_routes_and_bills_as_cloud(tmp_path, use_explicit_flag):
    output = tmp_path / 'api_runs'
    args = ['run', '--task', 'paper', '--backend', 'mock', '--max-steps', '1',
            '--weak-backend', 'cloud_small', '--output-dir', str(output)]
    if use_explicit_flag:
        args += ['--models-config', 'configs/models.siliconflow.yaml']
    else:
        args += ['--config', 'configs/api_smoke.yaml']
    assert main(args) == 0
    summary = json.loads(next(output.rglob('summary.json')).read_text(encoding='utf-8'))
    assert summary['cloud_calls'] > 0
    assert summary['local_calls'] == 0
    assert summary['total_cloud_cny'] > 0
    assert summary['total_gpu_s'] == summary['total_gpu_cny'] == 0


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
