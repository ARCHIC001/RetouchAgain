"""Pure WB review/context helpers. No bank imports, native IO or visual claims.

The host saves assessments and owns authoritative phase/observation selection.
Mask suitability is a public photographic judgment, never a geometry heuristic.
"""
import copy
import json
import math
import re

from photo_working_context import source_ref, version_ancestry


_PHASES = ('G1', 'tone', 'G2', 'color', 'G3', 'joint', 'G4', 'complete')
_PAIR_TEXT = ('material', 'lighting', 'neutrality_basis', 'temperature_residual', 'tint_residual')


def _object(properties, required):
    return {'type': 'object', 'properties': properties, 'required': list(required),
            'additionalProperties': False}


def _text_schema():
    return {'type': 'string', 'minLength': 1, 'pattern': r'\S'}


def assessment_schema():
    pair = _object({**{key: {'type': 'string', 'pattern': '^[A-D][1-4]$'}
                      for key in ('author_cell', 'current_cell')},
                    **{key: _text_schema() for key in _PAIR_TEXT}},
                   ('author_cell', 'current_cell', *_PAIR_TEXT))
    properties = {
        'reference_status': {'type': 'string', 'enum': ['available', 'not_available']},
        'reference_pairs': {'type': 'array', 'maxItems': 2, 'items': pair},
        'target_intent': {'type': 'string', 'enum': ['neutral', 'intentional_cast', 'uncertain']},
        'intent_reason': _text_schema(),
        'atmosphere': _object({'route': {'type': 'string', 'enum': ['grading', 'local_wb', 'none', 'uncertain']},
                               'reason': _text_schema()}, ('route', 'reason')),
        'wb_task_status': {'type': 'string', 'enum': ['needed', 'not_needed', 'uncertain']},
        'reason': _text_schema(), 'issue_id': _text_schema(), 'group_id': _text_schema(),
    }
    result = _object(properties, tuple(key for key in properties if key not in ('issue_id', 'group_id')))
    result.update(description='Author/current WB diagnosis, not a parent/current effect verdict. No reliable neutral reference is valid; intentional casts need not be neutralized.',
                  allOf=[{'if': {'properties': {'reference_status': {'const': 'available'}}},
                          'then': {'properties': {'reference_pairs': {'minItems': 1}}},
                          'else': {'properties': {'reference_pairs': {'maxItems': 0}}}}])
    return result


def local_review_schema():
    result = _object({'region': _text_schema(), 'decision': {'type': 'string', 'enum': ['reuse', 'create']},
                      'mask_id': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295},
                      'reason': _text_schema(),
                      'existing_mask_ids': {'type': 'array', 'uniqueItems': True,
                                            'items': {'type': 'integer', 'minimum': 0, 'maximum': 4294967295}}},
                     ('region', 'decision', 'reason'))
    result.update(description='For this Color/Joint local temp/tint action only. Reuse a suitable same-region mask; creation lists all considered existing simple IDs and explains different purpose/coverage. Geometry overlap is not semantic evidence.',
                  allOf=[{'if': {'properties': {'decision': {'const': 'reuse'}}},
                          'then': {'required': ['mask_id']},
                          'else': {'required': ['existing_mask_ids'], 'not': {'required': ['mask_id']}}}])
    return result


def _keys(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError(label + ' requires the documented fields only')


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be a nonempty public string')


def _choice(value, choices, label):
    if value not in choices:
        raise ValueError('Unknown ' + label)


def _public(value):
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('WB metadata must be finite JSON data') from exc
    return copy.deepcopy(value)


def _source(observation):
    if not isinstance(observation, dict):
        raise ValueError('Actual WB source observation required')
    meta = observation.get('stage_observation') or {}
    phase = observation.get('phase', meta.get('phase'))
    _choice(phase, _PHASES, 'WB observation phase')
    _text(observation.get('id'), 'observation.id')
    _text(observation.get('version'), 'observation.version')
    spec = observation.get('view_spec') or {}
    mode = observation.get('comparison_mode', spec.get('comparison_mode', 'target'))
    view_mode = meta.get('view_mode', 'grayscale' if phase == 'tone' else 'color')
    return {'observation_id': observation['id'], 'settings_version': observation['version'],
            'phase': phase, 'comparison_mode': mode, 'view_mode': view_mode,
            'target_comparison': mode == 'target' and view_mode == 'color',
            'view_spec': _public(spec)}


def validate_assessment(value, observation, *, require_target=False):
    """Validate an assessment body and overwrite no source fields from the model."""
    schema = assessment_schema()
    _keys(value, schema['properties'], schema['required'], 'wb_assessment')
    for key in ('reference_status', 'target_intent', 'wb_task_status'):
        _choice(value[key], schema['properties'][key]['enum'], 'wb_assessment.' + key)
    for key in ('intent_reason', 'reason', 'issue_id', 'group_id'):
        if key in value:
            _text(value[key], 'wb_assessment.' + key)
    _keys(value['atmosphere'], ('route', 'reason'), ('route', 'reason'), 'wb_assessment.atmosphere')
    _choice(value['atmosphere']['route'], ('grading', 'local_wb', 'none', 'uncertain'), 'atmosphere.route')
    _text(value['atmosphere']['reason'], 'atmosphere.reason')
    pairs = value['reference_pairs']
    if (not isinstance(pairs, list) or len(pairs) > 2 or
            (value['reference_status'] == 'available' and not pairs) or
            (value['reference_status'] == 'not_available' and pairs)):
        raise ValueError('Available WB references need 1–2 pairs; not_available needs an empty list')
    for pair in pairs:
        fields = ('author_cell', 'current_cell', *_PAIR_TEXT)
        _keys(pair, fields, fields, 'WB reference pair')
        for key in ('author_cell', 'current_cell'):
            if not isinstance(pair[key], str) or not re.fullmatch(r'[A-D][1-4]', pair[key]):
                raise ValueError(key + ' must independently identify an A1–D4 cell')
        for key in _PAIR_TEXT:
            _text(pair[key], 'WB reference pair.' + key)
    source = _source(observation)
    if require_target and not source['target_comparison']:
        raise ValueError('WB target assessment requires an actual color author/current observation, not effect/grayscale')
    if pairs and not source['target_comparison']:
        raise ValueError('Author/current reference pairs cannot be claimed from an effect/grayscale observation')
    return {**_public(value), **source, 'claim_type': 'model_wb_assessment_not_measured_fact'}


def _mask_index(settings):
    masks = settings.get('masks') if isinstance(settings, dict) else None
    if not isinstance(masks, list):
        raise ValueError('Actual masks settings readback required for local WB')
    result = {}
    for mask in masks:
        if not isinstance(mask, dict) or type(mask.get('id')) is not int or not 0 <= mask['id'] <= 4294967295:
            raise ValueError('Actual integer mask IDs required')
        if mask['id'] in result:
            raise ValueError('Duplicate actual mask IDs')
        result[mask['id']] = mask
    return result


def _simple(mask):
    components = mask.get('components')
    if not isinstance(components, list) or len(components) != 1 or not isinstance(components[0], dict):
        return False
    component = components[0]
    shape = component.get('shape')
    return (isinstance(shape, dict) and shape.get('kind') in ('linear', 'radial')
            and component.get('op', 'add') == 'add'
            and component.get('invert', False) is False and mask.get('invert', False) is False
            and shape.get('invert', False) is False)


def _local_actions(arguments):
    plan = arguments.get('mask_plan')
    if isinstance(plan, dict) and isinstance(plan.get('values'), dict) and {'temp', 'tint'}.intersection(plan['values']):
        return [{'decision': 'create' if plan.get('action') == 'create' else 'reuse',
                 'mask_id': plan.get('id'), 'values': _public(plan['values'])}]
    return [{'decision': 'reuse', 'mask_id': command.get('params', {}).get('id'),
             'values': _public(command['params']['values'])}
            for command in arguments.get('commands', []) if isinstance(command, dict)
            and command.get('id') == 'mask.adjust' and isinstance(command.get('params'), dict)
            and isinstance(command['params'].get('values'), dict)
            and {'temp', 'tint'}.intersection(command['params']['values'])]


def validate_local_wb_review(review, args, settings, phase, observation):
    """Return bound metadata only for this turn's Color/Joint local WB writes.

    The host/typed executor still validates geometry and exact native readback.
    An explicit creation reason is semantic evidence, not a computed overlap test.
    """
    if phase not in ('color', 'joint', 'integrated'):
        return None
    actions = _local_actions(args)
    if not actions:
        return None
    value = review.get('wb_local_review') if isinstance(review, dict) else None
    schema = local_review_schema()
    _keys(value, schema['properties'], schema['required'], 'review.wb_local_review')
    for key in ('region', 'reason'):
        _text(value[key], 'wb_local_review.' + key)
    _choice(value['decision'], ('reuse', 'create'), 'wb_local_review.decision')
    source = _source(observation)
    if source['phase'] != ('joint' if phase == 'integrated' else phase):
        raise ValueError('Local WB review must use the authoritative phase observation')
    masks = _mask_index(settings)
    considered = value.get('existing_mask_ids', [])
    if (not isinstance(considered, list) or any(type(ident) is not int or ident not in masks for ident in considered)
            or len(set(considered)) != len(considered)):
        raise ValueError('existing_mask_ids must contain unique exact IDs from actual settings')
    if value['decision'] == 'create':
        if 'mask_id' in value or 'existing_mask_ids' not in value or any(a['decision'] != 'create' for a in actions):
            raise ValueError('Creation review must match a new-mask action, with no invented mask_id')
        if not {ident for ident, mask in masks.items() if _simple(mask)} <= set(considered):
            raise ValueError('Consider all existing simple mask IDs; explain different purpose/coverage before creating')
    else:
        ident = value.get('mask_id')
        if (type(ident) is not int or ident not in masks or not _simple(masks[ident]) or
                any(a['decision'] != 'reuse' or a['mask_id'] != ident for a in actions)):
            raise ValueError('Reuse review must match the actual adjusted simple mask ID')
    return {**_public(value), **source, 'actual_local_actions': actions,
            'considered_masks': [_public(masks[ident]) for ident in considered],
            'claim_type': 'model_local_wb_purpose_not_geometry_inference'}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _wb(settings):
    wb = settings.get('wb', {}) if isinstance(settings, dict) else {}
    return {key: copy.deepcopy(wb.get(key, 'unknown')) for key in ('mode', 'scale', 'temp', 'tint')}


def _delta(before, after):
    return after - before if _finite(before) and _finite(after) else None


def coupling_detail(before, after, action):
    """Describe actual WB changes requiring tone/cross-stage review; add no debt."""
    old, new = _wb(before), _wb(after)
    delta = {key: _delta(old[key], new[key]) for key in ('temp', 'tint')}
    triggers = []
    if old['scale'] == new['scale'] == 'kelvin' and delta['temp'] is not None and abs(delta['temp']) > 400:
        triggers.append('kelvin_temperature_delta_gt_400')
    if delta['tint'] not in (None, 0):
        triggers.append('tint_change')
    for key in ('mode', 'scale'):
        if old[key] != new[key]:
            triggers.append(key + '_change')
    arguments = action.get('arguments', action) if isinstance(action, dict) else {}
    picker = any(isinstance(command, dict) and command.get('id') == 'develop.wbPick'
                 for command in arguments.get('commands', []))
    if picker and old != new:
        triggers.append('picker_actual_wb_change')
    local = []
    left = _mask_index(before) if 'masks' in before else {}
    right = _mask_index(after) if 'masks' in after else {}
    for ident in sorted(left.keys() | right.keys()):
        previous = left.get(ident, {}).get('adjust', {})
        current = right.get(ident, {}).get('adjust', {})
        change = {key: _delta(previous.get(key, 0), current.get(key, 0)) for key in ('temp', 'tint')}
        if any(value not in (None, 0) for value in change.values()):
            local.append({'mask_id': ident, 'before': {key: previous.get(key, 0) for key in change},
                          'after': {key: current.get(key, 0) for key in change}, 'delta': change,
                          'created': ident not in left, 'deleted': ident not in right})
    if local:
        triggers.append('local_wb_change')
    if not triggers:
        return None
    return {'triggers': triggers, 'global': {'before': old, 'after': new, 'delta': delta},
            'local': local, 'mask_ids': [row['mask_id'] for row in local],
            'claim_type': 'actual_settings_change_not_visual_judgment'}


def _version_settings(state, version, fallback=None):
    record = state.get('versions', {}).get(version, {})
    return record.get('settings') if isinstance(record.get('settings'), dict) else fallback


def _public_mask_history(state, ident, version):
    """Only actual ancestor-linked operations may explain a mask's public use."""
    ancestry = version_ancestry(state, version)
    ancestors = set(ancestry)
    result = []
    for index, row in enumerate(state.get('history', [])):
        if row.get('action') != 'edit' or row.get('version') not in ancestors:
            continue
        actual = state.get('versions', {}).get(row.get('version'), {})
        if (row.get('parent') != actual.get('parent') or
                actual.get('operation_id') is not None and row.get('operation_id') != actual['operation_id']):
            continue
        child = _version_settings(state, row.get('version')) or {}
        parent = _version_settings(state, actual.get('parent'))
        if not any(mask.get('id') == ident for mask in child.get('masks', [])):
            continue
        args = row.get('arguments') or {}
        plan = args.get('mask_plan') or {}
        created = (plan.get('action') == 'create' and isinstance(parent, dict)
                   and isinstance(parent.get('masks'), list)
                   and not any(mask.get('id') == ident for mask in parent['masks']))
        targeted = plan.get('id') == ident or any(command.get('params', {}).get('id') == ident
                    for command in args.get('commands', []) if isinstance(command, dict))
        if created or targeted:
            result.append({**source_ref('history', index, row),
                           'operation_id': row.get('operation_id'), 'version': row.get('version'),
                           'created': created, 'rationale': args.get('rationale', 'unknown'),
                           'predicted_effect': args.get('predicted_effect', 'unknown'),
                           'wb_local_review': _public((args.get('review') or {}).get('wb_local_review', 'unknown'))})
    # Host-bound purpose records may be saved independently of native arguments.
    for index, row in enumerate(state.get('wb_mask_usage', [])):
        used_id = row.get('actual_mask_id', row.get('mask_id'))
        used_version = row.get('child_version', row.get('settings_version'))
        if used_id == ident and used_version in ancestors:
            result.append({**_public(row), **source_ref('wb_mask_usage', index, row)})
    # Nearest ancestor wins; a late-appended old record is not the latest use.
    return sorted(result, key=lambda row: -ancestry.index(
        row.get('child_version', row.get('version', row.get('settings_version')))))


def _mask_context(state, settings, version):
    if not isinstance(settings, dict) or not isinstance(settings.get('masks'), list):
        return []
    result = []
    for ident, mask in _mask_index(settings).items():
        history = _public_mask_history(state, ident, version)
        last = history[-1] if history else {}
        prior_settings = _version_settings(state, last.get('child_version', last.get('version', last.get('settings_version'))))
        prior_mask = next((row for row in (prior_settings or {}).get('masks', []) if row.get('id') == ident), None)
        changed = (any(prior_mask.get(key) != mask.get(key) for key in ('components', 'visible', 'invert', 'adjust'))
                   if prior_mask is not None else 'unknown')
        result.append({'id': ident, 'name': mask.get('name', 'unknown'), 'simple': _simple(mask),
                       'shape': _public(mask.get('components', [])),
                       'components': _public(mask.get('components', [])),
                       'visible': mask.get('visible', 'unknown'), 'invert': mask.get('invert', 'unknown'),
                       'adjust': _public(mask.get('adjust', {})),
                       'public_purpose': [_public(last)] if history else 'unknown',
                       'purpose_refs': [{key: _public(row[key]) for key in
                                         ('source_collection', 'source_index', 'source_ref', 'history_record_id', 'id', 'operation_id',
                                          'observation_id', 'settings_version', 'version', 'child_version') if key in row}
                                        for row in history],
                       'purpose_record_count': len(history),
                       'purpose_requires_confirmation': True,
                       'historical_purpose_not_current_fact': bool(history),
                       'settings_changed_since_last_purpose': changed,
                       'purpose_confirmation_reason': 'Confirm region/purpose against this turn actual image and settings. Historical use is not current semantic proof; changed shape/visibility/inversion/adjustments need fresh confirmation, never overlap guessing.'})
    return result


def work_context(state, phase, settings, observation):
    """Detached work-packet context. Never substitutes initial settings for current."""
    source = _source(observation)
    observed_version, current_version = source['settings_version'], state.get('current')
    observed = _version_settings(state, observed_version, settings)
    current = _version_settings(state, current_version, observed if current_version == observed_version else None)
    records = state.get('wb_assessments', [])
    record = records[-1] if records else None
    latest = None
    if record is not None:
        origin = next((row for row in state.get('observations', []) if row.get('id') == record.get('observation_id')), None)
        bound = _source(origin) if origin is not None else None
        trustworthy = bool(bound and record.get('settings_version') == bound['settings_version']
                           and record.get('phase') == bound['phase'])
        same_phase = trustworthy and bound['phase'] == phase
        phase_compatible = same_phase or bool(trustworthy and bound['phase'] == 'G2' and phase == 'color')
        valid_observed = bool(phase_compatible and bound['settings_version'] == observed_version)
        valid_current = bool(phase_compatible and bound['settings_version'] == current_version)
        target = bool(trustworthy and bound['target_comparison'])
        latest = {'record': _public(record), 'source_present': origin is not None,
                  'source_binding_valid': trustworthy, 'same_phase': bool(same_phase),
                  'phase_compatible': bool(phase_compatible),
                  'valid_for_observed_version': valid_observed, 'valid_for_current_version': valid_current,
                  'source_is_target_comparison': target,
                  'reusable_target_assessment': bool(valid_observed and target),
                  'guidance': 'Parent/current effect shows a change, not author-target correctness. Reassess on the actual phase/version; never silently reuse a stale/off-branch target judgment.'}
    result = {'actual_current': {'version': current_version, **_wb(current)},
              'observed': {'version': observed_version, **_wb(observed)},
              'latest_assessment': latest,
              'step_guidance': {'binding': 'soft guidance, not a cap or required round count',
                                'kelvin_temp': 'Near 5000 K, ±150–250 K is a small step; ±500 K or more is a distinct change requiring clear evidence. These are examples, not a bound or maximum. Kelvin only when actual scale=kelvin.',
                                'tint': 'Usually ±3–6 native Tint units; negative Tint moves toward green, positive toward magenta.',
                                'rounds': 'Re-evaluate after about 2–3 small-step rounds; this is guidance, not a cap.',
                                'axis_isolation': 'Prefer one axis at a time. Separate yellow/blue temperature residual from green/magenta Tint; couple only with explicit evidence.',
                                'target_intent': 'Do not force neutral: material, lighting, photographic role and intentional cast govern qualification. No reliable reference may still justify needed WB.',
                                'atmosphere': 'Prefer grading for overall atmosphere; use local WB for a justified regional lighting residual, reusing an appropriate same-region mask.'},
              'simple_masks': _mask_context(state, observed, observed_version),
              'mask_policy': 'IDs/shape/visible/invert/adjust are actual settings. Public purpose needs explicit ancestor-linked history, otherwise unknown. Never infer same-region suitability from geometry overlap.'}
    if current_version != observed_version:
        result['current_simple_masks'] = _mask_context(state, current, current_version)
    return result
