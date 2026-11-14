"""Rebuild a closed source proof from actual records; no mutation, replay or hashing."""
import json
import re
from pathlib import Path


def _read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _no_effect(action):
    proof = action.get('no_effect') or {}
    return (action.get('disposition') == 'rejected_before_execution'
            and action.get('execution_status') == 'not_executed'
            and action.get('recoverable') is True and proof.get('passed') is True
            and proof.get('native_execution_started') is False and proof.get('pending', 'missing') is None
            and all(proof.get(key) is True for key in ('versions_unchanged', 'ledger_unchanged', 'photography_unchanged'))
            and bool(action.get('rejection_ref')))


def validate_operator_hold_proof(proof, source_round, request_id, call_id):
    source = Path(source_round).resolve(strict=True)
    if (proof.get('schema') != 'stage-operator-hold-before-worker/v1'
            or proof.get('request_id') != request_id or proof.get('call_id') != call_id
            or proof.get('native_execution_started') is not False):
        raise ValueError('Exact operator-hold proof identity is required')
    log = (source / proof['session_log']).resolve(strict=True)
    log.relative_to(source)
    found = []
    for line in log.open(encoding='utf-8'):
        record = json.loads(line)
        if record.get('seq') in (proof['tool_call_sequence'], proof['tool_result_sequence']):
            found.append(record)
    if len(found) != 2 or [row['type'] for row in found] != ['tool/call', 'tool/result']:
        raise ValueError('Original exact tool call/result pair is missing')
    call, result = found
    data = result['data']['message']
    blocks = data.get('content', [])
    if (call['data'].get('callId') != call_id or call['data'].get('name') != 'photo_stage_turn'
            or data.get('source', {}).get('callId') != call_id or len(blocks) != 1
            or blocks[0].get('toolCallId') != call_id or blocks[0].get('isError') is not True
            or blocks[0].get('content') != [{'type': 'text', 'text': 'Error: Paid dispatch stopped: lab-timing-transport-hold.json'}]
            or call.get('time') != proof['tool_call_time'] or result.get('time') != proof['tool_result_time']):
        raise ValueError('Exact before-worker hold error is not proved')
    receipt = _read(source / 'receipts' / (request_id + '.json'))
    actual_calls = receipt.get('normalized_calls') or [row for row in receipt['public_content'] if row.get('type') == 'tool_use']
    actual = next((row for row in actual_calls if row.get('id') == call_id and row.get('name') == 'photo_stage_turn'), None)
    if actual is None or json.loads(call['data']['arguments']) != actual['input']:
        raise ValueError('Operator proof arguments differ from the actual proposal')
    return proof


def build_close_proof(source_round, root):
    root = Path(root).resolve(strict=True)
    source = Path(source_round).resolve(strict=True)
    source.relative_to(root / 'experiments/runs')
    folder = source / 'editing/DSC_3734/r1/module_support'
    state = _read(folder / 'state.json')
    attempt = _read(folder / 'stage-attempt.json')
    supervised = _read(source / 'supervised-window-result.json')
    if (state.get('pending') is not None or not attempt.get('finished_at') or attempt.get('status') == 'started'
            or not supervised.get('finished_at') or supervised.get('paid_enabled_after') is not False
            or supervised.get('paid_lease_after') is not False):
        raise ValueError('Source attempt/supervisor/native operation is not closed')
    if state.get('workflow', {}).get('pre_tone_wb', {}).get('status') == 'in_flight':
        raise ValueError('Source coarse-WB outcome is unknown')
    for pattern in ('*.lease', 'stage-dispatch-pending.json', 'native-cleanup-unknown.json'):
        if any(source.rglob(pattern)):
            raise ValueError('Source has an active/unknown execution marker: ' + pattern)
    windows = sorted((source / 'authorization/windows').glob('*/window.json'))
    if not windows or any(not (path.parent / 'closed.json').is_file() for path in windows):
        raise ValueError('Every original authorization window must be closed')
    window_records = []
    for path in windows:
        closed = _read(path.parent / 'closed.json')
        if closed.get('restored') is not True or not closed.get('closed_at'):
            raise ValueError('Authorization restoration is not proved')
        window_records.append(closed)
    ledger = _read(source / 'native-render-ledger.json')
    if (ledger.get('issued') != len(ledger.get('claims', []))
            or any(claim.get('status') != 'completed' for claim in ledger['claims'])):
        raise ValueError('Source native ledger is not entirely completed')
    requests = {path.stem: _read(path) for path in (source / 'requests').glob('*.json')}
    receipts = {path.stem: _read(path) for path in (source / 'receipts').glob('*.json')}
    if set(requests) != set(receipts):
        raise ValueError('Source request/receipt count is not reconciled')
    proofs = []
    for rid, request in sorted(requests.items(), key=lambda item: item[1]['sequence']):
        receipt = receipts[rid]
        if receipt.get('request_id') != rid or any(receipt.get(k) != value for k, value in request.items()):
            raise ValueError('Source exact request/receipt envelope mismatch')
        usage = receipt.get('usage', {})
        if (receipt.get('service_tier') != 'standard' or
                any(type(usage.get(key)) is not int or usage[key] < 0 for key in
                    ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'))):
            raise ValueError('Source usage identity is not known')
        calls = [block for block in receipt.get('public_content', []) if block.get('type') == 'tool_use']
        if not calls:
            texts = [block['text'].strip() for block in receipt.get('public_content', [])
                     if block.get('type') == 'text' and block.get('text', '').strip()]
            if len(texts) == 1:
                text = texts[0]
                fenced = re.fullmatch(r'```(?:json)?\s*\n([\s\S]*?)\n```', text, flags=re.IGNORECASE)
                if fenced:
                    text = fenced.group(1).strip()
                try:
                    submission = json.loads(text)
                except ValueError:
                    submission = None
                if isinstance(submission, dict) and all(key in submission for key in ('contract_revision', 'review', 'action')):
                    calls = [{'id': 'text-call:' + rid, 'name': 'photo_stage_turn', 'input': submission,
                              'source': 'exact-public-json-text'}]
        normalized = receipt.get('normalized_calls', [])
        actions = receipt.get('actions', [])
        if len(calls) != 1 or len(actions) != 1 or receipt.get('rejected_action'):
            raise ValueError('Source exact single-call settlement is not proved')
        call, action = calls[0], actions[0]
        if action.get('call_id') != call.get('id') or action.get('name') != call.get('name'):
            raise ValueError('Source action call identity differs')
        execution = next((row['input'] for row in normalized if row.get('id') == call['id'] and row.get('name') == call['name']), call.get('input'))
        if _no_effect(action):
            if action.get('proof_origin_adapter'):
                if action.get('proof') != 'adapter-before-sdk-no-native-call':
                    raise ValueError('Source adapter rejection proof is not exact')
            elif action.get('proof_origin_operator'):
                validate_operator_hold_proof(action.get('operator_proof', {}), source, rid, call['id'])
            elif not any(row.get('id') == action.get('rejection_ref') for row in state.get('stage_rejections', [])):
                raise ValueError('Source worker rejection has no retained record')
            classification = 'proven_not_executed'
        elif action.get('disposition') == 'executed':
            records = [row for row in state.get('workflow_submissions', [])
                       if execution in (row.get('normalized_submission'), row.get('submission'))
                       and (row.get('stage_turn_result') or {}).get('kind') not in ('fatal', 'rejected_before_execution')]
            if not records:
                raise ValueError('Source executed action has no exact completed workflow submission')
            chosen = records[-1]
            if (execution.get('action') or {}).get('name') in ('photo_edit', 'pre_tone_wb'):
                version = state['versions'].get(chosen.get('result_version'), {})
                if not version.get('operation_id') or not any(h.get('action') == 'edit'
                    and h.get('version') == chosen['result_version'] and h.get('operation_id') == version['operation_id']
                    for h in state.get('history', [])):
                    raise ValueError('Source real edit result has no exact version/history binding')
            classification = 'completed_workflow_action'
        else:
            raise ValueError('Source provider action result remains unknown')
        proofs.append({'request_id': rid, 'call_id': call['id'], 'classification': classification,
            'evidence_paths': ['requests/' + rid + '.json', 'receipts/' + rid + '.json'],
            'usage': usage, 'action': action})
    if supervised.get('dispatch_pending_after') is not False and not any(
            row['action'].get('proof_origin_operator') for row in proofs):
        raise ValueError('A formerly pending supervisor needs an exact operator settlement proof')
    return {'schema': 'photo-stage-source-close-proof/v1',
        'source_round': source.relative_to(root).as_posix(), 'attempt_finished': True,
        'supervisor_closed': True, 'native_claims_completed': True, 'no_leases': True,
        'provider_actions_unsettled': [], 'receipt_proof': proofs,
        'attempt_snapshot': attempt, 'supervisor_snapshot': supervised, 'authorization_closed': window_records,
        'state_current': state['current'], 'state_status': state['status'], 'native_ledger_snapshot': ledger,
        'native_operations_replayed': False, 'hash_verification': False}


def validate_close_proof(proof, source_round, root):
    if proof != build_close_proof(source_round, root):
        raise ValueError('Source close proof differs from actual completed records')
    return proof
