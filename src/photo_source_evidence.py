"""Source/book qualification utilities; no legacy protocol, budget or CLI.

Copied from src/dsh_module_validation.py before archival. Reads retain exact
source/book/record/geometry binding; no rendering, transport or hashes occur.
"""
import datetime
import json
import re
from pathlib import Path

FOUNDATION_BUILD = 'dsh-native-memory-v18-20261111'
WORKFLOW = 'compact-step/v1'
PHOTO = 'DSC_3734'
CONDITIONS = ['baseline', 'module_support']
POLICY_RELEASE = 'dsh-module-policy/v1'
RULE_RELEASE = 'module-exact-index/v1'
BOOK = ('experiments/runs/backend-textbook-20261112-r2/textbook-snapshots/'
        'release-teaching-v1/textbook.json')
ROOT = Path(__file__).resolve().parents[1]

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def new_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _equal(left, right):
    # JSON equality must not treat true as the integer 1 in safety limits/jobs.
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def _file(value, base):
    path = Path(value)
    if not path.is_absolute():
        path = Path(base) / path
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError('Expected an existing file: ' + str(path))
    return path


def round_path(value, root=ROOT, *, exists=False):
    root = Path(root).resolve(strict=True)
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    path = path.resolve(strict=exists)
    relative = path.relative_to(root / 'experiments/runs')
    if (len(relative.parts) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', path.name)
            or path.name in ('constructor', 'prototype', '__proto__')):
        raise ValueError('Module round must be a direct, safely named child of experiments/runs')
    return path


def jobs():
    return [{'photo_id': PHOTO, 'repeat': 1, 'condition': condition, 'memory_condition': 'B',
             'features': {'module_support': condition == 'module_support',
                          'public_module_contract': True, 'strict_native_execution': True,
                          'effect_comparison': True}} for condition in CONDITIONS]


def _book(root, contract):
    if (not isinstance(contract, dict) or set(contract) != {'policy_release', 'rule_release', 'book_path', 'source_book_path'}
            or contract['policy_release'] != POLICY_RELEASE or contract['rule_release'] != RULE_RELEASE
            or not isinstance(contract['book_path'], str) or not Path(contract['book_path']).is_absolute()):
        raise ValueError('Explicit current module policy/rule release and absolute frozen book path required')
    path = _file(contract['book_path'], root)
    source_path = _file(contract['source_book_path'], root)
    if not Path(contract['source_book_path']).is_absolute() or path.read_bytes() != source_path.read_bytes():
        raise ValueError('Independent knowledge book must be an unchanged byte copy of the frozen source book')
    book = read(path)
    manifest = book.get('manifest', {})
    if (book.get('schema') != 'photo-textbook/v1' or manifest.get('book_id') != 'teaching-v1-v18'
            or manifest.get('foundation_build') != FOUNDATION_BUILD
            or manifest.get('final_for_this_teaching_release') is not True
            or not isinstance(book.get('cards'), list) or not book['cards']):
        raise ValueError('The independently frozen v18 teaching book is required')
    return book


def _rule_index(round_dir, protocol):
    sidecar = Path(protocol['module_support_contract']['book_path']).with_name('module_policy_index.json')
    sidecar.resolve(strict=True).relative_to(round_dir / 'knowledge')
    index = read(sidecar)
    if (index.get('schema') != 'photo-module-policy-index/v1' or index.get('frozen') is not True
            or index.get('release') != protocol['module_support_contract']['rule_release']
            or index.get('book_id') != 'teaching-v1-v18' or not isinstance(index.get('entries'), list)):
        raise ValueError('Independent frozen exact-rule index and book/release binding required')
    return index


def _source_evidence(root, source_round, source_file=None, qualification_file=None):
    source_round = round_path(source_round, root, exists=True)
    source_file = _file(source_file or source_round / 'inputs' / PHOTO / 'input-source.json', root)
    qualification_file = _file(qualification_file or source_round / 'inputs' / PHOTO / 'baseline-qualified.json', root)
    for path in (source_file, qualification_file):
        path.relative_to(source_round)
    source = read(source_file)
    qualification = read(qualification_file)
    foundation_file = source_round / 'foundation/manifest.json'
    foundation = read(foundation_file)
    source_protocol = read(source_round / 'protocol.json')
    raw = source.get('raw_contract', {})
    flags = raw.get('clean_develop_flags', {})
    dimensions = source.get('original_dimensions')
    if (source_protocol.get('schema') != 'backend-capability/v1'
            or source_protocol.get('foundation_build') != FOUNDATION_BUILD
            or foundation.get('build') != FOUNDATION_BUILD
            or source.get('photo') != PHOTO or source.get('source_id') != PHOTO
            or raw.get('schema') != 'raw-managed-source/v3'
            or flags.get('HasSettings') is not False or flags.get('HasCrop') is not False
            or not isinstance(dimensions, list) or len(dimensions) != 2
            or any(type(x) is not int or x < 1 for x in dimensions)
            or not _equal(raw.get('expected_developed_dimensions'), dimensions)
            or qualification.get('foundation') != FOUNDATION_BUILD
            or not _equal(qualification.get('dimensions'), dimensions)
            or not isinstance(qualification.get('base_settings'), dict) or not qualification['base_settings']
            or type(qualification.get('paid_model_calls')) is not int or qualification['paid_model_calls'] != 0):
        raise ValueError('Existing clean source, original dimensions and actual source qualification required')
    actual_source = _file(source['source'], root)
    if actual_source != _file(qualification['actual_raw_source'], root) or actual_source.suffix.lower() != '.dng':
        raise ValueError('Qualification is not for this exact existing DNG source')
    if not _equal(read(_file(source['initial_settings_source'], root)), qualification['base_settings']):
        raise ValueError('Existing clean source settings differ from qualified base readback')
    _file(qualification['v0'], root)
    _file(qualification['preview'], root)
    return {'schema': 'dsh-module-source-evidence/v1', 'input_source_round': str(source_round),
            'source_record': str(source_file), 'qualification_record': str(qualification_file),
            'foundation_record': str(foundation_file), 'source_snapshot': source,
            'qualification_snapshot': qualification, 'foundation_snapshot': foundation,
            'hash_verification': False}
