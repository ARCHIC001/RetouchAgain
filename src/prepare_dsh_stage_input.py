"""Explicit one-baseline image preparation for an independently prepared stage v2.

No model, authorization window, credentials or legacy preparer. The frozen Native
renderer exports full RAW before deriving previews. Failures retain accounting
and ownership for offline reconciliation; preparation never performs role review.
"""
import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dsh_stage_validation as stage
import lab_timing_experiment as lab
from photo_image_contract import assert_scaled_geometry, inspect_image
from raw_render_contract import inspect_dng

ROOT = stage.ROOT
BUILD = stage.BUILD


def _idle(root, round_dir):
    policy = stage.read(root / 'experiments/config/lab-paid-dispatch-policy.local.json')
    if policy.get('enabled') is not False:
        raise ValueError('Prepare only outside model dispatch windows; policy must be disabled')
    markers = [root / 'experiments/dsh-photo-paid.lease',
               round_dir / 'stage-dispatch.lease', round_dir / 'stage-dispatch-pending.json']
    markers += list(round_dir.rglob('*.lease'))
    markers += list(round_dir.rglob('stage-dispatch-pending.json'))
    markers += list(round_dir.rglob('native-cleanup-unknown.json'))
    for path in markers:
        if path.exists() or path.is_symlink():
            raise ValueError('Active or unknown execution marker; never replay: ' + str(path))
    # Paid hold/circuit markers deliberately remain in place during explicit,
    # local-only preparation. Never call dispatch_precheck or open a paid window.


def _fresh_ledger(round_dir):
    ledger = stage._native_ledger(round_dir)
    if ledger['issued'] != 0 or ledger['claims']:
        raise ValueError('Fresh independent stage ledger required; no baseline replay')
    return ledger


def _close(native, folder):
    original = sys.exception()
    try:
        native.close()
    except BaseException as exc:
        marker = folder / 'native-cleanup-unknown.json'
        try:
            stage.new_json(marker, {'schema': 'native-cleanup-unknown/v1', 'build': BUILD,
                'foundation_build': stage.FOUNDATION_BUILD, 'exit_known': False,
                'original_error': None if original is None else repr(original),
                'cleanup_errors': [{'step': 'stage_prepare.close', 'error': repr(exc)}],
                'resume': 'Offline reconciliation required; retain worker lease; never replay'})
        except FileExistsError:
            pass  # Preserve the first unknown cleanup record.
        except Exception as marker_error:
            exc.add_note('Cleanup marker could not be persisted: ' + repr(marker_error))
        if original is not None:
            original.add_note('Native cleanup failed: ' + repr(exc))
            raise original from exc
        raise


def prepare(round_dir, root=ROOT):
    root = Path(root).resolve(strict=True)
    round_dir = stage.round_path(round_dir, root, exists=True)
    protocol = stage.validate_protocol(root, round_dir)
    if protocol['status'] != 'preparing' or protocol['paid_eligible'] is not False:
        raise ValueError('Independent preparing stage protocol required; no frozen/paid preparation')
    _idle(root, round_dir)
    ledger = _fresh_ledger(round_dir)
    evidence = stage._source_snapshot(root, round_dir, protocol)
    source = evidence['source_snapshot']
    base = evidence['initial_settings_snapshot']
    raw = stage._file(source['source'], root)
    dimensions = source['original_dimensions']
    contract = inspect_dng(raw)
    flags = contract.get('clean_develop_flags', {})
    if (contract.get('schema') != lab.RAW_RENDER_CONTRACT
            or not stage._equal(contract.get('expected_developed_dimensions'), dimensions)
            or flags.get('HasSettings') is not False or flags.get('HasCrop') is not False):
        raise ValueError('Actual clean RAW metadata/ordered geometry differs from qualified source')
    author = stage._file(root / 'paired photos' / stage.PHOTO / (stage.PHOTO + '.jpg'), root)
    author_image = lab.srgb(author, True)  # Real ICC conversion, never mere relabelling.
    if min(author_image.size) != 512:
        raise ValueError('Ordinary paired author target must be the 512-short-side copy')
    folder = round_dir / 'inputs' / stage.PHOTO
    if folder.exists() or folder.is_symlink():
        raise FileExistsError('Do not overwrite or replay partial stage input preparation')
    if folder.parent.is_symlink() or folder.parent.is_junction():
        raise ValueError('Stage inputs must be an independent contained directory')
    folder.mkdir(parents=True, exist_ok=False)
    claim = {'id': 'stage_prepare_baseline_' + round_dir.name, 'unit': 'prepare_baseline',
             'format': 'png', 'status': 'pending', 'path': str(folder / 'v0.png'),
             'build': BUILD, 'foundation_build': stage.FOUNDATION_BUILD}
    # The lease covers construction and close as well as rendering. Any raised
    # error leaves it in place, even if persisting a cleanup marker also fails.
    with lab.trajectory_lease(folder):
        _idle_policy = stage.read(root / 'experiments/config/lab-paid-dispatch-policy.local.json')
        if (_idle_policy.get('enabled') is not False
                or (root / 'experiments/dsh-photo-paid.lease').exists()
                or (round_dir / 'stage-dispatch.lease').exists()
                or (round_dir / 'stage-dispatch-pending.json').exists()):
            raise ValueError('Dispatch window changed during stage input admission')
        ledger = _fresh_ledger(round_dir)
        ledger.update(issued=1, claims=[claim])
        try:
            lab.write(round_dir / 'native-render-ledger.json', ledger)
            stage.new_json(folder / 'initial-settings.json', base)
            stage.new_json(folder / 'raw-render-contract.json', contract)
            lab.save_srgb_png(author_image, folder / 'author-srgb.png')
            lab.save_srgb_png(lab.thumb(author_image, 512), folder / 'author-512.png')
            lab.event(folder, 'stage_prepare_start', build=BUILD, foundation_build=stage.FOUNDATION_BUILD,
                      claim_id=claim['id'], source=str(raw), source_qualification=evidence['qualification_record'])
            from photo_image_dependencies import activate_image_dependencies
            activate_image_dependencies()
            native = lab.Native(folder / 'baseline-backend', raw)
            try:
                rows = native.call('query_photos', {}).get('photos', [])
                if (len(rows) != 1 or rows[0].get('kind') != 'raw'
                        or 'previewOnly' not in rows[0] or rows[0]['previewOnly'] is not None
                        or rows[0].get('fileName') != raw.name
                        or not stage._equal([rows[0].get('width'), rows[0].get('height')], dimensions)):
                    raise ValueError('Actual native catalog must identify this complete RAW and ordered frame')
                native.restore(base)
                if not stage._equal(native.settings(), base):
                    raise ValueError('Preparation exact qualified base readback mismatch')
                dims = native.render(folder / 'v0.png', protocol['render_long_edge'])
                facts = inspect_image(folder / 'v0.png', require_srgb=True, require_upright=True,
                                      max_side=protocol['render_long_edge'], expected_format='PNG')
                if not stage._equal(dims, facts['dimensions']):
                    raise ValueError('Actual baseline PNG/readback dimensions differ')
                assert_scaled_geometry(dimensions, dims, protocol['render_long_edge'])
                if not stage._equal(native.settings(), base):
                    raise ValueError('Qualified base changed during baseline rendering')
                lab.save_srgb_png(lab.thumb(lab.srgb(folder / 'v0.png', True), 512), folder / 'v0-512.png')
                stage.new_json(folder / 'backend-source.json', {key: rows[0].get(key) for key in
                    ('fileName', 'width', 'height', 'kind', 'previewOnly')})
            finally:
                _close(native, folder)
            if any(folder.rglob('native-cleanup-unknown.json')):
                raise ValueError('Native cleanup outcome unknown; never replay')
            for name, value in [('anchors.json', []), ('reference-measurements.json', {}), ('v0-measurements.json', {})]:
                stage.new_json(folder / name, value)
            inp = {'photo_id': stage.PHOTO, 'build': BUILD, 'foundation_build': stage.FOUNDATION_BUILD,
                'source': str(raw), 'initial_settings': str(folder / 'initial-settings.json'),
                'v0': str(folder / 'v0.png'), 'v0_dimensions': dims,
                'reference': str(folder / 'author-srgb.png'), 'author_target': str(author),
                'author_source_role': 'author_512_short_side_copy', 'reference_role': 'photographer_reference',
                'author_dimensions': list(author_image.size), 'raw_decode_qualified': True,
                'backend_previewOnly': None, 'raw_render_contract': lab.RAW_RENDER_CONTRACT,
                'raw_render_contract_path': str(folder / 'raw-render-contract.json'),
                'original_dimensions': dimensions, 'catalog_raw_dimensions': [rows[0]['width'], rows[0]['height']],
                'source_role': 'Actual qualified full RAW; v0 and candidates are not author targets',
                'reviewed_roles': False, 'role_review_note': '', 'role_review_image_max_side': 512,
                'source_qualification': evidence['qualification_record'],
                'stage_source_evidence': str(round_dir / protocol['source_evidence'])}
            stage.new_json(folder / 'input.json', inp)
            lab.event(folder, 'stage_prepare_completed', build=BUILD, foundation_build=stage.FOUNDATION_BUILD,
                      claim_id=claim['id'], reviewed_roles=False)
            claim.update(status='completed', dimensions=dims)
            lab.write(round_dir / 'native-render-ledger.json', ledger)
        except BaseException as exc:
            claim.update(status='unknown', error=repr(exc), outcome='unknown_do_not_replay')
            try:
                lab.write(round_dir / 'native-render-ledger.json', ledger)
            except Exception as checkpoint_error:
                exc.add_note('Unknown baseline checkpoint failed; retain pending claim/lease: ' + repr(checkpoint_error))
            raise
    return {'status': 'prepared_pending_direct_actual_role_review', 'build': BUILD, 'native_renders': 1,
            'author': str(folder / 'author-512.png'), 'baseline': str(folder / 'v0-512.png')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', required=True)
    parser.add_argument('--explicit-native-window', action='store_true', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.round), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
