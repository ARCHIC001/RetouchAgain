"""Synthetic-only focused workflow checks; not photographic quality evidence.

Run: trusted-python -I -B -X utf8 src/check_photo_phase_workflow.py
No model, native backend, render, paid transport, hash, source-photo or run IO.
Temporary image fixtures live under experiments/validation/latest and are removed.
"""
import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageCms

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_phase_scheduler as phase
import photo_proposal_bank as bank
import photo_stage_observation as observation
import photo_review_anchors as anchors
from photo_image_contract import inspect_image

ROOT = Path(__file__).resolve().parent.parent
SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()


def state():
    # Unmarked in-memory protocol fixtures, not a forged real-photo result.
    result = {'current': 'v0', 'versions': {'v0': {'parent': None}}, 'status': 'working',
              'observations': [], 'reviewed': [], 'export': None}
    phase.init_workflow(result)
    return result


def seen(subject, stage=None, version=None, *, artifact='canonical', reviewed=True, **fields):
    stage = stage or subject['workflow']['phase']
    version = version or subject['current']
    item = {'id': 'o' + str(len(subject['observations']) + 1), 'version': version,
            'phase': stage, 'artifact': artifact, 'comparison_mode': 'target', 'view_spec': {},
            'stage_observation': observation.stage_observation_metadata(stage, 'test'),
            'decision_judgment': {'visible_comparison': 'Public fixture comparison'} if reviewed else None,
            **fields}
    subject['observations'].append(item)
    return item['id']


def forward(subject, **options):
    ident = seen(subject)
    return phase.advance_workflow(subject, 'Explicit stage decision', ident, **options)


def to_joint(subject):
    forward(subject)
    forward(subject)
    forward(subject, bank_delta=[], color_reassessment=[])
    forward(subject)
    forward(subject)
    assert subject['workflow']['phase'] == 'joint'


def to_g4(subject):
    to_joint(subject)
    ident = seen(subject)
    subject['export'] = {'version': 'v0', 'path': 'fixture-export.jpg'}
    phase.advance_workflow(subject, 'Actual export recorded by caller', ident)
    return seen(subject, artifact='export', path='fixture-export.jpg')


def pixels(path):
    with Image.open(path) as image:
        return np.array(image)


def non_srgb_profile():
    """Synthetic RGB matrix variation with a misleading sRGB description."""
    data = bytearray(SRGB)
    count = int.from_bytes(data[128:132], 'big')
    for index in range(count):
        cursor = 132 + index * 12
        if data[cursor:cursor + 4] == b'rXYZ':
            offset = int.from_bytes(data[cursor + 4:cursor + 8], 'big')
            for position in range(offset + 8, offset + 20, 4):
                value = int.from_bytes(data[position:position + 4], 'big', signed=True)
                data[position:position + 4] = int(value * .75).to_bytes(4, 'big', signed=True)
            return bytes(data)
    raise AssertionError('Standard RGB ICC lacks rXYZ fixture tag')


class WorkflowChecks(unittest.TestCase):
    def test_init_single_authority_idempotent_and_detached(self):
        subject = state()
        before = copy.deepcopy(subject)
        result = phase.init_workflow(subject)
        self.assertEqual(subject, before)
        self.assertEqual(set(result), {'phase', 'revision', 'checkpoints', 'accepted_versions', 'review_debts', 'events'})
        self.assertNotIn('stage_phase', subject)
        result['events'].append('not shared')
        self.assertEqual(subject['workflow']['events'], [])
        with self.assertRaises(ValueError):
            phase.init_workflow({'workflow': 'legacy', 'current': 'v0', 'versions': {'v0': {}}})

    def test_full_mainline_no_action_or_fifth_issue_quota(self):
        subject = state()
        ident = seen(subject)
        delta = [{'op': 'add', 'id': 'color' + str(i), 'branch': 'color', 'diagnosis': 'Color judgment ' + str(i)}
                 for i in range(17)]
        bank.apply_bank_delta(subject, delta, ident)
        phase.advance_workflow(subject, 'Begin tone', ident)
        forward(subject)
        keeps = [{'op': 'keep', 'id': 'color' + str(i)} for i in range(17)]
        forward(subject, bank_delta=keeps,
                color_reassessment=[{'decision': 'unchanged_all', 'reason': 'All 17 explicitly remain valid'}])
        forward(subject)
        forward(subject)
        ident = seen(subject)
        subject['export'] = {'version': 'v0', 'path': 'fixture-export.jpg'}
        phase.advance_workflow(subject, 'Export ready', ident)
        self.assertEqual(subject['workflow']['phase'], 'G4')
        ident = seen(subject, artifact='export', path='fixture-export.jpg')
        with self.assertRaises(ValueError):
            phase.advance_workflow(subject, 'Cannot fake confirmation', ident)
        subject.update(status='complete', selected_version='v0')
        phase.advance_workflow(subject, 'Caller successfully confirmed actual JPEG', ident, bank_delta=[])
        self.assertEqual(subject['workflow']['phase'], 'complete')
        self.assertEqual(len(subject['workflow']['events']), 7)
        self.assertEqual(len(subject['proposal_bank']['issues']), 17)

    def test_transition_validation_has_no_mutation(self):
        subject = state()
        ident = seen(subject)
        before = copy.deepcopy(subject)
        plan = phase.validate_transition(subject, 'Open tone', ident)
        self.assertEqual(subject, before)
        plan['workflow']['events'].clear()
        self.assertEqual(subject, before)
        for options in ({'target_phase': 'color'}, {'selected_version': 'missing'}, {'target_phase': 'invented'}):
            with self.assertRaises(ValueError):
                phase.advance_workflow(subject, 'No skipping', ident, **options)
            self.assertEqual(subject, before)
        for bad in ('', ' ', None):
            with self.assertRaises(ValueError):
                phase.advance_workflow(subject, bad, ident)

    def test_bad_review_roi_engineering_pending_and_phase(self):
        for patch in ({'decision_judgment': {}}, {'phase': 'tone'}, {'synthetic': True},
                      {'comparison_mode': 'effect'}, {'view_spec': {'candidate_roi': [0, 0, 1, 1]}}):
            subject = state()
            ident = seen(subject, **patch)
            before = copy.deepcopy(subject)
            with self.assertRaises(ValueError):
                phase.advance_workflow(subject, 'Rejected evidence', ident)
            self.assertEqual(subject, before)
        for patch in ({'pending': {}}, {'status': 'engineering_diagnosis_required'}, {'engineering_fake': True}):
            subject = state()
            ident = seen(subject)
            subject.update(patch)
            with self.assertRaises(ValueError):
                phase.advance_workflow(subject, 'Not progress', ident)

    def test_g2_reassessment_matches_actual_bank_operations_atomically(self):
        subject = state()
        ident = seen(subject)
        bank.apply_bank_delta(subject, [{'op': 'add', 'id': 'c', 'branch': 'color', 'diagnosis': 'Initial color'}], ident)
        phase.advance_workflow(subject, 'Begin', ident)
        forward(subject)
        ident = seen(subject)
        before = copy.deepcopy(subject)
        # G2 can commit incrementally: unmentioned proposals remain stored,
        # not falsely marked reviewed and not a reason to repeat the whole turn.
        partial = phase.validate_transition(subject, 'Incremental reassessment', ident,
            bank_delta=[{'op': 'keep', 'id': 'c'}], color_reassessment=[])
        self.assertEqual(partial['to_phase'], 'color')
        self.assertEqual(subject, before)
        delta = [{'op': 'revise', 'id': 'c', 'diagnosis': 'Changed after real tone evidence'},
                 {'op': 'add', 'id': 'c2', 'branch': 'color', 'diagnosis': 'New color judgment'}]
        decisions = [{'issue_id': 'c', 'decision': 'revise', 'reason': 'Tone changes interpretation'},
                     {'issue_id': 'c2', 'decision': 'new', 'reason': 'Now visible'}]
        phase.advance_workflow(subject, 'Reassessed', ident, bank_delta=delta, color_reassessment=decisions)
        self.assertEqual(subject['proposal_bank']['issues']['c']['diagnosis'], 'Changed after real tone evidence')
        self.assertEqual(subject['proposal_bank']['issues']['c']['records'][0]['diagnosis'], 'Initial color')

    def test_g2_mixed_issue_color_group_must_be_reassessed_and_may_defer(self):
        subject = state()
        ident = seen(subject)
        bank.apply_bank_delta(subject, [{'op': 'add', 'id': 'mixed', 'branch': 'tone',
            'diagnosis': 'One original mixed proposal',
            'proposed_controls': {'light.contrast': 10, 'wb.temp': 5300}}], ident)
        phase.advance_workflow(subject, 'Begin tone', ident)
        forward(subject)
        ident = seen(subject)
        plan = phase.validate_transition(subject, 'Unmentioned mixed proposal remains stored', ident,
                                         bank_delta=[], color_reassessment=[])
        self.assertIn('mixed', plan['proposal_bank']['issues'])
        phase.advance_workflow(subject, 'Color remains uncertain and is explicitly deferred', ident,
            bank_delta=[{'op': 'defer', 'id': 'mixed', 'reason': 'No justified color execution yet'}],
            color_reassessment=[{'issue_id': 'mixed', 'decision': 'defer', 'reason': 'Needs later evidence'}])
        self.assertEqual(subject['workflow']['phase'], 'color')
        self.assertEqual(subject['proposal_bank']['issues']['mixed']['status'], 'deferred')
        self.assertEqual(subject['proposal_bank']['issues']['mixed']['records'][0]['diagnosis'], 'One original mixed proposal')

    def test_bank_full_long_records_version_binding_and_operation(self):
        subject = state()
        ident = seen(subject)
        text = 'Full public narrative. ' * 12000
        bank.apply_bank_delta(subject, [{'op': 'add', 'id': 't', 'branch': 'tone', 'diagnosis': text,
                                         'proposed_controls': {'values': {'light.exposure': .25}},
                                         'evidence': {'observation': ident, 'note': text}}], ident)
        phase.advance_workflow(subject, 'Begin', ident)
        self.assertEqual(bank.validate_selected_issue(subject, 't', 'v0')['diagnosis'], text)
        self.assertIsNone(bank.validate_selected_issue(subject, None, 'v0'))
        subject['versions']['v1'] = {'parent': 'v0', 'operation_id': 'op1'}
        subject['current'] = 'v1'
        bank.record_operation(subject, 't', 'op1', 'v0', 'v1')
        bank.invalidate_bank(subject, 'v1', except_issue='t')
        row = subject['proposal_bank']['issues']['t']
        self.assertTrue(row['needs_reassessment'])
        self.assertEqual(row['proposed_controls']['values']['light.exposure'], .25)
        with self.assertRaises(ValueError):
            bank.validate_selected_issue(subject, 't', 'v1')
        ident = seen(subject)
        bank.apply_bank_delta(subject, [{'op': 'keep', 'id': 't', 'reason': text}], ident)
        self.assertFalse(bank.validate_selected_issue(subject, 't', 'v1')['needs_reassessment'])
        history = bank.bank_history(subject, 't')
        self.assertEqual(history[0]['diagnosis'], text)
        history[0]['diagnosis'] = 'detached'
        self.assertEqual(bank.bank_history(subject, 't')[0]['diagnosis'], text)
        before = copy.deepcopy(subject)
        with self.assertRaises(ValueError):
            bank.apply_bank_delta(subject, [{'op': 'retire', 'id': 't'}, {'op': 'keep', 'id': 'missing'}], ident)
        self.assertEqual(subject, before)
        bank.apply_bank_delta(subject, [{'op': 'defer', 'id': 't'}], ident)
        bank.apply_bank_delta(subject, [{'op': 'keep', 'id': 't'}], ident)
        bank.apply_bank_delta(subject, [{'op': 'retire', 'id': 't'}], ident)
        self.assertEqual(subject['proposal_bank']['issues']['t']['status'], 'retired')
        self.assertEqual(bank.bank_history(subject, 't')[0]['diagnosis'], text)

    def test_projection_is_not_storage_quota_and_stale_proposals_survive(self):
        subject = state()
        ident = seen(subject)
        bank.apply_bank_delta(subject, [{'op': 'add', 'id': 'i' + str(i), 'branch': 'tone',
                                         'diagnosis': 'Judgment', 'priority': i} for i in range(21)], ident)
        projection = bank.bank_projection(subject, 'tone')
        self.assertEqual(len(projection['active']), 3)
        self.assertEqual(len(projection['summary']), 21)
        subject['versions']['v1'] = {'parent': 'v0', 'operation_id': 'op1'}
        bank.invalidate_bank(subject, 'v1')
        self.assertTrue(all(row['needs_reassessment'] for row in subject['proposal_bank']['issues'].values()))
        self.assertEqual(len(bank.bank_history(subject)), 42)

    def test_review_debts_require_explicit_checkpoint_review(self):
        subject = state()
        forward(subject)
        phase.add_review_debt(subject, {'id': 'd1', 'version': 'v0', 'due_phase': 'G2',
                                        'operation_id': 'op-context', 'reason': 'Color after grayscale'})
        ident = seen(subject)
        with self.assertRaises(ValueError):
            phase.resolve_review_debts(subject, [{'id': 'd1', 'status': 'resolved', 'reason': 'Too early'}], ident)
        phase.advance_workflow(subject, 'Tone reviewed; color next', ident)
        ident = seen(subject)
        plan = phase.validate_transition(subject, 'Debt remains visible, not an administrative stop', ident,
                                         bank_delta=[], color_reassessment=[])
        self.assertEqual(plan['workflow']['events'][-1]['outstanding_review_debts'], ['d1'])
        self.assertEqual(subject['workflow']['review_debts'][0]['status'], 'pending')
        phase.resolve_review_debts(subject, [{'id': 'd1', 'status': 'accepted_tradeoff', 'reason': 'Explicit reviewed tradeoff'}], ident)
        phase.advance_workflow(subject, 'Now color-qualified', ident, bank_delta=[], color_reassessment=[])
        self.assertEqual(subject['workflow']['review_debts'][0]['status'], 'accepted_tradeoff')

    def test_accept_earlier_branch_archives_only_off_ancestry_debts(self):
        for branch, checkpoint in (('tone', 'G2'), ('color', 'G3')):
            with self.subTest(branch=branch):
                subject = state()
                forward(subject)
                if branch == 'color':
                    forward(subject)
                    forward(subject, bank_delta=[], color_reassessment=[])
                for number in (1, 2):
                    version = 'v' + str(number)
                    operation = 'op' + str(number)
                    subject['versions'][version] = {'parent': 'v' + str(number - 1), 'operation_id': operation}
                    subject['current'] = version
                    phase.add_review_debt(subject, {'id': 'd' + str(number), 'version': version,
                                                    'due_phase': checkpoint, 'operation_id': operation,
                                                    'reason': 'Cross-stage consequence ' + str(number)})
                original_events = copy.deepcopy(subject['workflow']['events'])
                original_debts = copy.deepcopy(subject['workflow']['review_debts'])
                # Reopen and explicitly accept the earlier actually observed version.
                subject['current'] = 'v1'
                ident = seen(subject, version='v1')
                before = copy.deepcopy(subject)
                plan = phase.validate_transition(subject, 'Prefer observed v1 over v2', ident, 'v1')
                self.assertEqual(subject, before)
                self.assertEqual(plan['workflow']['review_debts'][1]['status'], 'not_applicable_selected_branch')
                phase.advance_workflow(subject, 'Prefer observed v1 over v2', ident, 'v1')
                selection = subject['workflow']['events'][-1]
                self.assertEqual(subject['workflow']['phase'], checkpoint)
                self.assertEqual(subject['workflow']['accepted_versions'][branch], 'v1')
                ancestor, discarded = subject['workflow']['review_debts']
                self.assertEqual(ancestor, original_debts[0])
                self.assertEqual(ancestor['status'], 'pending')
                self.assertEqual(discarded['status'], 'not_applicable_selected_branch')
                self.assertEqual(discarded['selection_event_id'], selection['id'])
                self.assertEqual(discarded['selected_version'], 'v1')
                self.assertEqual(discarded['selection_observation_id'], ident)
                for key in ('id', 'version', 'due_phase', 'operation_id', 'reason'):
                    self.assertEqual(discarded[key], original_debts[1][key])
                for key in ('reviewed_version', 'observation_id', 'resolution_reason'):
                    self.assertNotIn(key, discarded)
                self.assertEqual(subject['workflow']['events'][:-1], original_events)
                ident = seen(subject, version='v1')
                options = {'bank_delta': [], 'color_reassessment': []} if branch == 'tone' else {}
                plan = phase.validate_transition(subject, 'Ancestor debt remains in the review ledger', ident, **options)
                self.assertIn('d1', plan['workflow']['events'][-1]['outstanding_review_debts'])
                self.assertEqual(subject['workflow']['review_debts'][0]['status'], 'pending')
                with self.assertRaises(ValueError):
                    phase.resolve_review_debts(subject, [{'id': 'd2', 'status': 'resolved',
                                                         'reason': 'Cannot review an abandoned descendant'}], ident)
                phase.resolve_review_debts(subject, [{'id': 'd1', 'status': 'resolved',
                                                     'reason': 'DSH explicitly reviewed v1 consequence'}], ident)
                phase.advance_workflow(subject, 'Selected branch reviewed', ident, **options)
                self.assertEqual(subject['workflow']['phase'], 'color' if branch == 'tone' else 'joint')
                self.assertEqual(len(subject['workflow']['review_debts']), 2)
                self.assertEqual(subject['workflow']['review_debts'][0]['status'], 'resolved')
                self.assertEqual(subject['workflow']['review_debts'][1], discarded)

    def test_branch_selection_rejects_unknown_or_cyclic_ancestry_atomically(self):
        for parent in ('missing', 'v1'):
            with self.subTest(parent=parent):
                subject = state()
                forward(subject)
                subject['versions']['v1'] = {'parent': parent, 'operation_id': 'op1'}
                subject['current'] = 'v1'
                ident = seen(subject)
                before = copy.deepcopy(subject)
                with self.assertRaises(ValueError):
                    phase.advance_workflow(subject, 'Invalid ancestry is not branch selection', ident, 'v1')
                self.assertEqual(subject, before)

    def test_g4_actual_artifact_and_repair_preserve_history(self):
        subject = state()
        to_joint(subject)
        ident = seen(subject)
        with self.assertRaises(ValueError):
            phase.advance_workflow(subject, 'Preview is not export', ident)
        subject['export'] = {'version': 'v0', 'path': 'fixture-export.jpg'}
        phase.advance_workflow(subject, 'Worker exported', ident)
        ident = seen(subject, artifact='export', path='wrong.jpg')
        with self.assertRaises(ValueError):
            phase.return_to_joint(subject, 'Repair', ident)
        ident = seen(subject, artifact='export', path='fixture-export.jpg')
        event_count = len(subject['workflow']['events'])
        phase.return_to_joint(subject, 'Actual JPEG needs repair', ident)
        self.assertEqual(subject['workflow']['phase'], 'joint')
        self.assertIsNone(subject['workflow']['accepted_versions']['joint'])
        self.assertEqual(subject['export']['path'], 'fixture-export.jpg')
        self.assertIn('confirmation_invalidated_by', subject['export'])
        self.assertEqual(len(subject['workflow']['events']), event_count + 1)
        with self.assertRaises(ValueError):
            phase.return_to_joint(subject, 'No arbitrary reversal', ident)

    def test_g2_revisit_is_explicit_and_preserves_original_judgments(self):
        subject = state()
        forward(subject)
        forward(subject)
        ident = seen(subject)
        phase.revisit_workflow(subject, 'Color reveals tone error', ident, 'tone')
        self.assertEqual(subject['workflow']['phase'], 'tone')
        self.assertIsNone(subject['workflow']['accepted_versions']['tone'])
        self.assertEqual(len(subject['workflow']['events']), 3)


class FocusDebtChecks(unittest.TestCase):
    def setup_debt(self, *, explicit=False, full=False, roi=None):
        subject = state()
        forward(subject)
        spec = {'candidate_roi': roi} if roi is not None else {}
        ident = seen(subject, view_spec=spec,
                     stage_observation=observation.stage_observation_metadata('tone', 'fixture', explicit))
        review = {'visible_comparison': 'Actual fixture grayscale comparison', 'reason': 'Keep original rationale'}
        if explicit or full:
            anchor = {'id': 'sky', 'purpose': 'target', 'extent': 'explicit_cells' if explicit else 'full_frame',
                      'view_role': 'current', 'region': 'Sky above facade', 'qualification': 'Not author registration'}
            if explicit:
                anchor['cells'] = ['A1']
            review['review_anchors'] = [anchor]
        expectation = {'target': 'Sky', 'expected_change': 'More separation', 'protected_region': 'Warm facade',
                       'risk': 'Spill', 'falsifier': 'Facade becomes dull'}
        focus = anchors.build_debt_focus(review, subject['observations'][-1], 'op1', expectation)
        phase.add_review_debt(subject, {'schema': 'photo-review-debt/v2', 'id': 'd1', 'version': 'v0',
            'due_phase': 'G2', 'reason': 'Recheck color and protection', 'operation_id': 'op1', 'focus': focus})
        ident = seen(subject)
        phase.advance_workflow(subject, 'Open actual color checkpoint', ident)
        return subject, self.color_seen(subject, grid=explicit)

    def color_seen(self, subject, grid=False, **fields):
        return seen(subject, stage_observation=observation.stage_observation_metadata(
            subject['workflow']['phase'], 'fixture', grid), **fields)

    def update(self, subject, resolution='no_regression'):
        debt = subject['workflow']['review_debts'][0]
        return {'id': debt['id'], 'resolution': resolution, 'reason': 'Explicit full review conclusion',
                'checks': [{'focus_id': focus['id'], 'outcome': resolution,
                            'evidence': 'Visible preserved relation in ' + focus['purpose'] + ' region',
                            'review_region': focus['region'],
                            **({'review_cells': ['D4']} if focus['extent'] == 'explicit_cells' else {})}
                           for focus in debt['focus']],
                **({'joint_diagnosis': 'New boundary color residue needs diagnosis'} if resolution == 'spawn_joint_task' else {})}

    def test_unknown_focus_pending_is_not_gate_and_complete_checks_bind_actual_phase(self):
        subject, ident = self.setup_debt()
        before = copy.deepcopy(subject)
        plan = phase.validate_transition(subject, 'No debt checklist gate', ident, bank_delta=[], color_reassessment=[])
        self.assertEqual(subject, before)
        self.assertEqual(plan['workflow']['events'][-1]['outstanding_review_debts'], ['d1'])
        self.assertEqual(subject['workflow']['review_debts'][0]['status'], 'pending')
        result = phase.resolve_review_debts(subject, [self.update(subject)], ident)[0]
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['actual_phase'], 'G2')
        self.assertEqual(result['reviewed_version'], subject['current'])
        self.assertFalse(result['photographic_fix_claimed'])
        self.assertTrue(all(c['source']['observation_id'] == ident for c in result['checks']))

    def test_v2_status_bypass_missing_focus_or_evidence_rejected_without_mutation(self):
        subject, ident = self.setup_debt()
        for case in ('legacy', 'missing', 'blank', 'duplicate', 'mismatch'):
            update = self.update(subject)
            if case == 'legacy':
                update = {'id': 'd1', 'status': 'resolved', 'reason': 'Batch reason cannot bypass v2'}
            elif case == 'missing':
                update['checks'].pop()
            elif case == 'blank':
                update['checks'][0]['evidence'] = ''
            elif case == 'duplicate':
                update['checks'].append(copy.deepcopy(update['checks'][0]))
            else:
                update['checks'][0]['outcome'] = 'minor_acceptable'
            before = copy.deepcopy(subject)
            with self.assertRaises(ValueError, msg=case):
                phase.resolve_review_debts(subject, [update], ident)
            self.assertEqual(subject, before)

    def test_explicit_cells_relocalized_on_independent_actual_target_grid(self):
        subject, _ = self.setup_debt(explicit=True, roi=[.1, .1, .6, .7])
        ident = self.color_seen(subject, grid=True,
            view_spec={'reference_roi': [0, 0, .4, .5], 'candidate_roi': [.4, .4, 1, 1]})
        update = self.update(subject)
        reviewed = phase.resolve_review_debts(subject, [update], ident)[0]
        self.assertEqual(reviewed['focus'][0]['cells'], ['A1'])
        self.assertEqual(reviewed['checks'][0]['review_cells'], ['D4'])
        self.assertEqual(reviewed['focus'][0]['source']['roi'], [.1, .1, .6, .7])
        self.assertEqual(reviewed['checks'][0]['source']['roi'], [.4, .4, 1, 1])
        self.assertEqual(reviewed['checks'][0]['mapping'], 'qualitative_relocalization_not_registration')

    def test_explicit_cells_missing_actual_grid_or_cells_stays_pending(self):
        for grid, cells in ((False, True), (True, False)):
            subject, _ = self.setup_debt(explicit=True)
            ident = self.color_seen(subject, grid=grid)
            update = self.update(subject)
            if not cells:
                update['checks'][0].pop('review_cells')
            before = copy.deepcopy(subject)
            with self.assertRaises(ValueError):
                phase.resolve_review_debts(subject, [update], ident)
            self.assertEqual(subject, before)

    def test_full_frame_review_cannot_be_borrowed_from_cropped_view(self):
        subject, _ = self.setup_debt(full=True)
        ident = self.color_seen(subject, view_spec={'candidate_roi': [.1, .1, .8, .8]})
        before = copy.deepcopy(subject)
        with self.assertRaises(ValueError):
            phase.resolve_review_debts(subject, [self.update(subject)], ident)
        self.assertEqual(subject, before)

    def test_due_phase_actual_checkpoint_and_current_target_cannot_be_faked(self):
        for change in ('effect', 'gray', 'joint', 'stale', 'early'):
            subject, _ = self.setup_debt()
            if change == 'joint':
                subject['workflow']['phase'] = 'joint'
            if change == 'early':
                subject['workflow']['review_debts'][0]['due_phase'] = 'G3'
            if change == 'stale':
                subject['versions']['v1'] = {'parent': 'v0'}
                subject['current'] = 'v1'
            fields = {'comparison_mode': 'effect'} if change == 'effect' else {}
            ident = self.color_seen(subject, version='v0', **fields)
            if change == 'gray':
                subject['observations'][-1]['stage_observation']['view_mode'] = 'grayscale'
            before = copy.deepcopy(subject)
            with self.assertRaises(ValueError, msg=change):
                phase.resolve_review_debts(subject, [self.update(subject)], ident)
            self.assertEqual(subject, before)

    def test_overdue_review_records_actual_g4_not_forged_g2_or_g3(self):
        subject, ident = self.setup_debt()
        phase.advance_workflow(subject, 'Leave explicitly pending', ident, bank_delta=[], color_reassessment=[])
        forward(subject)
        forward(subject)
        ident = seen(subject)
        subject['export'] = {'version': 'v0', 'path': 'fixture-export.jpg'}
        phase.advance_workflow(subject, 'Actual JPEG available', ident)
        ident = self.color_seen(subject, artifact='export', path='fixture-export.jpg')
        result = phase.resolve_review_debts(subject, [self.update(subject, 'minor_acceptable')], ident)[0]
        self.assertEqual(result['actual_phase'], 'G4')
        self.assertEqual(result['due_phase'], 'G2')
        self.assertEqual(result['status'], 'accepted_tradeoff')

    def test_new_focus_requires_bound_observation_and_explicit_v2_schema(self):
        subject, _ = self.setup_debt()
        prototype = copy.deepcopy(subject['workflow']['review_debts'][0])
        prototype.update(id='d2', due_phase='G3')
        for bad in ('unbound', 'legacy', 'unknown_schema', 'operation'):
            debt = copy.deepcopy(prototype)
            if bad == 'unbound':
                debt['focus'][0]['source']['observation_id'] = 'missing'
            elif bad == 'legacy':
                debt.pop('schema')
            elif bad == 'operation':
                debt['operation_id'] = 'other'
            else:
                debt['schema'] = 'future-unknown'
            before = copy.deepcopy(subject)
            with self.assertRaises(ValueError, msg=bad):
                phase.add_review_debt(subject, debt)
            self.assertEqual(subject, before)

    def test_spawn_is_diagnostic_only_stable_and_exact_retry_is_noop(self):
        subject, ident = self.setup_debt()
        update = self.update(subject, 'spawn_joint_task')
        result = phase.resolve_review_debts(subject, [update], ident)[0]
        self.assertEqual(result['status'], 'reviewed_joint_task')
        self.assertTrue(result['review_complete'])
        self.assertFalse(result['photographic_fix_claimed'])
        issue = subject['proposal_bank']['issues'][result['joint_issue_id']]
        self.assertEqual(issue['id'], 'joint-from:d1')
        self.assertEqual(issue['branch'], 'joint')
        self.assertEqual(issue['task_groups'], {})
        self.assertNotIn('proposed_controls', issue)
        self.assertEqual(issue['source_debt_ids'], ['d1'])
        self.assertEqual(issue['source_refs'][0]['anchors'], result['focus'])
        before = copy.deepcopy(subject)
        phase.resolve_review_debts(subject, [update], ident)
        self.assertEqual(subject, before)

    def test_spawn_link_does_not_modify_existing_groups(self):
        subject, ident = self.setup_debt()
        bank.apply_bank_delta(subject, [{'op': 'add', 'id': 'existing', 'branch': 'joint',
            'diagnosis': 'Preexisting work', 'groups': [{'group_id': 'small', 'module': 'light',
            'proposed_controls': {'values': {'light.exposure': .1}}}]}], ident)
        groups = copy.deepcopy(subject['proposal_bank']['issues']['existing']['task_groups'])
        update = {**self.update(subject, 'spawn_joint_task'), 'existing_joint_issue_id': 'existing'}
        result = phase.resolve_review_debts(subject, [update], ident)[0]
        self.assertEqual(result['joint_issue_id'], 'existing')
        self.assertEqual(subject['proposal_bank']['issues']['existing']['task_groups'], groups)

    def test_bank_and_debt_commit_roll_back_together_on_later_failure(self):
        subject, ident = self.setup_debt()
        update = self.update(subject, 'spawn_joint_task')
        before = copy.deepcopy(subject)
        with self.assertRaises(ValueError):
            phase.resolve_review_debts(subject, [update, {'id': 'missing', 'reason': 'Invalid later debt'}], ident)
        self.assertEqual(subject, before)
        def fail_after_change(draft, *args, **kwargs):
            draft['proposal_bank']['revision'] += 1
            raise ValueError('Synthetic bank failure after draft change')
        with patch.object(bank, 'spawn_joint_diagnostic', fail_after_change):
            with self.assertRaises(ValueError):
                phase.resolve_review_debts(subject, [update], ident)
        self.assertEqual(subject, before)

    def test_parent_rollback_restore_is_reversible_idempotent_not_false_resolution(self):
        subject = state()
        forward(subject)
        subject['versions'].update({'v1': {'parent': 'v0'}, 'v2': {'parent': 'v1'}, 'other': {'parent': 'v0'}})
        for ident, version in (('d1', 'v1'), ('d2', 'v2'), ('off', 'other')):
            phase.add_review_debt(subject, {'id': ident, 'version': version, 'due_phase': 'G2', 'reason': 'Legacy consequence'})
        subject['workflow']['review_debts'].extend([
            {'id': 'resolved', 'version': 'v2', 'due_phase': 'G2', 'status': 'resolved'},
            {'id': 'accepted', 'version': 'v2', 'due_phase': 'G2', 'status': 'accepted_tradeoff'}])
        phase.reconcile_debt_applicability(subject, 'v1', 'parent-selection')
        self.assertEqual(subject['workflow']['review_debts'][1]['status'], 'not_applicable_selected_branch')
        phase.reconcile_debt_applicability(subject, 'v2', 'child-selection')
        statuses = {d['id']: d['status'] for d in subject['workflow']['review_debts']}
        self.assertEqual(statuses, {'d1': 'pending', 'd2': 'pending', 'off': 'not_applicable_selected_branch',
                                    'resolved': 'resolved', 'accepted': 'accepted_tradeoff'})
        before = copy.deepcopy(subject)
        phase.reconcile_debt_applicability(subject, 'v2', 'child-selection')
        self.assertEqual(subject, before)
        subject['current'] = 'v1'
        ident = seen(subject)
        phase.advance_workflow(subject, 'Parent branch accepted', ident)
        self.assertEqual(subject['workflow']['review_debts'][1]['status'], 'not_applicable_selected_branch')
        subject['workflow']['phase'] = 'tone'
        subject['current'] = 'v2'
        ident = seen(subject)
        phase.advance_workflow(subject, 'Reselected actual descendant', ident)
        self.assertEqual(subject['workflow']['review_debts'][1]['status'], 'pending')
        self.assertNotIn('reviewed_version', subject['workflow']['review_debts'][1])


class ObservationChecks(unittest.TestCase):
    def setUp(self):
        latest = ROOT / 'experiments' / 'validation' / 'latest'
        latest.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='phase-workflow-', dir=latest)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def inputs(self, array, *, profile=SRGB, exif=None):
        views = []
        for index, role in enumerate(('author target fixture', 'actual current fixture')):
            path = self.folder / ('input' + str(index) + '.png')
            options = {'icc_profile': profile}
            if exif is not None:
                options['exif'] = exif
            Image.fromarray(array).save(path, **options)
            views.append((str(path), role))
        return views

    def test_gray_linear_luminance_no_levels_equal_rgb_and_no_mutation(self):
        array = np.array([[[255, 0, 0], [0, 255, 0], [0, 0, 255], [60, 60, 60],
                           [128, 128, 128], [255, 255, 255], [0, 0, 0]]], dtype=np.uint8)
        views = self.inputs(array)
        originals = [Path(path).read_bytes() for path, _ in views]
        result = observation.derive_stage_views(views, 'tone', self.folder, 'gray', grid=True)
        self.assertEqual([role for _, role in result], [role for _, role in views])
        for path, _ in result:
            actual = pixels(path)
            self.assertTrue(np.array_equal(actual[:, :, 0], actual[:, :, 1]))
            self.assertTrue(np.array_equal(actual[:, :, 1], actual[:, :, 2]))
            np.testing.assert_allclose(actual[0, :, 0], [127, 220, 76, 60, 128, 255, 0], atol=1)
            inspect_image(path, require_srgb=True, require_upright=True, max_side=512)
        self.assertEqual(originals, [Path(path).read_bytes() for path, _ in views])
        metadata = observation.stage_observation_metadata('tone', 'gray', True)
        self.assertEqual(len(metadata['grid']['cells']), 16)
        self.assertFalse(metadata['grid']['burned_into_pixels'])
        self.assertEqual(metadata['grid']['line_rgb'], [128] * 3)
        self.assertEqual(metadata['grid']['label_rgb'], [0] * 3)
        # Grid changes no pixels, and a midgray-only scene is not autoleveled.
        plain = observation.derive_stage_views(views, 'tone', self.folder, 'plain')
        self.assertTrue(np.array_equal(pixels(plain[0][0]), pixels(result[0][0])))

    def test_all_other_phases_preserve_color_and_rgb8_quality(self):
        array = np.array([[[20, 120, 230], [180, 90, 40], [135, 155, 175]]], dtype=np.uint8)
        views = self.inputs(array)
        for stage in phase.PHASES:
            if stage == 'tone':
                continue
            result = observation.derive_stage_views(views, stage, self.folder, stage)
            np.testing.assert_array_equal(pixels(result[0][0]), array)
            self.assertEqual(observation.stage_observation_metadata(stage, stage)['view_mode'], 'color')

    def test_actual_icc_conversion_before_gray_not_retagging(self):
        array = np.array([[[150, 100, 60], [220, 90, 120], [10, 190, 70]]], dtype=np.uint8)
        profile = non_srgb_profile()
        views = self.inputs(array, profile=profile)
        expected = ImageCms.profileToProfile(Image.fromarray(array), ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                             ImageCms.createProfile('sRGB'), outputMode='RGB', renderingIntent=0, flags=0)
        color = observation.derive_stage_views(views, 'color', self.folder, 'icc')
        self.assertFalse(np.array_equal(np.asarray(expected), array))
        np.testing.assert_array_equal(pixels(color[0][0]), np.asarray(expected))
        gray = observation.derive_stage_views(views, 'tone', self.folder, 'iccgray')
        rgb = np.asarray(expected, dtype=float) / 255
        linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
        y = (linear * [.2126, .7152, .0722]).sum(axis=2)
        encoded = np.where(y <= .0031308, y * 12.92, 1.055 * y ** (1 / 2.4) - .055)
        np.testing.assert_allclose(pixels(gray[0][0])[:, :, 0], np.floor(encoded * 255 + .5), atol=0)

    def test_upright_max_side_independent_geometry_and_exclusive_outputs(self):
        array = np.zeros((300, 900, 3), dtype=np.uint8)
        array[:100, :100] = (220, 70, 30)
        exif = Image.Exif()
        exif[274] = 6
        views = self.inputs(array, exif=exif)
        originals = [Path(path).read_bytes() for path, _ in views]
        result = observation.derive_stage_views(views, 'tone', self.folder, 'upright')
        facts = inspect_image(result[0][0], require_srgb=True, require_upright=True, max_side=512)
        self.assertEqual(facts['dimensions'], [171, 512])
        self.assertEqual(originals, [Path(path).read_bytes() for path, _ in views])
        with self.assertRaises(FileExistsError):
            observation.derive_stage_views(views, 'tone', self.folder, 'upright')
        with self.assertRaises(ValueError):
            observation.derive_stage_views(views, 'tone', self.folder, '../escape')

    def test_invalid_inputs_do_not_leave_half_a_pair(self):
        array = np.full((3, 4, 3), 80, dtype=np.uint8)
        views = self.inputs(array)
        bad = self.folder / 'untagged.png'
        Image.fromarray(array).save(bad)
        with self.assertRaises(ValueError):
            observation.derive_stage_views([views[0], (bad, 'untagged')], 'tone', self.folder, 'bad')
        self.assertFalse((self.folder / 'bad-stage-0.png').exists())
        with self.assertRaises(ValueError):
            observation.derive_stage_views(views[:1], 'tone', self.folder, 'short')
        rgba = self.folder / 'alpha.png'
        Image.new('RGBA', (3, 4)).save(rgba, icc_profile=SRGB)
        with self.assertRaises(ValueError):
            observation.derive_stage_views([(rgba, 'alpha'), views[1]], 'color', self.folder, 'alpha')


if __name__ == '__main__':
    unittest.main(verbosity=2)
