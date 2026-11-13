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

BUILD = 'dsh-stage-runtime-v13-20261113'
SCHEMA = 'dsh-stage-runtime/v2'
RESOURCE_PROFILE = 'stage-resource/v13'
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
    result['photo_edit']['properties']['review']['properties']['judgment_items'] = {
        'type': 'array', 'items': module.obj({'id': {'type': 'string', 'minLength': 1},
        'region': {'type': 'string', 'minLength': 1},
        'kind': {'type': 'string', 'enum': ['tone', 'color', 'joint', 'protection', 'uncertain']},
        'judgment': {'type': 'string', 'minLength': 1}, 'evidence': {'type': 'string'},
        'uncertainty': {'type': 'string'}}, ('id', 'region', 'kind', 'judgment'))}
    text = {'type': 'string', 'minLength': 1}
    group_review = module.obj({'issue_id': text, 'group_id': text, 'reason': text,
        'outcome': {'enum': ['resolved', 'needs_reassessment', 'waiting_dependency', 'keep', 'revise', 'retire', 'defer']},
        'operation_id': text, 'proposed_controls': {'oneOf': [{'type': 'object'}, {'type': 'array'}]},
        'priority': {'type': 'number'}, 'dependencies': {}}, ('issue_id', 'group_id', 'reason', 'outcome'))
    result['photo_edit']['properties']['review']['properties']['group_reviews'] = {'type': 'array', 'items': group_review}
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
                           'awaiting_effect', 'resolved', 'retired', 'off_selected_branch', 'unresolved']},
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
    result = module.obj({
        'contract_revision': {'type': 'string', 'const': revision},
        'review': review, 'bank_delta': bank_delta_schema(),
        'selected_issue_id': {'type': 'string'},
        'selected_group_id': {'type': 'string', 'minLength': 1},
        'selection_reason': {'type': 'string', 'minLength': 1},
        'override_reason': {'type': 'string', 'minLength': 1},
        'review_debt_updates': {'type': 'array', 'items': module.obj({'id': {'type': 'string'},
            'status': {'type': 'string', 'enum': ['resolved', 'accepted_tradeoff']},
            'reason': {'type': 'string', 'minLength': 1}}, ('id', 'status', 'reason'))},
        'action': {'oneOf': branches}},
        ('contract_revision', 'review', 'action'))
    if phase in ('tone', 'color', 'joint'):
        edit_review = copy.deepcopy(review)
        edit_review['required'] = ['visible_comparison', 'module_decisions', 'effect_expectation']
        nonedit = {'oneOf': [branch for branch in branches if branch['properties']['name']['const'] != 'photo_edit']}
        edit = next(branch for branch in branches if branch['properties']['name']['const'] == 'photo_edit')
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
                    ('schema', 'source_round', 'selected_version', 'native_operations_replayed')}
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
        if prior and not reviewed and name in ('photo_edit', 'photo_finish', 'advance'):
            require_action(bool(effect), 'effect_review_required', 'Review the actual prior effect before accepting another action', 'review.effect_review')
        if effect:
            expectation = next((x for x in self.state.get('effect_expectations', []) if x['id'] == effect['expectation_id']), None)
            require_action(expectation and expectation.get('child_version') == observation['version'],
                'effect_review_version', 'Effect review refers to another or unknown version', 'review.effect_review.expectation_id')
            require_action(observation.get('comparison_mode') == 'effect' or effect['outcome'] not in ('supported', 'contradicted'),
                'effect_view_required', 'A definitive effect comparison requires actually delivered parent/current views', 'review.effect_review.outcome')
        if name == 'photo_edit':
            base = arguments.get('base_version', self.state['current'])
            require_action(base in self.state['versions'] and base == observation['version'], 'edit_base_not_delivered',
                           'Effect expectation must use the actually delivered base version', 'action.arguments.base_version')
            actual = self._operation_modules(arguments)
            require_action(bool(actual), 'operation_modules_required',
                           'The explicit operation must identify known native modules', 'action.arguments')

    def _stage_turn(self, submission, source_observation_id=None):
        from photo_phase_scheduler import PHASES, advance_workflow, resolve_review_debts, revisit_workflow
        from photo_proposal_bank import (apply_bank_delta, resolve_selected_group, record_operation,
                                         record_group_review, invalidate_bank)
        if self.state.get('pending') is not None or self.state['status'] != 'working':
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
        if name == 'photo_edit':
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
        self.state = accepted
        self._record_public_review(current['decision_judgment'], current)
        self.save()
        self._accepted_turn_state = copy.deepcopy(self.state)
        permitted = {b['properties']['name']['const'] for b in self.tool_schemas()['photo_stage_turn']['properties']['action']['oneOf']}
        require_action(name in permitted, 'action_outside_phase', 'Current action is outside the authoritative phase', 'action.name')
        if name == 'photo_edit':
            from photo_stage_schema import stage_edit_schema
            # The same fields already passed the broad native contract. Only a
            # known phase narrowing can fail this pure schema check.
            for field, spec in stage_edit_schema(phase).items():
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
        record_group_review(draft, review, observation['id'])
        apply_bank_delta(draft, delta, observation['id'])
        if submission.get('review_debt_updates'):
            resolve_review_debts(draft, submission['review_debt_updates'], observation['id'])
        selected_group = None
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
            self.state['stage_phase'] = self.state['workflow']['phase'] if self.state['workflow']['phase'] in ('tone', 'color', 'joint') else 'joint'
            self.save()
            self.observe(self.state['current'], args={'comparison_mode': 'target'}, requested=False)
        else:
            if name == 'photo_edit':
                base = arguments.get('base_version', self.state['current'])
                require_action(bool(submission.get('selected_issue_id')), 'issue_selection_required',
                               'Select an explicit bank issue for the current edit group', 'selected_issue_id')
                selected_group = resolve_selected_group(draft, submission['selected_issue_id'],
                    submission.get('selected_group_id'), base, action=action_context)
                from photo_stage_operations import validate_stage_operation
                validate_stage_operation(arguments, draft['versions'][base]['settings'],
                    self.state['stage_phase'], self.backend().dependencies, controls=self.backend().controls())
            elif name == 'photo_finish':
                self._validate_finish(arguments, observation, phase)
            self.state = draft
            arguments['review'] = copy.deepcopy(review)
            before = self.state['current']
            self.act(name, arguments, observation['id'])
            if name == 'photo_edit':
                child = self.state['current']
                record_operation(self.state, submission['selected_issue_id'],
                    self.state['versions'][child]['operation_id'], arguments.get('base_version', before), child,
                    group_id=selected_group['id'], action=action_context)
                invalidate_bank(self.state, child)
                if phase in ('tone', 'color'):
                    from photo_phase_scheduler import add_review_debt
                    add_review_debt(self.state, {'id': 'debt:' + self.state['versions'][child]['operation_id'],
                        'due_phase': 'G2' if phase == 'tone' else 'G3', 'version': child,
                        'reason': 'Review color consequences of gray tone work' if phase == 'tone' else 'Reassess accepted tone after color work'})
            if name == 'photo_finish' and self.state.get('export') and not arguments.get('confirm_export') and phase == 'joint':
                advance_workflow(self.state, arguments.get('rationale', 'Inspect actual exported JPEG'),
                    observation['id'], self.state['current'])
                self.state['stage_phase'] = 'joint'
                self.observe(self.state['current'], Path(self.state['export']['path']), 'export')
            elif name == 'photo_finish' and arguments.get('confirm_export'):
                advance_workflow(self.state, arguments.get('rationale', 'Actual JPEG reviewed'),
                    observation['id'], self.state['current'])
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
            if ident in seen:
                raise ValueError('Duplicate module decision')
            seen.add(ident)

    def normalize_public_review(self, review):
        review = copy.deepcopy(review or {})
        result = {key: review.get(key, '') for key in ('visible_comparison', 'remaining_gaps', 'uncertainty')}
        if any(not isinstance(value, str) for value in result.values()):
            raise ValueError('Public comparison metadata must be strings')
        result['previous_outcome'] = review.get('previous_outcome', 'uncertain')
        for key in ('judgment_items', 'group_reviews'):
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
        item['views'] = derive_stage_views(original, phase, self.folder / 'stage-observations', item['id'], grid=phase == 'tone')
        item['phase'] = phase
        item['stage_observation'] = stage_observation_metadata(phase, item['id'], grid=phase == 'tone')
        self.save()
        return item

    def combine_observations(self, ids):
        result = super().combine_observations(ids)
        from photo_stage_observation import stage_observation_metadata
        item = self.state['observations'][-1]
        phase = self.state['workflow']['phase']
        item['phase'] = phase
        item['stage_observation'] = stage_observation_metadata(phase, item['id'], grid=False)
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
                          ('effect_expectations', 'prediction'), ('effect_reviews', 'effect_review'),
                          ('module_rule_archive', 'discovery'), ('module_public_records', 'module_decision'),
                          ('public_decisions', 'decision'), ('decision_memory', 'memory'), ('history', 'action')):
            for index, item in enumerate(self.state.get(key, [])):
                ident = item.get('id') or kind + '-' + str(index)
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
        audit_path = self.folder / 'audit/events.jsonl'
        if audit_path.exists():
            for line in audit_path.read_text(encoding='utf-8').splitlines():
                event = json.loads(line)
                rows.append({'id': event['id'], 'kind': 'lifecycle', 'record': event})
        from photo_proposal_bank import bank_history
        for item in bank_history(self.state):
            rows.append({'id': item['record_id'], 'kind': 'proposal_bank', 'record': item})
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
            'G1': 'First full-color diagnosis: submit public judgment_items for tonal/color differences and protection. Add relevant tone/color issues to bank_delta using op:add,id,branch,diagnosis,priority,proposed_controls. Then action advance with reason enters gray tone. If no adjustments are justified, explicitly explain that comparison rather than inventing issues.',
            'tone': 'Only grayscale is supplied. Prioritize one tonal issue, explicitly keep/revise its bank item against the current observed version, select its ID and submit a small legal tonal group. After real effect review, continue, inspect target/ROI, or advance with reason and the actually observed selected version to G2.',
            'G2': 'Full color after tone. Reassess the relevant color/joint issues against this actual version, preserve deferred or uncertain items and choose the next useful color work. Reassessment and review debt are incremental records, not an all-items-at-once administrative gate. Existing bank/group updates carry the decisions; do not duplicate whole checklists. Advance enters color; each actual edit still selects a fresh current group.',
            'color': 'Choose one current color issue. Keep/revise bank against the actual current observation, selected_issue_id links the edit. Global color tools and local mask temp/tint only; preserve accepted tone. Review actual parent/current effect before further edit or advance to G3.',
            'G3': 'Full color integrated diagnosis. Explicitly resolve pending due G3 debts, update/add joint issues and priorities, then advance enters joint. Keep already adequate areas protected.',
            'joint': 'Use small justified residual groups only, always selected_issue_id and a fresh/reassessed bank item for an edit. After reviewing the selected version, photo_finish confirm_export:false produces actual JPEG and opens G4; do not use advance to skip export.',
            'G4': 'The supplied current image comes from actual JPEG. photo_finish confirm_export:true confirms this reviewed export; partial/unresolved is valid. If a correction is justified, advance target_phase:joint with reason; old export confirmation is invalidated and a new actual JPEG must be observed.',
            'complete': 'Task terminal; no more actions.'}
        result['context']['phase_guidance'] = guides[phase]
        pending = result['context'].get('pending_effect_review')
        if pending:
            original = next(row for row in self.state['effect_expectations'] if row['id'] == pending['id'])
            pending['effect_expectation'] = copy.deepcopy(original['effect_expectation'])
            pending['review_focus'] = copy.deepcopy(original['effect_expectation'])
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
        workflow['events'] = workflow['events'][-3:]
        result['context'].update(stage_runtime={'schema': SCHEMA, 'build': BUILD,
            'resource_profile_id': RESOURCE_PROFILE, 'phase': workflow['phase']},
            workflow=workflow, contract_revision=self.contract_revision(),
            proposal_bank=bank_projection(self.state, workflow['phase']),
            stage_observation=copy.deepcopy(self.state['observations'][-1].get('stage_observation')))
        result['context']['work_packet'] = copy.deepcopy(result['context']['proposal_bank'].get('work_packet', {}))
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
