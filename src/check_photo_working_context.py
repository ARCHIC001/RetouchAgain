"""Pure in-memory v16 context checks; no image/native/model/RAW/hash/paid IO."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_working_context as context


def mask(ident=73, kind='linear', center=(.5, .5)):
    x, y = center
    shape = {'kind': kind, 'start': {'x': x, 'y': y - .2}, 'end': {'x': x, 'y': y + .2}} if kind == 'linear' else {
        'kind': kind, 'center': {'x': x, 'y': y}, 'rx': 1.4, 'ry': .2, 'angle': 32, 'feather': 60}
    return {'id': ident, 'name': 'Not a semantic label', 'visible': True, 'invert': False,
            'components': [{'op': 'add', 'invert': False, 'shape': shape}],
            'adjust': {'temp': -6, 'tint': 2, 'exposure': .3}}


def state():
    first, second, fork = {'masks': []}, {'masks': [mask()]}, {'masks': [mask(91, 'radial')]}
    return {'current': 'v2', 'versions': {
        'v0': {'parent': None, 'settings': first},
        'v1': {'parent': 'v0', 'settings': second, 'operation_id': 'op1'},
        'v2': {'parent': 'v1', 'settings': second, 'operation_id': 'op2'},
        'fork': {'parent': 'v0', 'settings': fork, 'operation_id': 'op3'}},
        'history': [
            {'action': 'edit', 'parent': 'v0', 'version': 'v1', 'operation_id': 'op1',
             'changed_controls': ['masks'], 'arguments': {'rationale': 'A real operation'}},
            {'action': 'edit', 'parent': 'v1', 'version': 'v2', 'operation_id': 'op2',
             'changed_controls': ['wb.tint'], 'arguments': {'values': {'wb.tint': 2}}},
            {'action': 'edit', 'parent': 'v0', 'version': 'fork', 'operation_id': 'op3',
             'changed_controls': ['masks'], 'arguments': {'rationale': 'INACTIVE_BODY_' * 500}}],
        'public_decisions': [{'proposed_controls': {'light.exposure': 5}}]}


def observation(version='v2', phase='joint'):
    return {'id': 'obs-' + version, 'version': version, 'phase': phase,
            'stage_observation': {'view_mode': 'grayscale' if phase == 'tone' else 'color'}}


class GeometryChecks(unittest.TestCase):
    def test_linear_midpoint_is_not_coverage_and_strict_decimal_boundary(self):
        for left, right in ((.2, .3), (.4, .5), (0, .1)):
            actual = {'masks': [mask(1, center=(left, .5)), mask(2, center=(right, .5))]}
            self.assertEqual(context.geometry_proximity(actual), [])
        actual = {'masks': [mask(1, center=(.2, .5)), mask(2, center=(.299, .5))]}
        warning = context.geometry_proximity(actual)[0]
        self.assertAlmostEqual(warning['distance'], .099)
        self.assertEqual(warning['centers'], [[.2, .5], [.299, .5]])
        self.assertIn('not_coverage', warning['center_definition'])
        self.assertFalse(warning['semantic_overlap']); self.assertFalse(warning['automatic_rejection'])

    def test_diagonal_boundary_and_custom_threshold(self):
        actual = {'masks': [mask(1, 'radial', (0, 0)), mask(2, 'radial', (.06, .08))]}
        self.assertEqual(context.geometry_proximity(actual), [])
        self.assertEqual(len(context.geometry_proximity(actual, threshold=.1001)), 1)
        for threshold in (0, -1, float('nan'), float('inf'), True):
            with self.assertRaises(ValueError):
                context.geometry_proximity(actual, threshold=threshold)

    def test_kinds_not_mixed_and_complex_geometry_not_made_up(self):
        simple, radial = mask(1), mask(2, 'radial')
        complex_mask = mask(3); complex_mask['components'] *= 2
        unknown = mask(4, 'brush')
        inverted = mask(5); inverted['invert'] = True
        missing = mask(6); del missing['components'][0]['shape']['end']
        self.assertEqual(context.geometry_proximity({'masks': [simple, radial, complex_mask, unknown, inverted, missing]}), [])

    def test_nearest_warnings_are_nonblocking_and_keep_hidden_ids(self):
        masks = [mask(1, center=(.2, .5)), mask(2, center=(.25, .5)), mask(3, center=(.27, .5))]
        masks[1]['visible'] = False
        actual = {'masks': masks}; before = copy.deepcopy(actual)
        warnings = context.geometry_proximity(actual)
        self.assertEqual(warnings[0]['mask_ids'], [2, 3])
        self.assertEqual(len(warnings), 3)
        self.assertTrue(all(not row['automatic_rejection'] for row in warnings))
        self.assertEqual(actual, before)

    def test_prospective_create_has_no_invented_id_or_automatic_selection(self):
        actual = {'masks': [mask()]}
        plan = {'action': 'create', 'kind': 'linear',
                'geometry': {'start': [.52, .2], 'end': [.52, .8]}, 'values': {'temp': -4}}
        before = copy.deepcopy((actual, plan))
        rows = context.geometry_proximity(actual, plan)
        self.assertEqual(rows[0]['mask_ids'], [73]); self.assertTrue(rows[0]['prospective'])
        self.assertEqual(rows[0]['prospective_geometry']['midpoint'], [.52, .5])
        self.assertNotIn('id', rows[0]['prospective_geometry'])
        self.assertFalse(rows[0]['automatic_rejection'])
        self.assertEqual((actual, plan), before)
        self.assertEqual(context.geometry_proximity(actual, {'action': 'adjust', 'id': 73}), [])

    def test_radial_long_edge_radii_angle_feather_actual_aspect_not_clamped(self):
        value = state(); value['current'] = 'fork'
        result = context.active_masks_summary(value, observation('fork'), (600, 900))
        geometry = result['selected']['masks'][0]['geometry_display']
        self.assertEqual((geometry['rx'], geometry['ry'], geometry['angle'], geometry['feather']), (1.4, .2, 32, 60))
        self.assertEqual(geometry['radius_units'], 'source_long_edge_fraction')
        self.assertAlmostEqual(geometry['source_dimensions']['aspect_ratio'], 2 / 3)
        absent = context.active_masks_summary(value, observation('fork'))
        self.assertIsNone(absent['selected']['masks'][0]['geometry_display']['source_dimensions'])
        with self.assertRaises(ValueError):
            context.active_masks_summary(value, observation('fork'), (600, 0))

    def test_changes_actual_ids_components_visibility_invert_only(self):
        before = {'masks': [mask()]}; after = copy.deepcopy(before)
        after['masks'][0]['adjust']['temp'] = 90; after['masks'][0]['name'] = 'Not geometry'
        self.assertEqual(context.mask_geometry_changes(before, after), [])
        after['masks'][0]['visible'] = False
        after['masks'][0]['invert'] = True
        after['masks'][0]['components'][0]['shape']['end']['x'] = .7
        row = context.mask_geometry_changes(before, after)[0]
        self.assertEqual(row['mask_id'], 73)
        self.assertEqual(row['changed_fields'], ['components', 'visible', 'invert'])
        self.assertNotIn('adjust', row['after']); self.assertNotIn('name', row['after'])
        self.assertFalse(row['created']); self.assertFalse(row['deleted'])
        row['after']['components'].clear()
        self.assertTrue(after['masks'][0]['components'])

    def test_geometry_changes_creation_deletion_actual_ids_and_validation(self):
        self.assertTrue(context.mask_geometry_changes({'masks': []}, {'masks': [mask(901)]})[0]['created'])
        self.assertTrue(context.mask_geometry_changes({'masks': [mask(901)]}, {'masks': []})[0]['deleted'])
        for masks in ([mask(), mask()], [{**mask(), 'id': True}], [{**mask(), 'id': '73'}]):
            with self.assertRaises(ValueError):
                context.mask_geometry_changes({'masks': masks}, {'masks': []})


class MaskSummaryChecks(unittest.TestCase):
    def test_every_phase_actual_readback_selected_observed_separate(self):
        for phase in ('G1', 'tone', 'G2', 'color', 'G3', 'joint', 'G4', 'complete'):
            value = state(); before = copy.deepcopy(value)
            result = context.active_masks_summary(value, observation('fork', phase))
            self.assertEqual(result['selected']['version'], 'v2')
            self.assertEqual(result['observed']['version'], 'fork')
            self.assertEqual(result['selected']['masks'][0]['id'], 73)
            self.assertEqual(result['observed']['masks'][0]['id'], 91)
            self.assertFalse(result['selected_is_observed'])
            self.assertEqual(value, before)
            result['selected']['masks'][0]['adjust']['temp'] = 999
            self.assertEqual(value, before)

    def test_hidden_zero_complex_kept_and_no_ui_active_selection(self):
        value = state(); hidden = mask(); hidden['visible'] = False; hidden['adjust'] = {'temp': 0}
        complex_mask = mask(91); complex_mask['components'] *= 2
        value['versions']['v2']['settings'] = {'masks': [hidden, complex_mask], 'activeMask': 999}
        rows = context.active_masks_summary(value, observation())['selected']['masks']
        self.assertEqual([row['id'] for row in rows], [73, 91])
        self.assertFalse(rows[0]['visible']); self.assertFalse(rows[1]['simple'])
        self.assertIsNone(rows[1]['geometry_display'])
        self.assertEqual(rows[0]['public_purpose'], 'unknown')

    def test_missing_readback_not_initial_settings_or_semantics(self):
        value = state(); del value['versions']['v2']['settings']
        result = context.active_masks_summary(value, observation())
        self.assertEqual(result['selected']['masks'], [])
        self.assertFalse(result['selected']['readback_available'])

    def test_observation_is_not_reset_and_summary_does_not_append(self):
        value = state(); before = copy.deepcopy(value)
        context.active_masks_summary(value, observation('v0'))
        context.selected_baseline(value, observation('fork'))
        self.assertEqual(value, before)
        self.assertNotIn('branch_reset_events', value)


class BaselineChecks(unittest.TestCase):
    def test_actual_chain_root_first_and_inactive_are_refs_not_failures(self):
        value = state(); before = copy.deepcopy(value)
        baseline = context.selected_baseline(value, observation('fork'))
        self.assertEqual([row['version'] for row in baseline['chain']], ['v0', 'v1', 'v2'])
        self.assertEqual([row['operation_id'] for row in baseline['chain']], [None, 'op1', 'op2'])
        self.assertEqual(baseline['chain'][-1]['changed_controls'], ['wb.tint'])
        self.assertEqual(baseline['source_current_settings'], 'versions[v2].settings')
        self.assertEqual(baseline['excluded_inactive_versions'][0]['version'], 'fork')
        self.assertEqual(baseline['excluded_inactive_operations'][0]['history_record_id'], 'action-2')
        self.assertNotIn('INACTIVE_BODY_', str(baseline))
        self.assertNotIn('light.exposure', str(baseline))
        self.assertIsNone(baseline['branch_reset_notice'])
        self.assertEqual(value, before)

    def test_mismatched_operation_or_parent_cannot_claim_actual_controls(self):
        value = state(); value['history'][1]['operation_id'] = 'not-actual'
        row = context.selected_baseline(value, observation())['chain'][-1]
        self.assertIsNone(row['changed_controls']); self.assertEqual(row['operation_refs'], [])
        value['history'][1]['operation_id'] = 'op2'; value['history'][1]['parent'] = 'fork'
        self.assertIsNone(context.selected_baseline(value, observation())['chain'][-1]['changed_controls'])

    def test_cycles_missing_current_observed_and_missing_parent_rejected(self):
        for version, parent in (('v0', 'v2'), ('v2', 'missing')):
            value = state(); value['versions'][version]['parent'] = parent
            with self.assertRaises(ValueError):
                context.selected_baseline(value, observation())
        with self.assertRaises(ValueError):
            context.selected_baseline(state(), observation('missing'))
        value = state(); value['current'] = None
        with self.assertRaises(ValueError):
            context.active_masks_summary(value, observation())

    def test_only_successful_changed_nondirect_selection_appends(self):
        value = state(); before = copy.deepcopy(value)
        self.assertIsNone(context.record_branch_change(value, 'v2', 'photo_observe', 'obs-v0'))
        self.assertIsNone(context.record_branch_change(value, 'v1', 'photo_edit', 'obs-v1'))
        self.assertEqual(value, before)
        value['current'] = 'v0'
        event = context.record_branch_change(value, 'v2', 'select_parent', 'obs-v2')
        self.assertEqual(event['relation'], 'ancestor'); self.assertEqual(event['id'], 'branch_reset_00001')
        event['relation'] = 'Detached'
        self.assertEqual(value['branch_reset_events'][0]['relation'], 'ancestor')
        notice = context.selected_baseline(value, observation('v2'))['branch_reset_notice']
        self.assertEqual(notice['title'], 'BRANCH RESET NOTICE')
        self.assertFalse(notice['selected_is_observed'])

    def test_descendant_branchswitch_stable_append_and_duplicate_delivery(self):
        value = state()
        first = context.record_branch_change(value, 'v0', 'select_version', 'obs-v0')
        self.assertEqual(first['relation'], 'descendant')
        self.assertEqual(context.record_branch_change(value, 'v0', 'select_version', 'obs-v0'), first)
        self.assertEqual(len(value['branch_reset_events']), 1)
        value['current'] = 'fork'
        second = context.record_branch_change(value, 'v2', 'select_version', 'obs-v2')
        self.assertEqual(second['relation'], 'branch_switch'); self.assertNotEqual(first['id'], second['id'])
        baseline = context.selected_baseline(value, observation('fork'))
        self.assertEqual(baseline['branch_reset_notice']['event']['id'], second['id'])
        self.assertEqual(len(baseline['branch_reset_refs']), 2)
        before = copy.deepcopy(value)
        context.selected_baseline(value, observation('fork'))
        self.assertEqual(value, before)

    def test_event_notice_carried_to_direct_descendant(self):
        value = state(); context.record_branch_change(value, 'fork', 'select_version', 'obs-fork')
        value['versions']['v3'] = {'parent': 'v2', 'operation_id': 'op4', 'settings': {'masks': []}}
        value['current'] = 'v3'
        self.assertIsNone(context.record_branch_change(value, 'v2', 'photo_edit', 'obs-v2'))
        self.assertIsNotNone(context.selected_baseline(value, observation('v3'))['branch_reset_notice'])

    def test_explicit_history_detail_and_unmarked_results_never_folded(self):
        for result in ({'kind': 'history', 'records': [{'id': 'x', 'record': {'reason': 'full'}}]},
                       {'kind': 'history', 'mode': 'detail_get', 'text': 'Full original'},
                       {'kind': 'discover', 'projection_origin': 'internal_advance', 'records': [{'record': 'full'}]}):
            before = copy.deepcopy(result)
            self.assertEqual(context.compact_tool_result(result), before)
            self.assertEqual(result, before)
        result = {'kind': 'history', 'projection_origin': 'internal_advance',
                  'records': [{'id': 'x', 'kind': 'action', 'record': {'reason': 'OLD_BODY'}}]}
        folded = context.compact_tool_result(result)
        self.assertEqual(folded['records'], [{'id': 'x', 'kind': 'action'}])
        self.assertNotIn('OLD_BODY', str(folded)); self.assertIn('OLD_BODY', str(result))


if __name__ == '__main__':
    unittest.main()
