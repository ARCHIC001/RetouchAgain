"""Strict module-native execution primitives, not a photographic recipe.

No provider/render calls. Existing Native implements allowed sliders/settings;
commands use the same capability catalog contracts and actual result handoff.
"""
import copy
from photo_capability_catalog import module_for, validate_commands, _check, _result_contract
import photo_native_commands as atlas


SETTINGS_MODULES = {'light': 'light', 'wb': 'wb', 'profile': 'basis', 'calibration': 'basis',
                    'color': 'mixer_point', 'mixer': 'mixer_point', 'point_colors': 'mixer_point',
                    'curve': 'curves', 'grading': 'grading', 'effects': 'effects', 'masks': 'masks'}


def contract_operation_modules(arguments):
    if not isinstance(arguments, dict):
        raise ValueError('Explicit operation object required')
    modules = set()
    for ident in arguments.get('values', {}):
        modules.add(module_for(ident))
    for section in arguments.get('settings', {}):
        if section not in SETTINGS_MODULES:
            raise ValueError('Unsupported settings module: ' + section)
        modules.add(SETTINGS_MODULES[section])
    for command in arguments.get('commands', []):
        modules.add(module_for(command['id']))
    if None in modules or '' in modules:
        raise ValueError('Cannot determine exact native module')
    return sorted(modules)


def apply_strict(backend, base, arguments, dependencies, trace, checkpoint=None):
    if not isinstance(arguments, dict) or not isinstance(trace, list):
        raise ValueError('Explicit arguments and caller-owned trace required')
    native_commands = arguments.get('commands') or []
    if not native_commands:
        trace.append({'kind': 'native_apply', 'operation': {k: copy.deepcopy(arguments[k]) for k in ('values', 'settings') if k in arguments},
                      'status': 'pending'})
        if checkpoint: checkpoint()
        actual = backend.apply(base, arguments)
        trace[-1].update(status='complete', settings_readback=copy.deepcopy(actual))
        if checkpoint: checkpoint()
        return actual
    if arguments.get('values') or arguments.get('settings'):
        raise ValueError('Commands cannot mix with slider/settings edits in one native batch')
    backend.restore(base)
    if backend.settings() != base:
        raise ValueError('Exact native baseline restore mismatch')
    before, control, current = atlas.state(backend)
    validate_commands(native_commands, current, dependencies)
    actual = before
    for command in native_commands:
        # Revalidate against the actual state returned by the previous command.
        before, control, current = atlas.state(backend)
        validate_commands([command], current, dependencies)
        record = {'command': copy.deepcopy(command), 'status': 'pending',
                  'before_settings': copy.deepcopy(before), 'before_control_state': copy.deepcopy(control)}
        trace.append(record)
        if checkpoint: checkpoint()
        result = atlas.decode_result(backend.call('command_run', command))
        if isinstance(result, dict) and (result.get('pending') is True or result.get('started') is True):
            raise ValueError('Unknown asynchronous native outcome; retain pending, never replay')
        _check(result, _result_contract(command['id'])['native_schema'], command['id'] + '.actual_result')
        actual, control_after, _ = atlas.state(backend)
        for key in ('crop', 'geometry', 'orientation', 'optics'):
            if before.get(key) != actual.get(key):
                raise ValueError('Native command changed protected geometry/optics')
        if command['id'] in ('mask.add', 'mask.duplicate'):
            mid = result.get('activeMask') if isinstance(result, dict) else None
            if (type(mid) is not int or any(m['id'] == mid for m in before['masks'])
                    or not any(m['id'] == mid for m in actual['masks']) or control_after['activeMask'] != mid):
                raise ValueError('New actual activeMask must be returned and present in settings')
        if command['id'] == 'pointColor.pick':
            index = result.get('index') if isinstance(result, dict) else None
            if (type(index) is not int or not 0 <= index < len(actual['point_colors'])
                    or len(actual['point_colors']) != len(before['point_colors']) + 1):
                raise ValueError('Actual point index was not verified against fresh settings')
        record.update(status='complete', result=copy.deepcopy(result), settings_readback=copy.deepcopy(actual),
                      control_state_readback=copy.deepcopy(control_after))
        if checkpoint: checkpoint()
    return actual
