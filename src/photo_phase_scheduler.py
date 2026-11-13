"""In-memory stage bookkeeping, not a renderer or a photographic progress judge.

Call init_workflow once on a NEW state. validate_transition is read-only;
advance_workflow validates again and commits after the worker's selected action
succeeds. The worker owns persistence/rollback of its entire compound turn.
There is no legacy workflow migration, timer, action quota or hidden stage flag.
"""
import copy
import json

from photo_stage_rejections import ActionRejected, require_action

PHASES = ('G1', 'tone', 'G2', 'color', 'G3', 'joint', 'G4', 'complete')
CHECKPOINTS = ('G1', 'G2', 'G3', 'G4')


def public_copy(value):
    """Require serializable public state without clipping narrative fields."""
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('Public state must be finite JSON data') from exc
    return copy.deepcopy(value)


def require_version(state, version):
    if not isinstance(version, str) or version not in state.get('versions', {}):
        raise ValueError('Unknown version: ' + str(version))
    record = state['versions'][version]
    if not isinstance(record, dict):
        raise ValueError('Version record must be an object')
    _not_engineering(record)
    return record


def _version_ancestry(state, version):
    ancestors = set()
    while version is not None:
        if version in ancestors:
            raise ValueError('Cyclic version ancestry')
        ancestors.add(version)
        version = require_version(state, version).get('parent')
    return ancestors


def _not_engineering(record):
    if (any(record.get(key) is True for key in ('synthetic', 'engineering_fake', 'fake'))
            or record.get('evidence_kind') in ('synthetic', 'engineering', 'fake')):
        raise ValueError('Engineering fixtures cannot qualify photographic workflow progress')


def require_ready(state, *, completing=False):
    if not isinstance(state, dict):
        raise ValueError('State must be an object')
    if state.get('pending') is not None:
        raise ValueError('Pending/unknown action; reconcile before changing workflow')
    allowed = ('working', 'complete') if completing else ('working',)
    if state.get('status', 'working') not in allowed:
        raise ValueError('Stopped/engineering state cannot advance workflow')
    _not_engineering(state)
    require_version(state, state.get('current'))


def observation_for(state, observation_id, *, reviewed=False):
    if not isinstance(observation_id, str) or not observation_id.strip():
        raise ValueError('Explicit observation ID required')
    rows = [o for o in state.get('observations', []) if o.get('id') == observation_id]
    if len(rows) != 1:
        raise ValueError('Unknown or ambiguous observation ID')
    observation = rows[0]
    require_version(state, observation.get('version'))
    _not_engineering(observation)
    if reviewed:
        judgment = observation.get('decision_judgment')
        if (not isinstance(judgment, dict)
                or not isinstance(judgment.get('visible_comparison'), str)
                or not judgment['visible_comparison'].strip()
                or judgment.get('review_quality') == 'missing_visible_comparison'):
            raise ValueError('Explicit public visible comparison required; delivery is not review')
    return observation


def _workflow(state):
    workflow = state.get('workflow')
    if not isinstance(workflow, dict) or workflow.get('phase') not in PHASES:
        raise ValueError('New authoritative state.workflow required; do not upgrade old runs')
    if (type(workflow.get('revision')) is not int or workflow['revision'] < 0
            or not isinstance(workflow.get('checkpoints'), dict)
            or set(workflow['checkpoints']) != set(CHECKPOINTS)
            or not isinstance(workflow.get('accepted_versions'), dict)
            or set(workflow['accepted_versions']) != {'tone', 'color', 'joint'}
            or not isinstance(workflow.get('review_debts'), list)
            or not isinstance(workflow.get('events'), list)):
        raise ValueError('Malformed authoritative workflow')
    public_copy(workflow)
    return workflow


def init_workflow(state):
    """Initialize only workflow/bank; return a detached snapshot. No IO."""
    require_ready(state)
    if 'workflow' in state:
        workflow = _workflow(state)
    else:
        workflow = {'phase': 'G1', 'revision': 0,
                    'checkpoints': {name: None for name in CHECKPOINTS},
                    'accepted_versions': {name: None for name in ('tone', 'color', 'joint')},
                    'review_debts': [], 'events': []}
    from photo_proposal_bank import validate_bank
    bank = state.get('proposal_bank', {'revision': 0, 'issues': {}, 'events': []})
    validate_bank(bank)
    state['workflow'] = copy.deepcopy(workflow)
    state['proposal_bank'] = copy.deepcopy(bank)
    return copy.deepcopy(workflow)


def _bound_phase(observation, phase):
    metadata = observation.get('stage_observation', {})
    declared = observation.get('phase', metadata.get('phase'))
    require_action(declared == phase, 'observation_phase_mismatch',
                   'Observation must be explicitly bound to phase ' + phase, 'source_observation_id')


def _full_color_target(observation):
    spec = observation.get('view_spec', {})
    return (observation.get('comparison_mode', spec.get('comparison_mode', 'target')) == 'target'
            and observation.get('artifact', 'canonical') in ('canonical', 'export')
            and not any(spec.get(key) is not None for key in ('candidate_roi', 'reference_roi'))
            and not observation.get('batch_of') and not spec.get('batch_of')
            and observation.get('stage_observation', {}).get('view_mode', 'color') == 'color')


def checkpoint_evidence(state, observation, phase):
    """Bind the decision's current detail view and an actually delivered full view."""
    _bound_phase(observation, phase)
    if phase not in CHECKPOINTS:
        return observation
    if phase == 'G4':
        # A current detail view may borrow the full view's framing, never its
        # artifact identity: both must describe this actual selected JPEG.
        _actual_export(state, observation, observation['version'])
    sources = set(state.get('model_action_source_observations', []))
    for candidate in reversed(state.get('observations', [])):
        if candidate.get('version') != observation['version'] or not _full_color_target(candidate):
            continue
        if candidate.get('phase', candidate.get('stage_observation', {}).get('phase')) != phase:
            continue
        judgment = candidate.get('decision_judgment') or {}
        if candidate['id'] != observation['id'] and candidate['id'] not in sources and not judgment.get('visible_comparison', '').strip():
            continue
        if phase == 'G4':
            _actual_export(state, candidate, observation['version'])
        return candidate
    raise ActionRejected('checkpoint_full_view_required',
        'Checkpoint requires a same-phase, same-version delivered/reviewed full-color target view',
        'source_observation_id')


def _actual_export(state, observation, version):
    export = state.get('export')
    if (not isinstance(export, dict) or observation.get('artifact') != 'export'
            or not isinstance(export.get('path'), str) or not export['path']
            or observation.get('path') != export['path'] or export.get('version') != version):
        raise ActionRejected('actual_export_required', 'G4 requires the worker-recorded actual selected-version export artifact', 'action.arguments.selected_version')
    _not_engineering(export)


def _color_reassessment(before, after, delta, reassessment):
    delta = [] if delta is None else delta
    reassessment = [] if reassessment is None else reassessment
    if not isinstance(delta, list) or not isinstance(reassessment, list):
        raise ActionRejected('color_reassessment_required', 'Color reassessment must be a list when supplied', 'action.arguments.color_reassessment')
    def needs_color(row):
        groups = row.get('task_groups', {}).values()
        return row['status'] != 'retired' and (row['branch'] in ('color', 'joint') or any(
            group.get('branch') in ('color', 'joint') and group.get('status') not in ('retired', 'resolved') for group in groups))
    old = {i: row for i, row in before.get('issues', {}).items() if needs_color(row)}
    new = {i for i, row in after.get('issues', {}).items()
           if i not in before.get('issues', {}) and needs_color(row)}
    decisions = {}
    for item in reassessment:
        if not isinstance(item, dict) or not isinstance(item.get('reason'), str) or not item['reason'].strip():
            raise ValueError('Color reassessment needs an explicit public reason')
        decision = item.get('decision')
        if decision == 'unchanged_all':
            if len(reassessment) != 1 or new:
                raise ValueError('unchanged_all cannot hide new/revised color judgments')
            decisions = {ident: 'keep' for ident in old}
            break
        ident = item.get('issue_id')
        if not isinstance(ident, str) or ident in decisions or decision not in ('keep', 'revise', 'retire', 'new', 'defer', 'uncertain'):
            raise ValueError('Invalid/duplicate color reassessment')
        decisions[ident] = decision
    # Reassessment is a working ledger, not an all-items-at-once checkpoint.
    # Unmentioned proposals remain stale/deferred and are reviewed when selected.
    expected_ids = set(old) | new
    decisions = {ident: decision for ident, decision in decisions.items() if ident in expected_ids}
    operations = {item.get('id'): item.get('op') for item in delta}
    expected = {'keep': 'keep', 'revise': 'revise', 'retire': 'retire', 'new': 'add', 'defer': 'defer', 'uncertain': 'revise'}
    for ident, decision in decisions.items():
        # A same-version group review may already carry the disposition. Keep
        # the public decision even when it is not duplicated in bank_delta.
        if decision == 'new' and ident not in after.get('issues', {}):
            raise ActionRejected('color_reassessment_unknown', 'New judgment needs a stored proposal: ' + ident, 'action.arguments.color_reassessment')


def validate_transition(state, reason, observation_id, selected_version=None, *,
                        target_phase=None, bank_delta=None, color_reassessment=None, bank_before=None):
    """Return a detached commit plan; never change state or execute the next action.

    Observations carry phase (or stage_observation.phase). All exits need a public
    review. joint->G4 additionally requires the worker's actual export record;
    the worker creates the new G4 observation after this transition. G4->complete
    is only committed AFTER the worker's
    explicit successful finish/confirm action has set status and selected_version.
    G2->color requires explicit per-judgment keep/revise/retire/new reassessment;
    unchanged_all is allowed only with matching explicit keep bank operations.
    """
    workflow = _workflow(state)
    phase = workflow['phase']
    require_ready(state, completing=phase == 'G4')
    if phase == 'complete':
        raise ValueError('Workflow is already complete')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('Explicit public stage-exit reason required')
    next_phase = PHASES[PHASES.index(phase) + 1]
    if target_phase is not None and target_phase != next_phase:
        raise ActionRejected('invalid_forward_phase', 'Cannot skip, reverse or invent workflow phases', 'action.arguments.target_phase')
    observation = observation_for(state, observation_id, reviewed=True)
    version = observation['version'] if selected_version is None else selected_version
    require_version(state, version)
    if version != observation['version']:
        raise ValueError('Selected version must be the version actually observed')
    full_observation = checkpoint_evidence(state, observation, phase)
    if phase == 'joint':
        export = state.get('export')
        if (not isinstance(export, dict) or export.get('version') != version
                or not isinstance(export.get('path'), str) or not export['path']):
            raise ValueError('Only the worker actual selected-version export can open G4')
        _not_engineering(export)
    if phase == 'G2' and workflow['accepted_versions']['tone'] != version:
        raise ValueError('G2 must reassess the accepted tone version')
    if phase == 'G3' and workflow['accepted_versions']['color'] != version:
        raise ValueError('G3 must review the accepted color version')
    if phase == 'G4':
        _actual_export(state, full_observation, version)
        if (workflow['accepted_versions']['joint'] != version or state.get('status') != 'complete'
                or state.get('selected_version') != version):
            raise ValueError('Worker must successfully confirm this actual JPEG before completing workflow')
    outstanding = [d['id'] for d in workflow['review_debts']
                   if d.get('status', 'pending') == 'pending' and d.get('due_phase') == phase]
    draft = copy.deepcopy(state)
    if bank_delta is not None and not isinstance(bank_delta, list):
        raise ValueError('bank_delta must be a list')
    if bank_delta and bank_before is None:
        from photo_proposal_bank import apply_bank_delta
        apply_bank_delta(draft, bank_delta, observation_id)
    if phase == 'G2':
        _color_reassessment(state.get('proposal_bank', {}) if bank_before is None else bank_before, draft.get('proposal_bank', {}),
                            bank_delta, color_reassessment)
    # Outside G2 this is planning information, not completion of its review.
    # Preserve it below without blocking an otherwise valid phase transition.
    result = copy.deepcopy(workflow)
    result['phase'] = next_phase
    result['revision'] += 1
    event = {'id': 'workflow:' + str(result['revision']), 'from_phase': phase,
             'to_phase': next_phase, 'observation_id': observation_id, 'version': version, 'reason': reason}
    if outstanding:
        event['outstanding_review_debts'] = outstanding
    if color_reassessment is not None:
        event['color_reassessment' if phase == 'G2' else 'early_color_reassessment_plan'] = public_copy(color_reassessment)
    if phase in CHECKPOINTS:
        event.update(source_observation_id=observation_id, full_observation_id=full_observation['id'])
    if phase in CHECKPOINTS:
        result['checkpoints'][phase] = copy.deepcopy(event)
    if phase in ('tone', 'color', 'joint'):
        result['accepted_versions'][phase] = version
        ancestors = _version_ancestry(state, version)
        for debt in result['review_debts']:
            require_version(state, debt.get('version'))
            if debt.get('status', 'pending') == 'pending' and debt['version'] not in ancestors:
                # Branch selection removes applicability, not the unreviewed audit.
                debt.update(status='not_applicable_selected_branch',
                            selection_event_id=event['id'], selected_version=version,
                            selection_observation_id=observation_id)
    result['events'].append(event)
    return {'from_phase': phase, 'to_phase': next_phase, 'expected_revision': workflow['revision'],
            'workflow': result, 'proposal_bank': copy.deepcopy(draft.get('proposal_bank'))}


def revisit_workflow(state, reason, observation_id, target_phase):
    """Explicit repair only: G2->tone, G3->tone/color or reviewed G4->joint."""
    require_ready(state)
    workflow = copy.deepcopy(_workflow(state))
    phase = workflow['phase']
    allowed = {'G2': ('tone',), 'G3': ('tone', 'color'), 'G4': ('joint',)}
    if target_phase not in allowed.get(phase, ()):
        raise ActionRejected('unsupported_repair_route', 'Unsupported checkpoint repair route', 'action.arguments.target_phase')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('Explicit public repair reason required')
    observation = observation_for(state, observation_id, reviewed=True)
    full_observation = checkpoint_evidence(state, observation, phase)
    if phase == 'G4':
        _actual_export(state, full_observation, observation['version'])
    draft = copy.deepcopy(state)
    from photo_proposal_bank import invalidate_bank
    invalidate_bank(draft, observation['version'])
    for branch in ('tone', 'color', 'joint'):
        if PHASES.index(branch) >= PHASES.index(target_phase):
            workflow['accepted_versions'][branch] = None
    for checkpoint in CHECKPOINTS:
        if PHASES.index(checkpoint) > PHASES.index(target_phase):
            workflow['checkpoints'][checkpoint] = None
    workflow['phase'] = target_phase
    workflow['revision'] += 1
    event = {'id': 'workflow:' + str(workflow['revision']), 'kind': 'checkpoint_repair',
             'from_phase': phase, 'to_phase': target_phase, 'reason': reason,
             'version': observation['version'], 'observation_id': observation_id,
             'source_observation_id': observation_id, 'full_observation_id': full_observation['id'],
             'export_confirmation_invalidated': phase == 'G4'}
    workflow['events'].append(event)
    state['workflow'] = workflow
    state['proposal_bank'] = draft['proposal_bank']
    if phase == 'G4':
        export = copy.deepcopy(state['export'])
        export['confirmation_invalidated_by'] = event['id']
        state['export'] = export
    return copy.deepcopy(workflow)


def return_to_joint(state, reason, observation_id):
    """G4 JPEG repair; preserve the actual export and selected-version history."""
    return revisit_workflow(state, reason, observation_id, 'joint')


def advance_workflow(state, reason, observation_id, selected_version=None, **options):
    """Validate against current state and commit only workflow/bank, atomically in memory."""
    plan = validate_transition(state, reason, observation_id, selected_version, **options)
    state['workflow'] = plan['workflow']
    if plan['proposal_bank'] is not None:
        state['proposal_bank'] = plan['proposal_bank']
    return copy.deepcopy(state['workflow'])


def add_review_debt(state, debt):
    """Record a host-bound cross-stage consequence; never invent a review."""
    require_ready(state)
    workflow = copy.deepcopy(_workflow(state))
    debt = public_copy(debt)
    if (not isinstance(debt, dict) or not isinstance(debt.get('id'), str) or not debt['id'].strip()
            or debt.get('due_phase') not in CHECKPOINTS
            or not isinstance(debt.get('reason'), str) or not debt['reason'].strip()
            or debt.get('status', 'pending') != 'pending'):
        raise ValueError('Debt needs an ID, known due checkpoint and public reason')
    require_version(state, debt.get('version'))
    if any(row['id'] == debt['id'] for row in workflow['review_debts']):
        raise ValueError('Duplicate review debt ID')
    if PHASES.index(debt['due_phase']) < PHASES.index(workflow['phase']):
        raise ValueError('Review debt cannot be due at an already passed checkpoint')
    debt['status'] = 'pending'
    workflow['review_debts'].append(debt)
    workflow['revision'] += 1
    workflow['events'].append({'id': 'workflow:' + str(workflow['revision']),
                               'kind': 'review_debt_added', 'debt': copy.deepcopy(debt)})
    state['workflow'] = workflow
    return copy.deepcopy(debt)


def resolve_review_debts(state, updates, observation_id):
    """Explicit model reviews/tradeoffs, with the actual observed descendant version."""
    require_ready(state)
    workflow = copy.deepcopy(_workflow(state))
    observation = observation_for(state, observation_id, reviewed=True)
    checkpoint_evidence(state, observation, workflow['phase'])
    if not isinstance(updates, list):
        raise ValueError('Debt updates must be a list')
    ancestors = _version_ancestry(state, observation['version'])
    seen = set()
    for update in updates:
        if not isinstance(update, dict):
            raise ValueError('Debt update must be an object')
        ident = update.get('id')
        debt = next((row for row in workflow['review_debts'] if row['id'] == ident), None)
        if (debt is None or ident in seen or debt['status'] != 'pending'
                or debt['version'] not in ancestors or update.get('status') not in ('resolved', 'accepted_tradeoff')
                or not isinstance(update.get('reason'), str) or not update['reason'].strip()):
            raise ValueError('Invalid, stale, duplicate or unsupported debt resolution')
        if PHASES.index(workflow['phase']) < PHASES.index(debt['due_phase']):
            raise ValueError('Debt requires its designated later color checkpoint')
        seen.add(ident)
        debt.update(status=update['status'], resolution_reason=update['reason'],
                    observation_id=observation_id, reviewed_version=observation['version'])
    if updates:
        workflow['revision'] += 1
        workflow['events'].append({'id': 'workflow:' + str(workflow['revision']), 'kind': 'review_debts_resolved',
                                   'observation_id': observation_id, 'updates': public_copy(updates)})
        state['workflow'] = workflow
    return copy.deepcopy(workflow['review_debts'])
