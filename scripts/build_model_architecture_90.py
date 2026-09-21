#!/usr/bin/env python3
"""Build balanced 90-trial GPT-5.4 and DeepSeek comparison datasets."""
import csv, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'experiments/analysis/model_architecture_90_v1'
TASKS={27,30,132}; FAULTS={'control','web_dom_missing','web_http_error'}

def read_csv(path): return list(csv.DictReader(open(path)))

def main():
  gpt=[]
  for r in read_csv(ROOT/'experiments/analysis/schema_corrected_official_v1/official_trials.csv'):
    if r.get('architecture') in ('react','plan_execute') and int(r['task_id']) in TASKS and r.get('fault_type') in FAULTS and int(r['seed'])<=5:
      r['model']='gpt54'; gpt.append(r)
  ds=[]
  for r in read_csv(ROOT/'experiments/analysis/model_gpt54_vs_deepseek_react_core60_v1/model_trial_results.csv'):
    if r['model']=='deepseek_v4_pro' and int(r['task_id']) in TASKS and r['fault_type'] in FAULTS and int(r['seed'])<=5:
      r['model']='deepseek'; r['architecture']='react'
      # The historical comparison table repeats shared controls once per fault.
      # Normalize them to one control per task/seed before building the matrix.
      if r['condition'] == 'control':
        r['fault_type'] = 'control'
      ds.append(r)
  for r in read_csv(ROOT/'experiments/analysis/deepseek_architecture_final_v1/deepseek_architecture_trials.csv'):
    if r['architecture']=='plan_execute' and int(r['task_id']) in TASKS and r['fault_type'] in FAULTS and int(r['seed'])<=5:
      ds.append(r)
  # Use one control per task/seed for each model/architecture; discard extra P&E controls.
  final=[]; seen=set()
  for r in gpt+ds:
    key=(r['model'],r['architecture'],r['condition'],r['fault_type'],int(r['task_id']),int(r['seed']))
    if key in seen: continue
    seen.add(key); final.append(r)
  OUT.mkdir(parents=True,exist_ok=True)
  fields=list(final[0]);
  with (OUT/'model_architecture_trials.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(final)
  summary=[]
  for model in ('gpt54','deepseek'):
   for arch in ('react','plan_execute'):
    for fault in ('control','web_dom_missing','web_http_error'):
     c='control' if fault=='control' else 'fault'; group=[r for r in final if r['model']==model and r['architecture']==arch and r['fault_type']==fault and r['condition']==c]
     if group: summary.append({'model':model,'architecture':arch,'fault_type':fault,'condition':c,'trials':len(group),'official_successes':sum(str(r['official_success']).lower()=='true' for r in group),'official_success_rate':sum(str(r['official_success']).lower()=='true' for r in group)/len(group),'evaluator_errors':sum(str(r['evaluator_status']).upper()=='ERROR' for r in group)})
  with (OUT/'model_architecture_summary.csv').open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
  (OUT/'manifest.json').write_text(json.dumps({'target_per_model':90,'actual_records':len(final),'models':['gpt54','deepseek'],'architectures':['react','plan_execute'],'tasks':sorted(TASKS),'seeds':[1,2,3,4,5],'conditions':['control','web_dom_missing','web_http_error'],'note':'One control per model/architecture/task/seed is retained; extra control replicates are excluded from the balanced primary dataset.'},indent=2)+'\n')
  print(json.dumps({'records':len(final),'gpt54':sum(r['model']=='gpt54' for r in final),'deepseek':sum(r['model']=='deepseek' for r in final)}))
if __name__=='__main__': main()
