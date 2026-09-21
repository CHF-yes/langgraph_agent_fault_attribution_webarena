#!/usr/bin/env python3
import json, subprocess, sys, time
from collections import deque
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'experiments/deepseek_plan_execute_extra_controls_v1'
TASK_IDS=[27,30,132]
def main():
 from run_baseline import load_tasks,resolve_start_url
 tasks={t['task_id']:t for t in load_tasks(task_ids=TASK_IDS)}; jobs=[]
 for tid in TASK_IDS:
  t=tasks[tid]
  for seed in range(1,6): jobs.append((tid,seed,(t.get('sites')or[None])[0],resolve_start_url(t),t['intent']))
 OUT.mkdir(parents=True,exist_ok=True); (OUT/'manifest.json').write_text(json.dumps({'trials':len(jobs),'model':'deepseek','architecture':'plan_execute','condition':'control','tasks':TASK_IDS,'seeds':[1,2,3,4,5]},indent=2)+'\n')
 q=deque(jobs); active={}; done=0
 while q or active:
  while q and len(active)<6:
   tid,seed,site,url,intent=q.popleft(); key=f'plan_execute_control_extra_task{tid}_seed{seed}'; log=(OUT/f'{key}.log').open('w')
   cmd=[sys.executable,'main.py','--benchmark','--site',site,'--url',url,'--task',intent,'--task-id',str(tid),'--model-profile','deepseek','--architecture','plan_execute','--max-steps','20','--trials','1','--condition','control','--fault-seed',str(seed),'--webarena-output-dir',str(OUT/'outputs'/key)]
   p=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True); active[key]=(p,log); print('START',key,flush=True)
  for key,(p,log) in list(active.items()):
   if p.poll() is not None: log.close();active.pop(key);done+=1;print('DONE',key,'rc=',p.returncode,flush=True)
  if q or active: time.sleep(.5)
 (OUT/'summary.json').write_text(json.dumps({'completed':done,'scheduled':len(jobs)},indent=2)+'\n'); print(json.dumps({'completed':done,'scheduled':len(jobs)}))
if __name__=='__main__':main()
