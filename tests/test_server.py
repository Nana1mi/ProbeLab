"""Tests for FastAPI server endpoints (/runs, /runs/{id}, /runs/{id}/report)."""

import pytest
from starlette.testclient import TestClient
from tierlab.server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_server_runs_lifecycle(client: TestClient):
    # 1. Trigger run
    payload = {
        "task": "math",
        "policy": "tierlab",
        "backend": "mock",
        "seed": 0,
        "max_steps": 2,
    }
    resp = client.post("/runs", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    run_id = data["run_id"]
    assert data["status"] == "queued"

    # 2. Check run status
    resp2 = client.get(f"/runs/{run_id}")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["run_id"] == run_id
    assert data2["status"] in ("queued", "running", "completed")


def test_server_unknown_run_404(client: TestClient):
    resp = client.get("/runs/non_existent_run_id")
    assert resp.status_code == 404
