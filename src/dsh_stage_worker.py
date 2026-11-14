"""Independent stage-profile worker; reuses module native/effect bookkeeping.

No transport, paid authorization, foundation mutation or legacy-round upgrade.
"""
import argparse
import copy
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_session_core as module
import dsh_photo_module_policy as policy
import lab_timing_experiment as lab
from photo_capability_catalog import _check
from photo_stage_rejections import ActionRejected, require_action

BUILD = 'dsh-stage-runtime-v20-20261114'
SCHEMA = 'dsh-stage-runtime/v2'
RESOURCE_PROFILE = 'stage-resource/v20'
RESOURCE_FILE = Path(__file__).with_name('photo_stage_resources.json')


def _uncap_metadata(value):
    if isinstance(value, dict):
        value.pop('maxLength', None)
        value.pop('maxItems', None)
        for child in value.values():
            _uncap_metadata(child)
    elif isinstance(value, list):
        for child in value:
            _uncap_metadata(child)


def action_schemas(phase='joint'):
    result = module.schemas()
    for name, spec in result.items():
        for field, child in spec['properties'].items():
            if field not in ('values', 'settings', 'commands', 'reference_roi', 'candidate_roi'):
                _uncap_metadata(child)
        if name in ('photo_history', 'photo_discover'):
            spec['properties']['page_size'].pop('maximum', None)
    result['photo_history']['properties'].update(
        mode={'type': 'string', 'enum': ['index', 'detail_get']},
        record_id={'type': 'string', 'minLength': 1},
        char_offset={'type': 'integer', 'minimum': 0},
        char_limit={'type': 'integer', 'minimum': 1})
    from photo_stage_schema import stage_edit_schema
    result['photo_edit']['properties'].update(stage_edit_schema(phase))
    if phase == 'pre_tone_wb':
        for field in ('settings', 'commands', 'mask_plan', 'phase'):
            result['photo_edit']['properties'].pop(field, None)
    result['photo_edit']['properties']['review']['properties']['judgment_items'] = {
        'type': 'array', 'items': module.obj({'id': {'type': 'string', 'minLength': 1},
        'region': {'type': 'string', 'minLength': 1},
        'kind': {'type': 'string', 'enum': ['tone', 'color', 'joint', 'protection', 'uncertain']},
        'judgment': {'type': 'string', 'minLength': 1}, 'evidence': {'type': 'string'},
        'uncertainty': {'type': 'string'}}, ('id', 'region', 'kind', 'judgment'))}
    text = {'type': 'string', 'minLength': 1}
    group_review = module.obj({'issue_id': text, 'group_id': text, 'reason': text,
        'outcome': {'enum': ['resolved', 'needs_reassessment', 'waiting_dependency', 'keep', 'revise', 'retire', 'defer', 'skip']},
        'operation_id': text, 'proposed_controls': {'oneOf': [{'type': 'object'}, {'type': 'array'}]},
        'priority': {'type': 'number'}, 'dependencies': {}}, ('issue_id', 'group_id', 'reason', 'outcome'))
    result['photo_edit']['properties']['review']['properties']['group_reviews'] = {'type': 'array', 'items': group_review}
    from photo_white_balance import assessment_schema, local_review_schema
    result['photo_edit']['properties']['review']['properties'].update(
        wb_assessment=assessment_schema(), wb_local_review=local_review_schema(),
        wb_pick_review=module.obj({'neutrality_basis': text, 'source_region': text,
            'coordinate_space': {'const': 'full_oriented_source'},
            'full_image_point': module.obj({'x': {'type': 'number', 'minimum': 0, 'maximum': 1},
                                           'y': {'type': 'number', 'minimum': 0, 'maximum': 1}}, ('x', 'y')),
            'roi_mapping_reason': text}, ('neutrality_basis', 'source_region', 'coordinate_space',
                                        'full_image_point', 'roi_mapping_reason')))
    from photo_tone_guidance import tone_assessment_schema
    result['photo_edit']['properties']['review']['properties'].update(
        tone_assessment=tone_assessment_schema(), tone_basis_impact=text,
        tone_boundary_review=module.obj({'operation_id': text,
            'outcome': {'enum': ['protected', 'affected', 'uncertain']},
            'evidence': text, 'joint_handoff_reason': text, 'joint_group_id': text}, ('operation_id', 'outcome', 'evidence')))
    from photo_review_anchors import anchor_schema
    result['photo_edit']['properties']['review']['properties']['review_anchors'] = {
        'type': 'array', 'items': anchor_schema()}
    for field in ('rationale', 'predicted_effect'):
        result['photo_edit']['properties'][field]['minLength'] = 1
    return result


def bank_delta_schema():
    text = {'type': 'string', 'minLength': 1}
    fields = {key: {} for key in ('evidence', 'scope', 'target_change', 'protected', 'risks', 'dependencies')}
    fields.update(branch={'type': 'string', 'enum': ['tone', 'color', 'joint']},
                  diagnosis=text, priority={'type': 'number'},
                  proposed_controls={'oneOf': [{'type': 'object'}, {'type': 'array'}]},
                  rationale=text, reason=text)
    group_fields = {'group_id': text, 'branch': fields['branch'], 'module': text, 'step': text,
        'proposed_controls': fields['proposed_controls'], 'priority': {'type': 'number'},
        'dependencies': {}, 'reason': text,
        'status': {'enum': ['ready', 'needs_reassessment', 'waiting_dependency', 'later_phase', 'current',
                           'awaiting_effect', 'resolved', 'retired', 'skipped', 'off_selected_branch', 'unresolved']},
        'reorder_override': {'oneOf': [module.obj({'order': {'type': 'number'}, 'reason': text}, ('order', 'reason')),
                                     module.obj({'before': text, 'reason': text}, ('before', 'reason'))]}}
    fields['groups'] = {'type': 'array', 'items': module.obj(group_fields, ('proposed_controls',))}
    fields.update(group_id=text, reorder_override=group_fields['reorder_override'])
    identity = {'id': text}
    add = module.obj({**identity, **fields, 'op': {'const': 'add'}}, ('op', 'id', 'branch', 'diagnosis'))
    revise = module.obj({**identity, **fields, 'op': {'const': 'revise'}}, ('op', 'id'))
    other = module.obj({**identity, 'op': {'enum': ['keep', 'retire', 'defer']},
                        'reason': text, 'rationale': text, 'group_id': text}, ('op', 'id'))
    return {'type': 'array', 'items': {'oneOf': [add, revise, other]}}


def color_reassessment_schema():
    reason = {'type': 'string', 'minLength': 1}
    return {'type': 'array', 'items': {'oneOf': [
        module.obj({'issue_id': reason, 'decision': {'enum': ['keep', 'revise', 'retire', 'new', 'defer', 'uncertain']},
                    'reason': reason}, ('issue_id', 'decision', 'reason')),
        module.obj({'decision': {'const': 'unchanged_all'}, 'reason': reason}, ('decision', 'reason'))]}}


def schemas(phase='G1', contract_revision=None, *, validation_envelope=False):
    editing_phase = phase if phase in ('tone', 'color', 'joint') else 'joint'
    actions = action_schemas(editing_phase)
    permitted = ['photo_observe', 'photo_history', 'photo_discover', 'photo_unable']
    if phase in ('tone', 'color', 'joint'):
        permitted += ['photo_edit']
    if phase in ('joint', 'G4'):
        permitted += ['photo_finish']
    branches = []
    if phase == 'G1' or validation_envelope:
        from photo_stage_schema import stage_edit_schema
        text = {'type': 'string', 'minLength': 1}
        branches.append(module.obj({'name': {'const': 'pre_tone_wb'},
            'arguments': module.obj({'base_version': text, **stage_edit_schema('pre_tone_wb'),
                'rationale': text, 'predicted_effect': text},
                ('base_version', 'values', 'rationale', 'predicted_effect'))}, ('name', 'arguments')))
    for name in permitted:
        arguments = copy.deepcopy(actions[name])
        arguments['properties'].pop('review', None)
        arguments['required'] = [field for field in arguments.get('required', []) if field != 'review']
        branches.append(module.obj({'name': {'type': 'string', 'const': name}, 'arguments': arguments}, ('name', 'arguments')))
    if phase != 'complete':
        from photo_phase_scheduler import PHASES
        next_phase = PHASES[PHASES.index(phase) + 1]
        targets = {'G2': ['color', 'tone'], 'G3': ['joint', 'tone', 'color'], 'G4': ['joint']}.get(phase, [next_phase])
        if validation_envelope:
            targets = list(PHASES)
        branches.append(module.obj({'name': {'type': 'string', 'const': 'advance'},
            'arguments': module.obj({'reason': {'type': 'string', 'minLength': 1},
                'selected_version': {'type': 'string'}, 'target_phase': {'type': 'string', 'enum': targets},
                'color_reassessment': color_reassessment_schema()}, ('reason',))}, ('name', 'arguments')))
    revision = contract_revision or 'stage-contract/v3:' + phase
    review = copy.deepcopy(actions['photo_edit']['properties']['review'])
    review['required'] = ['visible_comparison']
    review['properties']['judgment_items'] = {'type': 'array', 'items': module.obj({
        'id': {'type': 'string', 'minLength': 1}, 'region': {'type': 'string', 'minLength': 1},
        'kind': {'type': 'string', 'enum': ['tone', 'color', 'joint', 'protection', 'uncertain']},
        'judgment': {'type': 'string', 'minLength': 1}, 'evidence': {'type': 'string'},
        'uncertainty': {'type': 'string'}}, ('id', 'region', 'kind', 'judgment'))}
    from photo_review_anchors import debt_update_schema
    result = module.obj({
        'contract_revision': {'type': 'string', 'const': revision},
        'review': review, 'bank_delta': bank_delta_schema(),
        'selected_issue_id': {'type': 'string'},
        'selected_group_id': {'type': 'string', 'minLength': 1},
        'selection_reason': {'type': 'string', 'minLength': 1},
        'override_reason': {'type': 'string', 'minLength': 1},
        'review_debt_updates': {'type': 'array', 'items': debt_update_schema()},
        'action': {'oneOf': branches}},
        ('contract_revision', 'review', 'action'))
    if phase in ('G1', 'tone', 'color', 'joint') or validation_envelope:
        edit_review = copy.deepcopy(review)
        edit_review['required'] = ['visible_comparison', 'module_decisions', 'effect_expectation']
        nonedit = {'oneOf': [branch for branch in branches if branch['properties']['name']['const'] not in ('photo_edit', 'pre_tone_wb')]}
        edit = {'oneOf': [branch for branch in branches if branch['properties']['name']['const'] in ('photo_edit', 'pre_tone_wb')]}
        result['oneOf'] = [module.obj({'action': edit, 'review': edit_review,
                                      'selected_issue_id': {'type': 'string', 'minLength': 1}},
                                     ('action', 'review', 'selected_issue_id')),
                           module.obj({'action': nonedit}, ('action',))]
        # Root owns the full field envelope; these conditional branches only add
        # edit-required metadata, so they must not reject root sibling fields.
        for condition in result['oneOf']:
            condition['additionalProperties'] = True
    return {'photo_stage_turn': result}


def _validate_contract(value, spec, path='stage_turn'):
    # The shared validator covers shape/type/native ranges. These additional
    # schema assertions are needed for the dynamic, exact phase contract.
    _check(value, spec, path)
    if isinstance(value, dict):
        props = spec.get('properties', {})
        for key, child in value.items():
            if key in props:
                _validate_contract(child, props[key], path + '.' + key)
    elif isinstance(value, list) and isinstance(spec.get('items'), dict):
        for index, child in enumerate(value):
            _validate_contract(child, spec['items'], path + '[' + str(index) + ']')


class StageBackend(module.BoundedBackend):
    def _admit_native(self):
        from dsh_stage_validation import stage_native_status
        stage_native_status(lab.ROOT, self.session.round)
        ledger = lab.read(self.session.round / 'native-render-ledger.json')
        if ledger['issued'] != len(ledger['claims']) or any(c['status'] != 'completed' for c in ledger['claims']):
            raise ValueError('Native outcome pending/unknown; no native replay')
        return ledger

    def restore(self, settings):
        self.session.execution_started('restore')
        self._admit_native()
        return self.native.restore(settings)

    def apply(self, base, args):
        self.session.execution_started('apply')
        self._admit_native()
        from photo_stage_operations import apply_stage_operation
        self.trace = []
        if self.session.state.get('pending') is not None:
            self.session.state['pending']['execution_trace'] = self.trace
            self.session.save()
        self.native.stage_phase = self.session.state.get('stage_phase', 'joint')
        return apply_stage_operation(self.native, base, args, self.dependencies,
                                     self.trace, checkpoint=self.session.save)

    def render(self, path, long_edge=0, fmt='png'):
        self.session.execution_started('render')
        ledger = self._admit_native()
        if Path(path).exists():
            raise FileExistsError('Refuse existing native output')
        claim = {'id': 'native_' + uuid.uuid4().hex, 'unit': self.session.job['condition'],
                 'path': str(path), 'status': 'pending', 'format': fmt}
        ledger['claims'].append(claim)
        ledger['issued'] += 1
        file = self.session.round / 'native-render-ledger.json'
        lab.write(file, ledger)
        try:
            dimensions = self.native.render(path, long_edge, fmt)
            claim.update(status='completed', dimensions=dimensions)
            lab.write(file, ledger)
            return dimensions
        except Exception as exc:
            claim.update(error=str(exc), outcome='unknown_do_not_replay')
            lab.write(file, ledger)
            raise


class StageSession(module.ModuleSession):
    def validate_protocol(self, protocol, job):
        resources = lab.read(RESOURCE_FILE)
        if (protocol.get('schema') != SCHEMA or protocol.get('build') != BUILD
                or protocol.get('foundation_build') != module.FOUNDATION
                or protocol.get('resource_profile_id') != RESOURCE_PROFILE
                or protocol.get('resources_snapshot') != resources
                or protocol.get('workflow') != lab.WORKFLOW
                or protocol.get('visual_only') is not True
                or protocol.get('runner') != 'dsh-native/v1'
                or job.get('condition') not in ('baseline', 'module_support')
                or job.get('memory_condition') != 'B'
                or job not in protocol.get('jobs', [])):
            raise ValueError('Independent frozen stage schema/build/resource profile required')

    def _initialize_photo_session(self, round_dir, job):
        # The legacy constructor binds module globals to v11; read the new
        # frozen identity directly instead of relabelling it for that constructor.
        from dsh_stage_validation import validate_stage_frozen
        self.round = Path(round_dir)
        validate_stage_frozen(lab.ROOT, self.round)
        self.protocol = lab.read(self.round / 'protocol.json')
        self.validate_protocol(self.protocol, job)
        frozen = lab.read(self.round / 'qualification' / 'frozen.json')
        if (frozen.get('build') != BUILD or frozen.get('status') != 'qualified'
                or frozen.get('protocol_snapshot') != self.protocol):
            raise lab.LocalDispatchDenied('Stage build/protocol has not been frozen and qualified')
        self.job = job
        self.input_folder = self.round / 'inputs' / job['photo_id']
        self.input = lab.read(self.input_folder / 'input.json')
        if not self.input.get('raw_decode_qualified'):
            raise ValueError('Full RAW decode qualification required')
        self.anchors = lab.read(self.input_folder / 'anchors.json')
        self.reference = lab.read(self.input_folder / 'reference-measurements.json')
        if self.protocol.get('paid_eligible') is True:
            if (self.protocol.get('raw_render_contract') != lab.RAW_RENDER_CONTRACT
                    or self.input.get('raw_render_contract') != lab.RAW_RENDER_CONTRACT):
                raise lab.LocalDispatchDenied('Qualified RAW source-display contract required')
            lab.inspect_dng(self.input['source'])
        self.folder = self.round / 'editing' / job['photo_id'] / ('r' + str(job['repeat'])) / job['condition']
        self.folder.mkdir(parents=True, exist_ok=True)
        self.file = self.folder / 'state.json'
        self.native = None
        self.condition = 'B'
        self.allow_overlapping_views = True
        self.state = lab.read(self.file) if self.file.exists() else {
            'job': copy.deepcopy(job), 'execution_workflow': self.protocol['workflow'], 'status': 'working', 'current': 'v0',
            'versions': {'v0': {'settings': lab.read(self.input['initial_settings']), 'master': self.input['v0'],
                                'parent': None, 'measurements': lab.read(self.input_folder / 'v0-measurements.json')}},
            'observations': [], 'history': [], 'reviewed': [], 'export': None, 'visible_packets': []}
        continued = False
        if not self.file.exists():
            from photo_stage_continuation import load_continuation
            continuation = load_continuation(self.round, lab.ROOT)
            if continuation is not None:
                self.state = copy.deepcopy(continuation['continued_state'])
                self.state['continuation_provenance'] = {k: continuation[k] for k in
                    ('schema', 'source_round', 'selected_version', 'native_operations_replayed',
                     'origin_round', 'continuation_chain', 'origin_source_round', 'immediate_source_round',
                     'source_chain', 'source_close_proof') if k in continuation}
                continued = True
        if self.state.get('job') != job or self.state.get('execution_workflow') != lab.WORKFLOW:
            raise lab.LocalDispatchDenied('Stage checkpoint job/workflow mismatch')
        if self.state.get('memory_condition', 'B') != 'B':
            raise lab.LocalDispatchDenied('Cannot change active memory condition')
        if self.state.get('pending') or self.state['status'] != 'working':
            raise lab.LocalDispatchDenied('Checkpoint requires offline reconciliation')
        self.state['memory_condition'] = 'B'
        from photo_phase_scheduler import init_workflow
        init_workflow(self.state)
        self.state['stage_phase'] = self.state['workflow']['phase'] if self.state['workflow']['phase'] in ('tone', 'color', 'joint') else 'joint'
        if continued or not self.state['observations']:
            self.observe(self.state['current'], args={'comparison_mode': 'target'})
        self.save()
        from photo_lifecycle_audit import record_lifecycle
        record_lifecycle(self.folder, self.state, 'initialized')

    def contract_revision(self):
        workflow = self.state['workflow']
        return 'stage-contract/v3:' + workflow['phase'] + ':' + str(workflow['revision'])

    def tool_schemas(self):
        return schemas(self.state['workflow']['phase'], self.contract_revision())

    def execution_started(self, boundary):
        """Persist before every mutating boundary; never clear this fence on failure."""
        marker = {'turn_id': getattr(self, '_active_turn_id', None), 'boundary': boundary,
                  'id': 'execution:' + uuid.uuid4().hex}
        with (self.folder / 'execution-started.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(marker, ensure_ascii=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        self.state['execution_started'] = marker
        self.save()
        if lab.read(self.file).get('execution_started') != marker:
            raise RuntimeError('Execution fence persistence mismatch')

    def _photography_state(self):
        return copy.deepcopy({key: self.state.get(key) for key in
            ('current', 'versions', 'export', 'status', 'pending', 'selected_version', 'workflow', 'stage_phase')})

    def _submission_record(self, submission, observation_id, phase, **details):
        normalization = getattr(self, '_turn_normalization', None) or {}
        self.state.setdefault('workflow_submissions', []).append({
            'id': 'turn-' + str(len(self.state.get('workflow_submissions', [])) + 1),
            'source_observation_id': observation_id, 'phase': phase,
            'submission': copy.deepcopy(normalization.get('original', submission)),
            'normalized_submission': copy.deepcopy(normalization.get('canonical', submission)),
            'normalization_warnings': copy.deepcopy(normalization.get('warnings', [])),
            'result_version': self.state['current'],
            'result_phase': self.state['workflow']['phase'], **details})

    def stage_turn(self, submission, source_observation_id=None):
        module.public_only(submission)
        from photo_stage_submission import normalize_submission
        canonical, warnings = normalize_submission(submission)
        review = canonical.get('review', {}) if isinstance(canonical, dict) else {}
        effect = review.get('effect_review') if isinstance(review, dict) else None
        if isinstance(effect, dict):
            observed = self.state['observations'][-1]['version']
            matches = [row for row in self.state.get('effect_expectations', [])
                       if row.get('child_version') == observed and effect.get('expectation_id') in
                       (row.get('id'), row.get('operation_id'))]
            if len(matches) == 1 and effect['expectation_id'] != matches[0]['id']:
                warnings.append({'code': 'exact_operation_expectation_alias',
                    'field_path': 'review.effect_review.expectation_id', 'original': effect['expectation_id'],
                    'canonical': matches[0]['id'], 'operation_id': matches[0]['operation_id'],
                    'version': observed})
                effect['expectation_id'] = matches[0]['id']
            actual = next((row for row in reversed(self.state.get('effect_expectations', []))
                           if row.get('child_version') == observed), None)
            if actual and any(row.get('expectation_id') == actual['id'] and row.get('version') == observed
                              for row in self.state.get('effect_reviews', [])):
                warnings.append({'code': 'redundant_effect_review_not_adopted',
                    'field_path': 'review.effect_review', 'metadata_extra': copy.deepcopy(effect),
                    'actual_expectation_id': actual['id'], 'version': observed,
                    'message': 'The actual effect was already reviewed; this redundant statement changes no prior judgment and does not veto the next explicit action.'})
                review.pop('effect_review')
        self._turn_normalization = {'original': copy.deepcopy(submission),
                                    'canonical': canonical, 'warnings': warnings}
        from photo_lifecycle_audit import record_lifecycle
        phase = self.state['workflow']['phase']
        observation_id = self.state['observations'][-1]['id']
        before = copy.deepcopy(self.state)
        photograph = self._photography_state()
        ledger_path = self.round / 'native-render-ledger.json'
        ledger = ledger_path.read_bytes()
        fence_path = self.folder / 'execution-started.jsonl'
        fence = fence_path.read_bytes() if fence_path.exists() else b''
        turn_id = record_lifecycle(self.folder, self.state, 'turn_before', submission=submission)
        self._active_turn_id = turn_id
        self._accepted_turn_state = None
        try:
            result = self._stage_turn(canonical, source_observation_id)
            if before['current'] != self.state['current']:
                from photo_working_context import record_branch_change
                from photo_phase_scheduler import reconcile_debt_applicability
                event = record_branch_change(self.state, before['current'], canonical['action']['name'], observation_id)
                reconcile_debt_applicability(self.state, self.state['current'],
                    selection_event_id=event['id'] if event else None,
                    observation_id=self.state['observations'][-1]['id'])
                from photo_proposal_bank import reconcile_dependencies
                reconcile_dependencies(self.state, observation_id)
                self.save()
                result = self.snapshot()
            record_lifecycle(self.folder, self.state, 'turn_after', submission=submission, parent_turn_id=turn_id)
            return result
        except ActionRejected as exc:
            persisted = lab.read(self.file)
            crossed = any(s.get('execution_started', {}).get('turn_id') == turn_id for s in (self.state, persisted))
            safe = (not crossed and before.get('pending') is None
                    and self.state.get('pending') is None and persisted.get('pending') is None
                    and self._photography_state() == photograph and ledger_path.read_bytes() == ledger
                    and (fence_path.read_bytes() if fence_path.exists() else b'') == fence
                    and {key: persisted.get(key) for key in photograph} == json.loads(json.dumps(photograph))
                    and self.state.get('execution_started') == before.get('execution_started')
                    and persisted.get('execution_started') == before.get('execution_started')
                    and [o['id'] for o in self.state['observations']] == [o['id'] for o in before['observations']])
            if not safe:
                record_lifecycle(self.folder, self.state, 'turn_failed', submission=submission,
                                 parent_turn_id=turn_id, error=exc)
                raise RuntimeError('Action rejection crossed execution/state boundary; retain evidence') from exc
            # A legal review/bank ingestion is its own transaction. Debt/effect
            # acceptance and workflow changes exist only in the action draft.
            self.state = copy.deepcopy(self._accepted_turn_state or before)
            ref = 'rejection:' + uuid.uuid4().hex
            result = {'kind': 'rejected_before_execution', 'recoverable': True,
                'execution_status': 'not_executed', 'code': exc.code, 'message': exc.message,
                'field_path': exc.field_path, 'no_effect': {'passed': True,
                    'native_execution_started': False, 'pending': None, 'versions_unchanged': True,
                    'ledger_unchanged': True, 'photography_unchanged': True},
                'accepted_bank_revision': self.state['proposal_bank']['revision'], 'rejection_ref': ref}
            self.state['stage_turn_result'] = copy.deepcopy(result)
            self.state['tool_result'] = copy.deepcopy(result)
            self.state.setdefault('stage_rejections', []).append({'id': ref, **copy.deepcopy(result),
                'submission': copy.deepcopy(submission), 'source_observation_id': observation_id,
                'parent_turn_id': turn_id})
            self._submission_record(submission, observation_id, phase, stage_turn_result=copy.deepcopy(result))
            self.save()
            if lab.read(self.file) != json.loads(json.dumps(self.state)) or ledger_path.read_bytes() != ledger:
                raise RuntimeError('Rejection persistence/no-effect verification failed')
            record_lifecycle(self.folder, self.state, 'turn_rejected', submission=submission,
                             parent_turn_id=turn_id, error=exc)
            return self.snapshot()
        except Exception as exc:
            # No inferred rollback after an unknown result. Preserve all evidence.
            self._submission_record(submission, observation_id, phase,
                                    stage_turn_result={'kind': 'fatal', 'message': str(exc)})
            self.save()
            record_lifecycle(self.folder, self.state, 'turn_failed', submission=submission,
                             parent_turn_id=turn_id, error=exc)
            raise
        finally:
            self._active_turn_id = None
            self._accepted_turn_state = None
            self._turn_normalization = None

    def _review_for_operation(self, review, arguments):
        """Project typed operation coverage, not a photographic module choice."""
        review = copy.deepcopy(review)
        actual = list(dict.fromkeys(self._operation_modules(arguments)))
        declared = review['module_decisions']
        uses = {module.canonical_module(row['module']) for row in declared if row['choice'] == 'use'}
        if set(actual) != uses:
            review['module_decisions'] = [row for row in review['module_decisions']
                if module.canonical_module(row['module']) not in actual and row['choice'] != 'use']
            review['module_decisions'] += [{'module': ident, 'choice': 'use',
                'reason': 'Derived execution coverage from the explicit typed operation; model declaration retained separately.'}
                for ident in actual]
        return review

    def _validate_turn_review(self, review, observation, name, arguments):
        self._validate_public_review(review)
        require_action(bool(review.get('visible_comparison', '').strip()), 'visible_review_required',
                       'Workflow turn needs an explicit public comparison of supplied pixels', 'review.visible_comparison')
        prior = next((x for x in reversed(self.state.get('effect_expectations', []))
                      if x.get('child_version') == observation['version']), None)
        reviewed = prior and any(x.get('expectation_id') == prior['id'] and x.get('version') == observation['version']
                                 for x in self.state.get('effect_reviews', []))
        effect = review.get('effect_review')
        if prior and not reviewed and name in ('photo_edit', 'pre_tone_wb', 'photo_finish', 'advance'):
            require_action(bool(effect), 'effect_review_required', 'Review the actual prior effect before accepting another action', 'review.effect_review')
        if effect:
            expectation = next((x for x in self.state.get('effect_expectations', []) if x['id'] == effect['expectation_id']), None)
            require_action(expectation and expectation.get('child_version') == observation['version'],
                'effect_review_version', 'Effect review refers to another or unknown version', 'review.effect_review.expectation_id')
            require_action(observation.get('comparison_mode') == 'effect' or effect['outcome'] not in ('supported', 'contradicted'),
                'effect_view_required', 'A definitive effect comparison requires actually delivered parent/current views', 'review.effect_review.outcome')
        if name in ('photo_edit', 'pre_tone_wb'):
            base = arguments.get('base_version', self.state['current'])
            require_action(base in self.state['versions'] and base == observation['version'], 'edit_base_not_delivered',
                           'Effect expectation must use the actually delivered base version', 'action.arguments.base_version')
            actual = self._operation_modules(arguments)
            require_action(bool(actual), 'operation_modules_required',
                           'The explicit operation must identify known native modules', 'action.arguments')

    def _white_balance_review(self, state, review, observation, phase):
        if 'wb_assessment' not in review:
            return None
        from photo_white_balance import validate_assessment
        require_action(phase in ('G1', 'G2', 'color', 'G3', 'joint', 'G4'), 'wb_color_view_required',
                       'White-balance judgment needs actual color views, never gray tone.', 'review.wb_assessment')
        try:
            bound = validate_assessment(review['wb_assessment'], observation, require_target=True)
        except ValueError as exc:
            raise ActionRejected('wb_assessment_invalid', str(exc), 'review.wb_assessment') from exc
        bound.update(observation_id=observation['id'], settings_version=observation['version'], phase=phase)
        records = state.setdefault('wb_assessments', [])
        if not records or records[-1] != bound:
            records.append(copy.deepcopy(bound))
        return bound

    def _white_balance_action_review(self, review, arguments, observation, phase):
        from photo_white_balance import validate_local_wb_review
        settings = self.state['versions'][observation['version']]['settings']
        plan = arguments.get('mask_plan', {})
        no_existing_create = (phase in ('color', 'joint') and plan.get('action') == 'create'
            and set(plan.get('values', {})).intersection(('temp', 'tint'))
            and settings.get('masks') == [] and 'wb_local_review' not in review)
        if no_existing_create:
            local = {'decision': 'create', 'existing_mask_ids': [],
                'region': review.get('effect_expectation', {}).get('target', arguments.get('rationale', '')),
                'reason': arguments.get('rationale', ''), 'observation_id': observation['id'],
                'settings_version': observation['version'], 'phase': phase,
                'claim_type': 'explicit_model_plan_with_actual_empty_mask_list_not_independent_semantic_review'}
        else:
            try:
                local = validate_local_wb_review(review, arguments, settings, phase, observation)
            except ValueError as exc:
                adjusted = []
                if plan.get('action') == 'adjust' and set(plan.get('values', {})).intersection(('temp', 'tint')):
                    adjusted.append(plan.get('id'))
                adjusted.extend(command.get('params', {}).get('id') for command in arguments.get('commands', [])
                    if command.get('id') == 'mask.adjust' and set(command.get('params', {}).get('values', {})).intersection(('temp', 'tint')))
                actual_ids = {mask.get('id') for mask in settings.get('masks', [])}
                if adjusted and len(set(adjusted)) == 1 and type(adjusted[0]) is int and adjusted[0] in actual_ids:
                    local = {'decision': 'reuse', 'mask_id': adjusted[0], 'actual_mask_id': adjusted[0],
                        'region': review.get('effect_expectation', {}).get('target', arguments.get('rationale', '')),
                        'reason': arguments.get('rationale', ''), 'observation_id': observation['id'],
                        'settings_version': observation['version'], 'phase': phase,
                        'submitted_reuse_review': copy.deepcopy(review.get('wb_local_review')),
                        'review_warning': str(exc),
                        'claim_type': 'explicit_model_adjust_id_from_actual_readback_not_inferred_region_equivalence'}
                else:
                    raise ActionRejected('wb_mask_reuse_review', str(exc), 'review.wb_local_review') from exc
        picks = [command for command in arguments.get('commands', []) if command.get('id') == 'develop.wbPick']
        if picks:
            dependencies = self.backend().dependencies
            commands = dependencies.get('commands', []) if isinstance(dependencies, dict) else []
            require_action(any(row.get('id') == 'develop.wbPick' and row.get('enabled') is True for row in commands),
                'wb_pick_unavailable', 'Native WB picker is unavailable; choose an explicit scalar WB edit instead, never a guessed picker result.',
                'action.arguments.commands')
            assessment = next((row for row in reversed(self.state.get('wb_assessments', []))
                if row.get('settings_version') == observation['version'] and row.get('reference_status') == 'available'), None)
            require_action(bool(assessment), 'wb_pick_neutral_reference_required',
                           'An optional picker needs a qualified current-version neutral reference; use scalar WB otherwise.',
                           'review.wb_assessment')
            grounding = review.get('wb_pick_review')
            require_action(bool(grounding), 'wb_pick_grounding_required',
                           'Identify a physically neutral source region and full-oriented-source point; never use author/crop coordinates.',
                           'review.wb_pick_review')
            for command in picks:
                require_action(command['params'] == grounding['full_image_point'], 'wb_pick_coordinate_mismatch',
                               'The reviewed full-source point must exactly match the explicit picker coordinates.',
                               'review.wb_pick_review.full_image_point')
                roi = observation.get('view_spec', {}).get('candidate_roi')
                if roi is not None:
                    point = command['params']
                    require_action(roi[0] <= point['x'] <= roi[2] and roi[1] <= point['y'] <= roi[3],
                        'wb_pick_point_not_observed', 'The full-source point must be visible inside the current source ROI; map coordinates or obtain a suitable view.',
                        'review.wb_pick_review.full_image_point')
        return local

    def _white_balance_resolution_review(self, state, review, observation):
        for disposition in review.get('group_reviews', []):
            if disposition.get('outcome') != 'resolved':
                continue
            from photo_proposal_bank import group_for_review
            group = group_for_review(state, disposition['issue_id'], disposition['group_id'])
            if not group or group.get('module') != 'wb':
                continue
            assessment = next((row for row in reversed(state.get('wb_assessments', []))
                if row.get('settings_version') == observation['version']
                and row.get('target_comparison') and row.get('phase') == observation.get('phase')
                and row.get('issue_id', disposition['issue_id']) == disposition['issue_id']
                and (row.get('group_id') is None or
                     (group_for_review(state, disposition['issue_id'], row['group_id']) or {}).get('id') == group['id'])), None)
            require_action(bool(assessment), 'wb_target_evidence_required',
                'Resolving a global WB group needs a target/current assessment of this actual version; effect support alone is not author matching.',
                'review.group_reviews')

    def _tone_boundary_review(self, state, review, observation):
        expectation = next((row for row in reversed(state.get('effect_expectations', []))
                            if row.get('child_version') == observation['version']), None)
        if not expectation or not review.get('effect_review'):
            return
        debt = next((row for row in state['workflow']['review_debts']
                     if row.get('operation_id') == expectation['operation_id'] and row.get('tone_boundary')), None)
        if not debt:
            return
        if any(row.get('operation_id') == expectation['operation_id'] for row in state.get('tone_boundary_reviews', [])):
            return
        value = review.get('tone_boundary_review')
        require_action(bool(value), 'tone_boundary_review_required',
            'Review the protected region, feather neighborhood and outside effect, or explicitly state uncertainty in this same effect review.',
            'review.tone_boundary_review')
        require_action(value['operation_id'] == expectation['operation_id']
                       and observation.get('comparison_mode') == 'effect', 'tone_boundary_source',
                       'Boundary review must bind this actual parent/child operation, not a target or old version.',
                       'review.tone_boundary_review.operation_id')
        if value['outcome'] == 'affected':
            require_action(bool(value.get('joint_handoff_reason', '').strip()), 'tone_boundary_handoff_reason',
                'Explain the Joint boundary residual, or why immediate small Tone repair is preferable.',
                'review.tone_boundary_review.joint_handoff_reason')
            if value.get('joint_group_id'):
                from photo_proposal_bank import group_for_review
                group = group_for_review(state, debt['issue_id'], value['joint_group_id'])
                require_action(group and group.get('branch') == 'joint', 'tone_boundary_joint_group',
                    'A handoff references an actual Joint group under this same issue; add/revise it in bank_delta.',
                    'review.tone_boundary_review.joint_group_id')
        state.setdefault('tone_boundary_reviews', []).append({**copy.deepcopy(value),
            'observation_id': observation['id'], 'version': observation['version'],
            'issue_id': debt['issue_id'], 'group_id': debt['group_id'],
            'claim_type': 'model_boundary_review_not_measured_gray_values'})

    def _stage_turn(self, submission, source_observation_id=None):
        from photo_phase_scheduler import PHASES, advance_workflow, resolve_review_debts, revisit_workflow
        from photo_proposal_bank import (apply_bank_delta, resolve_selected_group, record_operation,
                                         record_group_review, invalidate_bank)
        if (self.state.get('pending') is not None or self.state['status'] != 'working'
                or self.state.get('workflow', {}).get('pre_tone_wb', {}).get('status') == 'in_flight'):
            raise lab.SafetyStop('Workflow has unknown/stopped work; no replay')
        # Validate the closed broad envelope before classifying a known action
        # as outside this phase; unknown native fields remain fatal wire errors.
        _validate_contract(submission, schemas('joint', self.contract_revision(), validation_envelope=True)['photo_stage_turn'])
        observation = self.state['observations'][-1]
        require_action(source_observation_id is None or source_observation_id == observation['id'],
                       'stale_source_observation', 'Workflow turn must bind the current actually projected observation', 'source_observation_id')
        review = submission['review']
        name = submission['action']['name']
        arguments = copy.deepcopy(submission['action']['arguments'])
        phase = self.state['workflow']['phase']
        # Ingest legal public judgments and bank independently of action acceptance.
        accepted = copy.deepcopy(self.state)
        normalization = getattr(self, '_turn_normalization', None) or {}
        turn_warnings = copy.deepcopy(normalization.get('warnings', []))
        if name in ('photo_edit', 'pre_tone_wb'):
            actual = list(dict.fromkeys(self._operation_modules(arguments)))
            declared = copy.deepcopy(review['module_decisions'])
            coverage = {'observation_id': observation['id'], 'actual_modules': actual,
                        'model_declared': declared}
            accepted.setdefault('module_coverage_records', []).append(coverage)
            uses = {module.canonical_module(row['module']) for row in declared if row['choice'] == 'use'}
            if set(actual) != uses:
                turn_warnings.append({'code': 'module_coverage_mismatch',
                    'field_path': 'review.module_decisions', **copy.deepcopy(coverage),
                    'message': 'Execution coverage derives from the explicit typed operation; no native arguments changed.'})
        accepted['normalization_warnings'] = turn_warnings
        current = accepted['observations'][-1]
        current['decision_judgment'] = self.normalize_public_review(review)
        current['review_submitted'] = True
        require_action(bool(current['decision_judgment']['visible_comparison'].strip()),
                       'visible_review_required', 'Workflow turn needs an explicit public comparison', 'review.visible_comparison')
        sources = accepted.setdefault('model_action_source_observations', [])
        if observation['id'] not in sources:
            sources.append(observation['id'])
        bank_before = copy.deepcopy(accepted['proposal_bank'])
        delta = submission.get('bank_delta', [])
        apply_bank_delta(accepted, delta, observation['id'])
        from photo_proposal_bank import reconcile_dependencies
        reconcile_dependencies(accepted, observation['id'])
        if 'review_anchors' in review:
            from photo_review_anchors import bind_anchors
            try:
                bound_anchors = bind_anchors(review['review_anchors'], observation)
            except ValueError as exc:
                bound_anchors = []
                accepted['normalization_warnings'].append({'code': 'optional_anchors_not_adopted',
                    'field_path': 'review.review_anchors', 'message': str(exc),
                    'metadata_extra': copy.deepcopy(review['review_anchors']),
                    'rule': 'No invalid role/cell is promoted to source evidence; debt falls back to explicit unknown focus. Native arguments are unchanged.'})
            accepted.setdefault('review_anchor_records', []).append({'observation_id': observation['id'],
                'version': observation['version'], 'anchors': bound_anchors,
                'submitted_anchors': copy.deepcopy(review['review_anchors'])})
        wb_assessment = self._white_balance_review(accepted, review, observation, phase)
        if wb_assessment:
            from photo_proposal_bank import bind_wb_assessment
            bind_wb_assessment(accepted, wb_assessment)
        tone_assessment = None
        if 'tone_assessment' in review:
            from photo_tone_guidance import validate_tone_assessment
            try:
                tone_assessment = validate_tone_assessment(review['tone_assessment'], observation)
            except ValueError as exc:
                raise ActionRejected('tone_assessment_invalid', str(exc), 'review.tone_assessment') from exc
            accepted.setdefault('tone_assessments', []).append(copy.deepcopy(tone_assessment))
            from photo_proposal_bank import bind_tone_assessment
            bind_tone_assessment(accepted, tone_assessment)
        self.state = accepted
        self._record_public_review(current['decision_judgment'], current)
        self.save()
        self._accepted_turn_state = copy.deepcopy(self.state)
        permitted = {b['properties']['name']['const'] for b in self.tool_schemas()['photo_stage_turn']['properties']['action']['oneOf']}
        require_action(name in permitted, 'action_outside_phase', 'Current action is outside the authoritative phase', 'action.name')
        if name in ('photo_edit', 'pre_tone_wb'):
            from photo_stage_schema import stage_edit_schema
            # The same fields already passed the broad native contract. Only a
            # known phase narrowing can fail this pure schema check.
            scope = 'pre_tone_wb' if name == 'pre_tone_wb' else phase
            for field, spec in stage_edit_schema(scope).items():
                if field in arguments:
                    try:
                        _validate_contract(arguments[field], spec, 'action.arguments.' + field)
                    except ValueError as exc:
                        raise ActionRejected('operation_outside_phase', str(exc), 'action.arguments.' + field) from exc
        self._validate_turn_review(review, observation, name, arguments)
        draft = copy.deepcopy(self.state)
        # The action transaction reviews the prior operation before reassessing
        # proposals, so a same-turn keep/revise can make its next group eligible.
        draft['proposal_bank'] = copy.deepcopy(bank_before)
        self._white_balance_resolution_review(draft, review, observation)
        record_group_review(draft, review, observation['id'])
        apply_bank_delta(draft, delta, observation['id'])
        from photo_proposal_bank import reconcile_dependencies
        reconcile_dependencies(draft, observation['id'])
        if wb_assessment:
            from photo_proposal_bank import bind_wb_assessment
            bind_wb_assessment(draft, wb_assessment)
        if tone_assessment:
            from photo_proposal_bank import bind_tone_assessment
            bind_tone_assessment(draft, tone_assessment)
        self._tone_boundary_review(draft, review, observation)
        if submission.get('review_debt_updates'):
            updates = submission['review_debt_updates']
            try:
                resolve_review_debts(draft, updates, observation['id'])
            except (ActionRejected, ValueError) as exc:
                # Debt evidence is independent of an explicit legal action.
                # Refusing its adoption must not silently resolve it or veto IO.
                deferred = {'observation_id': observation['id'], 'version': observation['version'],
                    'phase': phase, 'submitted_updates': copy.deepcopy(updates), 'reason': str(exc),
                    'disposition': 'not_adopted_debts_unchanged'}
                draft.setdefault('deferred_debt_reviews', []).append(deferred)
                draft.setdefault('normalization_warnings', []).append({
                    'code': 'debt_updates_not_adopted', 'field_path': 'review_debt_updates',
                    **copy.deepcopy(deferred),
                    'message': 'No debt was resolved by this invalid/untimely batch. Continue the explicit legal action and reassess pending debt on a qualified checkpoint.'})
        selected_group = None
        local_wb_review = None
        action_context = copy.deepcopy(submission['action'])
        action_context.update({key: submission[key] for key in ('selection_reason', 'override_reason') if key in submission})
        if name == 'advance':
            if phase == 'joint':
                export = draft.get('export')
                require_action(bool(export) and export.get('version') == observation['version']
                               and not export.get('confirmation_invalidated_by'),
                               'actual_export_required',
                               'Use photo_finish confirm_export:false to create the actual JPEG before entering G4',
                               'action.name')
            target = arguments.get('target_phase')
            next_phase = PHASES[PHASES.index(phase) + 1] if phase != 'complete' else None
            selected = arguments.get('selected_version', observation['version'])
            require_action(selected == observation['version'], 'selected_version_not_observed',
                           'Observe the selected best version before accepting it', 'action.arguments.selected_version')
            if phase == 'G4' or (target is not None and target != next_phase):
                revisit_workflow(draft, arguments['reason'], observation['id'], target)
            else:
                if phase == 'G1':
                    assessment = next((row for row in reversed(draft.get('tone_assessments', []))
                        if row.get('phase') == 'G1' and row.get('settings_version') == selected), None)
                    require_action(bool(assessment), 'tone_assessment_required',
                        'Record structural/chroma/uncertain attribution or explicitly no relevant difference in this same G1 turn.',
                        'review.tone_assessment')
                if phase == 'G2':
                    assessment = next((row for row in reversed(draft.get('wb_assessments', []))
                        if row.get('phase') == 'G2' and row.get('settings_version') == selected), None)
                    require_action(bool(assessment), 'wb_assessment_required',
                        'Record the current G2 WB basis, or explicitly no reliable reference/no WB task, in this same turn.',
                        'review.wb_assessment')
                # The bank was already ingested; validate G2 against that exact
                # original delta without applying it a second time.
                advance_workflow(draft, arguments['reason'], observation['id'], selected,
                    target_phase=target, bank_delta=delta, bank_before=bank_before,
                    color_reassessment=arguments.get('color_reassessment'))
                draft['current'] = selected
                if draft.get('export') and draft['export']['version'] != selected:
                    draft['export'] = None
            self.state = draft
            # Record effect acceptance only after the whole transition validated.
            self.act('photo_history', {'review': copy.deepcopy(review)}, observation['id'])
            self.state['implicit_history_projection'] = True
            self.state['stage_phase'] = self.state['workflow']['phase'] if self.state['workflow']['phase'] in ('tone', 'color', 'joint') else 'joint'
            self.save()
            self.observe(self.state['current'], args={'comparison_mode': 'target'}, requested=False)
        else:
            if name in ('photo_edit', 'pre_tone_wb'):
                base = arguments.get('base_version', self.state['current'])
                require_action(bool(submission.get('selected_issue_id')), 'issue_selection_required',
                               'Select an explicit bank issue for the current edit group', 'selected_issue_id')
                selected_group = resolve_selected_group(draft, submission['selected_issue_id'],
                    submission.get('selected_group_id'), base, action=action_context)
                scope = self.state['stage_phase']
                if name == 'pre_tone_wb':
                    from photo_pre_tone import validate_request
                    validate_request(draft, review, observation)
                    scope = 'pre_tone_wb'
                else:
                    local_wb_review = self._white_balance_action_review(review, arguments, observation, phase)
                from photo_stage_operations import validate_stage_operation
                try:
                    validate_stage_operation(arguments, draft['versions'][base]['settings'],
                        scope, self.backend().dependencies, controls=self.backend().controls())
                except ValueError as exc:
                    raise ActionRejected('stage_operation_invalid', str(exc), 'action.arguments') from exc
                from photo_working_context import geometry_proximity
                proximity = geometry_proximity(draft['versions'][base]['settings'], arguments.get('mask_plan'))
                draft['geometry_warnings'] = [{**row, 'source_observation_id': observation['id'],
                    'base_version': base, 'claim_type': 'host_geometry_hint_not_semantic_equivalence'} for row in proximity]
            elif name == 'photo_finish':
                self._validate_finish(arguments, observation, phase)
            self.state = draft
            arguments['review'] = copy.deepcopy(review)
            before = self.state['current']
            if name == 'pre_tone_wb':
                self.state['stage_phase'] = 'pre_tone_wb'
                self.state['workflow']['pre_tone_wb'] = {'status': 'in_flight', 'base_version': before,
                    'source_observation_id': observation['id'], 'tone_basis_impact': review['tone_basis_impact']}
                self.save()
                self.act('photo_edit', arguments, observation['id'])
                child = self.state['current']
                actual = self.state['versions'][child]
                self.state['workflow']['pre_tone_wb'].update(status='succeeded', child_version=child,
                    operation_id=actual['operation_id'], expectation_id=actual['expectation_id'])
                self.state['stage_phase'] = 'joint'
                history = next(row for row in reversed(self.state['history'])
                               if row.get('operation_id') == actual['operation_id'] and row.get('action') == 'edit')
                history['stage_action'] = 'pre_tone_wb'
            else:
                self.act(name, arguments, observation['id'])
            if name in ('photo_edit', 'pre_tone_wb'):
                child = self.state['current']
                record_operation(self.state, submission['selected_issue_id'],
                    self.state['versions'][child]['operation_id'], arguments.get('base_version', before), child,
                    group_id=selected_group['id'], action=action_context)
                invalidate_bank(self.state, child)
                base_version = arguments.get('base_version', before)
                if local_wb_review:
                    if local_wb_review.get('decision') == 'create':
                        previous_ids = {mask['id'] for mask in self.state['versions'][base_version]['settings'].get('masks', [])}
                        created = [mask['id'] for mask in self.state['versions'][child]['settings'].get('masks', [])
                                   if mask['id'] not in previous_ids]
                        if len(created) != 1:
                            raise RuntimeError('Completed local WB creation has ambiguous actual mask readback; retain execution evidence')
                        local_wb_review['actual_mask_id'] = created[0]
                    else:
                        local_wb_review['actual_mask_id'] = local_wb_review['mask_id']
                    self.state.setdefault('wb_mask_usage', []).append({**copy.deepcopy(local_wb_review),
                        'operation_id': self.state['versions'][child]['operation_id'], 'base_version': base_version,
                        'child_version': child, 'issue_id': submission['selected_issue_id'], 'group_id': selected_group['id']})
                from photo_working_context import mask_geometry_changes
                geometry_changes = mask_geometry_changes(self.state['versions'][base_version]['settings'],
                                                         self.state['versions'][child]['settings'])
                if phase in ('tone', 'color') or (phase == 'joint' and geometry_changes):
                    from photo_phase_scheduler import add_review_debt
                    from photo_review_anchors import build_debt_focus
                    operation_id = self.state['versions'][child]['operation_id']
                    debt = {'id': 'debt:' + operation_id, 'schema': 'photo-review-debt/v2',
                        'focus': build_debt_focus(review, observation, operation_id, review['effect_expectation']),
                        'due_phase': 'G2' if phase == 'tone' else 'G3' if phase == 'color' else 'G4', 'version': child,
                        'operation_id': self.state['versions'][child]['operation_id'], 'base_version': base_version,
                        'issue_id': submission['selected_issue_id'], 'group_id': selected_group['id'],
                        'reason': 'Review color consequences of gray tone work' if phase == 'tone' else 'Reassess accepted tone after color work'}
                    if geometry_changes:
                        debt['mask_geometry_changes'] = geometry_changes
                        debt['geometry_review_reason'] = 'Geometry/visibility/inversion can change accepted Tone and WB coverage; review actual color boundaries and protected regions, not just unchanged numeric sliders.'
                    from photo_tone_guidance import tone_boundary_detail, color_luminance_detail
                    if phase == 'tone':
                        boundary = tone_boundary_detail(self.state['versions'][base_version]['settings'],
                            self.state['versions'][child]['settings'], arguments, review['effect_expectation'])
                        if boundary:
                            debt.update(kind='tone_boundary_color_review', tone_boundary=boundary,
                                reason='Recheck actual Tone mask boundary and protected region after color returns; grayscale fusion does not prove color-edge safety.')
                    if phase == 'color':
                        luminance = color_luminance_detail(self.state['versions'][base_version]['settings'],
                                                           self.state['versions'][child]['settings'])
                        if luminance:
                            debt.update(kind='color_luminance_tone_review', hsl_luminance_changes=luminance,
                                reason='Review actual HSL luminance changes against the accepted tone skeleton; do not compensate the same color-associated region twice.')
                        from photo_white_balance import coupling_detail
                        coupling = coupling_detail(self.state['versions'][base_version]['settings'],
                                                   self.state['versions'][child]['settings'], action_context)
                        if coupling:
                            debt.update(kind='wb_tone_coupling', wb_coupling=coupling,
                                reason='Review actual WB tone coupling in affected regions: highlight detail, shadow support and accepted environment color; adjust only justified small Joint residuals.',
                                wb_assessment_ref=copy.deepcopy(self.state.get('wb_assessments', [])[-1:]),
                                local_wb_review=copy.deepcopy(local_wb_review))
                    add_review_debt(self.state, debt)
            if name == 'photo_finish' and self.state.get('export') and not arguments.get('confirm_export') and phase == 'joint':
                advance_workflow(self.state, arguments.get('rationale', 'Inspect actual exported JPEG'),
                    observation['id'], self.state['current'])
                self.state['stage_phase'] = 'joint'
                self.observe(self.state['current'], Path(self.state['export']['path']), 'export')
            elif name == 'photo_finish' and arguments.get('confirm_export'):
                advance_workflow(self.state, arguments.get('rationale', 'Actual JPEG reviewed'),
                    observation['id'], self.state['current'])
        from photo_pre_tone import accepted_effect
        accepted_effect(self.state, review, observation)
        self.state.pop('stage_turn_result', None)
        if self.state.get('tool_result', {}).get('kind') == 'rejected_before_execution':
            # A completed action supersedes the previous recoverable refusal.
            # Its archived record remains available, but is not current feedback.
            self.state.pop('tool_result', None)
        self._submission_record(submission, observation['id'], phase)
        self.save()
        return self.snapshot()

    def _validate_finish(self, arguments, observation, phase):
        version = arguments.get('selected_version', self.state['current'])
        require_action(version == observation['version'], 'selected_version_not_observed',
                       'Finish must select the actually observed version', 'action.arguments.selected_version')
        if arguments.get('confirm_export'):
            from photo_phase_scheduler import checkpoint_evidence
            require_action(phase == 'G4', 'actual_export_required', 'Review the actual JPEG first in G4', 'action.arguments.confirm_export')
            checkpoint_evidence(self.state, observation, 'G4')
            require_action(not self.state.get('export', {}).get('confirmation_invalidated_by'),
                           'export_confirmation_invalidated', 'Review a fresh actual JPEG first', 'action.arguments.confirm_export')
        else:
            require_action(phase == 'joint', 'export_phase_required', 'Only joint may create the actual JPEG', 'action.arguments.confirm_export')

    def backend(self):
        if self.native is None:
            from photo_image_dependencies import activate_image_dependencies
            activate_image_dependencies()
            self.native = StageBackend(self)
        return self.native

    def _action_schema(self, name):
        catalog = action_schemas(self.state.get('stage_phase', 'joint'))
        result = copy.deepcopy(catalog.get(name))
        if result is not None:
            review = copy.deepcopy(catalog['photo_edit']['properties']['review'])
            if name != 'photo_edit':
                review['required'] = []
            result['properties']['review'] = review
        return result

    def _validate_public_review(self, review):
        spec = copy.deepcopy(action_schemas(self.state.get('stage_phase', 'joint'))['photo_edit']['properties']['review'])
        spec['required'] = []
        _check(review, spec, 'review')
        seen = set()
        for decision in review.get('module_decisions', []):
            module.canonical_module(decision['module'])
            ident = decision['module']
            # A model may discuss a slider family separately from its module.
            # Preserve all declarations; typed action coverage is authoritative.
            seen.add(ident)

    def normalize_public_review(self, review):
        review = copy.deepcopy(review or {})
        result = {key: review.get(key, '') for key in ('visible_comparison', 'remaining_gaps', 'uncertainty')}
        if any(not isinstance(value, str) for value in result.values()):
            raise ValueError('Public comparison metadata must be strings')
        result['previous_outcome'] = review.get('previous_outcome', 'uncertain')
        for key in ('judgment_items', 'group_reviews', 'wb_assessment', 'wb_local_review', 'wb_pick_review',
                    'tone_assessment', 'tone_basis_impact', 'tone_boundary_review', 'review_anchors'):
            if key in review:
                result[key] = copy.deepcopy(review[key])
        result['review_quality'] = 'explicit' if result['visible_comparison'].strip() else 'missing_visible_comparison'
        return result

    def _record_public_review(self, review, observation):
        if observation['version'] not in self.state['versions']:
            raise ValueError('Unknown observation version')
        record = {'id': 'd' + str(len(self.state.get('public_decisions', [])) + 1).zfill(5),
                  'observation_id': observation['id'], 'version': observation['version'], **copy.deepcopy(review)}
        self.state.setdefault('public_decisions', []).append(record)
        return record

    def prepare_edit_rule_check(self):
        return False

    def _operation_fields(self):
        return (*super()._operation_fields(), 'mask_plan')

    def _operation_modules(self, arguments):
        result = list(super()._operation_modules(arguments))
        if arguments.get('mask_plan') and 'masks' not in result:
            result.append('masks')
        return result

    def _state_only_operation(self, arguments):
        plan = arguments.get('mask_plan', {})
        return (plan.get('action') == 'create' and all(value == 0 for value in plan.get('values', {}).values())) or super()._state_only_operation(arguments)

    def _validate_forwarded_edit(self, arguments):
        from photo_stage_operations import validate_stage_operation
        base = arguments['base_version']
        validate_stage_operation(arguments, self.state['versions'][base]['settings'],
                                 self.state.get('stage_phase', 'joint'), self.backend().dependencies,
                                 controls=self.backend().controls())

    def act(self, name, args, source_observation_id=None):
        _check(args, self._action_schema(name) or {}, name)
        if name == 'photo_edit':
            args = copy.deepcopy(args)
            args['review'] = self._review_for_operation(args['review'], args)
            from photo_stage_operations import validate_stage_operation
            base = args.get('base_version', self.state['current'])
            if base not in self.state['versions']:
                raise ValueError('Unknown edit base')
            validate_stage_operation(args, self.state['versions'][base]['settings'],
                                     self.state.get('stage_phase', 'joint'), self.backend().dependencies,
                                 controls=self.backend().controls())
        if name == 'photo_history':
            self.state['implicit_history_projection'] = False
        if name == 'photo_discover':
            query = {k: args.get(k) for k in ('kind', 'query', 'exact_ids', 'module', 'offset', 'page_size', 'result_id')}
            query['kind'] = query.get('kind') or 'catalog'
            observation = next((o for o in self.state['observations'] if o['id'] == source_observation_id), None) if source_observation_id else self.state['observations'][-1]
            if observation is None:
                raise ValueError('Unavailable source observation')
            prior = next((r for r in reversed(self.state.get('module_rule_archive', []))
                          if r['version'] == observation['version'] and r['request'] == query), None)
            if prior:
                if self.state.get('pending') or self.state['status'] != 'working':
                    raise lab.SafetyStop('Stage trajectory is not eligible for another action')
                self.state.setdefault('unprojected_discovery_ids', []).append(prior['id'])
                key = 'active_rule_result_id' if query['kind'] == 'rule' else 'active_catalog_result_id'
                self.state[key] = prior['id']
                self.state['tool_result'] = {'kind': 'discovery_result', 'result_id': prior['id'],
                    'request': query, 'status': prior['result']['status'], 'reused': True,
                    'result': module.runtime_catalog_projection(prior['result'], exact=bool(query['exact_ids'])) if query['kind'] == 'catalog' else prior['runtime_result']}
                self.save()
                return self.snapshot()
        if name in ('photo_edit', 'photo_finish'):
            # Covers injected/fake backends as well as the production native methods.
            self.execution_started(name)
        return super().act(name, args, source_observation_id)

    def _check_discovery_repeat(self, query, observation):
        return None

    def _catalog_discover(self, catalog, query='', module=None, offset=0, page_size=8):
        # Native catalog validation stays unchanged; aggregate its bounded pages.
        if page_size <= 32:
            return super()._catalog_discover(catalog, query, module, offset, page_size)
        result = super()._catalog_discover(catalog, query, module, offset, 32)
        items = result['items']
        while result['next_offset'] is not None and len(items) < page_size:
            result = super()._catalog_discover(catalog, query, module, result['next_offset'], min(32, page_size - len(items)))
            items.extend(result['items'])
        return {**result, 'items': items, 'offset': offset, 'page_size': page_size}

    def _discovery_policy_context(self, catalog, book_path, **kwargs):
        return module.policy_context(catalog, book_path, **kwargs,
                                     retrieval_capacity={'complete_records': True})

    def _policy_projection(self, packet):
        result = super()._policy_projection(packet)
        budget = self.protocol['resources_snapshot']['projection']['target_text_bytes']
        for match in result.get('matches', []):
            if len(json.dumps(match, ensure_ascii=False).encode('utf-8')) > budget and match.get('card_id'):
                match.pop('approvedlesson', None)
                match['lesson_body'] = 'full_record_available_by_detail_get_not_projected'
                match['detail_get'] = {'mode': 'detail_get', 'record_id': 'card:' + match['card_id'], 'char_offset': 0}
        return result

    def budget(self, version):
        seen = [o for o in self.state['observations'] if o['version'] == version]
        return {'version': version, 'extra_views_left': None, 'prior_view_count': len(seen),
                'per_version_hard_limit': None}

    def observe(self, version=None, path=None, artifact='canonical', args=None, requested=False):
        from photo_stage_observation import derive_stage_views, stage_observation_metadata
        item = self._observe_color(version, path, artifact, args, requested)
        phase = self.state['workflow']['phase']
        original = item.get('color_views', item['views'])
        item['color_views'] = copy.deepcopy(original)
        item['views'] = derive_stage_views(original, phase, self.folder / 'stage-observations', item['id'], grid=phase in ('G1', 'tone', 'G2', 'color', 'G3', 'joint', 'G4'))
        item['phase'] = phase
        item['stage_observation'] = stage_observation_metadata(phase, item['id'], grid=phase in ('G1', 'tone', 'G2', 'color', 'G3', 'joint', 'G4'))
        self.save()
        return item

    def combine_observations(self, ids):
        result = super().combine_observations(ids)
        from photo_stage_observation import stage_observation_metadata
        item = self.state['observations'][-1]
        phase = self.state['workflow']['phase']
        item['phase'] = phase
        item['stage_observation'] = stage_observation_metadata(phase, item['id'], grid=phase in ('G1', 'tone', 'G2', 'color', 'G3', 'joint', 'G4'))
        self.save()
        return self.snapshot()

    def _observe_color(self, version=None, path=None, artifact='canonical', args=None, requested=False):
        spec = args or {}
        version = version or self.state['current']
        mode = spec.get('comparison_mode', 'target')
        roi = spec.get('candidate_roi')
        if mode == 'effect':
            require_action(bool(self.state['versions'].get(version, {}).get('parent')),
                           'effect_parent_required', 'Effect comparison needs an actual parent version', 'action.arguments.version')
            if spec.get('reference_roi') is not None:
                reference = spec['reference_roi']
                require_action(roi is None or reference == roi, 'effect_roi_mismatch',
                               'Parent/current effect views use the same region; supply one matching ROI', 'action.arguments.reference_roi')
                roi = reference if roi is None else roi
                spec = {k: v for k, v in spec.items() if k != 'reference_roi'}
                spec['candidate_roi'] = roi
            identity = {'comparison_mode': 'effect', 'candidate_roi': roi,
                        'side': min(spec.get('side', 384 if roi else 512), 384 if roi else 512)}
        else:
            limit = lab.SIDE_LIMITS['detail' if roi or spec.get('reference_roi') else 'overview']
            identity = {'reference_roi': spec.get('reference_roi'), 'candidate_roi': roi,
                        'side': min(int(spec.get('side', limit)), limit)}
        for key in ('reference_roi', 'candidate_roi'):
            if identity.get(key) is not None:
                lab.pixel_box((10000, 10000), identity[key])
        prior = next((o for o in reversed(self.state['observations']) if o['version'] == version
                      and o.get('artifact') == artifact and o.get('view_spec') == identity
                      and (path is None or o.get('path') == str(path))), None)
        if requested and prior:
            self.validate_views(prior['views'])
            item = copy.deepcopy(prior)
            item.update(id='o' + str(len(self.state['observations']) + 1).zfill(4), requested=True,
                        question=spec.get('question', '重新查看'), decision_judgment=None,
                        reused=True, revisit_of=prior.get('revisit_of', prior['id']))
            self.state['observations'].append(item)
            self.save()
            return item
        if mode == 'effect':
            return super().observe(version, path, artifact, args=spec, requested=requested)
        # Bypass only PhotoSession's legacy repeat/cap policy, not its image contract.
        item = lab.Trajectory.observe(self, version, path, artifact, spec, False)
        item['requested'] = requested
        folder = self.folder / 'observations' / item['id']
        index = 2 if item['parent'] else 1
        item['views'] = [(str(folder / 'view-0.png'), '作者成片'),
                         (str(folder / ('view-' + str(index) + '.png')),
                          ('实际 JPEG ' if artifact == 'export' else '观察版本 ') + version)]
        item['comparison_mode'] = 'target'
        self.validate_views(item['views'])
        self.save()
        return item

    def evidence_state(self, observation):
        result = super().evidence_state(observation)
        result.update(repeat_will_pause=False, repeat_guard='Exact repeated views reuse cached pixels; no new evidence or terminal pause.')
        return result

    def _history_records(self):
        rows, seen = [], set()
        for key, kind in (('workflow_submissions', 'workflow_turn'), ('stage_rejections', 'stage_rejection'),
                          ('branch_reset_events', 'branch_reset'), ('review_anchor_records', 'review_anchors'),
                          ('wb_mask_usage', 'wb_mask_usage'), ('wb_assessments', 'wb_assessments'),
                          ('tone_assessments', 'tone_assessments'), ('tone_boundary_reviews', 'tone_boundary_reviews'),
                          ('deferred_debt_reviews', 'deferred_debt_reviews'),
                          ('effect_expectations', 'prediction'), ('effect_reviews', 'effect_review'),
                          ('module_rule_archive', 'discovery'), ('module_public_records', 'module_decision'),
                          ('public_decisions', 'decision'), ('decision_memory', 'memory'), ('history', 'action')):
            for index, item in enumerate(self.state.get(key, [])):
                ident = item.get('id') or key + ':' + str(index) if key in (
                    'wb_mask_usage', 'wb_assessments', 'tone_assessments', 'tone_boundary_reviews') else item.get('id') or kind + '-' + str(index)
                if ident in seen:
                    ident = kind + ':' + ident + ':' + str(index)
                seen.add(ident)
                rows.append({'id': ident, 'kind': kind, 'record': copy.deepcopy(item)})
        for entry in self.state.get('module_rule_archive', []):
            for packet in (entry.get('result') or {}, entry.get('associated_rule_selection') or {}):
                for match in packet.get('matches', []):
                    card = match.get('textbook_card')
                    ident = 'card:' + card['id'] if card else None
                    if ident and ident not in seen:
                        seen.add(ident)
                        rows.append({'id': ident, 'kind': 'textbook_card', 'record': copy.deepcopy(card)})
        for debt in self.state.get('workflow', {}).get('review_debts', []):
            rows.append({'id': debt['id'], 'kind': 'review_debt', 'record': copy.deepcopy(debt)})
        for event in self.state.get('workflow', {}).get('events', []):
            rows.append({'id': event['id'], 'kind': 'workflow_event', 'record': copy.deepcopy(event)})
        audit_path = self.folder / 'audit/events.jsonl'
        if audit_path.exists():
            for line in audit_path.read_text(encoding='utf-8').splitlines():
                event = json.loads(line)
                rows.append({'id': event['id'], 'kind': 'lifecycle', 'record': event})
        from photo_proposal_bank import bank_history
        for item in bank_history(self.state):
            rows.append({'id': item['record_id'], 'kind': 'proposal_bank', 'record': item})
        bank = self.state.get('proposal_bank', {})
        for event in bank.get('events', []):
            rows.append({'id': event['id'], 'kind': 'proposal_bank_event', 'record': copy.deepcopy(event)})
        for issue in bank.get('issues', {}).values():
            for group in issue.get('task_groups', {}).values():
                for item in group.get('records', []):
                    rows.append({'id': item['record_id'], 'kind': 'proposal_group', 'record': copy.deepcopy(item)})
                for item in group.get('waiver_records', []):
                    rows.append({'id': item['record_id'], 'kind': 'dependency_waiver', 'record': copy.deepcopy(item)})
        return rows

    def _history_result(self, arguments, observation):
        rows = self._history_records()
        if arguments.get('mode') == 'detail_get':
            record_id = arguments.get('record_id')
            row = next((r for r in rows if r['id'] == record_id), None)
            if row is None:
                raise ValueError('Unknown stage history record ID')
            text = json.dumps(row['record'], ensure_ascii=False, separators=(',', ':'))
            start = arguments.get('char_offset', 0)
            if start > len(text):
                raise ValueError('History character cursor exceeds record')
            count = arguments.get('char_limit', self.protocol['resources_snapshot']['projection']['history_page_bytes'] // 4)
            end = min(len(text), start + count)
            return {'kind': 'history', 'mode': 'detail_get', 'record_id': record_id,
                    'text': text[start:end], 'char_offset': start, 'total_chars': len(text),
                    'next_char_offset': end if end < len(text) else None}
        ids, query, version = arguments.get('ids', []), arguments.get('query', ''), arguments.get('version')
        if version is not None and version not in self.state['versions']:
            raise ValueError('Unknown history version')
        rows = [r for r in rows if (not ids or r['id'] in ids)
                and (not query or query.casefold() in json.dumps(r, ensure_ascii=False).casefold())
                and (version is None or version in (r['record'].get('version'), r['record'].get('source_version'),
                                                   r['record'].get('child_version'), r['record'].get('base_version')))]
        offset, count = arguments.get('offset', 0), arguments.get('page_size', 5)
        selected = rows[offset:offset + count]
        budget = self.protocol['resources_snapshot']['projection']['history_page_bytes']
        page, used = [], 0
        for row in selected:
            size = len(json.dumps(row, ensure_ascii=False).encode('utf-8'))
            if size > budget:
                row = {'id': row['id'], 'kind': row['kind'], 'detail_required': True,
                       'detail_get': {'mode': 'detail_get', 'record_id': row['id'], 'char_offset': 0}}
                size = len(json.dumps(row).encode('utf-8'))
            if page and used + size > budget:
                break
            page.append(row)
            used += size
        next_offset = offset + len(page)
        return {'kind': 'history', 'mode': 'index', 'records': page, 'total': len(rows),
                'next_offset': next_offset if next_offset < len(rows) else None}

    def _dispatch_photo_action(self, name, arguments, source_observation_id):
        observation = next((o for o in self.state['observations'] if o['id'] == source_observation_id), None) if source_observation_id else self.state['observations'][-1]
        if observation is None:
            raise ValueError('Unavailable source observation')
        review = self.normalize_public_review(arguments.get('review'))
        self._record_public_review(review, observation)
        self.record_decision(observation, {'action': name[6:], 'arguments': {'review': review}})
        if name == 'photo_observe':
            version = arguments.get('version', self.state['current'])
            export = self.state.get('export')
            if self.state['workflow']['phase'] == 'G4':
                require_action(bool(export) and export.get('version') == version
                               and not export.get('confirmation_invalidated_by'),
                               'actual_export_required', 'G4 details must come from the actual selected JPEG',
                               'action.arguments.version')
                viewed = self.observe(version, Path(export['path']), 'export', args=arguments, requested=True)
            else:
                viewed = self.observe(version, args=arguments, requested=True)
            self.state['history'].append({'action': 'observe', 'version': viewed['version'],
                                         'parent': viewed.get('parent'), 'observation_id': viewed['id']})
            if viewed.get('reused'):
                self.state['tool_result'] = {'kind': 'observation_reused', 'note': 'Cached pixels reused; no new visual evidence.'}
            else:
                self.state.pop('tool_result', None)
        elif name == 'photo_unable':
            self.state.update(status='diagnosed_unfinished', diagnosis=arguments)
            self.state['history'].append({'action': 'unable', 'version': observation['version']})
        elif name == 'photo_finish':
            version = arguments.get('selected_version', self.state['current'])
            if version not in self.state['reviewed']:
                raise ValueError('Select a reviewed version')
            export = self.state.get('export')
            if arguments.get('confirm_export'):
                if not export or export.get('confirmation_invalidated_by') or export['version'] != version or not any(o['artifact'] == 'export' and
                        o['path'] == export['path'] and lab.reviewed(o) for o in self.state['observations']):
                    raise ValueError('Review the actual JPEG first')
                self.state.update(status='complete', selected_version=version,
                    model_goal_claim=arguments.get('goal_status', 'unresolved'),
                    model_remaining_gaps=arguments.get('remaining_gaps', '未说明'),
                    model_stop_basis=arguments.get('stop_basis', '未说明'), author_accepted=None)
            elif export and export['version'] == version and not export.get('confirmation_invalidated_by'):
                facts = lab.require_srgb_tag(export['path'])
                if facts['dimensions'] != self.input['original_dimensions']:
                    raise ValueError('Existing JPEG dimensions mismatch')
                self.state['current'] = version
                latest = self.state['observations'][-1]
                if latest['artifact'] != 'export' or latest['path'] != export['path']:
                    self.observe(version, Path(export['path']), 'export')
                self.state['tool_result'] = {'kind': 'export_reused', 'note': 'Actual JPEG already exists; explicitly review and confirm.'}
            else:
                dest = self.folder / ('export_' + uuid.uuid4().hex[:10] + '.jpg')
                self.state['pending'] = {'operation_id': 'op_' + uuid.uuid4().hex, 'kind': 'export', 'base': version, 'path': str(dest)}
                self.save()
                self.backend().restore(self.state['versions'][version]['settings'])
                dims = self.backend().render(dest, 0, 'jpeg')
                if dims != self.input['original_dimensions']:
                    raise ValueError('JPEG dimensions mismatch')
                self.state.update(current=version, export={'version': version, 'path': str(dest), 'dimensions': dims})
                self.state.pop('tool_result', None)
                self.observe(version, dest, 'export')
                self.state.pop('pending')
            self.state['history'].append({'action': 'finish', 'version': version, 'confirmed': bool(arguments.get('confirm_export'))})
        else:
            raise ValueError('Unknown stage photo action')
        self.save()
        return self.snapshot()

    def snapshot(self):
        from photo_proposal_bank import bank_projection
        result = super().snapshot()
        phase = self.state['workflow']['phase']
        guides = {
            'G1': 'Full-color diagnosis: record review.tone_assessment with structural/chroma/uncertain regional attribution and qualified neutral black/white/midtone anchors, or explicitly no relevant differences/anchors. Chroma is a revisable hypothesis, not proof of author HSL edits. Explicit bank_delta routes justified color luminance to Color; host never invents controls. Protect these regions from purposefully replacing color work with grayscale exposure/curve compensation. Default advance enters Tone. Only for a severe cast obstructing the tone basis, optional once-only pre_tone_wb submits explicit scalar WB, current wb_assessment needed, tone_basis_impact and selected WB fine group; after success review actual color effect then fresh full G1 target before Tone. G1 never exposes general photo_edit.',
            'tone': 'Only grayscale is supplied. Use carried G1 color attribution as historical protection, never claim newly seen colors. Default neutral-first: usable black/white support, then low-saturation midtone contrast/simple Master curve, then broad physical lighting residuals with simple masks. Order is soft, no required knob walk or forced 0/1 endpoints. Do not use exposure/highlights to imitate a specifically deferred chroma-lightness task; actual global tone may still affect it with an explicit protected tradeoff. Master curve reset or 2-5 monotone-y points, typically three main anchors plus optional edge supports; no complex waves. After local effect, explicitly assess protected core/feather/outside in tone_boundary_review; same-issue Joint handoff handles justified fine-boundary residuals, not contour-chasing. Keep/revise/select one actual current fine group, review and act in the same turn, or advance the actually observed selected version to G2. Eighteen Tone requests are guidance, not a cap.',
            'G2': 'Full color after tone. Record review.wb_assessment on this actual target/current version: identify 1-2 independent author/current midtone low-saturation reference cells with material/lighting qualification, or explicitly not_available. Distinguish physical neutrality from intended environment color; route justified shadow/highlight atmosphere to grading and spatial light differences to local WB, not blanket global warming. No reliable reference and no WB task are valid explicit outcomes. Reassess only relevant color/joint issues; no whole-bank checklist. The assessment, bank update and advance may occur in this same turn.',
            'color': 'Use work_packet.white_balance actual WB readback, reference basis and existing masks. Prefer attributable small absolute WB edits, one axis first; 2-3 rounds and step sizes are guidance, never gates. Optional develop.wbPick requires physical-neutral source grounding in review.wb_pick_review, full-oriented-source coordinates and actual enabled backend support; it samples a source thumbnail, not the delivered rendered target. Local WB needs review.wb_local_review: reuse the actual ID for the same suitable region, or explain why existing masks do not cover this distinct purpose. Color only patches local temp/tint, preserving prior tone. Effect supported means response matched a prediction, not author matching; resolve WB only with target evidence and reopen by explicit revise when new evidence contradicts it.',
            'G3': 'Full color integrated diagnosis. Review pending G3 WB tone-coupling debt on actual author/current regions: highlight detail, shadow support and skin where present. Explicitly resolve or accept a justified tradeoff; unreviewed debt remains pending, not a checklist gate. New global yellow/green evidence may revise/reopen an accepted WB baseline. Add only justified small Joint exposure/highlights or WB residuals; keep adequate areas protected.',
            'joint': 'Use small justified residual groups only, always selected_issue_id and a fresh/reassessed bank item for an edit. After reviewing the selected version, photo_finish confirm_export:false produces actual JPEG and opens G4; do not use advance to skip export.',
            'G4': 'The supplied current image comes from actual JPEG. photo_finish confirm_export:true confirms this reviewed export; partial/unresolved is valid. If a correction is justified, advance target_phase:joint with reason; old export confirmation is invalidated and a new actual JPEG must be observed.',
            'complete': 'Task terminal; no more actions.'}
        result['context']['phase_guidance'] = guides[phase]
        pending = result['context'].get('pending_effect_review')
        if pending:
            original = next(row for row in self.state['effect_expectations'] if row['id'] == pending['id'])
            pending['effect_expectation'] = copy.deepcopy(original['effect_expectation'])
            pending['review_focus'] = copy.deepcopy(original['effect_expectation'])
            boundary = next((debt.get('tone_boundary') for debt in self.state['workflow']['review_debts']
                if debt.get('operation_id') == original['operation_id'] and debt.get('tone_boundary')), None)
            if boundary:
                pending['tone_boundary'] = copy.deepcopy(boundary)
                pending['boundary_review_instruction'] = 'Include review.tone_boundary_review {operation_id,outcome:protected|affected|uncertain,evidence,joint_handoff_reason?,joint_group_id?}. Inspect protected core, feather neighborhood and outside; report visible effects, never numeric gray samples. If affected, explain same-issue Joint handoff or immediate small repair; do not chase pixel contours with simple gradients.'
            linked = next((event for event in self.state['proposal_bank']['events']
                           if event.get('kind') == 'operation' and event.get('operation_id') == original['operation_id']), None)
            if linked:
                pending.update(issue_id=linked['issue_id'], group_id=linked['group_id'])
        result['context']['proposal_warnings'] = copy.deepcopy(self.state.get('proposal_reference_warnings', []))
        result['context']['normalization_warnings'] = copy.deepcopy(self.state.get('normalization_warnings', []))
        if self.state.get('module_coverage_records'):
            result['context']['module_coverage'] = copy.deepcopy(self.state['module_coverage_records'][-1])
        if self.state.get('stage_turn_result'):
            result['stage_turn_result'] = copy.deepcopy(self.state['stage_turn_result'])
            result['context']['stage_turn_result'] = copy.deepcopy(self.state['stage_turn_result'])
            result['context']['tool_result'] = copy.deepcopy(self.state['stage_turn_result'])
        result['context']['public_contract_help'] = {
            'bank_operations': 'add needs id/branch/diagnosis. All-phase proposals are stored and classified into stable task_groups; unknown paths remain unresolved. group_reviews keep/revise/retire/defer/resolved/needs_reassessment/waiting_dependency are proposal dispositions, not effect judgments: they need no operation_id and never change actual operation outcomes. Extra operation_id stays raw metadata with a not_adopted warning in proposal_warnings on the next projection. waiting_dependency applies priority/dependencies; unique same-issue short group IDs resolve locally, unknown/ambiguous dependencies wait without blocking unrelated groups. Proposed controls never execute themselves; select the issue and group explicitly when needed.',
            'edit_review': 'Every photo_edit needs review.module_decisions and review.effect_expectation{target,expected_change,protected_region,risk,falsifier}, action arguments rationale/predicted_effect, and root selected_issue_id. Module declarations remain model metadata; execution coverage is derived from the explicit typed operation, with a warning on disagreement: light; wb; calibration=basis; color/mixer=mixer_point; curve=curves; grading; effects; mask_plan/mask.*=masks.',
            'views': 'photo_observe only obtains pixels; question is view purpose, not a request to another analyst. target=author/current; effect=actual parent/current. Gray tone never silently becomes a color comparison.',
            'memory_reuse': 'Use work_packet current/pending_review before ready candidates; waiting and later-phase items stay stored. effect_review must bind the actual expectation, operation and observed child/group, unlike proposal disposition. Review that full prior expectation, keep/revise the group on the actual version and select the next small edit in this same turn; no additional confirmation turn is required. selection_reason/override_reason may justify skipping or revisiting default steps; no required knob count or per-group round cap. Old absolute values are never automatically reapplied.'}

        workflow = copy.deepcopy(self.state['workflow'])
        workflow['contract_revision'] = self.contract_revision()
        # Full workflow submissions and bank revisions are accessible by history.
        workflow['events'] = [{key: copy.deepcopy(value) for key, value in event.items()
            if key not in ('debt', 'updates', 'wb_assessment', 'tone_assessment')}
            for event in workflow['events'][-3:]]
        for debt in workflow['review_debts']:
            debt['history_record_id'] = debt['id']
            if debt.get('status') != 'pending':
                for key in ('focus', 'checks', 'review_update', 'wb_coupling', 'wb_assessment_ref',
                            'local_wb_review', 'tone_boundary', 'mask_geometry_changes', 'hsl_luminance_changes'):
                    debt.pop(key, None)
            elif debt.get('focus'):
                debt['original_expectation'] = copy.deepcopy(debt['focus'][0].get('expectation', {}))
                debt['focus'] = [{key: copy.deepcopy(value) for key, value in focus.items()
                    if key not in ('context', 'expectation')} for focus in debt['focus']]
        result['context'].update(stage_runtime={'schema': SCHEMA, 'build': BUILD,
            'resource_profile_id': RESOURCE_PROFILE, 'phase': workflow['phase']},
            workflow=workflow, contract_revision=self.contract_revision(),
            proposal_bank=bank_projection(self.state, workflow['phase']),
            stage_observation=copy.deepcopy(self.state['observations'][-1].get('stage_observation')))
        from photo_white_balance import work_context
        observation = self.state['observations'][-1]
        observed_settings = self.state['versions'][observation['version']]['settings']
        packet = copy.deepcopy(result['context']['proposal_bank'].get('work_packet', {}))
        if phase in ('G1', 'G2', 'color', 'G3', 'joint', 'G4'):
            packet = {'white_balance': work_context(self.state, phase, observed_settings, observation), **packet}
        from photo_tone_guidance import work_context as tone_work_context
        packet = {'tone': tone_work_context(self.state, phase, observation), **packet}
        packet['pre_tone_wb'] = {'available': phase == 'G1' and self.state['workflow'].get('pre_tone_wb') is None,
            'lifecycle': copy.deepcopy(self.state['workflow'].get('pre_tone_wb')),
            'rule': 'Optional once-only scalar global WB for a severe cast obstructing the tone basis; not a default prerequisite. After success review color effect, then fresh full G1 target before Tone. Unknown never retries.'}
        from photo_working_context import selected_baseline, active_masks_summary
        baseline = selected_baseline(self.state, observation)
        packet = {'branch_reset_notice': baseline.get('branch_reset_notice'),
                  'selected_baseline': baseline,
                  'active_masks_summary': active_masks_summary(self.state, observation, self.input.get('original_dimensions')),
                  **packet}
        result['context']['proposal_bank']['work_packet'] = {'$ref': 'context.work_packet',
            'compatibility': 'The authoritative packet body appears once at context.work_packet; full bank history remains retrievable.'}
        result['context']['work_packet'] = packet
        result['context']['geometry_warnings'] = copy.deepcopy(self.state.get('geometry_warnings', []))
        tool_result = result['context'].get('tool_result')
        if self.state.get('implicit_history_projection') and isinstance(tool_result, dict) and tool_result.get('kind') == 'history':
            result['context']['tool_result'] = {'kind': 'history_index', 'implicit_after_advance': True,
                'total': tool_result.get('total'), 'record_refs': [{'id': row['id'], 'kind': row.get('kind')}
                    for row in tool_result.get('records', [])],
                'instruction': 'Full records were not deleted. Explicit photo_history/detail_get retrieves original content.'}
        result['context']['public_contract_help']['memory'] = (
            'Top-level context.work_packet is the single body; proposal_bank.work_packet is its explicit reference. '
            'selected_baseline identifies actual root-to-selected operations; BRANCH RESET NOTICE marks inactive historical branches, not failed or deleted evidence. Observed old version is not selected. '
            'group_reviews skip is a reasoned proposal disposition, never an effect. Legacy dependencies are soft; typed {group_id|issue_id,kind:soft|hard,requirement:disposition|effect,operation_id?} require actual branch-applicable evidence for hard effects. '
            'active_masks_summary shows all actual IDs including hidden masks. Same-kind center distance below 0.1 is a non-blocking geometry hint, not semantic equivalence or a request for an extra confirmation turn. '
            'Optional review.review_anchors [{id,purpose:target|protected,extent:explicit_cells|full_frame|unknown,view_role:author|current|effect_parent|effect_current,cells?,region,qualification}] uses actual view grid/ROI; never guess cells from old text or mask geometry. '
            'New review_debt_updates use {id,resolution:no_regression|minor_acceptable|spawn_joint_task,reason,checks:[{focus_id,outcome,evidence,review_region,review_cells?}],joint_diagnosis?,existing_joint_issue_id?}. '
            'Each due focus needs current-color-target evidence; unavailable evidence remains pending without blocking progress. spawn_joint_task creates a diagnosis with groups:[], not controls or native action; reviewed_joint_task means reviewed-and-routed, not photographically fixed.')
        result['context']['public_contract_help']['tone'] = (
            'G1 tone_assessment {items:[{id,author_region,current_region,material,lighting,attribution:structure|chroma|uncertain,evidence,protection,handoff:tone|color|joint,issue_id?,group_id?}],neutral_anchors:[{role:black_support|white_support|midtone,author_region,current_region,qualification}],reason}. '
            'Empty items/anchors and uncertain are valid. Color-purpose hypotheses remain revisable; explicit bank_delta creates any actual task. '
            'pre_tone_wb requires scalar values only, selected issue/group, normal edit expectations, wb_assessment needed and tone_basis_impact; no new top-level tool or general G1 edit. '
            'tone_boundary_review {operation_id,outcome:protected|affected|uncertain,evidence,joint_handoff_reason?,joint_group_id?} reviews actual effect only; HSL-L and WB consequences share the original G3 debt.')
        result['context']['public_contract_help']['white_balance'] = (
            'wb_assessment is target/current visual evidence, never numeric pixel sampling. G2 can explicitly report no reference or no task. '
            'wb_pick_review requires neutrality_basis/source_region/coordinate_space:full_oriented_source/full_image_point{x,y}/roi_mapping_reason; '
            'the point must match the atomic command, not author or crop coordinates. wb_local_review explains reuse/create against actual IDs. '
            'All current settings are actual controls; steps are guidance, resolved is revisable, effect response is not target correctness.')
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--round', required=True)
    parser.add_argument('--photo', required=True)
    parser.add_argument('--variant', choices=['baseline', 'module_support'], required=True)
    args = parser.parse_args()
    root = Path(args.round).resolve()
    protocol = lab.read(root / 'protocol.json')
    jobs = [j for j in protocol['jobs'] if j['photo_id'] == args.photo and j['condition'] == args.variant]
    if len(jobs) != 1:
        raise ValueError('Exactly one independently frozen stage job required')
    folder = root / 'editing' / args.photo / ('r' + str(jobs[0]['repeat'])) / args.variant
    with lab.trajectory_lease(folder):
        session = StageSession(root, jobs[0])
        try:
            for line in sys.stdin:
                try:
                    request = json.loads(line)
                    op = request['op']
                    if op == 'snapshot': value = session.snapshot()
                    elif op == 'project_snapshot': value = session.project_snapshot()
                    elif op == 'schemas': value = session.tool_schemas()
                    elif op == 'stage_turn': value = session.stage_turn(request['arguments'], request.get('source_observation_id'))
                    elif op == 'action': raise ValueError('Use the unified version-bound stage_turn protocol')
                    elif op == 'combine_observations': value = session.combine_observations(request['ids'])
                    elif op == 'budget_state': value = session.budget_state(request['action'], request.get('note', ''))
                    else: raise ValueError('Unknown stage worker operation')
                    print(json.dumps({'ok': True, 'value': value}, ensure_ascii=False), flush=True)
                except Exception as exc:
                    session.state.update(status='engineering_diagnosis_required', interruption=str(exc))
                    session.save()
                    print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), flush=True)
                    break
        finally:
            if session.native:
                session.native.close()


if __name__ == '__main__':
    main()
