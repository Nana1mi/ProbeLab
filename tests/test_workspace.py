import json
import importlib.util
from pathlib import Path
import pytest
from starlette.testclient import TestClient
import tierlab.server as server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'RUNS_DIR', tmp_path/'runs')
    monkeypatch.setattr(server, 'RUNS_REGISTRY', {})
    with TestClient(server.app) as client:
        yield client


@pytest.mark.parametrize('task', ['math', 'code', 'paper'])
def test_workspace_mock_history_and_paged_evidence(client, task):
    assert 'VELA' in client.get('/').text
    assert client.get('/ui/app.js').status_code == 200
    response = client.post('/runs', json={'task':task,'max_steps':2,'backend':'mock'})
    assert response.status_code == 200
    run_id = response.json()['run_id']
    state = client.get(f'/runs/{run_id}').json()
    assert state['status'] == 'completed', state
    assert state['backend'] == 'mock'
    assert state['summary']['stop_reason']
    assert state['ledger_count'] > 1
    first = client.get(f'/runs/{run_id}/ledger?limit=1').json()
    second = client.get(f'/runs/{run_id}/ledger?limit=1&offset={first["next_offset"]}').json()
    assert first['entries'] != second['entries']
    assert len(client.get('/runs').json()['runs']) == 1
    assert client.get(f'/runs/{run_id}/report').status_code == 200
    server.RUNS_REGISTRY.clear()
    assert client.get(f'/runs/{run_id}').json()['summary'] == state['summary']


def test_restart_does_not_claim_incomplete_run_completed(client):
    folder = server.RUNS_DIR/'run_interrupted'
    folder.mkdir(parents=True)
    (folder/'status.json').write_text(json.dumps({'run_id':'run_interrupted','status':'running'}))
    assert client.get('/runs/run_interrupted').json()['status'] == 'interrupted'
    assert client.get('/runs').json()['runs'][0]['status'] == 'interrupted'


def test_invalid_requests_do_not_create_jobs(client):
    for payload in [{'task':'other'}, {'policy':'other'}, {'max_steps':0}, {'cloud_budget_cny':-1}, {'k_init':128}]:
        assert client.post('/runs',json=payload).status_code == 422
    assert not server.RUNS_REGISTRY
    assert client.get('/runs/run%20invalid/ledger').status_code == 400
    assert client.get('/runs/missing/ledger').status_code == 404


def test_installer_preserves_existing_customizations(tmp_path):
    spec = importlib.util.spec_from_file_location('vela_installer',Path(__file__).parents[1]/'scripts/install_deerflow_frontend.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = tmp_path/'frontend/src/app'
    (app/'workspace').mkdir(parents=True)
    (app/'workspace/layout.tsx').touch()
    (tmp_path/'backend').mkdir()
    files = module.install(tmp_path)
    assert len(files) == 6
    module.install(tmp_path)
    page = app/'workspace/vela/page.tsx'
    page.write_text('customized')
    with pytest.raises(FileExistsError): module.install(tmp_path)
    assert page.read_text() == 'customized'
