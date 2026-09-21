#!/usr/bin/env python3
"""Merge schema-corrected rebuilt and rerun official trial results."""

import csv, json, re
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REBUILT=ROOT/'experiments/analysis/rebuilt_official_responses_v1'
RERUN=ROOT/'experiments/rerun_missing_trials_v1/outputs'
ARCH_GAPS=ROOT/'experiments/rerun_architecture_gaps_v1/outputs'
OUT=ROOT/'experiments/analysis/schema_corrected_official_v1'

def parse(path, rerun=False):
    parts=path.parts
    if rerun:
        outer=parts[-4]; task=int(parts[-3]); leaf=parts[-2]
        match=re.match(r'(react|plan_execute)_(control|fault)_(.+)_task(\d+)_seed(\d+)',outer)
        arch,condition,label,task_id,seed=match.groups(); task_id=int(task_id); seed=int(seed)
        fault='control' if condition=='control' else label
        source='rerun_missing_trials_v1'
    else:
        outer=next(p for p in parts if '_task' in p and '_seed' in p)
        match=re.match(r'(?:(react|plan_execute)_)?(?:(control|fault)_)?(.+)_task(\d+)_seed(\d+)',outer)
        arch,explicit,label,task_id,seed=match.groups(); task_id=int(task_id); seed=int(seed)
        leaf=parts[-2]; condition='control' if leaf.startswith('control_') else 'fault'
        if not arch: arch='react'
        if label.startswith('control_control'): fault='control'; condition='control'
        elif label.startswith('fault_'): fault=label.removeprefix('fault_')
        else: fault='control' if condition=='control' else label
        source=parts[0]
    result=json.loads(path.read_text())
    return {'architecture':arch,'condition':condition,'fault_type':fault,'task_id':task_id,'seed':seed,'official_success':result.get('official_success'),'evaluator_status':result.get('status'),'evaluator_error':result.get('error_msg'),'agent_response':str(path.with_name('agent_response.json').relative_to(ROOT)),'network_har':str(path.with_name('network.har').relative_to(ROOT)),'official_eval':str(path.relative_to(ROOT)),'source':source,'rerun':rerun}

def write_csv(path, rows):
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def main():
    records=[]
    for p in REBUILT.rglob('official_eval.json'): records.append(parse(p))
    for p in RERUN.rglob('official_eval.json'): records.append(parse(p,rerun=True))
    for p in ARCH_GAPS.rglob('official_eval.json'): records.append(parse(p,rerun=True))
    groups=defaultdict(list)
    for r in records: groups[(r['architecture'],r['condition'],r['fault_type'],r['task_id'],r['seed'])].append(r)
    final=[]; audit=[]
    for key, group in sorted(groups.items()):
        # Prefer freshly rerun records; otherwise use a deterministic historical source.
        group.sort(key=lambda r:(not r['rerun'],r['source']))
        chosen=group[0].copy(); chosen['source_count']=len(group); chosen['source_chain']=';'.join(sorted({r['source'] for r in group}))
        final.append(chosen)
        if len(group)>1: audit.append({'architecture':key[0],'condition':key[1],'fault_type':key[2],'task_id':key[3],'seed':key[4],'records':len(group),'chosen_source':chosen['source'],'source_chain':chosen['source_chain']})
    OUT.mkdir(parents=True,exist_ok=True)
    write_csv(OUT/'official_trials.csv',final)
    write_csv(OUT/'duplicate_resolution.csv',audit or [{'architecture':'','condition':'','fault_type':'','task_id':'','seed':'','records':0,'chosen_source':'','source_chain':''}])
    summary=[]
    for arch in sorted({r['architecture'] for r in final}):
        for fault in sorted({r['fault_type'] for r in final}):
            for condition in ('control','fault'):
                g=[r for r in final if r['architecture']==arch and r['fault_type']==fault and r['condition']==condition]
                if g: summary.append({'architecture':arch,'fault_type':fault,'condition':condition,'trials':len(g),'official_successes':sum(bool(r['official_success']) for r in g),'official_success_rate':sum(bool(r['official_success']) for r in g)/len(g),'evaluator_errors':sum(r['evaluator_status']=='ERROR' for r in g)})
    write_csv(OUT/'official_summary.csv',summary)
    # Pair architectures only where same task/seed/condition/fault exists.
    index={(r['architecture'],r['condition'],r['fault_type'],r['task_id'],r['seed']):r for r in final}; pairs=[]
    for (_,condition,fault,task,seed),react in sorted(index.items()):
        if _!='react': continue
        plan=index.get(('plan_execute',condition,fault,task,seed))
        if plan: pairs.append({'condition':condition,'fault_type':fault,'task_id':task,'seed':seed,'react_success':react['official_success'],'plan_execute_success':plan['official_success'],'success_delta_plan_minus_react':int(bool(plan['official_success']))-int(bool(react['official_success']))})
    write_csv(OUT/'architecture_paired.csv',pairs or [{'condition':'','fault_type':'','task_id':'','seed':'','react_success':'','plan_execute_success':'','success_delta_plan_minus_react':''}])
    expected=[]
    for arch in ('react','plan_execute'):
        for task in (22,24,27,28,30,132,133,134):
            for seed in (1,2,3,4,5):
                for condition,fault in (('control','control'),('fault','web_dom_missing'),('fault','web_http_error')):
                    key=(arch,condition,fault,task,seed)
                    if key not in index:
                        expected.append({'architecture':arch,'condition':condition,'fault_type':fault,'task_id':task,'seed':seed,'status':'missing_schema_corrected_trial'})
    write_csv(OUT/'coverage_gaps.csv',expected or [{'architecture':'','condition':'','fault_type':'','task_id':'','seed':'','status':'complete'}])
    manifest={'raw_records':len(records),'unique_trials':len(final),'duplicate_keys':len(audit),'rerun_records':sum(r['rerun'] for r in final),'evaluator_errors':sum(r['evaluator_status']=='ERROR' for r in final),'coverage_gaps':len(expected),'note':'Rerun trials override rebuilt historical records for the same architecture/condition/fault/task/seed key.'}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n'); print(json.dumps(manifest))
if __name__=='__main__': main()
