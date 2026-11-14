"""Pure host-bound review focus. No pixels, controls, registration or inferred cells."""
import copy
import json
import math
from urllib.parse import quote


_PHASES = ('G1', 'tone', 'G2', 'color', 'G3', 'joint', 'G4', 'complete')
RESOLUTIONS = ('no_regression', 'minor_acceptable', 'spawn_joint_task')
EXPECTATION_FIELDS = ('target', 'expected_change', 'protected_region', 'risk', 'falsifier')


def _text_schema():
    return {'type': 'string', 'minLength': 1, 'pattern': r'\S'}


def _object(properties, required):
    return {'type': 'object', 'properties': properties, 'required': list(required),
            'additionalProperties': False}


def _cells_schema():
    return {'type': 'array', 'minItems': 1, 'uniqueItems': True,
            'items': {'type': 'string', 'pattern': '^[A-D][1-4]$'}}


def anchor_schema():
    """A single model anchor item; callers wrap it in an array schema."""
    result = _object({'id': _text_schema(), 'purpose': {'enum': ['target', 'protected']},
                      'extent': {'enum': ['explicit_cells', 'full_frame', 'unknown']},
                      'view_role': {'enum': ['author', 'current', 'effect_parent', 'effect_current']},
                      'cells': _cells_schema(), 'region': _text_schema(),
                      'qualification': _text_schema()},
                     ('id', 'purpose', 'extent', 'view_role', 'region', 'qualification'))
    result['allOf'] = [{'if': {'properties': {'extent': {'const': 'explicit_cells'}}},
                        'then': {'required': ['cells']}, 'else': {'not': {'required': ['cells']}}}]
    return result


def debt_update_schema():
    """One update item. Legacy shape is admitted only for legacy debts at runtime."""
    check = _object({'focus_id': _text_schema(), 'outcome': {'enum': list(RESOLUTIONS)},
                     'evidence': _text_schema(), 'review_region': _text_schema(),
                     'review_cells': _cells_schema()},
                    ('focus_id', 'outcome', 'evidence', 'review_region'))
    new = _object({'id': _text_schema(), 'resolution': {'enum': list(RESOLUTIONS)},
                   'reason': _text_schema(), 'checks': {'type': 'array', 'minItems': 1, 'items': check},
                   'joint_diagnosis': _text_schema(), 'existing_joint_issue_id': _text_schema()},
                  ('id', 'resolution', 'reason', 'checks'))
    new['allOf'] = [{'if': {'properties': {'resolution': {'const': 'spawn_joint_task'}}},
                     'then': {'required': ['joint_diagnosis']},
                     'else': {'not': {'anyOf': [{'required': ['joint_diagnosis']},
                                              {'required': ['existing_joint_issue_id']}]}}}]
    legacy = _object({'id': _text_schema(), 'status': {'enum': ['resolved', 'accepted_tradeoff']},
                      'reason': _text_schema()}, ('id', 'status', 'reason'))
    return {'oneOf': [new, legacy]}


def _public(value):
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError('Review anchors must be finite JSON data') from exc
    return copy.deepcopy(value)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be a nonempty public string')


def _keys(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError(label + ' requires the documented fields only')


def _roi(value):
    if value is None:
        return None
    if (not isinstance(value, list) or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
            or not 0 <= value[0] < value[2] <= 1 or not 0 <= value[1] < value[3] <= 1):
        raise ValueError('Anchor ROI must be the actual normalized host ROI')
    return _public(value)


def _source(observation, role):
    if not isinstance(observation, dict):
        raise ValueError('Actual source observation required')
    _text(observation.get('id'), 'observation.id')
    _text(observation.get('version'), 'observation.version')
    meta = observation.get('stage_observation') or {}
    spec = observation.get('view_spec') or {}
    if not isinstance(meta, dict) or not isinstance(spec, dict):
        raise ValueError('Actual source observation metadata required')
    phase = observation.get('phase', meta.get('phase'))
    mode = observation.get('comparison_mode', spec.get('comparison_mode'))
    view_mode = meta.get('view_mode')
    if (phase not in _PHASES or (meta.get('phase') is not None and meta['phase'] != phase)
            or mode not in ('target', 'effect') or view_mode not in ('color', 'grayscale')
            or (spec.get('comparison_mode') is not None and spec['comparison_mode'] != mode)):
        raise ValueError('Anchor source requires explicit consistent phase/mode/view_mode')
    allowed = ('author', 'current') if mode == 'target' else ('effect_parent', 'effect_current')
    if role not in allowed or observation.get('batch_of') or spec.get('batch_of'):
        raise ValueError('Anchor view role must belong to this actual unbatched comparison')
    roi = spec.get('reference_roi' if role == 'author' else 'candidate_roi')
    result = {'observation_id': observation['id'], 'settings_version': observation['version'],
              'phase': phase, 'comparison_mode': mode, 'view_mode': view_mode,
              'view_role': role, 'roi': _roi(roi), 'grid': _public(meta.get('grid'))}
    if mode == 'effect':
        _text(observation.get('parent'), 'effect parent version')
        if spec.get('reference_roi') is not None:
            raise ValueError('Effect views must share the actual candidate ROI')
        result['parent_version'] = observation['parent']
    return result


def _grid_cells(grid):
    if (not isinstance(grid, dict) or grid.get('rows') != 4 or grid.get('columns') != 4
            or grid.get('coordinate_space') != 'normalized_each_view_independently'
            or grid.get('registration_claim') is not False or not isinstance(grid.get('cells'), list)):
        raise ValueError('Explicit cells require an actual independent-view 4x4 host grid')
    expected = {chr(65 + row) + str(col + 1): [col / 4, row / 4, (col + 1) / 4, (row + 1) / 4]
                for row in range(4) for col in range(4)}
    found = {}
    for item in grid['cells']:
        if not isinstance(item, dict) or item.get('id') not in expected or item['id'] in found:
            raise ValueError('Malformed actual grid cells')
        found[item['id']] = item.get('box')
    if found != expected:
        raise ValueError('Actual grid must declare the delivered A1-D4 cells and boxes')
    return set(found)


def _cells(value, grid):
    allowed = _grid_cells(grid)
    if (not isinstance(value, list) or not value
            or any(not isinstance(cell, str) or cell not in allowed for cell in value)
            or len(set(value)) != len(value)):
        raise ValueError('Cells must explicitly name unique delivered A1-D4 cells')


def bind_anchors(value, observation):
    """Bind a model list to host metadata; never accept a model-supplied source."""
    if not isinstance(value, list):
        raise ValueError('review_anchors must be a list')
    schema = anchor_schema()
    result, ids = [], set()
    for item in value:
        _keys(item, schema['properties'], schema['required'], 'review anchor')
        for key in ('id', 'region', 'qualification'):
            _text(item[key], 'review anchor.' + key)
        if item['id'] in ids:
            raise ValueError('Duplicate review anchor ID')
        for key in ('purpose', 'extent', 'view_role'):
            if item[key] not in schema['properties'][key]['enum']:
                raise ValueError('Unknown review anchor.' + key)
        source = _source(observation, item['view_role'])
        if item['extent'] == 'explicit_cells':
            _cells(item.get('cells'), source['grid'])
        elif 'cells' in item:
            raise ValueError('Only explicit_cells anchors may contain cells')
        if item['extent'] == 'full_frame' and source['roi'] is not None:
            raise ValueError('A cropped source view cannot establish full_frame extent')
        ids.add(item['id'])
        result.append({**_public(item), 'source': source})
    return result


def build_debt_focus(review, observation, operation_id, expectation):
    """Preserve anchors and original text; absent purposes stay explicitly unknown."""
    _text(operation_id, 'operation_id')
    if not isinstance(review, dict) or not isinstance(expectation, dict):
        raise ValueError('Public review and effect expectation required')
    expected = expectation.get('effect_expectation', expectation)
    if not isinstance(expected, dict):
        raise ValueError('Effect expectation must be an object')
    try:
        bound = bind_anchors(review.get('review_anchors', []), observation)
        unadopted = None
    except ValueError as exc:
        bound = []
        unadopted = {'submitted': _public(review.get('review_anchors', [])), 'reason': str(exc),
                     'rule': 'Optional invalid localization is retained, not inferred or used as evidence; target/protection focus remains unknown.'}
    adopted_count = len(bound)
    context = {key: _public(review[key]) for key in ('reason', 'visible_comparison') if key in review}
    if unadopted:
        context['unadopted_anchors'] = unadopted
    context['effect_expectation'] = {key: _public(expected[key]) for key in EXPECTATION_FIELDS if key in expected}
    for purpose, field in (('target', 'target'), ('protected', 'protected_region')):
        if not any(item['purpose'] == purpose for item in bound):
            text = expected.get(field)
            bound.append({'id': 'unknown-' + purpose, 'purpose': purpose, 'extent': 'unknown',
                          'view_role': 'current' if observation.get('comparison_mode') == 'target' else 'effect_current',
                          'region': text if isinstance(text, str) and text.strip() else 'Unspecified ' + purpose + ' region',
                          'qualification': 'No explicit review anchor for this purpose; location remains unknown.',
                          'source': _source(observation, 'current' if observation.get('comparison_mode') == 'target' else 'effect_current')})
    result = []
    for index, item in enumerate(bound):
        # Namespace supplied IDs separately from fallback IDs without hashing text.
        origin = 'anchor' if index < adopted_count else 'unknown'
        result.append({**_public(item), 'anchor_id': item['id'],
                       'id': 'focus:' + quote(operation_id, safe='') + ':' + origin + ':' + quote(item['id'], safe=''),
                       'operation_id': operation_id, 'expectation': _public(expected), 'context': _public(context)})
    return result


def validate_debt_focus(value, state=None):
    """Validate persisted v2 focus and its exact host observation binding."""
    if not isinstance(value, list) or not value:
        raise ValueError('v2 debt requires nonempty host-bound focus')
    seen = set()
    for focus in value:
        if not isinstance(focus, dict):
            raise ValueError('Debt focus must be an object')
        for key in ('id', 'anchor_id', 'operation_id'):
            _text(focus.get(key), 'focus.' + key)
        if focus['id'] in seen:
            raise ValueError('Duplicate debt focus ID')
        seen.add(focus['id'])
        source = focus.get('source')
        if not isinstance(source, dict):
            raise ValueError('Debt focus needs a bound source')
        if state is not None:
            sources = [o for o in state.get('observations', []) if o.get('id') == source.get('observation_id')]
            if len(sources) != 1:
                raise ValueError('Debt focus source observation is missing or ambiguous')
            observation = sources[0]
        else:
            spec = {'reference_roi' if focus.get('view_role') == 'author' else 'candidate_roi': source.get('roi')}
            observation = {'id': source.get('observation_id'), 'version': source.get('settings_version'),
                           'phase': source.get('phase'), 'comparison_mode': source.get('comparison_mode'),
                           'parent': source.get('parent_version'), 'view_spec': spec,
                           'stage_observation': {'view_mode': source.get('view_mode'), 'grid': source.get('grid')}}
        raw = {key: _public(focus[key]) for key in anchor_schema()['properties'] if key in focus}
        raw['id'] = focus['anchor_id']
        if bind_anchors([raw], observation)[0]['source'] != source:
            raise ValueError('Debt focus source must exactly match its host observation')
        if state is not None and source['settings_version'] not in state.get('versions', {}):
            raise ValueError('Debt focus source version is unknown')
    return _public(value)


def validate_debt_update(update, debt, observation):
    """Validate every focus independently on the actual current color target view."""
    schema = debt_update_schema()['oneOf'][0]
    _keys(update, schema['properties'], schema['required'], 'v2 debt update')
    for key in ('id', 'reason'):
        _text(update[key], 'debt update.' + key)
    if update['resolution'] not in RESOLUTIONS:
        raise ValueError('Unknown debt resolution')
    source = _source(observation, 'current')
    if source['comparison_mode'] != 'target' or source['view_mode'] != 'color':
        raise ValueError('Debt checks require the actual color author/current target view')
    focuses = {item['id']: item for item in validate_debt_focus(debt.get('focus'))}
    checks, seen = update['checks'], set()
    if not isinstance(checks, list) or not checks:
        raise ValueError('Debt checks must cover all focus IDs')
    bound = []
    for check in checks:
        check_schema = schema['properties']['checks']['items']
        _keys(check, check_schema['properties'], check_schema['required'], 'debt focus check')
        for key in ('focus_id', 'evidence', 'review_region'):
            _text(check[key], 'check.' + key)
        ident = check['focus_id']
        if ident not in focuses or ident in seen or check['outcome'] not in RESOLUTIONS:
            raise ValueError('Unknown/duplicate focus ID or invalid outcome')
        focus = focuses[ident]
        if focus['extent'] == 'explicit_cells' or 'review_cells' in check:
            _cells(check.get('review_cells'), source['grid'])
        if focus['extent'] == 'full_frame' and source['roi'] is not None:
            raise ValueError('Full-frame focus requires an actual uncropped current review')
        seen.add(ident)
        bound.append({**_public(check), 'source': _public(source),
                      'mapping': 'qualitative_relocalization_not_registration'})
    if seen != set(focuses):
        raise ValueError('Debt checks must cover every focus, not only a batch reason')
    rank = {value: index for index, value in enumerate(RESOLUTIONS)}
    outcome = max((check['outcome'] for check in checks), key=rank.get)
    if update['resolution'] != outcome:
        raise ValueError('Debt resolution must reflect its most consequential focus outcome')
    if outcome == 'spawn_joint_task':
        _text(update.get('joint_diagnosis'), 'joint_diagnosis')
        if 'existing_joint_issue_id' in update:
            _text(update['existing_joint_issue_id'], 'existing_joint_issue_id')
    elif 'joint_diagnosis' in update or 'existing_joint_issue_id' in update:
        raise ValueError('Joint linkage is only valid for spawn_joint_task')
    return bound
