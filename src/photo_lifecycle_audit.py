"""Append-only public audit snapshots, separate from photographic runtime decisions."""
import datetime
import json
import os
from pathlib import Path
import uuid

from photo_textbook import public_only


def record_lifecycle(folder, state, kind, *, submission=None, parent_turn_id=None, error=None):
    public_only(state)
    if submission is not None:
        public_only(submission)
    folder = Path(folder)
    ident = 'audit_' + uuid.uuid4().hex
    relative = 'audit/states/' + ident + '.json'
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    observation = state.get('observations', [{}])[-1]
    row = {'schema': 'photo-lifecycle-audit/v1', 'id': ident,
           'time': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'kind': kind,
           'observation_id': observation.get('id'), 'phase': state.get('workflow', {}).get('phase'),
           'version': state.get('current'), 'state_ref': relative,
           'claim_type': 'public_runtime_state_not_hidden_reasoning',
           'dispatch_claim': 'none; actual request/receipt remains delivery authority'}
    if parent_turn_id is not None:
        row['parent_turn_id'] = parent_turn_id
    if submission is not None:
        row['submission'] = submission
    if error is not None:
        row['error'] = str(error)
    with (folder / 'audit/events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    return ident
