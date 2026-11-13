"""Pure offline, exact-index policy context; never executes or promotes a rule.

The first argument is a photo_capability_catalog.build_catalog() snapshot. Pass
an index as catalog['module_policy_index'], or supply book_path (a textbook.json
file or its directory) with a sibling module_policy_index.json. No default book,
recursive search, lesson-text inference, or same-module fallback is performed.

Frozen sidecar contract::

    {"schema": "photo-module-policy-index/v1", "frozen": true,
     "release": "explicit-release", "book_id": "optional-book-binding",
     "entries": [{"id": "rule-id", "module": "curves",
       "kind": "interface_fact", "exact_ids": ["curve.lights"],
       "verification_level": "schema_checked",
       "applicable_scope": {"kind": "interface_only"},
       "known_limits": ["Not visual evidence"], "source_refs": ["source-id"],
       "terms": ["lights"], "required_settings": {},
       "required_dependencies": {}, "commands": []}]}

Entry kinds are interface_fact, visual_evidence, strategy. Non-interface entries
require an explicit card_id retrieved ONLY by photo_textbook.lookup(ids=[...]).
Their applicable_scope must equal the frozen card scope; levels are retained,
not inferred or upgraded. Optional catalog_build/base_build bind the index to
its catalog. book_id is mandatory when any entry names a card.

Targets are exact native control/command IDs, or command:shape, e.g.
mask.add:radial (also mask.addComponent:radial, mask.update:radial). A command-only
entry is NOT evidence for a particular shape. requirements are exact readback
path/value maps, using JSON Pointer (/masks/0/id) or dotted paths (light.exposure);
a literal top-level key wins, including segment.model.status. commands contains
explicit native envelopes to validate, never catalog synthetic examples. Missing
state, changed prerequisites, unavailable dependencies and pending work block the
entry. No next IDs/indices or missing parameters are invented.

module_index always counts all eight modules: current catalog interfaces (each
shape variant separately) and frozen rules, independent of query/limit. matches
are indexed rules, interface_facts are separate schema-only catalog information.
status=matched means an indexed rule was retrieved, NOT that an edit is safe or
that a photographic effect was observed. No indexed rule => no_verified_match.
All returned JSON (normal json.dumps, ensure_ascii=False) fits maxbytes; complete
entries are omitted, never shortened. Too-small mandatory envelopes raise.
Review extensions are optional, strictly public, and never required by discover.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import re


def _sibling(name):
    # Explicit local loading also works under python -I, without adding cwd/data
    # directories to sys.path or importing any worker/runtime/backend module.
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cap = _sibling('photo_capability_catalog')
textbook = _sibling('photo_textbook')
INDEX_SCHEMA = 'photo-module-policy-index/v1'
SCHEMA = 'photo-module-policy-context/v1'
KINDS = ('interface_fact', 'visual_evidence', 'strategy')
LEVELS = ('schema_checked', 'backend_response_checked', 'independent_visual_checked', 'task_transfer_checked')
OUTCOMES = ('supported', 'contradicted', 'uncertain', 'not_visible', 'unknown', 'effect_not_applicable')
MODULE_ALIASES = {'color': 'mixer_point', 'mixer': 'mixer_point', 'pointColor': 'mixer_point',
                  'curve': 'curves', 'calibration': 'basis', 'profile': 'basis', 'mask': 'masks'}


def canonical_module(name):
    value = MODULE_ALIASES.get(name, name)
    if value not in cap.MODULES:
        raise ValueError('Unknown module: ' + str(name))
    return value
_PRIVATE = {re.sub(r'[^a-z0-9]', '', key) for key in textbook.PRIVATE_KEYS}


def _public(value):
    textbook.public_only(value)
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or re.sub(r'[^a-z0-9]', '', key.casefold()) in _PRIVATE:
                raise ValueError('Hidden/private reasoning keys are forbidden')
            _public(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _public(child)


def _size(value):
    try:
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError('Public values must be finite JSON data') from error


def _text(value, name, maximum=500, nonempty=False):
    if not isinstance(value, str) or len(value) > maximum or (nonempty and not value.strip()):
        raise ValueError(name + ' must be a string of at most ' + str(maximum) + ' characters')


def _object(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError('Missing/unknown fields; expected ' + ', '.join(allowed))


def _strings(value, name, maximum=64):
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ValueError(name + ' must be a bounded string array')
    for child in value:
        _text(child, name, nonempty=True)


def _target(ident):
    if not isinstance(ident, str):
        raise ValueError('Exact IDs must be strings')
    if ':' in ident:
        command, shape = ident.split(':', 1)
        if command not in ('mask.add', 'mask.addComponent', 'mask.update') or shape not in cap.SHAPE_KINDS:
            raise ValueError('Unknown exact command:shape ID: ' + ident)
        return 'masks', command, shape
    return cap.module_for(ident), ident, None


def _index(catalog, book_path):
    book = None
    supplied = catalog.get('module_policy_index')
    if book_path is not None:
        if not isinstance(book_path, (str, os.PathLike)):
            raise ValueError('book_path must be an explicit local path')
        path = Path(book_path)
        path = path / 'textbook.json' if path.is_dir() else path
        book = textbook.load_json(path)
        # Reuse publication/read semantics, without looking up arbitrary cards.
        textbook.lookup(book, ids=[])
        sidecar = path.with_name('module_policy_index.json')
        if supplied is None and sidecar.is_file():
            supplied = textbook.load_json(sidecar)
    if supplied is None:
        return [], book
    _public(supplied)
    _size(supplied)
    _object(supplied, ('schema', 'frozen', 'release', 'book_id', 'catalog_build', 'base_build', 'entries'),
            ('schema', 'frozen', 'release', 'entries'))
    if supplied['schema'] != INDEX_SCHEMA or supplied['frozen'] is not True:
        raise ValueError('An explicitly frozen policy index is required')
    _text(supplied['release'], 'release', nonempty=True)
    for field, source in (('catalog_build', 'build'), ('base_build', 'base_build')):
        if field in supplied and supplied[field] != catalog.get(source):
            raise ValueError('Policy index/catalog binding mismatch: ' + field)
    entries = supplied['entries']
    if not isinstance(entries, list):
        raise ValueError('entries must be an array')
    seen = set()
    for entry in entries:
        _object(entry, ('id', 'module', 'kind', 'exact_ids', 'verification_level', 'applicable_scope',
                        'known_limits', 'source_refs', 'card_id', 'terms', 'required_settings',
                        'required_dependencies', 'commands'),
                ('id', 'module', 'kind', 'exact_ids', 'verification_level', 'applicable_scope',
                 'known_limits', 'source_refs'))
        _text(entry['id'], 'entry.id', nonempty=True)
        if entry['id'] in seen:
            raise ValueError('Duplicate policy entry ID')
        seen.add(entry['id'])
        if entry['module'] not in cap.MODULES or entry['kind'] not in KINDS or entry['verification_level'] not in LEVELS:
            raise ValueError('Unknown module/kind/verification_level')
        _strings(entry['exact_ids'], 'exact_ids')
        if not entry['exact_ids'] or len(set(entry['exact_ids'])) != len(entry['exact_ids']):
            raise ValueError('Unique nonempty exact_ids required')
        if any(_target(ident)[0] != entry['module'] for ident in entry['exact_ids']):
            raise ValueError('Exact ID belongs to another module')
        if not isinstance(entry['applicable_scope'], dict) or not entry['applicable_scope']:
            raise ValueError('An explicit applicable_scope object is required')
        _strings(entry['known_limits'], 'known_limits')
        refs = entry['source_refs']
        if not isinstance(refs, list) or not refs or any(not isinstance(r, (str, dict)) or not r for r in refs):
            raise ValueError('Nonempty source_refs required')
        _strings(entry.get('terms', []), 'terms')
        for key in ('required_settings', 'required_dependencies'):
            if not isinstance(entry.get(key, {}), dict):
                raise ValueError(key + ' must be an exact path/value map')
        if not isinstance(entry.get('commands', []), list):
            raise ValueError('commands must be explicit native envelopes')
        for command in entry.get('commands', []):
            if not isinstance(command, dict) or not isinstance(command.get('id'), str):
                raise ValueError('commands must contain native envelopes')
            params = command.get('params', {})
            if not isinstance(params, dict):
                raise ValueError('Command params must be an object')
            shape = (params['shape'].get('kind') if isinstance(params.get('shape'), dict)
                     else params.get('kind'))
            target = command['id'] + (':' + shape if isinstance(shape, str) else '')
            if command['id'] not in entry['exact_ids'] and target not in entry['exact_ids']:
                raise ValueError('Command is not bound to an entry exact ID')
        if entry['kind'] != 'interface_fact' and 'card_id' not in entry:
            raise ValueError('Visual evidence/strategy must reference a frozen textbook card')
        if entry['kind'] == 'visual_evidence' and entry['verification_level'] not in LEVELS[2:]:
            raise ValueError('Visual evidence requires explicit independent visual verification')
        if entry['kind'] == 'interface_fact' and entry['verification_level'] not in LEVELS[:2]:
            raise ValueError('Interface facts cannot masquerade as visual verification')
        if 'card_id' in entry:
            _text(entry['card_id'], 'card_id', nonempty=True)
            _text(supplied.get('book_id'), 'book_id', nonempty=True)
    if book is not None and supplied.get('book_id') is not None and supplied['book_id'] != book.get('manifest', {}).get('book_id'):
        raise ValueError('Policy index/textbook book_id mismatch')
    return copy.deepcopy(entries), book


def _read_path(snapshot, path):
    if not isinstance(path, str) or not path:
        raise ValueError('Requirement paths must be nonempty strings')
    if not isinstance(snapshot, dict):
        raise ValueError('Required snapshot is missing')
    if path in snapshot:
        return snapshot[path]
    parts = [p.replace('~1', '/').replace('~0', '~') for p in path[1:].split('/')] if path.startswith('/') else path.split('.')
    value = snapshot
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise ValueError('Required readback path missing: ' + path)
    return value


def _equal(actual, expected):
    # Recursive JSON equality: even nested bool true cannot stand in for 1.
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(_equal(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _requirements(expected, actual, label):
    blockers = []
    for path, value in sorted(expected.items()):
        try:
            if not _equal(_read_path(actual, path), value):
                blockers.append(label + ' mismatch: ' + path)
        except ValueError as error:
            blockers.append(label + ': ' + str(error))
    return blockers


def _interface(catalog, ident):
    _, command, shape = _target(ident)
    # Deliberately use discover's EXACT branch, never its substring alias branch.
    found = cap.discover(catalog, query=command, page_size=32)
    rows = [row for row in found['items'] if row['id'] == command]
    if not rows:
        return None
    row = rows[0]
    if shape is not None:
        qualified = row.get('shapes', {}).get(shape)
        if qualified is None:
            return None
        row = {**row, **qualified, 'id': ident, 'kind': 'shape'}
    blockers = list(row.get('blockers', []))
    return {'id': ident, 'module': row['module'], 'kind': row['kind'],
            'verification_level': 'schema_checked', 'applicable_scope': {'kind': 'interface_only'},
            'known_limits': ['Source/schema facts are not visual evidence or dispatch authorization.'],
            'source_refs': [row['source_status']['source']] if isinstance(row.get('source_status'), dict) else [command],
            'units': copy.deepcopy(row.get('units')), 'availability': row['availability'], 'blockers': blockers}


def _tokens(value):
    return set(re.findall(r'[^\W_]+', value.casefold()))


def policy_context(catalog, book_path=None, *, exact_ids=(), module=None, query='',
                   current_settings=None, dependencies=None, limit=3, maxbytes=6144, retrieval_capacity=None):
    """Return bounded public rules plus distinct current interface facts; no IO writes.

    Explicit IDs are authoritative (query never broadens them). Without IDs an
    exact native-ID query wins; otherwise ALL query tokens must match explicit
    index terms/target components, not arbitrary card prose. module alone only
    filters: it cannot select rules. Input snapshots are never modified.
    """
    if not isinstance(catalog, dict) or catalog.get('schema') != 'photo-capability-catalog/v1':
        raise ValueError('A built photo capability catalog is required')
    if type(limit) is not int or not 1 <= limit <= 3 or type(maxbytes) is not int or not 128 <= maxbytes <= 6144:
        raise ValueError('limit must be 1..3 and maxbytes 128..6144')
    complete = retrieval_capacity == {'complete_records': True}
    if retrieval_capacity is not None and not complete:
        raise ValueError('Unknown host retrieval capacity')
    if complete:
        if (not isinstance(query, str) or not isinstance(exact_ids, (list, tuple))
                or any(not isinstance(ident, str) or not ident.strip() for ident in exact_ids)):
            raise ValueError('Query must be a string; exact IDs must be nonempty strings')
    else:
        _text(query, 'query', maximum=500)
        _strings(exact_ids, 'exact_ids', maximum=32)
    if current_settings is not None and not isinstance(current_settings, dict):
        raise ValueError('current_settings must be a readback object')
    if dependencies is not None and not isinstance(dependencies, dict):
        raise ValueError('dependencies must be a read-only snapshot object')
    _public((catalog, current_settings, dependencies))
    _size((catalog, current_settings, dependencies))
    entries, book = _index(catalog, book_path)
    deps = copy.deepcopy(catalog.get('dependencies', {}) if dependencies is None else dependencies)
    # Rebuild to avoid treating stale item readiness as current dependency status.
    fresh = cap.build_catalog(catalog.get('controls', []), deps.get('commands', []), deps)
    selected_module = None
    if module is not None:
        normalized = cap.discover(fresh, module=module, page_size=1)
        key = module.strip().casefold()
        selected_module = next(m for m, aliases in cap.MODULE_ALIASES.items() if key in [a.casefold() for a in aliases])
        del normalized
    module_index = {m: {'interfaces': 0, 'rules': 0} for m in cap.MODULES}
    for row in fresh['items']:
        module_index[row['module']]['interfaces'] += 1 + len(row.get('shapes', {}))
    for entry in entries:
        module_index[entry['module']]['rules'] += 1
    requested = list(dict.fromkeys(exact_ids))
    mode = 'exact_ids' if requested else 'query' if query.strip() else 'index_only'
    if not requested and query.strip():
        try:
            _target(query.strip())
            requested = [query.strip()]
            mode = 'exact_query'
        except ValueError:
            pass
    tokens = _tokens(query)
    candidates = []
    for entry in entries:
        if selected_module is not None and entry['module'] != selected_module:
            continue
        relevant = [ident for ident in requested if ident in entry['exact_ids']]
        if requested and not relevant:
            continue
        if not requested and (not tokens or not tokens.issubset(_tokens(' '.join(list(entry['exact_ids']) + list(entry.get('terms', [])))))):
            continue
        candidates.append(entry)
    candidates.sort(key=lambda e: (min((requested.index(i) for i in e['exact_ids'] if i in requested), default=0),
                                   KINDS.index(e['kind']), e['id']))
    out = {'schema': SCHEMA, 'module_index': module_index, 'matches': [],
           'status': 'no_verified_match', 'blockers': [], 'selection_reason': mode + ': no exact indexed rule selected; no module fallback',
           'interface_facts': [], 'truncated': False}
    global_blockers = []
    if current_settings is not None:
        try:
            cap.validate_commands([], current_settings, deps)
        except ValueError as error:
            global_blockers.append('current_settings: ' + str(error))
    facts = {}
    interface_ids = requested or list(dict.fromkeys(ident for entry in candidates for ident in entry['exact_ids']))
    for ident in interface_ids:
        try:
            facts[ident] = _interface(fresh, ident)
        except ValueError:
            facts[ident] = None
    viable, rejected = [], []
    for entry in candidates:
        blockers = list(global_blockers)
        if entry['kind'] != 'interface_fact' and current_settings is None:
            blockers.append('Evidence/strategy applicability requires current_settings readback')
        for ident in entry['exact_ids']:
            fact = facts.get(ident)
            if ident not in facts:
                fact = _interface(fresh, ident)
            if fact is None:
                blockers.append('Exact interface not currently listed: ' + ident)
            elif fact['availability'] != 'ready':
                blockers.extend(ident + ': ' + reason for reason in fact['blockers'])
            if ident.startswith('pointColor.') and ident not in cap.COMMAND_IDS:
                index = int(ident.split('.')[1])
                samples = (current_settings or {}).get('point_colors')
                if not isinstance(samples, list) or index >= len(samples):
                    blockers.append('Dynamic point-color ID requires an observed current sample: ' + ident)
        blockers.extend(_requirements(entry.get('required_settings', {}), current_settings, 'current_settings'))
        blockers.extend(_requirements(entry.get('required_dependencies', {}), deps, 'dependencies'))
        if entry.get('commands'):
            if current_settings is None:
                blockers.append('Explicit commands require current_settings readback')
            else:
                try:
                    cap.validate_commands(entry['commands'], current_settings, deps)
                except ValueError as error:
                    blockers.append('commands: ' + str(error))
        projected = {key: copy.deepcopy(entry[key]) for key in (
            'id', 'module', 'kind', 'verification_level', 'applicable_scope', 'known_limits', 'source_refs', 'exact_ids')}
        # Keep units with the rule even when separate interface facts do not fit.
        projected['native_units'] = {ident: copy.deepcopy((_interface(fresh, ident) or {}).get('units'))
                                     for ident in entry['exact_ids']}
        for key in ('required_settings', 'required_dependencies', 'commands'):
            if key in entry:
                projected[key] = copy.deepcopy(entry[key])
        if 'card_id' in entry:
            if book is None:
                blockers.append('Frozen textbook required for card_id: ' + entry['card_id'])
            else:
                result = textbook.lookup(book, ids=[entry['card_id']], module=entry['module'], limit=1, maxbytes=8192,
                                         retrieval_capacity=retrieval_capacity)
                if not result['cards']:
                    blockers.append('Exact textbook card missing or exceeds retrieval bound: ' + entry['card_id'])
                elif result['cards'][0]['scope'] != entry['applicable_scope']:
                    blockers.append('Index scope differs from frozen textbook card; no scope promotion')
                else:
                    projected['textbook_card'] = result['cards'][0]
        if blockers:
            rejected.extend(entry['id'] + ': ' + reason for reason in blockers)
        else:
            viable.append(projected)
    requested_blockers = [ident + ': ' + reason for ident, fact in facts.items() if fact for reason in fact['blockers']]
    out['blockers'] = sorted(set(global_blockers + rejected + requested_blockers))
    if out['blockers'] and not viable:
        out['status'] = 'blocked'
    out['selection_reason'] = mode + (': exact indexed rules; interface facts are separate, no automatic promotion' if viable else ': no verified indexed match; no module fallback')
    # Blockers are safety-critical: never discard/truncate them to squeeze in a rule.
    if not complete and _size(out) > maxbytes:
        raise ValueError('maxbytes cannot contain the complete module index and blockers')
    for match in viable:
        proposal = {**out, 'matches': out['matches'] + [match], 'status': 'matched', 'truncated': False}
        if not complete and (len(out['matches']) >= limit or _size(proposal) > maxbytes):
            out['truncated'] = True
        else:
            out['matches'].append(match)
            out['status'] = 'matched'
    for fact in facts.values():
        if fact is None or (selected_module is not None and fact['module'] != selected_module):
            continue
        proposal = {**out, 'interface_facts': out['interface_facts'] + [fact], 'truncated': False}
        if not complete and (len(out['interface_facts']) >= limit or _size(proposal) > maxbytes):
            out['truncated'] = True
        else:
            out['interface_facts'].append(fact)
    # Keep the selection envelope fixed while adding/omitting entries, so even
    # a one-byte boundary cannot grow after the last budget check.
    _public(out)
    if not complete and _size(out) > maxbytes:
        raise ValueError('Policy context exceeds maxbytes')
    return out


def validate_module_review(review):
    """Validate optional public extensions only; return a detached copy.

    This checks shape/bounds, not truth, expectation identity or actual images.
    It never writes memory, admits cards, or promotes supported claims to rules.
    The empty object is valid; discovery never calls or requires this function.
    """
    _public(review)
    _object(review, ('module_decisions', 'effect_expectation', 'effect_review'))
    if 'module_decisions' in review:
        decisions = review['module_decisions']
        if not isinstance(decisions, list) or len(decisions) > 8:
            raise ValueError('module_decisions must be an array of at most 8')
        seen = set()
        for decision in decisions:
            _object(decision, ('module', 'choice', 'reason'), ('module', 'choice', 'reason'))
            canonical_module(decision['module'])
            if decision['choice'] not in ('use', 'avoid', 'uncertain'):
                raise ValueError('Unknown module/choice')
            if decision['module'] in seen:
                raise ValueError('Duplicate module decision')
            seen.add(decision['module'])
            _text(decision['reason'], 'reason', 400)
    if 'effect_expectation' in review:
        fields = ('target', 'expected_change', 'protected_region', 'risk', 'falsifier')
        _object(review['effect_expectation'], fields, fields)
        for key in fields:
            _text(review['effect_expectation'][key], key)
    if 'effect_review' in review:
        item = review['effect_review']
        _object(item, ('expectation_id', 'outcome', 'evidence'), ('expectation_id', 'outcome', 'evidence'))
        _text(item['expectation_id'], 'expectation_id', nonempty=True)
        if item['outcome'] not in OUTCOMES:
            raise ValueError('Unknown effect_review outcome')
        _text(item['evidence'], 'evidence')
    return copy.deepcopy(review)
