"""Lightweight FastAPI server for running and monitoring TierLab tasks."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from tierlab.config import load_run_config
from tierlab.controller import ResearchController
from tierlab.gateway import ModelGateway
from tierlab.ledger import DecisionLedger
from tierlab.lessons import LessonStore
from tierlab.report import generate_html_report
from tierlab.tasks import get_task
from tierlab.types import Budget, ControllerConfig, PolicyType

app = FastAPI(title="TierLab Research Server", version="0.1.0")

# In-memory registry of runs
RUNS_REGISTRY: dict[str, dict[str, Any]] = {}
RUNS_DIR = Path("runs")


class RunRequest(BaseModel):
    task: str = "math"
    policy: str = "tierlab"
    backend: str = "mock"
    seed: int = 0
    cloud_budget_cny: float = 5.0
    max_wall_clock_s: float = 300.0
    max_steps: int = 15
    k_init: int = 2


async def _execute_task_worker(run_id: str, req: RunRequest) -> None:
    try:
        run_folder = RUNS_DIR / run_id
        run_folder.mkdir(parents=True, exist_ok=True)
        ledger_path = run_folder / "ledger.jsonl"
        ledger = DecisionLedger(ledger_path)

        budget = Budget(
            cloud_budget_cny=req.cloud_budget_cny,
            max_wall_clock_s=req.max_wall_clock_s,
            max_steps=req.max_steps,
        )
        task_instance = get_task(req.task, budget=budget)
        gateway = ModelGateway(budget=budget, backend=req.backend)
        lessons = LessonStore(run_folder / "lessons.json")
        controller_conf = ControllerConfig(k_init=req.k_init, backend=req.backend)

        controller = ResearchController(
            task=task_instance,
            gateway=gateway,
            ledger=ledger,
            lessons=lessons,
            config=controller_conf,
            budget=budget,
            policy=PolicyType(req.policy),
            run_id=run_id,
            seed=req.seed,
        )

        RUNS_REGISTRY[run_id]["status"] = "running"
        summary = await controller.run()

        # Generate report
        report_path = run_folder / "report.html"
        generate_html_report(run_folder, report_path)

        RUNS_REGISTRY[run_id].update({
            "status": "completed",
            "summary": summary,
            "final_score": summary.get("final_score", 0.0),
            "report_path": str(report_path),
        })
    except Exception as e:
        RUNS_REGISTRY[run_id].update({
            "status": "failed",
            "error": str(e),
        })


@app.post("/runs")
async def start_run(req: RunRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Start a research experiment run in the background."""
    run_id = f"run_{req.task}_{req.policy}_{uuid.uuid4().hex[:8]}"
    RUNS_REGISTRY[run_id] = {
        "run_id": run_id,
        "task": req.task,
        "policy": req.policy,
        "status": "queued",
        "seed": req.seed,
    }
    background_tasks.add_task(_execute_task_worker, run_id, req)
    return {"run_id": run_id, "status": "queued"}


@app.get("/runs/{run_id}")
async def get_run_status(run_id: str) -> dict[str, Any]:
    """Retrieve status and progress metrics for a run."""
    if run_id not in RUNS_REGISTRY:
        # Check if run folder exists on disk
        target_ledger = RUNS_DIR / run_id / "ledger.jsonl"
        if target_ledger.is_file():
            l = DecisionLedger(target_ledger)
            return {"run_id": run_id, "status": "completed", "summary": l.summary()}
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    return RUNS_REGISTRY[run_id]


@app.get("/runs/{run_id}/report", response_class=HTMLResponse)
async def get_run_report(run_id: str) -> str:
    """Retrieve the self-contained HTML dashboard for a run."""
    run_folder = RUNS_DIR / run_id
    if not run_folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Run folder for {run_id} not found")

    html = generate_html_report(run_folder)
    return html


def create_app() -> FastAPI:
    return app
