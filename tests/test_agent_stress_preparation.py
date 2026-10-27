"""Regressions for grading bindings, HC visibility and clean-stage execution gates."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

from scripts import agent_stress_screening as screening
from scripts.agent_stress_decision import ROOT, verify
from scripts.agent_stress_offline import check_decision_impact


class DecisionEvidenceRegression(unittest.TestCase):
    def setUp(self):
        self.fixture = ROOT / "experiments/agent_stress_v1/decisions/fixture_reviews"
        self.evidence = json.loads((self.fixture / "evidence.json").read_text())

    def test_saved_success_booleans_cannot_replace_grading(self):
        self.evidence["official"] = {"truth": {"rule": "unsupported", "artifact": {
            "path": str((self.fixture / "official_truth.json").relative_to(ROOT)),
            "sha256": hashlib.sha256((self.fixture / "official_truth.json").read_bytes()).hexdigest()}}}
        result = verify(self.evidence)
        self.assertFalse(result["passed"])
        self.assertTrue(any("saved booleans" in error for error in result["errors"]))

    def test_synthetic_positive_does_not_verify_real_contract(self):
        result = verify(self.evidence)
        self.assertTrue(result["passed"], result["errors"])
        self.assertFalse(result["real_contract_eligible"])
        messages = check_decision_impact({"status": "verified", "task_id": 21,
            "decision_impact": {"evidence": self.evidence}, "recovery_references": {"HC_episode": {}}})
        self.assertTrue(any("native grading required" in message for message in messages))

    def test_hc_evidence_cannot_be_omitted(self):
        self.evidence.pop("hc")
        self.assertFalse(verify(self.evidence, require_hc=True)["passed"])

    def test_hc_requires_field_entity_and_distinct_scopes(self):
        for key, value in (("c_field", "missing_field"), ("c_entity_key", "another author"),
                           ("c_value", ""), ("scopes", {"H": "same", "C": "same"})):
            with self.subTest(key=key):
                evidence = copy.deepcopy(self.evidence)
                evidence["hc"][key] = value
                self.assertFalse(verify(evidence)["passed"])

    def test_alternate_artifact_root_is_honored(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / self.fixture.relative_to(ROOT)
            shutil.copytree(self.fixture, destination)
            self.assertTrue(verify(self.evidence, root=root)["passed"])
            (destination / "truth_input.json").write_text('{}')
            self.assertFalse(verify(self.evidence, root=root)["passed"])

    def test_computation_timeout_is_rejection(self):
        with patch('scripts.agent_stress_decision.subprocess.run', side_effect=subprocess.TimeoutExpired('compute', 30)):
            result = verify(self.evidence)
        self.assertFalse(result["passed"])

    def test_different_legal_protected_output_is_allowed(self):
        # The real task118 example has three different URLs, all accepted by the
        # recorded navigation rule. Its ONLY failure is polluted still accepted.
        evidence = json.loads((self.fixture.parent / 'task118_title/evidence.json').read_text())
        result = verify(evidence)
        self.assertNotEqual(result['outputs']['truth'], result['outputs']['protected'])
        self.assertTrue(result['grading']['protected'])
        self.assertEqual(len(result['errors']), 1)
        self.assertIn('official:polluted', result['errors'][0])


class ScreeningPreparation(unittest.TestCase):
    def setUp(self):
        self.candidates = screening.load(ROOT / 'docs/故障压力测试候选任务.json')
        self.matrix = screening.load(ROOT / 'docs/故障压力测试矩阵草案.json')
        self.plan = screening.build_plan(self.candidates, self.matrix)

    def test_complete_clean_plan_is_independent_of_hc_contracts(self):
        self.assertEqual(len(self.plan['cells']), 348)
        self.assertEqual(len({cell['key'] for cell in self.plan['cells']}), 348)
        self.assertEqual({cell['condition'] for cell in self.plan['cells']}, {'control'})
        self.assertFalse(self.plan['requires_HC_runtime'])
        self.assertEqual(screening.build_plan(self.candidates, self.matrix), self.plan)

    def test_requested_screening_defaults_to_existing_qwen_profile(self):
        self.assertEqual(self.plan['model_profile'], 'qwen38_flash')
        self.assertEqual({cell['model_profile'] for cell in self.plan['cells']}, {'qwen38_flash'})
        self.assertEqual(self.plan['eligibility_applies_to_model_profile'], 'qwen38_flash')
        self.assertFalse(self.plan['cross_model_eligibility_transfer_allowed'])
        self.assertEqual(self.matrix['scope']['model_profiles'], ['deepseek_v41_flash'])

    def test_model_switch_preserves_348_cells_but_changes_identity(self):
        original=copy.deepcopy(self.matrix)
        deepseek=screening.build_plan(self.candidates,self.matrix,model_profile='deepseek_v41_flash')
        qwen=screening.build_plan(self.candidates,self.matrix,model_profile='qwen38_flash')
        identity=lambda plan: [(cell['task_id'],cell['architecture'],cell['seed'],cell['condition']) for cell in plan['cells']]
        self.assertEqual(identity(deepseek),identity(qwen))
        self.assertEqual(len(qwen['cells']),348)
        self.assertNotEqual(deepseek['experiment_id'],qwen['experiment_id'])
        self.assertNotEqual(deepseek['plan_sha256'],qwen['plan_sha256'])
        self.assertFalse({cell['key'] for cell in deepseek['cells']} & {cell['key'] for cell in qwen['cells']})
        self.assertEqual(self.matrix,original)
        with self.assertRaisesRegex(ValueError,'unsupported'):
            screening.build_plan(self.candidates,self.matrix,model_profile='unconfigured_model')

    def test_dataset_must_bind_all_58_exact_task_definitions(self):
        rows = [{"task_id": task['task_id'], "intent_template_id": task['intent_template_id'],
                 "intent": task['intent'], "sites": [task['site']],
                 "start_urls": [f"__{task['site'].upper()}__/task-{task['task_id']}"],
                 "eval": [{"expected": {}}]} for task in self.candidates['tasks']]
        with tempfile.TemporaryDirectory() as folder:
            dataset = Path(folder)/'dataset.json'
            dataset.write_text(json.dumps(rows))
            bound = screening.build_plan(self.candidates, self.matrix, dataset)
            self.assertTrue(bound['dataset_sha256'])
            self.assertTrue(all(cell['start_url'] for cell in bound['cells']))
            rows[0]['intent'] = 'different intent'
            dataset.write_text(json.dumps(rows))
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                screening.build_plan(self.candidates, self.matrix, dataset)

    def test_unapproved_batch_never_spawns_or_creates_run_root(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(screening.subprocess, 'run') as run:
            root = Path(folder)/'run'
            with self.assertRaises(ValueError):
                screening.execute(self.plan, {'approved_for_paid_runs': False},
                                  {'environment_ready': False}, 'missing', root,
                                  dataset='missing', authorization_path=Path(folder)/'auth.json')
            run.assert_not_called()
            self.assertFalse(root.exists())

    def test_hash_and_scope_changes_are_rejected(self):
        report = {'model_profile': self.plan['model_profile'], 'environment_ready': True, 'evaluator_version': {'package_sha256': 'version'},
                  'model_identity_sha256': 'model'}
        with tempfile.NamedTemporaryFile() as config:
            plan = copy.deepcopy(self.plan)
            plan['dataset_sha256'] = 'dataset'
            for cell in plan['cells']:
                cell['start_url'] = 'http://localhost:7770/test'
            plan['plan_sha256'] = screening.digest({k:v for k,v in plan.items() if k != 'plan_sha256'})
            auth = screening.authorization_template(plan, report, config.name)
            auth.update(status='frozen', approved_for_paid_runs=True,
                        prompt_token_stop_before_next_trial=1000000)
            self.assertEqual(screening.check_authorization(plan, auth, report, config.name), [])
            for key, value in (('plan_sha256', 'other'), ('max_trials', 349),
                               ('max_steps', 40), ('code_sha256', 'other'),
                               ('dataset_sha256', 'other'), ('approval_consumed', True),
                               ('model_profile','deepseek_v41_flash')):
                altered = dict(auth, **{key:value})
                self.assertTrue(screening.check_authorization(plan, altered, report, config.name), key)
            plan['cells'][0]['condition'] = 'fault'
            self.assertTrue(any('plan content' in error for error in
                                screening.check_authorization(plan, auth, report, config.name)))

    def test_command_only_runs_one_control_and_no_expected_answer(self):
        cell = dict(self.plan['cells'][0], start_url='http://localhost:7770/test')
        cmd = screening.command(cell, Path('/tmp/output'))
        self.assertEqual(cmd[cmd.index('--condition')+1], 'control')
        self.assertEqual(cmd[cmd.index('--trials')+1], '1')
        self.assertNotIn('--expected-answer', cmd)
        self.assertNotIn('--fault-type', cmd)
        self.assertEqual(cmd[cmd.index('--model-profile')+1], 'qwen38_flash')

    def test_different_model_authorization_never_starts_a_process(self):
        report={'environment_ready':True,'model_profile':'qwen38_flash',
                'evaluator_version':{'package_sha256':'version'},'model_identity_sha256':'model'}
        with tempfile.TemporaryDirectory() as folder, patch.object(screening.subprocess,'run') as run:
            config=Path(folder)/'config.json';config.write_text('{}')
            plan=copy.deepcopy(self.plan)
            plan['dataset_sha256']='dataset'
            for cell in plan['cells']:
                cell['start_url']='http://localhost:7770/test'
            plan['plan_sha256']=screening.digest({k:v for k,v in plan.items() if k!='plan_sha256'})
            auth=screening.authorization_template(plan,report,config)
            auth.update(status='frozen',approved_for_paid_runs=True,
                        prompt_token_stop_before_next_trial=1000000,model_profile='deepseek_v41_flash')
            with self.assertRaisesRegex(ValueError,'model_profile'):
                screening.execute(plan,auth,report,config,Path(folder)/'run',dataset='dataset',
                                  authorization_path=Path(folder)/'auth.json')
            run.assert_not_called()

    def test_trace_usage_is_actual_and_missing_usage_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            trace=Path(folder)/'trace.jsonl'
            record={'paths':{'trace':str(trace)}}
            trace.write_text(json.dumps({'event':'llm_provenance','prompt_tokens':123})+'\n')
            self.assertEqual(screening.trace_prompt_tokens(record),123)
            trace.write_text(json.dumps({'event':'llm_provenance','prompt_tokens':None})+'\n')
            with self.assertRaises(ValueError):
                screening.trace_prompt_tokens(record)

    def test_runner_grades_records_usage_and_stops_before_next_trial(self):
        report = {'model_profile': self.plan['model_profile'], 'environment_ready': True, 'evaluator_version': {'package_sha256': 'version'},
                  'model_identity_sha256': 'model'}
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            config=folder/'config.json'; config.write_text('{}')
            dataset=folder/'dataset.json'; dataset.write_text('[]')
            plan=copy.deepcopy(self.plan)
            plan['dataset_sha256']=screening.file_sha(dataset)
            for cell in plan['cells']:
                cell['start_url']='http://localhost:7770/test'
            plan['plan_sha256']=screening.digest({k:v for k,v in plan.items() if k!='plan_sha256'})
            auth=screening.authorization_template(plan,report,config)
            auth.update(status='frozen',approved_for_paid_runs=True,
                        prompt_token_stop_before_next_trial=50)
            auth_path=folder/'auth.json'; auth_path.write_text(json.dumps(auth))
            run_root=folder/'run'
            def fake_child(cmd, **kwargs):
                self.assertEqual(cmd[cmd.index('--model-profile')+1], 'qwen38_flash')
                self.assertEqual(cmd[cmd.index('--experiment-id')+1], plan['experiment_id'])
                cell=plan['cells'][0]
                output=Path(cmd[cmd.index('--webarena-output-dir')+1])
                arm=output/str(cell['task_id'])/f"control_seed_{cell['seed']}"
                arm.mkdir(parents=True)
                trace=arm/'trace.jsonl'
                trace.write_text(json.dumps({'event':'llm_provenance','prompt_tokens':100})+'\n')
                (arm/'trial_record.json').write_text(json.dumps({'injection_count':0,'paths':{'trace':str(trace)}}))
                return SimpleNamespace(returncode=0)
            artifacts=Mock(return_value=(True,[]))
            grade=Mock(return_value={'evaluation_status':'native','official_success':False})
            with patch.object(screening.subprocess,'run',side_effect=fake_child) as run, patch.dict('os.environ'):
                with self.assertRaisesRegex(RuntimeError,'prompt-token limit'):
                    screening.execute(plan,auth,report,config,run_root,dataset=dataset,authorization_path=auth_path,
                                      artifact_checker=artifacts,evaluator=grade)
                self.assertEqual(run.call_count,1)
                self.assertEqual(grade.call_count,1)
                self.assertEqual(artifacts.call_args.kwargs['expect']['fault_type'],'control')
                self.assertEqual(artifacts.call_args.kwargs['expect']['model_profile'],'qwen38_flash')
                self.assertEqual(artifacts.call_args.kwargs['expect']['experiment_id'],plan['experiment_id'])
                progress=screening.load(run_root/'progress.json')
                self.assertEqual(progress['prompt_tokens'],100)
                self.assertFalse(progress['completed'][0]['official_success'])
                # A failed clean result was retained, not rerun for a success.
                with self.assertRaises(FileExistsError):
                    screening.execute(plan,auth,report,config,folder/'other-run',dataset=dataset,authorization_path=auth_path,
                                      artifact_checker=artifacts,evaluator=grade)
                self.assertEqual(run.call_count,1)


if __name__ == '__main__':
    unittest.main()
