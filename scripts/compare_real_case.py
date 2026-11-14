"""Small, fully recorded real-model comparison; no success is assumed."""
import argparse
import asyncio
import getpass
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from tierlab.config import load_models_config
from tierlab.controller import ResearchController
from tierlab.gateway import ModelGateway
from tierlab.ledger import DecisionLedger
from tierlab.prompts import RUNG_MAX_TOKENS
from tierlab.tasks import get_task
from tierlab.types import Budget, ConsultationRung, ControllerConfig


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


class RecordingGateway(ModelGateway):
    async def call(self, *args, **kwargs):
        rec = await super().call(*args, **kwargs)
        n = len(self.call_history)
        save(self.prompt_dump_dir.parent / f'response_{n:03d}.json', rec.model_dump())
        print('CALL', rec.target, rec.completion_tokens, round(rec.wall_clock_s, 1), flush=True)
        return rec


async def run_one(task_name, policy, seed, out):
    folder = out / f'{task_name}_{policy}_s{seed}'
    folder.mkdir(parents=True, exist_ok=True)
    budget = Budget(cloud_budget_cny=1.0, max_steps=6, max_wall_clock_s=600)
    config = ControllerConfig(backend='real', weak_backend='cloud_small', k_schedule=[1],
        k_init=1, no_widen=True, local_patience=1, stagnation_patience=4,
        max_format_failures=0, local_max_tokens=4096, hint_max_tokens=400,
        enable_lessons=False)
    models = load_models_config(ROOT / 'configs/models.siliconflow.yaml')
    # Equal complete-candidate output caps, with no hidden SDK/gateway retries.
    for endpoint in (models.cloud_model, models.cloud_small_model):
        endpoint.max_tokens = 4096
        endpoint.max_retries = 0
    task_options = {'max_speedup': 1000.0} if task_name == 'code' else {}
    task = get_task(task_name, budget=budget, **task_options)
    gateway = RecordingGateway(models_config=models, budget=budget, backend='real',
        weak_target='cloud_small', prompt_dump_dir=folder / 'prompts')
    controller = ResearchController(task=task, gateway=gateway,
        ledger=DecisionLedger(folder / 'ledger.jsonl'), config=config,
        budget=budget, policy=policy, run_id=folder.name, seed=seed)
    # Record every verifier invocation without changing verification or selection.
    counter = [0]
    verifiers = list(task.ladder) + ([task.final_verifier] if task.final_verifier else [])
    for verifier in verifiers:
        original = verifier.verify
        def wrapped(candidate, context=None, original=original, name=verifier.name):
            result = original(candidate, context)
            counter[0] += 1
            save(folder / f'verify_{counter[0]:03d}.json', {
                'verifier': name, 'candidate': candidate.model_dump(),
                'result': result.model_dump()})
            return result
        verifier.verify = wrapped
    save(folder / 'settings.json', {'task':task_name, 'policy':policy, 'seed':seed,
        'controller':config.model_dump(), 'budget':budget.model_dump(),
        'task_options':task_options, 'solve_max_tokens':4096,
        'seed_note':'Controller seed is matched; provider sampling is not deterministic.'})
    print('START', folder.name, flush=True)
    try:
        summary = await controller.run()
        summary['weak_model_calls'] = sum(c.target == 'cloud_small' for c in gateway.call_history)
        summary['strong_model_calls'] = sum(c.target == 'cloud' for c in gateway.call_history)
        summary['gateway_cost_cny'] = gateway.spent_cloud_cny
        save(folder / 'summary.json', summary)
        save(folder / 'final_candidate.json', controller.current_best_candidate.model_dump())
        save(folder / 'calls.json', [c.model_dump() for c in gateway.call_history])
        print('DONE', json.dumps(summary, ensure_ascii=False), flush=True)
        return summary
    except Exception as error:
        result = {'run':folder.name,'task':task_name,'policy':policy,
                  'error_type':type(error).__name__, 'completed':False,
                  'recorded_calls':len(gateway.call_history),
                  'gateway_cost_cny':gateway.spent_cloud_cny}
        save(folder / 'error.json', result)
        print('ERROR', json.dumps(result), flush=True)
        return result
    finally:
        for client in (gateway._cloud_client, gateway._local_client, gateway._cloud_small_client):
            if client:
                await client.close()


async def main(args):
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    # This manifest is written before any model call; retain every run, including failures.
    save(out / 'manifest.json', {'tasks':args.tasks, 'policies':['all_local','all_cloud','tierlab'],
        'seed':args.seed, 'created_unix':time.time(), 'max_steps':6, 'stagnation_patience':4,
        'candidate_max_tokens':4096, 'hint_max_tokens':400, 'k':1,
        'code_max_speedup':1000, 'selection':'All runs retained; exploratory single-case comparison.',
        'success':'Higher quality than both baselines, or higher quality than small-only and '
                  '>=95% big-only quality at lower cost. Code differences must exceed 10% '
                  'and survive fresh held-out retiming; math differences must exceed 0.001.'})
    RUNG_MAX_TOKENS[ConsultationRung.SOLVE] = 4096
    results = []
    for task in args.tasks:
        for policy in ('all_local','all_cloud','tierlab'):
            results.append(await run_one(task,policy,args.seed,out))
            save(out / 'results.json',results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks',nargs='+',default=['code'])
    parser.add_argument('--seed',type=int,default=0)
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    os.chdir(ROOT)
    if not os.environ.get('SILICONFLOW_API_KEY'):
        os.environ['SILICONFLOW_API_KEY'] = getpass.getpass('SiliconFlow key: ')
    asyncio.run(main(args))
