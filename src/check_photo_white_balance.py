"""Pure synthetic WB checks. No photographs, RAW, model, Native, hash or paid IO."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_white_balance as wb


def observation(ident='o1', version='v0', phase='G2', mode='target'):
    return {'id': ident, 'version': version, 'phase': phase, 'comparison_mode': mode,
            'stage_observation': {'phase': phase, 'view_mode': 'grayscale' if phase == 'tone' else 'color'}}


def assessment():
    return {'reference_status': 'not_available', 'reference_pairs': [],
            'target_intent': 'intentional_cast', 'intent_reason': 'Preserve the author warm lighting.',
            'atmosphere': {'route': 'grading', 'reason': 'Use grading for the intended atmosphere.'},
            'wb_task_status': 'needed', 'reason': 'A reliable neutral is unavailable, but a yellow/green residual remains.'}


def pair():
    return {'author_cell': 'A1', 'current_cell': 'C3', 'material': 'Light painted wall',
            'lighting': 'Comparable diffuse light', 'neutrality_basis': 'Known paint, not warm reflected light',
            'temperature_residual': 'Current is slightly yellower', 'tint_residual': 'Current is greener'}


def mask(ident=73, kind='linear'):
    return {'id': ident, 'name': 'Name is not semantic proof', 'visible': True, 'invert': False,
            'components': [{'op': 'add', 'invert': False, 'shape': {'kind': kind}}],
            'adjust': {'temp': -8, 'tint': 0, 'exposure': .3}}


def settings(temp=6000, tint=8):
    return {'wb': {'mode': 'custom', 'scale': 'kelvin', 'temp': temp, 'tint': tint}, 'masks': []}


class AssessmentChecks(unittest.TestCase):
    def test_no_reference_can_need_wb_and_intent_need_not_be_neutral(self):
        value, obs = assessment(), observation()
        bound = wb.validate_assessment(value, obs, require_target=True)
        self.assertEqual(bound['wb_task_status'], 'needed')
        self.assertEqual(bound['reference_pairs'], [])
        self.assertEqual((bound['observation_id'], bound['settings_version'], bound['phase']), ('o1', 'v0', 'G2'))
        self.assertEqual(bound['target_intent'], 'intentional_cast')
        self.assertNotIn('observation_id', value)
        self.assertNotIn('issue_id', bound)

    def test_available_one_or_two_independent_cells_and_lossless_narrative(self):
        for count in (1, 2):
            value = assessment(); value.update(reference_status='available', reference_pairs=[pair() for _ in range(count)])
            value['reason'] = 'Evidence ' * 300
            result = wb.validate_assessment(value, observation(), require_target=True)
            self.assertEqual(result['reason'], value['reason'])
            self.assertEqual(result['reference_pairs'][0]['author_cell'], 'A1')
            self.assertEqual(result['reference_pairs'][0]['current_cell'], 'C3')
            result['reference_pairs'][0]['material'] = 'Detached'
            self.assertEqual(value['reference_pairs'][0]['material'], 'Light painted wall')

    def test_invalid_counts_cells_and_missing_reason_are_rejected(self):
        bad = []
        for status, pairs in [('available', []), ('not_available', [pair()]), ('available', [pair()] * 3)]:
            value = assessment(); value.update(reference_status=status, reference_pairs=pairs); bad.append(value)
        for field in ('author_cell', 'current_cell'):
            value = assessment(); p = pair(); p[field] = 'A5'
            value.update(reference_status='available', reference_pairs=[p]); bad.append(value)
        for key in ('intent_reason', 'reason'):
            value = assessment(); value[key] = '  '; bad.append(value)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                wb.validate_assessment(value, observation())

    def test_host_source_cannot_be_spoofed(self):
        for key in ('observation_id', 'settings_version', 'phase', 'target_comparison'):
            value = assessment(); value[key] = 'invented'
            with self.assertRaises(ValueError):
                wb.validate_assessment(value, observation())

    def test_effect_and_gray_never_qualify_as_target(self):
        for obs in (observation(mode='effect'), observation(phase='tone')):
            with self.assertRaises(ValueError):
                wb.validate_assessment(assessment(), obs, require_target=True)
            bound = wb.validate_assessment(assessment(), obs)
            self.assertFalse(bound['target_comparison'])
            value = assessment(); value.update(reference_status='available', reference_pairs=[pair()])
            with self.assertRaises(ValueError):
                wb.validate_assessment(value, obs)

    def test_schema_is_fresh_and_optional_ids_do_not_create_work(self):
        schema = wb.assessment_schema(); schema['properties'].clear()
        self.assertIn('reference_pairs', wb.assessment_schema()['properties'])
        value = assessment(); value.update(issue_id='i', group_id='g')
        result = wb.validate_assessment(value, observation())
        self.assertEqual((result['issue_id'], result['group_id']), ('i', 'g'))


class LocalReviewChecks(unittest.TestCase):
    def test_only_this_color_joint_local_wb_action_requires_review(self):
        for phase in ('G2', 'tone'):
            self.assertIsNone(wb.validate_local_wb_review({}, {'mask_plan': {'action': 'create', 'values': {'temp': 1}}},
                                                        {}, phase, observation()))
        for args in ({'values': {'wb.tint': 1}}, {'mask_plan': {'action': 'adjust', 'id': 73, 'values': {'exposure': .4}}},
                     {'commands': [{'id': 'mask.visible', 'params': {'id': 73, 'visible': True}}]}):
            self.assertIsNone(wb.validate_local_wb_review({}, args, {}, 'joint', observation(phase='joint')))
        with self.assertRaises(ValueError):
            wb.validate_local_wb_review({}, {'mask_plan': {'action': 'create', 'values': {'temp': 0}}},
                                        settings(), 'color', observation(phase='color'))

    def test_reuse_exact_id_for_plan_or_command_and_bind_source(self):
        current = settings(); current['masks'] = [mask()]
        review = {'wb_local_review': {'region': 'Upper sky', 'decision': 'reuse', 'mask_id': 73,
                                      'reason': 'Existing mask has the same lighting region and purpose.'}}
        for args in ({'mask_plan': {'action': 'adjust', 'id': 73, 'values': {'temp': -10}}},
                     {'commands': [{'id': 'mask.adjust', 'params': {'id': 73, 'values': {'tint': 3}}}]}):
            result = wb.validate_local_wb_review(review, args, current, 'color', observation(phase='color'))
            self.assertEqual(result['mask_id'], 73)
            self.assertEqual(result['settings_version'], 'v0')
            result['actual_local_actions'][0]['values'].clear()
            self.assertTrue((args.get('mask_plan') or args['commands'][0]['params'])['values'])

    def test_create_considers_existing_ids_and_reason_not_geometry(self):
        current = settings(); current['masks'] = [mask(73), mask(91, 'radial')]
        args = {'mask_plan': {'action': 'create', 'values': {'temp': -12}}}
        review = {'wb_local_review': {'region': 'Sky', 'decision': 'create', 'existing_mask_ids': [73, 91],
                                      'reason': '73 protects bright foreground; 91 isolates a face, neither covers sky lighting.'}}
        result = wb.validate_local_wb_review(review, args, current, 'joint', observation(phase='joint'))
        self.assertEqual(result['existing_mask_ids'], [73, 91])
        for ids in ([], [73], [73, 73, 91], [73, 91, 999], [True, 91]):
            bad = copy.deepcopy(review); bad['wb_local_review']['existing_mask_ids'] = ids
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                wb.validate_local_wb_review(bad, args, current, 'joint', observation(phase='joint'))
        current['masks'] = []
        review['wb_local_review']['existing_mask_ids'] = []
        self.assertEqual(wb.validate_local_wb_review(review, args, current, 'joint', observation(phase='joint'))['decision'], 'create')

    def test_no_invented_reuse_id_inversion_or_create_decision_for_adjust(self):
        current = settings(); current['masks'] = [mask()]
        args = {'mask_plan': {'action': 'adjust', 'id': 73, 'values': {'tint': 2}}}
        for ident in (999, '73', True):
            review = {'wb_local_review': {'region': 'Sky', 'decision': 'reuse', 'mask_id': ident, 'reason': 'Public purpose.'}}
            with self.assertRaises(ValueError):
                wb.validate_local_wb_review(review, args, current, 'joint', observation(phase='joint'))
        review = {'wb_local_review': {'region': 'Sky', 'decision': 'create', 'existing_mask_ids': [73], 'reason': 'Different purpose.'}}
        with self.assertRaises(ValueError):
            wb.validate_local_wb_review(review, args, current, 'joint', observation(phase='joint'))
        review['wb_local_review'].update(decision='reuse', mask_id=73)
        current['masks'][0]['invert'] = True
        with self.assertRaises(ValueError):
            wb.validate_local_wb_review(review, args, current, 'joint', observation(phase='joint'))

    def test_review_cannot_cover_two_distinct_mask_ids(self):
        current = settings(); current['masks'] = [mask(73), mask(91)]
        args = {'commands': [{'id': 'mask.adjust', 'params': {'id': ident, 'values': {'tint': 1}}} for ident in (73, 91)]}
        review = {'wb_local_review': {'region': 'Sky', 'decision': 'reuse', 'mask_id': 73, 'reason': 'One region.'}}
        with self.assertRaises(ValueError):
            wb.validate_local_wb_review(review, args, current, 'joint', observation(phase='joint'))


class CouplingChecks(unittest.TestCase):
    def test_actual_delta_threshold_and_not_submission_magnitude(self):
        before = settings()
        for temp in (6000, 6250, 6400):
            self.assertIsNone(wb.coupling_detail(before, settings(temp), {'values': {'wb.temp': 10000}}))
        detail = wb.coupling_detail(before, settings(6401), {})
        self.assertEqual(detail['global']['delta']['temp'], 401)
        self.assertEqual(detail['mask_ids'], [])
        self.assertIn('kelvin_temperature_delta_gt_400', detail['triggers'])
        self.assertIsNone(wb.coupling_detail(before, before, {'values': {'wb.tint': 0}}))
        self.assertEqual(wb.coupling_detail(settings(tint=8), settings(tint=0), {})['global']['delta']['tint'], -8)

    def test_tint_mode_scale_picker_and_legacy_units(self):
        before = settings(); after = copy.deepcopy(before); after['wb']['mode'] = 'as_shot'
        self.assertIn('mode_change', wb.coupling_detail(before, after, {})['triggers'])
        before['wb']['scale'] = 'legacy'; after = copy.deepcopy(before); after['wb']['temp'] += 1000
        self.assertIsNone(wb.coupling_detail(before, after, {}))
        action = {'arguments': {'commands': [{'id': 'develop.wbPick', 'params': {'x': .2, 'y': .3}}]}}
        self.assertIn('picker_actual_wb_change', wb.coupling_detail(before, after, action)['triggers'])
        self.assertIsNone(wb.coupling_detail(before, before, action))

    def test_local_actual_ids_creation_deletion_zero_and_preserved_axes(self):
        before = settings(); before['masks'] = [mask(73)]
        after = copy.deepcopy(before); after['masks'][0]['adjust']['tint'] = 4
        detail = wb.coupling_detail(before, after, {})
        self.assertEqual(detail['mask_ids'], [73]); self.assertEqual(detail['local'][0]['delta'], {'temp': 0, 'tint': 4})
        self.assertEqual(before['masks'][0]['adjust']['tint'], 0)
        after = copy.deepcopy(before); after['masks'][0]['adjust']['exposure'] = 1
        self.assertIsNone(wb.coupling_detail(before, after, {}))
        after['masks'].append(mask(999))
        self.assertTrue(wb.coupling_detail(before, after, {})['local'][0]['created'])
        self.assertTrue(wb.coupling_detail(after, before, {})['local'][0]['deleted'])
        before['masks'] = []; after = copy.deepcopy(before); zero = mask(99); zero['adjust'].update(temp=0, tint=0); after['masks'] = [zero]
        self.assertIsNone(wb.coupling_detail(before, after, {}))


class ContextChecks(unittest.TestCase):
    def test_actual_observed_and_current_versions_override_stale_settings(self):
        obs = observation('o2', 'v1', 'color')
        state = {'current': 'v2', 'versions': {'v0': {'settings': settings(5000)},
                 'v1': {'parent': 'v0', 'settings': settings(6500)}, 'v2': {'parent': 'v1', 'settings': settings(6700)}},
                 'observations': [obs]}
        before = copy.deepcopy(state)
        context = wb.work_context(state, 'color', settings(5000), obs)
        self.assertEqual(context['observed']['temp'], 6500)
        self.assertEqual(context['actual_current']['temp'], 6700)
        self.assertIn('not a cap', context['step_guidance']['binding'])
        self.assertIn('negative Tint moves toward green', context['step_guidance']['tint'])
        self.assertEqual(state, before)

    def test_latest_assessment_validity_phase_version_and_effect_distinction(self):
        target = observation(); effect = observation('o2', 'v1', 'color', 'effect')
        state = {'current': 'v1', 'versions': {'v0': {'settings': settings()}, 'v1': {'parent': 'v0', 'settings': settings(6200)}},
                 'observations': [target, effect], 'wb_assessments': [wb.validate_assessment(assessment(), target)]}
        latest = wb.work_context(state, 'color', {}, effect)['latest_assessment']
        self.assertTrue(latest['source_binding_valid']); self.assertFalse(latest['valid_for_current_version'])
        self.assertFalse(latest['same_phase']); self.assertFalse(latest['reusable_target_assessment'])
        state['wb_assessments'].append(wb.validate_assessment(assessment(), effect))
        latest = wb.work_context(state, 'color', {}, effect)['latest_assessment']
        self.assertTrue(latest['valid_for_current_version']); self.assertFalse(latest['source_is_target_comparison'])
        self.assertFalse(latest['reusable_target_assessment'])
        del state['observations'][-1]
        self.assertFalse(wb.work_context(state, 'color', {}, effect)['latest_assessment']['source_binding_valid'])

    def test_g2_basis_reused_in_color_only_on_exact_same_version(self):
        g2 = observation(); color = observation('o2', 'v0', 'color')
        state = {'current': 'v0', 'versions': {'v0': {'settings': settings()}},
                 'observations': [g2, color], 'wb_assessments': [wb.validate_assessment(assessment(), g2)]}
        latest = wb.work_context(state, 'color', {}, color)['latest_assessment']
        self.assertFalse(latest['same_phase']); self.assertTrue(latest['phase_compatible'])
        self.assertTrue(latest['reusable_target_assessment']); self.assertEqual(latest['record']['phase'], 'G2')
        state['versions']['v1'] = {'parent': 'v0', 'settings': settings(6200)}; state['current'] = 'v1'
        newer = observation('o3', 'v1', 'color'); state['observations'].append(newer)
        latest = wb.work_context(state, 'color', {}, newer)['latest_assessment']
        self.assertFalse(latest['valid_for_current_version']); self.assertFalse(latest['reusable_target_assessment'])
        self.assertFalse(wb.work_context(state, 'joint', {}, newer)['latest_assessment']['phase_compatible'])

    def test_mask_semantics_from_actual_ancestor_usage_only(self):
        obs = observation('o2', 'v1', 'joint')
        current = settings(); current['masks'] = [mask(73), mask(91)]
        state = {'current': 'v1', 'versions': {'v0': {'settings': settings()}, 'v1': {'parent': 'v0', 'settings': current},
                  'vOther': {'parent': 'v0', 'settings': current}}, 'observations': [obs],
                 'wb_mask_usage': [{'actual_mask_id': 73, 'child_version': 'v1', 'region': 'Sky', 'decision': 'create', 'reason': 'Regional lighting residual.'},
                                   {'mask_id': 91, 'child_version': 'vOther', 'region': 'Wrong branch'}]}
        rows = wb.work_context(state, 'joint', {}, obs)['simple_masks']
        self.assertEqual(rows[0]['id'], 73)
        self.assertEqual(rows[0]['public_purpose'][0]['region'], 'Sky')
        self.assertEqual(rows[1]['public_purpose'], 'unknown')
        self.assertEqual(rows[0]['adjust'], current['masks'][0]['adjust'])
        self.assertEqual(rows[0]['shape'], current['masks'][0]['components'])

    def test_historical_purpose_needs_confirmation_after_mask_settings_change(self):
        old = settings(); old['masks'] = [mask(73)]
        current = copy.deepcopy(old); current['masks'][0]['visible'] = False
        current['masks'][0]['components'][0]['shape']['kind'] = 'radial'
        obs = observation('o3', 'v2', 'joint')
        state = {'current': 'v2', 'versions': {'v0': {'settings': settings()},
                 'v1': {'parent': 'v0', 'settings': old}, 'v2': {'parent': 'v1', 'settings': current}},
                 'observations': [obs], 'wb_mask_usage': [{'actual_mask_id': 73, 'child_version': 'v1',
                 'region': 'Sky', 'reason': 'Historical purpose only.'}]}
        row = wb.work_context(state, 'joint', {}, obs)['simple_masks'][0]
        self.assertTrue(row['purpose_requires_confirmation'])
        self.assertTrue(row['historical_purpose_not_current_fact'])
        self.assertTrue(row['settings_changed_since_last_purpose'])
        self.assertEqual(row['public_purpose'][0]['region'], 'Sky')

    def test_purpose_keeps_nearest_body_and_all_source_refs_without_state_change(self):
        current = settings(); current['masks'] = [mask(73)]
        obs = observation('o3', 'v2', 'joint')
        state = {'current': 'v2', 'versions': {'v0': {'settings': settings()},
                 'v1': {'parent': 'v0', 'settings': current}, 'v2': {'parent': 'v1', 'settings': current}},
                 'observations': [obs], 'wb_mask_usage': [
                     {'actual_mask_id': 73, 'child_version': 'v2', 'region': 'Current sky', 'reason': 'Recent purpose'},
                     {'actual_mask_id': 73, 'child_version': 'v1', 'region': 'OLD_BODY_' * 500}]}
        before = copy.deepcopy(state)
        context = wb.work_context(state, 'joint', {}, obs)
        row = context['simple_masks'][0]
        self.assertEqual(len(row['public_purpose']), 1)
        self.assertEqual(row['public_purpose'][0]['region'], 'Current sky')
        self.assertEqual(row['purpose_record_count'], 2)
        self.assertEqual({ref['history_record_id'] for ref in row['purpose_refs']},
                         {'wb_mask_usage:0', 'wb_mask_usage:1'})
        self.assertNotIn('OLD_BODY_', str(context))
        self.assertEqual(state, before)
        row['public_purpose'][0]['region'] = 'Detached'
        self.assertEqual(state, before)

    def test_hidden_complex_masks_keep_actual_identity(self):
        current = settings(); hidden = mask(73); hidden['visible'] = False
        complex_mask = mask(91); complex_mask['components'].append(copy.deepcopy(complex_mask['components'][0]))
        current['masks'] = [hidden, complex_mask]
        obs = observation()
        state = {'current': 'v0', 'versions': {'v0': {'settings': current}}, 'observations': [obs]}
        rows = wb.work_context(state, 'G2', {}, obs)['simple_masks']
        self.assertEqual([row['id'] for row in rows], [73, 91])
        self.assertFalse(rows[0]['visible']); self.assertFalse(rows[1]['simple'])
        self.assertEqual(rows[1]['components'], complex_mask['components'])

    def test_history_creation_provenance_no_guess_from_name_or_overlap(self):
        obs = observation('o2', 'v1', 'joint'); current = settings(); current['masks'] = [mask(73), mask(91)]
        previous = copy.deepcopy(current); previous['masks'] = [mask(91)]
        state = {'current': 'v1', 'versions': {'v0': {'settings': previous}, 'v1': {'parent': 'v0', 'settings': current}},
                 'observations': [obs], 'history': [{'action': 'edit', 'parent': 'v0', 'version': 'v1', 'operation_id': 'op1',
                 'arguments': {'mask_plan': {'action': 'create', 'values': {'temp': -8}}, 'rationale': 'Sky lighting residual.'}}]}
        rows = wb.work_context(state, 'joint', {}, obs)['simple_masks']
        self.assertTrue(rows[0]['public_purpose'][0]['created'])
        self.assertEqual(rows[0]['public_purpose'][0]['operation_id'], 'op1')
        self.assertEqual(rows[1]['public_purpose'], 'unknown')


if __name__ == '__main__':
    unittest.main()
