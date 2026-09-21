#!/usr/bin/env python3
"""Complete the DeepSeek Plan-and-Execute 60-trial target matrix."""
import csv, json, subprocess, sys, time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/deepseek_plan_execute_completion_v1"
AUDIT = ROOT / "experiments/analysis/deepseek_plan_execute_audit_v1/audit.csv"
TASK_IDS = [27, 30, 132]
FAULTS = ["web_dom_missing", "web_http_error"]

def main():
    from run_baseline import load_tasks, resolve_start_url
    tasks = {t["task_id"]: t for t in load_tasks(task_ids=TASK_IDS)}
    rebuilt = set()
    if AUDIT.exists():
        for row in csv.DictReader(AUDIT.open()):
            if row["status"] == "rebuildable":
                rebuilt.add(("plan_execute", row["condition"], row["fault_type"], int(row["task_id"]), int(row["seed"])))
    jobs = []
    for task_id in TASK_IDS:
        task = tasks[task_id]; site = (task.get("sites") or [None])[0]
        for seed in range(1, 6):
            for condition, fault in [("control", "control"), ("fault", "web_dom_missing"), ("fault", "web_http_error")]:
                key = ("plan_execute", condition, fault, task_id, seed)
                if key not in rebuilt:
                    jobs.append((task_id, seed, condition, fault, site, resolve_start_url(task), task["intent"]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "manifest.json").write_text(json.dumps({"target_trials":60,"skipped_rebuilt":len(rebuilt),"scheduled":len(jobs),"architecture":"plan_execute","model_profile":"deepseek","tasks":TASK_IDS,"faults":FAULTS,"seeds":[1,2,3,4,5]}, indent=2) + "\n")
    pending=deque(jobs); active={}; done=0
    while pending or active:
        while pending and len(active)<6:
            tid,seed,condition,fault,site,url,intent=pending.popleft()
            key=f"plan_execute_{condition}_{fault}_task{tid}_seed{seed}"
            log=(OUT/f"{key}.log").open("w",encoding="utf-8")
            cmd=[sys.executable,"main.py","--benchmark","--site",site,"--url",url,"--task",intent,"--task-id",str(tid),"--model-profile","deepseek","--architecture","plan_execute","--max-steps","20","--trials","1","--condition",condition,"--fault-seed",str(seed),"--webarena-output-dir",str(OUT/"outputs"/key)]
            if condition=="fault": cmd += ["--fault-type",fault,"--fault-intensity","high","--fault-injection-step","2"]
            p=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            active[key]=(p,log); print(f"START {key} pid={p.pid}",flush=True)
        for key,(p,log) in list(active.items()):
            if p.poll() is not None:
                log.close(); active.pop(key); done+=1; print(f"DONE {key} rc={p.returncode}",flush=True)
        if pending or active: time.sleep(.5)
    (OUT/"summary.json").write_text(json.dumps({"completed":done,"scheduled":len(jobs),"skipped_rebuilt":len(rebuilt)},indent=2)+"\n")
    print(json.dumps({"completed":done,"scheduled":len(jobs),"skipped_rebuilt":len(rebuilt)}))
if __name__ == "__main__": main()
