"""Smoke collection must retain partial failures and report actual tail latency."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import smoke_vllm as smoke
from tierlab.tasks.math_packing import MathPackingTask


@pytest.mark.asyncio
async def test_smoke_keeps_api_failures_and_verifier_results(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, 'OUTPUTS', tmp_path)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content='<think></think>\n' + MathPackingTask().initial_candidate().content,
            reasoning_content='Unexpected reasoning channel'), finish_reason='length')],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=2048),
    )
    create = AsyncMock(side_effect=[response, TimeoutError('request timed out')])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    summary = await smoke.format_check(client, 'local-vllm-model', 'math', 2)
    assert summary['valid'] == 1
    assert summary['think_leak'] == summary['truncated'] == summary['api_errors'] == 1
    records = json.loads((tmp_path / 'math.json').read_text(encoding='utf-8'))
    assert len(records) == 2 and records[0]['verification'][-1]['valid']
    assert records[1]['error_type'] == 'TimeoutError'
    assert (tmp_path / 'math_summary.json').is_file()
    assert create.call_args.kwargs['extra_body']['chat_template_kwargs']['enable_thinking'] is False


@pytest.mark.asyncio
async def test_smoke_throughput_saves_k_and_latency(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, 'OUTPUTS', tmp_path)
    result = dict(content='tutorial', finish='length', out_tokens=2048,
                  in_tokens=10, latency_s=31.0)
    monkeypatch.setattr(smoke, 'one_call', AsyncMock(return_value=result))
    measurements = await smoke.throughput(None, 'model')
    saved = json.loads((tmp_path / 'throughput.json').read_text(encoding='utf-8'))
    assert [row['k'] for row in saved] == [1, 2, 4]
    assert measurements[-1]['over_30s'] == 4
    assert measurements[-1]['p95_s'] == measurements[-1]['max_s'] == 31
    assert len(measurements[-1]['responses']) == 4


def test_smoke_p95_uses_nearest_rank():
    stats = smoke.latency_stats([{'latency_s': t} for t in [1, 2, 3, 40]])
    assert stats == {'median_s': 2.5, 'p95_s': 40, 'max_s': 40, 'over_30s': 1}


@pytest.mark.asyncio
async def test_siliconflow_uses_provider_parameters_and_cloud_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, 'OUTPUTS', tmp_path)
    result = dict(content='tutorial', finish='stop', out_tokens=80, in_tokens=10, latency_s=1.0)
    call = AsyncMock(return_value=result)
    monkeypatch.setattr(smoke, 'one_call', call)
    rows = await smoke.throughput(None, 'Qwen/Qwen3.6-35B-A3B', provider='siliconflow')
    assert all(r['provider'] == 'siliconflow' and not r['fixed_output_length'] for r in rows)
    assert all(c.args[-1] == {'enable_thinking': False} for c in call.call_args_list)
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_api_smoke_honors_concurrency_and_output_override(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(smoke, 'OUTPUTS', tmp_path)
    active = 0
    peak = 0
    async def fake_call(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.01)
        active -= 1
        assert args[3] == 4096 and args[-1] == {'enable_thinking': False}
        return dict(content='', finish='stop', in_tokens=10, out_tokens=0, latency_s=.01)
    monkeypatch.setattr(smoke, 'one_call', fake_call)
    summary = await smoke.format_check(None, 'model', 'math', 4, provider='siliconflow',
                                       max_tokens=4096, concurrency=2)
    assert peak == 2
    assert summary['concurrency'] == 2 and summary['max_tokens'] == 4096
    request = json.loads((tmp_path / 'math_request.json').read_text(encoding='utf-8'))
    assert request['max_tokens'] == 4096 and request['concurrency'] == 2


@pytest.mark.asyncio
async def test_json_code_smoke_records_actual_request_and_unwraps_code(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, 'OUTPUTS', tmp_path)
    code = MathPackingTask().initial_candidate().content
    async def call(*args):
        assert args[-1]['response_format'] == {'type': 'json_object'}
        assert 'one string field "code"' in args[2][-1]['content']
        return dict(content=json.dumps({'code': code}), finish='stop', in_tokens=20,
                    out_tokens=100, latency_s=1)
    monkeypatch.setattr(smoke, 'one_call', call)
    result = await smoke.format_check(None, 'model', 'math', 1, provider='siliconflow', json_code=True)
    assert result['valid'] == 1 and result['candidate_format'] == 'json_code'
