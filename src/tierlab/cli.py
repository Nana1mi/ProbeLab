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
from typing import Any

from tierlab.config import load_batch_config, load_run_config, load_models_config
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
    run_p.add_argument("--backend", type=str, default=None, choices=["mock", "real"], help="LLM backend")
    run_p.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    run_p.add_argument("--cloud-budget", type=float, default=None, help="Hard cloud budget in CNY")
    run_p.add_argument("--max-steps", type=int, default=None, help="Maximum iteration steps")
    run_p.add_argument("--max-time", type=float, default=None, help="Max wall-clock seconds")
    run_p.add_argument("--k-init", type=int, default=None, help="Initial parallel width K (#6)")
    run_p.add_argument("--no-widen", action="store_true", default=None, help="Ablation: disable K widening")
    run_p.add_argument("--enable-lessons", action="store_true", default=None, help="Enable lesson library (#6)")
    run_p.add_argument("--weak-backend", type=str, default=None, choices=["local", "cloud_small"], help="Weak backend (#6, #8)")
    run_p.add_argument("--local-patience", type=int, default=None, help="Stagnant rounds before consultation (#6, #12)")
    run_p.add_argument("--output-dir", type=str, default="runs", help="Output directory for ledgers")
    run_p.add_argument("--config", type=str, default=None, help="Path to custom run config yaml (#6)")
    run_p.add_argument('--models-config', default=None, help='Endpoint and price configuration YAML')

    run_p.add_argument('--dump-prompts', nargs='?', const=True, default=False,
                       help='Dump exact model requests as JSON (optional destination directory)')
    run_p.add_argument('--local-max-tokens', type=int, default=None)
    run_p.add_argument('--hint-max-tokens', type=int, default=None)
    run_p.add_argument('--max-feedback-chars', type=int, default=None)

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
    backend: str | None = None,
    seed: int | None = None,
    cloud_budget: float | None = None,
    max_steps: int | None = None,
    max_time: float | None = None,
    k_init: int | None = None,
    no_widen: bool | None = None,
    enable_lessons: bool | None = None,
    weak_backend: str | None = None,
    local_patience: int | None = None,
    output_dir: str | Path = "runs",
    run_id: str | None = None,
    config_file: str | Path | None = None,
    dump_prompts: bool | str | Path = False,
    local_max_tokens: int | None = None,
    hint_max_tokens: int | None = None,
    max_feedback_chars: int | None = None,
    models_config_file: str | Path | None = None,
) -> dict:
    """Synchronous wrapper to execute a single run with full configuration support (#6)."""
    raw_conf = load_run_config(config_file)
    c_ctrl = dict(raw_conf.get('controller', {}))
    c_budget = dict(raw_conf.get('budget', {}))
    backend = backend if backend is not None else raw_conf.get('backend', 'mock')
    seed = seed if seed is not None else raw_conf.get('seed', 0)
    overrides = dict(k_init=k_init, no_widen=no_widen, enable_lessons=enable_lessons,
                     weak_backend=weak_backend, local_patience=local_patience,
                     local_max_tokens=local_max_tokens, hint_max_tokens=hint_max_tokens,
                     max_feedback_chars=max_feedback_chars, backend=backend)
    c_ctrl.update({k: v for k, v in overrides.items() if v is not None})
    controller_conf = ControllerConfig(**c_ctrl)
    c_budget.update({k: v for k, v in dict(cloud_budget_cny=cloud_budget,
                    max_wall_clock_s=max_time, max_steps=max_steps).items() if v is not None})
    budget = Budget(**c_budget)
    run_id = run_id or f"{task_name}_{policy_name}_s{seed}_{time.time_ns()}"
    run_folder = Path(output_dir) / run_id
    run_folder.mkdir(parents=True, exist_ok=True)
    ledger = DecisionLedger(run_folder / 'ledger.jsonl')
    task_options = dict(raw_conf.get('task_options', {}).get(task_name, {}))
    task_instance = get_task(task_name, budget=budget, **task_options)
    dump_dir = (run_folder / 'prompts' if dump_prompts is True else Path(dump_prompts)) if dump_prompts else None
    models_path = models_config_file or raw_conf.get('models_config')
    gateway = ModelGateway(models_config=load_models_config(models_path) if models_path else None,
                           budget=budget, backend=backend, weak_target=controller_conf.weak_backend,
                           prompt_dump_dir=dump_dir)
    lessons = LessonStore(run_folder / 'lessons.json')

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
    (run_folder / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
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
            enable_lessons=args.enable_lessons,
            weak_backend=args.weak_backend,
            local_patience=args.local_patience,
            output_dir=args.output_dir,
            config_file=args.config,
            dump_prompts=args.dump_prompts,
            local_max_tokens=args.local_max_tokens,
            hint_max_tokens=args.hint_max_tokens,
            max_feedback_chars=args.max_feedback_chars,
            models_config_file=args.models_config,
        )
        print("\n=== RUN COMPLETED ===")
        print(f"Run ID:            {summary['run']}")
        print(f"Final Score:       {summary['final_score']}")
        print(f"Cloud Spend:       ¥{summary['total_cloud_cny']:.5f}")
        print(f"Total Steps:       {summary['total_steps']}")
        print(f"Wall Clock Time:   {summary['total_wall_s']:.2f}s")
        print(f"Cloud / local calls: {summary['cloud_calls']} / {summary['local_calls']}")
        print(f"Stop reason:       {summary['stop_reason']}")
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
        k_init = ctrl_conf.get("k_init", 1)
        no_widen = ctrl_conf.get("no_widen", False)
        enable_lessons = ctrl_conf.get("enable_lessons", False)
        weak_backend = ctrl_conf.get("weak_backend", "local")
        local_patience = ctrl_conf.get("local_patience", 2)

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
                        enable_lessons=enable_lessons,
                        weak_backend=weak_backend,
                        local_patience=local_patience,
                        output_dir=args.output_dir,
                        run_id=run_id,
                        config_file=args.config_file,
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
