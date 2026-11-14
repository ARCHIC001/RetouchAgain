"""Detached working-memory projections. Only record_branch_change appends state.

No executor, bank, filesystem, visual inference, or automatic mask selection.
"""
import copy
from decimal import Decimal
import math


def version_ancestry(state, version):
    """Selected-to-root actual IDs; reject missing links and cycles."""
    if version is None:
        raise ValueError('Actual working-context version required')
    result, seen = [], set()
    while version is not None:
        if version in seen:
            raise ValueError('Cyclic working-context version ancestry')
        row = state.get('versions', {}).get(version)
        if not isinstance(row, dict):
            raise ValueError('Unknown working-context version: ' + str(version))
        seen.add(version)
        result.append(version)
        version = row.get('parent')
    return result


def source_ref(collection, index, record):
    """Stable append-only source address, without carrying historical prose."""
    return {'source_collection': collection, 'source_index': index,
            'source_ref': collection + ':' + str(index),
            'history_record_id': record.get('id') or ('action-' + str(index) if collection == 'history' else collection + ':' + str(index)),
            **{key: copy.deepcopy(record[key]) for key in
               ('id', 'operation_id', 'observation_id', 'source_observation_id',
                'settings_version', 'version', 'child_version', 'parent', 'phase') if key in record}}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _point(value):
    if isinstance(value, dict):
        values = (value.get('x'), value.get('y'))
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        values = value
    else:
        return None
    return list(values) if all(_finite(v) for v in values) else None


def _geometry(shape):
    if not isinstance(shape, dict):
        return None
    kind = shape.get('kind')
    if kind == 'linear':
        start, end = _point(shape.get('start')), _point(shape.get('end'))
        if start is None or end is None or start == end:
            return None
        return {'kind': kind, 'start': start, 'end': end,
                'midpoint': [float((Decimal(str(a)) + Decimal(str(b))) / 2) for a, b in zip(start, end)],
                'position_units': 'normalized_source_xy',
                'center_definition': 'gradient_segment_midpoint_not_coverage_center'}
    if kind == 'radial':
        center = _point(shape.get('center'))
        if (center is None or any(not _finite(shape.get(key)) for key in ('rx', 'ry', 'angle', 'feather'))
                or shape['rx'] <= 0 or shape['ry'] <= 0 or not 0 <= shape['feather'] <= 100):
            return None
        return {'kind': kind, 'center': center,
                **{key: shape[key] for key in ('rx', 'ry', 'angle', 'feather')},
                'position_units': 'normalized_source_xy', 'radius_units': 'source_long_edge_fraction',
                'angle_units': 'degrees', 'feather_units': 'percent'}
    return None


def _masks(settings):
    from photo_white_balance import _mask_index
    if not isinstance(settings, dict) or 'masks' not in settings:
        return {}
    return _mask_index(settings)


def _simple_geometry(mask):
    from photo_white_balance import _simple
    return _geometry(mask['components'][0]['shape']) if _simple(mask) else None


def _center(geometry):
    return geometry['midpoint'] if geometry['kind'] == 'linear' else geometry['center']


def geometry_proximity(settings, plan=None, threshold=.1):
    """Same-kind normalized center distance strictly below threshold: warning only.

    A prospective create is compared to actual masks, never assigned a fake ID.
    Complex/inverted/unsupported masks have no guessed representative geometry.
    """
    if not _finite(threshold) or threshold <= 0:
        raise ValueError('Geometry proximity threshold must be finite and positive')
    rows = [(ident, geometry) for ident, mask in _masks(settings).items()
            if (geometry := _simple_geometry(mask)) is not None]
    pairs = [(left, right, False) for index, left in enumerate(rows) for right in rows[index + 1:]]
    if isinstance(plan, dict) and plan.get('action') == 'create':
        shape = {**plan.get('geometry', {}), 'kind': plan.get('kind')} if isinstance(plan.get('geometry'), dict) else None
        prospective = _geometry(shape)
        if prospective is not None:
            pairs.extend((row, (None, prospective), True) for row in rows)
    result = []
    for (left_id, left), (right_id, right), prospective in pairs:
        if left['kind'] != right['kind']:
            continue
        centers = [_center(left), _center(right)]
        distance = math.dist(*centers)
        # Native JSON decimal coordinates must not turn an exact .1 into < .1.
        squared = sum((Decimal(str(a)) - Decimal(str(b))) ** 2 for a, b in zip(*centers))
        if squared < Decimal(str(threshold)) ** 2:
            row = {'kind': left['kind'], 'mask_ids': [left_id] if prospective else [left_id, right_id],
                   'centers': copy.deepcopy(centers), 'distance': distance, 'threshold': threshold,
                   'center_definition': 'gradient_segment_midpoint_not_coverage_center' if left['kind'] == 'linear' else 'radial_center',
                   'prospective': prospective, 'semantic_overlap': False, 'automatic_rejection': False,
                   'claim_type': 'geometry_proximity_warning_not_semantic_overlap',
                   'notice': 'Nearby geometry may merit reuse review; it does not prove the same region or purpose. Never auto-select, merge or reject.'}
            if prospective:
                row['prospective_geometry'] = copy.deepcopy(right)
            result.append(row)
    return sorted(result, key=lambda row: row['distance'])


def mask_geometry_changes(before, after):
    """Actual IDs and components/visible/invert changes only, without semantics."""
    left, right = _masks(before), _masks(after)
    result = []
    for ident in sorted(left.keys() | right.keys()):
        old, new = left.get(ident), right.get(ident)
        fields = [key for key in ('components', 'visible', 'invert')
                  if (key in (old or {}), (old or {}).get(key)) != (key in (new or {}), (new or {}).get(key))]
        if old is None or new is None or fields:
            result.append({'mask_id': ident, 'created': old is None, 'deleted': new is None,
                           'changed_fields': fields,
                           'before': {key: copy.deepcopy(old[key]) for key in ('components', 'visible', 'invert') if key in old} if old is not None else None,
                           'after': {key: copy.deepcopy(new[key]) for key in ('components', 'visible', 'invert') if key in new} if new is not None else None,
                           'claim_type': 'actual_mask_geometry_change_not_semantic_verdict'})
    return result


def _dimensions(source_dimensions):
    if source_dimensions is None:
        return None
    if isinstance(source_dimensions, dict):
        values = (source_dimensions.get('width'), source_dimensions.get('height'))
    elif isinstance(source_dimensions, (list, tuple)) and len(source_dimensions) == 2:
        values = source_dimensions
    else:
        raise ValueError('Actual oriented source width/height required')
    if any(not _finite(v) or v <= 0 for v in values):
        raise ValueError('Actual oriented source dimensions must be positive and finite')
    return {'width': values[0], 'height': values[1], 'aspect_ratio': values[0] / values[1]}


def active_masks_summary(state, observation, source_dimensions=None):
    """All readback masks, not UI activeMask; selected and observed stay distinct."""
    from photo_white_balance import _mask_context
    if not isinstance(observation, dict):
        raise ValueError('Actual working-context observation required')
    dimensions = _dimensions(source_dimensions)
    selected, observed = state.get('current'), observation.get('version')
    def project(version):
        version_ancestry(state, version)
        settings = state['versions'][version].get('settings')
        rows = _mask_context(state, settings, version)
        for row in rows:
            mask = _masks(settings)[row['id']]
            geometry = _simple_geometry(mask)
            row['components'] = copy.deepcopy(mask.get('components', []))
            row['geometry_display'] = {**geometry, 'source_dimensions': copy.deepcopy(dimensions)} if geometry else None
            row['geometry_status'] = 'supported_simple' if geometry else 'complex_or_unsupported_geometry_not_inferred'
        return {'version': version, 'settings_source': 'versions[' + str(version) + '].settings',
                'readback_available': isinstance(settings, dict) and isinstance(settings.get('masks'), list),
                'masks': rows, 'geometry_warnings': geometry_proximity(settings)}
    return {'selected': project(selected),
            'observed': {**project(observed), 'observation_id': observation.get('id')},
            'selected_is_observed': selected == observed,
            'source_dimensions': dimensions,
            'notice': 'Actual settings identity, not UI selection or semantic coverage. Hidden, zero-adjustment and complex masks remain listed; historical purpose needs image confirmation.'}


def _operation_rows(state, chain):
    rows = []
    for version in chain:
        actual = state['versions'][version]
        matches = [(index, row) for index, row in enumerate(state.get('history', []))
                   if row.get('action') == 'edit' and row.get('version') == version
                   and row.get('parent') == actual.get('parent')
                   and actual.get('operation_id') is not None
                   and row.get('operation_id') == actual['operation_id']]
        rows.append({'version': version, 'parent': actual.get('parent'),
                     'operation_id': actual.get('operation_id'),
                     'changed_controls': copy.deepcopy(matches[-1][1].get('changed_controls')) if matches else None,
                     'operation_refs': [source_ref('history', index, row) for index, row in matches],
                     'claim_type': 'actual_version_operation_not_proposal'})
    return rows


def selected_baseline(state, observation):
    """Root-to-selected fact chain; inactive references are not failure verdicts."""
    selected, observed = state.get('current'), observation.get('version')
    chain = list(reversed(version_ancestry(state, selected)))
    version_ancestry(state, observed)
    active = set(chain)
    inactive_versions = [{'version': version, 'parent': row.get('parent'), 'operation_id': row.get('operation_id'),
                          'status': 'not_active_on_selected_chain_not_failure'}
                         for version, row in state.get('versions', {}).items() if version not in active]
    inactive_operations = [source_ref('history', index, row)
                           for index, row in enumerate(state.get('history', []))
                           if row.get('action') == 'edit' and row.get('version') not in active]
    relevant = [(index, row) for index, row in enumerate(state.get('branch_reset_events', []))
                if row.get('to_version') in active]
    reset = None
    if relevant:
        index, event = relevant[-1]
        reset = {'title': 'BRANCH RESET NOTICE', 'event': copy.deepcopy(event),
                 'event_ref': source_ref('branch_reset_events', index, event),
                 'selected_version': selected, 'observed_version': observed,
                 'selected_is_observed': selected == observed,
                 'notice': 'Use selected actual settings and its chain as baseline. Operations outside it are currently inactive, not failed or deleted; historical proposals are not executed facts.'}
    return {'selected_version': selected, 'observed_version': observed,
            'selected_is_observed': selected == observed, 'chain': _operation_rows(state, chain),
            'source_current_settings': 'versions[' + str(selected) + '].settings',
            'excluded_inactive_versions': inactive_versions, 'excluded_inactive_operations': inactive_operations,
            'branch_reset_notice': reset,
            'branch_reset_refs': [source_ref('branch_reset_events', index, row)
                                  for index, row in enumerate(state.get('branch_reset_events', []))],
            'notice': 'Selected settings are authoritative. Observing a different version does not select it. Proposed controls and prior absolute values are not facts or an instruction to replay.'}


def record_branch_change(state, before_version, action_name, source_observation_id):
    """Call only after success; same/direct child is normal progress, not reset.

    Append-only metadata, never an executor or snapshot side effect. Returned data
    is detached; unchanged snapshots cannot create another event.
    """
    after = state.get('current')
    before_chain, after_chain = version_ancestry(state, before_version), version_ancestry(state, after)
    if before_version == after or state['versions'][after].get('parent') == before_version:
        return None
    if not isinstance(action_name, str) or not action_name.strip():
        raise ValueError('Successful branch-change action name required')
    if not isinstance(source_observation_id, str) or not source_observation_id.strip():
        raise ValueError('Branch-change source observation ID required')
    relation = 'ancestor' if after in before_chain else 'descendant' if before_version in after_chain else 'branch_switch'
    events = state.get('branch_reset_events', [])
    if not isinstance(events, list):
        raise ValueError('branch_reset_events must be an append-only list')
    identity = {'from_version': before_version, 'to_version': after, 'relation': relation,
                'action_name': action_name, 'source_observation_id': source_observation_id}
    # Retried delivery of the same completed call must not duplicate its event.
    if events and all(events[-1].get(key) == value for key, value in identity.items()):
        return copy.deepcopy(events[-1])
    number = len(events) + 1
    used_ids = {row.get('id') for row in events}
    while 'branch_reset_' + str(number).zfill(5) in used_ids:
        number += 1
    event = {'id': 'branch_reset_' + str(number).zfill(5), **identity,
             'claim_type': 'actual_selected_version_change_not_visual_verdict'}
    state.setdefault('branch_reset_events', []).append(event)
    return copy.deepcopy(event)


def compact_tool_result(result):
    """Fold only advance's implicit history; explicit history/detail_get is intact."""
    value = copy.deepcopy(result)
    if (not isinstance(value, dict) or value.get('kind') != 'history'
            or value.get('projection_origin') != 'internal_advance'):
        return value
    rows = value.get('records')
    if isinstance(rows, list):
        value['records'] = [{key: copy.deepcopy(row[key]) for key in ('id', 'kind', 'record_id', 'source_ref') if key in row}
                            for row in rows]
        value['body_folded_to_source_index'] = True
    return value
