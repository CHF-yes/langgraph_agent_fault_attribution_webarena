#!/usr/bin/env python3
import csv, json, subprocess, sys, time
from collections import deque
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from run_baseline import load_tasks, resolve_start_url

CSV=ROOT/'experiments/analysis/rebuilt_official_responses_v1/needs_agent_rerun.csv'
OUT=ROOT/'experiments/rerun_missing_trials_v1'

def main():
  OUT.mkdir(parents=True,exist_ok=True)
  task_ids=sorted({int(r['task_id']) for r in csv.DictReader(CSV.open())})
  tasks={t['task_id']:t for t in load_tasks(task_ids=task_ids)}; jobs=[]
  for r in csv.DictReader(CSV.open()):
    label=r['fault_type']; arch='plan_execute' if label.startswith('plan_execute_') else 'react'
    condition=r['condition']; fault='control'
    if condition=='fault': fault=label.split('_fault_',1)[-1] if '_fault_' in label else label
    t=tasks[int(r['task_id'])]; jobs.append((arch,int(r['task_id']),int(r['seed']),condition,fault,(t.get('sites')or[None])[0],resolve_start_url(t),t['intent'],r['source']))
  (OUT/'manifest.json').write_text(json.dumps({'trials':len(jobs),'workers':6,'source':str(CSV.relative_to(ROOT))},indent=2)+'\n')
  pending=deque(jobs); active={}; done=0
  while pending or active:
    while pending and len(active)<6:
      arch,tid,seed,condition,fault,site,url,intent,source=pending.popleft(); key=f'{arch}_{condition}_{fault}_task{tid}_seed{seed}'; log=(OUT/f'{key}.log').open('w')
      cmd=[sys.executable,'main.py','--benchmark','--site',site,'--url',url,'--task',intent,'--task-id',str(tid),'--model-profile','gpt54','--architecture',arch,'--max-steps','20','--trials','1','--condition',condition,'--fault-seed',str(seed),'--webarena-output-dir',str(OUT/'outputs'/key)]
      if condition=='fault': cmd += ['--fault-type',fault,'--fault-intensity','high','--fault-injection-step','1' if fault=='agent_param_error' else '2']
      p=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True); active[key]=(p,log); print('START',key,flush=True)
    for key,(p,log) in list(active.items()):
      if p.poll() is not None: log.close(); active.pop(key); done+=1; print('DONE',key,'rc=',p.returncode,flush=True)
    if active or pending: time.sleep(.5)
  (OUT/'summary.json').write_text(json.dumps({'completed':done,'scheduled':len(jobs)},indent=2)+'\n'); print(json.dumps({'completed':done,'scheduled':len(jobs)}))
if __name__=='__main__': main()
