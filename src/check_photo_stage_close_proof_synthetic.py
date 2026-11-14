"""Publication-only synthetic close-proof checks; no field results or model/Native.

Constructs request/receipt/action-close records on disk, then invokes the actual
read-only proof builder. No supplied proof is trusted without rebuilding it.
"""
import copy
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
from photo_stage_close_proof import build_close_proof, validate_close_proof

LATEST = SRC.parent / 'experiments/validation/latest'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class CloseProofSyntheticChecks(unittest.TestCase):
    def setUp(self):
        LATEST.mkdir(parents=True, exist_ok=True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(
            prefix='close-proof-SYNTHETIC-', dir=LATEST)))
        self.source = self.root / 'experiments/runs/SYNTHETIC-source'
        self.folder = self.source / 'editing/DSC_3734/r1/module_support'
        self.guards = [self.stack.enter_context(patch.object(owner, name,
            side_effect=AssertionError('Offline close-proof: network/process forbidden')))
            for owner, name in ((socket, 'create_connection'), (socket.socket, 'connect'), (subprocess, 'Popen'))]
        self.addCleanup(self.assert_no_io)
        self.edit = {'contract_revision': 'stage-contract/v3:SYNTHETIC',
            'review': {'visible_comparison': 'SYNTHETIC only'},
            'action': {'name': 'photo_edit', 'arguments': {'base_version': 'v0',
                'values': {'light.exposure': 0.5}, 'rationale': 'SYNTHETIC stored record'}}}
        self.rejected = {'contract_revision': 'stage-contract/v3:SYNTHETIC',
            'review': {'visible_comparison': 'SYNTHETIC only'},
            'action': {'name': 'advance', 'arguments': {'reason': 'SYNTHETIC rejection'}}}
        self.state = {'pending': None, 'current': 'v1', 'status': 'working',
            'workflow': {}, 'versions': {'v0': {}, 'v1': {'operation_id': 'SYNTHETIC-operation'}},
            'history': [{'action': 'edit', 'version': 'v1', 'operation_id': 'SYNTHETIC-operation'}],
            'workflow_submissions': [{'submission': self.edit, 'normalized_submission': self.edit,
                'result_version': 'v1', 'stage_turn_result': {'kind': 'completed'}}],
            'stage_rejections': [{'id': 'SYNTHETIC-rejection'}]}
        self.no_effect = {'disposition': 'rejected_before_execution', 'execution_status': 'not_executed',
            'recoverable': True, 'rejection_ref': 'SYNTHETIC-rejection', 'no_effect': {'passed': True,
                'native_execution_started': False, 'pending': None, 'versions_unchanged': True,
                'ledger_unchanged': True, 'photography_unchanged': True}}
        self.put('editing/DSC_3734/r1/module_support/state.json', self.state)
        self.put('editing/DSC_3734/r1/module_support/stage-attempt.json',
            {'status': 'diagnosed_unfinished', 'finished_at': 'SYNTHETIC-finished'})
        self.put('supervised-window-result.json', {'finished_at': 'SYNTHETIC-finished',
            'paid_enabled_after': False, 'paid_lease_after': False, 'dispatch_pending_after': False})
        self.put('authorization/windows/SYNTHETIC/window.json', {'id': 'SYNTHETIC-window'})
        self.put('authorization/windows/SYNTHETIC/closed.json', {'restored': True, 'closed_at': 'SYNTHETIC-closed'})
        self.put('native-render-ledger.json', {'issued': 1, 'claims': [{'id': 'SYNTHETIC-claim', 'status': 'completed'}]})
        self.make_receipt('SYNTHETIC-edit-request', 1, self.edit, {'disposition': 'executed'})
        self.make_receipt('SYNTHETIC-reject-request', 2, self.rejected, self.no_effect)

    def assert_no_io(self):
        for guard in self.guards:
            guard.assert_not_called()

    def put(self, relative, value):
        write(self.source / relative, value)

    def get(self, relative):
        return json.loads((self.source / relative).read_text(encoding='utf-8'))

    def make_receipt(self, rid, sequence, submission, action):
        request = {'request_id': rid, 'sequence': sequence, 'purpose': 'SYNTHETIC-close-proof',
            'model': 'SYNTHETIC-never-called', 'service_tier': 'standard'}
        self.put('requests/' + rid + '.json', request)
        self.put('receipts/' + rid + '.json', {**request, 'usage': {'input_tokens': 11, 'output_tokens': 7,
            'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0},
            'public_content': [{'type': 'tool_use', 'id': 'call:' + rid, 'name': 'photo_stage_turn', 'input': submission}],
            'actions': [{**copy.deepcopy(action), 'call_id': 'call:' + rid, 'name': 'photo_stage_turn'}]})

    def build(self):
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.source.rglob('*') if p.is_file()}
        try:
            return build_close_proof(self.source, self.root)
        finally:
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.source.rglob('*') if p.is_file()}, 'Builder must never mutate records')

    def test_actual_request_receipt_and_action_close_rebuild(self):
        proof = self.build()
        self.assertEqual([row['classification'] for row in proof['receipt_proof']],
            ['completed_workflow_action', 'proven_not_executed'])
        self.assertEqual(proof['state_current'], 'v1')
        self.assertEqual(proof['state_status'], 'working')
        self.assertFalse(proof['native_operations_replayed'])
        self.assertFalse(proof['hash_verification'])
        self.assertEqual(validate_close_proof(proof, self.source, self.root), proof)
        altered = copy.deepcopy(proof)
        altered['receipt_proof'][0]['usage']['output_tokens'] += 1
        with self.assertRaisesRegex(ValueError, 'differs'):
            validate_close_proof(altered, self.source, self.root)

    def test_exact_normalized_execution_and_public_json_text(self):
        relative = 'receipts/SYNTHETIC-edit-request.json'
        receipt = self.get(relative)
        canonical = copy.deepcopy(self.edit)
        canonical['review']['visible_comparison'] = 'SYNTHETIC canonical form'
        receipt['normalized_calls'] = [{'id': receipt['actions'][0]['call_id'], 'name': 'photo_stage_turn', 'input': canonical}]
        self.state['workflow_submissions'][0]['normalized_submission'] = canonical
        self.put('editing/DSC_3734/r1/module_support/state.json', self.state)
        self.put(relative, receipt)
        self.build()
        for fenced in (False, True):
            with self.subTest(fenced=fenced):
                value = copy.deepcopy(receipt)
                text = json.dumps(self.edit)
                value['public_content'] = [{'type': 'text', 'text': '```json\n' + text + '\n```' if fenced else text}]
                value['actions'][0]['call_id'] = 'text-call:SYNTHETIC-edit-request'
                value['normalized_calls'][0]['id'] = value['actions'][0]['call_id']
                self.put(relative, value)
                self.build()

    def test_adapter_no_execution_proof_must_be_exact(self):
        relative = 'receipts/SYNTHETIC-reject-request.json'
        receipt = self.get(relative)
        receipt['actions'][0].update(proof_origin_adapter=True, proof='adapter-before-sdk-no-native-call')
        self.put(relative, receipt)
        self.build()
        receipt['actions'][0]['proof'] = 'not-exact'
        self.put(relative, receipt)
        with self.assertRaisesRegex(ValueError, 'adapter rejection'):
            self.build()

    def test_operator_hold_requires_exact_session_call_result_and_arguments(self):
        relative = 'receipts/SYNTHETIC-reject-request.json'
        receipt = self.get(relative)
        call_id = receipt['actions'][0]['call_id']
        proof = {'schema': 'stage-operator-hold-before-worker/v1', 'request_id': 'SYNTHETIC-reject-request',
            'call_id': call_id, 'native_execution_started': False, 'session_log': 'SYNTHETIC-session.jsonl',
            'tool_call_sequence': 3, 'tool_result_sequence': 4, 'tool_call_time': 'SYNTHETIC-call-time',
            'tool_result_time': 'SYNTHETIC-result-time'}
        pair = [{'seq': 3, 'type': 'tool/call', 'time': proof['tool_call_time'],
            'data': {'callId': call_id, 'name': 'photo_stage_turn', 'arguments': json.dumps(self.rejected)}},
            {'seq': 4, 'type': 'tool/result', 'time': proof['tool_result_time'], 'data': {'message': {
                'source': {'callId': call_id}, 'content': [{'toolCallId': call_id, 'isError': True,
                    'content': [{'type': 'text', 'text': 'Error: Paid dispatch stopped: lab-timing-transport-hold.json'}]}]}}}]
        log = self.source / proof['session_log']
        log.write_text('\n'.join(map(json.dumps, pair)) + '\n', encoding='utf-8')
        receipt['actions'][0].update(proof_origin_operator=True, operator_proof=proof)
        self.put(relative, receipt)
        supervised = self.get('supervised-window-result.json')
        supervised['dispatch_pending_after'] = True
        self.put('supervised-window-result.json', supervised)
        self.build()
        pair[0]['data']['arguments'] = json.dumps({'different': True})
        log.write_text('\n'.join(map(json.dumps, pair)) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'arguments differ'):
            self.build()

    def test_unsettled_state_attempt_supervisor_and_pre_tone_are_rejected(self):
        cases = [('editing/DSC_3734/r1/module_support/state.json', 'pending', {'unknown': True}),
            ('editing/DSC_3734/r1/module_support/stage-attempt.json', 'finished_at', None),
            ('editing/DSC_3734/r1/module_support/stage-attempt.json', 'status', 'started'),
            ('supervised-window-result.json', 'finished_at', None),
            ('supervised-window-result.json', 'paid_enabled_after', True),
            ('supervised-window-result.json', 'paid_lease_after', True),
            ('supervised-window-result.json', 'dispatch_pending_after', True),
            ('editing/DSC_3734/r1/module_support/state.json', 'workflow', {'pre_tone_wb': {'status': 'in_flight'}})]
        for relative, key, value in cases:
            with self.subTest(key=key, value=value):
                original = self.get(relative)
                self.put(relative, {**original, key: value})
                with self.assertRaises(ValueError):
                    self.build()
                self.put(relative, original)

    def test_active_markers_and_missing_authorization_close_are_rejected(self):
        for name in ('active.lease', 'stage-dispatch-pending.json', 'native-cleanup-unknown.json'):
            with self.subTest(marker=name):
                marker = self.source / name
                marker.write_text('SYNTHETIC', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'active/unknown'):
                    self.build()
                marker.unlink()
        relative = 'authorization/windows/SYNTHETIC/closed.json'
        original = self.get(relative)
        for value in ({'restored': False, 'closed_at': 'SYNTHETIC'}, {'restored': True}):
            self.put(relative, value)
            with self.assertRaisesRegex(ValueError, 'restoration'):
                self.build()
        (self.source / relative).unlink()
        with self.assertRaisesRegex(ValueError, 'must be closed'):
            self.build()
        self.put(relative, original)

    def test_incomplete_native_claims_and_request_receipt_mismatch_are_rejected(self):
        for ledger in ({'issued': 2, 'claims': [{'status': 'completed'}]},
                       {'issued': 1, 'claims': [{'status': 'unknown'}]}):
            self.put('native-render-ledger.json', ledger)
            with self.assertRaisesRegex(ValueError, 'ledger'):
                self.build()
        self.put('native-render-ledger.json', {'issued': 1, 'claims': [{'status': 'completed'}]})
        receipt = self.source / 'receipts/SYNTHETIC-edit-request.json'
        receipt.unlink()
        with self.assertRaisesRegex(ValueError, 'count'):
            self.build()

    def test_usage_envelope_and_action_identity_are_rejected(self):
        relative = 'receipts/SYNTHETIC-edit-request.json'
        original = self.get(relative)
        candidates = []
        for key, value in (('request_id', 'wrong'), ('sequence', 99), ('service_tier', 'unknown')):
            candidates.append({**copy.deepcopy(original), key: value})
        for value in (None, -1, True):
            candidate = copy.deepcopy(original)
            candidate['usage']['input_tokens'] = value
            candidates.append(candidate)
        for key, value in (('call_id', 'wrong'), ('name', 'wrong'), ('disposition', 'unknown')):
            candidate = copy.deepcopy(original)
            candidate['actions'][0][key] = value
            candidates.append(candidate)
        candidates.append({**copy.deepcopy(original), 'actions': []})
        candidates.append({**copy.deepcopy(original), 'rejected_action': True})
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.put(relative, candidate)
                with self.assertRaises(ValueError):
                    self.build()

    def test_no_effect_and_executed_workflow_binding_cannot_be_forged(self):
        relative = 'receipts/SYNTHETIC-reject-request.json'
        original = self.get(relative)
        for key, value in (('native_execution_started', True), ('pending', {}), ('versions_unchanged', False),
                           ('ledger_unchanged', False), ('photography_unchanged', False)):
            candidate = copy.deepcopy(original)
            candidate['actions'][0]['no_effect'][key] = value
            self.put(relative, candidate)
            with self.assertRaises(ValueError):
                self.build()
        self.put(relative, original)
        state_path = 'editing/DSC_3734/r1/module_support/state.json'
        for key, value in (('stage_rejections', []), ('workflow_submissions', []), ('history', []),
                           ('versions', {'v1': {}})):
            self.put(state_path, {**copy.deepcopy(self.state), key: value})
            with self.assertRaises(ValueError):
                self.build()

    def test_source_must_be_below_runs_root(self):
        outside = self.root / 'not-runs'
        outside.mkdir()
        with self.assertRaises(ValueError):
            build_close_proof(outside, self.root)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CloseProofSyntheticChecks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    write(LATEST / 'publication-close-proof-synthetic-summary.json', {
        'publication_derivative': True, 'tests': result.testsRun, 'passed': result.wasSuccessful(),
        'failures': len(result.failures), 'errors': len(result.errors), 'field_run_artifacts_read': False,
        'model_or_native_called': False, 'hashes_computed': False})
    raise SystemExit(0 if result.wasSuccessful() else 1)
