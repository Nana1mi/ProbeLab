"""DeerFlow Tool Integration for TierLab.

CRITICAL DESIGN PRINCIPLE:
  This module is an isolated client-side tool. It communicates with TierLab strictly
  via HTTP to `tierlab serve` (FastAPI) and DOES NOT import the `tierlab` core library.
  It requires zero modifications to DeerFlow source code.

LangChain registration is provided separately in registered_tools.py so this
HTTP client remains usable without a LangChain dependency.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any
import httpx

TIERLAB_SERVER_URL = os.environ.get("TIERLAB_SERVER_URL", "http://127.0.0.1:8000")


def start_research(
    task: str = "math",
    policy: str = "tierlab",
    backend: str = "mock",
    seed: int = 0,
    cloud_budget_cny: float = 5.0,
    max_wall_clock_s: float = 300.0,
    max_steps: int = 6,
    weak_backend: str = 'local',
    models_profile: str = 'default',
    local_max_tokens: int = 4096,
    code_max_speedup: float = 1000.0,
) -> dict[str, Any]:
    """Start an autonomous research task via TierLab server.

    Args:
        task: Research task type ('math', 'code', 'paper').
        policy: Escalation policy ('tierlab', 'all_cloud', 'all_local', 'cascade').
        backend: Execution backend ('mock' or 'real').
        seed: Random seed for deterministic reproducibility.
        cloud_budget_cny: Hard cloud budget cap in CNY.
        max_wall_clock_s: Maximum duration in seconds.
        max_steps: Maximum controller iterations.
        weak_backend: local vLLM or cloud_small API.
        models_profile: default or siliconflow endpoint configuration.
        local_max_tokens: Maximum small-model response tokens.
        code_max_speedup: Shared code benchmark timing guard.

    Returns:
        JSON with run_id and initial status.
    """
    payload = {
        "task": task,
        "policy": policy,
        "backend": backend,
        "seed": seed,
        "cloud_budget_cny": cloud_budget_cny,
        "max_wall_clock_s": max_wall_clock_s,
        'max_steps': max_steps,
        'weak_backend': weak_backend,
        'models_profile': models_profile,
        'local_max_tokens': local_max_tokens,
        'code_max_speedup': code_max_speedup,
    }
    with httpx.Client(base_url=TIERLAB_SERVER_URL, timeout=10.0) as client:
        resp = client.post("/runs", json=payload)
        resp.raise_for_status()
        return resp.json()


def get_research_status(run_id: str, wait_seconds: int = 0) -> dict[str, Any]:
    """Check the execution status and progress of an ongoing or completed TierLab run.

    Args:
        run_id: Identifier returned by start_research.
        wait_seconds: Wait up to 120 seconds for completion instead of rapid polling.

    Returns:
        Current execution state, score, and ledger summary.
    """
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', run_id):
        raise ValueError('Invalid run identifier')
    deadline = time.monotonic() + max(0, min(wait_seconds, 120))
    with httpx.Client(base_url=TIERLAB_SERVER_URL, timeout=10.0) as client:
        while True:
            resp = client.get(f"/runs/{run_id}")
            resp.raise_for_status()
            data = resp.json()
            remaining = deadline - time.monotonic()
            if data.get('status') not in ('queued', 'running') or remaining <= 0:
                return data
            time.sleep(min(1.0, remaining))


def get_research_report(run_id: str) -> str:
    """Retrieve the generated static HTML report dashboard for a run.

    Args:
        run_id: Identifier returned by start_research.

    Returns:
        HTML string containing complete interactive dashboard.
    """
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,180}', run_id):
        raise ValueError('Invalid run identifier')
    with httpx.Client(base_url=TIERLAB_SERVER_URL, timeout=30.0) as client:
        resp = client.get(f"/runs/{run_id}/report")
        resp.raise_for_status()
        return resp.text
