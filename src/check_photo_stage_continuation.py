"""Offline exact-artifact checkpoint transfer tests; no Native/model/hash."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from photo_stage_continuation import load_continuation


class ContinuationChecks(unittest.TestCase):
    def test_pre_tone_lifecycle_cannot_be_erased_or_replayed_by_transfer(self):
        from photo_pre_tone import validate_transfer
        record = {'status': 'succeeded', 'base_version': 'v0', 'child_version': 'v1',
                  'operation_id': 'synthetic-op', 'effect_review_observation_id': 'o2'}
        source = {'workflow': {'pre_tone_wb': record}}
        continued = copy.deepcopy(source)
        validate_transfer(source, continued)
        for changed in ({'workflow': {}}, {'workflow': {'pre_tone_wb': {**record, 'operation_id': 'invented'}}}):
            with self.assertRaisesRegex(ValueError, 'preserve'):
                validate_transfer(source, changed)
        unknown = {'workflow': {'pre_tone_wb': {**record, 'status': 'in_flight'}}}
        with self.assertRaisesRegex(ValueError, 'in-flight/unknown'):
            validate_transfer(unknown, copy.deepcopy(unknown))

    def test_explicit_closed_source_and_actual_artifact_identity(self):
        latest = Path(__file__).resolve().parents[1] / 'experiments/validation/latest'
        latest.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='continuation-SYNTHETIC-', dir=latest) as directory:
            root = Path(directory)
            source = root / 'experiments/runs/source'
            target = root / 'experiments/runs/target'
            folder = source / 'editing/DSC_3734/r1/module_support'
            folder.mkdir(parents=True)
            (target / 'qualification').mkdir(parents=True)
            (target / 'artifacts').mkdir()
            (source / 'actual.png').write_bytes(b'clearly-labelled-synthetic-artifact')
            (target / 'artifacts/v3.png').write_bytes((source / 'actual.png').read_bytes())
            state = {'status': 'diagnosed_unfinished', 'pending': None, 'current': 'v3'}
            (folder / 'state.json').write_text(json.dumps(state), encoding='utf-8')
            ledger = {'issued': 1, 'claims': [{'status': 'completed'}]}
            (source / 'native-render-ledger.json').write_text(json.dumps(ledger), encoding='utf-8')
            continued = {'status': 'working', 'pending': None, 'current': 'v3',
                         'versions': {'v3': {'settings': {'light': {'contrast': 20}},
                                            'master': str(target / 'artifacts/v3.png')}}}
            record = {'schema': 'photo-stage-continuation/v1', 'native_operations_replayed': False,
                      'source_round': source.relative_to(root).as_posix(), 'selected_version': 'v3',
                      'source_state_snapshot': state, 'source_native_ledger_snapshot': ledger,
                      'continued_state': continued, 'selected_settings': continued['versions']['v3']['settings'],
                      'copied_files': [{'path': 'artifacts/v3.png', 'source_path': 'actual.png',
                                        'bytes': (source / 'actual.png').stat().st_size}]}
            path = target / 'qualification/continuation.json'
            path.write_text(json.dumps(record), encoding='utf-8')
            self.assertEqual(load_continuation(target, root), record)
            for change in ('pending', 'working', 'settings', 'bytes'):
                candidate = copy.deepcopy(record)
                if change == 'pending':
                    candidate['source_state_snapshot']['pending'] = {'unknown': True}
                elif change == 'working':
                    candidate['source_state_snapshot']['status'] = 'working'
                elif change == 'settings':
                    candidate['selected_settings'] = {}
                else:
                    candidate['copied_files'][0]['bytes'] += 1
                path.write_text(json.dumps(candidate), encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_continuation(target, root)
            path.write_text(json.dumps(record), encoding='utf-8')
            (target / 'artifacts/v3.png').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                load_continuation(target, root)


if __name__ == '__main__':
    unittest.main(verbosity=2)
