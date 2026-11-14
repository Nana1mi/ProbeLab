import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from tierlab.gateway import ModelGateway
from tierlab.trace import ResearchTrace, read_trace, trace_stats
from tierlab.types import Budget
import tierlab.server as server


@pytest.mark.asyncio
async def test_trace_distinguishes_small_cloud_billing_and_never_copies_credentials(tmp_path, mock_models_config, monkeypatch):
    mock_models_config.cloud_small_model = mock_models_config.cloud_model.model_copy(update={'name': 'small-api-model'})
    monkeypatch.setenv('SILICONFLOW_API_KEY', 'private-key-must-not-be-recorded')
    gateway = ModelGateway(models_config=mock_models_config, backend='mock', budget=Budget(cloud_budget_cny=1))
    gateway.trace = ResearchTrace(tmp_path/'trace.jsonl')
    await gateway.call('cloud_small', [{'role':'user','content':'Generate a candidate'}], task_class='code')
    await gateway.call('cloud', [{'role':'user','content':'Give advice'}], response_kind='advice')
    events = read_trace(gateway.trace.path)
    assert [e['role'] for e in events if e['kind']=='model_started'] == ['small', 'strong']
    assert trace_stats(events)['small']['calls'] == trace_stats(events)['strong']['calls'] == 1
    assert all(e['output'] for e in events if e['kind']=='model_finished')
    assert 'private-key-must-not-be-recorded' not in gateway.trace.path.read_text()


def test_collaboration_persists_candidates_feedback_and_final_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'RUNS_DIR', tmp_path/'runs')
    monkeypatch.setattr(server, 'RUNS_REGISTRY', {})
    with TestClient(server.app) as client:
        run = client.post('/runs', json={'task':'math','backend':'mock','max_steps':3}).json()['run_id']
        first = client.get(f'/runs/{run}/collaboration?limit=2').json()
        assert first['status'] == 'completed', first
        assert not first['legacy'] and first['next_offset'] == 2 and first['total'] > 2
        assert first['stats']['small']['calls'] == 3
        assert first['stats']['strong']['calls'] == 0
        all_events = client.get(f'/runs/{run}/collaboration?limit=200').json()['events']
        candidate = next(e for e in all_events if e['kind']=='candidate' and e.get('call_id'))
        assert 'code' not in candidate  # Large contents are lazy-loaded, never silently truncated.
        detail = client.get(f'/runs/{run}/collaboration/{candidate["id"]}').json()
        assert 'def solve' in detail['code']
        final = next(e for e in all_events if e['kind']=='verification' and e['stage']=='final')
        assert final['result']['valid']
        persisted = read_trace(server.RUNS_DIR/run/'trace.ndjson')
        model = next(e for e in persisted if e['kind']=='model_started')
        assert model['messages'] and 'score' in json.dumps(model['messages']).lower()
        server.RUNS_REGISTRY.clear()
        assert client.get(f'/runs/{run}/collaboration').json()['total'] == first['total']
        assert client.get(f'/runs/{run}/collaboration/999999').status_code == 404
        state = client.get(f'/runs/{run}').json()
        assert state['summary']['small_model_calls'] == 3
        assert state['summary']['strong_model_calls'] == 0


def test_legacy_trace_is_explicitly_unavailable_and_run_paths_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'RUNS_DIR', tmp_path)
    monkeypatch.setattr(server, 'RUNS_REGISTRY', {})
    folder = tmp_path/'old_run'; folder.mkdir()
    (folder/'status.json').write_text(json.dumps({'run_id':'old_run','status':'completed','backend':'real'}))
    with TestClient(server.app) as client:
        data = client.get('/runs/old_run/collaboration').json()
        assert data['legacy'] and data['stats'] is None and data['events'] == []
        assert client.get('/runs/run%20invalid/collaboration').status_code == 400
        assert client.get('/runs/no_such_run/collaboration').status_code == 404


def test_read_trace_survives_partial_last_line(tmp_path):
    path = tmp_path/'trace.jsonl'
    path.write_text('{"id":1,"kind":"decision"}\n{"id":2,')
    assert read_trace(path) == [{'id':1,'kind':'decision'}]


def test_native_renderer_patch_is_idempotent_and_preserves_upstream_code(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('native_installer',Path(__file__).parents[1]/'scripts/install_deerflow_collaboration.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    renderer = tmp_path/'frontend/src/components/workspace/messages/message-group.tsx'
    renderer.parent.mkdir(parents=True)
    renderer.write_text('import { ToolCallDetails } from "./tool-call-details";\nCUSTOM_UPSTREAM_CODE\n      {processingPanel}\n')
    module.install(tmp_path)
    module.install(tmp_path)
    changed = renderer.read_text()
    assert 'CUSTOM_UPSTREAM_CODE' in changed
    assert changed.count('<ResearchCollaborationRuns messages={messages} />') == 1
    assert (tmp_path/'frontend/src/core/messages/vela-research.ts').is_file()
