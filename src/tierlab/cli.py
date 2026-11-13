"""Command Line Interface for TierLab.

Commands:
  tierlab run    --task math --policy tierlab --backend mock --seed 0
  tierlab batch  configs/batch_experiment.yaml
  tierlab report runs/ --output report.html
  tierlab serve  --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from tierlab.config import load_batch_config, load_run_config
from tierlab.controller import ResearchController
from tierlab.gateway import ModelGateway
from tierlab.ledger import DecisionLedger
from tierlab.lessons import LessonStore
from tierlab.report import generate_html_report
from tierlab.tasks import get_task
from tierlab.types import Budget, ControllerConfig, PolicyType


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tierlab",
        description="TierLab: Universal Research Agent with Local GPU & Cloud Escalation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. run command
    run_p = subparsers.add_parser("run", help="Run a single research task experiment")
    run_p.add_argument("--task", type=str, default="math", help="Task name: math, code, or paper")
    run_p.add_argument(
        "--policy",
        type=str,
        default="tierlab",
        choices=["tierlab", "all_cloud", "all_local", "cascade", "static_split", "no_advice_retry"],
        help="Coordination policy",
    )
    run_p.add_argument("--backend", type=str, default="mock", choices=["mock", "real"], help="LLM backend")
    run_p.add_argument("--seed", type=int, default=0, help="Random seed for reproducibility")
    run_p.add_argument("--cloud-budget", type=float, default=5.0, help="Hard cloud budget in CNY")
    run_p.add_argument("--max-steps", type=int, default=15, help="Maximum iteration steps")
    run_p.add_argument("--max-time", type=float, default=300.0, help="Max wall-clock seconds")
    run_p.add_argument("--k-init", type=int, default=2, help="Initial parallel width K")
    run_p.add_argument("--no-widen", action="store_true", help="Ablation: disable K widening")
    run_p.add_argument("--no-lessons", action="store_true", help="Ablation: disable lesson reuse")
    run_p.add_argument("--output-dir", type=str, default="runs", help="Output directory for ledgers")
    run_p.add_argument("--config", type=str, default=None, help="Path to custom run config yaml")

    # 2. batch command
    batch_p = subparsers.add_parser("batch", help="Run a matrix of experiments from YAML")
    batch_p.add_argument("config_file", type=str, help="Path to batch experiment yaml")
    batch_p.add_argument("--output-dir", type=str, default="runs", help="Directory for ledgers")

    # 3. report command
    rep_p = subparsers.add_parser("report", help="Generate static HTML report from ledger runs")
    rep_p.add_argument("runs_dir", type=str, help="Path to directory containing run ledgers")
    rep_p.add_argument("--output", type=str, default="report.html", help="Output path for HTML report")

    # 4. serve command
    srv_p = subparsers.add_parser("serve", help="Launch FastAPI background task server")
    srv_p.add_argument("--host", type=str, default="127.0.0.1", help="Server host")
    srv_p.add_argument("--port", type=int, default=8000, help="Server port")

    return parser


def run_experiment(
    task_name: str,
    policy_name: str,
    backend: str,
    seed: int,
    cloud_budget: float,
    max_steps: int,
    max_time: float,
    k_init: int,
    no_widen: bool,
    no_lessons: bool,
    output_dir: str | Path,
    run_id: str | None = None,
) -> dict:
    """Synchronous wrapper to execute a single run."""
    run_id = run_id or f"{task_name}_{policy_name}_s{seed}_{int(time.time())}"
    run_folder = Path(output_dir) / run_id
    run_folder.mkdir(parents=True, exist_ok=True)

    ledger_path = run_folder / "ledger.jsonl"
    ledger = DecisionLedger(ledger_path)
    budget = Budget(
        cloud_budget_cny=cloud_budget,
        max_wall_clock_s=max_time,
        max_steps=max_steps,
    )
    task_instance = get_task(task_name, budget=budget)
    gateway = ModelGateway(budget=budget, backend=backend)
    lessons = LessonStore(run_folder / "lessons.json")
    controller_conf = ControllerConfig(
        k_init=k_init,
        no_widen=no_widen,
        no_lessons=no_lessons,
        backend=backend,
    )

    controller = ResearchController(
        task=task_instance,
        gateway=gateway,
        ledger=ledger,
        lessons=lessons,
        config=controller_conf,
        budget=budget,
        policy=PolicyType(policy_name),
        run_id=run_id,
        seed=seed,
    )

    summary = asyncio.run(controller.run())
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        print(f"[*] Starting TierLab run: task={args.task}, policy={args.policy}, backend={args.backend}, seed={args.seed}")
        summary = run_experiment(
            task_name=args.task,
            policy_name=args.policy,
            backend=args.backend,
            seed=args.seed,
            cloud_budget=args.cloud_budget,
            max_steps=args.max_steps,
            max_time=args.max_time,
            k_init=args.k_init,
            no_widen=args.no_widen,
            no_lessons=args.no_lessons,
            output_dir=args.output_dir,
        )
        print("\n=== RUN COMPLETED ===")
        print(f"Run ID:            {summary['run']}")
        print(f"Final Score:       {summary['final_score']}")
        print(f"Cloud Spend:       ¥{summary['total_cloud_cny']:.5f}")
        print(f"Total Steps:       {summary['total_steps']}")
        print(f"Wall Clock Time:   {summary['total_wall_s']:.2f}s")
        print(f"BA-AUC:            {summary['ba_auc']:.4f}")
        print(f"Lesson Hits:       {summary['lesson_hits']}")
        return 0

    elif args.command == "batch":
        conf = load_batch_config(args.config_file)
        tasks = conf.get("tasks", ["math"])
        policies = conf.get("policies", ["tierlab"])
        seeds = conf.get("seeds", [0])
        backend = conf.get("backend", "mock")

        budget_conf = conf.get("budget", {})
        cloud_budget = budget_conf.get("cloud_budget_cny", 5.0)
        max_time = budget_conf.get("max_wall_clock_s", 180.0)
        max_steps = budget_conf.get("max_steps", 10)

        ctrl_conf = conf.get("controller", {})
        k_init = ctrl_conf.get("k_init", 2)
        no_widen = ctrl_conf.get("no_widen", False)
        no_lessons = ctrl_conf.get("no_lessons", False)

        total_experiments = len(tasks) * len(policies) * len(seeds)
        print(f"[*] Executing batch: {len(tasks)} tasks x {len(policies)} policies x {len(seeds)} seeds = {total_experiments} runs")

        results = []
        for t in tasks:
            for p in policies:
                for s in seeds:
                    run_id = f"batch_{t}_{p}_s{s}"
                    print(f" -> Running {run_id} ...", end="", flush=True)
                    summary = run_experiment(
                        task_name=t,
                        policy_name=p,
                        backend=backend,
                        seed=s,
                        cloud_budget=cloud_budget,
                        max_steps=max_steps,
                        max_time=max_time,
                        k_init=k_init,
                        no_widen=no_widen,
                        no_lessons=no_lessons,
                        output_dir=args.output_dir,
                        run_id=run_id,
                    )
                    results.append(summary)
                    print(f" done! Score: {summary['final_score']}, Cloud: ¥{summary['total_cloud_cny']:.4f}")

        # Auto generate report
        report_file = Path(args.output_dir) / "batch_report.html"
        generate_html_report(args.output_dir, report_file)
        print(f"\n[+] Batch complete. Generated report at: {report_file}")
        return 0

    elif args.command == "report":
        out_html = generate_html_report(args.runs_dir, args.output)
        print(f"[+] Static HTML dashboard successfully written to: {args.output}")
        return 0

    elif args.command == "serve":
        import uvicorn
        from tierlab.server import app
        print(f"[*] Starting TierLab API server on {args.host}:{args.port} ...")
        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
