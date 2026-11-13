"""Shared public decision records extracted from dsh_photo_memory.py; no CLI.

This module performs no model calls and never infers a photographic outcome.
"""
import copy
import json

LIMIT = 4096
KINDS = {'issue', 'attempt'}
STATUSES = {'open', 'resolved', 'uncertain'}
OUTCOMES = {'improved', 'worse', 'mixed', 'uncertain'}


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def validate_review(review, versions):
    if not isinstance(review, dict):
        raise ValueError('review must be an object')
    for key in ('visible_comparison', 'remaining_gaps', 'uncertainty'):
        if not isinstance(review.get(key), str):
            raise ValueError(f'{key} must be a string')
    outcome = review.get('previous_outcome', 'uncertain')
    if outcome not in OUTCOMES:
        raise ValueError('invalid previous_outcome')
    preference = review.get('preferred_version')
    if preference is not None and not isinstance(preference,str):
        raise ValueError('preferred_version must be a string')
    reason = review.get('preference_reason', '')
    if not isinstance(reason, str):
        raise ValueError('preference_reason must be a string')
    updates = review.get('memory_updates', [])
    if not isinstance(updates, list):
        raise ValueError('memory updates must be a list')
    for item in updates:
        if not isinstance(item, dict) or item.get('kind') not in KINDS:
            raise ValueError('invalid memory kind')
        if item.get('status', 'open') not in STATUSES:
            raise ValueError('invalid memory status')
        if not isinstance(item.get('text'), str) or not item['text']:
            raise ValueError('memory text must be a nonempty string')
        if not isinstance(item.get('focus', ''), str):
            raise ValueError('memory focus must be a string')
        if item.get('id') is not None and not isinstance(item['id'], str):
            raise ValueError('invalid memory id')
    return review


def record(state, review, observation, changed_controls=()):
    """Store only public model claims, with identifiers supplied by the host."""
    validate_review(review, state['versions'])
    if observation.get('version') not in state['versions'] or not observation.get('id'):
        raise ValueError('unknown observation version or id')
    preferred = review.get('preferred_version')
    preference_valid = preferred is not None and preferred in state['versions'] and (
        preferred == observation['version'] or preferred in state.get('reviewed',[]))
    archive = state.setdefault('decision_memory', [])
    source = {'observation_id': observation['id'], 'version': observation['version'],
              'parent': observation.get('parent'), 'changed_controls': sorted(set(changed_controls))}
    known = {item['id']: item for item in archive}
    for update in review.get('memory_updates', []):
        prior = update.get('id')
        if prior is not None and prior not in known:
            raise ValueError('unknown memory id')
        if prior is not None and known[prior]['kind'] != update['kind']:
            raise ValueError('memory kind cannot change')
    for update in review.get('memory_updates', []):
        prior = update.get('id')
        item = {'id': f'm{len(archive)+1:05d}', 'supersedes': prior,
                'kind': update['kind'], 'status': update.get('status', 'open'),
                'text': update['text'], 'focus': update.get('focus', ''),
                'claim_type': 'model_judgment', **source}
        archive.append(item)
        known[item['id']] = item
    decision = {'id':f"d{len(state.get('public_decisions',[]))+1:05d}",
                'observation_id': source['observation_id'], 'version': source['version'],
                'visible_comparison': review['visible_comparison'],
                'remaining_gaps': review['remaining_gaps'], 'uncertainty': review['uncertainty'],
                'previous_outcome': review.get('previous_outcome', 'uncertain'),
                'review_quality': review.get('review_quality','explicit')}
    if review.get('unparsed_fields'):
        decision['unparsed_fields'] = copy.deepcopy(review['unparsed_fields'])
    if preferred is not None and not preference_valid:
        decision['unresolved_preferred_version'] = preferred
        decision['preference_note'] = 'Unrecognized or unreviewed version; no candidate was selected.'
        state['preferred_candidate'] = None
    state.setdefault('public_decisions', []).append(decision)
    if preference_valid:
        state['preferred_candidate'] = {'version': review['preferred_version'],
                                        'reason': review.get('preference_reason', ''),
                                        'observation_id': observation['id'],
                                        'claim_type': 'model_judgment'}
    return decision


def _live(archive):
    superseded = {item.get('supersedes') for item in archive}
    return [item for item in archive if item['id'] not in superseded]


def card(state, observation, recent_actions=(), limit=LIMIT):
    """Build a bounded card. Never shorten an individual record to fit."""
    if limit < 256:
        raise ValueError('memory limit too small')
    current = observation['version']
    live = _live(state.get('decision_memory', []))
    # Current evidence first, then open issues, then recent records. No semantic
    # claim is made about a control's effect on an image region.
    touched = set(recent_actions[-1].get('changed_controls', [])) if recent_actions else set()
    def rank(item):
        return (item['observation_id'] == observation['id'],
                item['version'] == current,
                item['kind'] == 'issue' and item['status'] != 'resolved',
                bool(touched.intersection(item.get('changed_controls', []))), item['id'])
    ranked = sorted(live, key=rank, reverse=True)
    out = {'latest_review': None, 'recent_actions': [], 'issues': [], 'attempts': [],
           'preferred_candidate': None, 'omitted_ids': []}
    decisions = state.get('public_decisions', [])
    candidates = []
    if decisions:
        item = copy.deepcopy(decisions[-1])
        item['validity'] = 'current_observation' if item['observation_id'] == observation['id'] else 'historical_recheck'
        candidates.append(('latest_review', item))
    for action in list(recent_actions)[-2:]:
        candidates.append(('recent_actions', copy.deepcopy(action)))
    counts = {'issues': 0, 'attempts': 0}
    for item in ranked:
        key = 'issues' if item['kind'] == 'issue' else 'attempts'
        if key == 'issues' and item['status'] == 'resolved':
            continue
        if counts[key] >= 3:
            continue
        counts[key] += 1
        projected = copy.deepcopy(item)
        projected['validity'] = 'current_observation' if item['observation_id'] == observation['id'] else 'historical_recheck'
        candidates.append((key, projected))
    if state.get('preferred_candidate'):
        candidates.append(('preferred_candidate', copy.deepcopy(state['preferred_candidate'])))
    for key, value in candidates:
        proposal = copy.deepcopy(out)
        if isinstance(proposal[key], list): proposal[key].append(value)
        else: proposal[key] = value
        if size(proposal) <= limit:
            out = proposal
        elif isinstance(value, dict) and value.get('id'):
            out['omitted_ids'].append(value['id'])
    # Omitted IDs are optional navigation hints, not a second unbounded index.
    while size(out) > limit and out['omitted_ids']:
        out['omitted_ids'].pop()
    if size(out) > limit:
        raise ValueError('memory card exceeds limit')
    return out


def history(state, *, ids=None, version=None, query=None, offset=0, page_size=5):
    if not isinstance(offset, int) or offset < 0 or not isinstance(page_size, int) or page_size < 1:
        raise ValueError('invalid history page')
    if version is not None and version not in state['versions']:
        raise ValueError('unknown version')
    if query is not None and not isinstance(query,str):
        raise ValueError('invalid query')
    if ids is not None and (not isinstance(ids,list) or any(not isinstance(x,str) for x in ids)):
        raise ValueError('invalid history IDs')
    entries = copy.deepcopy(state.get('decision_memory', []))
    entries.extend({'kind':'decision',**copy.deepcopy(x)} for x in state.get('public_decisions',[]))
    for index, item in enumerate(state.get('history',[])):
        if item.get('action') not in ('edit','observe','history','discover','finish','unable'): continue
        arguments=item.get('arguments',{})
        entry={'id':f'h{index:05d}','kind':'action','version':item.get('version'),
               'action':item['action'],'parent':item.get('parent'),
               'changed_controls':item.get('changed_controls',[]),
               'arguments':{k:copy.deepcopy(arguments[k]) for k in ('values','settings','commands','rationale','predicted_effect') if k in arguments}}
        if size(entry)>3000:
            entry['arguments']={'detail':'Complete original parameters remain in the local action archive.'}
        entries.append(entry)
    entries.extend({'id':key,'kind':'version','version':key,'parent':value.get('parent'),
                    'reviewed':key in state.get('reviewed',[])} for key,value in state['versions'].items())
    ids = set(ids or [])
    if ids and not ids.issubset({x['id'] for x in entries}):
        raise ValueError('unknown memory id')
    found = [x for x in entries if (not ids or x['id'] in ids)
             and (not version or x['version'] == version)
             and (not query or query.casefold() in json.dumps(x, ensure_ascii=False).casefold())]
    result = {'items': [], 'next_offset': None}
    for item in found[offset:offset+page_size]:
        candidate = {**result, 'items': result['items'] + [copy.deepcopy(item)]}
        if size(candidate) > LIMIT:
            break
        result = candidate
    next_offset = offset + len(result['items'])
    result['next_offset'] = next_offset if next_offset < len(found) else None
    if size(result) > LIMIT or (not result['items'] and found[offset:]):
        raise ValueError('single history entry exceeds limit')
    return result
