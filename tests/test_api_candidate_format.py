import json
from types import SimpleNamespace

import pytest

from tierlab.config import load_models_config
from tierlab.gateway import ModelGateway
from tierlab.tasks import get_task


@pytest.mark.parametrize('task_name', ['math', 'code'])
def test_json_code_extraction(task_name):
    task = get_task(task_name)
    code = task.initial_candidate().content
    assert task.extract_candidate(json.dumps({'code': code})) == code.strip()
    assert task.extract_candidate('```json\n' + json.dumps({'code': code}) + '\n```') == code.strip()


@pytest.mark.asyncio
@pytest.mark.parametrize('task_class,response_kind,wrapped', [
    ('math', 'candidate', True), ('code', 'candidate', True),
    ('paper', 'candidate', False), ('math', 'advice', False),
])
async def test_gateway_wraps_candidates_but_not_hints_or_paper(tmp_path, task_class, response_kind, wrapped):
    gateway = ModelGateway(models_config=load_models_config('configs/models.siliconflow.yaml'),
                           prompt_dump_dir=tmp_path)
    messages = [{'role': 'system', 'content': 'Generate'}, {'role': 'user', 'content': 'contract'}]
    await gateway.call('cloud_small', messages, task_class=task_class, response_kind=response_kind)
    request = json.loads(next(tmp_path.glob('*.json')).read_text(encoding='utf-8'))
    assert ('response_format' in request['extra_body']) == wrapped
    assert (len(request['messages']) == 3) == wrapped
    assert len(messages) == 2  # Caller-owned prompts remain intact.


def test_sdk_retries_do_not_multiply_gateway_retry_policy(monkeypatch):
    import tierlab.gateway as module
    calls = []
    def client(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace()
    monkeypatch.setattr(module, 'AsyncOpenAI', client)
    ModelGateway(models_config=load_models_config('configs/models.siliconflow.yaml'), backend='real')
    assert len(calls) == 3
    assert all(call['max_retries'] == 0 for call in calls)
