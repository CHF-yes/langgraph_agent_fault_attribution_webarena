#!/usr/bin/env python3
"""Merge DeepSeek ReAct and Plan-and-Execute official trial records."""
import csv, json, re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'experiments/analysis/deepseek_architecture_final_v1'

def add(records, root, arch, source):
  for e in root.rglob('official_eval.json'):
    parts=e.relative_to(root).parts
    outer=next((p for p in parts if '_task' in p and '_seed' in p),None)
    if not outer: continue
    m=re.search(r'(?:plan_execute|react)_(control|fault)_(?:extra_)?(control|web_dom_missing|web_http_error)_task(\d+)_seed(\d+)',outer)
    extra_control=re.search(r'plan_execute_control_extra_task(\d+)_seed(\d+)',outer)
    if not m and extra_control:
      condition, fault, task, seed = 'control', 'control', extra_control.group(1), extra_control.group(2)
      result=json.loads(e.read_text()); records.append({'model':'deepseek','architecture':arch,'condition':condition,'fault_type':fault,'task_id':int(task),'seed':int(seed),'official_success':result.get('official_success'),'evaluator_status':result.get('status'),'official_eval':str(e.relative_to(ROOT)),'source':source,'control_replicate':2}); continue
    if not m: continue
    if not m: continue
    condition,fault,task,seed=m.groups(); records.append({'model':'deepseek','architecture':arch,'condition':condition,'fault_type':'control' if condition=='control' else fault,'task_id':int(task),'seed':int(seed),'official_success':json.loads(e.read_text()).get('official_success'),'evaluator_status':json.loads(e.read_text()).get('status'),'official_eval':str(e.relative_to(ROOT)),'source':source,'control_replicate':2 if source=='extra_control' else 1})

def main():
  records=[]
  add(records,ROOT/'experiments/formal_deepseek_v4_pro_react_core45_v1/outputs','react','formal_deepseek_v4_pro_react_core45_v1')
  add(records,ROOT/'experiments/analysis/deepseek_plan_execute_audit_v1','plan_execute','rebuilt_plan_execute')
  add(records,ROOT/'experiments/deepseek_plan_execute_completion_v1/outputs','plan_execute','rerun_plan_execute')
  add(records,ROOT/'experiments/deepseek_plan_execute_extra_controls_v1/outputs','plan_execute','extra_control')
  groups={}; duplicates=[]
  for r in records:
    key=(r['model'],r['architecture'],r['condition'],r['fault_type'],r['task_id'],r['seed'],r['control_replicate'])
    if key in groups: duplicates.append({'key':key,'kept':groups[key]['source'],'duplicate':r['source']})
    else: groups[key]=r
  rows=[groups[k] for k in sorted(groups)]
  OUT.mkdir(parents=True,exist_ok=True)
  with (OUT/'deepseek_architecture_trials.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
  summary=[]
  for arch in ('react','plan_execute'):
    for fault in ('control','web_dom_missing','web_http_error'):
      for condition in ('control','fault'):
        g=[r for r in rows if r['architecture']==arch and r['fault_type']==fault and r['condition']==condition]
        if g: summary.append({'model':'deepseek','architecture':arch,'fault_type':fault,'condition':condition,'trials':len(g),'official_successes':sum(bool(r['official_success']) for r in g),'official_success_rate':sum(bool(r['official_success']) for r in g)/len(g),'evaluator_errors':sum(r['evaluator_status']=='ERROR' for r in g)})
  with (OUT/'deepseek_architecture_summary.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
  (OUT/'duplicates.csv').write_text(json.dumps(duplicates,indent=2)+'\n')
  (OUT/'manifest.json').write_text(json.dumps({'model':'deepseek','unique_trials':len(rows),'raw_records':len(records),'duplicates':len(duplicates),'architectures':['react','plan_execute'],'tasks':[27,30,132],'seeds':[1,2,3,4,5],'faults':['control','web_dom_missing','web_http_error'],'evaluator_errors':sum(r['evaluator_status']=='ERROR' for r in rows)},indent=2)+'\n')
  print(json.dumps({'raw':len(records),'unique':len(rows),'duplicates':len(duplicates)}))
if __name__=='__main__':main()
