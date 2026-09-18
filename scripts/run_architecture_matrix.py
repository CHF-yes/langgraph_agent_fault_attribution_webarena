#!/usr/bin/env python3
"""Reproduce the historical 54-trial architecture pilot only.

This task list is intentionally frozen to the published artifact.  For new
runs use scripts/run_fault_matrix.py and docs/experiment_roadmap.md.
"""
import argparse, json, subprocess, sys, time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run_baseline import load_tasks, resolve_start_url

TASK_IDS = [22, 24, 27, 28, 30, 132, 133, 134]
FAULTS = ["web_dom_missing", "web_http_error"]

def main():
    p=argparse.ArgumentParser(); p.add_argument('--output-dir',required=True); p.add_argument('--official-output-root',required=True); p.add_argument('--workers',type=int,default=6); p.add_argument('--max-steps',type=int,default=20); a=p.parse_args()
    out=ROOT/a.output_dir; out.mkdir(parents=True,exist_ok=True); jobs=[]
    tasks = {task["task_id"]: task for task in load_tasks(task_ids=TASK_IDS)}
    for arch in ('react','plan_execute'):
      for task_id in TASK_IDS:
       task = tasks[task_id]
       site = (task.get("sites") or [None])[0]
       intent = task["intent"]
       start_url = resolve_start_url(task)
       for seed in (1,2,3,4,5):
        for condition in ('control','fault'):
         faults=FAULTS if condition=='fault' else [None]
         for fault in faults: jobs.append((arch,task_id,site,start_url,intent,seed,condition,fault))
    (out/'manifest.json').write_text(json.dumps({'trials':len(jobs),'workers':a.workers,'tasks':TASK_IDS,'architectures':['react','plan_execute'],'faults':FAULTS,'seeds':[1,2,3,4,5]},indent=2)+'\n')
    pending=deque(jobs); active={}; done=0
    while pending or active:
      while pending and len(active)<a.workers:
       arch,task,site,start_url,intent,seed,condition,fault=pending.popleft(); key=f'{arch}_{condition}_{fault or "control"}_task{task}_seed{seed}'; log=(out/f'{key}.log').open('w')
       cmd=[sys.executable,'main.py','--benchmark','--site',site,'--url',start_url,'--task',intent,'--task-id',str(task),'--model-profile','gpt54','--architecture',arch,'--max-steps',str(a.max_steps),'--trials','1','--condition',condition,'--fault-seed',str(seed)]
       if fault: cmd += ['--fault-type',fault,'--fault-intensity','high','--fault-injection-step','2']
       cmd += ['--webarena-output-dir',str(ROOT/a.official_output_root/key)]
       proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True); active[key]=(proc,log); print('START',key,'pid=',proc.pid,flush=True)
      for key,(proc,log) in list(active.items()):
       if proc.poll() is not None: log.close(); active.pop(key); done+=1; print('DONE',key,'rc=',proc.returncode,flush=True)
      if pending or active: time.sleep(.5)
    (out/'summary.json').write_text(json.dumps({'completed':done,'scheduled':len(jobs),'stopped':False},indent=2)+'\n'); print(json.dumps({'completed':done,'scheduled':len(jobs)}))
if __name__=='__main__': main()
