"""Lightweight FastAPI server for running and monitoring TierLab tasks."""

from __future__ import annotations

import asyncio
import uuid
import json
import re
import threading
from pathlib import Path
from typing import Any, Literal
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tierlab.config import load_models_config
from tierlab.controller import ResearchController
from tierlab.gateway import ModelGateway
from tierlab.ledger import DecisionLedger
from tierlab.lessons import LessonStore
from tierlab.report import generate_html_report
from tierlab.tasks import get_task
from tierlab.types import Budget, ControllerConfig, PolicyType
from tierlab.trace import read_trace, event_preview, trace_stats

app = FastAPI(title="VELA Research Workspace", version="0.1.0")
UI_DIR = Path(__file__).parent / 'web'
app.mount('/ui', StaticFiles(directory=UI_DIR), name='vela-ui')
RUN_LOCK = threading.Lock()

# In-memory registry of runs
RUNS_REGISTRY: dict[str, dict[str, Any]] = {}
RUNS_DIR = Path("runs")


class RunRequest(BaseModel):
    task: Literal['math', 'code', 'paper'] = "math"
    policy: Literal['tierlab', 'all_cloud', 'all_local', 'cascade', 'static_split', 'no_advice_retry'] = "tierlab"
    backend: Literal['mock', 'real'] = "mock"
    seed: int = 0
    cloud_budget_cny: float = Field(default=5.0, gt=0)
    max_wall_clock_s: float = Field(default=300.0, gt=0)
    max_steps: int = Field(default=15, gt=0, le=1000)
    k_init: Literal[1, 2, 4] = 1
    enable_lessons: bool = False
    weak_backend: Literal['local', 'cloud_small'] = "local"
    models_profile: Literal['default', 'siliconflow'] = 'default'
    local_patience: int = Field(default=2, gt=0)
    local_max_tokens: int = Field(default=2048, gt=0)
    hint_max_tokens: int = Field(default=400, gt=0)
    max_feedback_chars: int = Field(default=2400, ge=256)
    code_allowed_libraries: list[str] = Field(default_factory=lambda: ['stdlib', 'numpy'])
    code_max_speedup: float = Field(default=50.0, gt=1)
    timing_tolerance_s: float = Field(default=2.0, gt=0)


def _run_folder(run_id: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', run_id):
        raise HTTPException(status_code=400, detail='Invalid run identifier')
    folder = (RUNS_DIR / run_id).resolve()
    if not folder.is_relative_to(RUNS_DIR.resolve()):
        raise HTTPException(status_code=400, detail='Invalid run path')
    return folder


def _persist(run_id: str) -> None:
    folder = _run_folder(run_id)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / 'status.tmp'
    tmp.write_text(json.dumps(RUNS_REGISTRY[run_id], ensure_ascii=False), encoding='utf-8')
    tmp.replace(folder / 'status.json')


async def _execute_task_run(run_id: str, req: RunRequest) -> None:
    try:
        run_folder = _run_folder(run_id)
        run_folder.mkdir(parents=True, exist_ok=True)
        ledger_path = run_folder / "ledger.jsonl"
        ledger = DecisionLedger(ledger_path)

        budget = Budget(
            cloud_budget_cny=req.cloud_budget_cny,
            max_wall_clock_s=req.max_wall_clock_s,
            max_steps=req.max_steps,
        )
        task_options = (dict(allowed_libraries=req.code_allowed_libraries, max_speedup=req.code_max_speedup,
                             timing_tolerance_s=req.timing_tolerance_s)
                        if req.task in ('code', 'code_opt', 'cosine') else {})
        task_instance = get_task(req.task, budget=budget, **task_options)
        models = load_models_config('configs/models.siliconflow.yaml') if req.models_profile == 'siliconflow' else load_models_config()
        gateway = ModelGateway(models_config=models, budget=budget, backend=req.backend, weak_target=req.weak_backend)
        RUNS_REGISTRY[run_id]['rho_gpu_cny_per_s'] = models.rho_gpu_cny_per_s
        lessons = LessonStore(run_folder / "lessons.json")
        controller_conf = ControllerConfig(
            k_init=req.k_init,
            enable_lessons=req.enable_lessons,
            weak_backend=req.weak_backend,
            local_patience=req.local_patience,
            backend=req.backend,
            local_max_tokens=req.local_max_tokens,
            hint_max_tokens=req.hint_max_tokens,
            max_feedback_chars=req.max_feedback_chars,
        )

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
        _persist(run_id)
        summary = await controller.run()
        (run_folder / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

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
    finally:
        if 'gateway' in locals():
            for client in (gateway._cloud_client, gateway._local_client, gateway._cloud_small_client):
                if client is not None:
                    await client.close()
        _persist(run_id)


async def _execute_task_worker(run_id: str, req: RunRequest) -> None:
    # Benchmarks and the sandbox cache share process resources: serialize runs,
    # while keeping HTTP polling and the event loop responsive.
    def work() -> None:
        with RUN_LOCK:
            asyncio.run(_execute_task_run(run_id, req))
    await asyncio.to_thread(work)


@app.get('/', response_class=HTMLResponse)
async def workspace() -> str:
    return (UI_DIR / 'index.html').read_text(encoding='utf-8')


@app.get('/health')
async def health() -> dict:
    return {'status': 'ok', 'name': 'VELA'}


@app.get('/runs')
async def list_runs() -> dict:
    ids = set(RUNS_REGISTRY)
    if RUNS_DIR.exists():
        ids.update(p.name for p in RUNS_DIR.iterdir() if p.is_dir() and ((p/'ledger.jsonl').is_file() or (p/'status.json').is_file()))
    items = []
    for run_id in sorted(ids, reverse=True)[:100]:
        try:
            items.append(await get_run_status(run_id))
        except HTTPException:
            continue
    return {'runs': items}


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
        'backend': req.backend,
        'request': req.model_dump(),
    }
    _persist(run_id)
    background_tasks.add_task(_execute_task_worker, run_id, req)
    return {"run_id": run_id, "status": "queued"}


@app.get("/runs/{run_id}")
async def get_run_status(run_id: str) -> dict[str, Any]:
    """Retrieve status and progress metrics for a run."""
    folder = _run_folder(run_id)
    data = dict(RUNS_REGISTRY.get(run_id, {}))
    if not data and (folder/'status.json').is_file():
        data = json.loads((folder/'status.json').read_text(encoding='utf-8'))
        if data.get('status') in ('queued', 'running'):
            data['status'] = 'interrupted'
    ledger = DecisionLedger(folder/'ledger.jsonl')
    if not data and not ledger.entries:
        raise HTTPException(status_code=404, detail=f'Run {run_id} not found')
    if not data:
        data = {'run_id': run_id, 'status': 'unknown', 'task': run_id.split('_')[0]}
        if (folder/'summary.json').is_file():
            data.update(status='completed', summary=json.loads((folder/'summary.json').read_text(encoding='utf-8')))
    if ledger.entries and 'summary' not in data:
        data['summary'] = ledger.summary(rho_gpu_cny_per_s=data.get('rho_gpu_cny_per_s', 0), budget_b=data.get('request', {}).get('cloud_budget_cny', 5))
    data['ledger_count'] = len(ledger.entries)
    if ledger.entries:
        task_match = re.search(r'(?:^|_)(math|code|paper)(?:_|$)', run_id)
        if data.get('task') not in ('math', 'code', 'paper') and task_match:
            data['task'] = task_match.group(1)
        data.setdefault('policy', ledger.entries[0].policy)
        data.setdefault('seed', ledger.entries[0].seed)
        data['latest'] = ledger.entries[-1].model_dump()
    return data


@app.get('/runs/{run_id}/ledger')
async def get_run_ledger(run_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=500)) -> dict:
    await get_run_status(run_id)
    entries = DecisionLedger(_run_folder(run_id)/'ledger.jsonl').entries
    batch = entries[offset:offset+limit]
    return {'entries': [e.model_dump() for e in batch], 'next_offset': offset+len(batch), 'total': len(entries)}


@app.get("/runs/{run_id}/report", response_class=HTMLResponse)
async def get_run_report(run_id: str) -> str:
    """Retrieve the self-contained HTML dashboard for a run."""
    run_folder = _run_folder(run_id)
    if not run_folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Run folder for {run_id} not found")

    html = generate_html_report(run_folder)
    return html


@app.get('/runs/{run_id}/collaboration')
async def get_collaboration(run_id: str, offset: int = Query(default=0, ge=0),
                            limit: int = Query(default=100, ge=1, le=200)) -> dict:
    state = await get_run_status(run_id)
    events = read_trace(_run_folder(run_id)/'trace.ndjson')
    batch = events[offset:offset+limit]
    return {'run_id': run_id, 'task': state.get('task'), 'status': state['status'], 'backend': state.get('backend'),
            'summary': state.get('summary'), 'stats': trace_stats(events) if events else None,
            'legacy': not events and state['status'] not in ('queued', 'running'),
            'events': [event_preview(e) for e in batch], 'total': len(events),
            'next_offset': offset+len(batch),
            'legacy_entries': ([e.model_dump() for e in DecisionLedger(_run_folder(run_id)/'ledger.jsonl').entries]
                               if not events else [])}


@app.get('/runs/{run_id}/collaboration/{event_id}')
async def get_collaboration_event(run_id: str, event_id: int) -> dict:
    await get_run_status(run_id)
    for event in read_trace(_run_folder(run_id)/'trace.ndjson'):
        if event['id'] == event_id:
            return event
    raise HTTPException(status_code=404, detail='Research event not found')


def create_app() -> FastAPI:
    return app
