"""Offline evidence-bound textbook API. No backend, transport, credentials or hashes.

Integration contract (v1)
-------------------------
compile_candidates(protocol, units, assessments) -> list[dict]. Inputs are JSON
objects/lists or JSON paths. protocol.round_dir resolves evidence paths; when
protocol is a path its parent is the default. units have id, module, scene_id,
source_ids, family_ids, split and optionally model_id. Assessments have unit_id,
model_claims (public only), and evidence_refs, a list of dictionaries containing
request_id, receipt_id, observation_id, pre_state_id, post_state_id,
prediction_id, review_id, action_id; timeline_path may select a timeline.
Missing evidence remains pending, never fabricated. IDs also accept camelCase
and *_ID spelling. Host IDs may be at timeline top level: the corresponding
public_submission must exactly equal actual receipt capability_submit.input.
A ref may identify the prediction request and final review receipt; each phase's
own actual request/receipt is checked separately. teaching explicitly means the
development partition; holdout source/family checks still apply.

admit_card(candidate, review_record) -> new card, or ValueError. Review is a
separate JSON path or {path: ...}; it must actually exist below round_dir. It
contains record_id, candidate_id, decision='approve', reviewer={id, kind:
human|independent_model, model_id?}, actual_image_review=true, reviewed_images:
[{path, observation_id}], backend_readback={path}, lesson (public text). The
separate backend record contains record_id, action_id, pre_state_id,
post_state_id, settings and status='ok'. It must agree with timeline readback.
An actual atlas record may instead be selected by backend_readback condition_id
and direction: complete native stimulus operation/readback/image and backend
fact ID must agree with timeline. Review collections use {path,record_id} to
select a single actual independent record; no duplicate receipt is invented.
Review must cover all evidence images (including before and after states).
This validates recorded provenance, not the honesty/identity of a local writer.

publish_textbook(root, manifest, cards) -> Path. root MUST NOT EXIST, even if
empty. manifest requires book_id; only independently admitted, non-holdout cards
can be frozen. lookup(book, query='', module=None, ids=None, limit=3,
maxbytes=8192) returns {schema,cards,truncated}; byte bound covers the complete
UTF-8 JSON response (ensure_ascii=False, normal json.dumps separators). Retrieval
uses an explicit lesson/evidence-reference projection: approved lesson text is
never shortened, full execution/settings/public claims stay at actual archive
paths. Oversized projections are omitted whole. All lessons remain single-scene.
"""
import copy
import json
import os
import re
from pathlib import Path

SCHEMA = 'photo-textbook/v1'
REF_FIELDS = ('request_id', 'receipt_id', 'observation_id', 'pre_state_id',
              'post_state_id', 'prediction_id', 'review_id', 'action_id')
PRIVATE_KEYS = {'thinking', 'reasoning', 'reasoning_content', 'hidden_cot',
                'hiddencot', 'chain_of_thought', 'analysis', 'internal_reasoning'}


def public_only(value):
    """Reject private traces rather than silently changing public/tool text."""
    if isinstance(value, dict):
        if any(str(k).lower() in PRIVATE_KEYS for k in value):
            raise ValueError('Private reasoning is not a public textbook/report field')
        for child in value.values():
            public_only(child)
    elif isinstance(value, list):
        for child in value:
            public_only(child)
    return value


def load_json(value):
    if isinstance(value, (str, os.PathLike)):
        return json.loads(Path(value).read_text(encoding='utf-8-sig'))
    return copy.deepcopy(value)


def field(row, key, default=None):
    camel = re.sub(r'_([a-z])', lambda m: m[1].upper(), key)
    return row.get(key, row.get(camel, row.get(key[:-3] + '_ID', default) if key.endswith('_id') else default)) if isinstance(row, dict) else default


def safe_path(root, value):
    if not isinstance(value, (str, os.PathLike)) or not str(value):
        raise ValueError('A local evidence path is required')
    base = Path(root).resolve()
    path = Path(value)
    path = (path if path.is_absolute() else base / path).resolve()
    if not path.is_relative_to(base):
        raise ValueError('Evidence must stay within the round directory')
    return path


def _rows(value, container):
    value = load_json(value)
    if isinstance(value, dict):
        value = value.get(container, value)
        if isinstance(value, dict):
            return [dict(row, **({} if 'id' in row else {'id': key}))
                    for key, row in value.items()]
    if not isinstance(value, list) or any(not isinstance(r, dict) for r in value):
        raise ValueError(container + ' must be a list or ID mapping')
    return value


def _ids(value):
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if not isinstance(value, (list, tuple, set)) or any(not isinstance(v, str) or not v for v in value):
        raise ValueError('Source identifiers must be nonempty strings')
    return set(value)


def _provenance(unit, protocol):
    sources = _ids(unit.get('source_ids', unit.get('source_id')))
    families = _ids(unit.get('family_ids', unit.get('family_id')))
    holdout = protocol.get('holdout', {})
    if not isinstance(holdout, dict):
        raise ValueError('holdout must be an object')
    excluded_sources = _ids(protocol.get('holdout_source_ids')) | _ids(holdout.get('source_ids'))
    excluded_families = _ids(protocol.get('holdout_family_ids')) | _ids(holdout.get('family_ids'))
    split = unit.get('split', 'unknown')
    reasons = []
    # Protocol's teaching partition is explicitly development, never holdout.
    if split not in ('development', 'dev', 'train', 'pilot', 'main', 'teaching'):
        reasons.append('not explicitly a development split: ' + str(split))
    if unit.get('holdout') is True or sources & excluded_sources or families & excluded_families:
        reasons.append('holdout source/family cannot enter a frozen textbook')
    if not sources or not families:
        reasons.append('source_ids and family_ids are required to check holdout leakage')
    return {'split': split, 'source_ids': sorted(sources), 'family_ids': sorted(families),
            'holdout_source_ids': sorted(excluded_sources),
            'holdout_family_ids': sorted(excluded_families), 'blocked_reasons': reasons}


def read_timeline(path):
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('Timeline line must be an object: ' + str(number))
            rows.append(row)
    return rows


def _record(root, category, ident):
    if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', ident):
        raise ValueError('Invalid ' + category + ' identifier')
    path = safe_path(root, category + '/' + ident + '.json')
    row = load_json(path)
    actual = field(row, 'receipt_id') if category == 'receipts' else field(row, 'request_id')
    # Current transport uses request ID as the receipt filename/identity.
    actual = actual or field(row, 'request_id')
    if actual != ident:
        raise ValueError(category + ' record ID mismatch')
    return row, path


def image_path(row):
    return row.get('path', row.get('sourcePath', row.get('source_path'))) if isinstance(row, dict) else None


def delivered(root, image, request_id, receipt_id, scope=None):
    """Read actual request/receipt and same-scope DSH mapping, never rewrite them.

    scope is (stage, unit, condition). Attachment-only requests resolve exclusively
    through model/<stage>/<unit>/<condition>/dsh-images.json, records[id].relative
    (or the registry's direct ID mapping). No global attachment-ID search.
    """
    request, _ = _record(root, 'requests', request_id)
    receipt, _ = _record(root, 'receipts', receipt_id)
    actual_scope = tuple(request.get(key) for key in ('stage', 'unit', 'condition'))
    if scope is not None and actual_scope != tuple(scope):
        raise ValueError('Image delivery belongs to another stage/unit/condition')
    if field(receipt, 'request_id') != request_id:
        raise ValueError('Receipt does not belong to request')
    if request_id not in image.get('delivered_request_ids', []):
        raise ValueError('Image is not recorded as delivered to this request')
    path = safe_path(root, image_path(image))
    if not path.is_file():
        raise ValueError('Actual image file is missing')
    matches = []
    registry = None
    for attachment in request.get('images', []):
        value = image_path(attachment)
        attachment_id = attachment.get('attachmentId')
        if not value and attachment_id:
            if any(not isinstance(part, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', part) or part in ('.', '..') for part in actual_scope):
                raise ValueError('Attachment mapping requires an explicit safe stage/unit/condition')
            if any(receipt.get(key) is not None and receipt[key] != part
                   for key, part in zip(('stage', 'unit', 'condition'), actual_scope)):
                raise ValueError('Receipt attachment scope differs from actual request')
            suffix = 'model/' + '/'.join(actual_scope)
            request_scope = request.get('scope')
            if request_scope and not str(request_scope).replace('\\', '/').rstrip('/').endswith('/' + suffix):
                raise ValueError('Request scope differs from attachment registry scope')
            if registry is None:
                registry = load_json(safe_path(root, suffix + '/dsh-images.json'))
                registry = registry.get('records', registry)
            record = registry.get(attachment_id)
            if not isinstance(record, dict) or not record.get('relative'):
                raise ValueError('Attachment missing from actual same-scope registry')
            relative = record['relative']
            if not isinstance(relative, str) or Path(relative).is_absolute():
                raise ValueError('DSH registry relative must be a round-relative path')
            resolved = safe_path(root, relative)
            if not resolved.is_file():
                raise ValueError('DSH mapped image file is missing')
            if [record.get('width'), record.get('height')] != [attachment.get('width'), attachment.get('height')]:
                raise ValueError('DSH registry dimensions disagree with actual request')
            if record.get('bytes') is not None and (record['bytes'] != attachment.get('bytes') or resolved.stat().st_size != record['bytes']):
                raise ValueError('DSH registry byte size disagrees with actual file/request')
            value = str(resolved)
        if value and safe_path(root, value) == path:
            matches.append(attachment)
    if not matches:
        raise ValueError('Actual request does not contain this image')
    dims = image.get('dimensions')
    for attachment in matches:
        recorded = attachment.get('dimensions') or [attachment.get('width'), attachment.get('height')]
        if dims and recorded != dims:
            raise ValueError('Image dimensions disagree with actual request')
    return request, receipt


def _has_id(row, key, expected):
    if field(row, key) == expected:
        return True
    if isinstance(row, dict):
        return any(_has_id(v, key, expected) for v in row.values() if isinstance(v, (dict, list)))
    if isinstance(row, list):
        return any(_has_id(v, key, expected) for v in row)
    return False


def _public_event(root, event, scope):
    """Host IDs live in timeline; model public input must exactly match receipt."""
    request, _ = _record(root, 'requests', field(event, 'request_id'))
    receipt, _ = _record(root, 'receipts', field(event, 'receipt_id'))
    if field(receipt, 'request_id') != field(event, 'request_id'):
        raise ValueError('Public event request/receipt mismatch')
    submission = event.get('public_submission')
    public_only(submission)
    blocks = [b for b in receipt.get('public_content', []) if b.get('type') == 'tool_use'
              and b.get('name') == 'capability_submit' and b.get('input') == submission]
    if len(blocks) != 1:
        raise ValueError('Timeline public submission differs from actual receipt tool input')
    if not event.get('images'):
        raise ValueError('Public observation has no actual images')
    for image in event['images']:
        delivered(root, image, field(event, 'request_id'), field(event, 'receipt_id'), scope=scope)
    return request, receipt


def _verify_ref(root, ref):
    if any(not isinstance(field(ref, key), str) or not field(ref, key) for key in REF_FIELDS):
        raise ValueError('All request/receipt/observation/state/public/action IDs are required')
    selected = ref.get('timeline_path')
    files = [safe_path(root, selected)] if selected else sorted(Path(root).glob('model/*/*/*/timeline.jsonl'))
    matches = []
    for path in files:
        events = read_timeline(path)
        scope = path.relative_to(Path(root).resolve()).parts[1:4]
        def resolves(event, key):
            return (field(event, key) == field(ref, key) or
                    _has_id(event.get('public_submission', {}), key, field(ref, key)))
        predictions = [e for e in events if e.get('public_submission') is not None and resolves(e, 'prediction_id')]
        reviews = [e for e in events if e.get('public_submission') is not None and resolves(e, 'review_id')]
        actions = [e for e in events if resolves(e, 'action_id') or
                   _has_id(e.get('operation', {}), 'action_id', field(ref, 'action_id'))]
        for prediction in predictions:
            for review in reviews:
                # Atlas refs intentionally identify the first request and final
                # receipt. Validate each phase's own pair, not a fabricated pair.
                if field(prediction, 'request_id') != field(ref, 'request_id') or field(review, 'receipt_id') != field(ref, 'receipt_id'):
                    continue
                if field(review, 'observation_id') != field(ref, 'observation_id'):
                    continue
                _public_event(root, prediction, scope)
                _public_event(root, review, scope)
                for action in actions:
                    if field(action, 'pre_state_id') != field(ref, 'pre_state_id') or field(action, 'post_state_id', action.get('next_state_id')) != field(ref, 'post_state_id'):
                        continue
                    if field(action, 'request_id') != field(prediction, 'request_id') or field(action, 'receipt_id') != field(prediction, 'receipt_id'):
                        continue
                    if action.get('settings_readback') is None or any(e.get('status') in ('error', 'uncertain', 'pending', 'failed') for e in (prediction, review, action)):
                        continue
                    if prediction is not review and (events.index(prediction) >= events.index(action) or events.index(action) >= events.index(review)):
                        continue
                    matches.append((path, prediction, review, action))
    if len(matches) != 1:
        raise ValueError('Expected one evidence-bound observation/action with public prediction/review and readback')
    path, prediction, observation, action = matches[0]
    return {'request_id': field(prediction, 'request_id'), 'receipt_id': field(observation, 'receipt_id'),
            'prediction_receipt_id': field(prediction, 'receipt_id'), 'review_request_id': field(observation, 'request_id'),
            'observation_id': field(ref, 'observation_id'), 'pre_state_id': field(ref, 'pre_state_id'),
            'post_state_id': field(ref, 'post_state_id'), 'action_id': field(ref, 'action_id'),
            'prediction_id': field(ref, 'prediction_id'), 'review_id': field(ref, 'review_id'),
            'timeline_path': str(path.relative_to(Path(root).resolve())),
            'backend_fact_id': action.get('backend_fact_id'),
            'images': copy.deepcopy(observation['images']),
            'operation': copy.deepcopy(action.get('operation')),
            'settings_readback': copy.deepcopy(action['settings_readback']),
            'fact_type': 'recorded_execution_not_visual_quality'}


def compile_candidates(protocol, units, assessments):
    """Compile public claims and verifiable execution facts; never auto-admit."""
    protocol_path = Path(protocol).resolve() if isinstance(protocol, (str, os.PathLike)) else None
    protocol = load_json(protocol)
    if not isinstance(protocol, dict):
        raise ValueError('protocol must be an object')
    root = protocol.get('round_dir') or (str(protocol_path.parent) if protocol_path else None)
    root = str(Path(root).resolve()) if root else None
    unit_rows = _rows(units, 'units')
    known = {str(row['id']): row for row in unit_rows}
    if len(known) != len(unit_rows):
        raise ValueError('Duplicate unit IDs')
    out = []
    for i, assessment in enumerate(_rows(assessments, 'assessments')):
        uid = assessment.get('unit_id', assessment.get('id'))
        if uid not in known:
            raise ValueError('Unknown assessment unit: ' + str(uid))
        unit = known[uid]
        provenance = _provenance(unit, protocol)
        claims = assessment.get('model_claims', assessment.get('public_submission', []))
        public_only(claims)
        refs = assessment.get('evidence_refs', [])
        if not isinstance(refs, list):
            raise ValueError('evidence_refs must be a list')
        facts, pending = [], []
        for ref in refs:
            try:
                if root is None:
                    raise ValueError('protocol.round_dir is required to check actual records')
                fact = _verify_ref(root, ref)
                public_only(fact)
                facts.append(fact)
            except (ValueError, OSError, TypeError, KeyError) as error:
                pending.append(str(error))
        if not refs:
            pending.append('No evidence_refs supplied')
        if not unit.get('scene_id'):
            pending.append('A single scene_id is required')
        out.append({'schema': SCHEMA, 'id': str(assessment.get('candidate_id', f'{uid}-c{i+1}')),
                    'unit_id': uid, 'module': unit.get('module'), 'scene_id': unit.get('scene_id'),
                    'model_id': assessment.get('model_id', unit.get('model_id', protocol.get('model_id'))),
                    'round_dir': root, 'provenance': provenance,
                    'facts': facts, 'model_claims': copy.deepcopy(claims), 'approvedlesson': None,
                    'evidence_refs': copy.deepcopy(refs), 'pending': pending,
                    'status': 'blocked' if provenance['blocked_reasons'] else 'candidate',
                    'scope': {'kind': 'single_scene', 'scene_id': unit.get('scene_id'),
                              'generalization': 'not_established'}})
    if len({c['id'] for c in out}) != len(out):
        raise ValueError('Duplicate candidate IDs')
    return out


def _admit(candidate, review_record):
    card = copy.deepcopy(candidate)
    if card.get('schema') != SCHEMA or card.get('status') != 'candidate' or card.get('pending') or not card.get('facts'):
        raise ValueError('Only evidence-complete candidates can be admitted')
    provenance = card.get('provenance', {})
    if provenance.get('blocked_reasons') or _provenance(provenance, provenance)['blocked_reasons']:
        raise ValueError('Holdout/unknown provenance cannot be admitted')
    if not card.get('scene_id') or card.get('scope') != {
            'kind': 'single_scene', 'scene_id': card['scene_id'], 'generalization': 'not_established'}:
        raise ValueError('Lessons must remain explicitly limited to one actual scene')
    root = card['round_dir']
    path = safe_path(root, review_record.get('path') if isinstance(review_record, dict) else review_record)
    review = load_json(path)
    selector = review_record.get('record_id') if isinstance(review_record, dict) else None
    if selector is not None:
        rows = [r for r in review.get('assessments', []) if r.get('record_id') == selector]
        if len(rows) != 1:
            raise ValueError('Independent review record selector is missing/ambiguous')
        review = rows[0]
    public_only(review)
    if review.get('candidate_id') != card['id'] or review.get('decision') != 'approve' or not review.get('record_id'):
        raise ValueError('An explicit independent approval for this candidate is required')
    reviewer = review.get('reviewer', {})
    if not reviewer.get('id') or reviewer.get('kind') not in ('human', 'independent_model'):
        raise ValueError('Independent reviewer identity/type is required')
    if reviewer['kind'] == 'independent_model' and (not card.get('model_id') or not reviewer.get('model_id') or reviewer['model_id'] == card['model_id']):
        raise ValueError('Same-model or unidentified-model self review cannot admit facts')
    if review.get('actual_image_review') is not True or not isinstance(review.get('lesson'), str) or not review['lesson'].strip():
        raise ValueError('Actual image review and an explicit lesson are required')
    reviewed = {(safe_path(root, image_path(r)), field(r, 'observation_id')) for r in review.get('reviewed_images', [])}
    for fact in card['facts']:
        for image in fact['images']:
            if (safe_path(root, image_path(image)), fact['observation_id']) not in reviewed:
                raise ValueError('Independent review does not cover the actual evidence images')
    readback_ref = review.get('backend_readback', {})
    readback_path = safe_path(root, readback_ref.get('path'))
    if readback_path == path:
        raise ValueError('Backend readback must be a separate actual record')
    readback = load_json(readback_path)
    if readback.get('schema') == 'photo-command-atlas/v1':
        if readback.get('status') != 'complete' or readback.get('command_readback_validation', {}).get('passed') is not True:
            raise ValueError('Actual command backend qualification is incomplete')
        if not any(readback.get('record_id') == fact.get('backend_fact_id') and
                   readback.get('settings_readback') == fact['settings_readback'] and
                   fact['operation'].get('commands') == [readback.get('actual_command')] and
                   any(safe_path(root, image_path(image)) == safe_path(root, readback.get('after_image')) for image in fact['images'])
                   for fact in card['facts']):
            raise ValueError('Actual command/backend settings/operation/image differs from timeline')
    elif readback.get('schema') == 'photo-control-atlas-facts/v1' or ('conditions' in readback and 'control_id' in readback):
        # Atlas native execution predates the model session; select its actual
        # condition/stimulus instead of inventing a duplicate settings record.
        condition = readback_ref.get('condition_id')
        direction = readback_ref.get('direction')
        selected = [c for c in readback.get('conditions', []) if c.get('condition_id') == condition]
        if readback.get('status') != 'complete' or len(selected) != 1:
            raise ValueError('Actual atlas backend condition is incomplete/ambiguous')
        stimulus = selected[0].get('stimuli', {}).get(direction, {})
        if stimulus.get('status') != 'complete' or not stimulus.get('actual_control_readback'):
            raise ValueError('Actual atlas stimulus/readback is incomplete')
        if not any(readback.get('record_id') == fact.get('backend_fact_id') and
                   stimulus.get('actual_readback') == fact['settings_readback'] and
                   stimulus.get('operation') == fact['operation'] and
                   any(safe_path(root, image_path(image)) == safe_path(root, stimulus.get('view512')) for image in fact['images'])
                   for fact in card['facts']):
            raise ValueError('Actual atlas backend fact/settings/operation/image differs from timeline')
    else:
        if not readback.get('record_id') or readback.get('status') != 'ok' or 'settings' not in readback:
            raise ValueError('Successful actual backend readback record is required')
        if not any(all(field(readback, key) == fact[key] for key in ('action_id', 'pre_state_id', 'post_state_id'))
                   and readback['settings'] == fact['settings_readback'] for fact in card['facts']):
            raise ValueError('Backend readback does not match actual timeline settings/state/action')
    # Re-read all source records at admission, not a caller-provided facts label.
    fresh = [_verify_ref(root, ref) for ref in card['evidence_refs']]
    if fresh != card['facts']:
        raise ValueError('Candidate facts differ from actual source records')
    card['approvedlesson'] = {'text': review['lesson'], 'review_record_id': review['record_id'],
                              'review_path': str(path.relative_to(Path(root))),
                              'review_record_selector': selector,
                              'backend_readback_path': str(readback_path.relative_to(Path(root))),
                              'scope': copy.deepcopy(card['scope']), 'reviewer': copy.deepcopy(reviewer)}
    card['status'] = 'admitted'
    return card


def admit_card(candidate, review_record):
    return _admit(candidate, review_record)


def publish_textbook(root, manifest, cards):
    """Exclusive publication. Existing directory is always refused."""
    root = Path(root)
    if root.exists():
        raise FileExistsError('Refusing to overwrite textbook: ' + str(root))
    manifest = load_json(manifest)
    public_only(manifest)
    if not isinstance(manifest, dict) or not isinstance(manifest.get('book_id'), str) or not manifest['book_id']:
        raise ValueError('manifest.book_id is required')
    cards = copy.deepcopy(list(cards))
    if not cards or len({c['id'] for c in cards}) != len(cards):
        raise ValueError('Nonempty unique admitted cards required')
    for card in cards:
        if card.get('status') != 'admitted' or not card.get('approvedlesson'):
            raise ValueError('Only admitted cards may be published')
        original = copy.deepcopy(card)
        original.update(status='candidate', approvedlesson=None)
        rechecked = _admit(original, {'path': card['approvedlesson']['review_path'],
                                      'record_id': card['approvedlesson'].get('review_record_selector')})
        if rechecked != card:
            raise ValueError('Admitted lesson differs from independent review record')
        provenance = _provenance(card['provenance'], manifest)
        if provenance['blocked_reasons']:
            raise ValueError('Manifest holdout overlaps a textbook source/family')
        public_only(card)
    book = {'schema': SCHEMA, 'manifest': manifest, 'cards': cards}
    payload = json.dumps(book, ensure_ascii=False, indent=2, allow_nan=False)
    root.mkdir(parents=True, exist_ok=False)
    with (root / 'textbook.json').open('x', encoding='utf-8') as handle:
        handle.write(payload + '\n')
    return root / 'textbook.json'


def lookup(book, query='', module=None, ids=None, limit=3, maxbytes=8192, *, retrieval_capacity=None):
    complete = retrieval_capacity == {'complete_records': True}
    if retrieval_capacity is not None and not complete:
        raise ValueError('Unknown host retrieval capacity')
    if type(limit) is not int or not 1 <= limit <= 3 or type(maxbytes) is not int or not 128 <= maxbytes <= 8192:
        raise ValueError('lookup requires 1..3 cards and 128..8192 UTF-8 bytes')
    if isinstance(book, (str, os.PathLike)):
        path = Path(book)
        book = load_json(path / 'textbook.json' if path.is_dir() else path)
    if not isinstance(query, str) or (module is not None and not isinstance(module, str)):
        raise ValueError('query and module must be strings')
    if ids is not None and (not isinstance(ids, (list, tuple)) or any(not isinstance(i, str) for i in ids)):
        raise ValueError('ids must be a list of strings')
    if not isinstance(book, dict) or book.get('schema') != SCHEMA:
        raise ValueError('Not a published textbook')
    public_only(book)
    out = {'schema': SCHEMA, 'cards': [], 'truncated': False}
    matches = []
    for card in book.get('cards', []):
        if card.get('status') != 'admitted' or not card.get('approvedlesson') or card.get('provenance', {}).get('blocked_reasons'):
            raise ValueError('Unadmitted/blocked card in textbook')
        if (module is None or card.get('module') == module) and (ids is None or card['id'] in ids) and all(
                token in json.dumps(card, ensure_ascii=False).casefold() for token in query.casefold().split()):
            matches.append(card)
    for card in matches:
        # A stable retrieval view, not arbitrary truncation of a raw archive card.
        # Keep the full approved lesson and enough exact IDs to retrieve original
        # settings/public submissions; never retransmit the whole native snapshot.
        projection = {key: copy.deepcopy(card[key]) for key in (
            'id', 'unit_id', 'module', 'scene_id', 'status', 'scope', 'approvedlesson', 'round_dir')}
        projection['retrieval_view'] = 'approved_lesson_with_actual_evidence_refs'
        projection['facts'] = [{key: copy.deepcopy(fact.get(key)) for key in (
            'fact_type', *REF_FIELDS, 'prediction_receipt_id', 'review_request_id',
            'timeline_path', 'backend_fact_id')} for fact in card['facts']]
        projection['model_claims'] = {'disposition': 'archived_model_claims_not_facts',
                                      'access': 'Use referenced timeline public_submission and actual receipt tool input'}
        proposal = {**out, 'cards': out['cards'] + [projection], 'truncated': True}
        size = len(json.dumps(proposal, ensure_ascii=False, allow_nan=False).encode('utf-8'))
        if not complete and (len(out['cards']) >= limit or size > maxbytes):
            out['truncated'] = True
            continue
        out['cards'].append(projection)
    return out
