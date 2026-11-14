"""Explicit closed-run checkpoint transfer; never replays an operation or edits its source."""
import copy
import json
from pathlib import Path


def load_continuation(round_dir, root):
    round_dir, root = Path(round_dir).resolve(), Path(root).resolve()
    file = round_dir / 'qualification/continuation.json'
    if not file.exists():
        return None
    record = json.loads(file.read_text(encoding='utf-8'))
    if record.get('schema') != 'photo-stage-continuation/v1' or record.get('native_operations_replayed') is not False:
        raise ValueError('Explicit verified continuation required')
    source_round = (root / record['source_round']).resolve(strict=True)
    source_round.relative_to(root / 'experiments/runs')
    if source_round == round_dir:
        raise ValueError('Continuation must preserve a distinct closed source round')
    source = source_round / 'editing/DSC_3734/r1/module_support/state.json'
    if json.loads(source.read_text(encoding='utf-8')) != record['source_state_snapshot']:
        raise ValueError('Closed continuation source changed')
    source_state = record['source_state_snapshot']
    if record.get('source_close_proof') is not None:
        from photo_stage_close_proof import validate_close_proof
        validate_close_proof(record['source_close_proof'], source_round, root)
        allowed_statuses = ('working', 'engineering_diagnosis_required', 'diagnosed_unfinished', 'paused_budget', 'paused_nonprogress')
    else:
        allowed_statuses = ('diagnosed_unfinished', 'paused_budget', 'paused_nonprogress')
    if source_state.get('pending') is not None or source_state.get('status') not in allowed_statuses:
        raise ValueError('Only a reconciled, explicitly paused or strongly proved closed source can be transferred')
    ledger = json.loads((source_round / 'native-render-ledger.json').read_text(encoding='utf-8'))
    if ledger != record['source_native_ledger_snapshot'] or any(c.get('status') != 'completed' for c in ledger['claims']):
        raise ValueError('Continuation native accounting unknown or changed')
    for name in ('stage-dispatch-pending.json', 'stage-dispatch.lease'):
        if (source_round / name).exists():
            raise ValueError('Continuation source has in-flight work')
    state = copy.deepcopy(record['continued_state'])
    if state.get('pending') is not None or state.get('status') != 'working' or state.get('current') != record['selected_version']:
        raise ValueError('Invalid continued state')
    from photo_pre_tone import validate_transfer
    validate_transfer(record['source_state_snapshot'], state)
    if state['versions'][state['current']]['settings'] != record['selected_settings']:
        raise ValueError('Continuation settings mismatch')
    for item in record['copied_files']:
        path = (round_dir / item['path']).resolve(strict=True)
        path.relative_to(round_dir)
        original = (source_round / item['source_path']).resolve(strict=True)
        original.relative_to(source_round)
        if path.stat().st_size != item['bytes'] or path.read_bytes() != original.read_bytes():
            raise ValueError('Continuation artifact differs from actual source: ' + item['path'])
    for version in state['versions'].values():
        Path(version['master']).resolve(strict=True).relative_to(round_dir)
    return record
