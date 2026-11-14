"""Offline synthetic review-anchor contracts; no image/model/native/hash/paid IO.

Run: trusted-python -I -B -X utf8 src/check_photo_review_anchors.py
"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_review_anchors as anchors
from photo_stage_observation import stage_observation_metadata


EXPECTATION = {'target': 'Sky A1 is narrative text, not an explicit cell.',
               'expected_change': 'Softer sky transition', 'protected_region': 'Keep warm facade',
               'risk': 'Color-edge spill', 'falsifier': 'Facade loses separation'}


def source(mode='target', phase='tone', grid=True, **fields):
    return {'id': 'o1', 'version': 'v1', 'phase': phase, 'comparison_mode': mode,
            'view_spec': {}, 'stage_observation': stage_observation_metadata(phase, 'o1', grid),
            **({'parent': 'v0'} if mode == 'effect' else {}), **fields}


def item(**fields):
    return {'id': 'sky', 'purpose': 'target', 'extent': 'explicit_cells', 'view_role': 'current',
            'cells': ['A1', 'A2'], 'region': 'Sky above roof',
            'qualification': 'Current-view sky only; author region is located separately', **fields}


class AnchorChecks(unittest.TestCase):
    def test_schema_items_are_objects_and_legacy_is_separate(self):
        self.assertEqual(anchors.anchor_schema()['type'], 'object')
        update = anchors.debt_update_schema()
        self.assertEqual(len(update['oneOf']), 2)
        self.assertNotIn('status', update['oneOf'][0]['properties'])
        self.assertIn('status', update['oneOf'][1]['properties'])

    def test_binding_is_host_only_detached_and_stable(self):
        observation = source()
        review = {'review_anchors': [item()], 'reason': 'Original full explanation'}
        before = copy.deepcopy((review, observation))
        result = anchors.build_debt_focus(review, observation, 'op1', EXPECTATION)
        again = anchors.build_debt_focus(review, observation, 'op1', EXPECTATION)
        self.assertEqual(result, again)
        self.assertEqual((review, observation), before)
        self.assertEqual(result[0]['source']['observation_id'], 'o1')
        self.assertEqual(result[0]['source']['settings_version'], 'v1')
        self.assertEqual(result[0]['source']['view_mode'], 'grayscale')
        result[0]['source']['grid']['cells'].clear()
        self.assertEqual(len(observation['stage_observation']['grid']['cells']), 16)
        with self.assertRaises(ValueError):
            anchors.bind_anchors([item(source={'observation_id': 'forged'})], observation)

    def test_independent_author_and_current_rois(self):
        observation = source(view_spec={'reference_roi': [.1, .2, .8, .9], 'candidate_roi': [.3, .4, .9, 1]})
        bound = anchors.bind_anchors([item(), item(id='author-sky', view_role='author', cells=['B3'])], observation)
        self.assertEqual(bound[0]['source']['roi'], [.3, .4, .9, 1])
        self.assertEqual(bound[1]['source']['roi'], [.1, .2, .8, .9])
        self.assertFalse(bound[0]['source']['grid']['registration_claim'])
        self.assertNotEqual(bound[0]['cells'], bound[1]['cells'])

    def test_effect_role_parent_and_shared_roi_are_explicit(self):
        observation = source('effect', view_spec={'candidate_roi': [.1, .1, .8, .8]})
        result = anchors.bind_anchors([item(view_role='effect_parent')], observation)
        self.assertEqual(result[0]['source']['parent_version'], 'v0')
        self.assertEqual(result[0]['source']['roi'], [.1, .1, .8, .8])
        for role in ('author', 'current'):
            with self.assertRaises(ValueError):
                anchors.bind_anchors([item(view_role=role)], observation)
        for role in ('effect_parent', 'effect_current'):
            with self.assertRaises(ValueError):
                anchors.bind_anchors([item(view_role=role)], source())
        with self.assertRaises(ValueError):
            anchors.bind_anchors([item(view_role='effect_parent')], source('effect', parent=None))

    def test_grid_absent_or_bad_cannot_establish_explicit_cells(self):
        for patch in ({'grid': None}, {'grid': True}, {'grid': {'cells': ['A1']}}):
            observation = source()
            observation['stage_observation'].update(patch)
            with self.assertRaises(ValueError):
                anchors.bind_anchors([item()], observation)
        for cells in ([], ['E1'], ['A1', 'A1'], 'A1', [None]):
            with self.assertRaises(ValueError):
                anchors.bind_anchors([item(cells=cells)], source())

    def test_unknown_does_not_need_or_infer_grid_or_cells(self):
        observation = source(grid=False)
        review = {'visible_comparison': 'A1 and D4 are prose only', 'reason': 'Preserve this rationale',
                  'wb_assessment': {'reference_pairs': [{'current_cell': 'A1'}]},
                  'mask_plan': {'id': 42, 'geometry': [0, 0, 1, 1]}}
        focus = anchors.build_debt_focus(review, observation, 'op1', EXPECTATION)
        self.assertEqual({f['purpose'] for f in focus}, {'target', 'protected'})
        self.assertTrue(all(f['extent'] == 'unknown' and 'cells' not in f for f in focus))
        self.assertTrue(all(f['source']['grid'] is None for f in focus))
        self.assertEqual(focus[0]['context']['effect_expectation'], EXPECTATION)
        self.assertEqual(focus[0]['context']['reason'], review['reason'])
        self.assertEqual(focus[0]['context']['visible_comparison'], review['visible_comparison'])
        self.assertEqual(focus[1]['region'], EXPECTATION['protected_region'])

    def test_full_frame_requires_explicit_declaration_and_uncropped_source(self):
        full = item(extent='full_frame')
        full.pop('cells')
        self.assertEqual(anchors.bind_anchors([full], source(grid=False))[0]['extent'], 'full_frame')
        with self.assertRaises(ValueError):
            anchors.bind_anchors([full], source(view_spec={'candidate_roi': [.1, .1, .8, .8]}))
        with self.assertRaises(ValueError):
            anchors.bind_anchors([item(extent='unknown')], source())

    def test_missing_host_phase_mode_and_gray_metadata_are_not_guessed(self):
        for key in ('id', 'version', 'phase', 'comparison_mode', 'stage_observation'):
            observation = source()
            observation.pop(key)
            if key == 'phase':
                observation['stage_observation'].pop('phase')
            with self.assertRaises(ValueError):
                anchors.bind_anchors([item()], observation)
        observation = source()
        observation['stage_observation']['phase'] = 'color'
        with self.assertRaises(ValueError):
            anchors.bind_anchors([item()], observation)

    def test_partial_purposes_get_only_missing_unknown_and_ids_do_not_collide(self):
        review = {'review_anchors': [item(id='unknown-protected')]}
        result = anchors.build_debt_focus(review, source(), 'op:1', EXPECTATION)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]['purpose'], 'protected')
        self.assertNotEqual(result[0]['id'], result[1]['id'])
        self.assertNotEqual(result[0]['id'], anchors.build_debt_focus(review, source(), 'op2', EXPECTATION)[0]['id'])
        with self.assertRaises(ValueError):
            anchors.bind_anchors([item(), item()], source())

    def test_persisted_focus_revalidates_actual_source(self):
        observation = source()
        focus = anchors.build_debt_focus({'review_anchors': [item()]}, observation, 'op1', EXPECTATION)
        state = {'observations': [observation], 'versions': {'v1': {}}}
        self.assertEqual(anchors.validate_debt_focus(focus, state), focus)
        for key, value in (('observation_id', 'missing'), ('settings_version', 'other'), ('roi', [0, 0, .5, .5])):
            bad = copy.deepcopy(focus)
            bad[0]['source'][key] = value
            with self.assertRaises(ValueError):
                anchors.validate_debt_focus(bad, state)

    def test_review_checks_require_individual_evidence_and_current_grid(self):
        debt = {'focus': anchors.build_debt_focus({'review_anchors': [item()]}, source(), 'op1', EXPECTATION)}
        update = {'id': 'd1', 'resolution': 'no_regression', 'reason': 'Whole review conclusion',
                  'checks': [{'focus_id': f['id'], 'outcome': 'no_regression', 'evidence': 'Independently visible preserved separation',
                              'review_region': 'Same photographic role located in current view',
                              **({'review_cells': ['D4']} if f['extent'] == 'explicit_cells' else {})} for f in debt['focus']]}
        actual = source(phase='G2')
        bound = anchors.validate_debt_update(update, debt, actual)
        self.assertEqual(bound[0]['review_cells'], ['D4'])
        self.assertEqual(bound[0]['source']['phase'], 'G2')
        self.assertEqual(bound[0]['mapping'], 'qualitative_relocalization_not_registration')
        for mutate in ('missing', 'evidence', 'cells', 'extra'):
            bad = copy.deepcopy(update)
            if mutate == 'missing':
                bad['checks'].pop()
            elif mutate == 'extra':
                bad['checks'].append(copy.deepcopy(bad['checks'][0]))
            elif mutate == 'cells':
                bad['checks'][0].pop('review_cells')
            else:
                bad['checks'][0]['evidence'] = ''
            with self.assertRaises(ValueError):
                anchors.validate_debt_update(bad, debt, actual)
        with self.assertRaises(ValueError):
            anchors.validate_debt_update(update, debt, source(phase='G2', grid=False))

    def test_optional_wrong_role_is_retained_without_blocking_real_edit_focus(self):
        observation = source(mode='effect')
        bad = item()
        bad['view_role'] = 'current'
        focus = anchors.build_debt_focus({'review_anchors': [bad]}, observation, 'op-real-feedback', EXPECTATION)
        self.assertEqual({f['purpose'] for f in focus}, {'target', 'protected'})
        self.assertTrue(all(f['extent'] == 'unknown' and f['view_role'] == 'effect_current' for f in focus))
        self.assertEqual(focus[0]['context']['unadopted_anchors']['submitted'], [bad])
        with self.assertRaises(ValueError):
            anchors.bind_anchors([bad], observation)

    def test_unknown_focus_allows_qualitative_review_without_cells(self):
        debt = {'focus': anchors.build_debt_focus({}, source(grid=False), 'op1', EXPECTATION)}
        update = {'id': 'd1', 'resolution': 'minor_acceptable', 'reason': 'Small visible tradeoff',
                  'checks': [{'focus_id': f['id'], 'outcome': 'minor_acceptable',
                              'evidence': 'Small warmth loss acceptable for separation', 'review_region': f['region']}
                             for f in debt['focus']]}
        self.assertEqual(len(anchors.validate_debt_update(update, debt, source(phase='G2', grid=False))), 2)
        update['resolution'] = 'no_regression'
        with self.assertRaises(ValueError):
            anchors.validate_debt_update(update, debt, source(phase='G2'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
