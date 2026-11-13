"""DeerFlow Tool Integration for TierLab.

CRITICAL DESIGN PRINCIPLE:
  This module is an isolated client-side tool. It communicates with TierLab strictly
  via HTTP to `tierlab serve` (FastAPI) and DOES NOT import the `tierlab` core library.
  It requires zero modifications to DeerFlow source code.

NOTE:
  Exact DeerFlow tool registration decorator and schema format are marked with
  TODO(verify: DeerFlow @tool registration format) and documented in REVIEW.md.
"""

from __future__ import annotations

import os
from typing import Any
import httpx

TIERLAB_SERVER_URL = os.environ.get("TIERLAB_SERVER_URL", "http://127.0.0.1:8000")


# TODO(verify: DeerFlow @tool registration format - depending on LangGraph / LangChain tool decorator)
def start_research(
    task: str = "math",
    policy: str = "tierlab",
    backend: str = "mock",
    seed: int = 0,
    cloud_budget_cny: float = 5.0,
    max_wall_clock_s: float = 300.0,
) -> dict[str, Any]:
    """Start an autonomous research task via TierLab server.

    Args:
        task: Research task type ('math', 'code', 'paper').
        policy: Escalation policy ('tierlab', 'all_cloud', 'all_local', 'cascade').
        backend: Execution backend ('mock' or 'real').
        seed: Random seed for deterministic reproducibility.
        cloud_budget_cny: Hard cloud budget cap in CNY.
        max_wall_clock_s: Maximum duration in seconds.

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
    }
    with httpx.Client(base_url=TIERLAB_SERVER_URL, timeout=10.0) as client:
        resp = client.post("/runs", json=payload)
        resp.raise_for_status()
        return resp.json()


# TODO(verify: DeerFlow @tool registration format)
def get_research_status(run_id: str) -> dict[str, Any]:
    """Check the execution status and progress of an ongoing or completed TierLab run.

    Args:
        run_id: Identifier returned by start_research.

    Returns:
        Current execution state, score, and ledger summary.
    """
    with httpx.Client(base_url=TIERLAB_SERVER_URL, timeout=10.0) as client:
        resp = client.get(f"/runs/{run_id}")
        resp.raise_for_status()
        return resp.json()


# TODO(verify: DeerFlow @tool registration format)
def get_research_report(run_id: str) -> str:
    """Retrieve the generated static HTML report dashboard for a run.

    Args:
        run_id: Identifier returned by start_research.

    Returns:
        HTML string containing complete interactive dashboard.
    """
    with httpx.Client(base_url=TIERLAB_SERVER_URL, timeout=10.0) as client:
        resp = client.get(f"/runs/{run_id}/report")
        resp.raise_for_status()
        return resp.text
