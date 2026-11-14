"""Pure synthetic Tone checks. No photographs, Native, render, model or paid IO."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_tone_guidance as tone


def observation(ident='o1', version='v0', phase='G1', mode='target'):
    return {'id': ident, 'version': version, 'phase': phase, 'comparison_mode': mode,
            'stage_observation': {'phase': phase, 'view_mode': 'grayscale' if phase == 'tone' else 'color'}}


def item(**fields):
    return {'id': 'sky', 'author_region': 'A1 upper blue sky', 'current_region': 'B2 upper sky',
            'material': 'Sky rather than painted material', 'lighting': 'Even twilight',
            'attribution': 'chroma', 'evidence': 'Color gives depth without darker structure',
            'protection': 'Keep the horizon and building edge', 'handoff': 'color', **fields}


def assessment(items=None):
    return {'items': [item()] if items is None else items, 'reason': 'Separate color from tonal structure',
            'neutral_anchors': [{'role': 'black_support', 'author_region': 'D1 dark wall',
                                 'current_region': 'C2 same wall', 'qualification': 'Same diffuse-light material role'}]}


def mask(ident=73, exposure=.3):
    return {'id': ident, 'visible': True, 'invert': False,
            'components': [{'op': 'add', 'shape': {'kind': 'linear', 'start': {'x': .1, 'y': .2}}}],
            'adjust': {'exposure': exposure, 'temp': 0}}


class AssessmentChecks(unittest.TestCase):
    def test_empty_items_and_anchors_are_valid_in_every_color_target_phase(self):
        value = {'items': [], 'reason': 'No qualified mismatch or anchors', 'neutral_anchors': []}
        for phase in ('G1', 'G2', 'color', 'G3', 'joint', 'G4'):
            bound = tone.validate_tone_assessment(value, observation(phase=phase))
            self.assertEqual(bound['items'], [])
            self.assertEqual((bound['observation_id'], bound['settings_version'], bound['phase']), ('o1', 'v0', phase))
        self.assertNotIn('settings_version', value)

    def test_uncertain_and_independent_descriptive_regions_are_not_control_errors(self):
        value = assessment([item(attribution='uncertain', handoff='joint')])
        result = tone.validate_tone_assessment(value, observation())
        self.assertEqual(result['items'][0]['attribution'], 'uncertain')
        self.assertNotIn('unresolved', result['items'][0])
        result['items'][0]['author_region'] = 'Detached'
        self.assertEqual(value['items'][0]['author_region'], 'A1 upper blue sky')

    def test_tone_effect_and_gray_cannot_claim_color_target_even_empty(self):
        gray = observation(); gray['stage_observation']['view_mode'] = 'grayscale'
        for obs in (observation(phase='tone'), observation(mode='effect'), gray, observation(phase='complete')):
            with self.assertRaises(ValueError):
                tone.validate_tone_assessment(assessment([]), obs)

    def test_strict_fields_enums_and_no_numeric_measurement_payload(self):
        bad = []
        for key, value in [('attribution', 'brightness'), ('handoff', 'effect'), ('evidence', ''),
                           ('author_region', {'cell': 'A1', 'pixel_mean': 42})]:
            bad.append(assessment([item(**{key: value})]))
        value = assessment(); value['items'][0]['pixel_measurement'] = 10; bad.append(value)
        value = assessment(); value['settings_version'] = 'forged'; bad.append(value)
        bad.append(assessment([item(), item()]))
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                tone.validate_tone_assessment(value, observation())

    def test_schema_fresh_no_count_gate(self):
        first = tone.tone_assessment_schema(); first['properties'].clear()
        schema = tone.tone_assessment_schema()
        self.assertNotIn('maxItems', schema['properties']['items'])
        self.assertNotIn('minItems', schema['properties']['neutral_anchors'])
        self.assertEqual(schema['properties']['items']['items']['properties']['attribution']['enum'],
                         ['structure', 'chroma', 'uncertain'])


class ContextChecks(unittest.TestCase):
    def state(self):
        obs = observation()
        return {'current': 'v0', 'versions': {'v0': {'parent': None}}, 'observations': [obs],
                'tone_assessments': [tone.validate_tone_assessment(assessment([item(issue_id='color', group_id='blue')]), obs)]}

    def test_g1_color_protection_flows_to_gray_tone_without_claiming_color_vision(self):
        value = self.state(); before = copy.deepcopy(value)
        context = tone.work_context(value, 'tone', observation('o2', phase='tone'))
        latest = context['latest_assessment']
        self.assertTrue(latest['carried_color_basis'])
        self.assertTrue(latest['valid_for_current_version'])
        self.assertFalse(context['tone_sees_color'])
        self.assertEqual(context['protected_regions'][0]['group_id'], 'blue')
        self.assertEqual(context['protected_regions'][0]['handoff'], 'color')
        self.assertEqual(value, before)

    def test_ancestors_are_context_not_actual_version_proof(self):
        value = self.state(); value['versions']['v1'] = {'parent': 'v0'}; value['current'] = 'v1'
        context = tone.work_context(value, 'tone', observation('o2', 'v1', 'tone'))
        self.assertEqual(context['ancestry'], ['v1', 'v0'])
        latest = context['latest_assessment']
        self.assertTrue(latest['on_selected_ancestry'])
        self.assertTrue(latest['stale'])
        self.assertFalse(latest['off_selected_branch'])
        self.assertFalse(latest['valid_for_current_version'])
        self.assertTrue(context['protected_regions'])

    def test_off_branch_and_missing_or_forged_source_never_reusable(self):
        value = self.state(); value['versions']['fork'] = {'parent': 'v0'}
        source = observation('fork-obs', 'fork', 'color'); value['observations'].append(source)
        record = tone.validate_tone_assessment(assessment(), source); value['tone_assessments'].append(record)
        context = tone.work_context(value, 'tone', observation('o2', phase='tone'))
        latest = context['latest_assessment']
        self.assertFalse(latest['off_selected_branch'])
        self.assertEqual(latest['record']['observation_id'], 'o1')
        archived = context['assessment_source_index'][-1]
        self.assertTrue(archived['off_selected_branch'])
        self.assertTrue(archived['body_folded_to_source_index'])
        self.assertNotIn('record', archived)
        self.assertEqual(archived['history_record_id'], 'tone_assessments:1')
        for field, forged in [('observation_id', 'missing'), ('phase', 'G1'), ('target_comparison', False)]:
            value['tone_assessments'][-1] = {**record, field: forged}
            context = tone.work_context(value, 'tone', observation('o2', phase='tone'))
            self.assertFalse(context['assessment_source_index'][-1]['source_binding_valid'])
            self.assertTrue(context['latest_assessment']['source_binding_valid'])

    def test_offbranch_protection_body_is_not_projected_or_deleted(self):
        value = self.state(); value['versions']['fork'] = {'parent': 'v0'}
        source = observation('fork-obs', 'fork', 'G1'); value['observations'].append(source)
        value['tone_assessments'].append(tone.validate_tone_assessment(
            assessment([item(id='fork', protection='OFFBRANCH_BODY_' * 500)]), source))
        before = copy.deepcopy(value)
        context = tone.work_context(value, 'tone', observation('o2', phase='tone'))
        self.assertNotIn('OFFBRANCH_BODY_', str(context))
        self.assertEqual([row['id'] for row in context['protected_regions']], ['sky'])
        self.assertTrue(context['latest_assessment']['carried_color_basis'])
        self.assertEqual(value, before)
        context['protected_regions'][0]['evidence'] = 'Detached'
        self.assertEqual(value, before)

    def test_current_valid_assessment_beats_late_appended_ancestor(self):
        value = self.state(); value['versions']['v1'] = {'parent': 'v0'}; value['current'] = 'v1'
        current = observation('new', 'v1', 'G1'); value['observations'].append(current)
        value['tone_assessments'].append(tone.validate_tone_assessment(assessment(), current))
        value['tone_assessments'].append(copy.deepcopy(value['tone_assessments'][0]))
        context = tone.work_context(value, 'tone', observation('o2', 'v0', 'tone'))
        self.assertEqual(context['latest_assessment']['record']['observation_id'], 'new')
        self.assertTrue(context['latest_assessment']['valid_for_current_version'])
        self.assertFalse(context['latest_assessment']['valid_for_observed_version'])
        self.assertFalse(context['latest_assessment']['reusable_target_assessment'])

    def test_guidance_is_soft_and_empty_history_valid(self):
        value = self.state(); value['tone_assessments'] = []
        context = tone.work_context(value, 'tone', observation('o2', phase='tone'))
        self.assertIsNone(context['latest_assessment'])
        self.assertIn('not a cap', context['step_guidance']['rounds'])
        self.assertIn('2–5', context['step_guidance']['simple_curve'])
        self.assertIn('three tonal anchors', context['step_guidance']['simple_curve'])
        self.assertIn('not chroma', context['step_guidance']['attribution'])
        self.assertTrue(context['local_boundary_review_required'])
        self.assertIn('actual grayscale parent/child now', context['step_guidance']['local_boundary'])
        self.assertIn('G2 then reviews', context['step_guidance']['local_boundary'])

    def test_cycle_rejected(self):
        value = self.state(); value['versions']['v0']['parent'] = 'v0'
        with self.assertRaises(ValueError):
            tone.work_context(value, 'tone', observation('o2', phase='tone'))


class DifferenceChecks(unittest.TestCase):
    def test_actual_created_tone_mask_id_not_requested_or_guessed_id(self):
        args = {'mask_plan': {'action': 'create', 'values': {'exposure': .3}}}
        expected = {'protected_region': 'Horizon edge', 'risk': 'Spill onto buildings', 'falsifier': 'Visible halo'}
        result = tone.tone_boundary_detail({'masks': []}, {'masks': [mask(901)]}, args, {'effect_expectation': expected})
        self.assertEqual(result['mask_ids'], [901])
        self.assertTrue(result['changes'][0]['created'])
        for key in expected:
            self.assertEqual(result[key], expected[key])

    def test_tone_adjustment_geometry_visibility_invert_and_delete_are_actual(self):
        original = {'masks': [mask()]}
        for field, change in [('adjust', {'exposure': .6}), ('components', []), ('visible', False), ('invert', True)]:
            after = copy.deepcopy(original); after['masks'][0][field] = change
            result = tone.tone_boundary_detail(original, after, {}, {})
            self.assertEqual(result['mask_ids'], [73])
            self.assertIn('adjust.exposure' if field == 'adjust' else field, result['changes'][0]['changed_fields'])
        result = tone.tone_boundary_detail(original, {'masks': []}, {}, {})
        self.assertTrue(result['changes'][0]['deleted'])

    def test_no_change_global_only_and_color_only_mask_have_no_boundary_fact(self):
        original = {'masks': [mask()]}
        self.assertIsNone(tone.tone_boundary_detail(original, copy.deepcopy(original), {}, {}))
        self.assertIsNone(tone.tone_boundary_detail({'light': {'exposure': 0}}, {'light': {'exposure': .2}}, {}, {}))
        color = mask(exposure=0); changed = copy.deepcopy(color); changed['adjust']['temp'] = 4
        self.assertIsNone(tone.tone_boundary_detail({'masks': [color]}, {'masks': [changed]}, {}, {}))
        changed['components'] = []
        self.assertIsNone(tone.tone_boundary_detail({'masks': [color]}, {'masks': [changed]}, {}, {}))

    def test_neutral_tone_creation_still_has_an_actual_boundary(self):
        result = tone.tone_boundary_detail({'masks': []}, {'masks': [mask(82, 0)]},
                 {'mask_plan': {'action': 'create', 'values': {'exposure': 0}}}, {})
        self.assertEqual(result['mask_ids'], [82])

    def test_mixer_luminance_actual_finite_delta_only(self):
        before = {'mixer': {'blue': {'lum': 0, 'sat': 2}, 'red': {'lum': 3}}}
        after = {'mixer': {'blue': {'lum': -7, 'sat': 20}, 'red': {'lum': 3}, 'green': {'lum': 8}}}
        result = tone.color_luminance_detail(before, after)
        self.assertEqual([(r['control_id'], r['delta']) for r in result], [('mixer.blue.lum', -7)])
        self.assertEqual(tone.color_luminance_detail(before, before), [])
        self.assertEqual(tone.color_luminance_detail({}, after), [])
        self.assertEqual(tone.color_luminance_detail({'mixer': {'blue': {'lum': 0}}},
                         {'mixer': {'blue': {'lum': float('nan')}}}), [])

    def test_actual_mask_ids_must_be_unique_integers(self):
        for masks in ([mask(), mask()], [{**mask(), 'id': '73'}]):
            with self.assertRaises(ValueError):
                tone.tone_boundary_detail({'masks': []}, {'masks': masks}, {}, {})


if __name__ == '__main__':
    unittest.main()
