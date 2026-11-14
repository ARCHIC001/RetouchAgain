"""Publication derivative of src/check_dsh_stage_phase_worker.py.

Exactly 33 synthetic-only cases retained from the original 35-case suite.
Excluded: test_real_r1_second_response_replays_only_on_new_synthetic_state;
test_real_r2_raw_turns_recover_future_reference_and_continue_actual_effect.
No field-run requests, responses, results or archive files are included/read.
Retained cases are copied without assertion or algorithm changes. The setup
stubs the external bundle activation only for the already-fake native backend;
this derivative does not establish native dependency or decoder qualification.
Run with Python -I -B -X utf8; real Native/provider/network/process IO is blocked.
This is not the full original suite or photographic-quality evidence.
"""
import copy
import json
from contextlib import ExitStack
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
from dsh_stage_replay_fixture import (StrictSyntheticFixture, SyntheticBackend,
                                      fixture_stats, lab, stage, validation, write,
                                      synthetic_debt_updates)

LATEST = SRC.parent / 'experiments/validation/latest'


class PhaseWorkerChecks(unittest.TestCase):
    def setUp(self):
        LATEST.mkdir(parents=True, exist_ok=True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(
            prefix='stage-SYNTHETIC-phase-', dir=LATEST)))
        forbidden = AssertionError('Real backend/provider/network/process calls forbidden')
        self.real_call_guards = []
        for owner, name in ((lab, 'Native'), (lab, 'api'), (lab.LightCraft, 'call'),
                            (socket, 'create_connection'), (socket.socket, 'connect'), (subprocess, 'Popen')):
            self.real_call_guards.append(self.stack.enter_context(patch.object(owner, name, side_effect=forbidden)))
        self.fixture = StrictSyntheticFixture(root)
        self.stack.enter_context(patch.object(lab, 'ROOT', root))
        self.stack.enter_context(patch.object(stage, 'StageBackend', SyntheticBackend))
        # The fake backend never decodes RAW or uses the unpublished native bundle.
        self.stack.enter_context(patch('photo_image_dependencies.activate_image_dependencies',
            return_value={'synthetic_only': True, 'runtime_install': False}))
        def inspect_raw(path):
            if Path(path) != self.fixture.raw:
                raise AssertionError('Only the labelled synthetic RAW boundary may be fake')
            return {'fixture_only': True, 'dimensions': self.fixture.dimensions}
        self.stack.enter_context(patch.object(lab, 'inspect_dng', side_effect=inspect_raw))
        self.session = stage.StageSession(self.fixture.round, validation.jobs()[0])
        self.addCleanup(self.close_backend)
        self.assertEqual(self.session.state['workflow']['phase'], 'G1')

    def close_backend(self):
        try:
            self.assertTrue(all((path.read_bytes(), path.stat().st_mtime_ns) == before
                                for path, before in self.fixture.protected.items()), 'Source bytes/mtimes changed')
            for guard in self.real_call_guards:
                guard.assert_not_called()
        finally:
            if self.session.native is not None:
                self.session.native.close()

    def submission(self, name, arguments, *, bank=None, issue=None, module=None, debts=True):
        context = self.session.project_snapshot()['context']
        review = {'visible_comparison': 'Actual supplied SYNTHETIC stored pixels only; no photographic quality claim.',
                  'remaining_gaps': 'Synthetic engineering fixture.', 'uncertainty': 'Fake native response.',
                  'previous_outcome': 'uncertain'}
        if context['workflow']['phase'] == 'G1' and context['comparison_mode'] == 'target':
            review['tone_assessment'] = {'items': [], 'neutral_anchors': [],
                'reason': 'Synthetic no attribution claim'}
        if context['workflow']['phase'] == 'G2':
            review['wb_assessment'] = {'reference_status': 'not_available', 'reference_pairs': [],
                'target_intent': 'uncertain', 'intent_reason': 'Synthetic shapes are not physical-neutral materials.',
                'atmosphere': {'route': 'none', 'reason': 'No photographic atmosphere claim.'},
                'wb_task_status': 'not_needed', 'reason': 'No WB claim in this generic synthetic fixture.'}
        if name in ('photo_edit', 'pre_tone_wb'):
            review.update(module_decisions=[{'module': module or 'light', 'choice': 'use',
                'reason': 'Exercise exact synthetic operation coverage.'}], effect_expectation={
                'target': 'Synthetic colored rectangle', 'expected_change': 'Controlled stored raster change.',
                'protected_region': 'Whole original canvas', 'risk': 'No visible response.',
                'falsifier': 'Actual parent/current pixels do not change.'})
        if context.get('pending_effect_review'):
            pending = context['pending_effect_review']
            review['effect_review'] = {'expectation_id': pending['id'],
                'outcome': 'uncertain', 'evidence': 'Only the actually supplied stored synthetic views.'}
            if any(debt.get('operation_id') == pending['operation_id'] and debt.get('tone_boundary')
                   for debt in context['workflow']['review_debts']):
                review['tone_boundary_review'] = {'operation_id': pending['operation_id'],
                    'outcome': 'uncertain', 'evidence': 'Synthetic mapping does not establish photographic boundary protection.'}
        value = {'contract_revision': context['contract_revision'], 'review': review,
                 'bank_delta': [] if bank is None else bank, 'action': {'name': name, 'arguments': arguments}}
        if issue is not None:
            value['selected_issue_id'] = issue
        updates = synthetic_debt_updates(context) if debts else []
        if updates:
            value['review_debt_updates'] = updates
        return value

    def submit(self, value):
        return self.session.stage_turn(value, self.session.state['observations'][-1]['id'])

    def advance(self, *, bank=None, debts=True, **arguments):
        return self.submit(self.submission('advance', {'reason': 'Explicitly accept this synthetic branch/checkpoint.',
            **arguments}, bank=bank, debts=debts))

    @staticmethod
    def keep(ident):
        return {'op': 'keep', 'id': ident, 'reason': 'Explicitly reassessed this actually observed version.'}

    def enter_tone(self):
        self.advance(bank=[{'op': 'add', 'id': 'tone', 'branch': 'tone', 'diagnosis': 'Synthetic midtones differ.',
            'proposed_controls': ['light.exposure']}, {'op': 'add', 'id': 'color', 'branch': 'color',
            'diagnosis': 'Synthetic color differs.', 'proposed_controls': ['color.saturation']}])

    def edit(self, value, issue='tone', module='light'):
        color = self.session.state['workflow']['phase'] == 'color'
        return self.submit(self.submission('photo_edit', {'base_version': self.session.state['current'],
            'values': {'color.saturation' if color else 'light.exposure': value},
            'rationale': 'Synthetic direct-worker regression.', 'predicted_effect': 'Inspect actual stored child pixels.'},
            bank=[self.keep(issue)], issue=issue, module=module))

    def finish(self, confirm):
        return self.submit(self.submission('photo_finish', {'selected_version': self.session.state['current'],
            'confirm_export': confirm, 'goal_status': 'unresolved'}))

    def reach_g4(self):
        self.enter_tone()
        self.edit(.5)
        self.advance(bank=[self.keep('tone')])
        self.advance(bank=[self.keep('color')], color_reassessment=[{
            'issue_id': 'color', 'decision': 'keep', 'reason': 'Actually reassessed accepted tone in color.'}])
        self.edit(15, issue='color', module='mixer_point')
        self.advance(bank=[self.keep('color')])
        self.advance(bank=[{'op': 'add', 'id': 'joint', 'branch': 'joint', 'diagnosis': 'Synthetic final brightness.'}])
        self.edit(.6, issue='joint')
        self.finish(False)
        self.assertEqual(self.session.state['workflow']['phase'], 'G4')

    def wb_assessment(self, available=False, *, issue=None, group=None):
        value = {'reference_status': 'available' if available else 'not_available',
            'reference_pairs': [{'author_cell': 'A1', 'current_cell': 'C3',
                'material': 'SYNTHETIC reference rectangle, not a photographed wall.',
                'lighting': 'Declared synthetic uniform light only.',
                'neutrality_basis': 'Fixture-declared neutral; no physical-neutral evidence.',
                'temperature_residual': 'Synthetic current rectangle is warmer.',
                'tint_residual': 'Synthetic current rectangle is greener.'}] if available else [],
            'target_intent': 'intentional_cast', 'intent_reason': 'Preserve the declared warm synthetic reference.',
            'atmosphere': {'route': 'grading', 'reason': 'Separate atmosphere from the WB response hypothesis.'},
            'wb_task_status': 'needed', 'reason': 'Exercise target evidence without a photographic correctness claim.'}
        if issue:
            value['issue_id'] = issue
        if group:
            value['group_id'] = group
        return value

    def enter_wb_color(self, controls=None):
        self.advance(bank=[])
        self.advance()
        self.assertEqual(self.session.state['workflow']['phase'], 'G2')
        value = self.submission('advance', {'reason': 'Synthetic WB basis and Color proposal in one turn.'}, bank=[{
            'op': 'add', 'id': 'wb', 'branch': 'color', 'diagnosis': 'Declared synthetic WB residual.',
            'proposed_controls': controls if controls is not None else ['wb.temp', 'wb.tint', 'develop.wbPick']}])
        value['review']['wb_assessment'] = self.wb_assessment(True, issue='wb')
        self.submit(value)
        self.assertEqual(self.session.state['workflow']['phase'], 'color')
        return next(iter(self.session.state['proposal_bank']['issues']['wb']['task_groups']))

    def operation(self, args, *, issue='wb', group=None, module='wb'):
        value = self.submission('photo_edit', {'base_version': self.session.state['current'], **args,
            'rationale': 'Explicit synthetic worker integration operation.',
            'predicted_effect': 'Only the controlled stored synthetic raster response.'},
            bank=[self.keep(issue)], issue=issue, module=module)
        if group:
            value['selected_group_id'] = group
        return value

    def coarse(self, values=None, *, add=False):
        value = self.submission('pre_tone_wb', {'base_version': self.session.state['current'],
            'values': {'wb.temp': 5583, 'wb.tint': 4} if values is None else values,
            'rationale': 'One explicitly justified SYNTHETIC coarse WB correction.',
            'predicted_effect': 'Controlled WB raster response before selecting a tone basis.'},
            bank=[{'op': 'add', 'id': 'coarse', 'branch': 'color', 'diagnosis': 'Declared synthetic severe cast.',
                'groups': [{'group_id': 'scalar', 'proposed_controls': ['wb.temp', 'wb.tint'],
                    'reason': 'One scalar WB hypothesis, not a general G1 edit.'}]}] if add else [self.keep('coarse')],
            issue='coarse', module='wb')
        value['selected_group_id'] = 'coarse::scalar'
        value['review']['wb_assessment'] = self.wb_assessment(issue='coarse', group='coarse::scalar')
        value['review']['tone_basis_impact'] = 'Declared severe cast obscures the synthetic neutral tone basis; retain intended warm atmosphere.'
        return value

    def reload(self):
        if self.session.native is not None:
            self.session.native.close()
        self.session = stage.StageSession(self.fixture.round, validation.jobs()[0])

    def assert_unexecuted(self, value, code):
        before = self.session._photography_state()
        calls = copy.deepcopy(self.session.native.calls if self.session.native else [])
        result = self.submit(value)['stage_turn_result']
        self.assertEqual(result['code'], code)
        self.assertEqual(result['kind'], 'rejected_before_execution')
        self.assertTrue(result['no_effect']['passed'])
        self.assertEqual(self.session._photography_state(), before)
        self.assertEqual(self.session.native.calls if self.session.native else [], calls)
        return result

    def test_memory_v16_skip_soft_waiver_same_turn_keep_has_no_ghost_wait(self):
        self.advance(bank=[{'op': 'add', 'id': 'ordered', 'branch': 'tone',
            'diagnosis': 'Synthetic ordered dependency dispositions.', 'groups': [
                {'group_id': 'first', 'proposed_controls': ['light.exposure']},
                {'group_id': 'next', 'proposed_controls': ['light.contrast'],
                 'dependencies': [{'group_id': 'first', 'kind': 'soft', 'requirement': 'disposition'}]}]}])
        value = self.submission('photo_history', {})
        value['review']['group_reviews'] = [{'issue_id': 'ordered', 'group_id': 'first',
            'outcome': 'skip', 'reason': 'Explicitly skip this synthetic hypothesis on the actual version.'}]
        self.submit(value)
        groups = self.session.state['proposal_bank']['issues']['ordered']['task_groups']
        self.assertEqual(groups['ordered::first']['status'], 'skipped')
        self.assertEqual(groups['ordered::next']['status'], 'needs_reassessment')
        self.assertTrue(groups['ordered::next']['waiver_records'])
        value = self.operation({'values': {'light.contrast': 8}}, issue='ordered', group='ordered::next')
        value['bank_delta'] = [{**self.keep('ordered'), 'group_id': 'next'}]
        self.submit(value)
        self.assertNotIn('stage_turn_result', self.session.snapshot())
        self.assertEqual(self.session.state['current'], 'v1')
        packet = self.session.project_snapshot()['context']['work_packet']
        projected = [g for key in ('current', 'pending_review') for g in packet[key]
                     if g['id'] == 'ordered::next']
        self.assertTrue(projected)
        self.assertTrue(all(g['status'] == 'awaiting_effect' for g in projected),
                        json.dumps(projected, ensure_ascii=False))
        self.assertEqual(sum(c[0] == 'apply' for c in self.session.native.calls), 1)

    def test_memory_v16_observe_is_not_reset_accept_ancestor_keeps_old_detail(self):
        self.enter_tone()
        self.edit(.4)
        self.edit(.8)
        old_operation = copy.deepcopy(self.session.state['versions']['v2'])
        self.submit(self.submission('photo_observe', {'version': 'v1', 'comparison_mode': 'target',
            'question': 'Inspect ancestor without changing the selected actual baseline.'}))
        context = self.session.project_snapshot()['context']
        baseline = context['work_packet']['selected_baseline']
        self.assertEqual((baseline['selected_version'], baseline['observed_version']), ('v2', 'v1'))
        self.assertFalse(baseline['selected_is_observed'])
        self.assertIsNone(context['work_packet']['branch_reset_notice'])
        self.assertEqual(self.session.state.get('branch_reset_events', []), [])
        self.advance(bank=[self.keep('tone')], selected_version='v1')
        context = self.session.project_snapshot()['context']
        packet = context['work_packet']
        self.assertEqual(packet['branch_reset_notice']['title'], 'BRANCH RESET NOTICE')
        self.assertEqual([r['version'] for r in packet['selected_baseline']['chain']], ['v0', 'v1'])
        self.assertEqual([r['version'] for r in packet['selected_baseline']['excluded_inactive_versions']], ['v2'])
        self.assertEqual(context['current_settings'], self.session.state['versions']['v1']['settings'])
        self.assertEqual(context['proposal_bank']['work_packet']['$ref'], 'context.work_packet')
        self.assertNotIn('current', context['proposal_bank']['work_packet'])
        records = self.session._history_records()
        row = next(r for r in records if r['kind'] == 'action'
                   and r['record'].get('operation_id') == old_operation['operation_id'])
        detail = self.session._history_result({'mode': 'detail_get', 'record_id': row['id'], 'char_limit': 100000},
                                              self.session.state['observations'][-1])
        self.assertEqual(json.loads(detail['text'])['operation_id'], old_operation['operation_id'])
        self.assertEqual(self.session.state['versions']['v2'], old_operation)
        before = copy.deepcopy(self.session.state['branch_reset_events'])
        self.session.project_snapshot()
        self.session.project_snapshot()
        self.assertEqual(self.session.state['branch_reset_events'], before)

    def test_memory_v16_near_linear_create_warns_nonblocking_and_keeps_actual_ids(self):
        plan = {'action': 'create', 'kind': 'linear', 'geometry': {'start': [.5, .1], 'end': [.5, .7]},
                'values': {'exposure': .4}}
        self.advance(bank=[{'op': 'add', 'id': 'edge', 'branch': 'tone',
            'diagnosis': 'Synthetic local regions with intentionally nearby geometry.',
            'proposed_controls': {'mask_plan': plan}}])
        self.submit(self.operation({'mask_plan': plan}, issue='edge', module='masks'))
        ident = self.session.state['versions']['v1']['settings']['masks'][0]['id']
        nearby = {**plan, 'geometry': {'start': [.52, .1], 'end': [.52, .7]}}
        calls = sum(c[0] == 'apply' for c in self.session.native.calls)
        self.submit(self.operation({'mask_plan': nearby}, issue='edge', module='masks'))
        self.assertNotIn('stage_turn_result', self.session.snapshot())
        self.assertEqual(sum(c[0] == 'apply' for c in self.session.native.calls), calls + 1)
        context = self.session.project_snapshot()['context']
        warning = next(w for w in context['geometry_warnings'] if w['prospective'])
        self.assertEqual(warning['mask_ids'], [ident])
        self.assertFalse(warning['automatic_rejection'])
        self.assertFalse(warning['semantic_overlap'])
        masks = self.session.state['versions']['v2']['settings']['masks']
        self.assertEqual(len(masks), 2)
        self.assertNotEqual(masks[1]['id'], ident)
        self.assertIsInstance(masks[1]['id'], int)
        summary = context['work_packet']['active_masks_summary']
        self.assertTrue(summary['selected_is_observed'])
        self.assertEqual([m['id'] for m in summary['selected']['masks']], [m['id'] for m in masks])
        self.assertEqual(sum(command['id'] == 'mask.add' for tool, *commands in self.session.native.calls
                             if tool == 'command_run' for command in commands), 2)

    def test_memory_v16_v2_debt_requires_individual_checks_then_legacy_still_works(self):
        self.enter_tone()
        self.edit(.4)
        self.advance(bank=[self.keep('tone')])
        debt = copy.deepcopy(self.session.state['workflow']['review_debts'][0])
        self.assertEqual(debt['schema'], 'photo-review-debt/v2')
        self.assertEqual(len(debt['focus']), 2)
        self.assertEqual({f['purpose'] for f in debt['focus']}, {'target', 'protected'})
        self.assertTrue(all(f['extent'] == 'unknown' and 'cells' not in f for f in debt['focus']))
        bad = self.submission('photo_history', {})
        bad['review_debt_updates'] = [{'id': debt['id'], 'status': 'resolved', 'reason': 'Not valid for v2.'}]
        self.submit(bad)
        self.assertNotIn('stage_turn_result', self.session.snapshot())
        self.assertEqual(self.session.state['deferred_debt_reviews'][-1]['disposition'], 'not_adopted_debts_unchanged')
        bad = self.submission('photo_history', {})
        bad['review_debt_updates'][0]['checks'].pop()
        self.submit(bad)
        self.assertNotIn('stage_turn_result', self.session.snapshot())
        self.assertEqual(self.session.state['deferred_debt_reviews'][-1]['disposition'], 'not_adopted_debts_unchanged')
        self.assertEqual(self.session.state['workflow']['review_debts'][0]['status'], 'pending')
        self.submit(self.submission('photo_history', {}))
        resolved = self.session.state['workflow']['review_debts'][0]
        self.assertEqual((resolved['status'], resolved['resolution'], len(resolved['checks'])),
                         ('resolved', 'no_regression', 2))
        from photo_phase_scheduler import add_review_debt
        add_review_debt(self.session.state, {'id': 'legacy-test-only', 'due_phase': 'G2', 'version': 'v1',
            'reason': 'Explicit legacy focus-free compatibility fixture.'})
        self.session.save()
        legacy = self.submission('photo_history', {})
        self.assertEqual(legacy['review_debt_updates'][0]['status'], 'resolved')
        self.submit(legacy)
        self.assertEqual(self.session.state['workflow']['review_debts'][-1]['status'], 'resolved')

    def test_memory_v16_explicit_anchor_roi_check_spawns_diagnostic_only(self):
        self.enter_tone()
        roi = [.1, .2, .9, .8]
        self.submit(self.submission('photo_observe', {'version': 'v0', 'comparison_mode': 'target',
            'reference_roi': [0, 0, .5, .5], 'candidate_roi': roi,
            'question': 'Independent actual synthetic source/author crop grids.'}))
        source = copy.deepcopy(self.session.state['observations'][-1])
        value = self.operation({'values': {'light.exposure': .4}}, issue='tone', module='light')
        anchors = [{'id': purpose, 'purpose': purpose, 'extent': 'explicit_cells', 'view_role': 'current',
            'cells': cells, 'region': region, 'qualification': 'Explicit synthetic crop cell, not registration.'}
            for purpose, cells, region in [('target', ['A1'], 'Synthetic crop subject'),
                                          ('protected', ['D4'], 'Synthetic crop protection')]]
        value['review']['review_anchors'] = anchors
        self.submit(value)
        debt = self.session.state['workflow']['review_debts'][0]
        for focus, anchor in zip(debt['focus'], anchors):
            self.assertEqual(focus['anchor_id'], anchor['id'])
            self.assertEqual(focus['source']['observation_id'], source['id'])
            self.assertEqual(focus['source']['roi'], roi)
            self.assertEqual(focus['source']['grid'], source['stage_observation']['grid'])
            self.assertEqual(focus['cells'], anchor['cells'])
        self.advance(bank=[self.keep('tone')])
        before = copy.deepcopy(self.session.state['versions'])
        calls = copy.deepcopy(self.session.native.calls)
        value = self.submission('photo_history', {})
        update = value['review_debt_updates'][0]
        update.update(resolution='spawn_joint_task', joint_diagnosis='Synthetic boundary residual needs diagnosis, not an automatic edit.')
        for index, check in enumerate(update['checks']):
            check.update(review_cells=['B2'] if index == 0 else ['C3'],
                         outcome='no_regression' if index == 0 else 'spawn_joint_task')
        self.submit(value)
        self.assertNotIn('stage_turn_result', self.session.snapshot())
        reviewed = self.session.state['workflow']['review_debts'][0]
        self.assertEqual(reviewed['status'], 'reviewed_joint_task')
        self.assertTrue(reviewed['review_complete'])
        self.assertFalse(reviewed['photographic_fix_claimed'])
        self.assertEqual(len(reviewed['checks']), 2)
        self.assertTrue(all(c['source']['observation_id'] == self.session.state['observations'][-1]['id']
                            for c in reviewed['checks']))
        diagnostic = self.session.state['proposal_bank']['issues'][reviewed['joint_issue_id']]
        self.assertEqual(diagnostic['branch'], 'joint')
        self.assertEqual(diagnostic['groups'], [])
        self.assertEqual(diagnostic['task_groups'], {})
        self.assertNotIn('proposed_controls', diagnostic)
        self.assertEqual(self.session.state['versions'], before)
        self.assertEqual(self.session.native.calls, calls)

    def test_memory_v16_return_to_branch_restores_pending_not_real_resolution(self):
        self.enter_tone()
        self.edit(.4)
        self.edit(.8)
        self.advance(bank=[self.keep('tone')])
        value = self.submission('photo_history', {})
        value['review_debt_updates'] = value['review_debt_updates'][:1]
        self.submit(value)
        resolved = copy.deepcopy(self.session.state['workflow']['review_debts'][0])
        self.assertEqual(resolved['status'], 'resolved')
        self.advance(target_phase='tone', debts=False)
        self.submit(self.submission('photo_observe', {'version': 'v1', 'comparison_mode': 'target',
            'question': 'Explicit old branch selection evidence, not a review of v2.'}))
        self.advance(selected_version='v1', debts=False)
        self.assertEqual(self.session.state['workflow']['review_debts'][1]['status'], 'not_applicable_selected_branch')
        self.advance(target_phase='tone', debts=False)
        self.submit(self.submission('photo_observe', {'version': 'v2', 'comparison_mode': 'target',
            'question': 'Revisit retained actual v2 branch, without replaying its edit.'}))
        self.advance(selected_version='v2', debts=False)
        debts = self.session.state['workflow']['review_debts']
        self.assertEqual(debts[0], resolved)
        self.assertEqual(debts[1]['status'], 'pending')
        self.assertEqual(sum(c[0] == 'apply' for c in self.session.native.calls), 2)

    def test_tone_g1_missing_is_correctable_root_relocation_and_no_edit_path(self):
        from photo_stage_submission import normalize_submission
        value = self.submission('advance', {'reason': 'Explicit no-edit G1 path.'})
        assessment = value['review'].pop('tone_assessment')
        self.assert_unexecuted(value, 'tone_assessment_required')
        self.assertEqual(self.session.state.get('tone_assessments', []), [])
        forbidden = self.submission('photo_edit', {'base_version': 'v0', 'values': {'light.exposure': .5},
            'rationale': 'Must not expose general G1 editing.', 'predicted_effect': 'Forbidden.'}, issue='missing')
        self.assert_unexecuted(forbidden, 'action_outside_phase')
        value = self.submission('advance', {'reason': 'Explicit empty attribution, no mandatory coarse WB.'})
        value['review'].pop('tone_assessment')
        value['tone_assessment'] = assessment
        original = copy.deepcopy(value)
        canonical, warnings = normalize_submission(value)
        self.assertEqual(value, original)
        self.assertEqual(canonical['review']['tone_assessment'], assessment)
        self.assertNotIn('tone_assessment', canonical)
        self.assertTrue(any(row['canonical_path'] == 'review.tone_assessment' for row in warnings))
        self.assertEqual(normalize_submission(canonical), (canonical, []))
        self.submit(value)
        self.assertEqual(self.session.state['workflow']['phase'], 'tone')
        self.assertIsNone(self.session.state['workflow'].get('pre_tone_wb'))
        self.assertEqual(list(self.session.state['versions']), ['v0'])
        self.assertEqual(self.session.state['workflow']['checkpoints']['G1']['tone_assessment']['items'], [])
        self.assertFalse(any(call[0] in ('apply', 'render') for call in self.session.native.calls))

    def test_tone_pre_wb_rejections_correct_once_and_actual_operation_identity(self):
        value = self.coarse(add=True)
        value.pop('selected_group_id')
        self.assert_unexecuted(value, 'group_selection_required')
        self.assertIsNone(self.session.state['workflow'].get('pre_tone_wb'))
        value = self.coarse()
        value['review'].pop('tone_basis_impact')
        self.assert_unexecuted(value, 'pre_tone_wb_impact')
        value = self.coarse()
        value['review']['wb_assessment']['wb_task_status'] = 'not_needed'
        self.assert_unexecuted(value, 'pre_tone_wb_assessment')
        before = copy.deepcopy(self.session.state['versions']['v0']['settings'])
        before['wb']['mode'] = 'asShot'
        self.session.state['versions']['v0']['settings'] = before
        self.session.backend().settings_value = copy.deepcopy(before)
        self.session.save()
        self.submit(self.coarse({'wb.tint': 4}))
        self.assertNotIn('stage_turn_result', self.session.snapshot())
        state = self.session.state
        once = state['workflow']['pre_tone_wb']
        self.assertEqual((state['workflow']['phase'], state['current'], state['stage_phase']), ('G1', 'v1', 'joint'))
        actual = state['versions']['v1']
        self.assertEqual(actual['parent'], 'v0')
        self.assertEqual(once['status'], 'succeeded')
        self.assertEqual((once['operation_id'], once['expectation_id']), (actual['operation_id'], actual['expectation_id']))
        self.assertEqual(actual['settings']['wb'], {**before['wb'], 'mode': 'custom', 'tint': 4})
        self.assertEqual({k: v for k, v in actual['settings'].items() if k != 'wb'},
                         {k: v for k, v in before.items() if k != 'wb'})
        self.assertEqual(state['observations'][-1]['comparison_mode'], 'effect')
        self.assertEqual(state['observations'][-1]['phase'], 'G1')
        operation = next(row for row in state['proposal_bank']['issues']['coarse']['operations']
                         if row['operation_id'] == actual['operation_id'])
        self.assertEqual(operation['action_context']['name'], 'pre_tone_wb')
        self.assertTrue(all(row['status'] == 'complete' for row in self.session.native.trace))
        self.assertTrue(fixture_stats(self.session, self.fixture)['changed_version_pixels'][0])
        self.submit(self.submission('photo_observe', {'version': 'v1', 'comparison_mode': 'target',
            'question': 'Fresh target after accepted actual effect.'}))
        self.assert_unexecuted(self.coarse(), 'pre_tone_wb_once')
        self.assertEqual(sum(call[0] == 'apply' for call in self.session.native.calls), 1)

    def test_tone_pre_wb_effect_review_then_fresh_target_and_current_assessment(self):
        self.submit(self.coarse(add=True))
        value = self.submission('advance', {'reason': 'Premature direct advance must be typed.'})
        self.assert_unexecuted(value, 'tone_assessment_required')
        self.assertNotIn('effect_review_observation_id', self.session.state['workflow']['pre_tone_wb'])
        self.assertEqual(self.session.state.get('effect_reviews', []), [])
        effect_id = self.session.state['observations'][-1]['id']
        self.submit(self.submission('photo_history', {}))
        self.assertEqual(self.session.state['workflow']['pre_tone_wb']['effect_review_observation_id'], effect_id)
        self.assertEqual(len(self.session.state['effect_reviews']), 1)
        self.assert_unexecuted(self.submission('advance', {'reason': 'Effect is not a fresh target.'}), 'tone_assessment_required')
        self.submit(self.submission('photo_observe', {'version': 'v1', 'comparison_mode': 'target',
            'question': 'Now inspect fresh G1 full author/current.'}))
        target_id = self.session.state['observations'][-1]['id']
        self.assertNotEqual(target_id, effect_id)
        value = self.submission('advance', {'reason': 'Old v0 attribution cannot select changed v1 tone basis.'})
        value['review'].pop('tone_assessment')
        self.assert_unexecuted(value, 'tone_assessment_required')
        self.advance()
        self.assertEqual(self.session.state['workflow']['phase'], 'tone')
        checkpoint = self.session.state['workflow']['checkpoints']['G1']
        self.assertEqual(checkpoint['tone_assessment']['settings_version'], 'v1')
        self.assertEqual(checkpoint['tone_assessment']['observation_id'], target_id)
        self.assertEqual(checkpoint['full_observation_id'], target_id)

    def test_tone_pre_wb_reload_parent_selection_never_reopens_once(self):
        self.submit(self.coarse(add=True))
        self.submit(self.submission('photo_history', {}))
        once = copy.deepcopy(self.session.state['workflow']['pre_tone_wb'])
        self.reload()
        self.assertEqual(self.session.state['workflow']['pre_tone_wb'], once)
        self.submit(self.submission('photo_observe', {'version': 'v0', 'comparison_mode': 'target',
            'question': 'Explicitly choose original parent after inspecting actual coarse effect.'}))
        self.assertFalse(self.session.project_snapshot()['context']['work_packet']['pre_tone_wb']['available'])
        value = self.coarse()
        value['action']['arguments']['base_version'] = 'v0'
        self.assert_unexecuted(value, 'pre_tone_wb_once')
        self.advance(selected_version='v0')
        self.assertEqual(self.session.state['current'], 'v0')
        self.assertEqual(self.session.state['workflow']['accepted_versions']['tone'], None)
        self.assertEqual(self.session.state['workflow']['checkpoints']['G1']['version'], 'v0')
        self.reload()
        self.assertEqual(self.session.state['workflow']['pre_tone_wb'], once)
        self.assertIn('v1', self.session.state['versions'])

    def test_tone_pre_wb_unknown_after_fence_and_inflight_cannot_claim_noeffect_or_replay(self):
        from photo_stage_rejections import ActionRejected
        value = self.coarse(add=True)
        backend = self.session.backend()
        calls = []
        def unknown(base, arguments):
            calls.append(copy.deepcopy(arguments))
            self.session.execution_started('synthetic.unknown_pre_tone')
            self.session.save()
            raise ActionRejected('injected_after_fence', 'Unknown native response must remain fatal.')
        with patch.object(backend, 'apply', side_effect=unknown):
            with self.assertRaisesRegex(RuntimeError, 'crossed execution/state'):
                self.submit(value)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.session.state['workflow']['pre_tone_wb']['status'], 'in_flight')
        self.assertIsNotNone(self.session.state['pending'])
        self.assertNotIn('stage_turn_result', self.session.state)
        self.assertEqual(list(self.session.state['versions']), ['v0'])
        with self.assertRaises(lab.SafetyStop):
            self.submit(self.coarse())
        with self.assertRaises(lab.LocalDispatchDenied):
            stage.StageSession(self.fixture.round, validation.jobs()[0])
        self.session.state['pending'] = None
        self.session.state['status'] = 'working'
        self.session.save()
        self.reload()
        with self.assertRaises(lab.SafetyStop):
            self.submit(self.coarse())
        self.assertEqual(self.session.state['workflow']['pre_tone_wb']['status'], 'in_flight')
        self.assertNotIn('stage_turn_result', self.session.state)
        self.assertEqual(len(calls), 1)

    def test_tone_chroma_color_protection_carries_into_gray_and_becomes_stale(self):
        value = self.submission('advance', {'reason': 'Route a chroma-associated lightness hypothesis to Color.'}, bank=[
            {'op': 'add', 'id': 'tone', 'branch': 'tone', 'diagnosis': 'Independent synthetic global support.',
             'proposed_controls': ['light.exposure']},
            {'op': 'add', 'id': 'blue', 'branch': 'color', 'diagnosis': 'Synthetic blue-associated lightness.',
             'groups': [{'group_id': 'lum', 'proposed_controls': ['mixer.blue.lum']}]}])
        value['review']['tone_assessment'] = {'reason': 'Synthetic attribution hypothesis, not recovered author controls.',
            'items': [{'id': 'blue-protection', 'author_region': 'A1 declared synthetic blue rectangle',
                'current_region': 'C3 independently located synthetic blue rectangle', 'material': 'Synthetic painted block',
                'lighting': 'Declared uniform fixture light', 'attribution': 'chroma',
                'evidence': 'Only the actual colored fixture comparison, not a gray sample.',
                'protection': 'Do not replace the Color-associated lightness task with global exposure.',
                'handoff': 'color', 'issue_id': 'blue', 'group_id': 'blue::lum'}],
            'neutral_anchors': [{'role': 'midtone', 'author_region': 'A2 neutral fixture mark',
                'current_region': 'B3 neutral fixture mark', 'qualification': 'Declared synthetic neutral only.'}]}
        source = copy.deepcopy(self.session.state['observations'][-1])
        self.submit(value)
        context = self.session.project_snapshot()['context']['work_packet']['tone']
        self.assertEqual(context['view_mode'], 'grayscale')
        self.assertFalse(context['tone_sees_color'])
        self.assertTrue(context['latest_assessment']['carried_color_basis'])
        self.assertTrue(context['latest_assessment']['valid_for_current_version'])
        protection = context['protected_regions'][0]
        self.assertEqual((protection['attribution'], protection['handoff'], protection['group_id']),
                         ('chroma', 'color', 'blue::lum'))
        group = self.session.state['proposal_bank']['issues']['blue']['task_groups']['blue::lum']
        self.assertEqual(group['branch'], 'color')
        self.assertEqual(group['tone_reference_contract'][-1]['observation_id'], source['id'])
        self.assertEqual([row['control_id'] for row in group['proposed_controls']], ['mixer.blue.lum'])
        self.edit(.4)
        context = self.session.project_snapshot()['context']['work_packet']['tone']
        latest = context['latest_assessment']
        self.assertTrue(latest['stale'])
        self.assertTrue(latest['carried_color_basis'])
        self.assertTrue(latest['on_selected_ancestry'])
        self.assertFalse(latest['valid_for_current_version'])
        self.assertFalse(latest['reusable_target_assessment'])
        self.assertEqual(context['protected_regions'][0]['protection'], protection['protection'])
        value = self.submission('photo_history', {})
        value['review']['tone_assessment'] = {'items': [], 'neutral_anchors': [], 'reason': 'Gray is not fresh color evidence.'}
        self.assert_unexecuted(value, 'tone_assessment_invalid')
        self.assertEqual(len(self.session.state['tone_assessments']), 1)

    def test_tone_boundary_missing_typed_then_actual_mask_same_issue_joint_handoff(self):
        plan = {'action': 'create', 'kind': 'linear', 'geometry': {'start': [.5, .1], 'end': [.5, .7]},
                'values': {'exposure': .4}}
        self.advance(bank=[{'op': 'add', 'id': 'edge', 'branch': 'tone', 'diagnosis': 'Synthetic broad local Tone.',
            'proposed_controls': {'mask_plan': plan}}])
        self.submit(self.operation({'mask_plan': plan}, issue='edge', module='masks'))
        actual = self.session.state['versions']['v1']
        ident = actual['settings']['masks'][0]['id']
        operation_id = actual['operation_id']
        debt = self.session.state['workflow']['review_debts'][-1]
        self.assertEqual(debt['tone_boundary']['mask_ids'], [ident])
        self.assertEqual(debt['due_phase'], 'G2')
        value = self.submission('photo_history', {})
        value['review'].pop('tone_boundary_review')
        self.assert_unexecuted(value, 'tone_boundary_review_required')
        value = self.submission('photo_history', {})
        value['review']['tone_boundary_review']['operation_id'] = 'not-the-actual-operation'
        self.assert_unexecuted(value, 'tone_boundary_source')
        value = self.submission('photo_history', {})
        value['review']['tone_boundary_review']['outcome'] = 'affected'
        self.assert_unexecuted(value, 'tone_boundary_handoff_reason')
        repair = {'action': 'adjust', 'id': ident, 'values': {'exposure': .2}}
        value = self.submission('photo_history', {}, bank=[{'op': 'revise', 'id': 'edge', 'groups': [{
            'group_id': 'joint-boundary', 'branch': 'joint', 'proposed_controls': {'mask_plan': repair},
            'reason': 'Same issue, actual existing mask; a small adjustment, not invented geometry.'}]}])
        value['review']['tone_boundary_review'] = {'operation_id': operation_id, 'outcome': 'affected',
            'evidence': 'Synthetic upper-region effect reaches the declared protected neighborhood.',
            'joint_handoff_reason': 'Recheck in color before small same-mask Joint repair; do not contour-chase.',
            'joint_group_id': 'edge::joint-boundary'}
        self.submit(value)
        self.assertNotIn('stage_turn_result', self.session.snapshot())
        review = self.session.state['tone_boundary_reviews'][-1]
        self.assertEqual((review['operation_id'], review['issue_id'], review['joint_group_id']),
                         (operation_id, 'edge', 'edge::joint-boundary'))
        self.assertEqual(len(self.session.state['workflow']['review_debts']), 1)
        self.advance(bank=[self.keep('edge')])
        self.advance(bank=[self.keep('edge')])
        self.advance()
        self.advance(bank=[self.keep('edge')])
        self.assertEqual(self.session.state['workflow']['phase'], 'joint')
        value = self.operation({'mask_plan': repair}, issue='edge', group='edge::joint-boundary', module='masks')
        self.submit(value)
        self.assertNotIn('stage_turn_result', self.session.snapshot())
        masks = self.session.state['versions']['v2']['settings']['masks']
        self.assertEqual(len(masks), 1)
        self.assertEqual(masks[0]['id'], ident)
        self.assertEqual(masks[0]['components'], actual['settings']['masks'][0]['components'])
        self.assertEqual(masks[0]['adjust']['exposure'], .2)
        self.assertEqual(sum(command['id'] == 'mask.add' for tool, *commands in self.session.native.calls
                             if tool == 'command_run' for command in commands), 1)

    def test_tone_color_blue_luminance_and_wb_share_one_actual_g3_debt(self):
        self.enter_wb_color(['wb.tint', 'mixer.blue.lum'])
        self.submit(self.submission('photo_history', {}, bank=[{'op': 'revise', 'id': 'wb', 'groups': [{
            'group_id': 'coupled', 'proposed_controls': ['wb.tint', 'mixer.blue.lum'],
            'reason': 'One explicit synthetic WB and blue-lightness coupling hypothesis.'}]}]))
        previous = len(self.session.state['workflow']['review_debts'])
        value = self.operation({'values': {'wb.tint': 4, 'mixer.blue.lum': 12}}, group='wb::coupled')
        value['selection_reason'] = 'Explicit shared WB/lightness hypothesis; inspect both actual consequences.'
        value['review']['module_decisions'].append({'module': 'mixer_point', 'choice': 'use',
            'reason': 'Explicit actual blue luminance edit, not inferred author HSL use.'})
        self.submit(value)
        self.assertNotIn('stage_turn_result', self.session.snapshot())
        actual = self.session.state['versions']['v1']
        self.assertEqual(actual['settings']['wb']['tint'], 4)
        self.assertEqual(actual['settings']['mixer']['blue']['lum'], 12)
        debts = self.session.state['workflow']['review_debts']
        self.assertEqual(len(debts), previous+1)
        matches = [row for row in debts if row['operation_id'] == actual['operation_id']]
        self.assertEqual(len(matches), 1)
        debt = matches[0]
        self.assertEqual((debt['id'], debt['due_phase'], debt['version']), ('debt:'+actual['operation_id'], 'G3', 'v1'))
        self.assertEqual(debt['hsl_luminance_changes'], [{'control_id': 'mixer.blue.lum', 'before': 0,
            'after': 12, 'delta': 12, 'claim_type': 'actual_settings_change_not_visual_judgment'}])
        self.assertIn('tint_change', debt['wb_coupling']['triggers'])
        self.assertEqual(debt['status'], 'pending')
        self.assertTrue(fixture_stats(self.session, self.fixture)['changed_version_pixels'][0])
        self.advance(bank=[self.keep('wb')], debts=False)
        self.assertEqual(self.session.state['workflow']['phase'], 'G3')
        self.assertEqual(self.session.state['workflow']['review_debts'][-1]['hsl_luminance_changes'], debt['hsl_luminance_changes'])
        self.assertEqual(self.session.state['workflow']['review_debts'][-1]['wb_coupling'], debt['wb_coupling'])

    def test_g2_missing_wb_is_typed_rejection_then_explicit_not_available_advances(self):
        self.advance(bank=[])
        self.advance()
        value = self.submission('advance', {'reason': 'No implicit WB judgment.'})
        del value['review']['wb_assessment']
        self.assert_unexecuted(value, 'wb_assessment_required')
        self.assertEqual(self.session.state.get('wb_assessments', []), [])
        self.advance()
        self.assertEqual(self.session.state['workflow']['phase'], 'color')
        stored = self.session.state['workflow']['checkpoints']['G2']['wb_assessment']
        self.assertEqual((stored['reference_status'], stored['reference_pairs'], stored['wb_task_status']),
                         ('not_available', [], 'not_needed'))
        self.assertNotIn('stage_turn_result', self.session.snapshot())

    def test_g2_available_independent_cells_checkpoint_bank_and_root_relocation(self):
        from photo_stage_submission import normalize_submission
        self.advance(bank=[])
        self.advance()
        value = self.submission('advance', {'reason': 'Preserve explicit target WB reference contract.'}, bank=[{
            'op': 'add', 'id': 'wb', 'branch': 'color', 'diagnosis': 'Synthetic WB task.',
            'proposed_controls': ['wb.temp', 'wb.tint']}])
        assessment = self.wb_assessment(True, issue='wb')
        value['review'].pop('wb_assessment')
        value['wb_assessment'] = copy.deepcopy(assessment)
        original = copy.deepcopy(value)
        canonical, warnings = normalize_submission(value)
        self.assertEqual(value, original)
        self.assertEqual(canonical['review']['wb_assessment'], assessment)
        self.assertNotIn('wb_assessment', canonical)
        self.assertTrue(any(w['canonical_path'] == 'review.wb_assessment' for w in warnings))
        self.assertEqual(normalize_submission(canonical), (canonical, []))
        source = copy.deepcopy(self.session.state['observations'][-1])
        self.submit(value)
        bound = self.session.state['wb_assessments'][-1]
        self.assertEqual((bound['observation_id'], bound['settings_version'], bound['phase']),
                         (source['id'], 'v0', 'G2'))
        self.assertEqual(bound['reference_pairs'], assessment['reference_pairs'])
        self.assertEqual(self.session.state['workflow']['checkpoints']['G2']['wb_assessment'], bound)
        issue = self.session.state['proposal_bank']['issues']['wb']
        group = next(iter(issue['task_groups'].values()))
        for owner in (issue, group):
            contract = owner['wb_reference_contract'][-1]
            self.assertEqual(contract['reference_pairs'], assessment['reference_pairs'])
            self.assertEqual(contract['target_change']['target_intent'], 'intentional_cast')
            self.assertEqual(contract['protected']['atmosphere'], assessment['atmosphere'])
            self.assertEqual(contract['observation_id'], source['id'])
        stored = lab.read(self.session.file)
        self.assertEqual(stored['wb_assessments'][-1], bound)
        self.assertEqual(stored['proposal_bank']['issues']['wb'], issue)
        record = self.session.state['workflow_submissions'][-1]
        self.assertEqual(record['submission'], original)
        self.assertEqual(record['normalized_submission'], canonical)
        context = self.session.project_snapshot()['context']['work_packet']['white_balance']
        self.assertEqual(context['actual_current'], {'version': 'v0', **self.fixture.base['wb']})
        self.assertEqual(context['latest_assessment']['record'], bound)
        self.assertTrue(context['latest_assessment']['valid_for_current_version'])
        context['latest_assessment']['record']['reference_pairs'].clear()
        self.assertEqual(self.session.state['wb_assessments'][-1], bound)

    def test_wb_actual_deltas_enhance_same_generic_debt_and_pending_does_not_gate(self):
        group = self.enter_wb_color(['wb.temp', 'wb.tint'])
        self.submit(self.submission('photo_history', {}, bank=[{'op': 'revise', 'id': 'wb',
            'groups': [{'group_id': 'coupling', 'proposed_controls': ['wb.temp', 'wb.tint', 'color.saturation'],
                'reason': 'One explicit shared WB-zero/color-response coupling hypothesis.'}]}]))
        group = 'wb::coupling'
        cases = [({'wb.temp': 5483}, None, 400),
                 ({'wb.temp': 5884}, 'kelvin_temperature_delta_gt_400', 401),
                 ({'wb.tint': 3}, 'tint_change', 0),
                 ({'wb.temp': 5884, 'color.saturation': 2}, None, 0)]
        for values, trigger, delta in cases:
            with self.subTest(values=values):
                previous = len(self.session.state['workflow']['review_debts'])
                value = self.operation({'values': values}, group=group)
                value['selection_reason'] = 'One explicitly grouped synthetic coupling hypothesis.'
                self.submit(value)
                self.assertNotIn('stage_turn_result', self.session.snapshot())
                version = self.session.state['current']
                operation_id = self.session.state['versions'][version]['operation_id']
                debts = self.session.state['workflow']['review_debts']
                self.assertEqual(len(debts), previous+1)
                matches = [d for d in debts if d['operation_id'] == operation_id]
                self.assertEqual(len(matches), 1, 'WB specializes the same debt, never appends a second one')
                debt = matches[0]
                self.assertEqual((debt['id'], debt['due_phase'], debt['version']), ('debt:'+operation_id, 'G3', version))
                self.assertEqual(debt['status'], 'pending')
                if trigger:
                    self.assertEqual(debt['kind'], 'wb_tone_coupling')
                    self.assertIn(trigger, debt['wb_coupling']['triggers'])
                    self.assertEqual(debt['wb_coupling']['global']['delta']['temp'], delta)
                else:
                    self.assertNotIn('wb_coupling', debt)
                    self.assertNotEqual(debt.get('kind'), 'wb_tone_coupling')
        debts = copy.deepcopy(self.session.state['workflow']['review_debts'])
        invalid = self.operation({'values': {'wb.temp': 6500}}, issue='unknown', group=group)
        invalid['bank_delta'] = []
        self.assert_unexecuted(invalid, 'inactive_issue')
        self.assertEqual(self.session.state['workflow']['review_debts'], debts)
        self.advance(bank=[self.keep('wb')], debts=False)
        self.assertEqual(self.session.state['workflow']['phase'], 'G3')
        self.advance(debts=False)
        self.assertEqual(self.session.state['workflow']['phase'], 'joint')
        self.assertTrue(all(d['status'] == 'pending' for d in self.session.state['workflow']['review_debts']))
        self.assertTrue(all(fixture_stats(self.session, self.fixture)['changed_version_pixels']))

    def test_wb_effect_support_cannot_resolve_without_target_assessment_then_revise_reopens(self):
        group = self.enter_wb_color()
        self.submit(self.operation({'values': {'wb.temp': 5283}}, group=group))
        value = self.submission('photo_history', {})
        value['review']['effect_review']['outcome'] = 'supported'
        value['review']['group_reviews'] = [{'issue_id': 'wb', 'group_id': group, 'outcome': 'resolved',
            'reason': 'Effect support alone must not claim author matching.'}]
        self.assert_unexecuted(value, 'wb_target_evidence_required')
        self.assertEqual(self.session.state['proposal_bank']['issues']['wb']['task_groups'][group]['status'], 'awaiting_effect')
        self.submit(self.submission('photo_observe', {'version': 'v1', 'comparison_mode': 'target',
            'question': 'Obtain actual current/author synthetic evidence, not parent effect.'}))
        value = self.submission('photo_history', {})
        value['review']['wb_assessment'] = self.wb_assessment(True, issue='wb', group=group)
        value['review']['group_reviews'] = [{'issue_id': 'wb', 'group_id': group, 'outcome': 'resolved',
            'reason': 'Explicit current-version target judgment, not an effect claim.'}]
        self.submit(value)
        stored = self.session.state['proposal_bank']['issues']['wb']['task_groups'][group]
        self.assertEqual(stored['status'], 'resolved')
        history = copy.deepcopy(stored['records'])
        packet = self.session.project_snapshot()['context']['work_packet']
        self.assertFalse(any(g['id'] == group for key in ('current', 'ready_candidates', 'pending_review') for g in packet[key]))
        self.submit(self.submission('photo_observe', {'version': 'v1', 'comparison_mode': 'target',
            'question': 'Fresh synthetic evidence contradicts prior closure.', 'candidate_roi': [0, 0, 1, .5]}))
        value = self.submission('photo_history', {})
        value['review']['wb_assessment'] = self.wb_assessment(True, issue='wb', group=group)
        value['review']['wb_assessment']['reason'] = 'Fresh regional evidence reveals a remaining synthetic residual.'
        value['review']['group_reviews'] = [{'issue_id': 'wb', 'group_id': group, 'outcome': 'revise',
            'reason': 'Reopen this same group from new target evidence.'}]
        self.submit(value)
        reopened = self.session.state['proposal_bank']['issues']['wb']['task_groups'][group]
        self.assertNotEqual(reopened['status'], 'resolved')
        self.assertEqual(reopened['records'][:len(history)], history)
        self.assertEqual(len(reopened['operations']), 1)
        packet = self.session.project_snapshot()['context']['work_packet']
        self.assertTrue(any(g['id'] == group for key in ('current', 'ready_candidates') for g in packet[key]))

    def test_tone_mask_color_reuse_preserves_six_tone_fields_and_creation_needs_distinct_purpose(self):
        tone = {'exposure': .4, 'contrast': 8, 'highlights': -12, 'shadows': 9, 'whites': -3, 'blacks': 2}
        plan = {'action': 'create', 'kind': 'linear', 'geometry': {'start': [.5, .1], 'end': [.5, .7]}, 'values': tone}
        self.advance(bank=[{'op': 'add', 'id': 'local-tone', 'branch': 'tone', 'diagnosis': 'Synthetic upper-region tone.',
            'proposed_controls': {'mask_plan': copy.deepcopy(plan)}}])
        self.submit(self.operation({'mask_plan': plan}, issue='local-tone', module='masks'))
        masks = self.session.state['versions']['v1']['settings']['masks']
        self.assertEqual(len(masks), 1)
        ident = masks[0]['id']
        self.assertEqual({k: masks[0]['adjust'][k] for k in tone}, tone)
        self.advance(bank=[self.keep('local-tone')])
        local = {'action': 'adjust', 'id': ident, 'values': {'temp': -8, 'tint': 3}}
        self.advance(bank=[{'op': 'add', 'id': 'local-wb', 'branch': 'color', 'diagnosis': 'Same synthetic regional lighting.',
            'proposed_controls': {'mask_plan': local}}])
        context = self.session.project_snapshot()['context']['work_packet']['white_balance']
        self.assertEqual(context['simple_masks'][0]['id'], ident)
        self.assertEqual(context['simple_masks'][0]['adjust'], masks[0]['adjust'])
        self.assertEqual(context['simple_masks'][0]['shape'], masks[0]['components'])
        self.assertTrue(context['simple_masks'][0]['visible'])
        self.assertFalse(context['simple_masks'][0]['invert'])
        self.assertTrue(context['simple_masks'][0]['public_purpose'])
        adds = lambda: [c for tool, *rest in self.session.native.calls if tool == 'command_run'
                        for c in rest if c['id'] == 'mask.add']
        for values, submitted_review in [({'temp': -8, 'tint': 3}, None),
                ({'temp': -6, 'tint': 2}, {'region': 'Synthetic upper region', 'decision': 'create',
                    'existing_mask_ids': [], 'reason': 'Mismatched metadata; the explicit action still adjusts the actual ID.'})]:
            with self.subTest(submitted_review=submitted_review):
                value = self.operation({'mask_plan': {**local, 'values': values}}, issue='local-wb', module='masks')
                if submitted_review is not None:
                    value['review']['wb_local_review'] = submitted_review
                source = copy.deepcopy(self.session.state['observations'][-1])
                original = copy.deepcopy(value)
                self.submit(value)
                self.assertNotIn('stage_turn_result', self.session.snapshot())
                current = self.session.state['versions'][self.session.state['current']]['settings']['masks']
                self.assertEqual(len(current), 1)
                self.assertEqual(current[0], {**masks[0], 'adjust': {**masks[0]['adjust'], **values}})
                self.assertEqual(len(adds()), 1)
                self.assertEqual(next(c for tool, *rest in reversed(self.session.native.calls) if tool == 'command_run'
                    for c in rest if c['id'] == 'mask.adjust'), {'id': 'mask.adjust', 'params': {'id': ident, 'values': values}})
                usage = self.session.state['wb_mask_usage'][-1]
                self.assertEqual((usage['decision'], usage['mask_id'], usage['actual_mask_id']), ('reuse', ident, ident))
                self.assertEqual((usage['region'], usage['reason']),
                    (value['review']['effect_expectation']['target'], value['action']['arguments']['rationale']))
                self.assertEqual((usage['settings_version'], usage['observation_id']), (source['version'], source['id']))
                self.assertEqual(usage['submitted_reuse_review'], submitted_review)
                self.assertEqual(usage['claim_type'], 'explicit_model_adjust_id_from_actual_readback_not_inferred_region_equivalence')
                self.assertTrue(usage['review_warning'])
                self.assertEqual(self.session.state['workflow_submissions'][-1]['submission'], original)
                self.assertEqual(value, original)
                self.assertEqual(self.session.state['workflow']['review_debts'][-1]['kind'], 'wb_tone_coupling')
        invalid = self.operation({'mask_plan': {**local, 'id': ident + 1}}, issue='local-wb', module='masks')
        self.assert_unexecuted(invalid, 'wb_mask_reuse_review')
        invalid = self.operation({'mask_plan': {**local, 'values': {'temp': -4, 'exposure': .2}}},
                                 issue='local-wb', module='masks')
        self.assert_unexecuted(invalid, 'operation_outside_phase')
        new = {'action': 'create', 'kind': 'radial', 'geometry': {'center': [.6, .75], 'rx': .2, 'ry': .15,
               'angle': 0, 'feather': 60}, 'values': {'temp': 6, 'tint': -4}}
        value = self.operation({'mask_plan': new}, issue='local-wb', module='masks')
        self.assert_unexecuted(value, 'wb_mask_reuse_review')
        value['review']['wb_local_review'] = {'region': 'Synthetic lower subject', 'decision': 'create',
            'existing_mask_ids': [], 'reason': 'Distinct lower subject lighting, not the upper tonal region.'}
        self.assert_unexecuted(value, 'wb_mask_reuse_review')
        value['review']['wb_local_review']['existing_mask_ids'] = [ident]
        self.submit(value)
        current = self.session.state['versions']['v4']['settings']['masks']
        self.assertEqual(len(current), 2)
        self.assertEqual(current[0], self.session.state['versions']['v3']['settings']['masks'][0])
        self.assertNotEqual(current[1]['id'], ident)
        self.assertEqual(len(adds()), 2)
        usage = self.session.state['wb_mask_usage'][-1]
        self.assertEqual((usage['decision'], usage['existing_mask_ids'], usage['actual_mask_id']), ('create', [ident], current[1]['id']))
        self.assertEqual(usage['considered_masks'][0], current[0])
        self.assertTrue(all(fixture_stats(self.session, self.fixture)['changed_version_pixels']))
        self.assertTrue(all(row['status'] == 'complete' for row in self.session.native.trace))

    def test_wb_picker_missing_grounding_is_correctable_and_fake_result_is_actual_readback(self):
        group = self.enter_wb_color()
        value = self.operation({'commands': [{'id': 'develop.wbPick', 'params': {'x': .23, 'y': .71}}]}, group=group)
        self.assert_unexecuted(value, 'wb_pick_grounding_required')
        value['review']['wb_pick_review'] = {'neutrality_basis': 'Declared synthetic neutral, not physical material proof.',
            'source_region': 'Synthetic source rectangle', 'coordinate_space': 'full_oriented_source',
            'full_image_point': {'x': .23, 'y': .71}, 'roi_mapping_reason': 'Full source coordinates, not author/crop coordinates.'}
        self.submit(value)
        actual = self.session.state['versions']['v1']['settings']
        self.assertEqual(actual['wb'], {'mode': 'custom', 'scale': 'kelvin', 'temp': 5732, 'tint': 7})
        self.assertEqual(actual['masks'], [])
        trace = next(row for row in self.session.native.trace if row.get('command', {}).get('id') == 'develop.wbPick')
        self.assertEqual(trace['result'], {'temp': 5732, 'tint': 7})
        self.assertEqual(trace['settings_readback'], actual)
        self.assertEqual(trace['status'], 'complete')
        debt = self.session.state['workflow']['review_debts'][-1]
        self.assertEqual(debt['kind'], 'wb_tone_coupling')
        self.assertIn('picker_actual_wb_change', debt['wb_coupling']['triggers'])
        self.assertTrue(fixture_stats(self.session, self.fixture)['changed_version_pixels'][0])

    def test_invalid_module_coverage_retains_legal_bank_but_not_effect_acceptance(self):
        self.enter_tone()
        self.edit(.5)
        before = {key: copy.deepcopy(self.session.state[key])
                  for key in ('workflow', 'proposal_bank', 'stage_phase', 'versions', 'current')}
        self.assertTrue(before['proposal_bank']['issues']['tone']['needs_reassessment'])
        invalid = self.submission('photo_edit', {'base_version': 'v1', 'values': {'light.exposure': .75},
            'rationale': 'Preserve the exact rejected public proposal.', 'predicted_effect': 'Synthetic child brightness.'},
            bank=[self.keep('tone')], issue='missing-tone', module='wb')
        calls = copy.deepcopy(self.session.native.calls)
        effects = copy.deepcopy(self.session.state.get('effect_reviews', []))
        rejected = self.submit(invalid)['stage_turn_result']
        self.assertEqual(rejected['kind'], 'rejected_before_execution')
        self.assertEqual(rejected['code'], 'inactive_issue')
        self.assertTrue(rejected['no_effect']['passed'])
        self.assertEqual(self.session.native.calls, calls)
        self.assertIsNone(self.session.state.get('pending'))
        self.assertGreater(self.session.state['proposal_bank']['revision'], before['proposal_bank']['revision'])
        self.assertEqual(self.session.state.get('effect_reviews', []), effects)
        for key, expected in before.items():
            if key == 'proposal_bank':
                continue
            self.assertEqual(self.session.state[key], expected, key)
            self.assertEqual(lab.read(self.session.file)[key], expected, 'persisted '+key)
        self.edit(.75)
        self.assertEqual(self.session.state['current'], 'v2')
        self.assertNotIn('stage_turn_result', self.session.snapshot())
        self.assertEqual(invalid['review']['module_decisions'][0]['module'], 'wb')
        self.assertEqual(invalid['bank_delta'], [self.keep('tone')])
        # Original successful public records remain; rollback is not history erasure.
        self.assertTrue(self.session.state['module_public_records'])

    def test_accept_earlier_tone_version_excludes_abandoned_child_debt(self):
        self.enter_tone()
        self.edit(.5)
        self.edit(.75)
        self.assertEqual(self.session.state['versions']['v2']['parent'], 'v1')
        self.submit(self.submission('photo_observe', {'version': 'v1', 'comparison_mode': 'target',
            'question': 'Explicitly compare earlier synthetic v1 before selecting the best version.'}))
        self.assertEqual(self.session.state['observations'][-1]['version'], 'v1')
        self.advance(bank=[self.keep('tone')], selected_version='v1')
        self.assertEqual(self.session.state['current'], 'v1')
        self.assertEqual(self.session.state['workflow']['phase'], 'G2')
        self.assertEqual(self.session.state['workflow']['accepted_versions']['tone'], 'v1')
        self.assertEqual(self.session.state['observations'][-1]['version'], 'v1')
        debts = {debt['version']: debt for debt in self.session.state['workflow']['review_debts']}
        self.assertEqual(debts['v1']['status'], 'pending')
        self.assertEqual(debts['v2']['status'], 'not_applicable_selected_branch')
        self.assertEqual(debts['v2']['selected_version'], 'v1')
        self.assertEqual(debts['v2']['selection_observation_id'],
                         self.session.state['workflow']['events'][-1]['observation_id'])
        self.advance(bank=[self.keep('color')], color_reassessment=[{
            'issue_id': 'color', 'decision': 'keep', 'reason': 'Reassessed actual accepted v1 in full color.'}])
        self.assertEqual(self.session.state['workflow']['phase'], 'color')
        self.assertEqual(self.session.state['current'], 'v1')
        self.assertEqual(lab.read(self.session.file)['current'], 'v1')
        self.assertIn('v2', self.session.state['versions'])
        self.assertEqual(next(d for d in self.session.state['workflow']['review_debts']
                              if d['version'] == 'v2')['status'], 'not_applicable_selected_branch')
        self.assertTrue(fixture_stats(self.session, self.fixture)['sources_unchanged'])



    def test_effect_exact_operation_alias_binds_observed_child_preserves_raw_and_native_values(self):
        self.enter_tone()
        self.edit(.3)
        actual = self.session.state['versions']['v1']
        self.assertNotEqual(actual['operation_id'], actual['expectation_id'])
        self.assertEqual(self.session.state.get('effect_reviews', []), [])
        value = self.operation({'values': {'light.exposure': .5}}, issue='tone', module='light')
        wrong = copy.deepcopy(value)
        wrong['review']['effect_review']['expectation_id'] = 'unknown-operation-not-an-alias'
        self.assert_unexecuted(wrong, 'effect_review_version')
        value['review']['effect_review'].update(expectation_id=actual['operation_id'], outcome='supported')
        original = copy.deepcopy(value)
        self.submit(value)
        self.assertNotIn('stage_turn_result', self.session.snapshot())
        self.assertEqual(self.session.state['current'], 'v2')
        record = self.session.state['workflow_submissions'][-1]
        self.assertEqual(record['submission'], original)
        self.assertEqual(value, original)
        canonical = copy.deepcopy(original)
        canonical['review']['effect_review']['expectation_id'] = actual['expectation_id']
        self.assertEqual(record['normalized_submission'], canonical)
        warning = next(w for w in record['normalization_warnings'] if w['code'] == 'exact_operation_expectation_alias')
        self.assertEqual((warning['original'], warning['canonical'], warning['operation_id'], warning['version']),
            (actual['operation_id'], actual['expectation_id'], actual['operation_id'], 'v1'))
        self.assertEqual([(r['expectation_id'], r['version'], r['outcome']) for r in self.session.state['effect_reviews']],
                         [(actual['expectation_id'], 'v1', 'supported')])
        issue = self.session.state['proposal_bank']['issues']['tone']
        self.assertEqual(issue['operations'][0]['outcome'], 'supported')
        self.assertEqual(issue['operations'][0]['model_judgment']['operation_id'], actual['operation_id'])
        self.assertEqual(self.session.state['versions']['v2']['settings']['light']['exposure'], .5)
        applies = [args for tool, *rest in self.session.native.calls if tool == 'apply' for args in rest]
        self.assertEqual(len(applies), 2)
        self.assertEqual(applies[-1]['values'], original['action']['arguments']['values'])

    def test_redundant_effect_review_does_not_overwrite_outcome_or_veto_next_action(self):
        self.enter_tone()
        self.edit(.3)
        actual = copy.deepcopy(self.session.state['versions']['v1'])
        value = self.submission('photo_history', {})
        value['review']['effect_review']['outcome'] = 'supported'
        self.submit(value)
        effects = copy.deepcopy(self.session.state['effect_reviews'])
        bank = self.session.state['proposal_bank']
        group_id = next(iter(bank['issues']['tone']['task_groups']))
        operation = copy.deepcopy(bank['issues']['tone']['operations'][0])
        group_operation = copy.deepcopy(bank['issues']['tone']['task_groups'][group_id]['operations'][0])
        event = copy.deepcopy(next(r for r in bank['events'] if r.get('kind') == 'operation'
                                  and r['operation_id'] == actual['operation_id']))
        self.assertEqual(operation['outcome'], 'supported')
        for ident, edit in [(actual['expectation_id'], False), ('unknown-stale-expectation', True)]:
            with self.subTest(expectation_id=ident):
                value = (self.operation({'values': {'light.exposure': .6}}, issue='tone', module='light')
                         if edit else self.submission('photo_history', {}))
                value['review']['effect_review'] = {'expectation_id': ident, 'outcome': 'contradicted',
                    'evidence': 'Redundant statement must not rewrite the accepted actual effect judgment.'}
                original = copy.deepcopy(value)
                self.submit(value)
                self.assertNotIn('stage_turn_result', self.session.snapshot())
                self.assertEqual(self.session.state['effect_reviews'], effects)
                self.assertEqual(self.session.state['versions']['v1'], actual)
                bank = self.session.state['proposal_bank']
                self.assertEqual(bank['issues']['tone']['operations'][0], operation)
                self.assertEqual(bank['issues']['tone']['task_groups'][group_id]['operations'][0], group_operation)
                self.assertEqual(next(r for r in bank['events'] if r.get('kind') == 'operation'
                    and r['operation_id'] == actual['operation_id']), event)
                record = self.session.state['workflow_submissions'][-1]
                self.assertEqual(record['submission'], original)
                self.assertEqual(value, original)
                canonical = copy.deepcopy(original)
                canonical['review'].pop('effect_review')
                self.assertEqual(record['normalized_submission'], canonical)
                warning = next(w for w in record['normalization_warnings'] if w['code'] == 'redundant_effect_review_not_adopted')
                self.assertEqual(warning['metadata_extra'], original['review']['effect_review'])
                self.assertEqual((warning['actual_expectation_id'], warning['version']), (actual['expectation_id'], 'v1'))
        self.assertEqual(self.session.state['current'], 'v2')
        self.assertEqual(sum(c[0] == 'apply' for c in self.session.native.calls), 2)
        self.assertEqual(self.session.state['versions']['v2']['settings']['light']['exposure'], .6)

    def test_unknown_bank_reference_returns_feedback_then_edit_continues(self):
        self.enter_tone()
        before = copy.deepcopy(self.session.state['proposal_bank'])
        calls = copy.deepcopy(self.session.native.calls)
        invalid = self.submission('photo_history', {}, bank=[self.keep('not-a-stored-issue')])
        result = self.submit(invalid)['stage_turn_result']
        self.assertEqual(result['code'], 'unknown_bank_issue')
        self.assertTrue(result['no_effect']['passed'])
        self.assertEqual(self.session.state['proposal_bank'], before)
        self.assertEqual(self.session.native.calls, calls)
        self.edit(.2)
        self.assertEqual(self.session.state['current'], 'v1')
        self.assertNotIn('stage_turn_result', self.session.snapshot())

    def test_explicit_next_equals_omitted_and_full_roi_evidence_is_bound(self):
        self.advance(target_phase='tone', bank=[])
        self.assertEqual(self.session.state['workflow']['phase'], 'tone')
        self.advance()
        self.advance(bank=[], target_phase='color', color_reassessment=[])
        self.assertEqual(self.session.state['workflow']['phase'], 'color')
        event = self.session.state['workflow']['checkpoints']['G2']
        self.assertEqual(event['source_observation_id'], event['full_observation_id'])

    def test_explicit_group_review_resolves_without_erasing_proposal_history(self):
        self.enter_tone()
        self.edit(.5)
        group = next(iter(self.session.state['proposal_bank']['issues']['tone']['task_groups'].values()))
        value = self.submission('photo_history', {})
        value['review']['group_reviews'] = [{'issue_id': 'tone', 'group_id': group['id'],
            'outcome': 'resolved', 'reason': 'Model explicitly elects no more work; not a measured improvement fact.'}]
        self.submit(value)
        current = self.session.state['proposal_bank']['issues']['tone']['task_groups'][group['id']]
        self.assertEqual(current['status'], 'resolved')
        packet = self.session.snapshot()['context']['work_packet']
        self.assertFalse(any(g['id'] == group['id'] for g in packet.get('ready_candidates', [])))
        self.assertEqual(current['last_review']['claim_type'], 'model_judgment')
        self.assertTrue(current['records'])
        self.assertEqual(self.session.state['workflow_submissions'][-1]['submission'], value)
        self.assertEqual(self.session.state['public_decisions'][-1]['group_reviews'], value['review']['group_reviews'])

    def test_noeffect_never_survives_fence_pending_child_state_or_ledger_change(self):
        from photo_stage_rejections import ActionRejected
        for mutation in ('fence', 'pending', 'child', 'photography', 'ledger'):
            with self.subTest(mutation=mutation):
                before = copy.deepcopy(self.session.state)
                ledger_path = self.session.round / 'native-render-ledger.json'
                ledger = ledger_path.read_bytes()
                def unknown(*args):
                    if mutation == 'fence':
                        self.session.execution_started('fault-injected-restore')
                        self.session.state.pop('execution_started', None)
                    elif mutation == 'pending':
                        self.session.state['pending'] = {'unknown': True}
                    elif mutation == 'child':
                        self.session.state['versions']['v1'] = {'parent': 'v0'}
                    elif mutation == 'photography':
                        self.session.state['export'] = {'path': 'unknown'}
                    else:
                        ledger_path.write_bytes(ledger + b'\n')
                    self.session.save()
                    raise ActionRejected('injected', 'Known type after unknown effects is still fatal')
                with patch.object(self.session, '_stage_turn', side_effect=unknown):
                    with self.assertRaisesRegex(RuntimeError, 'crossed execution/state'):
                        self.submit(self.submission('photo_history', {}))
                self.assertNotIn('stage_turn_result', self.session.state)
                self.session.state = before
                self.session.save()
                ledger_path.write_bytes(ledger)

    def test_rejection_and_original_submission_are_available_by_history(self):
        self.enter_tone()
        value = self.submission('photo_edit', {'base_version': 'v0', 'values': {'light.exposure': .2},
            'rationale': 'Test public record.', 'predicted_effect': 'Synthetic brightness.'},
            bank=[self.keep('tone')], issue='missing-tone', module='wb')
        rejected = self.submit(value)['stage_turn_result']
        records = self.session._history_records()
        row = next(r for r in records if r['id'] == rejected['rejection_ref'])
        self.assertEqual(row['record']['submission'], value)
        detail = self.session._history_result({'mode': 'detail_get', 'record_id': row['id'], 'char_limit': 100000},
                                              self.session.state['observations'][-1])
        self.assertEqual(json.loads(detail['text'])['submission'], value)
        self.assertTrue(any(r['kind'] == 'lifecycle' and r['record']['kind'] == 'turn_rejected' for r in records))
        bad = copy.deepcopy(value); bad['contract_revision'] = 'stage-contract/v2:stale'
        with self.assertRaises(ValueError):
            self.submit(bad)

    def test_identical_effect_rois_are_one_view_and_mismatch_is_correctable(self):
        self.enter_tone()
        self.edit(.3)
        value = self.submission('photo_observe', {'version': 'v1', 'comparison_mode': 'effect',
            'question': 'Inspect actual parent/current region.', 'reference_roi': [0, 0, 1, .32],
            'candidate_roi': [0, 0, 1, .32], 'side': 512})
        result = self.submit(value)
        self.assertEqual(result['context']['comparison_mode'], 'effect')
        self.assertEqual(self.session.state['observations'][-1]['view_spec']['candidate_roi'], [0, 0, 1, .32])
        bad = copy.deepcopy(value)
        bad['contract_revision'] = self.session.contract_revision()
        bad['action']['arguments']['reference_roi'] = [.2, 0, 1, .32]
        result = self.submit(bad)
        self.assertEqual(result['stage_turn_result']['code'], 'effect_roi_mismatch')
        self.assertEqual(self.session.state['current'], 'v1')
        value['contract_revision'] = self.session.contract_revision()
        self.submit(value)
        self.assertEqual(self.session.state['status'], 'working')

    def test_g4_details_keep_actual_jpeg_and_canonical_cannot_borrow_old_export(self):
        self.reach_g4()
        export = copy.deepcopy(self.session.state['export'])
        self.submit(self.submission('photo_observe', {'question': 'Inspect actual JPEG detail.',
            'reference_roi': [.1, .1, .7, .7], 'candidate_roi': [.1, .1, .7, .7]}))
        current = self.session.state['observations'][-1]
        self.assertEqual((current['artifact'], current['path']), ('export', export['path']))
        self.session.project_snapshot()
        # A canonical current view must never borrow a prior export's identity.
        from photo_phase_scheduler import checkpoint_evidence
        canonical = copy.deepcopy(current)
        canonical.update(artifact='canonical', path='not-the-export.png')
        with self.assertRaisesRegex(ValueError, 'actual selected-version export'):
            checkpoint_evidence(self.session.state, canonical, 'G4')
        self.finish(True)
        self.assertEqual(self.session.state['workflow']['phase'], 'complete')

    def test_joint_without_jpeg_returns_unexecuted_export_instruction(self):
        self.enter_tone()
        self.advance()
        self.advance(bank=[self.keep('color')], color_reassessment=[{
            'issue_id': 'color', 'decision': 'keep', 'reason': 'Current color comparison.'}])
        self.advance()
        self.advance()
        self.assertEqual(self.session.state['workflow']['phase'], 'joint')
        result = self.advance(target_phase='G4')['stage_turn_result']
        self.assertEqual(result['code'], 'actual_export_required')
        self.assertTrue(result['no_effect']['passed'])
        self.assertEqual(self.session.state['workflow']['phase'], 'joint')
        self.assertIsNone(self.session.state.get('export'))

    def test_g4_repair_without_edit_requires_new_export_and_fresh_confirmation(self):
        self.reach_g4()
        previous = copy.deepcopy(self.session.state['export'])
        versions = copy.deepcopy(self.session.state['versions'])
        self.advance(target_phase='joint', reason='Actual JPEG intentionally not confirmed; request joint repair.')
        self.assertIn('confirmation_invalidated_by', self.session.state['export'])
        rejected = self.finish(True)['stage_turn_result']
        self.assertEqual(rejected['code'], 'actual_export_required')
        self.assertEqual(self.session.state['workflow']['phase'], 'joint')
        self.assertEqual(self.session.state['status'], 'working')
        self.assertEqual(self.session.state['export']['path'], previous['path'])
        renders = sum(call[0] == 'render' for call in self.session.native.calls)
        self.finish(False)
        fresh = self.session.state['export']
        self.assertEqual(self.session.state['versions'], versions, 'Re-export must not require a forced edit')
        self.assertEqual(fresh['version'], previous['version'])
        self.assertNotEqual(fresh['path'], previous['path'])
        self.assertTrue(Path(previous['path']).is_file())
        self.assertTrue(Path(fresh['path']).is_file())
        self.assertNotIn('confirmation_invalidated_by', fresh)
        self.assertEqual(sum(call[0] == 'render' for call in self.session.native.calls), renders+1)
        observation = self.session.state['observations'][-1]
        self.assertEqual((observation['phase'], observation['artifact'], observation['path']), ('G4', 'export', fresh['path']))
        self.session.state['tool_result'] = {'kind': 'rejected_before_execution',
                                            'rejection_ref': 'historical-refusal'}
        self.finish(True)
        self.assertEqual(self.session.state['workflow']['phase'], 'complete')
        self.assertEqual(self.session.state['status'], 'complete')
        self.assertNotEqual((self.session.snapshot()['context'].get('tool_result') or {}).get('kind'),
                            'rejected_before_execution')
        self.assertEqual(self.session.state['selected_version'], previous['version'])
        self.assertTrue(fixture_stats(self.session, self.fixture)['sources_unchanged'])


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PhaseWorkerChecks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    write(LATEST/'publication-phase-worker-synthetic-summary.json', {'build': stage.BUILD,
        'publication_derivative': True, 'retained_synthetic_cases': 33, 'original_cases': 35,
        'excluded_field_cases': 2,
        'passed': result.wasSuccessful(), 'tests': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors), 'paid_model_calls': 0,
        'real_native_calls': 0, 'network_calls': 0, 'hash_verification': False, 'photographic_quality_claim': False,
        'source_bytes_mtimes_and_no_real_calls_asserted_per_test': True,
        'raster_response': 'SYNTHETIC mapping only, not backend physical response or photographic evidence'})
    raise SystemExit(0 if result.wasSuccessful() else 1)
