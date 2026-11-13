"""Focused offline task/bank tests. No model, Native, render, hash or run IO."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_phase_scheduler as scheduler
import photo_proposal_bank as bank
import photo_stage_tasks as tasks
from photo_stage_rejections import ActionRejected
from photo_stage_schema import stage_edit_schema


def state(phase='tone'):
    value = {'current': 'v0', 'versions': {'v0': {'parent': None}}, 'status': 'working', 'observations': []}
    scheduler.init_workflow(value)
    value['workflow']['phase'] = phase
    return value


def seen(value, version=None):
    ident = 'o' + str(len(value['observations']) + 1)
    value['observations'].append({'id': ident, 'version': version or value['current'],
                                 'decision_judgment': {'visible_comparison': 'Offline public comparison'}})
    return ident


def add(value, ident='i', controls=None, branch='tone', **fields):
    bank.apply_bank_delta(value, [{'op': 'add', 'id': ident, 'branch': branch, 'diagnosis': 'Public diagnosis',
                                  'proposed_controls': controls if controls is not None else ['light.exposure'], **fields}], seen(value))
    return value['proposal_bank']['issues'][ident]


def groups(value, phase=None):
    return bank.branch_workflow(value, phase or value['workflow']['phase'])['groups']


class TaskChecks(unittest.TestCase):
    def test_registry_and_schema_share_soft_order(self):
        expected = ['exposure', 'contrast', 'highlights_shadows', 'whites_blacks', 'master_curve', 'parametric_curve', 'local_tone', 'closure']
        self.assertEqual([r['step'] for r in tasks.registry('tone')], expected)
        keys = list(stage_edit_schema('tone')['values']['properties'])
        self.assertLess(keys.index('light.exposure'), keys.index('light.contrast'))
        self.assertLess(keys.index('light.blacks'), keys.index('curve.highlights'))
        self.assertIn('not an execution gate', stage_edit_schema()['values']['description'])

    def test_mixed_native_values_settings_and_local_paths(self):
        value = state()
        row = add(value, controls={'values': {'light.exposure': .2, 'wb.temp': 6000},
                                   'settings': {'mixer': {'blue': {'sat': -10}}}})
        self.assertEqual({g['step'] for g in row['task_groups'].values()}, {'exposure', 'wb', 'hsl_blue'})
        self.assertTrue(any('values.light.exposure' in path for g in row['task_groups'].values() for path in g['source_paths']))
        self.assertEqual(bank.resolve_selected_group(value, 'i', None, 'v0')['step'], 'exposure')
        value['workflow']['phase'] = 'color'
        with self.assertRaises(ActionRejected):
            bank.resolve_selected_group(value, 'i', None, 'v0')
        wb = next(g for g in groups(value) if g['step'] == 'wb')
        self.assertEqual(bank.resolve_selected_group(value, 'i', wb['id'], 'v0')['step'], 'wb')

    def test_stable_id_no_values_or_hash_and_original_snapshots(self):
        value = state(); original = {'values': {'light.exposure': .1}}
        row = add(value, controls=original); gid = next(iter(row['task_groups']))
        bank.apply_bank_delta(value, [{'op': 'revise', 'id': 'i', 'proposed_controls': {'values': {'light.exposure': .4}}}], seen(value))
        row = value['proposal_bank']['issues']['i']
        self.assertEqual(list(row['task_groups']), [gid])
        self.assertEqual(row['records'][0]['proposed_controls'], original)
        self.assertEqual(len(row['task_groups'][gid]['records']), 2)

    def test_multi_ready_needs_explicit_selection_even_matching_action(self):
        value = state(); row = add(value, controls=['light.exposure', 'light.contrast'])
        before = copy.deepcopy(value)
        with self.assertRaises(ActionRejected) as caught:
            bank.resolve_selected_group(value, 'i', None, 'v0', {'values': {'light.exposure': .2}})
        self.assertEqual(caught.exception.code, 'group_selection_required')
        self.assertEqual(value, before)
        gid = next(g['id'] for g in row['task_groups'].values() if g['step'] == 'exposure')
        selected = bank.resolve_selected_group(value, 'i', gid, 'v0', {'values': {'light.exposure': .8}})
        self.assertEqual(selected['id'], gid)
        with self.assertRaises(ActionRejected):
            bank.resolve_selected_group(value, 'i', gid, 'v0', {'values': {'light.exposure': .8, 'light.contrast': 5}})

    def test_unknown_structure_and_dependencies_do_not_block_unrelated(self):
        value = state()
        add(value, 'unknown', ['not.a.control'])
        add(value, 'blocked', dependencies=['missing'])
        add(value, 'ok')
        statuses = {g['issue_id']: g['status'] for g in groups(value)}
        self.assertEqual(statuses, {'unknown': 'unresolved', 'blocked': 'waiting_dependency', 'ok': 'ready'})
        self.assertIsNotNone(bank.resolve_selected_group(value, 'ok', None, 'v0'))
        with self.assertRaises(ActionRejected):
            bank.resolve_selected_group(value, 'unknown', None, 'v0')
        unknown_gid = next(iter(value['proposal_bank']['issues']['unknown']['task_groups']))
        bank.apply_bank_delta(value, [{'id': 'unknown', 'group_id': unknown_gid, 'op': 'keep'}], seen(value))
        with self.assertRaises(ActionRejected):
            bank.resolve_selected_group(value, 'unknown', unknown_gid, 'v0')
        bank.apply_bank_delta(value, [{'id': 'blocked', 'op': 'revise', 'dependencies': []}], seen(value))
        self.assertIsNotNone(bank.resolve_selected_group(value, 'blocked', None, 'v0'))

    def test_cycle_and_dependency_revision_recover_without_global_deadlock(self):
        value = state(); add(value, 'a', dependencies=['b']); add(value, 'b', dependencies=['a']); add(value, 'c')
        selected = {g['issue_id']: g for g in groups(value)}
        self.assertIn('Cyclic', selected['a']['wait_reason'])
        self.assertEqual(selected['c']['status'], 'ready')
        bank.apply_bank_delta(value, [{'id': 'a', 'op': 'revise', 'dependencies': []}], seen(value))
        self.assertEqual(next(g for g in groups(value) if g['issue_id'] == 'a')['status'], 'ready')

    def test_dependency_short_ids_resolve_only_in_same_issue_and_ambiguity_waits(self):
        value = state()
        row = add(value, groups=[{'group_id': 'first', 'proposed_controls': ['light.contrast']},
            {'group_id': 'second', 'proposed_controls': ['light.blacks'], 'dependencies': ['first']}])
        second = row['task_groups']['i::second']
        self.assertEqual(second['dependencies'], ['first'])
        self.assertEqual(second['dependency_references'], [{'original': 'first', 'resolved': 'i::first', 'resolution': 'resolved'}])
        self.assertEqual(next(g for g in groups(value) if g['id'] == 'i::second')['status'], 'waiting_dependency')
        bank.record_group_review(value, {'issue_id': 'i', 'group_id': 'first', 'outcome': 'resolved', 'reason': 'Explicit closure'}, seen(value))
        self.assertEqual(bank.resolve_selected_group(value, 'i', 'second', 'v0')['status'], 'ready')
        add(value, 'other', groups=[{'group_id': 'third', 'proposed_controls': ['light.exposure'], 'dependencies': ['first']}])
        other = next(g for g in groups(value) if g['issue_id'] == 'other')
        self.assertEqual(other['status'], 'waiting_dependency', 'Never infer another issue short ID')
        self.assertEqual(other['dependency_references'][0]['resolution'], 'unresolved')
        add(value, 'split', groups=[{'group_id': 'ambiguous', 'proposed_controls': ['light.exposure', 'light.contrast']},
            {'group_id': 'blocked', 'proposed_controls': ['light.blacks'], 'dependencies': ['ambiguous']}])
        blocked = next(g for g in groups(value) if g['id'] == 'split::blocked')
        self.assertEqual(blocked['status'], 'waiting_dependency')
        self.assertIn('Ambiguous dependency reference', blocked['wait_reason'])
        self.assertEqual(blocked['dependency_references'][0]['resolution'], 'ambiguous')
        self.assertEqual(next(g for g in groups(value) if g['id'] == 'split::ambiguous/tone/light/exposure')['status'], 'ready')

    def test_waiting_review_applies_priority_deps_then_review_keep_is_same_turn_ready(self):
        value = state(); add(value, groups=[{'group_id': 'first', 'proposed_controls': ['light.contrast']},
                                           {'group_id': 'second', 'proposed_controls': ['light.blacks']}])
        observation = seen(value)
        bank.record_group_review(value, {'group_reviews': [
            {'issue_id': 'i', 'group_id': 'second', 'outcome': 'waiting_dependency', 'reason': 'Wait on actual first effect',
             'priority': 60, 'dependencies': ['first'], 'operation_id': 'future-op'}]}, observation)
        second = value['proposal_bank']['issues']['i']['task_groups']['i::second']
        self.assertEqual(second['priority'], 60)
        self.assertEqual(second['dependency_references'][0]['resolved'], 'i::first')
        bank.record_group_review(value, {'issue_id': 'i', 'group_id': 'first', 'outcome': 'resolved', 'reason': 'Explicitly accepted actual evidence'}, observation)
        bank.apply_bank_delta(value, [{'id': 'i', 'group_id': 'second', 'op': 'keep'}], observation)
        self.assertEqual(bank.resolve_selected_group(value, 'i', 'second', 'v0')['status'], 'ready')
        self.assertEqual(value['proposal_reference_warnings'][0]['metadata_reference']['original_id'], 'future-op')

    def test_disposition_existing_operation_never_changes_actual_outcome(self):
        for outcome in ('keep', 'revise', 'retire', 'defer', 'resolved', 'needs_reassessment', 'waiting_dependency'):
            with self.subTest(outcome=outcome):
                value = state(); row = add(value); gid = next(iter(row['task_groups']))
                value['versions']['v1'] = {'parent': 'v0', 'operation_id': 'op1'}; value['current'] = 'v1'
                bank.record_operation(value, 'i', 'op1', 'v0', 'v1', group_id=gid, action={'values': {'light.exposure': .1}})
                bank.record_group_review(value, {'issue_id': 'i', 'group_id': gid, 'outcome': outcome,
                    'reason': 'Proposal disposition only', 'operation_id': 'op1'}, seen(value))
                issue = value['proposal_bank']['issues']['i']
                self.assertEqual(issue['operations'][0]['outcome'], 'unreviewed')
                self.assertEqual(issue['task_groups'][gid]['operations'][0]['outcome'], 'unreviewed')
                self.assertEqual(next(e for e in value['proposal_bank']['events'] if e['kind'] == 'operation')['outcome'], 'unreviewed')
                self.assertEqual(issue['task_groups'][gid]['last_review']['metadata_reference']['resolution'], 'not_adopted')

    def test_effect_group_mismatch_is_typed_and_leaves_bank_unchanged(self):
        value = state(); row = add(value); gid = next(iter(row['task_groups']))
        value['versions']['v1'] = {'parent': 'v0', 'operation_id': 'op1'}; value['current'] = 'v1'
        bank.record_operation(value, 'i', 'op1', 'v0', 'v1', group_id=gid, action={'values': {'light.exposure': .1}})
        value['effect_expectations'] = [{'id': 'e1', 'operation_id': 'op1', 'child_version': 'v1'}]
        observation = seen(value); before = copy.deepcopy(value)
        with self.assertRaises(ActionRejected) as caught:
            bank.record_group_review(value, {'issue_id': 'i', 'group_id': gid, 'outcome': 'improved',
                'operation_id': 'future', 'reason': 'Invalid actual-effect reference'}, observation)
        self.assertEqual(caught.exception.code, 'group_effect_mismatch')
        self.assertEqual(value, before)

    def test_joint_not_forced_back_to_early_phase(self):
        value = state('joint'); row = add(value, branch='joint', controls={'values': {'light.exposure': .3, 'color.saturation': 4}})
        self.assertTrue(all(g['branch'] == 'joint' for g in row['task_groups'].values()))
        gid = next(g['id'] for g in row['task_groups'].values() if g['module'] == 'light')
        self.assertEqual(bank.resolve_selected_group(value, 'i', gid, 'v0', {'values': {'light.exposure': .1}})['branch'], 'joint')

    def test_explicit_joint_hypothesis_requires_reason_not_parameter_count(self):
        value = state('joint')
        row = add(value, branch='joint', groups=[{'group_id': 'coupled', 'branch': 'joint', 'selection_reason': 'One coupled residual',
                    'proposed_controls': {'values': {'light.exposure': .2, 'wb.tint': 5}}}])
        gid = next(iter(row['task_groups']))
        result = bank.resolve_selected_group(value, 'i', gid, 'v0', {'values': {'light.exposure': .5, 'wb.tint': 3}})
        self.assertEqual(result['id'], gid)

    def test_reasoned_coupled_hypotheses_in_tone_and_color(self):
        for phase, controls in [('tone', {'light.contrast': 5, 'light.blacks': -3}),
                                ('color', {'wb.tint': 2, 'calibration.redHue': 4})]:
            value = state(phase)
            row = add(value, branch=phase, groups=[{'group_id': 'hypothesis', 'reason': 'Shared visible relationship',
                      'proposed_controls': {'values': controls}}])
            self.assertEqual(len(row['task_groups']), 1)
            gid = next(iter(row['task_groups']))
            wrapper = {'name': 'photo_edit', 'selection_reason': 'Same hypothesis',
                       'arguments': {'base_version': 'v0', 'values': controls}}
            self.assertEqual(bank.resolve_selected_group(value, 'i', gid, 'v0', wrapper)['id'], gid)

    def test_same_observation_keep_preserved_after_effect_judgment(self):
        value = state(); row = add(value); gid = next(iter(row['task_groups']))
        value['versions']['v1'] = {'parent': 'v0', 'operation_id': 'op1'}; value['current'] = 'v1'
        bank.record_operation(value, 'i', 'op1', 'v0', 'v1', group_id=gid, action={'values': {'light.exposure': .1}})
        value['effect_expectations'] = [{'id': 'e1', 'operation_id': 'op1', 'child_version': 'v1'}]
        observation = seen(value)
        bank.apply_bank_delta(value, [{'op': 'keep', 'id': 'i'}], observation)
        bank.record_group_review(value, {'effect_review': {'expectation_id': 'e1', 'outcome': 'mixed', 'evidence': 'Tradeoff judged'}}, observation)
        self.assertEqual(bank.resolve_selected_group(value, 'i', gid, 'v1')['status'], 'ready')

    def test_waiting_never_hides_ready_candidates(self):
        value = state()
        for index in range(5):
            add(value, 'blocked' + str(index), dependencies=['missing'])
        add(value, 'ready', ['light.contrast'])
        packet = bank.bank_projection(value, 'tone')['work_packet']
        self.assertEqual([g['issue_id'] for g in packet['ready_candidates']], ['ready'])
        self.assertEqual(len(packet['waiting']), 5)
        self.assertEqual(packet['ready_candidates'][0]['diagnosis'], 'Public diagnosis')

    def test_wb_metadata_curve_points_and_empty_action_containers(self):
        value = state('color'); add(value, branch='color', controls=['wb.temp', 'wb.tint'])
        group = bank.resolve_selected_group(value, 'i', None, 'v0', {'values': {}, 'commands': [],
                 'settings': {'wb': {'temp': 5700, 'tint': 4, 'mode': 'custom', 'scale': 'kelvin'}}})
        self.assertEqual(group['step'], 'wb')
        value = state(); add(value, controls={'settings': {'curve': {'master': [{'x': 0, 'y': 0}, {'x': 1, 'y': 1}]}}})
        self.assertEqual(bank.resolve_selected_group(value, 'i', None, 'v0')['step'], 'master_curve')
        fragments = tasks.parse_controls({'base_version': 'v0', 'selected_version': 'v0',
            'selection_reason': 'Current local task', 'override_reason': 'Local residual first',
            'mask_plan': {'action': 'create', 'kind': 'linear', 'values': {'exposure': .3}}})
        self.assertEqual(len(fragments), 1)
        self.assertEqual(fragments[0]['step'], 'local_tone')

    def test_geometry_only_mask_group_ready_without_unrelated_local_values(self):
        value = state('joint')
        command = {'id': 'mask.update', 'params': {'id': 77, 'component': 0,
                   'shape': {'kind': 'radial', 'center': {'x': .4, 'y': .5},
                             'rx': .2, 'ry': .1, 'angle': 0, 'feather': 30}}}
        proposal = {'commands': [command]}
        row = add(value, branch='joint', groups=[{'group_id': 'edge', 'reason': 'Refine existing mask edge',
                                               'proposed_controls': proposal}])
        gid = next(iter(row['task_groups']))
        group = bank.resolve_selected_group(value, 'i', gid, 'v0', proposal)
        self.assertEqual((group['branch'], group['module'], group['step']), ('joint', 'masks', 'residual_local'))
        self.assertTrue(group['maintenance'])
        self.assertTrue(group['requires_cross_stage_review'])
        self.assertEqual(group['proposed_controls'][0]['mask_id'], 77)
        self.assertEqual(group['source_proposal'], proposal)
        self.assertEqual(group['proposed_controls'][0]['value'], command)

    def test_typed_mask_maintenance_routing_preserves_native_id_validation(self):
        for phase, step in [('tone', 'local_tone'), ('color', 'local_wb'), ('joint', 'residual_local')]:
            for ident, fields in [('mask.update', {'shape': {'kind': 'linear'}}),
                                  ('mask.select', {}), ('mask.visible', {'visible': False}),
                                  ('mask.delete', {}), ('mask.rename', {'name': 'Existing region'})]:
                with self.subTest(phase=phase, command=ident):
                    value = state(phase)
                    proposal = {'commands': [{'id': ident, 'params': {'id': 999, **fields}}]}
                    add(value, branch=phase, controls=proposal)
                    # No readback ID or shape is invented/qualified by this pure parser.
                    group = bank.resolve_selected_group(value, 'i', None, 'v0', proposal)
                    self.assertEqual(group['step'], step)
                    self.assertEqual(group['proposed_controls'][0]['mask_id'], 999)
                    self.assertNotIn('unresolved_reason', group['proposed_controls'][0])
        for command in [{'id': 'mask.unsupported', 'params': {'id': 7}},
                        {'id': 'mask.select', 'params': {}},
                        {'id': 'mask.select', 'params': {'id': 'inferred'}}]:
            self.assertIn('unresolved_reason', tasks.parse_controls({'commands': [command]}, branch='joint')[0])

    def test_sort_steps_priority_override_and_group_retention(self):
        value = state(); add(value, 'contrast', ['light.contrast'], priority=100)
        add(value, 'exposure', priority=0)
        self.assertEqual([g['issue_id'] for g in groups(value)], ['exposure', 'contrast'])
        bank.apply_bank_delta(value, [{'id': 'contrast', 'op': 'revise', 'reorder_override': {'order': -1, 'reason': 'Already judged exposure sufficient'}}], seen(value))
        self.assertEqual(groups(value)[0]['issue_id'], 'contrast')
        before = copy.deepcopy(value)
        with self.assertRaises(ValueError):
            bank.apply_bank_delta(value, [{'id': 'contrast', 'op': 'revise', 'reorder_override': {'order': -1}}], seen(value))
        self.assertEqual(value['proposal_bank'], before['proposal_bank'])

    def test_operation_and_normal_effect_review_bind_actual_params(self):
        value = state(); row = add(value); gid = next(iter(row['task_groups']))
        value['versions']['v1'] = {'parent': 'v0', 'operation_id': 'op1'}; value['current'] = 'v1'
        params = {'values': {'light.exposure': .4}, 'rationale': 'Actual operation'}
        record = bank.record_operation(value, 'i', 'op1', 'v0', 'v1', group_id=gid, action=params)
        self.assertEqual(record['actual_params'], params)
        self.assertEqual(value['selected_group_id'], gid)
        self.assertEqual(bank.branch_workflow(value, 'tone')['current_step'], 'exposure')
        self.assertEqual(bank.bank_projection(value, 'tone')['work_packet']['current'][0]['id'], gid)
        self.assertEqual(groups(value)[0]['status'], 'awaiting_effect')
        value['effect_expectations'] = [{'id': 'e1', 'operation_id': 'op1', 'child_version': 'v1'}]
        bank.record_group_review(value, {'effect_review': {'expectation_id': 'e1', 'outcome': 'improved', 'evidence': 'Model saw a lift'}}, seen(value))
        group = value['proposal_bank']['issues']['i']['task_groups'][gid]
        self.assertEqual(group['status'], 'needs_reassessment')
        self.assertEqual(group['last_review']['claim_type'], 'model_judgment')
        self.assertEqual(group['operations'][0]['outcome'], 'improved')
        with self.assertRaises(ActionRejected):
            bank.resolve_selected_group(value, 'i', gid, 'v1')
        bank.apply_bank_delta(value, [{'op': 'keep', 'id': 'i', 'group_id': gid}], seen(value))
        self.assertEqual(bank.resolve_selected_group(value, 'i', gid, 'v1')['id'], gid)

    def test_legacy_none_and_issue_operation_compatibility(self):
        value = state(); self.assertIsNone(bank.validate_selected_issue(value, None, 'v0'))
        self.assertIsNone(bank.resolve_selected_group(value, None, None, 'v0'))
        add(value); value['versions']['v1'] = {'parent': 'v0', 'operation_id': 'op1'}; value['current'] = 'v1'
        self.assertIsNotNone(bank.record_operation(value, 'i', 'op1', 'v0', 'v1')['group_id'])

    def test_rollback_marks_after_evidence_offbranch_and_never_deletes(self):
        value = state(); row = add(value); gid = next(iter(row['task_groups']))
        value['versions']['v1'] = {'parent': 'v0', 'operation_id': 'op1'}; value['current'] = 'v1'
        bank.record_operation(value, 'i', 'op1', 'v0', 'v1', group_id=gid, action={'values': {'light.exposure': .3}})
        value['current'] = 'v0'; bank.invalidate_bank(value, 'v0')
        self.assertEqual(groups(value)[0]['status'], 'off_selected_branch')
        self.assertEqual(len(value['proposal_bank']['issues']['i']['operations']), 1)
        self.assertEqual(len(bank.bank_history(value, 'i')), 3)
        self.assertEqual(bank.bank_projection(value, 'tone')['work_packet']['candidates'], [])
        bank.apply_bank_delta(value, [{'op': 'keep', 'id': 'i', 'group_id': gid}], seen(value))
        self.assertEqual(bank.resolve_selected_group(value, 'i', gid, 'v0')['status'], 'ready')
        self.assertEqual(len(value['proposal_bank']['issues']['i']['operations']), 1)

    def test_resolved_keep_not_resurrected_and_all_history_detached(self):
        value = state(); row = add(value); gid = next(iter(row['task_groups']))
        bank.record_group_review(value, {'issue_id': 'i', 'group_id': gid, 'outcome': 'resolved', 'reason': 'Explicit task closure'}, seen(value))
        bank.apply_bank_delta(value, [{'id': 'i', 'op': 'keep'}], seen(value))
        self.assertEqual(groups(value)[0]['status'], 'resolved')
        history = bank.bank_history(value); history[0]['diagnosis'] = 'Changed copy'
        self.assertEqual(bank.bank_history(value)[0]['diagnosis'], 'Public diagnosis')

    def test_projection_g2_color_joint_g3_joint_priority_and_no_storage_cap(self):
        value = state('joint'); add(value, 'tone'); add(value, 'color', ['wb.tint'], branch='color')
        add(value, 'joint', ['light.exposure'], branch='joint')
        g2 = bank.bank_projection(value, 'G2')['work_packet']
        self.assertEqual({g['issue_id'] for g in g2['candidates']}, {'color', 'joint'})
        self.assertEqual(bank.bank_projection(value, 'G3')['work_packet']['candidates'][0]['issue_id'], 'joint')
        for index in range(12):
            add(value, 'extra' + str(index))
        projection = bank.bank_projection(value, 'joint')
        self.assertEqual(len(projection['summary']), 15)
        self.assertEqual(len(projection['work_packet']['summary']), 15)
        self.assertEqual(len(projection['work_packet']['candidates']), 3)
        self.assertTrue(projection['full_records_available'])


if __name__ == '__main__':
    unittest.main()
