"""Pure Tone target/context and actual-settings differences; no executor or IO.

Color attribution is carried from an actual color target observation, never
relabelled as something the grayscale Tone branch has itself seen.
"""
import math

from photo_phase_scheduler import public_copy
from photo_working_context import mask_geometry_changes, source_ref, version_ancestry


_ASSESSMENT_PHASES = ('G1', 'G2', 'color', 'G3', 'joint', 'G4')
_TONE_FIELDS = ('exposure', 'contrast', 'highlights', 'shadows', 'whites', 'blacks')
_BANDS = ('red', 'orange', 'yellow', 'green', 'aqua', 'blue', 'purple', 'magenta')


def _object(properties, required):
    return {'type': 'object', 'properties': properties, 'required': list(required),
            'additionalProperties': False}


def _text_schema():
    return {'type': 'string', 'minLength': 1, 'pattern': r'\S'}


def tone_assessment_schema():
    item = _object({**{key: _text_schema() for key in
                       ('id', 'author_region', 'current_region', 'material', 'lighting',
                        'evidence', 'protection', 'issue_id', 'group_id')},
                    'attribution': {'type': 'string', 'enum': ['structure', 'chroma', 'uncertain']},
                    'handoff': {'type': 'string', 'enum': ['color', 'joint', 'tone']}},
                   ('id', 'author_region', 'current_region', 'material', 'lighting',
                    'attribution', 'evidence', 'protection', 'handoff'))
    anchor = _object({'role': {'type': 'string', 'enum': ['black_support', 'white_support', 'midtone']},
                      **{key: _text_schema() for key in
                         ('author_region', 'current_region', 'qualification')}},
                     ('role', 'author_region', 'current_region', 'qualification'))
    result = _object({'items': {'type': 'array', 'items': item}, 'reason': _text_schema(),
                      'neutral_anchors': {'type': 'array', 'items': anchor}},
                     ('items', 'reason', 'neutral_anchors'))
    result['description'] = ('Qualitative color author/current attribution, not pixel measurements or an effect verdict. '
                             'Regions independently locate author/current material and lighting roles; descriptive text '
                             'with separate grid cells is valid. Empty items/anchors and uncertain attribution are valid. '
                             'Chroma is not a mandate to repair grayscale structure with exposure/contrast.')
    return result


def _keys(value, schema, label):
    if (not isinstance(value, dict) or set(value) - set(schema['properties'])
            or set(schema['required']) - set(value)):
        raise ValueError(label + ' requires the documented fields only')


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be a nonempty public string')


def _source(observation):
    if not isinstance(observation, dict):
        raise ValueError('Actual Tone source observation required')
    for key in ('id', 'version'):
        _text(observation.get(key), 'observation.' + key)
    meta = observation.get('stage_observation') or {}
    spec = observation.get('view_spec') or {}
    phase = observation.get('phase', meta.get('phase'))
    if phase not in (*_ASSESSMENT_PHASES, 'tone', 'complete'):
        raise ValueError('Unknown Tone observation phase')
    mode = observation.get('comparison_mode', spec.get('comparison_mode', 'target'))
    view = meta.get('view_mode', 'grayscale' if phase == 'tone' else 'color')
    return {'observation_id': observation['id'], 'settings_version': observation['version'],
            'phase': phase, 'comparison_mode': mode, 'view_mode': view,
            'target_comparison': mode == 'target' and view == 'color',
            'view_spec': public_copy(spec)}


def validate_tone_assessment(value, observation):
    """Bind only qualitative assessments of an actual color author/current view."""
    schema = tone_assessment_schema()
    _keys(value, schema, 'tone_assessment')
    _text(value['reason'], 'tone_assessment.reason')
    for field in ('items', 'neutral_anchors'):
        if not isinstance(value[field], list):
            raise ValueError('tone_assessment.' + field + ' must be a list')
        row_schema = schema['properties'][field]['items']
        seen = set()
        for row in value[field]:
            _keys(row, row_schema, field)
            for key, entry in row.items():
                _text(entry, field + '.' + key)
                if 'enum' in row_schema['properties'][key] and entry not in row_schema['properties'][key]['enum']:
                    raise ValueError('Unknown ' + field + '.' + key)
            if field == 'items':
                if row['id'] in seen:
                    raise ValueError('Tone item IDs must be unique within an assessment')
                seen.add(row['id'])
    source = _source(observation)
    if source['phase'] not in _ASSESSMENT_PHASES or not source['target_comparison']:
        raise ValueError('Tone assessment requires G1/G2/Color/G3/Joint/G4 color target evidence, not Tone/effect')
    return {**public_copy(value), **source, 'claim_type': 'model_tone_assessment_not_measured_fact'}


def _ancestry(state, version):
    return version_ancestry(state, version)


def work_context(state, phase, observation):
    """Keep applicable protection bodies; fold off-branch/unbound bodies to refs.

    Carried ancestor evidence is useful context, not current-version proof or a
    task-readiness gate. Original records are never removed from state.
    """
    source = _source(observation)
    current, observed = state.get('current'), source['settings_version']
    ancestry = _ancestry(state, current)
    _ancestry(state, observed)
    assessments, protections, source_index = [], [], []
    for index, record in enumerate(state.get('tone_assessments', [])):
        origins = [row for row in state.get('observations', [])
                   if row.get('id') == record.get('observation_id')]
        binding = None
        if len(origins) == 1:
            try:
                binding = validate_tone_assessment(
                    {key: record[key] for key in ('items', 'reason', 'neutral_anchors') if key in record}, origins[0])
            except ValueError:
                pass
        trustworthy = bool(binding and all(record.get(key) == binding[key] for key in
                           ('observation_id', 'settings_version', 'phase', 'comparison_mode',
                            'view_mode', 'target_comparison')))
        version = record.get('settings_version')
        on_branch = version in ancestry
        carried = bool(trustworthy and on_branch and phase == 'tone' and binding['phase'] == 'G1')
        compatible = bool(trustworthy and (binding['phase'] == phase or carried
                          or (binding['phase'], phase) in (('G2', 'color'), ('G3', 'joint'))))
        ref = source_ref('tone_assessments', index, record)
        entry = {'record': public_copy(record), 'source_ref': ref, 'source_present': bool(origins),
                 'source_binding_valid': trustworthy,
                 'source_is_target_comparison': bool(trustworthy and binding['target_comparison']),
                 'phase_compatible': compatible, 'on_selected_ancestry': on_branch,
                 'stale': version != current, 'off_selected_branch': not on_branch,
                 'carried_color_basis': carried,
                 'valid_for_observed_version': bool(compatible and on_branch and version == observed),
                 'valid_for_current_version': bool(compatible and on_branch and version == current),
                 'reusable_target_assessment': bool(compatible and on_branch and version == observed == current),
                 'guidance': 'Color source attribution is carried context, not Tone seeing color. Ancestor/stale/off-branch judgments need actual-version review; effect support is not target correctness.'}
        summary = {**ref, **{key: value for key, value in entry.items() if key not in ('record', 'source_ref', 'guidance')},
                   'body_folded_to_source_index': not (trustworthy and on_branch)}
        source_index.append(summary)
        if trustworthy and on_branch:
            assessments.append(entry)
            for item in record.get('items', []):
                protections.append({**public_copy(item), 'observation_id': record.get('observation_id'),
                                    'settings_version': version, 'source_binding_valid': trustworthy,
                                    'stale': entry['stale'], 'off_selected_branch': False,
                                    'carried_color_basis': carried, 'source_ref': public_copy(ref)})
    candidates = [entry for entry in assessments if entry['phase_compatible']]
    latest = max(candidates, key=lambda entry: (-ancestry.index(entry['record']['settings_version']),
                 entry['source_ref']['source_index'])) if candidates else None
    return {'actual_version': current, 'observed_version': observed, 'ancestry': ancestry,
            'view_mode': source['view_mode'], 'tone_sees_color': False if phase == 'tone' else source['view_mode'] == 'color',
            'latest_assessment': latest, 'assessment_source_index': source_index,
            'assessments': assessments, 'protected_regions': protections,
            'local_boundary_review_required': True,
            'step_guidance': {
                'binding': 'Soft photographic guidance, not a menu walk, cap or required round count.',
                'neutral_first': 'whites_blacks → exposure → highlights_shadows → contrast → master_curve → parametric_curve → local_tone → closure; qualify black/white support and midtone by material/lighting, not arbitrary extremes. Preserve intentional raised blacks/soft whites; never force black/white to 0/1 or auto-level.',
                'attribution': 'Repair structure, not chroma by proxy. A chroma-only grayscale mismatch belongs to Color/Joint; do not substitute exposure, contrast or local Tone. Uncertain attribution is valid and is not an unresolved control. Protected-region coverage does not blanket-ban global controls: justify independent structural purpose and review the protected consequence.',
                'simple_curve': 'Tone master uses [] to reset, otherwise 2–5 points with x strictly increasing, y nondecreasing and x/y in [0,1]. Organize by three tonal anchors (black support, midtone, white support); this is not a three-point minimum or fixed coordinates. Endpoints are optional, not forced to (0,0)/(1,1). Test one hypothesis; simplify or reset kinks. Joint retains its existing approved curve scope.',
                'rounds': '18 Tone opportunities are planning guidance, not a cap, minimum, timer or permission gate.',
                'local_boundary': 'For actual local Tone changes/creation/geometry, first review the actual grayscale parent/child now: target core, feather/transition neighborhood, outside region and specific protected objects, spill/halo risk and falsifier. G2 then reviews the actual color relationship and edge protection; grayscale support cannot close color protection. Attach detail to the existing operation debt, never infer semantic coverage from geometry.'}}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _masks(settings):
    masks = settings.get('masks', []) if isinstance(settings, dict) else []
    if not isinstance(masks, list):
        raise ValueError('Actual mask readback must be a list')
    result = {}
    for mask in masks:
        if (not isinstance(mask, dict) or type(mask.get('id')) is not int
                or not 0 <= mask['id'] <= 4294967295 or mask['id'] in result):
            raise ValueError('Unique actual integer mask IDs required')
        result[mask['id']] = mask
    return result


def tone_boundary_detail(before, after, arguments, expectation):
    """Actual mask differences only; caller augments one existing operation debt."""
    left, right = _masks(before), _masks(after)
    args = arguments.get('arguments', arguments) if isinstance(arguments, dict) else {}
    plan = args.get('mask_plan') or {}
    explicit_tone = set()
    plan_tone = isinstance(plan, dict) and isinstance(plan.get('values'), dict) and bool(set(plan['values']).intersection(_TONE_FIELDS))
    if plan_tone and type(plan.get('id')) is int:
        explicit_tone.add(plan['id'])
    if plan_tone and plan.get('action') == 'create':
        explicit_tone.update(right.keys() - left.keys())
    for command in args.get('commands', []):
        if not isinstance(command, dict):
            continue
        params = command.get('params', {})
        if (command.get('id') == 'mask.adjust' and isinstance(params.get('values'), dict)
                and set(params['values']).intersection(_TONE_FIELDS) and type(params.get('id')) is int):
            explicit_tone.add(params['id'])
    changes = []
    geometry_changes = {row['mask_id']: row for row in mask_geometry_changes(before, after)}
    for ident in sorted(left.keys() | right.keys()):
        old, new = left.get(ident), right.get(ident)
        previous, current = (old or {}).get('adjust', {}), (new or {}).get('adjust', {})
        tone_changed = [key for key in _TONE_FIELDS if previous.get(key, 0) != current.get(key, 0)]
        geometry = geometry_changes.get(ident, {}).get('changed_fields', [])
        nonzero_tone = any(_finite(row.get(key)) and row[key] != 0
                           for row in (previous, current) for key in _TONE_FIELDS)
        created, deleted = old is None, new is None
        if not (tone_changed or (nonzero_tone or ident in explicit_tone) and (geometry or created or deleted)):
            continue
        changes.append({'mask_id': ident, 'created': created, 'deleted': deleted,
                        'changed_fields': [*['adjust.' + key for key in tone_changed], *geometry],
                        'before': public_copy(old), 'after': public_copy(new)})
    if not changes:
        return None
    expected = expectation.get('effect_expectation', expectation) if isinstance(expectation, dict) else {}
    return {'mask_ids': [row['mask_id'] for row in changes], 'changes': changes,
            **{key: public_copy(expected.get(key, 'unknown')) for key in ('protected_region', 'risk', 'falsifier')},
            'claim_type': 'actual_tone_mask_change_not_boundary_verdict'}


def color_luminance_detail(before, after):
    """Report only finite actual mixer-band luminance differences; zero is no fact."""
    result = []
    for band in _BANDS:
        old = (before.get('mixer', {}).get(band, {}) if isinstance(before, dict) else {}).get('lum')
        new = (after.get('mixer', {}).get(band, {}) if isinstance(after, dict) else {}).get('lum')
        if _finite(old) and _finite(new) and old != new:
            result.append({'control_id': 'mixer.' + band + '.lum', 'before': old, 'after': new,
                           'delta': new - old, 'claim_type': 'actual_settings_change_not_visual_judgment'})
    return result
