"""Export actual --dump-prompts requests, then compare all policies at seed 0.

Run from the repository root: python docs/prompt_samples/generate.py
"""
import json
import shutil
from pathlib import Path

from tierlab.cli import main, run_experiment
from tierlab.types import PolicyType


def generate():
    destination = Path(__file__).resolve().parent
    config = destination / 'config.yaml'
    output = Path('runs') / 'round3_review'
    rows = []
    for task in ('math', 'code', 'paper'):
        # These are actual CLI dispatches, not calls to template builders.
        main(['run', '--task', task, '--policy', 'tierlab', '--backend', 'mock',
              '--seed', '0', '--config', str(config), '--dump-prompts',
              '--output-dir', str(output)])
        folders = sorted(output.glob(f'{task}_tierlab_s0_*'), key=lambda p: p.stat().st_mtime_ns)
        folder = folders[-1]
        requests = [(p, json.loads(p.read_text(encoding='utf-8'))) for p in sorted((folder / 'prompts').glob('*.json'))]
        for kind in ('local', 'hint', 'solve'):
            matching = [(p, d) for p, d in requests if d['kind'] == kind]
            if not matching:
                raise RuntimeError(f'No actual {kind} request dispatched for {task}')
            # Prefer a local attempt after feedback exists; retain request bytes intact.
            source, _ = matching[-1] if kind == 'local' else matching[0]
            shutil.copyfile(source, destination / f'{task}_{kind}.json')
        tierlab = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
        rows.append({'task': task, **tierlab})
        for policy in PolicyType:
            if policy == PolicyType.TIERLAB:
                continue
            result = run_experiment(task, policy.value, backend='mock', seed=0,
                                    config_file=config, output_dir=output)
            rows.append({'task': task, **result})
    (destination / 'strategy_comparison.json').write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for row in rows:
        print(f"{row['task']:5} {row['policy']:16} cloud={row['cloud_calls']:2} "
              f"local={row['local_calls']:2} stop={row['stop_reason']}")


if __name__ == '__main__':
    generate()
