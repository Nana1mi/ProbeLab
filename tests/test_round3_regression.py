"""Adversarial probes and request capture for the third review round."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tierlab.controller import ResearchController
from tierlab.exceptions import ConfigurationError
from tierlab.gateway import ModelGateway
from tierlab.ledger import DecisionLedger
from tierlab.mock_llm import MockLLMBackend, MockResponse
from tierlab.prompts import build_local_prompt
from tierlab.sandbox import clear_sandbox_cache, execute_in_sandbox, get_sandbox_cache
from tierlab.tasks.code_opt import (
    CAND_RUNNER_SCRIPT, REFERENCE_CODE, CodeFinalEvaluationVerifier,
    CodeL2Verifier, CodeOptimizationTask, run_candidate_sandbox, run_reference_sandbox,
)
from tierlab.tasks.math_packing import MathPackingTask
from tierlab.tasks.paper_qa import PaperQATask
from tierlab.types import Budget, Candidate, ConsultationRung, ControllerConfig


class CapturingBackend(MockLLMBackend):
    def __init__(self, empty=False):
        super().__init__()
        self.requests = []
        self.empty = empty

    def generate(self, **kwargs):
        self.requests.append(kwargs)
        if kwargs['task_class'] == 'math':
            return MockResponse(content='' if self.empty else self._get_overlapping_math_code())
        return super().generate(**kwargs)


def controller_at(tmp_path, backend=None, policy='tierlab', **config):
    budget = Budget(max_steps=4)
    gateway = ModelGateway(budget=budget, mock_backend=backend or CapturingBackend())
    return ResearchController(
        task=MathPackingTask(), gateway=gateway,
        ledger=DecisionLedger(tmp_path / 'ledger.jsonl'), budget=budget,
        config=ControllerConfig(k_schedule=[1], no_widen=True, **config), policy=policy,
    )


def text(request):
    return '\n'.join(m['content'] for m in request['messages'])


def test_r3_01_paper_context_has_no_grading_fields(tmp_path):
    papers = tmp_path / 'data' / 'papers'
    papers.mkdir(parents=True)
    (papers / 'source.txt').write_text('Public source with no hidden sentinel.', encoding='utf-8')
    (papers / 'questions.json').write_text(json.dumps([{
        'qid': 'Q1', 'type': 'factual', 'question': 'What is stated?',
        'target_answer': 'SECRET_GRADING_SENTINEL', 'source_file': 'source.txt',
        'gold_paragraph': 0, 'split': 'dev',
    }, {'qid': 'Q2', 'type': 'factual', 'question': 'Another question?',
        'target_answer': 'SECRET_GRADING_SENTINEL', 'source_file': 'source.txt',
        'gold_paragraph': 0, 'split': 'held_out'}]), encoding='utf-8')
    task = PaperQATask(workspace=tmp_path)
    for rung in (None, 'hint', 'solve'):
        ctx = task.context_for_model(task.initial_candidate(), rung=rung)
        for secret in ('target_answer', 'gold_paragraph', 'SECRET_GRADING_SENTINEL', 'source_file'):
            assert secret not in ctx


def test_r3_02_local_keeps_state_with_full_context():
    prompt = build_local_prompt('task', 'CURRENT_BEST_SCORE: .2\nmath/circle_overlap', full_context='code')
    assert 'math/circle_overlap' in json.dumps(prompt)


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['tierlab', 'all_local', 'all_cloud', 'cascade', 'static_split', 'no_advice_retry'])
async def test_r3_02_failure_reaches_next_request(tmp_path, policy):
    backend = CapturingBackend()
    controller = controller_at(tmp_path, backend, policy=policy, local_patience=1)
    await controller.run()
    generation = [r for r in backend.requests if 'research planner' not in text(r)]
    assert len(generation) >= 2
    assert 'math/circle_overlap' in text(generation[1])
    assert 'CURRENT_BEST_SCORE' in text(generation[1])
    assert len({text(r) for r in backend.requests}) == len(backend.requests)


@pytest.mark.asyncio
async def test_r3_02_hint_uses_failure_from_current_batch(tmp_path):
    backend = CapturingBackend()
    controller = controller_at(tmp_path, backend, local_patience=1)
    await controller.run()
    hints = [r for r in backend.requests if r['max_tokens'] == 400]
    assert hints and 'math/circle_overlap' in text(hints[0])
    assert 'DEV_FEEDBACK: none' not in text(hints[0])


@pytest.mark.asyncio
async def test_r3_03_local_token_cap_and_empty_response(tmp_path):
    backend = CapturingBackend(empty=True)
    controller = controller_at(tmp_path, backend, local_max_tokens=321, local_patience=1)
    await controller.run()
    assert backend.requests[0]['max_tokens'] == 321
    assert 'model/empty_response' in text(backend.requests[1])
    assert all('format_local_retry' not in e.trigger for e in controller.ledger.entries)
    assert any(e.extra.get('failure_signature') == 'model/empty_response' for e in controller.ledger.entries)


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['local', 'cloud', 'cloud_small'])
async def test_r3_03_extra_body_sent_without_guided_decoding(mock_models_config, target):
    endpoint = mock_models_config.local_model if target == 'local' else mock_models_config.cloud_model
    endpoint.extra_body = {'enable_thinking': False}
    endpoint.supports_guided_decoding = False
    gateway = ModelGateway(models_config=mock_models_config)
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='ok'))], usage=None)
    create = AsyncMock(return_value=response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    gateway._local_client = gateway._cloud_client = gateway._cloud_small_client = client
    await gateway._call_real(target, endpoint, [{'role': 'user', 'content': 'hi'}], 17, .7, {'custom': 1})
    assert create.call_args.kwargs['extra_body'] == {'enable_thinking': False, 'custom': 1}
    assert create.call_args.kwargs['max_tokens'] == 17


def test_r3_03_real_rejects_unconfigured_pricing():
    with pytest.raises(ConfigurationError, match='pric'):
        ModelGateway(backend='real')


@pytest.mark.parametrize('verifier', [CodeL2Verifier, CodeFinalEvaluationVerifier])
def test_r3_04_clock_monkeypatch_rejected(verifier):
    malicious = 'import time\ntime.perf_counter = lambda: 0.0\n' + REFERENCE_CODE
    result = verifier().verify(Candidate(id='clock_attack', content=malicious))
    assert not result.valid
    assert result.failure_signature == 'code/suspicious_timing'


def test_r3_04_runner_pins_clock_before_import():
    assert CAND_RUNNER_SCRIPT.index('from time import perf_counter as _pc') < CAND_RUNNER_SCRIPT.index('import solution')
    result = run_candidate_sandbox(REFERENCE_CODE, mode='visible')
    assert result['parent_elapsed_s'] >= result['elapsed_s'] > 0


def test_r3_04_speedup_limit_applies_to_dev_and_final(monkeypatch):
    import tierlab.tasks.code_opt as module
    monkeypatch.setattr(module.CodeL1Verifier, 'verify', lambda *a, **k: module.VerificationResult(valid=True))
    monkeypatch.setattr(module, 'run_reference_sandbox', lambda **k: {'status': 'ok', 'ans': [[0]], 'elapsed_s': 1.0, 'parent_elapsed_s': 1.1})
    monkeypatch.setattr(module, 'run_candidate_sandbox', lambda *a, **k: {'status': 'ok', 'ans': [[0]], 'elapsed_s': .001, 'parent_elapsed_s': .1})
    for verifier in (CodeL2Verifier(), CodeFinalEvaluationVerifier()):
        result = verifier.verify(Candidate(id='c', content='pass'))
        assert not result.valid and result.failure_signature == 'code/suspicious_timing'


def test_r3_04_parent_clock_disagreement_rejected(monkeypatch):
    import tierlab.tasks.code_opt as module
    monkeypatch.setattr(module.CodeL1Verifier, 'verify', lambda *a, **k: module.VerificationResult(valid=True))
    monkeypatch.setattr(module, 'run_reference_sandbox', lambda **k: {'status': 'ok', 'ans': [[0]], 'elapsed_s': 1.0, 'parent_elapsed_s': 1.1})
    monkeypatch.setattr(module, 'run_candidate_sandbox', lambda *a, **k: {'status': 'ok', 'ans': [[0]], 'elapsed_s': 1.0, 'parent_elapsed_s': 30.0})
    for verifier in (CodeL2Verifier(), CodeFinalEvaluationVerifier()):
        result = verifier.verify(Candidate(id='c', content='pass'))
        assert not result.valid and result.failure_signature == 'code/suspicious_timing'


def test_r3_05_irrelevant_quote_and_ungrounded_answer_rejected():
    task = PaperQATask()
    answers = json.loads(MockLLMBackend()._get_perfect_paper_json())
    for answer in answers['answers']:
        answer['quote'] = 'This synthetic document'
    result = task.run_ladder(Candidate(id='unrelated', content=json.dumps(answers)))[-1]
    assert not result.valid and result.failure_signature == 'paper/wrong_paragraph'
    answers = json.loads(MockLLMBackend()._get_perfect_paper_json())
    answers['answers'][0]['answer'] = 'invented result'
    result = task.run_ladder(Candidate(id='ungrounded', content=json.dumps(answers)))[-1]
    assert not result.valid and result.failure_signature == 'paper/answer_not_in_quote'


def test_r3_05_explicit_dev_and_held_out_split():
    task = PaperQATask()
    questions = task._load_questions()
    assert {q['split'] for q in questions} == {'dev', 'held_out'}
    assert all(isinstance(q['gold_paragraph'], int) for q in questions)
    answer = json.loads(MockLLMBackend()._get_perfect_paper_json())
    answer['answers'] = [a for a in answer['answers'] if a['qid'] != 'Q3']
    candidate = Candidate(id='dev_only', content=json.dumps(answer))
    assert task.run_ladder(candidate)[-1].score == 1.0
    assert task.run_final_evaluation(candidate).score == pytest.approx(2 / 3, abs=1e-4)
    assert 'Q3' in task.context_for_model(candidate)


def test_r3_06_cache_key_includes_extra_files():
    clear_sandbox_cache()
    code = "from pathlib import Path\nprint(Path('value.txt').read_text())"
    a = execute_in_sandbox(code, extra_files={'value.txt': 'A'})
    b = execute_in_sandbox(code, extra_files={'value.txt': 'B'})
    assert a.stdout.strip() == 'A' and b.stdout.strip() == 'B'


def test_r3_06_benchmarks_are_never_cached():
    clear_sandbox_cache()
    run_reference_sandbox(mode='benchmark')
    run_candidate_sandbox(REFERENCE_CODE, mode='benchmark')
    assert not get_sandbox_cache()


@pytest.mark.asyncio
async def test_r3_06_new_run_drops_previous_execution_cache(tmp_path):
    execute_in_sandbox("print('from previous run')")
    assert get_sandbox_cache()
    controller_at(tmp_path)
    assert all('from previous run' not in r.stdout for r in get_sandbox_cache().values())


@pytest.mark.asyncio
async def test_r3_06_concurrent_runs_have_independent_caches():
    import asyncio
    from tierlab.sandbox import start_sandbox_run
    async def run(label):
        start_sandbox_run()
        execute_in_sandbox(f'print({label!r})')
        await asyncio.sleep(0)
        return [r.stdout.strip() for r in get_sandbox_cache().values()]
    assert await asyncio.gather(run('A'), run('B')) == [['A'], ['B']]


def test_r3_07_numpy_permission_is_configurable():
    assert 'numpy' in CodeOptimizationTask().context_for_model()
    assert 'numpy' not in CodeOptimizationTask(allowed_libraries=['math', 'heapq']).context_for_model()


def test_r3_07_numpy_candidate_runs_correctly():
    from tierlab.tasks.code_opt import CodeL1Verifier
    code = '''import numpy as np
def topk_cosine_sim(A, Q, k):
    a, q = np.asarray(A), np.asarray(Q)
    scores = (q @ a.T) / (np.linalg.norm(q, axis=1)[:, None] * np.linalg.norm(a, axis=1)[None, :] + 1e-12)
    return np.argsort(-scores, axis=1)[:, :k].tolist()
'''
    assert CodeL1Verifier().verify(Candidate(id='numpy', content=code)).valid


def test_r3_08_mock_usage_scales_with_prompt():
    backend = MockLLMBackend()
    short = backend.generate(target='cloud', messages=[{'role': 'user', 'content': 'solve'}])
    long = backend.generate(target='cloud', messages=[{'role': 'user', 'content': 'solve ' * 1000}])
    assert long.prompt_tokens > short.prompt_tokens * 10
    assert 0 <= short.cached_tokens <= short.prompt_tokens


def test_r3_prompt_dump_is_the_actual_dispatch(tmp_path):
    import asyncio
    dump = tmp_path / 'prompts'
    gateway = ModelGateway(prompt_dump_dir=dump)
    messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'exact bytes'}]
    asyncio.run(gateway.call('local', messages, max_tokens=11, task_class='math'))
    records = [json.loads(p.read_text(encoding='utf-8')) for p in dump.glob('*.json')]
    assert len(records) == 1 and records[0]['messages'] == messages
    assert records[0]['max_tokens'] == 11


def test_r3_cli_config_and_prompt_dump(tmp_path):
    from tierlab.cli import main
    config = tmp_path / 'config.yaml'
    config.write_text('''backend: mock
budget: {max_steps: 12}
controller:
  k_schedule: [1]
  local_patience: 1
  stagnation_patience: 1
  local_max_tokens: 123
  hint_max_tokens: 456
  max_feedback_chars: 512
''', encoding='utf-8')
    main(['run', '--task', 'math', '--config', str(config), '--dump-prompts', '--output-dir', str(tmp_path / 'out')])
    dumps = [json.loads(p.read_text(encoding='utf-8')) for p in (tmp_path / 'out').rglob('*.json') if '_math_' in p.name]
    assert any(d['kind'] == 'local' and d['max_tokens'] == 123 for d in dumps)
    assert any(d['kind'] == 'hint' and d['max_tokens'] == 456 for d in dumps)
    ledger_path = next((tmp_path / 'out').rglob('ledger.jsonl'))
    summary = DecisionLedger(ledger_path).summary()
    assert summary['stop_reason'] == 'consecutive_stagnation_stopped'


@pytest.mark.asyncio
async def test_r3_summary_counts_calls_and_stop_reason(tmp_path):
    controller = controller_at(tmp_path, policy='all_cloud')
    summary = await controller.run()
    assert summary['cloud_calls'] == len(controller.gateway.call_history)
    assert summary['local_calls'] == 0
    assert summary['stop_reason'] == 'consecutive_stagnation_stopped'


@pytest.mark.asyncio
async def test_r3_advisor_action_accounts_for_weak_cloud_batch(tmp_path):
    budget = Budget(max_steps=5)
    gateway = ModelGateway(budget=budget, weak_target='cloud_small')
    controller = ResearchController(
        task=MathPackingTask(), gateway=gateway, budget=budget,
        ledger=DecisionLedger(tmp_path / 'ledger.jsonl'),
        config=ControllerConfig(weak_backend='cloud_small', local_patience=1, no_widen=True, k_schedule=[1]),
    )
    summary = await controller.run()
    assert summary['cloud_calls'] == len(gateway.call_history)
    assert summary['total_cloud_cny'] == pytest.approx(gateway.spent_cloud_cny, abs=1e-6)
    assert summary['total_gpu_s'] == 0


def test_r3_exported_samples_have_state_and_no_grading_fields():
    samples = Path(__file__).resolve().parents[1] / 'docs' / 'prompt_samples'
    for task in ('math', 'code', 'paper'):
        for kind in ('local', 'hint', 'solve'):
            request = json.loads((samples / f'{task}_{kind}.json').read_text(encoding='utf-8'))
            content = text(request)
            assert request['kind'] == kind and request['task'] == task
            assert all(k in content for k in ('CURRENT_BEST_SCORE', 'LAST_FAILURE_SIGNATURE', 'DEV_FEEDBACK'))
            assert all(k not in content for k in ('target_answer', 'gold_paragraph', 'source_file'))
            assert request['max_tokens'] == (400 if kind == 'hint' else 2048)
