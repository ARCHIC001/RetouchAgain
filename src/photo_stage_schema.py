"""Offline model-facing stage edit properties; execution/readback guards still apply.

Only operation properties are returned, so callers keep their narrative/review
schemas unchanged. Source-specific WB ranges, observed IDs, coupled curve splits,
point ordering and command availability require validate_stage_operation too.
"""
import copy

import photo_stage_operations as stage
from photo_stage_tasks import control_sort_key, registry
from photo_capability_catalog import _command_schema, _LOCAL, _object


def _command_contracts(phase):
    """Narrow only the native commands in the current stage's execution scope."""
    ids = ['mask.adjust', 'mask.update', 'mask.select', 'mask.delete',
           'mask.visible', 'mask.rename']
    if phase != 'color':
        ids[:0] = ['develop.curve', 'curve.reset']
    if phase != 'tone':
        ids.append('develop.wbPick')
    branches = []
    for ident in ids:
        params = copy.deepcopy(_command_schema(ident))
        props = params['properties']
        required = list(params['required'])
        if ident.startswith('mask.'):
            props['id'] = {'type': 'integer', 'minimum': 0, 'maximum': 4294967295,
                'description': 'Explicit actual read-back mask ID; never infer an active or next ID.'}
            required.append('id')
        if ident == 'develop.curve':
            props['channel'] = {'type': 'string', 'const': 'master'}
            props['points'] = stage._curve_schema(False, phase)
            if phase == 'tone':
                params['description'] = props['points']['description']
        elif ident == 'curve.reset':
            props['channel'] = {'type': 'string', 'enum': ['master', 'parametric']}
            required.append('channel')
        elif ident == 'mask.adjust':
            props['values'] = _object({field: copy.deepcopy(_LOCAL[field])
                for field in stage._local_fields(phase)}, minProperties=1)
        elif ident == 'mask.update':
            props['component'] = {'type': 'integer', 'const': 0}
            shapes = []
            for shape in props['shape']['oneOf']:
                if shape['properties']['kind']['const'] not in ('linear', 'radial'):
                    continue
                if 'invert' in shape['properties']:
                    shape['properties']['invert'] = {'type': 'boolean', 'const': False}
                shapes.append(shape)
            props['shape'] = {'oneOf': shapes}
        elif ident == 'mask.visible':
            required.append('visible')
        params['required'] = list(dict.fromkeys(required))
        branches.append(_object({'id': {'type': 'string', 'const': ident},
            'params': params}, ('id', 'params')))
    return {'type': 'array', 'items': {'oneOf': branches}}


def pre_tone_wb_schema():
    """Operation properties for a host-only execution scope, not a workflow phase."""
    values = _object({ident: {'type': 'number',
        'minimum': stage.CONTROL_RANGES[ident][0], 'maximum': stage.CONTROL_RANGES[ident][1]}
        for ident in ('wb.temp', 'wb.tint')}, minProperties=1)
    values['description'] = ('Explicit finite absolute native WB values, at least one axis. '
        'Current list_controls ranges/availability are authoritative. Static native units '
        'are not a claim of Kelvin qualification for every source format. No settings, '
        'commands, mask_plan or submitted phase; narrative/review metadata stays with the caller.')
    return {'values': values}


def stage_edit_schema(phase='joint'):
    """Return operation property schemas for one phase or host execution scope.

    No hidden defaults or narrative limits are added. Native command and mask
    schemas retain physical point cardinality and positive long-edge radii.
    """
    if phase == 'pre_tone_wb':
        return pre_tone_wb_schema()
    stage._phase(phase)
    allowed = stage._allowed_ids(phase)
    values = _object({ident: {'type': 'number',
        'minimum': stage.CONTROL_RANGES[ident][0],
        'maximum': stage.CONTROL_RANGES[ident][1]}
        for ident in sorted(allowed, key=lambda ident: control_sort_key(ident, phase))}, minProperties=1)
    values['description'] = ('Finite native scalar values only. Current list_controls '
        'ranges/availability remain authoritative, including source-specific WB units. '
        'Suggested task order (not an execution gate; explain skips/revisits): ' +
        ' -> '.join(row['step'] for row in registry(phase)))
    settings = _object({})
    for path, ident in sorted(stage.SETTINGS_PATHS.items(), key=lambda item: control_sort_key(item[1], phase)):
        if ident not in allowed:
            continue
        node = settings
        parts = path.split('.')
        for part in parts[:-1]:
            node = node['properties'].setdefault(part, _object({}))
        node['properties'][parts[-1]] = copy.deepcopy(values['properties'][ident])
    if phase != 'color':
        settings['properties']['curve']['properties']['master'] = stage._curve_schema(True, phase)
    if phase != 'tone':
        settings['properties']['wb']['properties'].update(
            mode={'type': 'string', 'description': 'Only custom or the exact observed current mode; not preset selection. Requires an explicit temp/tint patch and runtime readback validation.'},
            scale={'type': 'string', 'enum': ['kelvin', 'legacy'],
                'description': 'Must equal the actual read-back source scale. Requires an explicit temp/tint patch; never default or change units.'})
    return {'values': values, 'settings': settings,
        'commands': _command_contracts(phase), 'mask_plan': stage.mask_plan_schema(phase)}
