"""Ordered photographic task registry and lossless, non-executing proposal parsing.

Order guides presentation, not permission: a reasoned selection may skip/revisit.
Only IDs already exposed by photo_stage_operations are classified here. Ambiguous
leaves remain unresolved, with their exact source paths and public values intact.
"""
import copy

from photo_stage_operations import (TONE_IDS, COLOR_IDS, EFFECT_IDS, SETTINGS_PATHS,
                                    TONE_FIELDS, BANDS)

BRANCHES = ('tone', 'color', 'joint')
GROUP_STATUSES = ('ready', 'needs_reassessment', 'waiting_dependency', 'later_phase',
                  'current', 'awaiting_effect', 'resolved', 'retired',
                  'off_selected_branch', 'unresolved')
# Each row is (step, canonical module, public label). Not a mandatory menu walk.
TASK_REGISTRY = {
    'tone': (
        ('exposure', 'light', '整体曝光'),
        ('contrast', 'light', '整体对比'),
        ('highlights_shadows', 'light', '高光与阴影'),
        ('whites_blacks', 'light', '白场与黑场'),
        ('master_curve', 'curves', '总体点曲线'),
        ('parametric_curve', 'curves', '参数曲线'),
        ('local_tone', 'masks', '局部影调'),
        ('closure', 'closure', '影调收束观察'),
    ),
    'color': (
        ('wb', 'wb', '全局白平衡'),
        ('calibration', 'basis', '必要时相机颜色基础校准'),
        ('global_color', 'mixer_point', '全局颜色'),
        *((('hsl_' + band, 'mixer_point', 'HSL ' + band)) for band in BANDS),
        ('grading', 'grading', '颜色分级'),
        ('local_wb', 'masks', '局部白平衡'),
        ('closure', 'closure', '颜色收束观察'),
    ),
    'joint': (
        ('residual_light', 'light', '残余整体影调'),
        ('residual_wb', 'wb', '残余白平衡'),
        ('residual_calibration', 'basis', '残余颜色基础'),
        ('residual_color', 'mixer_point', '残余颜色关系'),
        ('residual_curves', 'curves', '残余曲线关系'),
        ('residual_grading', 'grading', '残余分级关系'),
        ('residual_effects', 'effects', '残余质感'),
        ('residual_local', 'masks', '残余局部关系'),
        ('closure', 'closure', '综合收束观察'),
    ),
}
ALL_IDS = TONE_IDS | COLOR_IDS | EFFECT_IDS


def registry(phase):
    branch = {'G1': 'tone', 'G2': 'color', 'G3': 'joint', 'G4': 'joint',
              'complete': 'joint', 'integrated': 'joint'}.get(phase, phase)
    if branch not in TASK_REGISTRY:
        raise ValueError('Unknown task branch: ' + str(phase))
    return [{'branch': branch, 'module': module, 'step': step, 'order': index,
             'description': label} for index, (step, module, label) in enumerate(TASK_REGISTRY[branch])]


def classify_control(ident, *, local=False):
    if not isinstance(ident, str):
        return None
    if local:
        if ident in TONE_FIELDS:
            return 'tone', 'masks', 'local_tone'
        if ident in ('temp', 'tint'):
            return 'color', 'masks', 'local_wb'
        return None
    ident = SETTINGS_PATHS.get(ident, ident)
    if ident == 'curve.master':
        return 'tone', 'curves', 'master_curve'
    if ident in TONE_IDS:
        if ident.startswith('curve.'):
            return 'tone', 'curves', 'parametric_curve'
        field = ident.split('.')[1]
        step = ('highlights_shadows' if field in ('highlights', 'shadows') else
                'whites_blacks' if field in ('whites', 'blacks') else field)
        return 'tone', 'light', step
    if ident in COLOR_IDS:
        head = ident.split('.')[0]
        module, step = {'wb': ('wb', 'wb'), 'calibration': ('basis', 'calibration'),
                        'color': ('mixer_point', 'global_color'),
                        'grading': ('grading', 'grading'),
                        'mixer': ('mixer_point', 'hsl_' + ident.split('.')[1])}[head]
        return 'color', module, step
    if ident in EFFECT_IDS:
        return 'joint', 'effects', 'residual_effects'
    return None


def control_sort_key(ident, phase='joint'):
    found = classify_control(ident)
    if found is None:
        return (99, 99, ident)
    branch, module, step = found
    order = next(row['order'] for row in registry(branch) if row['step'] == step)
    return (BRANCHES.index(branch), order, ident)


def _joint_class(found):
    _, module, _ = found
    return 'joint', module, 'residual_' + {
        'light': 'light', 'wb': 'wb', 'basis': 'calibration', 'mixer_point': 'color',
        'curves': 'curves', 'grading': 'grading', 'effects': 'effects', 'masks': 'local'}[module]


def parse_controls(proposal, source_path='proposed_controls', branch=None):
    """Return classified fragments; unknown/ambiguous input is data, never an edit.

    Supported forms are actual action arguments, native ID/value maps, ID lists,
    settings trees and simple mask plans/commands. No text-to-control guessing.
    """
    result = []
    def emit(path, value, ident=None, local=False, task=None):
        found = task or (classify_control(ident, local=local) if ident else None)
        if found and branch == 'joint':
            found = _joint_class(found)
        result.append({'branch': found[0] if found else branch or 'joint',
                       'module': found[1] if found else 'unresolved',
                       'step': found[2] if found else 'unresolved',
                       'control_id': ident, 'source_path': path,
                       'value': copy.deepcopy(value),
                       **({} if found else {'unresolved_reason': 'Unknown or ambiguous control/structure'})})

    def visit(value, path, prefix=''):
        if prefix in ALL_IDS or prefix == 'curve.master' or prefix in SETTINGS_PATHS:
            emit(path, value, SETTINGS_PATHS.get(prefix, prefix))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                itempath = path + '[' + str(index) + ']'
                if isinstance(item, str):
                    emit(itempath, item, item)
                else:
                    visit(item, itempath)
        elif isinstance(value, dict):
            if 'control_id' in value or (isinstance(value.get('id'), str) and value['id'] in ALL_IDS):
                emit(path, value, value.get('control_id', value.get('id')))
                return
            if 'name' in value and 'arguments' in value:
                visit(value['arguments'], path + '.arguments')
                return
            if value.get('id') in ('develop.curve', 'curve.reset'):
                channel = value.get('params', {}).get('channel', 'master' if value['id'] == 'develop.curve' else None)
                emit(path, value, 'curve.master' if channel == 'master' else
                     'curve.highlights' if channel == 'parametric' else None)
                return
            if isinstance(value.get('id'), str) and value['id'].startswith('mask.'):
                params = value.get('params', {})
                if value['id'] == 'mask.adjust' and isinstance(params.get('values'), dict):
                    for key, child in params['values'].items():
                        emit(path + '.params.values.' + key, child, key, True)
                elif (value['id'] in ('mask.update', 'mask.select', 'mask.visible', 'mask.delete', 'mask.rename')
                      and branch in BRANCHES and isinstance(params, dict)
                      and type(params.get('id')) is int):
                    step = {'tone': 'local_tone', 'color': 'local_wb', 'joint': 'residual_local'}[branch]
                    emit(path, value, value['id'], task=(branch, 'masks', step))
                    result[-1].update(mask_id=params['id'], maintenance=True,
                                      requires_cross_stage_review=value['id'] in ('mask.update', 'mask.visible', 'mask.delete'))
                    # Classification is not readback: executor validates ID, shape and command fields.
                else:
                    emit(path, value)
                return
            if 'mask_plan' in value:
                plan = value['mask_plan']
                if isinstance(plan, dict) and isinstance(plan.get('values'), dict):
                    for key, child in plan['values'].items():
                        emit(path + '.mask_plan.values.' + key, child, key, True)
                else:
                    emit(path + '.mask_plan', plan)
                for key in value.keys() - {'mask_plan', 'rationale', 'predicted_effect', 'selection_reason',
                                           'override_reason', 'phase', 'base', 'base_version', 'selected_version', 'review'}:
                    visit(value[key], path + '.' + key)
                return
            if not value:
                emit(path, value)
            for key, child in value.items():
                if key in ('rationale', 'predicted_effect', 'selection_reason', 'override_reason', 'phase',
                           'base', 'base_version', 'selected_version', 'review') and not prefix:
                    continue
                if prefix == 'wb' and key in ('mode', 'scale'):
                    continue  # Source readback metadata, not additional task controls.
                if key in ('values', 'settings', 'commands') and not prefix:
                    if child:
                        visit(child, path + '.' + key)
                else:
                    nextprefix = prefix + '.' + key if prefix else key
                    visit(child, path + '.' + key, nextprefix)
        elif isinstance(value, str) and not prefix:
            emit(path, value, value)
        else:
            emit(path, value, prefix or None)
    visit(proposal, source_path)
    if not result:
        emit(source_path, proposal)
    return result


def group_sort_key(group):
    rows = registry(group['branch'])
    order = next((r['order'] for r in rows if r['step'] == group['step']), len(rows))
    override = group.get('reorder_override')
    if isinstance(override, dict) and isinstance(override.get('reason'), str) and override['reason'].strip():
        if type(override.get('order')) in (int, float):
            order = override['order']
    return (BRANCHES.index(group['branch']), order, -group.get('priority', 0), group['id'])


def derive_groups(issue):
    """Derive stable path-based groups; caller merges snapshots, never overwrites originals."""
    groups = []
    specs = issue.get('groups')
    if not isinstance(specs, list):
        specs = [{'proposed_controls': issue.get('proposed_controls', {}), 'source_path': 'proposed_controls'}]
    for index, raw in enumerate(specs):
        spec = copy.deepcopy(raw) if isinstance(raw, dict) else {'proposed_controls': raw}
        path = spec.get('source_path', 'groups[' + str(index) + '].proposed_controls')
        fragments = parse_controls(spec.get('proposed_controls', {}), path, spec.get('branch', issue['branch']))
        buckets = {}
        explicit = spec.get('group_id')
        for fragment in fragments:
            key = (fragment['branch'], fragment['module'], fragment['step'])
            buckets.setdefault(key, []).append(fragment)
        # Explicit reasoned hypotheses may couple registered steps without a count gate.
        declared = spec.get('branch', issue['branch'])
        reason = spec.get('selection_reason', spec.get('reason', issue.get('selection_reason', issue.get('reason'))))
        if (explicit and len(buckets) > 1 and reason and
                {f['branch'] for f in fragments} == {declared} and
                not any(f.get('unresolved_reason') for f in fragments)):
            first = min(buckets, key=lambda key: next((r['order'] for r in registry(key[0]) if r['step'] == key[2]), 99))
            modules = list(dict.fromkeys(f['module'] for f in fragments))
            buckets = {(declared, spec.get('module', modules[0] if len(modules) == 1 else 'coupled'),
                        spec.get('step', first[2])): fragments}
        for (branch, module, step), parts in buckets.items():
            suffix = branch + '/' + module + '/' + step
            ident = issue['id'] + '::' + (explicit if explicit and len(buckets) == 1 else
                                         (explicit + '/' if explicit else '') + suffix)
            row = {'id': ident, 'group_id': ident, 'issue_id': issue['id'], 'branch': branch,
                   'module': module, 'step': step, 'proposed_controls': copy.deepcopy(parts),
                   'shared_context_ref': {'issue_id': issue['id'], 'source_path': path,
                                          'source_revision': issue['revision']},
                   'source_proposal': copy.deepcopy(spec.get('proposed_controls', {})),
                   'source_paths': [p['source_path'] for p in parts], 'source_path': path,
                   'source_revision': issue['revision'], 'status': 'ready',
                   'priority': spec.get('priority', issue.get('priority', 0)),
                   'dependencies': copy.deepcopy(spec.get('dependencies', issue.get('dependencies', []))),
                   'base_version': issue['base_version'], 'observation_id': issue['observation_id'],
                   'observation': issue['observation_id'], 'operations': [], 'records': []}
            if any(p.get('maintenance') for p in parts):
                row.update(maintenance=True,
                           requires_cross_stage_review=any(p.get('requires_cross_stage_review') for p in parts))
            for field in ('reorder_override', 'reason', 'selection_reason'):
                if field in spec or field in issue:
                    row[field] = copy.deepcopy(spec.get(field, issue.get(field)))
            if any(p.get('unresolved_reason') for p in parts):
                row.update(status='unresolved', unresolved=True,
                           wait_reason='Unknown or ambiguous proposal; explicitly revise before execution')
            elif spec.get('status') in GROUP_STATUSES:
                row['status'] = spec['status']
            groups.append(row)
    # Same classification from multiple source leaves is one hypothesis by default.
    merged = {}
    for group in groups:
        if group['id'] in merged:
            merged[group['id']]['proposed_controls'].extend(group['proposed_controls'])
            merged[group['id']]['source_paths'].extend(group['source_paths'])
        else:
            merged[group['id']] = group
    return sorted(merged.values(), key=group_sort_key)
