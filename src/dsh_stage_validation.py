"""Stage-only metadata/freeze gates; no native, session, model or paid execution.

prepare records an existing qualified source and copies only its frozen teaching
book. Inputs and their direct-image role review must already exist at freeze.
Resource configuration is read exclusively from the sibling trusted JSON file.
A qualified freeze is evidence, not permission to enable any paid policy.
"""
import argparse
import copy
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_source_evidence as module

ROOT = Path(__file__).resolve().parents[1]
BUILD = 'dsh-stage-runtime-v13-20261113'
FOUNDATION_BUILD = module.FOUNDATION_BUILD
SCHEMA = 'dsh-stage-runtime/v2'
PROTOCOL = SCHEMA
RUNNER = 'dsh-native/v1'
WORKFLOW = module.WORKFLOW
FREEZE_SCHEMA = 'dsh-stage-freeze/v1'
LEDGER_SCHEMA = 'dsh-stage-native-ledger/v1'
PREFLIGHT = 'experiments/validation/latest/dsh-stage-preflight.json'
PHOTO = module.PHOTO
CONDITIONS = ['module_support']
BOOK = module.BOOK
FOUNDATION_FILES = ('lab_timing_experiment.py', 'raw_render_contract.py', 'photo_image_contract.py',
                    'photo_input_contract.py', 'photo_opacity_contract.py', 'photo_resize_contract.py',
                    'photo_source_router.py', 'dsh_photo_color.py')
read = module.read
new_json = module.new_json
round_path = module.round_path
_equal = module._equal
_file = module._file


def load_resources():
    """No caller-supplied root/path or protocol snapshot can select a profile."""
    resources = read(Path(__file__).with_name('photo_stage_resources.json'))
    if (resources.get('schema') != 'photo-stage-resources/v1'
            or resources.get('id') != 'stage-resource/v13' or resources.get('build') != BUILD):
        raise ValueError('Trusted stage resource profile schema/id/build required')
    return resources


def jobs():
    return [job for job in module.jobs() if job['condition'] == 'module_support']


def _foundation_snapshot(root, source_round):
    record = read(source_round / 'foundation/source-records.json')
    rows = record.get('files')
    expected = {'src/' + name for name in FOUNDATION_FILES}
    if (record.get('foundation_build') != FOUNDATION_BUILD or not isinstance(rows, list)
            or len(rows) != 8 or any(not isinstance(row, dict) for row in rows)
            or {row.get('file') for row in rows} != expected):
        raise ValueError('Exactly eight frozen v18 foundation source records required')
    for row in rows:
        stat = _file(row['file'], root).stat()
        if (type(row.get('bytes')) is not int or type(row.get('mtime_ns')) is not int
                or (stat.st_size, stat.st_mtime_ns) != (row['bytes'], row['mtime_ns'])):
            raise ValueError('Frozen v18 foundation source stat changed: ' + row['file'])
    return record


def _source_evidence(root, source_round):
    evidence = module._source_evidence(root, source_round)
    source_round = Path(evidence['input_source_round'])
    return {**evidence, 'schema': 'dsh-stage-source-evidence/v1',
            'source_protocol_snapshot': read(source_round / 'protocol.json'),
            'initial_settings_snapshot': read(_file(evidence['source_snapshot']['initial_settings_source'], root)),
            'foundation_source_records_snapshot': _foundation_snapshot(root, source_round)}


def _protocol(path, source_round, resources, validation_only, created_at):
    transport, model = resources['transport'], resources['model']
    return {'schema': SCHEMA, 'runner': RUNNER, 'workflow': WORKFLOW, 'build': BUILD,
            'foundation_build': FOUNDATION_BUILD, 'task_id': path.name, 'task_root_id': path.name,
            'prior_task_rounds': [], 'status': 'preparing', 'paid_eligible': False,
            'validation_only': validation_only, 'visual_only': True,
            'photo_ids': ['3734'], 'conditions': CONDITIONS.copy(), 'jobs': jobs(), 'development_ids': [],
            'resource_profile_id': resources['id'], 'resources_snapshot': copy.deepcopy(resources),
            'allow_replay': False, 'hash_verification': False,
            'requires_full_raw_decode': True, 'raw_render_contract': 'raw-managed-source/v3',
            'author_reference': '512_short_side', 'model_image_long_edge': transport['overview_long_edge'],
            'detail_image_long_edge': transport['detail_long_edge'], 'render_long_edge': 2561,
            'side_limits': {'overview': transport['overview_long_edge'], 'detail': transport['detail_long_edge']},
            'memory_budget_bytes': 4096,
            'model': {'provider': model['provider'], 'id': model['id'], 'effort': model['effort'],
                      'max_tokens': model['requested_output_tokens']},
            'provider': model['provider'], 'reasoning': model['effort'],
            'max_output_tokens_per_request': model['requested_output_tokens'],
            'input_source_round': str(source_round), 'source_evidence': 'qualification/source-evidence.json',
            'module_support_contract': {'policy_release': module.POLICY_RELEASE, 'rule_release': module.RULE_RELEASE,
                                        'book_path': str(path / 'knowledge/textbook.json'),
                                        'source_book_path': str(path.parents[2] / BOOK)},
            'created_at': created_at}


def prepare(path, input_source_round, *, root=ROOT, validation_only=False):
    """Fresh root stage only; writes metadata/book, never prepares or copies images."""
    root = Path(root).resolve(strict=True)
    path = round_path(path, root)
    if path.exists() or path.is_symlink():
        raise FileExistsError('Do not reuse any existing stage directory')
    if type(validation_only) is not bool:
        raise ValueError('validation_only must be a boolean')
    resources = load_resources()
    evidence = _source_evidence(root, input_source_round)
    protocol = _protocol(path, Path(evidence['input_source_round']), resources, validation_only, module.utc_now())
    source_book = _file(root / BOOK, root)
    module._book(root, {**protocol['module_support_contract'], 'book_path': str(source_book)})
    from build_dsh_module_index import build_index
    index = build_index(source_book)  # JSON-only admission evidence; never a new image review.
    path.mkdir(parents=True, exist_ok=False)
    local_book = path / 'knowledge/textbook.json'
    local_book.parent.mkdir()
    with local_book.open('xb') as stream:
        stream.write(source_book.read_bytes())
    new_json(path / 'protocol.json', protocol)
    new_json(path / 'knowledge/module_policy_index.json', index)
    new_json(path / 'qualification/source-evidence.json', evidence)
    new_json(path / 'native-render-ledger.json', {'schema': LEDGER_SCHEMA, 'build': BUILD, 'issued': 0, 'claims': []})
    return protocol


def validate_protocol(root, round_dir):
    root = Path(root).resolve(strict=True)
    round_dir = round_path(round_dir, root, exists=True)
    protocol = read(round_dir / 'protocol.json')
    if (type(protocol.get('validation_only')) is not bool or type(protocol.get('paid_eligible')) is not bool
            or not isinstance(protocol.get('created_at'), str) or not protocol['created_at'].strip()
            or (protocol.get('status'), protocol['paid_eligible']) not in (('preparing', False), ('frozen', True))):
        raise ValueError('Independent stage protocol and preparing/frozen status required')
    source_round = round_path(protocol['input_source_round'], root, exists=True)
    expected = _protocol(round_dir, source_round, load_resources(), protocol['validation_only'], protocol['created_at'])
    expected.update(status=protocol['status'], paid_eligible=protocol['paid_eligible'])
    # Reject unknown fields too: self-declared limits/legacy lineage cannot add
    # a second authority alongside the trusted stage resource profile.
    if not _equal(protocol, expected):
        raise ValueError('Exact fresh-root stage protocol/jobs/resource profile required; no legacy lineage or self limits')
    module._book(root, protocol['module_support_contract'])
    if _file(protocol['module_support_contract']['book_path'], root) != round_dir / 'knowledge/textbook.json':
        raise ValueError('Stage knowledge book must remain an independent new-round file')
    return protocol


def _native_ledger(round_dir):
    ledger = read(round_dir / 'native-render-ledger.json')
    claims = ledger.get('claims')
    if (ledger.get('schema') != LEDGER_SCHEMA or ledger.get('build') != BUILD
            or type(ledger.get('issued')) is not int or ledger['issued'] < 0
            or not isinstance(claims, list) or ledger['issued'] != len(claims)):
        raise ValueError('Invalid independent stage native ledger accounting')
    seen = set()
    for claim in claims:
        if (not isinstance(claim, dict) or claim.get('status') != 'completed'
                or claim.get('outcome') not in (None, 'completed')
                or not isinstance(claim.get('id'), str) or not claim['id'].strip()):
            raise ValueError('Stage native outcome pending/unknown; never replay')
        if claim['id'] in seen:
            raise ValueError('Duplicate stage native claim ID')
        seen.add(claim['id'])
    return ledger


def stage_native_status(root, round_dir):
    """Read-only actual accounting; no request/render-count hard cap or session."""
    root = Path(root).resolve(strict=True)
    round_dir = round_path(round_dir, root, exists=True)
    protocol = validate_protocol(root, round_dir)
    ledger = _native_ledger(round_dir)
    return {'task_root_id': protocol['task_root_id'], 'issued': ledger['issued'],
            'current_issued': ledger['issued'], 'prior_issued': 0, 'next_allowed': True}


def _source_snapshot(root, round_dir, protocol):
    evidence = read(round_dir / protocol['source_evidence'])
    if not _equal(evidence, _source_evidence(root, protocol['input_source_round'])):
        raise ValueError('Source qualification/foundation changed since stage preparation')
    return evidence


def _preflight(root):
    proof = read(root / PREFLIGHT)
    if (proof.get('build') != BUILD or proof.get('passed') is not True
            or type(proof.get('paid_model_calls')) is not int or proof['paid_model_calls'] != 0):
        raise ValueError('Independent stage offline preflight has not passed')
    return proof


def _inputs(root, round_dir, evidence, *, inspect_pixels):
    # The old helper hard-wires the module ledger/cap. Keep its input checks,
    # substituting only stage-native accounting, without patching shared globals.
    inp = read(round_dir / 'inputs' / PHOTO / 'input.json')
    source, qualification = evidence['source_snapshot'], evidence['qualification_snapshot']
    if (inp.get('photo_id') != PHOTO or inp.get('raw_decode_qualified') is not True
            or inp.get('raw_render_contract') != 'raw-managed-source/v3'
            or 'backend_previewOnly' not in inp or inp['backend_previewOnly'] is not None
            or _file(inp['source'], root) != _file(source['source'], root)
            or not _equal(inp.get('original_dimensions'), source['original_dimensions'])
            or not _equal(read(_file(inp['initial_settings'], root)), qualification['base_settings'])
            or inp.get('author_source_role') != 'author_512_short_side_copy'
            or inp.get('reference_role') != 'photographer_reference'
            or _file(inp['author_target'], root) != _file(root / 'paired photos' / PHOTO / (PHOTO + '.jpg'), root)
            or inp.get('reviewed_roles') is not True
            or not isinstance(inp.get('role_review_note'), str) or not inp['role_review_note'].strip()
            or type(inp.get('role_review_image_max_side')) is not int
            or not 1 <= inp['role_review_image_max_side'] <= 512):
        raise ValueError('Actual qualified source/base, paired512 author and direct-image role review required')
    folder = round_dir / 'inputs' / PHOTO
    for field in ('initial_settings', 'reference', 'v0'):
        _file(inp[field], root).relative_to(folder)
    ledger = _native_ledger(round_dir)
    baselines = [claim for claim in ledger['claims'] if claim.get('unit') == 'prepare_baseline']
    if len(baselines) != 1:
        raise ValueError('Exactly one completed prepare_baseline claim required')
    claim = baselines[0]
    if (_file(claim['path'], root) != _file(inp['v0'], root) or claim.get('format') != 'png'
            or not _equal(claim.get('dimensions'), inp.get('v0_dimensions'))):
        raise ValueError('Baseline claim does not identify this v0 PNG and ordered dimensions')
    for filename, expected in [('anchors.json', []), ('v0-measurements.json', {}), ('reference-measurements.json', {})]:
        if not _equal(read(folder / filename), expected):
            raise ValueError('Visual-only compatibility records must contain no LAB measurements')
    for key in ('author_dimensions', 'v0_dimensions'):
        dims = inp.get(key)
        if not isinstance(dims, list) or len(dims) != 2 or any(type(x) is not int or x < 1 for x in dims):
            raise ValueError('Positive ordered image dimensions required')
    if inspect_pixels:
        from photo_image_contract import inspect_image, read_srgb
        author = read_srgb(inp['author_target'], True)
        reference = read_srgb(inp['reference'], True)
        inspect_image(inp['reference'], require_srgb=True, require_upright=True, expected_format='PNG')
        baseline = inspect_image(inp['v0'], require_srgb=True, require_upright=True, max_side=2561, expected_format='PNG')
        if (min(author.size) != 512 or author.size != reference.size
                or author.tobytes() != reference.tobytes()
                or not _equal(list(reference.size), inp['author_dimensions'])
                or not _equal(baseline['dimensions'], inp['v0_dimensions'])):
            raise ValueError('Actual author ICC conversion/reference pixels or baseline dimensions differ')
    return inp, ledger, claim


def _snapshots(root, round_dir, protocol, *, inspect_pixels):
    evidence = _source_snapshot(root, round_dir, protocol)
    inp, ledger, claim = _inputs(root, round_dir, evidence, inspect_pixels=inspect_pixels)
    snapshots = {'protocol_snapshot': protocol, 'resources_snapshot': load_resources(),
                 'inputs_snapshot': inp, 'initial_settings_snapshot': read(_file(inp['initial_settings'], root)),
                 'source_evidence_snapshot': evidence, 'qualification_snapshot': evidence['qualification_snapshot'],
                 'preflight_snapshot': _preflight(root),
                 'module_book_snapshot': module._book(root, protocol['module_support_contract']),
                 'module_rule_index_snapshot': module._rule_index(round_dir, protocol),
                 'native_prepare_claim': claim}
    from photo_stage_continuation import load_continuation
    continuation = load_continuation(round_dir, root)
    if continuation is not None:
        snapshots['continuation_snapshot'] = continuation
    return snapshots, ledger


def freeze(path, *, root=ROOT):
    """Inspect actual ICC/image roles once; freeze metadata without native work."""
    root = Path(root).resolve(strict=True)
    path = round_path(path, root, exists=True)
    protocol = validate_protocol(root, path)
    if protocol['paid_eligible'] or (path / 'qualification/frozen.json').exists():
        raise ValueError('Stage round already frozen; no replay or edits')
    snapshots, ledger = _snapshots(root, path, protocol, inspect_pixels=True)
    if ledger['issued'] != 1:
        raise ValueError('Freeze requires exactly one baseline render and no experimental actions')
    protocol = {**protocol, 'status': 'frozen', 'paid_eligible': True}
    frozen = {'schema': FREEZE_SCHEMA, 'build': BUILD, 'foundation_build': FOUNDATION_BUILD,
              'runner': RUNNER, 'status': 'qualified', 'frozen_at': module.utc_now(),
              **snapshots, 'protocol_snapshot': protocol, 'hash_verification': False}
    # Proof first: interrupted freezes stay closed, never auto-repaired/replayed.
    new_json(path / 'qualification/frozen.json', frozen)
    temporary = path / 'protocol.freeze.tmp'
    new_json(temporary, protocol)
    temporary.replace(path / 'protocol.json')
    return frozen


def validate_stage_frozen(root, round_dir):
    """Read-only exact snapshot gate. Does not construct or recurse into a session."""
    root = Path(root).resolve(strict=True)
    round_dir = round_path(round_dir, root, exists=True)
    protocol = validate_protocol(root, round_dir)
    frozen = read(round_dir / 'qualification/frozen.json')
    if (protocol['paid_eligible'] is not True or frozen.get('schema') != FREEZE_SCHEMA
            or frozen.get('build') != BUILD or frozen.get('foundation_build') != FOUNDATION_BUILD
            or frozen.get('runner') != RUNNER or frozen.get('status') != 'qualified'
            or frozen.get('hash_verification') is not False):
        raise ValueError('Independent stage round is not qualified and frozen')
    snapshots, ledger = _snapshots(root, round_dir, protocol, inspect_pixels=False)
    if ledger['issued'] < 1:
        raise ValueError('Frozen stage baseline accounting missing')
    for key, value in snapshots.items():
        if not _equal(frozen.get(key), value):
            raise ValueError('Frozen stage evidence changed: ' + key)
    return protocol


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(ROOT))
    sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('prepare', help='Metadata only; no implicit image/native preparation')
    command.add_argument('--round', required=True)
    command.add_argument('--input-source-round', required=True)
    command.add_argument('--validation-only', action='store_true')
    for name in ('freeze', 'validate', 'native-status'):
        command = sub.add_parser(name)
        command.add_argument('--round', required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.round, args.input_source_round, root=args.root, validation_only=args.validation_only)
    elif args.command == 'freeze':
        result = freeze(args.round, root=args.root)
    elif args.command == 'native-status':
        result = stage_native_status(args.root, args.round)
    else:
        result = validate_stage_frozen(args.root, args.round)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
