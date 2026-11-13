"""Lossless, version-bound proposal issues and derived photographic work groups.

Issue deltas remain add/keep/revise/retire/defer. Groups are views of those same
proposals, not another executor. Native actions are never generated or trimmed.
All mutations validate a detached bank, append snapshots, then commit atomically.
"""
import copy

from photo_phase_scheduler import (PHASES, observation_for, public_copy,
                                   require_ready, require_version)
from photo_stage_rejections import ActionRejected
from photo_stage_tasks import (GROUP_STATUSES, derive_groups, group_sort_key,
                               parse_controls, registry)

BRANCHES = ('tone', 'color', 'joint')
OPS = ('add', 'keep', 'revise', 'retire', 'defer')
FIELDS = {'branch', 'diagnosis', 'priority', 'proposed_controls', 'evidence', 'scope',
          'target_change', 'protected', 'risks', 'dependencies', 'rationale', 'reason',
          'groups', 'group_id', 'module', 'step', 'reorder_override', 'selection_reason'}


def _id(value, name='issue ID'):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Explicit nonempty ' + name + ' required')
    return value


def validate_bank(bank):
    if (not isinstance(bank, dict) or type(bank.get('revision')) is not int or bank['revision'] < 0
            or not isinstance(bank.get('issues'), dict) or not isinstance(bank.get('events'), list)):
        raise ValueError('Malformed proposal bank')
    public_copy(bank)
    for ident, row in bank['issues'].items():
        _id(ident)
        if (not isinstance(row, dict) or row.get('id') != ident or row.get('branch') not in BRANCHES
                or row.get('status') not in ('active', 'deferred', 'retired')
                or type(row.get('revision')) is not int or row['revision'] < 1
                or not isinstance(row.get('records'), list) or not row['records']
                or type(row.get('needs_reassessment')) is not bool):
            raise ValueError('Malformed proposal issue: ' + ident)
        groups = row.get('task_groups', {})
        if not isinstance(groups, dict):
            raise ValueError('Malformed task groups: ' + ident)
        for gid, group in groups.items():
            if (not isinstance(group, dict) or group.get('id') != gid or group.get('issue_id') != ident
                    or group.get('branch') not in BRANCHES or group.get('status') not in GROUP_STATUSES
                    or not isinstance(group.get('records'), list)):
                raise ValueError('Malformed task group: ' + str(gid))
    return bank


def _bank(state):
    return validate_bank(state.get('proposal_bank', {'revision': 0, 'issues': {}, 'events': []}))


def _snapshot(row, event, **details):
    row['revision'] += 1
    record = {key: copy.deepcopy(value) for key, value in row.items() if key != 'records'}
    record.update(record_id='bank:' + row['id'] + ':' + str(row['revision']), event=event, **details)
    row['records'].append(record)


def _group_snapshot(group, event, **details):
    record = {key: copy.deepcopy(value) for key, value in group.items() if key != 'records'}
    record.update(record_id='group:' + group['id'] + ':' + str(len(group['records']) + 1),
                  event=event, **details)
    group['records'].append(record)


def _event(bank, kind, **fields):
    bank['revision'] += 1
    bank['events'].append({'id': 'bank-event:' + str(bank['revision']), 'kind': kind, **fields})


def _ensure_groups(row):
    # Old issue-only API state remains readable; derivation does not rewrite its originals.
    if 'task_groups' not in row:
        row['task_groups'] = {g['id']: g for g in derive_groups(row)}
    return row['task_groups']


def _ancestry(state, base):
    result = set()
    while base is not None:
        if base in result:
            raise ValueError('Cyclic version ancestry')
        result.add(base)
        base = require_version(state, base).get('parent')
    return result


def _validate_changes(changes):
    if 'branch' in changes and changes['branch'] not in BRANCHES:
        raise ValueError('Unknown proposal branch')
    for key in ('diagnosis', 'rationale', 'reason', 'selection_reason'):
        if key in changes and (not isinstance(changes[key], str) or not changes[key].strip()):
            raise ValueError(key + ' must be a nonempty public string')
    if 'priority' in changes and type(changes['priority']) not in (int, float):
        raise ValueError('priority must be a finite number')
    if 'proposed_controls' in changes and not isinstance(changes['proposed_controls'], (dict, list)):
        raise ValueError('proposed_controls must be an explicit object or list')
    override = changes.get('reorder_override')
    if override is not None:
        if (not isinstance(override, dict) or not isinstance(override.get('reason'), str)
                or not override['reason'].strip()
                or (type(override.get('order')) not in (int, float) and not isinstance(override.get('before'), str))):
            raise ValueError('reorder_override requires an order or before ID and an explicit reason')
    if 'groups' in changes:
        if not isinstance(changes['groups'], list):
            raise ValueError('groups must be a list')
        for spec in changes['groups']:
            if not isinstance(spec, dict):
                raise ValueError('Each group must be a public object')
            _validate_changes({k: v for k, v in spec.items() if k != 'groups'})
            if 'group_id' in spec:
                _id(spec['group_id'], 'group ID')
            if 'status' in spec and spec['status'] not in GROUP_STATUSES:
                raise ValueError('Unknown group status')


def _refresh_groups(row, op, changed):
    old = _ensure_groups(row)
    if op == 'revise' and changed.intersection({'proposed_controls', 'groups'}):
        derived = derive_groups({**row, 'revision': row['revision'] + 1})
        live = set()
        for group in derived:
            gid = group['id']; live.add(gid)
            prior = old.get(gid)
            if prior:
                explicit = next((spec for spec in row.get('groups', [])
                    if isinstance(spec, dict) and gid == row['id'] + '::' + str(spec.get('group_id'))), {})
                reopened = (prior.get('source_proposal') != group.get('source_proposal') or
                            explicit.get('status') in ('ready', 'current', 'needs_reassessment'))
                group = {**prior, **group, 'records': prior['records'], 'operations': prior.get('operations', [])}
                if prior['status'] == 'resolved' and not reopened:
                    group['status'] = 'resolved'
            old[gid] = group
        for gid in old.keys() - live:
            old[gid].update(status='retired', wait_reason='Proposal revised; original group and history retained')
    for group in old.values():
        for field in ('priority', 'dependencies', 'reorder_override', 'selection_reason'):
            if field in changed:
                group[field] = copy.deepcopy(row[field])
        if group['status'] == 'retired' and op != 'retire':
            continue
        group.update(base_version=row['base_version'], observation_id=row['observation_id'], observation=row['observation_id'])
        if op in ('keep', 'revise'):
            group['reassessed_for_version'] = row['base_version']
            group['manual_wait'] = False
        if op == 'add':
            group['source_revision'] = row['revision'] + 1
            group['shared_context_ref']['source_revision'] = group['source_revision']
        if op == 'retire':
            group.update(status='retired', wait_reason='Issue explicitly retired')
        elif op == 'defer':
            group.update(status='later_phase', wait_reason=row.get('reason', 'Issue explicitly deferred'))
        elif group['status'] not in ('unresolved', 'resolved'):
            group.update(status='ready', wait_reason=None)
        _group_snapshot(group, op)


def _find_group(row, group_id):
    groups = _ensure_groups(row)
    return groups.get(group_id) or groups.get(row['id'] + '::' + str(group_id))


def _dependency_targets(bank, group, ref):
    if not isinstance(ref, str):
        return [], 'unresolved'
    local = _ensure_groups(bank['issues'][group['issue_id']])
    if '::' not in ref:
        prefix = group['issue_id'] + '::' + ref
        matches = [gid for gid in local if gid == prefix or gid.startswith(prefix + '/')]
        if matches:
            return (matches, 'resolved') if len(matches) == 1 else ([], 'ambiguous')
    all_groups = {gid for row in bank['issues'].values() for gid in _ensure_groups(row)}
    if ref in all_groups:
        return [ref], 'resolved'
    if ref in bank['issues']:
        return list(_ensure_groups(bank['issues'][ref])), 'resolved'
    return [], 'unresolved'


def _normalize_dependencies(bank):
    # Keep the source spelling; only unique same-issue aliases become group IDs.
    for row in bank['issues'].values():
        for group in _ensure_groups(row).values():
            deps = group.get('dependencies', [])
            deps = [deps] if isinstance(deps, str) else deps
            references = []
            for dep in deps if isinstance(deps, list) else []:
                ref = dep if isinstance(dep, str) else dep.get('group_id', dep.get('issue_id', dep.get('id'))) if isinstance(dep, dict) else None
                targets, resolution = _dependency_targets(bank, group, ref)
                references.append({'original': copy.deepcopy(dep), 'resolved': targets[0] if len(targets) == 1 else targets,
                                   'resolution': resolution})
            group['dependency_references'] = references


def apply_bank_delta(state, delta, observation_id):
    """Commit the complete issue/group delta only after validation. No execution."""
    require_ready(state)
    observation = observation_for(state, observation_id, reviewed=True)
    if not isinstance(delta, list):
        raise ValueError('bank_delta must be a list')
    bank = copy.deepcopy(_bank(state)); seen = set()
    for item in delta:
        item = public_copy(item)
        if not isinstance(item, dict) or item.get('op') not in OPS or set(item) - FIELDS - {'id', 'op'}:
            raise ValueError('Unknown bank operation/fields')
        ident = _id(item.get('id')); op = item['op']; gid = item.get('group_id')
        key = (ident, gid)
        if key in seen or ((ident, None) in seen) or (gid is None and any(i == ident for i, _ in seen)):
            raise ActionRejected('duplicate_bank_update', 'Only one explicit change per issue/group in a delta', 'bank_delta')
        seen.add(key)
        changes = {key: value for key, value in item.items() if key not in ('id', 'op', 'group_id')}
        _validate_changes(changes)
        if op == 'add':
            if gid is not None or ident in bank['issues'] or 'branch' not in changes or 'diagnosis' not in changes:
                raise ActionRejected('bank_add_identity', 'New issue needs a unique ID, branch and public diagnosis', 'bank_delta.id')
            row = {'id': ident, 'status': 'active', 'revision': 0, 'records': [],
                   'operations': [], 'needs_reassessment': False, **changes}
            bank['issues'][ident] = row
        else:
            row = bank['issues'].get(ident)
            if row is None:
                raise ActionRejected('unknown_bank_issue', 'Unknown issue ID: ' + ident, 'bank_delta.id')
            if row['status'] == 'retired':
                raise ActionRejected('retired_bank_issue', 'Retired issue is immutable; add a new explicit issue instead', 'bank_delta.id')
            if op != 'revise' and set(changes) - {'reason', 'rationale'}:
                raise ActionRejected('bank_revise_required', 'Use revise to change a judgment or proposal', 'bank_delta.op')
            if 'branch' in changes and changes['branch'] != row['branch'] and gid is None:
                raise ActionRejected('bank_branch_revision', 'Revise or add explicit stage groups instead of replacing an issue branch', 'bank_delta.branch')
        if gid is not None:
            group = _find_group(row, _id(gid, 'group ID'))
            if group is None:
                raise ActionRejected('unknown_bank_group', 'Unknown group ID', 'bank_delta.group_id')
            if group['status'] == 'retired':
                raise ActionRejected('retired_bank_group', 'Retired group is immutable; explicitly revise the issue instead', 'bank_delta.group_id')
            group.update(copy.deepcopy(changes))
            if 'proposed_controls' in changes:
                parts = parse_controls(changes['proposed_controls'], 'group_revision.proposed_controls', group['branch'])
                group.update(proposed_controls=parts, source_revision=row['revision'] + 1,
                             source_proposal=copy.deepcopy(changes['proposed_controls']),
                             source_paths=[p['source_path'] for p in parts])
                group['unresolved'] = any(p.get('unresolved_reason') for p in parts)
            prior_status = group['status']
            group.update(base_version=observation['version'], observation_id=observation_id, observation=observation_id,
                         status='retired' if op == 'retire' else 'later_phase' if op == 'defer' else
                         'resolved' if op == 'keep' and prior_status == 'resolved' else
                         'unresolved' if group.get('unresolved') else 'ready', wait_reason=changes.get('reason'))
            if op in ('keep', 'revise'):
                group['reassessed_for_version'] = observation['version']
                group['manual_wait'] = False
            _group_snapshot(group, op)
        else:
            row.update(changes)
            row.update(status='retired' if op == 'retire' else 'deferred' if op == 'defer' else 'active',
                       base_version=observation['version'], observation_id=observation_id,
                       needs_reassessment=op == 'defer')
            _refresh_groups(row, op, set(changes))
        row.pop('invalidated_for_version', None)
        _snapshot(row, op, **({'selected_group_id': group['id']} if gid is not None else {}))
    _normalize_dependencies(bank)
    if delta:
        _event(bank, 'bank_delta', observation_id=observation_id, version=observation['version'], delta=public_copy(delta))
    state['proposal_bank'] = bank
    return copy.deepcopy(bank)


def validate_selected_issue(state, issue_id, base):
    """Old issue-only guard retained. Fine selection is resolve_selected_group."""
    require_version(state, base)
    if issue_id is None:
        return None
    row = _bank(state)['issues'].get(_id(issue_id))
    if (row is None or row['status'] != 'active' or row['needs_reassessment'] or row['base_version'] != base):
        raise ActionRejected('stale_issue', 'Selected issue is unknown, inactive or stale; explicitly reassess first', 'selected_issue_id')
    phase = state.get('workflow', {}).get('phase')
    if phase not in BRANCHES or (phase != 'joint' and row['branch'] != phase):
        raise ActionRejected('issue_phase', 'Selected issue is outside the authoritative editing phase', 'selected_issue_id')
    return copy.deepcopy(row)


def _effective_groups(state, bank, phase, base=None):
    """Compute version/phase/dependency readiness without changing any stored record."""
    base = base or state['current']; ancestors = _ancestry(state, base)
    result = {}; issue_groups = {}
    for row in bank['issues'].values():
        issue_groups[row['id']] = []
        for group in _ensure_groups(row).values():
            group = copy.deepcopy(group); gid = group['id']
            issue_groups[row['id']].append(gid)
            if group['status'] != 'retired':
                operation = group.get('operations', [])[-1] if group.get('operations') else {}
                if group['base_version'] not in ancestors or (operation.get('child') and operation['child'] not in ancestors
                        and group.get('reassessed_for_version') != base):
                    group.update(status='off_selected_branch', wait_reason='Evidence belongs to an unselected branch; reassess on selected version')
                elif group.get('unresolved') or group['status'] == 'unresolved':
                    group.update(status='unresolved', wait_reason='Unknown or ambiguous proposal; explicitly revise before execution')
                elif row['status'] == 'retired':
                    group.update(status='retired', wait_reason='Issue explicitly retired')
                elif row['status'] == 'deferred':
                    group.update(status='later_phase', wait_reason=row.get('reason', 'Issue explicitly deferred'))
                elif group['status'] == 'resolved':
                    pass  # A later edit does not reopen a task explicitly closed by DSH.
                elif group['status'] == 'awaiting_effect':
                    operation = group.get('operations', [])[-1] if group.get('operations') else {}
                    if operation.get('child') not in ancestors:
                        group.update(status='off_selected_branch', wait_reason='After-version is not on selected branch; review selected version afresh')
                elif group['base_version'] != base:
                    group.update(status='needs_reassessment', wait_reason='Proposal requires the actual selected-version observation')
                elif group['status'] not in ('resolved', 'waiting_dependency', 'needs_reassessment', 'off_selected_branch'):
                    if phase in ('G3', 'joint', 'G4', 'complete') or group['branch'] == phase:
                        group.update(status='current' if group['status'] == 'current' else 'ready', wait_reason=None)
                    else:
                        group.update(status='later_phase', wait_reason='Available in ' + group['branch'] + ' or joint editing, not ' + phase)
            group['needs_reassessment'] = group['status'] in ('needs_reassessment', 'off_selected_branch', 'unresolved')
            result[gid] = group
    edges = {}; missing = {}
    for gid, group in result.items():
        deps = group.get('dependencies', [])
        if deps in (None, []):
            deps = []
        elif isinstance(deps, str):
            deps = [deps]
        elif not isinstance(deps, list):
            missing[gid] = ['Ambiguous dependency structure; revise explicit IDs']; deps = []
        edges[gid] = []
        for dep in deps:
            ref = dep if isinstance(dep, str) else dep.get('group_id', dep.get('issue_id', dep.get('id'))) if isinstance(dep, dict) else None
            targets, resolution = _dependency_targets(bank, group, ref)
            if not targets:
                missing.setdefault(gid, []).append(('Ambiguous' if resolution == 'ambiguous' else 'Unknown') + ' dependency reference: ' + str(ref))
            edges[gid].extend(targets)
    def cyclic(start, node, visited):
        for target in edges[node]:
            if target == start:
                return True
            if target not in visited and cyclic(start, target, visited | {target}):
                return True
        return False
    for gid, group in result.items():
        reasons = missing.get(gid, [])
        if cyclic(gid, gid, {gid}):
            reasons = [*reasons, 'Cyclic dependency; explicitly revise or retire the dependency']
        unmet = [target for target in edges[gid] if result[target]['status'] not in ('resolved', 'retired')]
        if unmet:
            reasons = [*reasons, 'Waiting for dependency: ' + ', '.join(unmet)]
        if reasons and group['status'] not in ('retired', 'resolved', 'off_selected_branch', 'unresolved', 'awaiting_effect'):
            group.update(status='waiting_dependency', wait_reason='; '.join(reasons))
        elif not reasons and group['status'] == 'waiting_dependency' and not group.get('manual_wait'):
            group.update(status='ready' if phase == group['branch'] or phase == 'joint' else 'later_phase', wait_reason=None)
    return result


def _ordered(groups, phase):
    ordered = sorted(groups, key=lambda group: ((0 if phase in ('G3', 'joint') and group['branch'] == 'joint' else 1), *group_sort_key(group)))
    # Relative overrides are one presentation pass, never an execution dependency.
    for group in list(ordered):
        override = group.get('reorder_override', {})
        if isinstance(override, dict) and override.get('reason') and override.get('before'):
            target = next((g for g in ordered if g['id'] == override['before']), None)
            if target is not None and target is not group:
                ordered.remove(group); ordered.insert(ordered.index(target), group)
    return ordered


def resolve_selected_group(state, issue_id, group_id, base, action=None):
    """Pure selection guard; no automatic group change, action clipping or native IO."""
    require_version(state, base)
    if issue_id is None:
        if group_id is not None:
            raise ActionRejected('group_issue_required', 'A selected group requires its issue ID', 'selected_issue_id')
        return None  # Legacy shared API; the production worker requires selection.
    bank = copy.deepcopy(_bank(state)); row = bank['issues'].get(issue_id)
    if row is None or row['status'] != 'active':
        raise ActionRejected('inactive_issue', 'Select an active known issue', 'selected_issue_id')
    phase = state.get('workflow', {}).get('phase')
    if phase not in BRANCHES:
        raise ActionRejected('group_phase', 'Groups execute only in an authoritative editing phase', 'action')
    effective = _effective_groups(state, bank, phase, base)
    groups = [g for g in effective.values() if g['issue_id'] == issue_id]
    if group_id is None:
        eligible = [g for g in groups if g['status'] in ('ready', 'current')]
        if len(eligible) != 1:
            raise ActionRejected('group_selection_required', 'Explicit selected_group_id required: no unique ready fine group; reassess or choose a group', 'selected_group_id')
        group = eligible[0]
    else:
        group = next((g for g in groups if g['id'] in (group_id, issue_id + '::' + group_id)), None)
        if group is None:
            raise ActionRejected('unknown_group', 'Unknown selected group for this issue', 'selected_group_id')
    selection_reason = ((action or {}).get('selection_reason') or (action or {}).get('override_reason')) if isinstance(action, dict) else None
    if (group['status'] == 'waiting_dependency' and group['base_version'] == base
            and group_id and isinstance(selection_reason, str) and selection_reason.strip()):
        group['dependency_override'] = {'reason': selection_reason, 'waiting_reason': group.get('wait_reason'),
                                        'base_version': base, 'claim_type': 'explicit_model_selection'}
        group.update(status='ready', wait_reason=None)
    if group['status'] not in ('ready', 'current'):
        raise ActionRejected('group_not_ready', group.get('wait_reason') or 'Selected group is not ready: ' + group['status'], 'selected_group_id')
    if phase != 'joint' and group['branch'] != phase:
        raise ActionRejected('group_phase', 'Selected group is outside the authoritative phase', 'selected_group_id')
    if action is not None:
        arguments = action.get('arguments', action) if isinstance(action, dict) else action
        parts = parse_controls(arguments, 'action', group['branch'])
        if any(part.get('unresolved_reason') for part in parts):
            raise ActionRejected('unresolved_action_group', 'Actual operation contains an unclassified task path; explicitly revise the group', 'action.arguments')
        proposed = group['proposed_controls']
        allowed = {p['control_id'] for p in proposed}
        submitted = {p['control_id'] for p in parts}
        if not submitted <= allowed:
            raise ActionRejected('group_action_mismatch', 'Actual parameters belong to another fine group; explicitly revise/select without silently splitting the operation', 'action.arguments')
        hypotheses = {(p['module'], p['step']) for p in parts}
        selection_reason = (action.get('selection_reason') or action.get('override_reason') or
                            arguments.get('selection_reason') or arguments.get('override_reason') or
                            group.get('selection_reason') or group.get('reason'))
        if len(hypotheses) > 1 and not (group_id and selection_reason):
            raise ActionRejected('group_reason_required', 'Cross-step operation needs an explicit shared-hypothesis group and selection_reason', 'action.arguments.selection_reason')
    return copy.deepcopy(group)


def record_operation(state, issue_id, operation_id, base, child, *, group_id=None, action=None):
    """Bind actual child/params to the selected group; never claim improvement."""
    require_ready(state); _id(operation_id, 'operation ID'); require_version(state, base)
    actual = require_version(state, child)
    if child == base or actual.get('parent') != base or actual.get('operation_id') != operation_id:
        raise ValueError('Operation must match the actual child version and parent')
    group = resolve_selected_group(state, issue_id, group_id, base, action) if (group_id is not None or action is not None) else None
    if group is None:
        row = validate_selected_issue(state, issue_id, base)
        if row is not None:
            candidates = [g for g in _ensure_groups(row).values() if g['status'] not in ('retired', 'resolved', 'unresolved')]
            if len(candidates) == 1:
                group = candidates[0]
    bank = copy.deepcopy(_bank(state))
    if any(event.get('operation_id') == operation_id for event in bank['events']):
        raise ValueError('Operation was already recorded; never replay')
    arguments = action.get('arguments', action) if isinstance(action, dict) else action
    record = {'issue_id': issue_id, 'group_id': group['id'] if group else None,
              'operation_id': operation_id, 'base': base, 'child': child, 'outcome': 'unreviewed',
              'actual_params': public_copy(arguments if arguments is not None else actual.get('arguments', {})),
              'action_context': public_copy(action)}
    if issue_id is not None:
        row = bank['issues'][issue_id]; row.setdefault('operations', []).append(copy.deepcopy(record))
        row.update(needs_reassessment=True, invalidated_for_version=child)
        if group:
            target = _ensure_groups(row)[group['id']]
            if group.get('dependency_override'):
                record['dependency_override'] = copy.deepcopy(group['dependency_override'])
            target.setdefault('operations', []).append(copy.deepcopy(record))
            target.update(status='awaiting_effect', wait_reason='Review actual parent/current consequence before reusing this group')
            _group_snapshot(target, 'operation', operation=record)
        _snapshot(row, 'operation', operation=copy.deepcopy(record))
    _event(bank, 'operation', **record); state['proposal_bank'] = bank
    if group:
        state['selected_group_id'] = group['id']
    return copy.deepcopy(record)


def invalidate_bank(state, newversion, except_issue=None):
    """Preserve full history; mark stale or off-selected-branch without restoring it."""
    require_ready(state); require_version(state, newversion)
    bank = copy.deepcopy(_bank(state)); ancestors = _ancestry(state, newversion)
    if except_issue is not None and except_issue not in bank['issues']:
        raise ValueError('Unknown except_issue')
    invalidated = []
    for ident, row in bank['issues'].items():
        if ident == except_issue or row['status'] == 'retired':
            continue
        groups = _ensure_groups(row)
        group_changed = False
        for group in groups.values():
            if group['status'] in ('retired', 'resolved'):
                continue
            op = group.get('operations', [])[-1] if group.get('operations') else {}
            offbranch = group['base_version'] not in ancestors or (op.get('child') and op['child'] not in ancestors
                            and group.get('reassessed_for_version') != newversion)
            status = 'off_selected_branch' if offbranch else 'awaiting_effect' if group['status'] == 'awaiting_effect' else 'needs_reassessment'
            if (group['base_version'] != newversion or offbranch) and group['status'] != status:
                group.update(status=status, wait_reason='Reassess actual selected version; later evidence is not current' if offbranch else 'Version changed; reassess before execution')
                _group_snapshot(group, 'invalidate', new_version=newversion); group_changed = True
        if row['base_version'] == newversion and not group_changed:
            continue
        if row.get('invalidated_for_version') == newversion and not group_changed:
            continue
        row.update(needs_reassessment=True, invalidated_for_version=newversion)
        _snapshot(row, 'invalidate', new_version=newversion); invalidated.append(ident)
    if invalidated:
        _event(bank, 'invalidate', version=newversion, issue_ids=invalidated)
    state['proposal_bank'] = bank
    return copy.deepcopy(bank)


def record_group_review(state, review, observation_id):
    """Keep proposal dispositions separate from judgments of actual effects.

    Raw references are retained. Only effect judgments can update actual
    operations; dispositions may reassess a proposal and edit in the same turn.
    """
    require_ready(state); observation = observation_for(state, observation_id, reviewed=True)
    review = public_copy(review)
    if not isinstance(review, dict):
        raise ValueError('Public group review must be an object')
    bank = copy.deepcopy(_bank(state)); updates = list(review.get('group_reviews', []))
    if 'group_id' in review:
        updates.append(review)
    effect = review.get('effect_review')
    expectations = state.get('effect_expectations', [])
    if isinstance(expectations, dict):
        expectations = list(expectations.values())
    if effect:
        expectation = next((e for e in expectations if e.get('id') == effect.get('expectation_id')), None)
        if expectation is None or expectation.get('child_version') != observation['version']:
            raise ActionRejected('effect_review_version', 'Effect review must bind the actually observed child version', 'review.effect_review.expectation_id')
        actual = require_version(state, observation['version'])
        if actual.get('operation_id') != expectation.get('operation_id') or actual.get('parent') != expectation.get('base_version', actual.get('parent')):
            raise ActionRejected('effect_operation_mismatch', 'Effect expectation must match the actual child operation and parent', 'review.effect_review.expectation_id')
        linked = next((event for event in bank['events'] if event.get('kind') == 'operation' and
                       event.get('operation_id') == expectation.get('operation_id')), None)
        if linked and linked.get('group_id'):
            updates.insert(0, {**effect, 'issue_id': linked['issue_id'], 'group_id': linked['group_id'],
                               'operation_id': linked['operation_id'], 'reason': effect.get('evidence', effect.get('reason'))})
    committed = []; warnings = []
    dispositions = (*OPS[1:], 'resolved', 'needs_reassessment', 'waiting_dependency')
    for update in updates:
        if not isinstance(update, dict):
            raise ValueError('Group review must be an object')
        row = bank['issues'].get(update.get('issue_id'))
        group = _find_group(row, update.get('group_id')) if row else None
        if group is None:
            raise ActionRejected('unknown_reviewed_group', 'Unknown reviewed group for this issue', 'review.group_reviews.group_id')
        reason = update.get('reason', update.get('evidence'))
        if not isinstance(reason, str) or not reason.strip():
            raise ActionRejected('group_review_reason_required', 'Group review requires public evidence/reason', 'review.group_reviews.reason')
        outcome = update.get('outcome', update.get('result', update.get('judgment')))
        operation_id = update.get('operation_id'); operation = None
        reference = None
        if outcome in dispositions:
            if operation_id:
                reference = {'original_id': operation_id, 'resolution': 'not_adopted',
                             'reason': 'Proposal disposition is not an actual effect judgment; operation_id retained as metadata only'}
                warnings.append({'code': 'proposal_operation_reference_not_adopted', 'issue_id': row['id'],
                                 'group_id': group['id'], 'metadata_reference': copy.deepcopy(reference)})
            if outcome in OPS[1:]:
                changes = {k: update[k] for k in FIELDS - {'group_id'} if k in update and k != 'reason'}
                if outcome in ('defer', 'retire') and changes:
                    # A disposition may include scheduling metadata. Apply it
                    # before the disposition rather than demanding another turn.
                    apply_bank_delta(state_copy := {**state, 'proposal_bank': bank},
                        [{'id': row['id'], 'group_id': group['id'], 'op': 'revise', 'reason': reason, **changes}], observation_id)
                    bank = state_copy['proposal_bank']
                    changes = {}
                apply_bank_delta(state_copy := {**state, 'proposal_bank': bank},
                                 [{'id': row['id'], 'group_id': group['id'], 'op': outcome,
                                   'reason': reason, **changes}], observation_id)
                bank = state_copy['proposal_bank']; row = bank['issues'][row['id']]; group = _find_group(row, group['id'])
            else:
                changes = {k: update[k] for k in ('priority', 'dependencies') if k in update}
                _validate_changes(changes)
                group.update(copy.deepcopy(changes))
                group.update(status=outcome, manual_wait=outcome == 'waiting_dependency', wait_reason=reason)
        elif outcome in ('improved', 'worse', 'mixed', 'uncertain', 'supported', 'contradicted', 'not_visible'):
            operation = next((op for op in group.get('operations', []) if op['operation_id'] == operation_id), None)
            expectation = next((e for e in expectations if e.get('operation_id') == operation_id and
                                (not update.get('expectation_id') or e.get('id') == update['expectation_id'])), None)
            actual = require_version(state, observation['version'])
            if (not operation or operation['child'] != observation['version'] or not expectation
                    or expectation.get('child_version') != observation['version']
                    or actual.get('operation_id') != operation_id or actual.get('parent') != operation['base']
                    or expectation.get('base_version', operation['base']) != operation['base']):
                raise ActionRejected('group_effect_mismatch', 'Effect judgment requires this group actual operation, expectation and observed child', 'review.group_reviews.operation_id')
            if group['base_version'] not in _ancestry(state, observation['version']):
                raise ActionRejected('group_effect_off_branch', 'Off-branch group needs fresh proposal reassessment', 'review.group_reviews.group_id')
            reaffirmed = any(record.get('event') in ('keep', 'revise') and
                             record.get('observation_id') == observation_id for record in group['records'][-1:])
            if not reaffirmed:
                group.update(status='needs_reassessment', wait_reason='Model effect reviewed; keep/revise/retire/defer explicitly before reuse')
        else:
            raise ActionRejected('unknown_group_review_outcome', 'Unknown group review outcome', 'review.group_reviews.outcome')
        result = {**copy.deepcopy(update), 'observation_id': observation_id,
                  'version': observation['version'], 'claim_type': 'model_judgment',
                  'review_kind': 'proposal_disposition' if outcome in dispositions else 'effect_judgment'}
        if reference:
            result['metadata_reference'] = reference
        group['last_review'] = result
        group.update(base_version=observation['version'], observation_id=observation_id, observation=observation_id)
        if operation:
            for record in [*group.get('operations', []), *row.get('operations', []), *bank['events']]:
                if record.get('operation_id') == operation_id and record.get('kind', 'operation') == 'operation':
                    record.update(outcome=outcome, model_judgment=copy.deepcopy(result))
        _group_snapshot(group, 'review', review=result); _snapshot(row, 'group_review', review=result)
        committed.append(result)
    if committed:
        _normalize_dependencies(bank)
        _event(bank, 'group_review', observation_id=observation_id, reviews=committed)
        state['proposal_bank'] = bank
    if warnings:
        state.setdefault('proposal_reference_warnings', []).extend(warnings)
    return copy.deepcopy(committed)


def _summary(group):
    keys = ('id', 'group_id', 'issue_id', 'branch', 'module', 'step', 'status', 'priority',
            'base_version', 'observation_id', 'source_revision', 'source_paths', 'dependencies', 'dependency_references',
            'needs_reassessment', 'wait_reason', 'reorder_override')
    return {key: copy.deepcopy(group[key]) for key in keys if key in group}


def branch_workflow(state, phase):
    """Detached ordered branch/module/group navigation for worker presentation."""
    if phase not in PHASES:
        raise ValueError('Unknown workflow phase')
    bank = copy.deepcopy(_bank(state)); groups = _ordered(_effective_groups(state, bank, phase).values(), phase)
    modules = []
    for branch in BRANCHES:
        for module in dict.fromkeys(row['module'] for row in registry(branch)):
            members = [g for g in groups if g['branch'] == branch and g['module'] == module]
            modules.append({'branch': branch, 'module': module,
                            'group_ids': [g['id'] for g in members],
                            'statuses': list(dict.fromkeys(g['status'] for g in members)),
                            'reviewed_groups': [g['id'] for g in members if g.get('last_review')]})
    branches = ({'color', 'joint'} if phase == 'G2' else {'tone'} if phase in ('G1', 'tone')
                else {'color'} if phase == 'color' else set(BRANCHES))
    relevant = [g for g in groups if g['branch'] in branches]
    selected = next((g for g in relevant if g['id'] == state.get('selected_group_id')
                     and g['status'] in ('current', 'awaiting_effect', 'needs_reassessment', 'ready')), None)
    if selected is None:
        selected = next((g for g in relevant if g['status'] in ('current', 'awaiting_effect', 'ready')), None)
    return {'phase': phase, 'registry': {branch: registry(branch) for branch in BRANCHES},
            'groups': [_summary(g) for g in groups], 'modules': modules,
            'current_step': selected['step'] if selected else None,
            'selection_policy': 'Default step order, then priority; explicit reason may skip or revisit. No parameter-count gate.',
            'selected_version': state['current']}


def bank_projection(state, phase):
    """Current + pending review + phase candidates, with all useful ID summaries.

    Body count is a context presentation choice, never a storage/execution quota.
    G2 covers color and joint reassessment; G3 prioritizes joint residual groups.
    """
    if phase not in PHASES:
        raise ValueError('Unknown workflow phase')
    bank = copy.deepcopy(_bank(state)); rows = list(bank['issues'].values())
    groups = _ordered(_effective_groups(state, bank, phase).values(), phase)
    branches = {'tone', 'color', 'joint'} if phase in ('G3', 'joint', 'G4', 'complete') else {'color', 'joint'} if phase == 'G2' else {'tone'} if phase in ('G1', 'tone') else {'color'}
    relevant = [g for g in groups if g['branch'] in branches and g['status'] != 'retired']
    current = [g for g in relevant if g['status'] == 'current']
    selected_id = state.get('selected_group_id')
    if selected_id:
        current.extend(g for g in relevant if g['id'] == selected_id and g not in current)
    pending = [g for g in relevant if g['status'] == 'awaiting_effect']
    ready = [g for g in relevant if g['status'] == 'ready'][:3]
    reassessment = [g for g in relevant if g['status'] == 'needs_reassessment' or
                    (phase in ('G1', 'G2', 'G3') and g['status'] == 'later_phase' and not g.get('unresolved'))][:3]
    candidates = [*ready, *[g for g in reassessment if g not in ready]]
    def body(group):
        return {**{k: copy.deepcopy(v) for k, v in group.items() if k != 'records'},
                'issue_ref': group['issue_id'], 'diagnosis': bank['issues'][group['issue_id']]['diagnosis']}
    packet = {'current': [body(g) for g in current], 'pending_review': [body(g) for g in pending],
              'candidates': [body(g) for g in candidates], 'ready_candidates': [body(g) for g in ready],
              'needs_reassessment': [body(g) for g in reassessment],
              'waiting': [_summary(g) for g in relevant if g['status'] in ('waiting_dependency', 'later_phase', 'unresolved', 'off_selected_branch')],
              'summary': [_summary(g) for g in groups],
              'issues': {r['id']: {'diagnosis': r['diagnosis'], 'priority': r.get('priority', 0), 'branch': r['branch']} for r in rows},
              'selection_required': len([g for g in relevant if g['status'] in ('ready', 'current')]) > 1,
              'selected_version': state['current'], 'full_history_available': True}
    selected_issues = list(dict.fromkeys(g['issue_id'] for g in [*current, *pending, *candidates]))
    # Compatibility navigation for older callers, including diagnosis-only issues.
    if not selected_issues:
        selected_issues = [r['id'] for r in rows if r['status'] == 'active' and r['branch'] in branches][:3]
    return {'revision': bank['revision'], 'phase': phase,
            'active': [{k: copy.deepcopy(v) for k, v in bank['issues'][ident].items() if k not in ('records', 'task_groups')} for ident in selected_issues],
            'summary': [{'id': r['id'], 'branch': r['branch'], 'status': r['status'],
                         'priority': r.get('priority', 0), 'base_version': r['base_version'],
                         'needs_reassessment': r['needs_reassessment'],
                         'record_id': r['records'][-1]['record_id'], 'record_count': len(r['records']),
                         'groups': [_summary(g) for g in groups if g['issue_id'] == r['id']]} for r in rows],
            'work_packet': packet, 'branch_workflow': branch_workflow(state, phase),
            'total': len(rows), 'full_records_available': True}


def bank_history(state, issue_id=None):
    """Detached full original issue snapshots, including group/operation history."""
    bank = _bank(state)
    if issue_id is not None:
        row = bank['issues'].get(_id(issue_id))
        if row is None:
            raise ValueError('Unknown issue ID')
        return copy.deepcopy(row['records'])
    return [copy.deepcopy(record) for row in bank['issues'].values() for record in row['records']]
