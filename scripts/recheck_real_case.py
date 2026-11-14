"""Fresh, interleaved held-out correctness and timing checks, without model calls."""
import json
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / 'src'))
from tierlab.tasks.code_opt import run_reference_sandbox, run_candidate_sandbox, suspicious_timing
from tierlab.sandbox import start_sandbox_run

folder = Path(sys.argv[1])
policies = ['all_local','all_cloud','tierlab']
candidates = {p:json.loads((folder / f'code_{p}_s0/final_candidate.json').read_text(encoding='utf-8'))['content'] for p in policies}
seeds = [9999,10001,10003,10007,10009]
records = []
start_sandbox_run()
for i,seed in enumerate(seeds):
    order = policies[i%3:] + policies[:i%3]
    for policy in order:
        # Rotate both policy order and reference/candidate order.
        if i%2:
            cand = run_candidate_sandbox(candidates[policy], mode='benchmark',seed=seed)
            ref = run_reference_sandbox(mode='benchmark',seed=seed)
        else:
            ref = run_reference_sandbox(mode='benchmark',seed=seed)
            cand = run_candidate_sandbox(candidates[policy], mode='benchmark',seed=seed)
        correct = ref.get('status') == cand.get('status') == 'ok' and ref.get('ans') == cand.get('ans')
        valid = correct and not suspicious_timing(ref,cand,1000.0,2.0)
        row = {'policy':policy,'seed':seed,'correct':correct,'valid':valid,
               'reference_s':ref.get('elapsed_s'),'candidate_s':cand.get('elapsed_s'),
               'reference_parent_s':ref.get('parent_elapsed_s'),
               'candidate_parent_s':cand.get('parent_elapsed_s')}
        row['speedup'] = ref['elapsed_s']/cand['elapsed_s'] if valid else 0
        records.append(row)
        print(json.dumps(row),flush=True)
        (folder/'held_out_recheck.json').write_text(json.dumps(records,indent=2)+'\n',encoding='utf-8')
summary = {p:{'correct':sum(r['correct'] for r in records if r['policy']==p),
              'valid':sum(r['valid'] for r in records if r['policy']==p),
              'n':len(seeds),
              'median_speedup':statistics.median(r['speedup'] for r in records if r['policy']==p),
              'median_candidate_ms':1000*statistics.median(r['candidate_s'] for r in records if r['policy']==p)}
           for p in policies}
(folder/'held_out_summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
print('SUMMARY',json.dumps(summary),flush=True)
