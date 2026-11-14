"""Focused offline task/bank tests. No model, Native, render, hash or run IO."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_phase_scheduler as scheduler
import photo_proposal_bank as bank
import photo_stage_tasks as tasks
import photo_white_balance as white_balance
import photo_tone_guidance as tone
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
        expected = ['whites_blacks', 'exposure', 'highlights_shadows', 'contrast', 'master_curve', 'parametric_curve', 'local_tone', 'closure']
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
        for outcome in ('keep', 'revise', 'retire', 'defer', 'skip', 'resolved', 'needs_reassessment', 'waiting_dependency'):
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

    def test_group_for_review_matches_bank_full_and_short_alias_detached(self):
        value = state('color')
        add(value, branch='color', groups=[{'group_id': 'wb', 'proposed_controls': ['wb.tint']}])
        before = copy.deepcopy(value)
        full = bank.group_for_review(value, 'i', 'i::wb')
        self.assertEqual(full, bank.group_for_review(value, 'i', 'wb'))
        self.assertEqual(full['module'], 'wb')
        full['status'] = 'resolved'
        self.assertEqual(value, before)
        self.assertIsNone(bank.group_for_review(value, 'i', 'unknown'))
        self.assertIsNone(bank.group_for_review(value, 'missing', 'wb'))

    def test_picker_is_explicit_wb_task_in_color_and_joint_without_cap_lookup(self):
        picker = {'commands': [{'id': 'develop.wbPick', 'params': {'x': .3, 'y': .4}}]}
        self.assertEqual(tasks.classify_control('develop.wbPick'), ('color', 'wb', 'wb'))
        for phase, step in [('color', 'wb'), ('joint', 'residual_wb')]:
            value = state(phase); row = add(value, branch=phase, controls=picker)
            gid = next(iter(row['task_groups']))
            selected = bank.resolve_selected_group(value, 'i', gid, 'v0', picker)
            self.assertEqual((selected['module'], selected['step']), ('wb', step))
            self.assertEqual(selected['proposed_controls'][0]['control_id'], 'develop.wbPick')
            self.assertEqual(selected['source_proposal'], picker)
            self.assertEqual(tasks.parse_controls(['develop.wbPick'], branch=phase)[0]['step'], step)

    def test_selected_resolved_hidden_from_current_but_kept_in_summary_and_revised_open(self):
        value = state('color'); row = add(value, branch='color', controls=['wb.tint'])
        gid = next(iter(row['task_groups'])); value['selected_group_id'] = gid
        bank.record_group_review(value, {'issue_id': 'i', 'group_id': gid, 'outcome': 'resolved',
                                        'reason': 'Target evidence explicitly closes task'}, seen(value))
        projection = bank.bank_projection(value, 'color')
        self.assertEqual(projection['work_packet']['current'], [])
        self.assertEqual(projection['work_packet']['candidates'], [])
        self.assertEqual(projection['work_packet']['summary'][0]['status'], 'resolved')
        self.assertIsNone(projection['branch_workflow']['current_step'])
        self.assertTrue(bank.bank_history(value, 'i'))
        bank.apply_bank_delta(value, [{'id': 'i', 'group_id': gid, 'op': 'keep'}], seen(value))
        self.assertEqual(bank.bank_projection(value, 'color')['work_packet']['current'], [])
        bank.apply_bank_delta(value, [{'id': 'i', 'group_id': gid, 'op': 'revise', 'reason': 'Fresh contradictory target residual'}], seen(value))
        self.assertEqual(bank.bank_projection(value, 'color')['work_packet']['current'][0]['id'], gid)
        self.assertEqual(bank.resolve_selected_group(value, 'i', gid, 'v0')['status'], 'ready')

    def test_wb_binding_preserves_original_proposals_status_and_declared_fields(self):
        value = state('color')
        add(value, branch='color', controls=['wb.tint'], target_change={'original': 'Cool wall'}, protected=['accepted tone'])
        gid = next(iter(value['proposal_bank']['issues']['i']['task_groups']))
        obsid = seen(value); value['observations'][-1].update(phase='color', comparison_mode='target')
        assessment = white_balance.validate_assessment({
            'reference_status': 'not_available', 'reference_pairs': [], 'target_intent': 'intentional_cast',
            'intent_reason': 'Keep authored warm lighting.', 'atmosphere': {'route': 'grading', 'reason': 'Atmosphere is deliberate.'},
            'wb_task_status': 'needed', 'reason': 'Yellow green residual still visible.'}, value['observations'][-1])
        before = copy.deepcopy(value['proposal_bank']['issues']['i'])
        result = bank.bind_wb_assessment(value, assessment)
        row = value['proposal_bank']['issues']['i']; group = row['task_groups'][gid]
        self.assertEqual(result['group_ids'], [gid])
        for key in ('target_change', 'protected', 'proposed_controls', 'status'):
            self.assertEqual(row[key], before[key])
        self.assertEqual(group['status'], before['task_groups'][gid]['status'])
        self.assertEqual(group['proposed_controls'], before['task_groups'][gid]['proposed_controls'])
        self.assertEqual(group['wb_reference_contract'][0]['target_change']['target_intent'], 'intentional_cast')
        self.assertEqual(row['records'][-1]['event'], 'wb_assessment_binding')
        snapshot = copy.deepcopy(value)
        self.assertFalse(bank.bind_wb_assessment(value, assessment)['changed'])
        self.assertEqual(value, snapshot)
        assessment['reason'] = 'Different explicit reassessment'
        bank.bind_wb_assessment(value, assessment)
        self.assertEqual(len(value['proposal_bank']['issues']['i']['wb_reference_contract']), 2)
        self.assertEqual(value['proposal_bank']['issues']['i']['records'][-2]['wb_reference_contract'][0]['target_change']['reason'], 'Yellow green residual still visible.')

    def test_wb_binding_no_blank_issue_no_guessed_short_group_or_stale_auto_link(self):
        value = state('color'); obsid = seen(value)
        value['observations'][-1].update(phase='color', comparison_mode='target')
        assessment = {'observation_id': obsid, 'settings_version': 'v0', 'phase': 'color', 'target_intent': 'uncertain'}
        original = copy.deepcopy(value)
        self.assertFalse(bank.bind_wb_assessment(value, assessment)['changed'])
        self.assertEqual(value, original)
        add(value, 'a', branch='color', groups=[{'group_id': 'wb', 'proposed_controls': ['wb.tint']}])
        add(value, 'b', branch='color', groups=[{'group_id': 'wb', 'proposed_controls': ['wb.tint']}])
        assessment['group_id'] = 'wb'
        self.assertFalse(bank.bind_wb_assessment(value, assessment)['changed'])
        assessment['issue_id'] = 'a'
        self.assertEqual(bank.bind_wb_assessment(value, assessment)['group_ids'], ['a::wb'])
        value['versions']['v1'] = {'parent': 'v0'}; value['current'] = 'v1'
        obsid = seen(value); value['observations'][-1].update(phase='color', comparison_mode='target')
        assessment = {**assessment, 'observation_id': obsid, 'settings_version': 'v1'}
        del assessment['issue_id']; del assessment['group_id']
        self.assertFalse(bank.bind_wb_assessment(value, assessment)['changed'])

    def test_tone_neutral_first_does_not_change_stable_ids_or_create_a_menu_gate(self):
        value = state()
        row = add(value, controls=['light.exposure', 'light.contrast', 'light.shadows', 'light.blacks'])
        selected = list(row['task_groups'].values())
        self.assertEqual([g['step'] for g in selected], ['whites_blacks', 'exposure', 'highlights_shadows', 'contrast'])
        for group in selected:
            self.assertEqual(group['id'], 'i::tone/light/' + group['step'])
        contrast = next(g for g in selected if g['step'] == 'contrast')
        self.assertEqual(bank.resolve_selected_group(value, 'i', contrast['id'], 'v0',
                         {'values': {'light.contrast': 8}})['id'], contrast['id'])

    def test_tone_binding_only_explicit_refs_no_status_or_parameter_changes(self):
        value = state('G1')
        add(value, branch='color', controls=['mixer.blue.sat'])
        gid = next(iter(value['proposal_bank']['issues']['i']['task_groups']))
        obsid = seen(value); obs = value['observations'][-1]
        obs.update(phase='G1', comparison_mode='target')
        item = {'id': 'sky', 'author_region': 'A1 sky', 'current_region': 'B2 sky',
                'material': 'Sky', 'lighting': 'Twilight', 'attribution': 'uncertain',
                'evidence': 'Depth may come from chroma', 'protection': 'Keep skyline edge',
                'handoff': 'color', 'issue_id': 'i', 'group_id': gid}
        assessment = tone.validate_tone_assessment({'items': [item], 'reason': 'Separate attribution',
                                                    'neutral_anchors': []}, obs)
        before = copy.deepcopy(value['proposal_bank']['issues']['i'])
        result = bank.bind_tone_assessment(value, assessment)
        self.assertEqual(result['group_ids'], [gid])
        row = value['proposal_bank']['issues']['i']; group = row['task_groups'][gid]
        for key in ('proposed_controls', 'status', 'needs_reassessment', 'base_version'):
            self.assertEqual(row[key], before[key])
        for key in ('status', 'proposed_controls', 'source_proposal', 'dependencies'):
            self.assertEqual(group[key], before['task_groups'][gid][key])
        self.assertNotIn('unresolved', group)
        self.assertNotIn('values', group['tone_reference_contract'][0])
        value['tone_assessments'] = [assessment]
        context = tone.work_context(value, 'tone', {**obs, 'phase': 'tone'})
        self.assertEqual(context['protected_regions'][0]['group_id'], gid)
        snapshot = copy.deepcopy(value)
        self.assertFalse(bank.bind_tone_assessment(value, assessment)['changed'])
        self.assertEqual(value, snapshot)
        self.assertEqual(group['tone_reference_contract'][0]['target_change']['attribution'], 'uncertain')

    def test_tone_binding_unknown_ambiguous_empty_and_issue_only_are_non_gating(self):
        value = state('G1')
        for ident in ('a', 'b'):
            add(value, ident, controls=['light.blacks'], groups=[{'group_id': 'edge', 'proposed_controls': ['light.blacks']}])
        seen(value); obs = value['observations'][-1]; obs.update(phase='G1', comparison_mode='target')
        item = {'id': 'edge', 'author_region': 'Author dark wall', 'current_region': 'Current wall',
                'material': 'Stone', 'lighting': 'Diffuse', 'attribution': 'structure',
                'evidence': 'Missing dark support', 'protection': 'Keep texture', 'handoff': 'tone'}
        for refs in ({}, {'group_id': 'edge'}, {'issue_id': 'unknown'}, {'issue_id': 'a', 'group_id': 'unknown'}):
            assessment = tone.validate_tone_assessment({'items': [{**item, **refs}], 'reason': 'No automatic tasks', 'neutral_anchors': []}, obs)
            before = copy.deepcopy(value)
            result = bank.bind_tone_assessment(value, assessment)
            self.assertFalse(result['changed']); self.assertEqual(result['unbound_items'], ['edge'])
            self.assertEqual(value, before)
        empty = tone.validate_tone_assessment({'items': [], 'reason': 'No mismatch', 'neutral_anchors': []}, obs)
        self.assertFalse(bank.bind_tone_assessment(value, empty)['changed'])
        explicit = tone.validate_tone_assessment({'items': [{**item, 'issue_id': 'a'}], 'reason': 'Explicit issue only', 'neutral_anchors': []}, obs)
        result = bank.bind_tone_assessment(value, explicit)
        self.assertEqual(result['issue_ids'], ['a']); self.assertEqual(result['group_ids'], [])
        self.assertNotIn('tone_reference_contract', value['proposal_bank']['issues']['a']['task_groups']['a::edge'])
        forged = {**explicit, 'settings_version': 'forged'}
        with self.assertRaises(ValueError):
            bank.bind_tone_assessment(value, forged)

    def test_pre_tone_wb_g1_explicit_scalar_group_only_without_phase_mutation(self):
        value = state('G1')
        add(value, branch='color', controls=['wb.temp', 'wb.tint', 'mixer.blue.sat'])
        gid = next(g['id'] for g in value['proposal_bank']['issues']['i']['task_groups'].values() if g['module'] == 'wb')
        action = {'name': 'pre_tone_wb', 'arguments': {'values': {'wb.temp': 5500}}}
        before = copy.deepcopy(value)
        self.assertEqual(bank.resolve_selected_group(value, 'i', gid, 'v0', action)['module'], 'wb')
        self.assertEqual(value, before)
        self.assertEqual(value['workflow']['phase'], 'G1')
        packet = bank.bank_projection(value, 'G1')['work_packet']
        self.assertEqual([g['id'] for g in packet['pre_tone_wb_candidates']], [gid])
        self.assertEqual(packet['pre_tone_wb_candidates'][0]['status'], 'later_phase')
        self.assertEqual(packet['ready_candidates'], [])
        for name in ('photo_edit', None):
            with self.assertRaises(ActionRejected):
                bank.resolve_selected_group(value, 'i', gid, 'v0', {**action, 'name': name})
        with self.assertRaises(ActionRejected):
            bank.resolve_selected_group(value, 'i', None, 'v0', action)
        for controls, branch in [(['develop.wbPick'], 'color'), (['wb.temp'], 'joint'),
                                  (['mixer.blue.sat'], 'color'), (['light.exposure'], 'tone')]:
            other = state('G1'); row = add(other, branch=branch, controls=controls)
            with self.assertRaises(ActionRejected):
                bank.resolve_selected_group(other, 'i', next(iter(row['task_groups'])), 'v0', action)

    def test_pre_tone_wb_record_success_reresolves_in_g1_and_projects_pending(self):
        value = state('G1'); row = add(value, branch='color', controls=['wb.temp'])
        gid = next(iter(row['task_groups']))
        action = {'name': 'pre_tone_wb', 'arguments': {'values': {'wb.temp': 5500}}}
        bank.resolve_selected_group(value, 'i', gid, 'v0', action)
        value['versions']['v1'] = {'parent': 'v0', 'operation_id': 'pre1'}; value['current'] = 'v1'
        record = bank.record_operation(value, 'i', 'pre1', 'v0', 'v1', group_id=gid, action=action)
        self.assertEqual(record['actual_params'], action['arguments'])
        self.assertEqual(value['workflow']['phase'], 'G1')
        self.assertEqual(bank.bank_projection(value, 'G1')['work_packet']['pre_tone_wb_pending_review'][0]['id'], gid)
        with self.assertRaises(ActionRejected):
            bank.resolve_selected_group(value, 'i', gid, 'v1', action)

    def test_pre_tone_wb_stale_offbranch_unresolved_and_softdeps_preserved(self):
        action = {'name': 'pre_tone_wb', 'arguments': {'values': {'wb.tint': 3}}}
        for mode in ('stale', 'offbranch', 'unresolved'):
            value = state('G1'); row = add(value, branch='color', controls=['wb.tint'])
            gid = next(iter(row['task_groups']))
            value['versions']['v1'] = {'parent': 'v0'}
            if mode == 'stale':
                value['current'] = 'v1'
            elif mode == 'offbranch':
                value['proposal_bank']['issues']['i']['task_groups'][gid]['base_version'] = 'v1'
            else:
                value['proposal_bank']['issues']['i']['task_groups'][gid]['unresolved'] = True
            with self.assertRaises(ActionRejected):
                bank.resolve_selected_group(value, 'i', gid, value['current'], action)
        value = state('G1'); add(value, 'known', branch='color', controls=['wb.temp'])
        row = add(value, branch='color', controls=['wb.tint'], dependencies=['known'])
        gid = next(iter(row['task_groups']))
        with self.assertRaises(ActionRejected):
            bank.resolve_selected_group(value, 'i', gid, 'v0', action)
        selected = bank.resolve_selected_group(value, 'i', gid, 'v0', {**action, 'selection_reason': 'Independent evidence permits this small WB step'})
        self.assertIn('dependency_override', selected)
        self.assertEqual(value['workflow']['phase'], 'G1')
        with self.assertRaises(ActionRejected):
            bank.resolve_selected_group(value, 'i', gid, 'v0', {**action, 'arguments': {'values': {'mixer.blue.sat': 5}}})

    def test_skip_terminal_current_version_reason_and_explicit_reopen(self):
        value = state(); row = add(value); gid = next(iter(row['task_groups']))
        value['selected_group_id'] = gid; observation = seen(value)
        before = copy.deepcopy(value)
        with self.assertRaises(ActionRejected):
            bank.record_group_review(value, {'issue_id': 'i', 'group_id': gid, 'outcome': 'skip', 'reason': ''}, observation)
        self.assertEqual(value, before)
        bank.record_group_review(value, {'issue_id': 'i', 'group_id': gid, 'outcome': 'skip',
                                        'reason': 'Not appropriate for this target', 'operation_id': 'future'}, observation)
        group = value['proposal_bank']['issues']['i']['task_groups'][gid]
        self.assertEqual(group['status'], 'skipped')
        self.assertEqual(group['last_review']['observation_id'], observation)
        self.assertEqual(group['last_review']['version'], 'v0')
        self.assertEqual(group['operations'], [])
        self.assertEqual(group['last_review']['review_kind'], 'proposal_disposition')
        for op in ('keep',):
            bank.apply_bank_delta(value, [{'op': op, 'id': 'i', 'group_id': gid}], seen(value))
            bank.apply_bank_delta(value, [{'op': op, 'id': 'i'}], seen(value))
        projection = bank.bank_projection(value, 'tone')
        self.assertEqual(projection['work_packet']['current'], [])
        self.assertEqual(projection['work_packet']['candidates'], [])
        self.assertEqual(projection['work_packet']['summary'][0]['status'], 'skipped')
        self.assertIsNone(projection['branch_workflow']['current_step'])
        bank.apply_bank_delta(value, [{'op': 'revise', 'id': 'i', 'reason': 'Fresh target warrants revisiting'}], seen(value))
        self.assertEqual(bank.resolve_selected_group(value, 'i', gid, 'v0')['status'], 'ready')
        value['versions']['v1'] = {'parent': 'v0'}; value['current'] = 'v1'
        with self.assertRaises(ActionRejected):
            bank.record_group_review(value, {'issue_id': 'i', 'group_id': gid, 'outcome': 'skip', 'reason': 'Old review'}, observation)

    def test_soft_skip_and_retire_waivers_are_explicit_auditable_and_idempotent(self):
        for outcome, outcome_field in (('skip', 'outcome'), ('retire', 'outcome'), ('skip', 'result'), ('skip', 'judgment')):
            with self.subTest(outcome=outcome, outcome_field=outcome_field):
                value = state()
                add(value, groups=[{'group_id': 'first', 'proposed_controls': ['light.contrast']},
                    {'group_id': 'second', 'proposed_controls': ['light.blacks'], 'dependencies': ['first']}])
                obs = seen(value)
                bank.record_group_review(value, {'issue_id': 'i', 'group_id': 'first', outcome_field: outcome,
                                                'reason': 'Explicit upstream disposition'}, obs)
                before = copy.deepcopy(value)
                for _ in range(3):
                    packet = bank.bank_projection(value, 'tone')['work_packet']
                    self.assertEqual(next(g for g in packet['summary'] if g['id'] == 'i::second')['status'], 'needs_reassessment')
                self.assertEqual(value, before)
                result = bank.reconcile_dependencies(value, obs)
                self.assertTrue(result['changed']); self.assertEqual(len(result['waiver_records']), 1)
                waiver = result['waiver_records'][0]
                self.assertEqual(waiver['target_group_id'], 'i::first')
                self.assertEqual(waiver['version'], 'v0'); self.assertEqual(waiver['selected_version'], 'v0')
                self.assertTrue(waiver['target_record_id']); self.assertTrue(waiver['reason'])
                issue = value['proposal_bank']['issues']['i']
                self.assertEqual(issue['records'][-1]['waiver_records'][0], waiver)
                self.assertEqual(issue['task_groups']['i::second']['records'][-1]['waiver_records'][0], waiver)
                self.assertEqual(value['proposal_bank']['events'][-1]['waiver_records'][0], waiver)
                with self.assertRaises(ActionRejected):
                    bank.resolve_selected_group(value, 'i', 'second', 'v0', {'selection_reason': 'Cannot skip reassessment'})
                snapshot = copy.deepcopy(value)
                self.assertFalse(bank.reconcile_dependencies(value, obs)['changed']); self.assertEqual(value, snapshot)
                obs = seen(value)
                self.assertFalse(bank.reconcile_dependencies(value, obs)['changed'])
                bank.apply_bank_delta(value, [{'op': 'keep', 'id': 'i', 'group_id': 'second'}], obs)
                self.assertEqual(bank.resolve_selected_group(value, 'i', 'second', 'v0')['status'], 'ready')
                self.assertFalse(bank.reconcile_dependencies(value, obs)['changed'])

    def test_waiver_inherits_success_child_and_does_not_erase_unreviewed_operation(self):
        value = state(); add(value, 'up'); row = add(value, dependencies=['up'])
        gid = next(iter(row['task_groups'])); upstream = next(iter(value['proposal_bank']['issues']['up']['task_groups']))
        obs = seen(value)
        bank.record_group_review(value, {'issue_id': 'up', 'group_id': upstream, 'outcome': 'skip', 'reason': 'Not needed'}, obs)
        bank.reconcile_dependencies(value, obs)
        bank.apply_bank_delta(value, [{'id': 'i', 'op': 'keep'}], obs)
        value['versions']['v1'] = {'parent': 'v0', 'operation_id': 'op1'}; value['current'] = 'v1'
        bank.record_operation(value, 'i', 'op1', 'v0', 'v1', group_id=gid, action={'values': {'light.exposure': .1}})
        self.assertFalse(bank.reconcile_dependencies(value, obs)['changed'])
        group = value['proposal_bank']['issues']['i']['task_groups'][gid]
        self.assertEqual(len(group['waiver_records']), 1)
        self.assertEqual(group['status'], 'awaiting_effect')
        self.assertEqual(bank.bank_projection(value, 'tone')['work_packet']['pending_review'][0]['id'], gid)

    def test_hard_disposition_not_skipped_or_retired_and_never_overridden(self):
        for outcome in ('skip', 'retire', 'resolved'):
            value = state(); add(value, 'up')
            row = add(value, 'down', dependencies=[{'issue_id': 'up', 'kind': 'hard', 'requirement': 'disposition'}])
            gid = next(iter(row['task_groups'])); upstream = next(iter(value['proposal_bank']['issues']['up']['task_groups']))
            obs = seen(value)
            bank.record_group_review(value, {'issue_id': 'up', 'group_id': upstream, 'outcome': outcome, 'reason': 'Explicit decision'}, obs)
            result = bank.reconcile_dependencies(value, obs)
            self.assertFalse(result['changed'])
            action = {'values': {'light.exposure': .2}, 'selection_reason': 'Override must not satisfy hard dependency'}
            if outcome == 'resolved':
                self.assertEqual(bank.resolve_selected_group(value, 'down', gid, 'v0', action)['status'], 'ready')
            else:
                with self.assertRaises(ActionRejected):
                    bank.resolve_selected_group(value, 'down', gid, 'v0', action)

    def test_unknown_ambiguous_and_transitive_cycle_no_reason_override(self):
        for mode in ('unknown', 'ambiguous', 'cycle', 'transitive_cycle'):
            value = state()
            if mode == 'unknown':
                row = add(value, dependencies=['missing'])
            elif mode == 'ambiguous':
                row = add(value, groups=[{'group_id': 'split', 'proposed_controls': ['light.contrast', 'light.blacks']},
                    {'group_id': 'test', 'proposed_controls': ['light.exposure'], 'dependencies': ['split']}])
            else:
                add(value, 'a', dependencies=['b']); add(value, 'b', dependencies=['a'])
                row = value['proposal_bank']['issues']['a'] if mode == 'cycle' else add(value, dependencies=['a'])
            gid = 'i::test' if mode == 'ambiguous' else next(iter(row['task_groups']))
            with self.subTest(mode=mode), self.assertRaises(ActionRejected):
                bank.resolve_selected_group(value, row['id'], gid, 'v0', {'values': {'light.exposure': .1}, 'selection_reason': 'Explicit but invalid override'})

    def test_known_soft_waiting_override_still_records_actual_selection(self):
        value = state(); add(value, 'up'); row = add(value, dependencies=['up']); gid = next(iter(row['task_groups']))
        action = {'values': {'light.exposure': .2}, 'selection_reason': 'Independent selected-version evidence'}
        selected = bank.resolve_selected_group(value, 'i', gid, 'v0', action)
        self.assertIn('dependency_override', selected)
        value['versions']['v1'] = {'parent': 'v0', 'operation_id': 'op1'}; value['current'] = 'v1'
        record = bank.record_operation(value, 'i', 'op1', 'v0', 'v1', group_id=gid, action=action)
        self.assertEqual(record['dependency_override']['base_version'], 'v0')
        self.assertTrue(value['proposal_bank']['issues']['i']['task_groups'][gid]['operations'][0]['dependency_override'])

    def test_stale_waiting_never_becomes_ready_by_override(self):
        for missing in (False, True):
            value = state(); add(value, 'up'); row = add(value, dependencies=['missing' if missing else 'up'])
            gid = next(iter(row['task_groups']))
            value['versions']['v1'] = {'parent': 'v0'}; value['current'] = 'v1'
            item = next(g for g in groups(value) if g['id'] == gid)
            self.assertEqual(item['status'], 'needs_reassessment'); self.assertEqual(item['freshness'], 'stale')
            with self.assertRaises(ActionRejected):
                bank.resolve_selected_group(value, 'i', gid, 'v1', {'selection_reason': 'No stale override', 'values': {'light.exposure': .2}})

    def test_typed_dependencies_validation_atomic_and_explicit_target_aliases(self):
        value = state(); add(value, groups=[{'group_id': 'up', 'proposed_controls': ['light.contrast']},
             {'group_id': 'down', 'proposed_controls': ['light.blacks'],
              'dependencies': [{'group_id': 'up', 'kind': 'hard', 'requirement': 'disposition'}]}])
        self.assertEqual(value['proposal_bank']['issues']['i']['task_groups']['i::down']['dependency_references'][0]['resolved'], 'i::up')
        bank.record_group_review(value, {'issue_id': 'i', 'group_id': 'i::up', 'outcome': 'resolved', 'reason': 'Closed'}, seen(value))
        self.assertEqual(bank.resolve_selected_group(value, 'i', 'down', 'v0')['status'], 'ready')
        for dep in ({'group_id': 'up', 'issue_id': 'i', 'kind': 'hard', 'requirement': 'disposition'},
                    {'group_id': 'up', 'kind': 'unknown', 'requirement': 'effect'},
                    {'group_id': 'up', 'kind': 'hard', 'requirement': 'disposition', 'operation_id': 'op1'},
                    {'group_id': 'up'}, {'id': 'up', 'kind': 'soft', 'requirement': 'effect'}):
            obs = seen(value); before = copy.deepcopy(value)
            with self.assertRaises(ActionRejected):
                bank.apply_bank_delta(value, [{'id': 'i', 'group_id': 'down', 'op': 'revise', 'dependencies': [dep]}], obs)
            self.assertEqual(value, before)

    def test_hard_effect_actual_review_not_resolution_and_prior_operation_qualification(self):
        for outcome in bank.EFFECT_OUTCOMES:
            value = state(); row = add(value, 'up'); upstream = next(iter(row['task_groups']))
            add(value, dependencies=[{'issue_id': 'up', 'kind': 'hard', 'requirement': 'effect'}])
            value['versions']['v1'] = {'parent': 'v0', 'operation_id': 'op1'}; value['current'] = 'v1'
            bank.record_operation(value, 'up', 'op1', 'v0', 'v1', group_id=upstream, action={'values': {'light.exposure': .1}})
            value['effect_expectations'] = [{'id': 'e1', 'operation_id': 'op1', 'child_version': 'v1'}]
            obs = seen(value)
            bank.apply_bank_delta(value, [{'id': 'i', 'op': 'keep'}], obs)
            with self.assertRaises(ActionRejected):
                bank.resolve_selected_group(value, 'i', None, 'v1')
            bank.record_group_review(value, {'effect_review': {'expectation_id': 'e1', 'outcome': outcome, 'evidence': 'Actual observed child review'}}, obs)
            selected = bank.resolve_selected_group(value, 'i', None, 'v1')
            self.assertEqual(selected['dependency_checks'][0]['status'], 'reviewed')
            self.assertEqual(selected['dependency_checks'][0]['outcome'], outcome)
            self.assertNotEqual(value['proposal_bank']['issues']['up']['task_groups'][upstream]['status'], 'resolved')
            bank.apply_bank_delta(value, [{'id': 'up', 'group_id': upstream, 'op': 'keep'}], obs)
            value['versions']['v2'] = {'parent': 'v1', 'operation_id': 'op2'}; value['current'] = 'v2'
            bank.record_operation(value, 'up', 'op2', 'v1', 'v2', group_id=upstream, action={'values': {'light.exposure': .2}})
            obs = seen(value)
            bank.apply_bank_delta(value, [{'id': 'i', 'op': 'keep'}], obs)
            with self.assertRaises(ActionRejected):
                bank.resolve_selected_group(value, 'i', None, 'v2')
            bank.apply_bank_delta(value, [{'id': 'i', 'op': 'revise', 'dependencies': [
                {'group_id': upstream, 'kind': 'hard', 'requirement': 'effect', 'operation_id': 'op1'}]}], obs)
            self.assertEqual(bank.resolve_selected_group(value, 'i', None, 'v2')['dependency_checks'][0]['operation_id'], 'op1')

    def test_hard_effect_offbranch_keep_does_not_fake_operation_and_reconcile_audits_once(self):
        value = state(); row = add(value, 'up'); upstream = next(iter(row['task_groups']))
        add(value, dependencies=[{'group_id': upstream, 'kind': 'hard', 'requirement': 'effect', 'operation_id': 'op1'}])
        value['versions']['v1'] = {'parent': 'v0', 'operation_id': 'op1'}; value['current'] = 'v1'
        bank.record_operation(value, 'up', 'op1', 'v0', 'v1', group_id=upstream, action={'values': {'light.exposure': .2}})
        value['effect_expectations'] = [{'id': 'e1', 'operation_id': 'op1', 'child_version': 'v1'}]
        obs = seen(value)
        bank.record_group_review(value, {'effect_review': {'expectation_id': 'e1', 'outcome': 'supported', 'evidence': 'Reviewed actual operation'}}, obs)
        bank.apply_bank_delta(value, [{'id': 'i', 'op': 'keep'}], obs)
        self.assertEqual(bank.resolve_selected_group(value, 'i', None, 'v1')['status'], 'ready')
        value['current'] = 'v0'; bank.invalidate_bank(value, 'v0'); obs = seen(value)
        result = bank.reconcile_dependencies(value, obs); self.assertTrue(result['changed'])
        self.assertEqual(result['waiver_records'], [])
        self.assertFalse(bank.reconcile_dependencies(value, seen(value))['changed'])
        bank.apply_bank_delta(value, [{'id': 'up', 'group_id': upstream, 'op': 'keep'}, {'id': 'i', 'op': 'keep'}], seen(value))
        with self.assertRaises(ActionRejected):
            bank.resolve_selected_group(value, 'i', None, 'v0', {'selection_reason': 'Cannot reuse discarded actual effect'})
        self.assertEqual(value['proposal_bank']['issues']['up']['task_groups'][upstream]['operations'][0]['outcome'], 'supported')

    def test_reconcile_success_child_uses_source_observation_not_invented_review(self):
        value = state(); add(value, 'up'); add(value, dependencies=['up'])
        upstream = next(iter(value['proposal_bank']['issues']['up']['task_groups'])); obs = seen(value)
        bank.record_group_review(value, {'issue_id': 'up', 'group_id': upstream, 'outcome': 'skip', 'reason': 'Not needed'}, obs)
        value['versions']['v1'] = {'parent': 'v0'}; value['current'] = 'v1'
        result = bank.reconcile_dependencies(value, obs)
        self.assertEqual(result['waiver_records'][0]['version'], 'v0')
        self.assertEqual(result['waiver_records'][0]['selected_version'], 'v1')
        before = copy.deepcopy(value)
        self.assertFalse(bank.reconcile_dependencies(value, obs)['changed']); self.assertEqual(value, before)
        obs = seen(value); bank.apply_bank_delta(value, [{'id': 'i', 'op': 'keep'}], obs)
        self.assertEqual(bank.resolve_selected_group(value, 'i', None, 'v1')['status'], 'ready')

    def test_joint_diagnostic_stable_link_retry_and_lossless_provenance_no_controls(self):
        value = state('G3'); obs = seen(value)
        debt = {'id': 'd1', 'version': 'v0', 'reason': 'Residual', 'evidence': {'full': 'e' * 2000},
                'target_change': {'sky': 'Keep depth'}, 'protected': ['Accepted tone'], 'source_refs': ['op1']}
        ident = bank.spawn_joint_diagnostic(value, debt, 'Joint residual', obs, anchors=['A1'])
        self.assertEqual(ident, 'joint-from:d1'); row = value['proposal_bank']['issues'][ident]
        self.assertEqual(row['groups'], []); self.assertEqual(row['task_groups'], {})
        self.assertNotIn('proposed_controls', row); self.assertEqual(row['operations'], [])
        self.assertEqual(row['source_refs'][0]['debt'], debt)
        self.assertEqual(row['protected'], debt['protected'])
        before = copy.deepcopy(value)
        self.assertEqual(bank.spawn_joint_diagnostic(value, debt, 'Retry with same debt', obs), ident)
        self.assertEqual(value, before)
        value['versions']['v1'] = {'parent': 'v0'}; value['current'] = 'v1'
        obs2 = seen(value); before = copy.deepcopy(value)
        self.assertEqual(bank.spawn_joint_diagnostic(value, debt, 'Branch retry', obs2), ident)
        self.assertEqual(value, before)
        add(value, 'existing', branch='joint', target_change={'original': 'Preserve'})
        original = copy.deepcopy(value['proposal_bank']['issues']['existing']['task_groups'])
        self.assertEqual(bank.spawn_joint_diagnostic(value, {**debt, 'id': 'd2'}, 'Link explicit existing', obs2, existing_issue_id='existing'), 'existing')
        self.assertEqual(value['proposal_bank']['issues']['existing']['task_groups'], original)
        self.assertEqual(value['proposal_bank']['issues']['existing']['target_change'], {'original': 'Preserve'})
        add(value, 'pure-tone')
        before = copy.deepcopy(value)
        with self.assertRaises(ActionRejected):
            bank.spawn_joint_diagnostic(value, {**debt, 'id': 'd3'}, 'Not a Joint issue', obs2, existing_issue_id='pure-tone')
        self.assertEqual(value, before)

    def test_projection_operation_refs_no_actual_params_action_context_or_records(self):
        value = state(); row = add(value); gid = next(iter(row['task_groups']))
        action = {'name': 'photo_edit', 'arguments': {'values': {'light.exposure': .2}, 'rationale': 'x' * 20000}}
        value['versions']['v1'] = {'parent': 'v0', 'operation_id': 'op1'}; value['current'] = 'v1'
        bank.record_operation(value, 'i', 'op1', 'v0', 'v1', group_id=gid, action=action)
        before = copy.deepcopy(value)
        projection = bank.bank_projection(value, 'tone')
        def inspect(node):
            if isinstance(node, dict):
                self.assertNotIn('actual_params', node); self.assertNotIn('action_context', node); self.assertNotIn('records', node)
                for child in node.values():
                    inspect(child)
            elif isinstance(node, list):
                for child in node:
                    inspect(child)
        inspect(projection)
        operation = projection['work_packet']['pending_review'][0]['operations'][0]
        self.assertEqual(operation['operation_id'], 'op1'); self.assertEqual(operation['outcome'], 'unreviewed')
        self.assertTrue(operation['record_ref']['full_history_available'])
        self.assertEqual(value, before)
        self.assertEqual(value['proposal_bank']['issues']['i']['operations'][0]['actual_params'], action['arguments'])

    def test_retired_repeat_dispositions_are_metadata_only_for_issue_and_group(self):
        for whole_issue in (False, True):
            value = state(); row = add(value); gid = next(iter(row['task_groups']))
            target = {'id': 'i', **({} if whole_issue else {'group_id': gid})}
            bank.apply_bank_delta(value, [{**target, 'op': 'retire'}], seen(value))
            original = copy.deepcopy(value['proposal_bank']['issues']['i'])
            value['versions']['v1'] = {'parent': 'v0'}; value['current'] = 'v1'
            obs = seen(value)
            for op, resolution in (('retire', 'idempotent'), ('keep', 'not_adopted')):
                bank.apply_bank_delta(value, [{**target, 'op': op, 'reason': 'Original explicit disposition'}], obs)
                self.assertEqual(value['proposal_bank']['issues']['i'], original)
                self.assertEqual(value['proposal_bank']['events'][-2]['resolution'], resolution)
                result = bank.record_group_review(value, {'issue_id': 'i', 'group_id': gid,
                    'outcome': op, 'reason': 'Repeat public metadata'}, obs)
                self.assertEqual(result[0]['resolution'], resolution)
                self.assertEqual(value['proposal_bank']['issues']['i'], original)
            with self.assertRaises(ActionRejected):
                bank.resolve_selected_group(value, 'i', gid, 'v1', {'selection_reason': 'Never revive retired'})

    def test_retired_substantive_changes_and_other_dispositions_reject_atomically(self):
        value = state(); row = add(value); gid = next(iter(row['task_groups']))
        bank.apply_bank_delta(value, [{'id': 'i', 'group_id': gid, 'op': 'retire'}], seen(value))
        obs = seen(value)
        for update in ({'outcome': 'keep', 'priority': 3}, {'outcome': 'retire', 'proposed_controls': ['light.blacks']},
                       {'outcome': 'revise'}, {'outcome': 'resolved'}, {'outcome': 'skip'}):
            before = copy.deepcopy(value)
            with self.assertRaises(ActionRejected):
                bank.record_group_review(value, {'issue_id': 'i', 'group_id': gid, 'reason': 'Must not mutate', **update}, obs)
            self.assertEqual(value, before)
        before = copy.deepcopy(value)
        with self.assertRaises(ActionRejected):
            bank.apply_bank_delta(value, [{'id': 'i', 'op': 'revise', 'groups': [
                {'group_id': gid, 'status': 'ready', 'proposed_controls': ['light.exposure']}]}], obs)
        self.assertEqual(value, before)
        with self.assertRaises(ActionRejected) as caught:
            bank.apply_bank_delta(value, [{'id': 'i', 'group_id': 'missing', 'op': 'keep'}], obs)
        self.assertEqual(caught.exception.code, 'unknown_bank_group')

    def test_issue_group_append_and_replacement_do_not_retire_omitted_proposals(self):
        value = state('joint'); row = add(value, branch='joint', groups=[
            {'group_id': 'original', 'proposed_controls': ['light.exposure']}])
        gid = next(iter(row['task_groups'])); original = copy.deepcopy(row['task_groups'][gid])
        for spec in ({'group_id': 'additional', 'proposed_controls': ['wb.tint']},
                     {'group_id': 'replacement', 'proposed_controls': ['light.blacks']}):
            bank.apply_bank_delta(value, [{'id': 'i', 'op': 'revise', 'groups': [spec]}], seen(value))
            actual = bank.resolve_selected_group(value, 'i', gid, 'v0', {'values': {'light.exposure': .2}})
            self.assertEqual(actual['id'], gid)
            self.assertEqual(actual['source_proposal'], original['source_proposal'])
            self.assertEqual(actual['records'][:len(original['records'])], original['records'])
        self.assertEqual(len(value['proposal_bank']['issues']['i']['task_groups']), 3)
        bank.apply_bank_delta(value, [{'id': 'i', 'group_id': gid, 'op': 'retire'}], seen(value))
        bank.apply_bank_delta(value, [{'id': 'i', 'op': 'revise', 'groups': []}], seen(value))
        self.assertEqual(value['proposal_bank']['issues']['i']['task_groups'][gid]['status'], 'retired')

    def test_explicit_full_ids_stay_stable_for_add_and_repeated_revision(self):
        for explicit in ('sky', 'i::sky', 'i::i::i::sky'):
            value = state('joint')
            proposal = {'mask_plan': {'action': 'create', 'kind': 'linear',
                'geometry': {'start': [.5, 0], 'end': [.5, .34]}, 'values': {'exposure': -.3, 'highlights': -20, 'temp': -7}}}
            spec = {'group_id': explicit, 'proposed_controls': proposal}
            row = add(value, branch='joint', groups=[spec]); gid = next(iter(row['task_groups']))
            self.assertEqual(gid, explicit if explicit.startswith('i::') else 'i::' + explicit)
            for _ in range(3):
                bank.apply_bank_delta(value, [{'id': 'i', 'op': 'revise', 'groups': [
                    {**spec, 'group_id': gid}]}], seen(value))
                row = value['proposal_bank']['issues']['i']
                self.assertEqual(list(row['task_groups']), [gid])
                self.assertEqual(row['task_groups'][gid]['source_proposal'], proposal)
                self.assertEqual(bank.resolve_selected_group(value, 'i', gid, 'v0', proposal)['id'], gid)

    def test_full_id_split_hypothesis_no_second_issue_prefix(self):
        value = state(); row = add(value, groups=[{'group_id': 'i::split',
            'proposed_controls': ['light.exposure', 'light.contrast']}])
        self.assertEqual(set(row['task_groups']), {'i::split/tone/light/exposure', 'i::split/tone/light/contrast'})

    def test_legal_group_revision_clears_old_unresolved_without_guessing_controls(self):
        value = state('joint'); row = add(value, branch='joint', groups=[
            {'group_id': 'sky', 'proposed_controls': {'kind': 'linear', 'values': {'exposure': -.3}}}])
        gid = next(iter(row['task_groups'])); self.assertTrue(row['task_groups'][gid]['unresolved'])
        proposal = {'mask_plan': {'action': 'create', 'kind': 'linear', 'values': {'exposure': -.3}}}
        bank.apply_bank_delta(value, [{'id': 'i', 'op': 'revise', 'groups': [
            {'group_id': gid, 'proposed_controls': proposal}]}], seen(value))
        selected = bank.resolve_selected_group(value, 'i', gid, 'v0', proposal)
        self.assertFalse(selected.get('unresolved'))
        self.assertEqual(selected['source_proposal'], proposal)
        self.assertEqual(len(value['proposal_bank']['issues']['i']['task_groups']), 1)

    def test_reason_never_qualifies_unknown_stale_unresolved_or_retired_selection(self):
        value = state('joint'); row = add(value, branch='joint', groups=[
            {'group_id': 'sky', 'proposed_controls': ['light.exposure']}])
        action = {'values': {'light.exposure': .2}, 'selection_reason': 'Reason is not identity or freshness proof'}
        for gid in ('missing', 'i::i::sky'):
            with self.assertRaises(ActionRejected) as caught:
                bank.resolve_selected_group(value, 'i', gid, 'v0', action)
            self.assertEqual(caught.exception.code, 'unknown_group')
        value['versions']['v1'] = {'parent': 'v0'}; value['current'] = 'v1'
        with self.assertRaises(ActionRejected):
            bank.resolve_selected_group(value, 'i', 'sky', 'v1', action)

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
