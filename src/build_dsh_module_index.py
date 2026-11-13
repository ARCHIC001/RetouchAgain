"""Read-only, evidence-bound builder for the frozen teaching-v1-v18 sidecar.

build_index(book_path) -> dict performs JSON reads only. References resolve from
card.round_dir, NOT from the book's location: byte-for-byte copies in a new run
keep their original evidence bindings. No lesson/title matching, worker, native
backend, image renderer, network, model, or digest verification is involved.

CLI: python -I -B src/build_dsh_module_index.py --book PATH --output NEW.json
Only that explicitly named file is created (exclusive create; parent must exist).
Invalid/unmapped cards fail the build with card-specific reasons, never silently
vanish. This indexes recorded independent admission, not a new visual review.
"""
import argparse
import copy
import importlib.util
import json
from pathlib import Path


def _sibling(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    # get_source avoids even an implicit __pycache__ write in a normal CLI run.
    exec(compile(spec.loader.get_source(name), spec.origin, 'exec'), module.__dict__)
    return module


cap = _sibling('photo_capability_catalog')
SCHEMA = 'photo-module-policy-index/v1'
RELEASE = 'module-exact-index/v1'
BOOK_ID = 'teaching-v1-v18'
_SPLITS = ('split_shadows', 'split_mid', 'split_highlights')
_PARAMS = ('highlights', 'lights', 'darks', 'shadows')
_CURVE_PATHS = {
    'curve.splitShadows': 'curve.split_shadows',
    'curve.splitMid': 'curve.split_mid',
    'curve.splitHighlights': 'curve.split_highlights',
    'curve.refineSaturation': 'curve.refine_saturation',
}


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'),
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON: ' + value)))


def _need(test, message):
    if not test:
        raise ValueError(message)


def _path(root, value):
    _need(isinstance(value, str) and bool(value), 'Missing explicit archive path')
    # Original archives use Windows separators, even when inspected on POSIX.
    path = Path(value.replace('\\', '/'))
    path = (path if path.is_absolute() else root / path).resolve()
    _need(path.is_relative_to(root), 'Evidence path escapes original card.round_dir')
    return path


def _get(settings, path):
    value = settings
    for part in path.strip('/').replace('/', '.').split('.'):
        if isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise ValueError('Prerequisite/readback gap: ' + path)
    return copy.deepcopy(value)


def _signature(operation):
    _need(isinstance(operation, dict), 'Missing actual operation')
    # settings is a full after snapshot, not another set of edited control IDs.
    return {key: value for key, value in operation.items() if key != 'settings'}


def _requirements(target, before, activations, command=None):
    """Small native readback path/value map, never a complete scene-base lock."""
    paths = set()
    own_path = _CURVE_PATHS.get(target, target)
    for change in activations:
        _need(isinstance(change, dict) and isinstance(change.get('path'), str) and 'after' in change,
              'Malformed recorded activation')
        path = change['path']
        _need(_get(before, path) == change['after'], 'Activation disagrees with before readback: ' + path)
        # The edited value is not a prerequisite; do not require refine=50 after
        # the caller has already applied refine=80. Its companion activation is.
        if path != own_path:
            paths.add(path)
    if target in ('wb.temp', 'wb.tint'):
        paths.update(('wb.mode', 'wb.scale'))
    if target == 'profile.amount':
        paths.add('profile.id')
    if target in ('curve.highlights', 'curve.lights', 'curve.darks', 'curve.shadows'):
        paths.update('curve.' + key for key in _SPLITS)
    if target in _CURVE_PATHS:
        paths.update('curve.' + key for key in (*_PARAMS, *_SPLITS) if 'curve.' + key != own_path)
    if target.startswith('grading.'):
        # All four bands were activated together in the actual admitted trials.
        # Preserve companion hues/saturations and blend/balance, not their zero
        # luminance baselines or any unrelated WB/light/detail state.
        paths.update('grading.' + band + '.' + key
                     for band in ('shadows', 'midtones', 'highlights', 'global')
                     for key in ('hue', 'sat') if 'grading.' + band + '.' + key != target)
        paths.update(path for path in ('grading.blending', 'grading.balance') if path != target)
    if command:
        ident, params = command['id'], command.get('params', {})
        if ident == 'curve.reset':
            channel = params.get('channel', 'all')
            keys = {'rgb': ('master',), 'master': ('master',), 'red': ('red',),
                    'green': ('green',), 'blue': ('blue',),
                    'point': ('master', 'red', 'green', 'blue'),
                    'parametric': (*_PARAMS, *_SPLITS),
                    'all': ('master', 'red', 'green', 'blue', *_PARAMS, *_SPLITS)}
            _need(channel in keys, 'Unsupported observed reset channel: ' + str(channel))
            paths.update('curve.' + key for key in keys[channel])
        elif ident == 'pointColor.delete':
            index = params.get('index')
            _need(type(index) is int and index >= 0, 'Missing observed point-color index')
            # The actual sample is a small object, not the scene develop state.
            paths.add('/point_colors/' + str(index))
        elif ident in ('mask.addComponent', 'mask.brushStroke', 'mask.update'):
            ident = params.get('id')
            masks = before.get('masks', [])
            found = [i for i, mask in enumerate(masks) if mask.get('id') == ident]
            _need(len(found) == 1, 'Missing unique observed mask ID in before readback')
            prefix = '/masks/' + str(found[0])
            # Small structural/active-adjustment fixture only; never bind the
            # full masks array, its generated name, or unrelated zero sliders.
            paths.update(prefix + suffix for suffix in ('/id', '/visible', '/invert', '/components', '/adjust/amount'))
            for key, value in masks[found[0]].get('adjust', {}).items():
                if key != 'amount' and value != 0:
                    paths.add(prefix + '/adjust/' + key)
    return {path: _get(before, path) for path in sorted(paths)}


def _entry(card, row):
    uid, target = card['unit_id'], row['function_id']
    _need(card.get('status') == 'admitted' and not card.get('pending'), 'Card is not independently admitted/settled')
    scope, approved = card.get('scope'), card.get('approvedlesson', {})
    _need(isinstance(scope, dict) and scope and approved.get('scope') == scope, 'Missing or inconsistent frozen card scope')
    provenance = card.get('provenance', {})
    _need(not provenance.get('blocked_reasons') and provenance.get('split') in ('teaching', 'development', 'dev', 'train', 'pilot', 'main'),
          'Card provenance is blocked or not development/teaching')
    _need(row.get('independent_card_reviewed') is True and row.get('coverage_level') == 'independently_scoped_card',
          'Archive row does not record independent scoped-card review')
    _need(row.get('module') == card.get('module') == cap.module_for(target), 'Exact target/module binding mismatch')
    _need(row.get('scene_id') == card.get('scene_id') == scope.get('scene_id'), 'Scene/scope binding mismatch')
    root = Path(card.get('round_dir', '').replace('\\', '/'))
    _need(root.is_absolute(), 'card.round_dir must explicitly name the original absolute archive')
    root = root.resolve()
    unit_path = _path(root, row.get('unit_path'))
    backend_path = _path(root, row.get('backend_fact_path'))
    _need(_path(root, approved.get('backend_readback_path')) == backend_path, 'Approved backend path differs from archive index')
    unit, backend = _load(unit_path), _load(backend_path)
    _need(unit.get('id') == backend.get('unit_id') == uid, 'Original unit/backend unit ID mismatch')
    _need(unit.get('module') == card['module'] and unit.get('scene_id') == card['scene_id'], 'Original unit/module/scene mismatch')
    _need(unit.get('control_id', unit.get('command_id')) == target == backend.get('control_id', backend.get('command_id')),
          'Original unit/backend exact function ID mismatch')
    _need(backend.get('record_id') == row.get('backend_fact_id') and backend.get('status') == 'complete', 'Original backend ID/status mismatch')
    facts = card.get('facts', [])
    _need(len(facts) == 1, 'Expected one explicitly bound actual operation; unsupported card fact count')
    fact = facts[0]
    _need(fact.get('backend_fact_id') == backend['record_id'], 'Card fact/backend record ID mismatch')
    operation = fact.get('operation')
    signature = _signature(operation)
    review_path = _path(root, approved.get('review_path'))
    reviews = _load(review_path)
    records = reviews.get('assessments', [reviews]) if isinstance(reviews, dict) else reviews
    selected = [r for r in records if r.get('record_id') == approved.get('review_record_id')]
    _need(len(selected) == 1, 'Missing unique independent review record')
    review = selected[0]
    _need(review.get('candidate_id') == card['id'] and review.get('decision') == 'approve'
          and review.get('actual_image_review') is True, 'Review does not independently approve this exact card')
    reviewer = review.get('reviewer', {})
    _need(reviewer.get('kind') in ('human', 'independent_model') and reviewer == approved.get('reviewer'), 'Independent reviewer binding mismatch')
    if reviewer.get('kind') == 'independent_model':
        _need(reviewer.get('model_id') and reviewer['model_id'] != card.get('model_id'), 'Tested-model self-review is not independent')
    _need(_path(root, review.get('backend_readback', {}).get('path')) == backend_path, 'Review backend path mismatch')
    condition_id, direction, command, before_ref = None, None, None, None
    if target in cap.COMMAND_IDS:
        commands = signature.get('commands')
        _need(set(signature) == {'commands'} and isinstance(commands, list) and len(commands) == 1,
              'Command card must have one actual command envelope')
        command = commands[0]
        _need(command.get('id') == target and command == backend.get('actual_command'), 'Card command differs from actual backend command')
        before_ref = _path(root, backend.get('baseline_settings'))
        before = _load(before_ref)
        _need(isinstance(before, dict), 'Missing original command before settings')
        activations = []
        exact = target
        if target in ('mask.add', 'mask.addComponent', 'mask.update'):
            params = command.get('params', {})
            shape = params.get('shape', {}).get('kind') if target == 'mask.update' else params.get('kind')
            _need(shape in cap.SHAPE_KINDS, 'Missing explicit observed command shape (no native-default guess)')
            exact = target + ':' + shape
        _need(fact.get('settings_readback') == backend.get('settings_readback'), 'Card command after-readback mismatch')
    else:
        _need(set(signature) == {'values'} and set(signature['values']) == {target}, 'Control card must name exactly the actual known control')
        _need(signature == _signature(unit.get('frozen_operation')), 'Card operation differs from original frozen unit operation')
        condition_id, direction = unit.get('formal_condition'), unit.get('formal_direction')
        _need(review.get('backend_readback', {}).get('condition_id') == condition_id
              and review.get('backend_readback', {}).get('direction') == direction,
              'Independent review names a different original condition/direction')
        conditions = [c for c in backend.get('conditions', []) if c.get('condition_id') == condition_id]
        _need(len(conditions) == 1, 'Missing unique original formal condition')
        condition = conditions[0]
        stimulus = condition.get('stimuli', {}).get(direction, {})
        _need(stimulus.get('status') == 'complete' and stimulus.get('operation') == operation, 'Actual formal stimulus/operation mismatch')
        _need(stimulus.get('actual_readback') == fact.get('settings_readback'), 'Card control after-readback mismatch')
        baseline = condition.get('baseline_ref', {})
        _need(baseline.get('status') == 'complete', 'Original baseline readback is incomplete')
        before = baseline.get('actual_readback')
        _need(isinstance(before, dict) and before == condition.get('complete_base_settings'), 'Before readback missing or disagrees with condition settings')
        activations = condition.get('active_prerequisites', [])
        exact = target
    required = _requirements(target, before, activations, command)
    raw_source = unit.get('raw_source')
    _need(isinstance(raw_source, str) and raw_source and isinstance(unit.get('raw_basis_settings'), dict), 'RAW basis provenance gap')
    if 'backend_raw' in backend:
        _need(backend['backend_raw'].get('kind') == 'raw', 'Original recorded source is not RAW')
    refs = [{'unit_id': uid, 'function_id': target, 'unit_path': str(unit_path),
             'backend_fact_path': str(backend_path), 'backend_fact_id': backend['record_id'],
             'condition_id': condition_id, 'direction': direction},
            {'review_path': str(review_path), 'review_record_id': approved['review_record_id']},
            {'raw_source': raw_source, 'basis_ref': str(unit_path) + '#/raw_basis_settings',
             'before_settings_ref': str(before_ref) if before_ref else str(backend_path) + '#conditions/' + condition_id + '/baseline_ref/actual_readback'}]
    limits = ['Recorded independent single-scene review only; no task-transfer or author-acceptance qualification.',
              'RAW basis is recorded in source_refs; unrelated whole-scene base settings are not prerequisites.',
              'Exact operation and before/after evidence remain in the original archive; retrieval is not dispatch authorization.']
    if exact != target:
        limits.append('Shape-specific evidence only; bare command is intentionally not an OR selector in exact_ids.')
    if command:
        limits.append('Observed recipe only; existing object IDs/indices and current dependencies must be checked again before execution.')
    return {'id': card['id'], 'module': card['module'], 'kind': 'visual_evidence',
            'exact_ids': [exact], 'verification_level': 'independent_visual_checked',
            'applicable_scope': copy.deepcopy(scope), 'known_limits': limits,
            'source_refs': refs, 'card_id': card['id'], 'required_settings': required}


def build_index(book_path) -> dict:
    """Read an explicit frozen book and original JSON refs; return a new sidecar.

    Fail closed, with every per-card exclusion reason, rather than publish an
    incomplete index under the frozen release name. No writes or execution.
    """
    path = Path(book_path)
    path = path / 'textbook.json' if path.is_dir() else path
    book = _load(path)
    manifest = book.get('manifest', {})
    _need(book.get('schema') == 'photo-textbook/v1' and manifest.get('book_id') == BOOK_ID,
          'Expected explicit photo-textbook/v1 teaching-v1-v18 book')
    _need(manifest.get('final_for_this_teaching_release') is True and manifest.get('release_status') == 'frozen_teaching_v1',
          'Teaching release is not explicitly frozen/final')
    cards, archive = book.get('cards'), manifest.get('teaching_function_archive_index')
    _need(isinstance(cards, list) and cards and manifest.get('card_count') == len(cards), 'Frozen card count mismatch')
    _need(len({c['id'] for c in cards}) == len(cards), 'Duplicate frozen card ID')
    _need(isinstance(archive, list), 'Missing teaching_function_archive_index')
    entries, exclusions = [], []
    for card in cards:
        try:
            rows = [r for r in archive if r.get('unit_id') == card.get('unit_id')]
            _need(len(rows) == 1, 'Missing unique exact unit ID in teaching_function_archive_index')
            entries.append(_entry(card, rows[0]))
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
            exclusions.append(str(card.get('id')) + ': ' + str(error))
    _need(not exclusions, 'Index not published; excluded cards:\n' + '\n'.join(exclusions))
    result = {'schema': SCHEMA, 'frozen': True, 'release': RELEASE, 'book_id': BOOK_ID,
              'entries': sorted(entries, key=lambda row: row['id'])}
    # Ensure the public return is ordinary finite JSON, not custom path objects.
    json.dumps(result, ensure_ascii=False, allow_nan=False)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='Explicit NEW sidecar JSON path; never overwritten')
    args = parser.parse_args(argv)
    try:
        _need(not args.output.exists() and not args.output.is_symlink(), 'Output already exists; refusing overwrite')
        result = build_index(args.book)
        text = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
        # No mkdir, default output, book copy, automatic run, or secondary report.
        with args.output.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps({'entries': len(result['entries']),
                      'module_counts': {m: sum(e['module'] == m for e in result['entries']) for m in cap.MODULES},
                      'prerequisite_gaps': [], 'exclusions': []}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    main()
