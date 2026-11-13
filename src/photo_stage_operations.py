"""Narrow stage edits and an actual-ID, neutral-new-mask transaction.

No backend is opened here, and no render, transport or dependency installation is
performed. The caller owns the explicit base, authoritative phase and durable
trace/checkpoint. Current settings are context, not a second submitted recipe.
"""
import copy
import math

from dsh_photo_strict_execute import apply_strict
from photo_capability_catalog import (
    _check, _finite, _LOCAL, _object, _point, _RADIUS, _number,
    _result_contract, _rows, _shape_validate, module_for, validate_commands,
)
import photo_native_commands as atlas


TONE_FIELDS = ('exposure', 'contrast', 'highlights', 'shadows', 'whites', 'blacks')
LOCAL_FIELDS = (*TONE_FIELDS, 'temp', 'tint')
BANDS = ('red', 'orange', 'yellow', 'green', 'aqua', 'blue', 'purple', 'magenta')
CURVE_FIELDS = ('highlights', 'lights', 'darks', 'shadows', 'splitShadows', 'splitMid', 'splitHighlights')
CALIBRATION_FIELDS = tuple(c + f for c in ('red', 'green', 'blue') for f in ('Hue', 'Sat'))
TONE_IDS = frozenset(['light.' + f for f in TONE_FIELDS] + ['curve.' + f for f in CURVE_FIELDS])
COLOR_IDS = frozenset(['wb.temp', 'wb.tint', 'color.vibrance', 'color.saturation'] +
    ['mixer.' + b + '.' + f for b in BANDS for f in ('hue', 'sat', 'lum')] +
    ['grading.' + b + '.' + f for b in ('shadows', 'midtones', 'highlights') for f in ('hue', 'sat', 'lum')] +
    ['grading.blending', 'grading.balance'] + ['calibration.' + f for f in CALIBRATION_FIELDS])
EFFECT_IDS = frozenset('effects.' + f for f in ('texture', 'clarity', 'dehaze'))
GLOBAL_IDS = TONE_IDS | COLOR_IDS | EFFECT_IDS
# Exact source-reviewed ControlSpec ranges (develop/src/controls.rs). An explicit
# current list_controls snapshot overrides these, including relative WB units.
CONTROL_RANGES = {ident: (-100, 100) for ident in GLOBAL_IDS}
CONTROL_RANGES.update({'light.exposure': (-5, 5), 'wb.temp': (2000, 50000), 'wb.tint': (-150, 150),
    'curve.splitShadows': (10, 70), 'curve.splitMid': (20, 80), 'curve.splitHighlights': (30, 90),
    'grading.blending': (0, 100)})
for _band in ('shadows', 'midtones', 'highlights'):
    CONTROL_RANGES['grading.' + _band + '.hue'] = (0, 360)
    CONTROL_RANGES['grading.' + _band + '.sat'] = (0, 100)

SETTINGS_PATHS = {ident: ident for ident in GLOBAL_IDS}
for _ident in tuple(SETTINGS_PATHS):
    _section, *_tail = _ident.split('.')
    if _section in ('curve', 'calibration'):
        _name = ''.join('_' + c.lower() if c.isupper() else c for c in _tail[0])
        SETTINGS_PATHS[_section + '.' + _name] = _ident
        if _name != _tail[0]:
            SETTINGS_PATHS.pop(_ident, None)  # serde fields are snake_case, unlike control IDs.
SETTING_PREFIXES = {'.'.join(path.split('.')[:i]) for path in SETTINGS_PATHS for i in range(1, len(path.split('.')))}
SETTING_PREFIXES.add('curve')
DETAIL_FIELDS = ('sharpness', 'noise', 'moire', 'defringe')
# LocalAdjustments::default, including all20 fields. The four P0-excluded detail
# fields may only be written as zero by this private new-mask initialization.
NEUTRAL_LOCAL = {**{f: 0 for f in (*_LOCAL, *DETAIL_FIELDS)}, 'amount': 100}
PROTECTED = ('crop', 'geometry', 'orientation', 'optics')


def _phase(phase):
    if phase not in ('tone', 'color', 'joint', 'integrated'):
        raise ValueError('Unknown editing phase: ' + str(phase))
    return phase


def _allowed_ids(phase):
    return TONE_IDS if phase == 'tone' else COLOR_IDS if phase == 'color' else GLOBAL_IDS


def _local_fields(phase):
    return TONE_FIELDS if phase == 'tone' else ('temp', 'tint') if phase == 'color' else LOCAL_FIELDS


def mask_plan_schema(phase='joint'):
    """Explicit normalized positions; radial radii are positive long-edge units."""
    _phase(phase)
    values = _object({f: copy.deepcopy(_LOCAL[f]) for f in _local_fields(phase)}, minProperties=1)
    geometry = {
        'linear': _object({'start': _point(), 'end': _point()}, ('start', 'end')),
        'radial': _object({'center': _point(), 'rx': copy.deepcopy(_RADIUS), 'ry': copy.deepcopy(_RADIUS),
            'angle': _number(), 'feather': _number(0, 100)}, ('center', 'rx', 'ry', 'angle', 'feather')),
    }
    create = [_object({'action': {'const': 'create', 'type': 'string'},
        'kind': {'const': kind, 'type': 'string'}, 'geometry': props, 'values': values},
        ('action', 'kind', 'geometry', 'values')) for kind, props in geometry.items()]
    adjust = _object({'action': {'const': 'adjust', 'type': 'string'},
        'id': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295}, 'values': values}, ('action', 'id', 'values'))
    return {'oneOf': [*create, adjust]}


def _control_index(controls, dependencies):
    if controls is None and dependencies is not None:
        controls = dependencies.get('controls')
    if controls is None:
        return None
    if isinstance(controls, dict):
        controls = controls.get('controls')
    return _rows(controls, 'current controls')


def _validate_value(ident, value, phase, controls):
    if ident not in _allowed_ids(phase):
        raise ValueError('Control outside ' + phase + ' stage scope: ' + str(ident))
    lo, hi = CONTROL_RANGES[ident]
    if controls is not None:
        row = controls.get(ident)
        if row is None or row.get('enabled', True) is not True or row.get('availability', 'ready') != 'ready':
            raise ValueError('Control unavailable in current native controls: ' + ident)
        lo, hi = row.get('min'), row.get('max')
        if not _finite(lo) or not _finite(hi) or lo > hi:
            raise ValueError('Missing/invalid current native control range: ' + ident)
    _check(value, _number(lo, hi), ident)


def _curve_points(points, serialized):
    _check(points, {'type': 'array', 'items': _point(serialized)}, 'curve.master')
    xs = [p['x'] if serialized else p[0] for p in points]
    if any(a >= b for a, b in zip(xs, xs[1:])):
        raise ValueError('Curve input x must be strictly increasing')


def _settings_edits(settings):
    """Enumerate submitted leaves only; empty dictionaries do not invent edits."""
    result = []
    def visit(path, value):
        if path == 'curve.master':
            _curve_points(value, True)
            result.append((path, value))
        elif path in SETTINGS_PATHS:
            result.append((SETTINGS_PATHS[path], value))
        elif path in SETTING_PREFIXES or not path:
            if not isinstance(value, dict):
                raise ValueError('Expected settings object at ' + path)
            for key, child in value.items():
                if not isinstance(key, str) or '.' in key:
                    raise ValueError('Settings keys must be native nested field names')
                visit(path + '.' + key if path else key, child)
        else:
            raise ValueError('Settings outside current stage scope: ' + path)
    visit('', settings)
    return result


def _observed_mask(current, ident):
    masks = current.get('masks')
    if not isinstance(masks, list) or any(not isinstance(m, dict) or type(m.get('id')) is not int for m in masks):
        raise ValueError('Actual masks readback required')
    ids = [m['id'] for m in masks]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate actual mask IDs')
    mask = next((m for m in masks if m['id'] == ident), None)
    if mask is None:
        raise ValueError('No such observed mask ID; never infer a target')
    components = mask.get('components')
    if (not isinstance(components, list) or len(components) != 1 or not isinstance(components[0], dict) or
            not isinstance(components[0].get('shape'), dict) or
            components[0]['shape'].get('kind') not in ('linear', 'radial') or
            components[0].get('op', 'add') != 'add' or components[0].get('invert', False) is not False or
            mask.get('invert', False) is not False or
            components[0]['shape'].get('invert', False) is not False):
        raise ValueError('Only existing simple normal linear/radial masks are editable')
    return mask


def _command_ready(ident, dependencies):
    if not isinstance(dependencies, dict):
        raise ValueError('Read-only native command dependencies required')
    row = _rows(dependencies.get('commands'), 'dependencies.commands').get(ident)
    if row is None or row.get('enabled') is not True:
        raise ValueError('Native command unavailable/disabled: ' + ident)


def validate_stage_operation(arguments, current_settings, phase='joint', dependencies=None, controls=None):
    """Return sorted native modules after checking every submitted operation path.

    Public review/rationale metadata is deliberately not bounded or interpreted.
    Supply current controls (or dependencies['controls']) for source-specific WB
    ranges and current native availability. No current hidden state is reset.
    """
    _phase(phase)
    if not isinstance(arguments, dict) or not isinstance(current_settings, dict):
        raise ValueError('Explicit operation arguments and current settings required')
    if 'phase' in arguments and arguments['phase'] != phase:
        raise ValueError('Submitted phase differs from authoritative validation phase')
    if current_settings.get('pending') is True:
        raise ValueError('Pending native outcome; reconcile, never replay')
    index = _control_index(controls, dependencies)
    values, settings, commands = arguments.get('values', {}), arguments.get('settings', {}), arguments.get('commands', [])
    if not isinstance(values, dict) or not isinstance(settings, dict) or not isinstance(commands, list):
        raise ValueError('values/settings must be objects and commands a list')
    if 'mask_plan' in arguments:
        if values or settings or commands:
            raise ValueError('mask_plan cannot mix with values/settings/commands')
        plan = arguments['mask_plan']
        _check(plan, mask_plan_schema(phase), 'mask_plan')
        _command_ready('mask.adjust', dependencies)
        if plan['action'] == 'create':
            _command_ready('mask.add', dependencies)
            _shape_validate({'kind': plan['kind'], **plan['geometry']}, dependencies)
        else:
            _observed_mask(current_settings, plan['id'])
        return ['masks']
    if commands and (values or settings):
        raise ValueError('Commands cannot mix with values/settings edits')
    modules = set()
    submitted = {}
    if any(ident not in GLOBAL_IDS for ident in values):
        raise ValueError('Raw values must use enabled native scalar control IDs')
    wb = settings.get('wb', {})
    wb_metadata = {}
    if isinstance(wb, dict):
        wb_metadata = {key: value for key, value in wb.items() if key in ('mode', 'scale')}
        if wb_metadata:
            if phase == 'tone' or not (set(wb).intersection(('temp', 'tint')) or set(values).intersection(('wb.temp', 'wb.tint'))):
                raise ValueError('WB mode/scale require an explicit color-stage temp/tint patch')
            current_wb = current_settings.get('wb', {})
            for key, value in wb_metadata.items():
                if key == 'mode':
                    if not isinstance(value, str) or (value != 'custom' and value != current_wb.get('mode')):
                        raise ValueError('WB preset selection outside current two-control scope')
                elif value != current_wb.get('scale') or value not in ('kelvin', 'legacy'):
                    raise ValueError('WB scale must match the actual source context')
            if wb_metadata.get('scale') == 'kelvin' and current_wb.get('scale') != 'kelvin':
                raise ValueError('Kelvin WB requires actual qualified Kelvin source context')
    scalar_settings = copy.deepcopy(settings)
    if wb_metadata:
        scalar_settings['wb'] = {key: value for key, value in wb.items() if key not in wb_metadata}
    for ident, value in [*values.items(), *_settings_edits(scalar_settings)]:
        if ident == 'curve.master':
            if phase == 'color':
                raise ValueError('Master curve outside color stage scope')
            modules.add('curves')
        else:
            _validate_value(ident, value, phase, index)
            modules.add(module_for(ident))
        if ident in submitted and submitted[ident] != value:
            raise ValueError('Conflicting submitted control paths: ' + ident)
        submitted[ident] = value
    command_state = copy.deepcopy(current_settings)
    for command in commands:
        if not isinstance(command, dict) or set(command) - {'id', 'params'}:
            raise ValueError('Explicit atomic command object required')
        ident, params = command.get('id'), command.get('params', {})
        if not isinstance(ident, str) or not isinstance(params, dict):
            raise ValueError('Explicit string command ID and params object required')
        _command_ready(ident, dependencies)
        if ident == 'develop.curve':
            if phase == 'color' or params.get('channel', 'master') != 'master':
                raise ValueError('Only master point curve is enabled outside color phase')
            _curve_points(params.get('points'), False)
        elif ident == 'curve.reset':
            if phase == 'color' or params.get('channel') not in ('master', 'parametric'):
                raise ValueError('Reset must explicitly target master/parametric, never RGB/all')
        elif ident in ('mask.adjust', 'mask.update', 'mask.visible', 'mask.delete', 'mask.select', 'mask.rename'):
            if type(params.get('id')) is not int:
                raise ValueError('Explicit actual mask ID required')
            observed = _observed_mask(command_state, params['id'])
            if ident == 'mask.adjust':
                _check(params.get('values'), _object({f: _LOCAL[f] for f in _local_fields(phase)}, minProperties=1), 'mask.adjust.values')
            elif ident == 'mask.update':
                shape = params.get('shape')
                if (not isinstance(shape, dict) or shape.get('kind') not in ('linear', 'radial') or
                        shape.get('invert', False) is not False or params.get('component', 0) != 0):
                    raise ValueError('Only explicit normal linear/radial geometry on the observed component is enabled')
                geometry = {key: value for key, value in shape.items() if key not in ('kind', 'invert')}
                for key in ('start', 'end', 'center'):
                    if key in geometry and isinstance(geometry[key], dict):
                        geometry[key] = [geometry[key].get('x'), geometry[key].get('y')]
                _check({'action': 'create', 'kind': shape['kind'], 'geometry': geometry,
                        'values': {_local_fields(phase)[0]: 0}}, mask_plan_schema(phase), 'mask.update.geometry')
                _shape_validate(shape, dependencies, serialized=True)
            elif ident == 'mask.visible' and type(params.get('visible')) is not bool:
                raise ValueError('Explicit visible boolean required')
            if ident == 'mask.delete':
                command_state['masks'].remove(observed)
            elif ident == 'mask.update':
                observed['components'][0]['shape'] = copy.deepcopy(params['shape'])
        else:
            raise ValueError('Command outside current narrow stage scope: ' + str(ident))
        modules.add(module_for(ident))
    if commands:
        validate_commands(commands, current_settings, dependencies)
    if not modules:
        raise ValueError('No submitted editable fields or commands')
    # Only check coupled splits when submitted; inherited state alone is not an
    # additional recipe and must not block an unrelated light/color patch.
    split_ids = ('curve.splitShadows', 'curve.splitMid', 'curve.splitHighlights')
    if any(ident in submitted for ident in split_ids):
        curve = current_settings.get('curve', {})
        names = ('split_shadows', 'split_mid', 'split_highlights')
        splits = [submitted.get(ident, curve.get(name, curve.get(ident.split('.')[1]))) for ident, name in zip(split_ids, names)]
        if any(not _finite(v) for v in splits) or not splits[0] < splits[1] < splits[2]:
            raise ValueError('Actual curve splits must remain strictly ordered')
    return sorted(modules)


def _same(want, actual):
    if isinstance(want, dict):
        return isinstance(actual, dict) and set(want) == set(actual) and all(_same(v, actual[k]) for k, v in want.items())
    if isinstance(want, list):
        return isinstance(actual, list) and len(want) == len(actual) and all(_same(a, b) for a, b in zip(want, actual))
    if _finite(want):
        return _finite(actual) and math.isclose(want, actual, abs_tol=1e-7, rel_tol=1e-7)
    return type(want) is type(actual) and want == actual


def _protected(before, after):
    if any(before.get(k) != after.get(k) for k in PROTECTED):
        raise ValueError('Native operation changed protected geometry/optics')


def _only_mask_changed(before, after, ident, created=False):
    _protected(before, after)
    left = {k: v for k, v in before.items() if k != 'masks'}
    right = {k: v for k, v in after.items() if k != 'masks'}
    if not _same(left, right):
        raise ValueError('Mask operation changed unrelated global settings')
    old = before.get('masks', [])
    new = after.get('masks', [])
    if created:
        if len(new) != len(old) + 1 or not _same(old, [m for m in new if m['id'] != ident]):
            raise ValueError('Creation changed previous masks or created unexpected masks')
    elif ([m['id'] for m in old] != [m['id'] for m in new] or
          not _same([m for m in old if m['id'] != ident], [m for m in new if m['id'] != ident])):
        raise ValueError('Adjustment changed previous masks or IDs')


def _created_shape(plan):
    geometry = copy.deepcopy(plan['geometry'])
    for key in ('start', 'end', 'center'):
        if key in geometry:
            geometry[key] = dict(zip(('x', 'y'), geometry[key]))
    if plan['kind'] == 'radial':
        geometry['invert'] = False
    return {'kind': plan['kind'], **geometry}


def _verify_create(plan, before, after, control, result):
    ident = result.get('activeMask') if isinstance(result, dict) else None
    if (type(ident) is not int or not 0 <= ident <= 4294967295 or
            any(m['id'] == ident for m in before.get('masks', [])) or control.get('activeMask') != ident):
        raise ValueError('Creation must return a new actual activeMask matched by readback')
    mask = _observed_mask(after, ident)
    _only_mask_changed(before, after, ident, True)
    component = mask['components'][0]
    if (mask.get('visible') is not True or mask.get('invert') is not False or mask.get('refine', 0) != 0 or
            component.get('op') != 'add' or component.get('invert') is not False or
            not _same(_created_shape(plan), component['shape'])):
        raise ValueError('New mask geometry/visibility/normal-shape defaults mismatch')
    return ident


def _verify_patch(before, after, ident, values):
    _only_mask_changed(before, after, ident)
    old = _observed_mask(before, ident)
    new = _observed_mask(after, ident)
    expected = copy.deepcopy(old)
    if not isinstance(expected.get('adjust'), dict):
        raise ValueError('Actual local adjustment readback required')
    expected['adjust'].update(values)
    if not _same(expected, new):
        raise ValueError('Mask adjustment readback differs from exact field patch')


def _run_mask_command(backend, command, dependencies, trace, checkpoint, verify, trusted_init=False):
    before, control, current = atlas.state(backend)
    if trusted_init:
        if command['id'] != 'mask.adjust' or command['params']['values'] != NEUTRAL_LOCAL:
            raise ValueError('Only fixed all20-field neutral initialization is trusted')
        _command_ready('mask.adjust', dependencies)
        _observed_mask(current, command['params']['id'])
    else:
        validate_commands([command], current, dependencies)
    record = {'kind': 'mask_neutral_init' if trusted_init else 'mask_plan_command',
        'command': copy.deepcopy(command), 'status': 'pending', 'before_settings': before,
        'before_control_state': control}
    trace.append(record)
    if checkpoint:
        checkpoint()
    result = atlas.decode_result(backend.call('command_run', command))
    if isinstance(result, dict) and (result.get('pending') is True or result.get('started') is True):
        raise ValueError('Unknown asynchronous native outcome; retain pending, never replay')
    _check(result, _result_contract(command['id'])['native_schema'], command['id'] + '.actual_result')
    actual, control_after, _ = atlas.state(backend)
    verify(before, actual, control_after, result)
    record.update(status='complete', result=copy.deepcopy(result), settings_readback=copy.deepcopy(actual),
        control_state_readback=copy.deepcopy(control_after))
    if checkpoint:
        checkpoint()
    return actual, result


def apply_stage_operation(backend, base, args, dependencies, trace, checkpoint=None):
    """Apply once, returning actual settings; the outer worker alone renders.

    A non-complete caller trace blocks restore/replay. Host supplies stage_phase
    on its backend (standalone default joint); submitted phases cannot override it.
    """
    if not isinstance(base, dict) or not isinstance(trace, list) or not isinstance(args, dict):
        raise ValueError('Explicit base, args and caller-owned trace required')
    if any(row.get('status') != 'complete' for row in trace):
        raise ValueError('Pending/unknown trace; retain for reconciliation, never replay')
    phase = getattr(backend, 'stage_phase', 'joint')
    if 'phase' in args and args['phase'] != phase:
        raise ValueError('Submitted phase differs from authoritative backend phase')
    native_controls = backend.controls() if args.get('values') or args.get('settings') else None
    validate_stage_operation(args, base, phase, dependencies, native_controls)
    if 'mask_plan' not in args:
        # Strict execution preserves ordinary command/result contracts and uses
        # Native.apply for the existing exact-restore/settings/readback path.
        record = {'kind': 'stage_operation', 'phase': phase, 'status': 'pending'}
        trace.append(record)
        if checkpoint:
            checkpoint()
        actual = apply_strict(backend, base, args, dependencies, trace, checkpoint)
        if args.get('commands'):
            # Selection is outside DevelopSettings; verify the actual control
            # state as well as the field patch (Native settings alone cannot).
            for entry in trace:
                command = entry.get('command', {})
                if command.get('id') == 'mask.select':
                    if entry.get('control_state_readback', {}).get('activeMask') != command['params']['id']:
                        raise ValueError('Explicit mask selection was not read back')
            expected = copy.deepcopy(base)
            for command in args['commands']:
                ident, params = command['id'], command.get('params', {})
                if ident.startswith('mask.'):
                    target = _observed_mask(expected, params['id'])
                    if ident == 'mask.adjust':
                        target['adjust'].update(params['values'])
                    elif ident == 'mask.update':
                        target['components'][params.get('component', 0)]['shape'] = copy.deepcopy(params['shape'])
                    elif ident == 'mask.delete':
                        expected['masks'].remove(target)
                    elif ident == 'mask.visible':
                        target['visible'] = params['visible']
                    elif ident == 'mask.rename':
                        target['name'] = params['name']
                    # Selection changes control state, not DevelopSettings.
                elif ident == 'develop.curve':
                    expected['curve']['master'] = [dict(zip(('x', 'y'), point)) for point in params['points']]
                elif params['channel'] == 'master':
                    expected['curve']['master'] = []
                else:
                    expected['curve'].update(highlights=0, lights=0, darks=0, shadows=0,
                        split_shadows=25, split_mid=50, split_highlights=75)
            if not _same(expected, actual):
                raise ValueError('Atomic command fields or preserved state not read back')
        record.update(status='complete', settings_readback=copy.deepcopy(actual))
        if checkpoint:
            checkpoint()
        return actual
    plan = copy.deepcopy(args['mask_plan'])
    transaction = {'kind': 'mask_plan', 'plan': copy.deepcopy(plan), 'phase': phase, 'status': 'pending'}
    trace.append(transaction)
    if checkpoint:
        checkpoint()
    backend.restore(base)
    restored, _, current = atlas.state(backend)
    if restored != base:
        raise ValueError('Exact native baseline restore mismatch')
    _protected(base, restored)
    validate_stage_operation(args, current, phase, dependencies)
    if plan['action'] == 'create':
        command = atlas.command('mask.add', kind=plan['kind'], **plan['geometry'])
        actual, result = _run_mask_command(backend, command, dependencies, trace, checkpoint,
            lambda before, after, control, result: _verify_create(plan, before, after, control, result))
        ident = result['activeMask']
        transaction['actual_mask_id'] = ident
        neutral = copy.deepcopy(NEUTRAL_LOCAL)
        actual, _ = _run_mask_command(backend, atlas.command('mask.adjust', id=ident, values=neutral),
            dependencies, trace, checkpoint,
            lambda before, after, control, result: _verify_patch(before, after, ident, neutral), trusted_init=True)
    else:
        ident = plan['id']
    values = copy.deepcopy(plan['values'])
    actual, _ = _run_mask_command(backend, atlas.command('mask.adjust', id=ident, values=values),
        dependencies, trace, checkpoint,
        lambda before, after, control, result: _verify_patch(before, after, ident, values))
    transaction.update(status='complete', actual_mask_id=ident, settings_readback=copy.deepcopy(actual))
    if checkpoint:
        checkpoint()
    return actual
