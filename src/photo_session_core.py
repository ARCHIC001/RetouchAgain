"""Current reusable module-session behavior, without legacy controllers or CLI.

Source: src/dsh_module_worker.py before archival. StageSession owns its build,
admission, native limits and resource projections. CONTRACT labels only the
shared public record shape; it is not an old experiment execution identity.
"""
import copy
import json
import sys
import uuid
from pathlib import Path

import photo_session_base as original
from photo_session_base import BasePhotoSession, PhotoSession
import lab_timing_experiment as lab
import photo_native_commands as commands
from photo_capability_catalog import build_catalog, discover, _check, STATIC_CONTROL_IDS, POINT_FIELDS
from photo_image_contract import inspect_image
from photo_textbook import public_only
from dsh_photo_strict_execute import apply_strict, contract_operation_modules
from dsh_photo_module_policy import policy_context, validate_module_review, canonical_module, MODULE_ALIASES

FOUNDATION = 'dsh-native-memory-v18-20261111'
CONTRACT = 'dsh-module-use/v3'
MODULES = ['light', 'wb', 'basis', 'mixer_point', 'curves', 'grading', 'effects', 'masks']

def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


def schemas():
    result = original.schemas()
    short = {'type': 'string', 'minLength': 1}
    decision = obj({'module': {'type': 'string', 'enum': MODULES + list(MODULE_ALIASES)},
                    'choice': {'type': 'string', 'enum': ['use', 'avoid', 'uncertain']},
                    'reason': {'type': 'string', 'minLength': 1}}, ['module', 'choice', 'reason'])
    expectation = obj({k: short for k in ['target', 'expected_change', 'protected_region', 'risk', 'falsifier']},
                      ['target', 'expected_change', 'protected_region', 'risk', 'falsifier'])
    review = obj({'expectation_id': {'type': 'string', 'minLength': 1},
                  'outcome': {'type': 'string', 'enum': ['supported', 'contradicted', 'uncertain', 'not_visible', 'unknown', 'effect_not_applicable']},
                  'evidence': short}, ['expectation_id', 'outcome', 'evidence'])
    for name, spec in result.items():
        spec['additionalProperties'] = False
        # The historical schema shares review objects across branches. Detach
        # each branch before adding edit-only requirements.
        spec['properties']['review'] = copy.deepcopy(spec['properties']['review'])
        spec['properties']['review']['additionalProperties'] = False
        prop = spec['properties']['review']['properties']
        # This independent comparison uses B in both arms; do not transmit the
        # unrelated C-memory update schema six times.
        for field in ('memory_updates', 'preferred_version', 'preference_reason'):
            prop.pop(field, None)
        if name != 'photo_edit':
            prop.pop('previous_outcome', None)
        if name not in ('photo_discover', 'photo_unable'):
            prop['effect_review'] = review
        if name == 'photo_finish':
            prop['previous_outcome'] = {'type': 'string', 'enum': sorted(original.memory.OUTCOMES)}
        if name == 'photo_edit':
            prop.update(module_decisions={'type': 'array', 'minItems': 1, 'items': decision},
                        effect_expectation=expectation)
            spec['required'] = list(dict.fromkeys(spec['required'] + ['review', 'rationale', 'predicted_effect']))
            spec['properties']['review']['required'] = ['module_decisions', 'effect_expectation']
        if name == 'photo_discover':
            spec['required'] = [key for key in spec['required'] if key != 'query']
            spec['properties'].update(kind={'type': 'string', 'enum': ['catalog', 'rule']},
                exact_ids={'type': 'array', 'items': {'type': 'string'}},
                result_id={'type': 'string'},
                module={'type': 'string', 'enum': MODULES + list(MODULE_ALIASES)})
        if name == 'photo_observe':
            spec['properties']['comparison_mode'] = {'type': 'string', 'enum': ['target', 'effect']}
    return result


def append(path, row):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def runtime_catalog_projection(value, exact=False):
    """List compact interfaces; explicit IDs retain full execution contracts."""
    def row(item):
        keys = ('id', 'kind', 'module', 'value', 'min', 'max', 'units', 'availability', 'blockers', 'shape_kind')
        result = {k: copy.deepcopy(item[k]) for k in keys if k in item}
        if exact:
            # Numeric slider contracts are fully represented by ID/value/range/
            # units. Repeating aliases, source prose and the same number schema
            # for every exact control defeats the bounded discovery contract.
            fields = ('prerequisites',) if item.get('kind') == 'control' else ('schema', 'result_contract', 'prerequisites')
            for k in fields:
                if k in item: result[k] = copy.deepcopy(item[k])
            if item.get('shape_kind') and 'oneOf' in result.get('schema', {}):
                kind = item['shape_kind']
                narrowed = [branch for branch in result['schema']['oneOf'] if
                    branch.get('properties', {}).get('kind', {}).get('enum') == [kind]
                    or branch.get('properties', {}).get('kind', {}).get('const') == kind]
                if narrowed: result['schema']['oneOf'] = narrowed
        return result
    output = {k: copy.deepcopy(v) for k, v in value.items() if k not in
              ('items', 'exact_results', 'requested_ids', 'offset', 'page_size', 'exact_id', 'matched_blocked')}
    if 'items' in value:
        if exact:
            output['items'] = [row(item) for item in value['items']]
        else:
            output['columns'] = ['id', 'value', 'min', 'max', 'unit', 'availability']
            listed = value['items'][:2]
            output['items'] = [[item['id'], item.get('value'), item.get('min'), item.get('max'),
                                item.get('units') if item.get('kind') == 'control' else 'query exact command contract',
                                item['availability']] for item in listed]
            output['deferred_ids'] = [item['id'] for item in value['items'][2:]]
            blocked = {item['id']: item['blockers'] for item in listed if item.get('blockers')}
            if blocked: output['blockers_by_id'] = blocked
            output['details'] = 'exact_ids fetches deferred full contracts; native units, not effect percentages.'
    if 'exact_results' in value:
        parts = value['exact_results']
        if all(all(item.get('kind') == 'control' for item in part.get('items', [])) for part in parts):
            output['columns'] = ['id', 'value', 'min', 'max', 'unit', 'availability']
            output['control_rows'] = []
            output['exact_results'] = []
            for part in parts:
                indices = []
                for item in part.get('items', []):
                    indices.append(len(output['control_rows']))
                    output['control_rows'].append([item['id'], item.get('value'), item.get('min'), item.get('max'),
                                                   item.get('units'), item['availability']])
                output['exact_results'].append({'requested_id': part['requested_id'], 'status': part['status'], 'rows': indices})
            special = {item['id']: {k: copy.deepcopy(item[k]) for k in ('blockers', 'prerequisites') if item.get(k)}
                       for part in parts for item in part.get('items', []) if item.get('blockers') or item.get('prerequisites')}
            if special: output['control_constraints'] = special
        else:
            output['exact_results'] = [{'requested_id': part['requested_id'], 'status': part['status'],
                'items': [row(item) for item in part.get('items', [])]} for part in parts]
    # Never truncate an execution contract. An oversized command batch exposes
    # its first complete contract and explicit exact-ID followups for the rest.
    if len(json.dumps(output, ensure_ascii=False).encode('utf-8')) > 2300 and output.get('exact_results') and 'control_rows' not in output:
        parts = output['exact_results']
        first = next((part for part in parts if part.get('items')), None)
        if first is not None:
            output['exact_results'] = [first] + [{'requested_id': part['requested_id'], 'status': part['status'],
                'contract_body': 'not_projected', 'fetch': {'kind': 'catalog', 'exact_ids': [part['requested_id']]}}
                for part in parts if part is not first]
    return output


def runtime_policy_projection(packet):
    """Keep full lesson, prerequisites and scope; archive IDs replace duplication."""
    result = {k: copy.deepcopy(packet[k]) for k in
              ('schema', 'module_index', 'status', 'blockers', 'selection_reason', 'interface_facts', 'truncated') if k in packet}
    result['matches'] = []
    for match in packet.get('matches', []):
        compact = {k: copy.deepcopy(match[k]) for k in ('id', 'module', 'kind', 'verification_level', 'exact_ids',
            'native_units', 'required_settings', 'required_dependencies', 'commands', 'applicable_scope', 'known_limits') if k in match}
        card = match.get('textbook_card')
        if card:
            compact['approvedlesson'] = card['approvedlesson']['text']
            compact['card_id'] = card['id']
        compact['evidence_ref'] = 'knowledge/module_policy_index.json#' + match['id']
        result['matches'].append(compact)
    # Counts are useful initially; exact-rule packets need not repeat all counts.
    if result['matches']:
        result.pop('module_index', None)
        result.pop('interface_facts', None)  # Exact units remain inside each rule.
        result.pop('selection_reason', None)
        result.pop('schema', None)
    return result


def operation_exact_ids(arguments):
    """Resolve explicitly supplied settings leaves, never infer numeric values."""
    targets = list(arguments.get('values', {}))
    aliases = {'curve.split_shadows': 'curve.splitShadows', 'curve.split_mid': 'curve.splitMid',
               'curve.split_highlights': 'curve.splitHighlights', 'curve.refine_saturation': 'curve.refineSaturation'}
    for name in ('shadows_tint', 'red_hue', 'red_sat', 'green_hue', 'green_sat', 'blue_hue', 'blue_sat'):
        first, second = name.split('_')
        aliases['calibration.' + name] = 'calibration.' + first + second.title()
    for name in POINT_FIELDS:
        snake = ''.join('_' + c.lower() if c.isupper() else c for c in name)
        for index in range(8):
            aliases['point_colors.' + str(index) + '.' + snake] = 'pointColor.' + str(index) + '.' + name
    def visit(path, value):
        if isinstance(value, dict):
            for key, child in value.items(): visit(path + '.' + key if path else key, child)
        elif isinstance(value, list):
            if path == 'point_colors':
                for index, child in enumerate(value): visit(path + '.' + str(index), child)
            elif path in ('curve.master', 'curve.red', 'curve.green', 'curve.blue'):
                targets.append('develop.curve')
        else:
            ident = aliases.get(path, path)
            if ident in STATIC_CONTROL_IDS or ident.startswith('pointColor.'):
                targets.append(ident)
    visit('', arguments.get('settings', {}))
    for command in arguments.get('commands', []):
        params = command.get('params', {})
        shape = params['shape'].get('kind') if isinstance(params.get('shape'), dict) else params.get('kind')
        targets.append(command['id'] + (':' + shape if isinstance(shape, str) else ''))
    return list(dict.fromkeys(targets))


class BoundedBackend:
    def __init__(self, session):
        self.session = session
        self._close_error = None
        self.native = lab.Native(session.folder, session.input['source'])
        self.trace = []
        try:
            self.dependencies = commands.dependencies(self.native)
        except Exception as exc:
            try:
                self.close()
            except Exception as cleanup:
                session.native = self  # Keep failed cleanup reachable by the worker's finalizer.
                exc.add_note('BoundedBackend initialization cleanup failed: ' + repr(cleanup))
            raise

    def __getattr__(self, name):
        return getattr(self.native, name)

    def apply(self, base, args):
        raise lab.LocalDispatchDenied('use StageSession admission')

    def render(self, path, long_edge=0, fmt='png'):
        raise lab.LocalDispatchDenied('use StageSession admission')


    def close(self):
        if self.__dict__.get('_close_error') is not None:
            raise self._close_error
        original_error = sys.exception()
        try:
            self.native.close()
        except Exception as exc:
            import os
            import time
            self._close_error = exc
            marker = self.session.folder / 'native-cleanup-unknown.json'
            evidence = {'schema': 'native-cleanup-unknown/v1', 'time': time.time(), 'exit_known': False,
                        'original_error': None if original_error is None else repr(original_error),
                        'cleanup_errors': [{'step': 'BoundedBackend.close', 'error': repr(exc)}],
                        'resume': 'Offline native reconciliation required; retain the worker lease'}
            try:
                with marker.open('x', encoding='utf-8') as stream:
                    json.dump(evidence, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                pass
            except Exception as marker_error:
                exc.add_note('Native cleanup marker failed: ' + repr(marker_error))
            self.session.state.update(status='engineering_diagnosis_required', native_cleanup_unknown=evidence,
                                      interruption=repr(original_error if original_error is not None else exc))
            try:
                self.session.save()
            except Exception as checkpoint_error:
                exc.add_note('Native cleanup checkpoint failed: ' + repr(checkpoint_error))
            raise


class ModuleSession(original.PhotoSession):
    def validate_protocol(self, protocol, job):
        raise lab.LocalDispatchDenied('use StageSession admission')

    def _initialize_photo_session(self, round_dir, job):
        raise lab.LocalDispatchDenied('use StageSession admission')


    def _action_schema(self, name):
        return schemas().get(name)

    def _validate_public_review(self, review):
        return validate_module_review(review)

    def normalize_public_review(self, review):
        return original.normalize_review(review, self.state)

    def _record_public_review(self, review, observation):
        return original.memory.record(self.state, review, observation)

    def prepare_edit_rule_check(self):
        return True

    def _operation_modules(self, arguments):
        return contract_operation_modules(arguments)

    def _operation_fields(self):
        return ('values', 'settings', 'commands')

    def _validate_forwarded_edit(self, arguments):
        lab.validate_action({'action': 'edit', 'arguments': arguments}, 'step', allow_empty_review=True)

    def _state_only_operation(self, arguments):
        return bool(arguments.get('commands')) and not arguments.get('values') and not arguments.get('settings') and all(c['id'] in
            {'mask.add', 'mask.addComponent', 'mask.brushStroke', 'mask.update', 'mask.sampleColor',
             'mask.refine', 'mask.select', 'mask.rename', 'mask.move', 'pointColor.pick', 'pointColor.delete'}
            for c in arguments['commands'])

    def _history_result(self, arguments, observation):
        module_result = self.module_history(ids=arguments.get('ids', ()), query=arguments.get('query', ''),
            version=arguments.get('version'), offset=arguments.get('offset', 0), page_size=arguments.get('page_size', 5))
        wanted = arguments.get('ids', [])
        module_ids = {r['id'] for r in module_result['records']}
        legacy_ids = [ident for ident in wanted if ident not in module_ids]
        legacy_result = ({'items': [], 'next_offset': None} if wanted and not legacy_ids else
            original.memory.history(self.state, ids=legacy_ids or None, query=arguments.get('query'),
                version=arguments.get('version'), offset=arguments.get('offset', 0), page_size=arguments.get('page_size', 5)))
        return {'kind': 'history', **legacy_result, 'module_history': module_result}

    def _catalog_discover(self, *args, **kwargs):
        return discover(*args, **kwargs)

    def _discovery_policy_context(self, *args, **kwargs):
        return policy_context(*args, **kwargs)

    def _policy_projection(self, packet):
        return runtime_policy_projection(packet)

    def _check_discovery_repeat(self, query, observation):
        if any(h.get('action') == 'module_discover' and h.get('version') == observation['version'] and h.get('request') == query for h in self.state['history']):
            raise original.NoProgress('Repeated exact discovery at unchanged version')

    def _dispatch_photo_action(self, name, arguments, source_observation_id):
        return super().act(name, arguments, source_observation_id)

    def __init__(self, round_dir, job):
        protocol = lab.read(Path(round_dir) / 'protocol.json')
        self.validate_protocol(protocol, job)
        self.support = job['condition'] == 'module_support'
        self._initialize_photo_session(round_dir, job)
        self.state.setdefault('module_contract', CONTRACT)
        self.state.setdefault('effect_expectations', [])
        self.state.setdefault('module_public_records', [])
        if self.state['module_contract'] != CONTRACT:
            raise ValueError('Cannot change active module contract')
        self.save()

    def backend(self):
        if self.native is None:
            self.native = BoundedBackend(self)
        return self.native

    def catalog(self):
        backend = self.backend()
        controls = backend.controls()
        controls = controls.get('controls', []) if isinstance(controls, dict) else controls
        return build_catalog(controls, backend.dependencies['commands'], backend.dependencies)

    def observe(self, *args, **kwargs):
        spec = kwargs.get('args') or {}
        mode = spec.get('comparison_mode', 'target')
        if mode != 'effect':
            item = super().observe(*args, **kwargs)
            item['comparison_mode'] = 'target'; self.save()
            return item
        version = (args[0] if args else None) or self.state['current']
        v = self.state['versions'].get(version)
        if not v or not v.get('parent'):
            raise ValueError('Effect comparison needs an actual parent version')
        if spec.get('reference_roi') is not None:
            raise ValueError('Effect comparison uses candidate_roi identically on parent/current, not author ROI')
        roi = spec.get('candidate_roi')
        if roi is not None:
            lab.pixel_box((10000, 10000), roi)
        edge = min(spec.get('side', 384 if roi else 512), 384 if roi else 512)
        identity = {'comparison_mode': 'effect', 'candidate_roi': roi, 'side': edge}
        previous = next((o for o in reversed(self.state['observations']) if o.get('version') == version
                         and o.get('view_spec') == identity), None)
        if previous and self.state['observations'][-1].get('revisit_of', self.state['observations'][-1]['id']) == previous['id']:
            raise original.NoProgress('Repeated effect comparison without new evidence')
        ident = 'o' + str(len(self.state['observations']) + 1).zfill(4)
        folder = self.folder / 'observations' / ident; folder.mkdir(parents=True, exist_ok=False)
        views = []
        for index, (vid, label) in enumerate([(v['parent'], '实际父版本'), (version, '实际当前版本')]):
            image = lab.srgb(self.state['versions'][vid]['master'], True)
            if roi is not None:
                image = image.crop(lab.pixel_box(image.size, roi))
            shown = lab.thumb(image, edge)
            path = folder / ('view-' + str(index) + '.png'); lab.save_srgb_png(shown, path)
            inspect_image(path, require_srgb=True, require_upright=True, max_side=edge)
            views.append((str(path), label + ' ' + vid + '；非作者目标'))
        item = {'id': ident, 'version': version, 'parent': v['parent'], 'artifact': 'canonical',
                'path': v['master'], 'views': views, 'comparison_mode': 'effect', 'view_spec': identity,
                'question': spec.get('question', '核对实际同源编辑后果'), 'requested': True,
                'decision_judgment': None, 'numeric_disclosed': False}
        if previous:
            item.update(reused=True, revisit_of=previous['id'])
        self.state['observations'].append(item); self.save()
        return item

    def combine_observations(self, ids):
        selected = [o for o in self.state['observations'] if o['id'] in ids]
        if any(o.get('comparison_mode', 'target') != 'target' for o in selected):
            raise ValueError('Effect/target comparisons cannot share an observation batch')
        return super().combine_observations(ids)

    def record_decision(self, observation, result):
        """A tool call is not evidence that the model compared its pictures."""
        judgment = result['arguments']['review']
        explicit = bool(judgment.get('visible_comparison', '').strip())
        observation['last_review_quality'] = 'explicit' if explicit else 'missing_visible_comparison'
        if explicit:
            observation['decision_judgment'] = copy.deepcopy(judgment)
            observation['review_submitted'] = True
            self.state['reviewed'] = list(dict.fromkeys(self.state['reviewed'] + [observation['version']]))
        self.state['history'].append({'observation_id': observation['id'], 'version': observation['version'],
            'phase': 'integrated_decision', 'judgment': judgment, 'next_action': result['action'],
            'review_submitted': explicit, 'claim_type': 'model_judgment'})
        self.save()

    def evidence_state(self, observation):
        decisions = self.state.get('public_decisions', [])
        latest = next((d for d in reversed(decisions) if d.get('observation_id') == observation['id']
                       and d.get('visible_comparison', '').strip()), None)
        sources = set(self.state.get('model_action_source_observations', []))
        def view(item):
            return {'id': item['id'], 'version': item['version'], 'parent': item.get('parent'),
                    'mode': item.get('comparison_mode', 'target'),
                    'spec': {k: copy.deepcopy(v) for k, v in item.get('view_spec', {}).items() if v is not None},
                    'reused': bool(item.get('reused')), **({'revisit_of': item['revisit_of']} if item.get('revisit_of') else {}),
                    'request_evidence': 'model_action_source' if item['id'] in sources else 'prepared_for_projection'}
        recent = [view(o) for o in self.state['observations'] if o['id'] != observation['id']
                  and o.get('artifact') in ('canonical', 'export', 'batch')][-2:]
        return {'active_version': self.state['current'], 'displayed_version': observation['version'],
                'current_view': view(observation), 'image_roles': [role for _, role in observation['views']],
                'judgment': {'status': 'submitted' if latest else 'not_submitted',
                             'decision_id': latest['id'] if latest else None,
                             'claim_type': 'model_judgment'},
                'recent_views': recent,
                'repeat_guard': 'Same view adds no evidence; question alone changes nothing.',
                'repeat_will_pause': bool(observation.get('reused'))}

    def module_history(self, ids=(), query='', version=None, offset=0, page_size=5):
        if version is not None and version not in self.state['versions']:
            raise ValueError('Unknown module history version')
        rows = []
        for key, kind in [('effect_expectations', 'prediction'), ('effect_reviews', 'effect_review'),
                          ('module_rule_archive', 'discovery'), ('module_public_records', 'module_decision')]:
            for index, item in enumerate(self.state.get(key, [])):
                ident = item.get('id', item.get('expectation_id', kind + '-' + str(index)))
                source_version = item.get('child_version', item.get('version'))
                if ids and ident not in ids:
                    continue
                if version is not None and version not in (source_version, item.get('base_version')):
                    continue
                if query and query.casefold() not in json.dumps(item, ensure_ascii=False).casefold():
                    continue
                # A discovery's complete archived selection is not another
                # delivered lesson. Its exact result can be focused explicitly.
                value = ({k: copy.deepcopy(v) for k, v in item.items() if k not in ('result', 'runtime_result')}
                         if kind == 'discovery' else {k: copy.deepcopy(v) for k, v in item.items() if k != 'execution_trace'})
                if kind == 'discovery':
                    value['matched_rule_ids'] = [m['id'] for m in item['result'].get('matches', [])]
                    value['result_status'] = item['result']['status']
                    value['focus_with'] = {'kind': item['request'].get('kind', 'catalog'), 'result_id': ident}
                rows.append({'id': ident, 'kind': kind, 'record': value,
                             'validity': 'current_version' if source_version == self.state['current'] else 'historical_recheck'})
        page = rows[offset:offset + page_size]
        return {'records': page, 'total': len(rows), 'next_offset': offset + len(page) if offset + len(page) < len(rows) else None,
                'truncated': len(page) < min(page_size, max(0, len(rows) - offset))}

    def discovery_feedback(self, kind, query, value, catalog):
        if value.get('status') not in ('no_match', 'no_verified_match', 'blocked'):
            return None
        module = canonical_module(query['module']) if query.get('module') else None
        interfaces = [row['id'] for row in catalog.get('items', []) if module is None or row['module'] == module]
        entries = []
        if self.support:
            index = lab.read(Path(self.protocol['module_support_contract']['book_path']).with_name('module_policy_index.json'))
            entries = [entry for entry in index['entries'] if module is None or entry['module'] == module]
        candidates = []
        for entry in entries[:1]:
            exact = entry['exact_ids'][0]
            checked = policy_context(catalog, self.protocol['module_support_contract']['book_path'],
                exact_ids=[exact], current_settings=self.state['versions'][self.state['current']]['settings'],
                dependencies=self.backend().dependencies, limit=1, maxbytes=6144)
            candidates.append({'exact_id': exact, 'qualification': checked['status'],
                               'blockers': checked.get('blockers', [])})
        return {'reason': 'AND within one item, not OR across controls.',
                'interface_candidates': interfaces[:2], 'indexed_rule_candidates': candidates,
                'candidate_meaning': 'Availability, not advice or lessons.',
                'query_help': 'Empty query+module lists. exact_ids selects. query optional.',
                'exact_example': {'kind': kind, 'exact_ids': [candidates[0]['exact_id'] if candidates else (interfaces[0] if interfaces else 'light.exposure')]}}

    def project_snapshot(self):
        """Prepare one request surface; actual request/receipt proves delivery."""
        pending = self.state.get('unprojected_discovery_ids', [])
        archive = self.state.get('module_rule_archive', [])
        eligible = [r for r in archive if r['id'] in pending and r.get('version') == self.state['observations'][-1]['version']
                    and r.get('runtime_result', {}).get('matches')]
        if eligible:
            self.state['active_rule_result_id'] = eligible[0]['id']
        catalog_results = [r for r in archive if r['id'] in pending and r.get('request', {}).get('kind', 'catalog') == 'catalog']
        if catalog_results:
            self.state['active_catalog_result_id'] = catalog_results[0]['id']
        self.state['last_projection_discovery_ids'] = pending[-4:]
        self.state['unprojected_discovery_ids'] = []
        snap = self.snapshot()
        # A selection in the archive is not a projected lesson. The production
        # bridge projects before the next request, whose fitter retains this
        # whole packet; request/receipt evidence remains the delivery authority.
        packet = snap['context'].get('module_support', {})
        projected = self.state.setdefault('projected_rule_matches', [])
        for match in packet.get('matches', []):
            if not match.get('approvedlesson') or not match.get('card_id'):
                continue
            record = {'version': snap['context']['current_observation']['version'],
                      'result_id': packet['result_id'], 'rule_id': match['id'],
                      'exact_ids': copy.deepcopy(match.get('exact_ids', [])),
                      'claim_type': 'prepared_for_projection_not_dispatch_proof'}
            if record not in projected:
                projected.append(record)
        self.save()
        return snap

    def snapshot(self):
        snap = super().snapshot()
        observation = self.state['observations'][-1]
        # Project current authoritative state, not repeated long narration.
        # Original decisions/expectations remain in the worker journal.
        snap['context'] = {k: v for k, v in snap['context'].items() if k in
            ('task', 'current', 'versions', 'current_settings', 'observed_version_settings',
             'current_observation', 'tool_result', 'active_export')}
        # Current image identity/ROI/role is authoritative in evidence_state;
        # avoid retransmitting the free-text question as if it were a finding.
        snap['context']['current_observation'] = {k: observation.get(k) for k in ('id', 'version', 'parent', 'artifact')}
        snap['context']['module_contract'] = CONTRACT
        snap['context']['evidence_state'] = self.evidence_state(observation)
        snap['context']['comparison_mode'] = observation.get('comparison_mode', 'target')
        records = self.state.get('effect_expectations', [])
        reviewed_ids = {r.get('expectation_id') for r in self.state.get('effect_reviews', [])
                        if r.get('version') == observation['version']}
        current = next((r for r in reversed(records) if r.get('child_version') == observation['version']
                        and r['id'] not in reviewed_ids), None)
        snap['context']['pending_effect_review'] = None if not current else {
            k: copy.deepcopy(current[k]) for k in ['id', 'base_version', 'child_version', 'operation_id', 'operation'] if k in current}
        if current:
            snap['context']['pending_effect_review']['prediction_history_id'] = current['id']
            snap['context']['pending_effect_review']['review_focus'] = {k: current['effect_expectation'][k]
                for k in ('target', 'protected_region')}
        staged = self.state.get('prepared_native_proposal')
        if staged and staged['base_version'] == observation['version']:
            snap['context']['prepared_edit'] = {'proposal_id': staged['id'], 'base_version': staged['base_version'],
                'operation': staged['operation'], 'status': 'not_executed'}
            if snap['context'].get('tool_result', {}).get('kind') == 'edit_prepared_not_executed':
                # Same proposal/status/instruction is already authoritative here.
                snap['context'].pop('tool_result')
        archive = self.state.get('module_rule_archive', [])
        active_id = self.state.get('active_rule_result_id')
        active = next((r for r in archive if r['id'] == active_id and r.get('version') == observation['version']), None)
        if self.support:
            if active:
                packet = copy.deepcopy(active['runtime_result'])
                packet['result_id'] = active['id']
            else:
                packet = self._policy_projection(self._discovery_policy_context(self.catalog(),
                    self.protocol['module_support_contract']['book_path'],
                    current_settings=self.state['versions'][observation['version']]['settings'],
                    dependencies=self.backend().dependencies, limit=1, maxbytes=6144))
                packet['result_id'] = None
            if packet.get('matches'):
                # These are generic provenance restrictions, not the full
                # photographic lesson or prerequisites; keep equivalent brief
                # wording with original detail addressable by result/card ID.
                packet['evidence_limits'] = 'Single-scene independent review only; no task-transfer/author pass. Archive IDs are not dispatch authority.'
                for match in packet['matches']:
                    specific = [limit for limit in match.get('known_limits', []) if limit.startswith(
                        ('Observed recipe only;', 'Shape-specific evidence only;'))]
                    match['known_limits'] = specific
            else:
                packet = {'matches': [], 'status': 'no_active_lesson',
                          'module_index': {key: [counts['interfaces'], counts['rules']]
                                      for key, counts in packet.get('module_index', {}).items()},
                          'counts': 'interfaces,rules'}
            if self.state.get('module_rule_archive') and not packet.get('matches'):
                packet.pop('module_index', None)
                packet.pop('counts', None)
            packet['retrieval_limit'] = 1
            packet['release'] = self.protocol['module_support_contract']['rule_release']
            snap['context']['module_support'] = packet
        pending_ids = self.state.get('unprojected_discovery_ids', [])
        visible_ids = list(dict.fromkeys(self.state.get('last_projection_discovery_ids', []) + pending_ids))[-4:]
        snap['context']['discovery_results'] = []
        for row in archive:
            if row['id'] not in visible_ids:
                continue
            selected = [m['id'] for m in row.get('runtime_result', {}).get('matches', [])]
            feedback = row.get('feedback')
            projected = {'result_id': row['id'], 'request': {k: copy.deepcopy(v) for k, v in row['request'].items() if v is not None},
                         'version': row['version'], 'status': row['result']['status'], 'matched_rule_ids': selected,
                         'lesson_body': 'in_module_support' if row['id'] == active_id and active else
                                        'matched_not_projected' if selected else 'no_match',
                         'feedback': ({k: copy.deepcopy(feedback[k]) for k in
                                      ('interface_candidates', 'indexed_rule_candidates', 'exact_example')} if feedback else None)}
            if feedback:
                snap['context']['discovery_help'] = 'AND within one item, not OR; empty query+module lists interfaces. Candidates are availability, not advice/lessons. exact_ids selects; query optional.'
            if row['request'].get('kind', 'catalog') == 'catalog':
                projected.pop('matched_rule_ids', None)
                projected.pop('lesson_body', None)
                projected['total'] = row['result'].get('total', sum(r.get('total', 0) for r in row['result'].get('exact_results', [])))
                if projected.get('feedback') is None: projected.pop('feedback', None)
                if row['result'].get('items') or row['result'].get('exact_results'):
                    active_catalog = self.state.get('active_catalog_result_id', row['id'])
                    projected['interface_body'] = 'shown' if row['id'] == active_catalog else 'available_by_result_id'
                    if row['id'] == active_catalog:
                        projected['result'] = runtime_catalog_projection(row['result'],
                            exact=bool(row['request'].get('exact_ids')) or bool(row['result'].get('exact_id')))
                    else:
                        projected['focus_with'] = {'result_id': row['id']}
            snap['context']['discovery_results'].append(projected)
        return snap

    def act(self, name, args, source_observation_id=None):
        if self.state.get('pending') or self.state.get('status') != 'working':
            raise lab.SafetyStop('Module trajectory has pending or is not working')
        raw = copy.deepcopy(args)
        public_only(raw)
        schema = self._action_schema(name)
        if schema is None:
            raise ValueError('Unknown module photo tool')
        _check(raw, schema, name)
        public = raw.get('review', {})
        self._validate_public_review({k: v for k, v in public.items()
                                      if k in ('module_decisions', 'effect_expectation', 'effect_review')})
        observation = self.state['observations'][-1]
        if source_observation_id is not None:
            source = next((o for o in self.state['observations'] if o['id'] == source_observation_id), None)
            if source is None or (source['id'] != observation['id'] and name not in ('photo_observe', 'photo_history', 'photo_discover')):
                raise ValueError('Unavailable or stale source observation')
            observation = source
        known_sources = self.state.setdefault('model_action_source_observations', [])
        if observation['id'] not in known_sources:
            known_sources.append(observation['id'])
        if name == 'photo_edit':
            base = raw.get('base_version', self.state['current'])
            if base != observation['version']:
                raise ValueError('Effect expectation must use the actually delivered base version')
            declared = {canonical_module(d['module']) for d in public['module_decisions'] if d['choice'] == 'use'}
            actual_modules = set(self._operation_modules(raw))
            if not actual_modules or actual_modules != declared:
                raise ValueError('Selected use modules must exactly cover the native operation')
        if name in ('photo_edit', 'photo_finish'):
            submitted = bool(public.get('visible_comparison', '').strip())
            earlier = any(d.get('observation_id') == observation['id'] and d.get('visible_comparison', '').strip()
                          for d in self.state.get('public_decisions', []))
            if not submitted and not earlier:
                raise ValueError('Current delivered observation needs a nonempty public comparison before edit/selection')
        effect = public.get('effect_review')
        prior_expectation = next((x for x in reversed(self.state.get('effect_expectations', []))
                                  if x.get('child_version') == observation['version']), None)
        already_reviewed = prior_expectation and any(x.get('expectation_id') == prior_expectation['id']
            and x.get('version') == observation['version'] for x in self.state.get('effect_reviews', []))
        if prior_expectation and not already_reviewed and name in ('photo_edit', 'photo_finish') and not effect:
            raise ValueError('Review the actual prior effect before editing again or selecting delivery')
        if effect:
            expectation = next((x for x in self.state.get('effect_expectations', []) if x['id'] == effect['expectation_id']), None)
            if not expectation or expectation.get('child_version') != observation['version']:
                raise ValueError('Effect review refers to another or unknown version')
            if observation.get('comparison_mode') != 'effect' and effect['outcome'] in ('supported', 'contradicted'):
                raise ValueError('A definitive effect comparison requires actually delivered parent/current views')
        if name == 'photo_edit' and self.support and self.prepare_edit_rule_check():
            exact_ids = operation_exact_ids(raw)
            archived = self.state.setdefault('module_rule_archive', [])
            selected = policy_context(self.catalog(), self.protocol['module_support_contract']['book_path'],
                exact_ids=exact_ids, current_settings=self.state['versions'][observation['version']]['settings'],
                dependencies=self.backend().dependencies, limit=1, maxbytes=6144) if exact_ids else {'matches': []}
            needed = [m for m in selected.get('matches', []) if m.get('textbook_card')]
            projected = {p['rule_id'] for p in self.state.get('projected_rule_matches', [])
                         if p['version'] == observation['version']}
            if needed and not all(m['id'] in projected for m in needed):
                previous = next((entry for entry in archived if entry.get('version') == observation['version']
                    and entry.get('request', {}).get('kind') == 'rule'
                    and entry.get('result') == selected), None)
                if previous:
                    result_id = previous['id']
                else:
                    result_id = 'discovery_' + str(len(archived) + 1).zfill(5)
                    archived.append({'id': result_id, 'request': {'kind': 'rule', 'exact_ids': exact_ids},
                        'result': selected, 'runtime_result': self._policy_projection(selected),
                        'version': observation['version'], 'observation_id': observation['id'], 'feedback': None,
                        'claim_type': 'host_exact_preedit_selection_not_visual_judgment'})
                self.state['active_rule_result_id'] = result_id
                self.state.setdefault('unprojected_discovery_ids', []).append(result_id)
                ident = 'prepared_' + str(len(self.state.get('module_public_records', [])) + 1).zfill(5)
                operation = {k: copy.deepcopy(raw[k]) for k in self._operation_fields() if k in raw}
                staged = {'id': ident, 'base_version': observation['version'], 'source_observation_id': observation['id'],
                    'operation': operation, 'public_submission': raw, 'status': 'not_executed_rule_check',
                    'rule_result_id': result_id, 'claim_type': 'model_proposal_not_executed'}
                self.state['prepared_native_proposal'] = staged
                self.state.setdefault('module_public_records', []).append(staged)
                self.state['tool_result'] = {'kind': 'edit_prepared_not_executed', 'proposal_id': ident,
                    'rule_result_id': result_id, 'next': 'Explicitly confirm/revise with photo_edit after reading the supplied rule.'}
                self.save(); append(self.folder / 'module-public-timeline.jsonl', staged)
                return self.snapshot()
        if name == 'photo_discover':
            query = {k: raw.get(k) for k in ['kind', 'query', 'exact_ids', 'module', 'offset', 'page_size', 'result_id']}
            query['kind'] = query.get('kind') or 'catalog'
            query_module = canonical_module(query['module']) if query.get('module') else None
            self._check_discovery_repeat(query, observation)
            catalog = self.catalog()
            focused = query.get('result_id')
            archived = self.state.setdefault('module_rule_archive', [])
            if focused:
                original_result = next((r for r in archived if r['id'] == focused), None)
                if not original_result or original_result.get('version') != observation['version']:
                    raise ValueError('Discovery result is unknown or stale; query current exact interface again')
                if any(raw.get(k) for k in ('query', 'exact_ids', 'module')):
                    raise ValueError('Focus a result_id alone; do not silently override its request')
                if original_result['request'].get('kind', 'catalog') != query.get('kind', 'catalog'):
                    raise ValueError('Discovery result kind differs from its original request')
                if not self.support and query.get('kind') == 'rule':
                    raise ValueError('Baseline condition does not expose rule support')
                value = copy.deepcopy(original_result['result'])
            elif query.get('kind', 'catalog') == 'rule':
                if not self.support:
                    raise ValueError('Baseline condition does not expose rule support')
                self.state['module_rule_query'] = query
                value = self._discovery_policy_context(catalog, self.protocol['module_support_contract']['book_path'],
                    exact_ids=query.get('exact_ids') or [], module=query_module, query=query.get('query') or '',
                    current_settings=self.state['versions'][observation['version']]['settings'],
                    dependencies=self.backend().dependencies, limit=3, maxbytes=6144)
            else:
                targets = query.get('exact_ids') or []
                if targets:
                    results = []
                    for target in dict.fromkeys(targets):
                        native_id, separator, shape = target.partition(':')
                        found = self._catalog_discover(catalog, query=native_id, module=query_module, page_size=1)
                        found['items'] = [row for row in found['items'] if row['id'] == native_id]
                        found.update(total=len(found['items']), status='matched' if found['items'] else 'no_match',
                                     next_offset=None, exact_id=True)
                        if separator:
                            row = next((r for r in found['items'] if r['id'] == native_id), None)
                            branch = row.get('shapes', {}).get(shape) if row else None
                            found = {'status': branch.get('availability', 'no_match') if branch else 'no_match',
                                     'items': [] if not branch else [{**copy.deepcopy(row), **copy.deepcopy(branch),
                                               'id': target, 'shape_kind': shape}], 'total': int(bool(branch))}
                        results.append({'requested_id': target, **found})
                    value = {'status': 'matched' if any(r['items'] for r in results) else 'no_match',
                             'exact_results': results, 'requested_ids': targets}
                else:
                    value = self._catalog_discover(catalog, query=query.get('query') or '', module=query_module,
                                     offset=query.get('offset') or 0, page_size=query.get('page_size') or 8)
            result_id = 'discovery_' + str(len(archived) + 1).zfill(5)
            runtime = self._policy_projection(value) if query.get('kind', 'catalog') == 'rule' else {}
            associated = None
            if self.support and query.get('kind', 'catalog') == 'catalog' and query.get('exact_ids'):
                associated = self._discovery_policy_context(catalog, self.protocol['module_support_contract']['book_path'],
                    exact_ids=query['exact_ids'], current_settings=self.state['versions'][observation['version']]['settings'],
                    dependencies=self.backend().dependencies, limit=3, maxbytes=6144)
                runtime = self._policy_projection(associated)
            deferred_ids = [m['id'] for m in runtime.get('matches', [])[1:]]
            runtime['matches'] = runtime.get('matches', [])[:1]
            feedback = self.discovery_feedback(query.get('kind', 'catalog'), query, value, catalog)
            entry = {'id': result_id, 'request': query, 'result': value, 'runtime_result': runtime,
                     'version': observation['version'], 'observation_id': observation['id'],
                     'feedback': feedback, 'matched_body_deferred_ids': deferred_ids,
                     'associated_rule_selection': associated,
                     'claim_type': 'host_rule_selection_not_visual_outcome'}
            archived.append(entry)
            self.state.setdefault('unprojected_discovery_ids', []).append(result_id)
            if query.get('kind', 'catalog') == 'catalog':
                pending_catalog = set(self.state['unprojected_discovery_ids'])
                first_catalog = next(r for r in archived if r['id'] in pending_catalog
                                     and r['request'].get('kind', 'catalog') == 'catalog')
                self.state['active_catalog_result_id'] = first_catalog['id']
            if runtime.get('matches'):
                pending = set(self.state['unprojected_discovery_ids'])
                first = next(r for r in archived if r['id'] in pending and r.get('runtime_result', {}).get('matches'))
                self.state['active_rule_result_id'] = first['id']
            self.state['history'].append({'action': 'module_discover', 'version': observation['version'],
                                          'request': query, 'result_id': result_id})
            self.state['tool_result'] = {'kind': 'discovery_result', 'result_id': result_id,
                'request': query, 'status': value['status'], 'matched_rule_ids': [m['id'] for m in value.get('matches', [])],
                'matched_body_deferred_ids': deferred_ids, 'feedback': feedback,
                'lesson_body': 'in_module_support' if self.state.get('active_rule_result_id') == result_id and runtime.get('matches')
                               else 'matched_not_projected' if runtime.get('matches') else 'no_match'}
            if query.get('kind', 'catalog') == 'catalog':
                self.state['tool_result']['result'] = runtime_catalog_projection(value,
                    exact=bool(query.get('exact_ids')) or bool(value.get('exact_id')))
            self.save()
            result = self.snapshot()
        else:
            # Persist the exact public proposal and prediction before any native
            # mutation. A failure never erases a prediction or triggers repair.
            proposal_id = 'module_' + str(len(self.state['module_public_records']) + 1).zfill(5)
            proposal = {'id': proposal_id, 'tool': name, 'source_observation_id': observation['id'],
                        'source_version': observation['version'], 'public_submission': copy.deepcopy(raw),
                        'claim_type': 'model_judgment', 'status': 'submitted_before_execution'}
            self.state['module_public_records'].append(proposal)
            self.save()
            append(self.folder / 'module-public-timeline.jsonl', proposal)
            # Keep the new public fields outside historical lossless-normalization;
            # do not silently convert them into executable native parameters.
            forwarded = copy.deepcopy(raw)
            if isinstance(forwarded.get('review'), dict):
                for k in ['module_decisions', 'effect_expectation', 'effect_review']:
                    forwarded['review'].pop(k, None)
            if name == 'photo_observe':
                # comparison_mode is read by this subclass, accepted by base schema.
                forwarded['comparison_mode'] = raw.get('comparison_mode', 'target')
            if name == 'photo_edit':
                base = raw.get('base_version', self.state['current'])
                legacy_review = self.normalize_public_review(forwarded.get('review'))
                forwarded['review'] = legacy_review
                forwarded['base_version'] = base
                self._validate_forwarded_edit(forwarded)
                self._record_public_review(legacy_review, observation)
                self.record_decision(observation, {'action': 'edit', 'arguments': forwarded})
                if base not in self.state['reviewed']:
                    raise ValueError('Edit base must have an actual public review')
                operation = {k: raw.get(k, {} if k != 'commands' else []) for k in self._operation_fields()}
                if any(h.get('action') == 'edit' and h.get('parent') == base and
                       all(h.get('arguments', {}).get(k, [] if k == 'commands' else {}) == v for k, v in operation.items())
                       for h in self.state['history']):
                    raise original.NoProgress('Same native operation already tested from this base')
                op = 'op_' + uuid.uuid4().hex
                expected = {'id': proposal_id, 'base_version': base, 'source_observation_id': observation['id'],
                    'operation_id': op, 'operation': copy.deepcopy(operation),
                    'module_decisions': copy.deepcopy(public['module_decisions']),
                    'effect_expectation': copy.deepcopy(public['effect_expectation']),
                    'claim_type': 'model_prediction_not_measured_fact', 'status': 'locked_before_execution'}
                self.state['effect_expectations'].append(expected)
                self.state['pending'] = {'operation_id': op, 'base': base, 'arguments': raw, 'expectation_id': proposal_id}
                self.save()
                actual = self.backend().apply(self.state['versions'][base]['settings'], raw)
                state_only = self._state_only_operation(raw)
                if actual == self.state['versions'][base]['settings'] and not state_only:
                    raise ValueError('No actual setting change; preserve proposal for diagnosis')
                child = 'v' + str(len(self.state['versions']))
                dest = self.folder / (child + '.png')
                self.backend().render(dest, self.protocol.get('render_long_edge', lab.DSH_RENDER_EDGE))
                before, after = lab.srgb(self.state['versions'][base]['master'], True), lab.srgb(dest, True)
                if before.size != after.size:
                    raise ValueError('Candidate geometry changed')
                identical = original.ImageChops.difference(before, after).getbbox() is None
                if identical and not state_only:
                    raise ValueError('Pixel-effect operation had no visible raster response; stop diagnosis, no replay')
                self.state['versions'][child] = {'settings': actual, 'master': str(dest), 'parent': base,
                    'measurements': {}, 'operation_id': op, 'expectation_id': proposal_id}
                self.state.update(current=child, export=None)
                self.state.pop('prepared_native_proposal', None)
                self.state['history'].append({'action': 'edit', 'version': child, 'parent': base,
                    'arguments': raw, 'settings_readback': actual, 'operation_id': op,
                    'changed_controls': original.changed_paths(self.state['versions'][base]['settings'], actual),
                    'pixel_response': 'unchanged_state_only' if identical else 'changed'})
                self.state.pop('tool_result', None)
                self.save()
                self.observe(child, args={'comparison_mode': 'effect'}, requested=False)
                self.state['pending'] = None
                self.save()
                result = self.snapshot()
            elif name == 'photo_history':
                legacy_review = self.normalize_public_review(forwarded.get('review'))
                self._record_public_review(legacy_review, observation)
                self.record_decision(observation, {'action': 'history', 'arguments': {'review': legacy_review}})
                self.state['tool_result'] = self._history_result(raw, observation)
                self.state['history'].append({'action': 'history', 'version': observation['version'],
                    'observation_id': observation['id'], 'request': {k: raw.get(k) for k in ('ids', 'query', 'version', 'offset', 'page_size')}})
                self.save()
                result = self.snapshot()
            else:
                result = self._dispatch_photo_action(name, forwarded, source_observation_id)
        if name == 'photo_discover':
            record = {'id': 'module_' + str(len(self.state['module_public_records']) + 1).zfill(5),
                      'tool': name, 'source_observation_id': observation['id'], 'source_version': observation['version'],
                      'public_submission': raw, 'claim_type': 'model_judgment'}
            self.state['module_public_records'].append(record)
        else:
            record = proposal
            record['status'] = 'executed'
            record['result_status'] = self.state['status']
        if name == 'photo_edit':
            child = self.state['current']; version = self.state['versions'][child]
            expectation = {'id': record['id'], 'base_version': observation['version'], 'child_version': child,
                'source_observation_id': observation['id'], 'operation_id': version['operation_id'],
                'operation': {k: raw[k] for k in self._operation_fields() if k in raw},
                'module_decisions': public['module_decisions'], 'effect_expectation': public['effect_expectation'],
                'claim_type': 'model_prediction_not_measured_fact', 'execution_trace': copy.deepcopy(self.backend().trace)}
            locked = next(x for x in self.state['effect_expectations'] if x['id'] == record['id'])
            locked.update(expectation, status='executed_pending_review')
        if effect:
            self.state.setdefault('effect_reviews', []).append({**copy.deepcopy(effect), 'observation_id': observation['id'],
                                                              'version': observation['version'], 'claim_type': 'model_judgment'})
        self.save()
        append(self.folder / 'module-public-timeline.jsonl', record)
        return self.snapshot()
