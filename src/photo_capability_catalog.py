"""Offline, source-reviewed P0 catalog; no transport, filesystem IO or execution.

``controls``/``commands`` are native list_controls/commands rows, not promises of
rendering quality. Dependencies are caller-supplied read-only snapshots, keyed by
``commands`` (native command rows with explicit enabled flags) and
``segment.model.status`` (native status fields). Optional ``shapes`` maps a kind
to {status: ready|blocked, reason: ...} for additional qualification restrictions.
No snapshot is fetched here. Missing SAM status fails closed; loaded=False is not
by itself a failure (the native engine loads an installed model lazily). Remote
SAM endpoints are blocked in this local-only build.

current_settings is get_develop JSON, optionally with activeMask from the runner.
Creating commands must end a batch: the runner executes, reads back settings and
the command result, then supplies the actual ID/index/activeMask to the next call.
Validation simulates only deterministic changes to already observed objects. It
never predicts next_mask_id, a sample index, segmentation, or a pending result.
The validator is deliberately stricter than native silent defaults/clamping.
"""
import copy
import math
import re

BUILD = 'capability-textbook-v1-20261111'
BASE_BUILD = 'dsh-native-memory-v18-20261111'
MODULES = ('light', 'wb', 'basis', 'mixer_point', 'curves', 'grading', 'effects', 'masks')
MODULE_ALIASES = {
    'light': ('light', 'tone', '影调', '明暗', '光线'),
    'wb': ('wb', 'white balance', '白平衡', '色温', '色调'),
    'basis': ('basis', 'profile', 'calibration', '配置文件', '校准', '基础色彩'),
    'mixer_point': ('mixer_point', 'mixer', 'hsl', 'point color', '颜色混合', '混色器', '点颜色'),
    'curves': ('curves', 'curve', 'tone curve', '曲线', '点曲线'),
    'grading': ('grading', 'color grading', '色彩分级', '分级'),
    'effects': ('effects', 'texture', 'clarity', 'dehaze', '效果', '纹理', '清晰度', '去朦胧', '去雾'),
    'masks': ('masks', 'mask', 'local', '蒙版', '遮罩', '局部'),
}
_CONTROL_GROUPS = {
    'light': ['light.' + x for x in ('exposure', 'contrast', 'highlights', 'shadows', 'whites', 'blacks')],
    'wb': ['wb.temp', 'wb.tint'],
    'basis': ['profile.amount'] + ['calibration.' + x for x in ('shadowsTint', 'redHue', 'redSat', 'greenHue', 'greenSat', 'blueHue', 'blueSat')],
    'mixer_point': ['color.vibrance', 'color.saturation'] + ['mixer.' + b + '.' + f for b in ('red', 'orange', 'yellow', 'green', 'aqua', 'blue', 'purple', 'magenta') for f in ('hue', 'sat', 'lum')],
    'curves': ['curve.' + x for x in ('highlights', 'lights', 'darks', 'shadows', 'splitShadows', 'splitMid', 'splitHighlights', 'refineSaturation')],
    'grading': ['grading.' + b + '.' + f for b in ('shadows', 'midtones', 'highlights', 'global') for f in ('hue', 'sat', 'lum')] + ['grading.blending', 'grading.balance'],
    'effects': ['effects.texture', 'effects.clarity', 'effects.dehaze'],
}
STATIC_CONTROL_IDS = frozenset(x for group in _CONTROL_GROUPS.values() for x in group)
POINT_FIELDS = ('hueShift', 'satShift', 'lumShift', 'variance', 'range', 'hueRange', 'satRange', 'lumRange')
COMMAND_IDS = (
    'develop.curve', 'curve.reset', 'pointColor.pick', 'pointColor.delete',
    'mask.add', 'mask.addComponent', 'mask.brushStroke', 'mask.update',
    'mask.sampleColor', 'mask.objectPoint', 'mask.refineDetail', 'mask.refine',
    'mask.adjust', 'mask.select', 'mask.delete', 'mask.deleteAll', 'mask.rename',
    'mask.invert', 'mask.visible', 'mask.duplicate', 'mask.component', 'mask.move',
)
SHAPE_KINDS = ('brush', 'linear', 'radial', 'sky', 'subject', 'background', 'luminanceRange', 'colorRange', 'object', 'prompt')
EXCLUDED_LOCAL_FIELDS = ('sharpness', 'noise', 'moire', 'defringe')
_WORDS = {
    'exposure': ('曝光', 'EV'), 'contrast': ('对比度',), 'highlights': ('高光',),
    'shadows': ('阴影',), 'whites': ('白色', '白场'), 'blacks': ('黑色', '黑场'),
    'temp': ('色温', 'temperature', 'Kelvin'), 'tint': ('色调',), 'vibrance': ('自然饱和度',),
    'saturation': ('饱和度',), 'hue': ('色相',), 'sat': ('饱和度',), 'lum': ('明亮度', '亮度', 'luminance'),
    'red': ('红色',), 'orange': ('橙色',), 'yellow': ('黄色',), 'green': ('绿色',),
    'aqua': ('青色',), 'blue': ('蓝色',), 'purple': ('紫色',), 'magenta': ('洋红', '品红'),
    'midtones': ('中间调',), 'global': ('全局',), 'blending': ('混合',), 'balance': ('平衡',),
    'texture': ('纹理',), 'clarity': ('清晰度',), 'dehaze': ('去朦胧', '去雾'),
    'amount': ('数量', '强度'), 'pick': ('取样', '采样', '拾色'), 'delete': ('删除',),
    'add': ('创建', '新建'), 'addComponent': ('添加组件', '相交', '减去'),
    'brushStroke': ('画笔', '笔划'), 'update': ('更新形状',), 'sampleColor': ('颜色范围取样',),
    'objectPoint': ('对象点选',), 'refineDetail': ('细化AI细节',), 'refine': ('细化边缘',),
    'adjust': ('局部调整',), 'select': ('选择',), 'deleteAll': ('删除全部蒙版',),
    'rename': ('重命名',), 'invert': ('反转',), 'visible': ('可见', '隐藏'),
    'duplicate': ('复制',), 'component': ('组件',), 'move': ('移动', '排序'), 'reset': ('重置',),
    'linear': ('线性渐变',), 'radial': ('径向渐变',), 'sky': ('天空',),
    'subject': ('主体',), 'background': ('背景',), 'colorRange': ('颜色范围',),
    'luminanceRange': ('亮度范围',), 'object': ('对象',), 'prompt': ('文字描述',),
}
_SOURCE = 'external/lightcraft/source/crates/'


def module_for(control_id: str) -> str:
    """Return an approved module; reject unknown or out-of-scope IDs."""
    if not isinstance(control_id, str):
        raise ValueError('control_id must be a string')
    for module, ids in _CONTROL_GROUPS.items():
        if control_id in ids:
            return module
    if re.fullmatch(r'pointColor\.[0-7]\.(' + '|'.join(POINT_FIELDS) + ')', control_id):
        return 'mixer_point'
    if control_id in COMMAND_IDS:
        return 'masks' if control_id.startswith('mask.') else ('curves' if control_id in ('develop.curve', 'curve.reset') else 'mixer_point')
    raise ValueError('Outside P0 whitelist: ' + control_id)


def _number(lo=None, hi=None, **extra):
    result = {'type': 'number', **extra}
    if lo is not None:
        result['minimum'] = lo
    if hi is not None:
        result['maximum'] = hi
    return result


def _object(properties, required=(), **extra):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False, **extra}


def _enum(values, **extra):
    return {'type': 'string', 'enum': list(values), **extra}


def _array(item, minimum=0, maximum=None):
    result = {'type': 'array', 'items': item, 'minItems': minimum}
    if maximum is not None:
        result['maxItems'] = maximum
    return result


def _point(serialized=False):
    if serialized:
        return _object({'x': _number(0, 1), 'y': _number(0, 1)}, ('x', 'y'))
    return _array(_number(0, 1), 2, 2)


_ID = {'type': ['integer', 'null'], 'minimum': 0, 'maximum': 4294967295, 'description': 'Existing mask ID, or null/omitted uses runner-observed activeMask; never guess. mask.select null/omitted clears selection.'}
_INDEX = {'type': ['integer', 'null'], 'minimum': 0, 'description': 'Zero-based component index; null/omitted uses this command native default.'}
_REQUIRED_INDEX = {'type': 'integer', 'minimum': 0}
_RADIUS = _number(0, exclusiveMinimum=0, description='Positive radius as a fraction of the image LONG EDGE, not pixels or percent. No source-evidenced upper bound; values above 1 may be intentional.')
_BOOL = {'type': 'boolean'}
_STRING = {'type': 'string'}
_OP = _enum(('add', 'subtract', 'intersect'))
_LOCAL = {key: _number(-100, 100) for key in ('temp', 'tint', 'contrast', 'highlights', 'shadows', 'whites', 'blacks', 'texture', 'clarity', 'dehaze', 'hue', 'saturation')}
_LOCAL.update(exposure=_number(-4, 4), color_hue=_number(0, 360), color_sat=_number(0, 100), amount=_number(0, 200))


def _shape_schemas(serialized=False):
    point = _point(serialized)
    stroke = _object({
        'points': _array(point), 'size': _RADIUS,
        **{k: _number(0, 100) for k in ('feather', 'flow', 'density')},
        'erase': _BOOL, 'auto_mask': _BOOL, 'pressure': _array(_number(0, 1)),
    })  # BrushStroke is serde(default); even an empty stroke round-trips.
    fields = {
        'brush': {'strokes': _array(stroke)},
        'linear': {'start': point, 'end': point},
        'radial': {'center': point, 'rx': _RADIUS, 'ry': _RADIUS, 'angle': _number(), 'feather': _number(0, 100), 'invert': _BOOL},
        'sky': {}, 'subject': {}, 'background': {},
        'luminanceRange': {'lo': _number(0, 1), 'hi': _number(0, 1), ('lo_feather' if serialized else 'loFeather'): _number(0, 1), ('hi_feather' if serialized else 'hiFeather'): _number(0, 1)},
        'colorRange': {'samples': _array(_array(_number(), 3, 3), 0, 5), 'refine': _number(0, 100)},
        'object': {('hint' if serialized else 'points'): _array(point, 0, 64), 'exclude': _array(point, 0, 64)},
        'prompt': {'text': {'type': 'string', 'minLength': 1}},
    }
    # shape_from supplies these defaults for add/addComponent; MaskShape JSON
    # for update does not. Do not mistake missing geometry for wrong types.
    required = {'prompt': ('text',)}
    if serialized:
        required = {kind: tuple(props) for kind, props in fields.items()}
        required['object'] = ('hint',)
        required['prompt'] = ('text',)
        # Serialized Object/Prompt can carry seg/detail, but unqualified opaque
        # segment payloads are deliberately not accepted by this P0 interface.
    else:
        fields['brush'] = {}  # shape_from creates an empty brush; stroke is a separate command.
    return fields, required


def _command_schema(ident):
    target = {'id': _ID}
    if ident in ('mask.add', 'mask.addComponent'):
        fields, required = _shape_schemas()
        variants = []
        for kind, props in fields.items():
            default_kind = 'radial' if ident == 'mask.add' else 'brush'
            shared = {'kind': {'type': 'string', 'const': kind}}
            shared.update({'name': _STRING} if ident == 'mask.add' else {**target, 'op': _OP, 'invert': _BOOL})
            variants.append(_object({**shared, **props}, (*(() if kind == default_kind else ('kind',)), *required.get(kind, ()))))
        return {'oneOf': variants}
    if ident == 'mask.update':
        fields, required = _shape_schemas(True)
        shapes = [_object({'kind': {'type': 'string', 'const': kind}, **props}, ('kind', *required.get(kind, ()))) for kind, props in fields.items()]
        return _object({**target, 'component': _INDEX, 'shape': {'oneOf': shapes}}, ('shape',))
    table = {
        'develop.curve': _object({'channel': _enum(('master', 'rgb', 'red', 'green', 'blue')), 'points': _array(_point())}, ('points',), description='Empty resets to linear; otherwise input x must be strictly increasing. Output y need not be monotonic.'),
        'curve.reset': _object({'channel': _enum(('all', 'point', 'parametric', 'master', 'rgb', 'luma', 'red', 'green', 'blue'))}),
        'pointColor.pick': _object({'x': _number(0, 1), 'y': _number(0, 1)}, ('x', 'y')),
        'pointColor.delete': _object({'index': _REQUIRED_INDEX}, ('index',)),
        'mask.brushStroke': _object({**target, 'points': _array(_point(), 1), 'size': _RADIUS, **{k: _number(0, 100) for k in ('feather', 'flow', 'density')}, **{k: _BOOL for k in ('erase', 'autoMask', 'connect')}, 'pressure': {'anyOf': [_array(_number(0, 1)), {'type': 'null'}]}}, ('points',)),
        'mask.sampleColor': _object({**target, 'component': _INDEX, 'x': _number(0, 1), 'y': _number(0, 1), 'add': _BOOL}, ('x', 'y')),
        'mask.objectPoint': _object({**target, 'x': _number(0, 1), 'y': _number(0, 1), 'exclude': _BOOL}, ('x', 'y')),
        'mask.refineDetail': _object({**target, 'component': _INDEX}),
        'mask.refine': _object({**target, 'value': _number(0, 100, default=0)}),
        'mask.adjust': _object({**target, 'values': _object(_LOCAL, minProperties=1)}, ('values',)),
        'mask.select': _object(target), 'mask.delete': _object(target),
        'mask.deleteAll': _object({}), 'mask.rename': _object({**target, 'name': _STRING}, ('name',)),
        'mask.invert': _object(target), 'mask.visible': _object({**target, 'visible': _BOOL}),
        'mask.duplicate': _object({**target, 'invert': _BOOL}),
        'mask.component': {'oneOf': [_object({**target, 'component': _REQUIRED_INDEX, 'action': {'const': action, 'type': 'string'}, **({'name': _STRING} if action == 'rename' else {'op': _OP} if action == 'op' else {})}, ('component', 'action', *(['op'] if action == 'op' else []))) for action in ('invert', 'duplicate', 'delete', 'rename', 'op')]},
        'mask.move': _object({**target, 'to': {'type': 'integer', 'minimum': -(2**63), 'maximum': 2**63 - 1}, 'delta': {'type': 'integer', 'minimum': -(2**63), 'maximum': 2**63 - 1}}, anyOf=[{'required': ['to']}, {'required': ['delta']}]),
    }
    return table[ident]


_EXAMPLES = {
    'develop.curve': {'channel': 'master', 'points': [[0, 0], [0.5, 0.55], [1, 1]]},
    'curve.reset': {'channel': 'point'}, 'pointColor.pick': {'x': 0.5, 'y': 0.5},
    'pointColor.delete': {'index': 0}, 'mask.add': {'kind': 'linear', 'start': [0.5, 0.2], 'end': [0.5, 0.7]},
    'mask.addComponent': {'id': 7, 'kind': 'radial', 'op': 'subtract', 'center': [0.5, 0.5], 'rx': 0.2, 'ry': 0.1},
    'mask.brushStroke': {'id': 7, 'points': [[0.4, 0.4], [0.5, 0.5]], 'size': 0.03},
    'mask.update': {'id': 7, 'component': 0, 'shape': {'kind': 'linear', 'start': {'x': 0.5, 'y': 0.2}, 'end': {'x': 0.5, 'y': 0.7}}},
    'mask.sampleColor': {'id': 7, 'x': 0.5, 'y': 0.5}, 'mask.objectPoint': {'id': 7, 'x': 0.5, 'y': 0.5},
    'mask.refineDetail': {'id': 7}, 'mask.refine': {'id': 7, 'value': 30},
    'mask.adjust': {'id': 7, 'values': {'exposure': 0.3, 'temp': -10, 'color_hue': 210, 'color_sat': 8}},
    'mask.select': {'id': 7}, 'mask.delete': {'id': 7}, 'mask.deleteAll': {},
    'mask.rename': {'id': 7, 'name': 'Sky'}, 'mask.invert': {'id': 7},
    'mask.visible': {'id': 7, 'visible': False}, 'mask.duplicate': {'id': 7},
    'mask.component': {'id': 7, 'component': 0, 'action': 'invert'}, 'mask.move': {'id': 7, 'to': 0},
}


def _sam_blockers(dependencies):
    status = dependencies.get('segment.model.status')
    if not isinstance(status, dict):
        return ['segment.model.status missing: supply a read-only native snapshot; no automatic download/prepare']
    blockers = []
    for field in ('available', 'installed', 'loaded', 'busy', 'analyzing'):
        if type(status.get(field)) is not bool:
            blockers.append('SAM status field missing/not boolean: ' + field)
    if status.get('available') is not True:
        blockers.append('SAM unavailable or unknown in this build')
    if status.get('installed') is not True:
        blockers.append('SAM local model not installed/unknown')
    # remote_endpoint() serializes a local configuration as null, not false.
    if 'remote' not in status or status['remote'] is not None:
        blockers.append('Remote SAM forbidden or local endpoint status unknown')
    if status.get('busy') is not False or status.get('analyzing') is not False:
        blockers.append('SAM busy/analyzing or idle status unknown; read status again')
    download = status.get('download')
    if not isinstance(download, dict) or download.get('running') is not False:
        blockers.append('SAM download running/unknown; do not initiate or resume it here')
    return blockers


def _shape_blockers(kind, dependencies):
    blockers = _sam_blockers(dependencies) if kind in ('object', 'prompt') else []
    qualifications = dependencies.get('shapes')
    if qualifications is not None:
        qualification = qualifications.get(kind) if isinstance(qualifications, dict) else None
        if not isinstance(qualification, dict) or qualification.get('status') != 'ready':
            reason = qualification.get('reason', '') if isinstance(qualification, dict) else 'missing/malformed qualification'
            blockers.append('Shape qualification not ready: ' + kind + ' ' + str(reason))
    return blockers


def _command_blockers(native):
    if native is None:
        return ['Command absent from supplied native command list']
    if native.get('enabled') is not True:
        return [str(native.get('disabled_reason') or 'Native command disabled or explicit boolean enabled=true missing')]
    return []


def _result_contract(ident):
    """Native payloads, NOT an invented maskId returned by the engine."""
    active = {'type': 'integer', 'minimum': 0, 'maximum': 4294967295}
    pending = _object({'pending': {'type': 'boolean', 'const': True}}, ('pending',))
    active_result = _object({'activeMask': {**_ID, 'description': 'Actual native activeMask, not a predicted next ID'}, 'pending': _BOOL}, ('activeMask',))
    payload = active_result
    if ident == 'mask.duplicate':
        payload = _object({'activeMask': active}, ('activeMask',))
    elif ident == 'develop.curve':
        payload = {'type': 'null'}
    elif ident == 'curve.reset':
        payload = _object({'channel': _STRING}, ('channel',))
    elif ident == 'pointColor.pick':
        payload = _object({'index': _REQUIRED_INDEX, 'lum': _number(), 'chroma': _number(), 'hue': _number(0, 360)}, ('index', 'lum', 'chroma', 'hue'))
    elif ident == 'pointColor.delete':
        payload = {'type': 'null'}
    elif ident in ('mask.add', 'mask.addComponent'):
        payload = {'anyOf': [_object({'activeMask': active, 'pending': _BOOL}, ('activeMask',)), pending]}
    elif ident == 'mask.brushStroke':
        payload = _object({'activeMask': _ID, 'connected': _BOOL}, ('activeMask', 'connected'))
    elif ident == 'mask.sampleColor':
        payload = _object({'samples': _array(_array(_number(), 3, 3), 0, 5)}, ('samples',))
    elif ident == 'mask.objectPoint':
        payload = _object({'include': _REQUIRED_INDEX, 'exclude': _REQUIRED_INDEX, 'pending': _BOOL}, ('include', 'exclude'))
    elif ident == 'mask.refineDetail':
        payload = _object({'started': _BOOL}, ('started',))
    elif ident == 'mask.select':
        payload = {'type': 'null'}
    creates_id = ident in ('mask.add', 'mask.duplicate', 'mask.brushStroke')
    condition = ('no existing selected/explicit mask' if ident == 'mask.brushStroke' else
                 'always for completed creation' if creates_id else 'not a new mask')
    return {
        'native_schema': payload, 'may_create_mask_id': creates_id,
        'handoff_required': ident in ('pointColor.pick', 'mask.add', 'mask.addComponent', 'mask.duplicate', 'mask.sampleColor', 'mask.objectPoint', 'mask.refineDetail'),
        'handoff_condition': ('when it creates a mask or appends a brush component' if ident == 'mask.brushStroke' else
                              'when action is duplicate (new component index)' if ident == 'mask.component' else
                              'result/readback required after this command' if ident in ('pointColor.pick', 'mask.add', 'mask.addComponent', 'mask.duplicate', 'mask.sampleColor', 'mask.objectPoint', 'mask.refineDetail') else
                              'deterministic changes on observed objects may continue within the validated batch'),
        'new_mask_id': {'native_field': 'activeMask', 'runner_field': 'maskId', 'predict': False,
                        'creation_condition': condition,
                        'verification': 'Only for a completed NEW mask: require actual returned activeMask is a new ID in fresh masks. Pending-only has no ID; never derive from max/next_mask_id. An explicit existing-mask brushStroke does NOT create/select a mask; retain its observed target even if returned activeMask is null or different.'} if creates_id else None,
        'readback': 'Use command result AND fresh get_develop plus observed selection. pending=true/started=true is not completion. Existing IDs and point indices must be observed, not inferred.',
    }


def _aliases(ident, module):
    result = [ident, *MODULE_ALIASES[module]]
    for part in ident.split('.'):
        result.extend(_WORDS.get(part, ()))
        for word, aliases in _WORDS.items():
            if word.lower() != part.lower() and word.lower() in part.lower():
                result.extend(aliases)
    if ident in ('mask.add', 'mask.addComponent', 'mask.update'):
        for kind in SHAPE_KINDS:
            result.extend((kind, *_WORDS.get(kind, ())))
    return list(dict.fromkeys(result))


def _rows(rows, name):
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in rows):
        raise ValueError(name + ' must be a list of native rows with string id')
    if len({row['id'] for row in rows}) != len(rows):
        raise ValueError('Duplicate ' + name + ' ID')
    return {row['id']: row for row in rows}


def _dependency_schema():
    # Native status/list rows have extra descriptive fields; do not reject them.
    command_row = {'type': 'object', 'properties': {'id': _STRING, 'enabled': _BOOL},
                   'required': ['id', 'enabled'], 'additionalProperties': True}
    status = {'type': 'object', 'properties': {
        **{key: _BOOL for key in ('available', 'installed', 'loaded', 'busy', 'analyzing')},
        'remote': {'type': 'null'},
        'download': {'type': 'object', 'properties': {'running': _BOOL},
                     'required': ['running'], 'additionalProperties': True}},
        'required': ['available', 'installed', 'loaded', 'busy', 'analyzing', 'remote', 'download'],
        'additionalProperties': True}
    return {'type': 'object', 'properties': {'commands': _array(command_row), 'segment.model.status': status,
            'shapes': {'type': 'object', 'additionalProperties': _object({
                'status': _enum(('ready', 'blocked', 'skip')), 'reason': _STRING}, ('status',))}},
            'required': ['commands'], 'additionalProperties': True}


def build_catalog(controls: list, commands: list, dependencies: dict) -> dict:
    """Build JSON-serializable discovery data without expanding the native scope."""
    if not isinstance(dependencies, dict):
        raise ValueError('dependencies must be a dict of read-only snapshots')
    native_controls, native_commands = _rows(controls, 'controls'), _rows(commands, 'commands')
    control_rows = []
    for ident, native in native_controls.items():
        try:
            module = module_for(ident)
        except ValueError:
            continue
        if ident in COMMAND_IDS:
            continue
        for key in ('min', 'max'):
            if not _finite(native.get(key)):
                raise ValueError('Missing/invalid native ' + key + ': ' + ident)
        if native['min'] > native['max']:
            raise ValueError('Invalid native control range: ' + ident)
        units = ('kelvin' if native['min'] >= 1000 else 'relative slider units, NOT kelvin') if ident == 'wb.temp' else ('EV' if ident == 'light.exposure' else 'degrees' if ident.startswith('grading.') and ident.endswith('.hue') else 'native slider units')
        control_blockers = [] if 'enabled' not in native else _command_blockers(native)
        row = {**copy.deepcopy(native), 'kind': 'control', 'module': module, 'aliases': _aliases(ident, module), 'units': units, 'schema': _number(native['min'], native['max']), 'source_status': {'status': 'source_reviewed_not_visual_calibration', 'source': _SOURCE + 'develop/src/controls.rs', 'native_listed': True}, 'availability': 'blocked' if control_blockers else 'ready', 'blockers': control_blockers}
        if ident.startswith('pointColor.'):
            row['sample_index'] = int(ident.split('.')[1])
            row['prerequisites'] = ['Exact ID must be in current list_controls; sample_index must exist in current point_colors. Deletion shifts later indices; refetch controls.']
        control_rows.append(row)
    command_rows = []
    for ident in COMMAND_IDS:
        module = module_for(ident)
        native = native_commands.get(ident)
        blockers = _command_blockers(native)
        if ident == 'mask.objectPoint':
            blockers.extend(_shape_blockers('object', dependencies))
        elif ident == 'mask.refineDetail':
            shape_options = [_shape_blockers(kind, dependencies) for kind in ('object', 'prompt')]
            if all(shape_options):
                blockers.extend(dict.fromkeys(reason for reasons in shape_options for reason in reasons))
        prereqs = ['active photo; current get_develop readback', 'IDs/indices must come from readback or the previous runner result, not these synthetic examples']
        if ident.startswith('mask.') and ident not in ('mask.add', 'mask.deleteAll', 'mask.select', 'mask.brushStroke'):
            prereqs.append('existing mask id, or explicit runner-observed activeMask')
        if ident in ('mask.add', 'mask.addComponent', 'mask.update'):
            prereqs.append('shape-specific availability below; object/prompt require qualified local segmentation; update does not run resolve_ai')
        if ident in ('pointColor.pick', 'mask.add', 'mask.duplicate', 'mask.brushStroke'):
            prereqs.append('if creating an object: end batch; runner passes actual result and settings into next step')
        if ident == 'mask.add':
            prereqs.append('Native may inherit previous per-kind local adjustments; read adjust back. Host reset/neutralization must be explicit and traced, never assume zero.')
        if ident == 'pointColor.delete':
            prereqs.append('existing zero-based point_colors index; deletion shifts later indices')
        if ident == 'pointColor.pick':
            prereqs.append('fewer than 8 point_colors samples')
        if ident in ('mask.objectPoint', 'mask.refineDetail'):
            prereqs.append('segment.model.status: available, locally installed, idle, no remote; object/prompt component required')
        file = 'curves.rs' if ident == 'curve.reset' else 'develop.rs' if ident == 'develop.curve' else 'color.rs' if ident.startswith('pointColor.') else 'masks.rs'
        row = {
            'id': ident, 'kind': 'command', 'module': module, 'label': (native or {}).get('label', ident),
            'aliases': _aliases(ident, module), 'schema': _command_schema(ident), 'result_contract': _result_contract(ident),
            'units': {'coordinates': 'normalized full uncropped oriented image, x right/y down; add: [x,y], update: {x,y}', 'radii_and_brush_size': 'fraction of image long edge (brush size is radius)', 'angle_and_color_hue': 'degrees', 'curve_points': 'normalized input/output 0..1, not pixels or 0..255', 'local_temp_tint': 'relative slider units -100..100, NOT kelvin', 'local_exposure': 'EV -4..4', 'indices': 'zero-based; mask id is u32, not an index', 'colorRange.samples': 'OkLab triples, not RGB or CIELAB'},
            'prerequisites': prereqs, 'examples': [{'command': {'id': ident, 'params': copy.deepcopy(_EXAMPLES[ident])}, 'synthetic': True, 'context': 'ID 7 / sample index 0 are illustrative ONLY; substitute an observed existing object with the required shape.'}],
            'source_status': {'status': 'source_reviewed_not_visual_calibration', 'source': _SOURCE + 'engine/src/cmd/' + file, 'native_listed': native is not None, 'notes': 'P0 rejects unknown fields, nonfinite values, excluded local detail fields, and silent coercion/clamping. No runtime/visual claim.'},
            'availability': 'blocked' if blockers else 'ready', 'blockers': blockers,
        }
        if ident in ('mask.add', 'mask.addComponent', 'mask.update'):
            row['shapes'] = {}
            for kind in SHAPE_KINDS:
                shape_blockers = blockers + _shape_blockers(kind, dependencies)
                if ident == 'mask.update' and kind in ('object', 'prompt'):
                    shape_blockers += ['mask.update does not compute segmentation; opaque seg/detail replay is unqualified in P0']
                row['shapes'][kind] = {'availability': 'blocked' if shape_blockers else 'ready', 'blockers': shape_blockers,
                                       'source_status': 'classical heuristic / matte fallback, NOT SAM semantic segmentation' if kind in ('sky', 'subject', 'background') else 'source reviewed; no visual qualification'}
        command_rows.append(row)
    items = sorted(control_rows + command_rows, key=lambda row: (MODULES.index(row['module']), row['id']))
    return {'schema': 'photo-capability-catalog/v1', 'build': BUILD, 'base_build': BASE_BUILD,
            'modules': [{'id': module, 'aliases': list(MODULE_ALIASES[module])} for module in MODULES],
            'controls': control_rows, 'commands': command_rows, 'items': items,
            'counts': {'static_whitelist': len(STATIC_CONTROL_IDS), 'static_listed': sum(r['id'] in STATIC_CONTROL_IDS for r in control_rows), 'dynamic_point_controls': sum(r['id'].startswith('pointColor.') for r in control_rows), 'commands': len(command_rows)},
            'missing_static_controls': sorted(STATIC_CONTROL_IDS - native_controls.keys()),
            'dependencies': {**copy.deepcopy(dependencies), 'commands': copy.deepcopy(commands)},
            'dependency_schema': _dependency_schema(),
            'dependency_contract': {'commands': 'Required native command-list rows with boolean enabled=true; build_catalog supplies this snapshot for validate_commands.', 'segment.model.status': 'For object/prompt: native available/installed/loaded/busy/analyzing booleans, remote:null, download.running:false. loaded:false is valid lazy loading.', 'shapes': 'Optional qualification restrictions; if supplied, a missing/skip/blocked entry is NOT ready. Source-ready is not visual calibration.'},
            'readonly_dependency_queries': [{'id': 'segment.model.status', 'params': {}, 'readonly': True, 'execute_automatically': False}],
            'excluded_local_fields': list(EXCLUDED_LOCAL_FIELDS), 'result_handoff': 'Runner must pass actual command result plus current settings to next step. This catalog neither predicts IDs/indices nor resolves pending work.'}


def discover(catalog: dict, query: str = '', module=None, offset: int = 0, page_size: int = 8) -> dict:
    """Bilingual paginated lookup; exact ID outranks aliases, blocked != no_match."""
    if not isinstance(catalog, dict) or not isinstance(query, str):
        raise ValueError('catalog must be a dict and query a string')
    if type(offset) is not int or offset < 0 or type(page_size) is not int or not 1 <= page_size <= 32:
        raise ValueError('offset >= 0 and page_size in 1..32 must be integers')
    if module is not None:
        if not isinstance(module, str):
            raise ValueError('module must be a string')
        key = module.strip().casefold()
        module = next((m for m, aliases in MODULE_ALIASES.items() if key in [a.casefold() for a in aliases]), None)
        if module is None:
            raise ValueError('Unknown P0 module: ' + key)
    query = query.strip().casefold()
    rows = [row for row in catalog.get('items', []) if module is None or row['module'] == module]
    exact = [row for row in rows if row['id'].casefold() == query] if query else []
    if exact:
        rows = exact
    elif query:
        tokens = query.split()
        def matches(row):
            corpus = ' '.join([row['id'], str(row.get('label', '')), *row.get('aliases', [])]).casefold()
            compact = corpus.replace(' ', '')
            return all(token in corpus or token.replace(' ', '') in compact for token in tokens)
        rows = [row for row in rows if matches(row)]
    end = offset + page_size
    status = 'no_match' if not rows else 'blocked' if all(row['availability'] == 'blocked' for row in rows) else 'matched'
    return {'status': status, 'items': copy.deepcopy(rows[offset:end]), 'total': len(rows), 'next_offset': end if end < len(rows) else None, 'offset': offset, 'page_size': page_size, 'exact_id': bool(exact), 'matched_blocked': sum(r['availability'] == 'blocked' for r in rows)}


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False  # Python integers can exceed any finite native f64.


def _check(value, schema, path):
    # Small dependency-free validator for precisely the JSON Schema subset above.
    for keyword in ('oneOf', 'anyOf'):
        if keyword in schema:
            matches = 0
            for candidate in schema[keyword]:
                try:
                    _check(value, candidate, path)
                    matches += 1
                except ValueError:
                    pass
            if not matches or (keyword == 'oneOf' and matches != 1):
                raise ValueError(path + ': does not match ' + keyword + ' schema (check shape coordinates/fields/action)')
    types = schema.get('type')
    if types:
        types = types if isinstance(types, list) else [types]
        valid = {'object': isinstance(value, dict), 'array': isinstance(value, list), 'string': isinstance(value, str), 'boolean': type(value) is bool, 'integer': type(value) is int, 'number': _finite(value), 'null': value is None}
        if not any(valid[t] for t in types):
            raise ValueError(path + ': invalid type or nonfinite number')
    if 'const' in schema and value != schema['const']:
        raise ValueError(path + ': wrong constant')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(path + ': unknown enum value')
    if isinstance(value, dict):
        if any(key not in value for key in schema.get('required', [])):
            raise ValueError(path + ': missing required field')
        props = schema.get('properties', {})
        if schema.get('additionalProperties') is False and set(value) - props.keys():
            raise ValueError(path + ': unknown/excluded fields ' + str(sorted(set(value) - props.keys())))
        if len(value) < schema.get('minProperties', 0):
            raise ValueError(path + ': empty object')
        for key, child in value.items():
            if key in props:
                _check(child, props[key], path + '.' + key)
            elif isinstance(schema.get('additionalProperties'), dict):
                _check(child, schema['additionalProperties'], path + '.' + key)
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', math.inf):
            raise ValueError(path + ': wrong array length')
        for index, child in enumerate(value):
            if 'items' in schema:
                _check(child, schema['items'], path + '[' + str(index) + ']')
    if isinstance(value, str) and len(value.strip()) < schema.get('minLength', 0):
        raise ValueError(path + ': empty string')
    if type(value) in (int, float):
        if not _finite(value) or value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf) or ('exclusiveMinimum' in schema and value <= schema['exclusiveMinimum']):
            raise ValueError(path + ': out of range/nonfinite')


def _component(mask, index=None, kinds=None, last=False):
    components = mask['components']
    if index is None:
        indices = range(len(components) - 1, -1, -1) if last else range(len(components))
        index = next((i for i in indices if kinds is None or components[i].get('shape', {}).get('kind') in kinds), None)
    if index is None or index >= len(components):
        raise ValueError('No required existing mask component')
    component = components[index]
    if kinds is not None and component.get('shape', {}).get('kind') not in kinds:
        raise ValueError('Wrong mask component kind; expected ' + str(kinds))
    return component


def _shape_validate(shape, dependencies, serialized=False):
    kind = shape['kind']
    blockers = _shape_blockers(kind, dependencies)
    if blockers:
        raise ValueError('blocked: ' + '; '.join(blockers))
    if serialized and kind in ('object', 'prompt'):
        raise ValueError('blocked: mask.update does not segment; use add/objectPoint and runner readback; stored seg/detail unqualified')
    if kind == 'linear' and shape.get('start', [0.5, 0.25]) == shape.get('end', [0.5, 0.6]):
        raise ValueError('Linear start and end must differ')
    if kind == 'luminanceRange' and shape.get('lo', 0.6) > shape.get('hi', 1):
        raise ValueError('Luminance lo must not exceed hi')
    if kind == 'object' and len(shape.get('hint' if serialized else 'points', [])) + len(shape.get('exclude', [])) > 64:
        raise ValueError('At most 64 object clicks')
    for stroke in shape.get('strokes', []):
        if len(stroke.get('pressure', [])) > len(stroke.get('points', [])):
            raise ValueError('More pressure values than stroke points')


def validate_commands(commands: list, current_settings: dict, dependencies: dict) -> None:
    """Raise ValueError before dispatch; no effects, guessed objects, or IO."""
    if not isinstance(commands, list) or not isinstance(current_settings, dict) or not isinstance(dependencies, dict):
        raise ValueError('Expected commands list, current_settings dict, dependencies dict')
    native_commands = _rows(dependencies.get('commands'), 'dependencies.commands')
    state = copy.deepcopy(current_settings)
    masks, samples = state.get('masks'), state.get('point_colors')
    if not isinstance(masks, list) or not isinstance(samples, list) or any(not isinstance(p, dict) for p in samples) or any(not isinstance(m, dict) or type(m.get('id')) is not int or not 0 <= m['id'] <= 4294967295 or not isinstance(m.get('components'), list) for m in masks):
        raise ValueError('Malformed/incomplete current settings; use actual get_develop readback')
    for mask in masks:
        if any(not isinstance(c, dict) or not isinstance(c.get('shape'), dict) or not isinstance(c['shape'].get('kind'), str) for c in mask['components']):
            raise ValueError('Malformed observed mask component/shape')
    if state.get('pending') is True:
        raise ValueError('Pending native outcome: reconcile result and readback before commands')
    if len({m['id'] for m in masks}) != len(masks):
        raise ValueError('Duplicate mask IDs in settings')
    active = state.get('activeMask')
    if active is not None and (type(active) is not int or active not in [m['id'] for m in masks]):
        raise ValueError('activeMask must identify an existing read-back mask')
    for position, command in enumerate(commands):
        if not isinstance(command, dict) or set(command) - {'id', 'params'} or not isinstance(command.get('id'), str) or command['id'] not in COMMAND_IDS:
            raise ValueError('Unknown/out-of-scope command envelope')
        ident, params = command['id'], command.get('params', {})
        blockers = _command_blockers(native_commands.get(ident))
        if blockers:
            raise ValueError('blocked: ' + ident + ': ' + '; '.join(blockers))
        _check(params, _command_schema(ident), ident)
        if ident in ('mask.add', 'mask.addComponent'):
            params = {**params, 'kind': params.get('kind', 'radial' if ident == 'mask.add' else 'brush')}
        def handoff():
            if position != len(commands) - 1:
                raise ValueError('Runner result/readback required after ' + ident + '; creating command must end batch, never guess IDs/indices')
        if ident == 'develop.curve':
            if any(a[0] >= b[0] for a, b in zip(params['points'], params['points'][1:])):
                raise ValueError('Curve input x must be strictly increasing; do not silently sort/deduplicate')
            continue
        if ident == 'curve.reset':
            continue
        if ident == 'pointColor.pick':
            if len(samples) >= 8:
                raise ValueError('At most 8 point colors')
            handoff()
            continue
        if ident == 'pointColor.delete':
            if params['index'] >= len(samples):
                raise ValueError('No such observed point-color index')
            samples.pop(params['index'])
            continue
        if ident == 'mask.add':
            _shape_validate(params, dependencies)
            handoff()
            continue
        if ident == 'mask.select':
            active = params.get('id')
            if active is not None and not any(m['id'] == active for m in masks):
                raise ValueError('No such observed mask ID')
            continue
        if ident == 'mask.deleteAll':
            masks.clear()
            active = None
            continue
        mid = params.get('id') if params.get('id') is not None else active
        mask = next((m for m in masks if m['id'] == mid), None)
        if ident == 'mask.brushStroke':
            if len(params.get('pressure') or []) > len(params['points']):
                raise ValueError('More pressure values than stroke points')
            if mask is None:
                if params.get('id') is not None:
                    raise ValueError('Unknown explicit mask ID; native would silently create another mask')
                handoff()
            # Last brush component is chosen by native; if absent it appends one.
            if mask is not None and not any(c['shape']['kind'] == 'brush' for c in mask['components']):
                handoff()
                mask['components'].append({'shape': {'kind': 'brush', 'strokes': []}})
            continue
        if mask is None:
            raise ValueError('No such observed mask ID / activeMask missing; do not infer a target')
        if ident == 'mask.addComponent':
            _shape_validate(params, dependencies)
            handoff()
        elif ident == 'mask.duplicate':
            handoff()
        elif ident == 'mask.update':
            _shape_validate(params['shape'], dependencies, True)
            _component(mask, 0 if params.get('component') is None else params['component'])['shape'] = copy.deepcopy(params['shape'])
        elif ident == 'mask.sampleColor':
            component = _component(mask, params.get('component'), ('colorRange',))
            if params.get('add') and len(component['shape'].get('samples', [])) >= 5:
                raise ValueError('At most 5 color-range samples; native would ignore this add')
            handoff()  # sample content must be read back, not fabricated here.
        elif ident in ('mask.objectPoint', 'mask.refineDetail'):
            component = _component(mask, params.get('component'), ('object',) if ident == 'mask.objectPoint' else ('object', 'prompt'), True)
            blockers = _shape_blockers(component['shape']['kind'], dependencies)
            if blockers:
                raise ValueError('blocked: ' + '; '.join(blockers))
            if ident == 'mask.objectPoint':
                shape = component['shape']
                if len(shape.get('hint', [])) + len(shape.get('exclude', [])) >= 64:
                    raise ValueError('At most 64 object clicks')
            handoff()  # includes async pending/started outcomes; never precompute them.
        elif ident == 'mask.delete':
            masks.remove(mask)
            active = masks[-1]['id'] if masks else None
        elif ident == 'mask.component':
            component = _component(mask, params['component'])
            if params['action'] == 'delete':
                mask['components'].pop(params['component'])
                if not mask['components']:
                    masks.remove(mask)
                    active = masks[-1]['id'] if masks else None
            elif params['action'] == 'duplicate':
                handoff()
                mask['components'].insert(params['component'] + 1, copy.deepcopy(component))
        elif ident == 'mask.move':
            index = masks.index(mask)
            to = params.get('to', index + params.get('delta', 0))
            masks.pop(index)
            masks.insert(max(0, min(to, len(masks))), mask)
        # adjust/rename/invert/visible/refine cannot change subsequent target validity.
