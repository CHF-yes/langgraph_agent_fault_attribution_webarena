#!/usr/bin/env python3
import csv, json, subprocess, sys, time
from collections import deque
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from run_baseline import load_tasks, resolve_start_url
GAPS=ROOT/'experiments/analysis/schema_corrected_official_v1/coverage_gaps.csv'
OUT=ROOT/'experiments/rerun_architecture_gaps_v1'

def main():
  rows=[r for r in csv.DictReader(GAPS.open()) if r['architecture']=='plan_execute']
  tasks={t['task_id']:t for t in load_tasks(task_ids=sorted({int(r['task_id']) for r in rows}))}
  OUT.mkdir(parents=True,exist_ok=True); (OUT/'manifest.json').write_text(json.dumps({'trials':len(rows),'architecture':'plan_execute','source':str(GAPS.relative_to(ROOT))},indent=2)+'\n')
  pending=deque(rows); active={}; done=0
  while pending or active:
   while pending and len(active)<6:
    r=pending.popleft(); tid=int(r['task_id']); seed=int(r['seed']); condition=r['condition']; fault=r['fault_type']; task=tasks[tid]; site=(task.get('sites')or[None])[0]; url=resolve_start_url(task); key=f'plan_execute_{condition}_{fault}_task{tid}_seed{seed}'; log=(OUT/f'{key}.log').open('w')
    cmd=[sys.executable,'main.py','--benchmark','--site',site,'--url',url,'--task',task['intent'],'--task-id',str(tid),'--model-profile','gpt54','--architecture','plan_execute','--max-steps','20','--trials','1','--condition',condition,'--fault-seed',str(seed),'--webarena-output-dir',str(OUT/'outputs'/key)]
    if condition=='fault': cmd += ['--fault-type',fault,'--fault-intensity','high','--fault-injection-step','2']
    p=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True); active[key]=(p,log); print('START',key,flush=True)
   for key,(p,log) in list(active.items()):
    if p.poll() is not None: log.close(); active.pop(key); done+=1; print('DONE',key,'rc=',p.returncode,flush=True)
   if pending or active: time.sleep(.5)
  (OUT/'summary.json').write_text(json.dumps({'completed':done,'scheduled':len(rows)},indent=2)+'\n'); print(json.dumps({'completed':done,'scheduled':len(rows)}))
if __name__=='__main__': main()
