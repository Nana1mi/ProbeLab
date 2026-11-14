"""Confirm frozen candidates on five previously unseen input seeds."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from tierlab.sandbox import execute_in_sandbox
from tierlab.tasks.code_opt import REFERENCE_CODE

folder=Path(sys.argv[1])
repetitions=int(sys.argv[2]) if len(sys.argv)>2 else 7
names=['all_local','all_cloud','tierlab']
extra={'reference.py':REFERENCE_CODE}
for name in names:
    d=json.loads((folder/f'code_{name}_s0/final_candidate.json').read_text(encoding='utf-8'))
    extra[f'{name}.py']=d['content']
runner='''
import importlib,json,random,statistics
from time import perf_counter as pc
from reference import topk_cosine_sim as reference
names=['all_local','all_cloud','tierlab']
functions={n:importlib.import_module(n).topk_cosine_sim for n in names}
records=[]
for sidx,seed in enumerate([20011,20021,20023,20029,20047]):
    rng=random.Random(seed)
    A=[[rng.uniform(-1,1) for _ in range(64)] for _ in range(800)]
    Q=[[rng.uniform(-1,1) for _ in range(64)] for _ in range(100)]
    expected=reference(A,Q,5)
    reference_times=[]
    for _ in range(3):
        t=pc(); reference(A,Q,5); reference_times.append(pc()-t)
    times={n:[] for n in names}
    correct={n:True for n in names}
    # Warm all implementations before measured repetitions.
    for name in names:
        correct[name] &= functions[name](A,Q,5)==expected
    for rep in range(REPETITIONS):
        shift=(sidx+rep)%3
        for name in names[shift:]+names[:shift]:
            t=pc(); answer=functions[name](A,Q,5); dt=pc()-t
            correct[name] &= answer==expected
            times[name].append(dt)
    for name in names:
        med=statistics.median(times[name])
        records.append({'seed':seed,'policy':name,'correct':correct[name],
            'candidate_ms':1000*med,'candidate_samples_s':times[name],
            'reference_samples_s':reference_times,
            'speedup':statistics.median(reference_times)/med})
summary={n:{'correct':sum(r['correct'] for r in records if r['policy']==n),
            'n':5,'median_candidate_ms':statistics.median(r['candidate_ms'] for r in records if r['policy']==n),
            'median_speedup':statistics.median(r['speedup'] for r in records if r['policy']==n)} for n in names}
print(json.dumps({'records':records,'summary':summary}))
'''
runner=runner.replace('REPETITIONS',str(repetitions))
result=execute_in_sandbox(code='',entry_filename='main.py',runner_script=runner,
    extra_files=extra,timeout_s=60,use_cache=False)
if result.returncode or result.timed_out:
    raise RuntimeError(f'Confirmation failed: {result.stderr}')
data=json.loads(result.stdout)
data['parent_wall_s']=result.wall_clock_s
data['method']=f'Frozen candidates; seeds 20011/20021/20023/20029/20047; {repetitions} rotated warm repetitions per candidate/seed; reference median of 3; all outputs compared exactly.'
filename='confirmation.json' if repetitions==7 else f'confirmation_{repetitions}.json'
if (folder/filename).exists():
    filename=Path(filename).stem+f'_recheck_{time.time_ns()}.json'
(folder/filename).write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
print(json.dumps(data['summary'],indent=2))
print('Evidence:',folder/filename)
