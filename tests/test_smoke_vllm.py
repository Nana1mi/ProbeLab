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
