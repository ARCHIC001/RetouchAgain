"""TEST ONLY: fresh strict synthetic freeze and real StageSession; fake native IO.

JSON-line helper for check_dsh_stage_real_loop.mjs. No legacy test fixtures,
old runs, real RAW decoder, renderer, provider, credentials or hash verification.
"""
import argparse
import copy
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.dont_write_bytecode = True
SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
from PIL import Image, ImageCms, ImageDraw, ImageEnhance
import lab_timing_experiment as lab
import dsh_stage_worker as stage
import dsh_stage_validation as validation
import photo_capability_catalog as cap
from photo_image_contract import read_srgb
import photo_stage_operations as operations


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')


def synthetic_debt_updates(context):
    """Explicit per-focus synthetic target review; old debts retain old status input."""
    phase = context['workflow']['phase']
    if phase not in ('G2', 'G3', 'G4'):
        return []
    updates = []
    for debt in context['workflow']['review_debts']:
        if debt['status'] != 'pending' or debt['due_phase'] != phase:
            continue
        update = {'id': debt['id'],
                  'reason': 'Explicit current synthetic target review; no photographic quality claim.'}
        if debt.get('schema') == 'photo-review-debt/v2':
            update.update(resolution='no_regression', checks=[{
                'focus_id': focus['id'], 'outcome': 'no_regression',
                'evidence': 'Explicit current synthetic target review of ' + focus['region'],
                'review_region': focus['region']} for focus in debt['focus']])
        else:
            update['status'] = 'resolved'
        updates.append(update)
    return updates


def p3_profile():
    profile = bytearray(lab.SRGB_PROFILE)
    count = struct.unpack_from('>I', profile, 128)[0]
    tags = {bytes(profile[132+i*12:136+i*12]): struct.unpack_from('>II', profile, 136+i*12)
            for i in range(count)}
    for tag, values in {b'rXYZ': (.51514644, .24120034, -.00105016),
                        b'gXYZ': (.29198314, .69222254, .04187860),
                        b'bXYZ': (.15710418, .06657712, .78407160)}.items():
        for i, value in enumerate(values):
            struct.pack_into('>i', profile, tags[tag][0]+8+i*4, round(value*65536))
    return bytes(profile)


def synthetic_image(size):
    image = Image.new('RGB', size, (60, 90, 130))
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 40, size[0]//2, size[1]-10), fill=(170, 65, 35))
    draw.text((8, 8), 'SYNTHETIC TEST ONLY', fill=(255, 255, 255))
    return image


def synthetic_book(root):
    """One independently bound synthetic card, not an imported legacy fixture."""
    archive = root / 'SYNTHETIC-archive'
    unit_path, backend_path, review_path = [archive / name for name in ('unit.json', 'backend.json', 'review.json')]
    operation = {'values': {'light.exposure': .5}}
    before = {'light': {'exposure': 0}, 'masks': [], 'point_colors': []}
    after = {'light': {'exposure': .5}, 'masks': [], 'point_colors': []}
    scope = {'kind': 'single_scene', 'scene_id': 'synthetic-scene', 'generalization': 'not_established'}
    reviewer = {'kind': 'independent_model', 'model_id': 'synthetic-reviewer', 'id': 'synthetic-reviewer'}
    write(unit_path, {'id': 'synthetic-unit', 'control_id': 'light.exposure', 'module': 'light',
        'scene_id': 'synthetic-scene', 'raw_source': str(archive/'not-photo.dng'),
        'raw_basis_settings': before, 'formal_condition': 'activated', 'formal_direction': 'positive', 'frozen_operation': operation})
    write(backend_path, {'record_id': 'synthetic-fact', 'unit_id': 'synthetic-unit', 'control_id': 'light.exposure',
        'status': 'complete', 'backend_raw': {'kind': 'raw'}, 'conditions': [{'condition_id': 'activated',
        'complete_base_settings': before, 'baseline_ref': {'status': 'complete', 'actual_readback': before},
        'active_prerequisites': [], 'stimuli': {'positive': {'status': 'complete', 'operation': operation, 'actual_readback': after}}}]})
    write(review_path, {'assessments': [{'record_id': 'synthetic-review', 'candidate_id': 'synthetic-card',
        'decision': 'approve', 'actual_image_review': True, 'reviewer': reviewer,
        'backend_readback': {'path': str(backend_path), 'condition_id': 'activated', 'direction': 'positive'}}]})
    book = {'schema': 'photo-textbook/v1', 'manifest': {'book_id': 'teaching-v1-v18',
        'foundation_build': validation.FOUNDATION_BUILD, 'release_status': 'frozen_teaching_v1',
        'final_for_this_teaching_release': True, 'card_count': 1, 'teaching_function_archive_index': [{
        'unit_id': 'synthetic-unit', 'function_id': 'light.exposure', 'module': 'light', 'scene_id': 'synthetic-scene',
        'unit_path': 'unit.json', 'backend_fact_path': 'backend.json', 'backend_fact_id': 'synthetic-fact',
        'independent_card_reviewed': True, 'coverage_level': 'independently_scoped_card'}]}, 'cards': [{
        'id': 'synthetic-card', 'unit_id': 'synthetic-unit', 'module': 'light', 'scene_id': 'synthetic-scene',
        'model_id': 'synthetic-tested-model', 'status': 'admitted', 'pending': [], 'round_dir': str(archive), 'scope': scope,
        'provenance': {'split': 'teaching', 'blocked_reasons': []}, 'approvedlesson': {
        'text': 'SYNTHETIC contract fixture, not photographic evidence.', 'scope': scope,
        'review_record_id': 'synthetic-review', 'reviewer': reviewer, 'review_path': str(review_path),
        'backend_readback_path': str(backend_path)}, 'facts': [{
        'backend_fact_id': 'synthetic-fact', 'operation': operation, 'settings_readback': after}]}]}
    write(root / validation.BOOK, book)


class StrictSyntheticFixture:
    def __init__(self, root):
        self.root = root
        self.round = root / 'experiments/runs/stage-testing'
        self.source_round = root / 'experiments/runs/source-testing'
        folder = self.source_round / 'inputs' / validation.PHOTO
        folder.mkdir(parents=True)
        self.raw = folder / 'SYNTHETIC-not-decodable.dng'
        self.raw.write_bytes(b'SYNTHETIC TEST PLACEHOLDER; NEVER DECODE OR RENDER')
        self.base = {'light': {key: 0 for key in operations.TONE_FIELDS}, 'color': {'saturation': 0},
                     'mixer': {band: {'hue': 0, 'sat': 0, 'lum': 0} for band in operations.BANDS},
                     'wb': {'mode': 'custom', 'temp': 5083, 'tint': 0, 'scale': 'kelvin'}, 'masks': [], 'point_colors': []}
        self.dimensions = [170, 256]
        write(folder / 'initial-settings.json', self.base)
        baseline = folder / 'SYNTHETIC-v0.png'
        lab.save_srgb_png(synthetic_image(tuple(self.dimensions)), baseline)
        source = {'photo': validation.PHOTO, 'source_id': validation.PHOTO, 'source': str(self.raw),
            'initial_settings_source': str(folder/'initial-settings.json'), 'original_dimensions': self.dimensions,
            'raw_contract': {'schema': 'raw-managed-source/v3', 'expected_developed_dimensions': self.dimensions,
                             'clean_develop_flags': {'HasSettings': False, 'HasCrop': False}}}
        qualification = {'foundation': validation.FOUNDATION_BUILD, 'actual_raw_source': str(self.raw),
            'base_settings': self.base, 'dimensions': self.dimensions, 'v0': str(baseline), 'preview': str(baseline), 'paid_model_calls': 0}
        write(folder/'input-source.json', source)
        write(folder/'baseline-qualified.json', qualification)
        write(self.source_round/'protocol.json', {'schema': 'backend-capability/v1', 'foundation_build': validation.FOUNDATION_BUILD})
        write(self.source_round/'foundation/manifest.json', {'build': validation.FOUNDATION_BUILD})
        rows = []
        for name in validation.FOUNDATION_FILES:
            file = root / 'src' / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'SYNTHETIC frozen source-stat fixture, never executable')
            rows.append({'file': 'src/'+name, 'bytes': file.stat().st_size, 'mtime_ns': file.stat().st_mtime_ns})
        write(self.source_round/'foundation/source-records.json', {'foundation_build': validation.FOUNDATION_BUILD, 'files': rows, 'hash_verification': False})
        synthetic_book(root)
        author = root / 'paired photos/DSC_3734/DSC_3734.jpg'
        author.parent.mkdir(parents=True)
        synthetic_image((512, 768)).save(author, icc_profile=p3_profile())
        validation.prepare(self.round, self.source_round, root=root, validation_only=True)
        inputs = self.round / 'inputs' / validation.PHOTO
        write(inputs/'initial-settings.json', self.base)
        lab.save_srgb_png(read_srgb(author, True), inputs/'author-srgb.png')
        lab.save_srgb_png(synthetic_image(tuple(self.dimensions)), inputs/'v0.png')
        write(inputs/'input.json', {'photo_id': validation.PHOTO, 'source': str(self.raw), 'raw_decode_qualified': True,
            'raw_render_contract': 'raw-managed-source/v3', 'backend_previewOnly': None, 'original_dimensions': self.dimensions,
            'initial_settings': str(inputs/'initial-settings.json'), 'author_target': str(author),
            'author_source_role': 'author_512_short_side_copy', 'reference_role': 'photographer_reference',
            'reference': str(inputs/'author-srgb.png'), 'author_dimensions': [512, 768],
            'v0': str(inputs/'v0.png'), 'v0_dimensions': self.dimensions, 'reviewed_roles': True,
            'role_review_note': 'SYNTHETIC testing roles only, not photographic evidence', 'role_review_image_max_side': 512})
        for name, value in [('anchors.json', []), ('v0-measurements.json', {}), ('reference-measurements.json', {})]:
            write(inputs/name, value)
        claim = {'id': 'synthetic-baseline', 'unit': 'prepare_baseline', 'format': 'png', 'status': 'completed',
                 'path': str(inputs/'v0.png'), 'dimensions': self.dimensions}
        write(self.round/'native-render-ledger.json', {'schema': validation.LEDGER_SCHEMA, 'build': stage.BUILD, 'issued': 1, 'claims': [claim]})
        write(root/validation.PREFLIGHT, {'schema': 'dsh-stage-preflight/v1', 'build': stage.BUILD, 'passed': True,
                                        'paid_model_calls': 0, 'scope': 'SYNTHETIC isolated-root testing only'})
        validation.freeze(self.round, root=root)
        self.protected = {file: (file.read_bytes(), file.stat().st_mtime_ns)
                          for parent in (self.source_round, root/'paired photos', inputs)
                          for file in parent.rglob('*') if file.is_file()}


class SyntheticNative:
    """Small in-memory native boundary; production typed execution stays real."""
    def __init__(self, backend):
        self.backend = backend
        self.counter, self.active = 3017, None

    def settings(self):
        return self.backend.settings()

    def controls(self):
        return self.backend.controls()

    def restore(self, settings):
        self.backend.restore(settings)
        self.active = None

    def apply(self, base, args):
        backend = self.backend
        backend.settings_value = copy.deepcopy(base)
        for ident, value in args.get('values', {}).items():
            *parents, key = ident.split('.')
            target = backend.settings_value
            for parent in parents:
                target = target[parent]
            target[key] = value
        for group, values in args.get('settings', {}).items():
            backend.settings_value[group].update(copy.deepcopy(values))
        if any(ident.startswith('wb.') for ident in args.get('values', {})) or 'wb' in args.get('settings', {}):
            backend.settings_value['wb']['mode'] = 'custom'
        return self.settings()

    def call(self, tool, command):
        if tool != 'command_run':
            raise AssertionError('Unsupported synthetic native tool: ' + tool)
        self.backend.calls.append((tool, copy.deepcopy(command)))
        ident, params = command['id'], command.get('params', {})
        masks = self.backend.settings_value['masks']
        if ident == 'library.state':
            result = {'activeMask': self.active}
        elif ident == 'mask.add':
            self.counter = max([self.counter, *[mask['id'] for mask in masks]]) + 29
            plan = {'kind': params['kind'], 'geometry': {k: v for k, v in params.items() if k != 'kind'}}
            masks.append({'id': self.counter, 'name': 'SYNTHETIC mask', 'visible': True, 'invert': False,
                'components': [{'op': 'add', 'invert': False, 'shape': operations._created_shape(plan)}],
                'adjust': {**{key: 33 for key in operations.NEUTRAL_LOCAL}, 'amount': 71}})
            self.active = self.counter
            result = {'activeMask': self.active}
        elif ident == 'mask.adjust':
            next(mask for mask in masks if mask['id'] == params['id'])['adjust'].update(params['values'])
            result = {'activeMask': self.active}
        elif ident == 'develop.wbPick':
            result = {'temp': 5732, 'tint': 7}
            self.backend.settings_value['wb'].update(result, mode='custom')
        else:
            raise AssertionError('Unimplemented synthetic native command: ' + ident)
        return {'structuredContent': copy.deepcopy(result)}


class SyntheticBackend:
    """Only the native boundary is fake; outputs use actual stored synthetic pixels."""
    def __init__(self, session):
        self.session, self.calls, self.trace = session, [], []
        self.settings_value = copy.deepcopy(session.state['versions']['v0']['settings'])
        self.dependencies = {'commands': [{'id': ident, 'enabled': True} for ident in cap.COMMAND_IDS]}
        self.native = SyntheticNative(self)

    def controls(self):
        return {'controls': [{'id': ident, 'min': low, 'max': high, 'value': self.settings_value[group][key]}
            for ident, group, key, low, high in [('light.exposure', 'light', 'exposure', -5, 5),
                                                ('light.contrast', 'light', 'contrast', -100, 100),
                                                ('light.highlights', 'light', 'highlights', -100, 100),
                                                ('light.blacks', 'light', 'blacks', -100, 100),
                                                ('light.shadows', 'light', 'shadows', -100, 100),
                                                ('light.whites', 'light', 'whites', -100, 100),
                                                ('wb.temp', 'wb', 'temp', 2000, 50000),
                                                ('wb.tint', 'wb', 'tint', -150, 150),
                                                ('color.saturation', 'color', 'saturation', -100, 100)]
            if key in self.settings_value.get(group, {})] +
            [{'id': 'mixer.' + band + '.' + field, 'min': -100, 'max': 100,
              'value': self.settings_value['mixer'][band][field]}
             for band in operations.BANDS for field in ('hue', 'sat', 'lum')]}

    def apply(self, base, args):
        self.session.execution_started('synthetic.apply')
        self.calls.append(('apply', copy.deepcopy(args)))
        self.trace = []
        if self.session.state.get('pending') is not None:
            self.session.state['pending']['execution_trace'] = self.trace
            self.session.save()
        self.native.stage_phase = self.session.state.get('stage_phase', 'joint')
        return operations.apply_stage_operation(self.native, base, args, self.dependencies,
                                                self.trace, checkpoint=self.session.save)

    def restore(self, settings):
        self.session.execution_started('synthetic.restore')
        self.calls.append(('restore',))
        self.settings_value = copy.deepcopy(settings)

    def settings(self):
        return copy.deepcopy(self.settings_value)

    def render(self, path, long_edge=0, fmt='png'):
        self.session.execution_started('synthetic.render')
        self.calls.append(('render', str(path), fmt))
        image = read_srgb(self.session.state['versions']['v0']['master'], True)
        light = self.settings_value['light']
        image = ImageEnhance.Brightness(image).enhance(1+light['exposure']/4+light.get('highlights', 0)/500)
        image = ImageEnhance.Contrast(image).enhance(1+light.get('contrast', 0)/100)
        image = image.point(lambda x: min(255, max(0, round(x + light.get('blacks', 0)*(1-x/255)
                                                          + light.get('shadows', 0)*(1-x/255)**2))))
        image = ImageEnhance.Color(image).enhance(1+self.settings_value['color']['saturation']/100)
        blue_lum = self.settings_value.get('mixer', {}).get('blue', {}).get('lum', 0)
        if blue_lum:
            image.putdata([tuple(min(255, max(0, round(channel+blue_lum/2))) for channel in pixel)
                           if pixel[2] > pixel[0] and pixel[2] > pixel[1] else pixel
                           for pixel in image.getdata()])
        warmth = (self.settings_value.get('wb', {}).get('temp', 5083)-5083)/100
        tint = self.settings_value.get('wb', {}).get('tint', 0)
        red, green, blue = image.split()
        image = Image.merge('RGB', (red.point(lambda x: min(255, max(0, round(x+warmth)))),
                                     green.point(lambda x: min(255, max(0, round(x-tint)))),
                                     blue.point(lambda x: min(255, max(0, round(x-warmth))))))
        # Synthetic mapping only, not backend physics or semantic geometry coverage.
        for mask in self.settings_value.get('masks', []):
            if not mask.get('visible', True):
                continue
            adjust = mask.get('adjust', {})
            box = (0, 0, image.width, image.height//2)
            region = ImageEnhance.Brightness(image.crop(box)).enhance(1+adjust.get('exposure', 0)/4)
            red, green, blue = region.split()
            temp, tint = adjust.get('temp', 0), adjust.get('tint', 0)
            region = Image.merge('RGB', (red.point(lambda x: min(255, max(0, round(x+temp/2)))),
                                         green.point(lambda x: min(255, max(0, round(x-tint/2)))),
                                         blue.point(lambda x: min(255, max(0, round(x-temp/2))))))
            image.paste(region, box)
        if fmt == 'jpeg':
            image.save(path, format='JPEG', quality=92, icc_profile=lab.SRGB_PROFILE)
        else:
            lab.save_srgb_png(image, path)
        return list(image.size)

    def close(self):
        self.calls.append(('close',))


def fixture_stats(session, fixture):
    calls = session.native.calls if session.native else []
    rasters = [read_srgb(v['master'], True) for v in session.state['versions'].values()]
    observations = []
    for observation in session.state['observations']:
        pixels = [read_srgb(view[0], True) for view in observation['views']]
        gray = [all(r == g == b for r, g, b in image.getdata()) for image in pixels]
        color_pixels = [read_srgb(view[0], True).tobytes() for view in observation['color_views']]
        observations.append({'id': observation['id'], 'phase': observation['phase'], 'version': observation['version'],
            'artifact': observation['artifact'], 'gray_channels_equal': gray,
            'derived_matches_color': [image.tobytes() == original for image, original in zip(pixels, color_pixels)]})
    export = session.state.get('export')
    jpeg = None
    if export:
        with Image.open(export['path']) as image:
            image.load()
            jpeg = {'format': image.format, 'mode': image.mode, 'dimensions': list(image.size), 'icc_present': bool(image.info.get('icc_profile'))}
        lab.require_srgb_tag(export['path'])
    return {'status': session.state['status'], 'current': session.state['current'],
        'changed_version_pixels': [a.tobytes() != b.tobytes() for a, b in zip(rasters, rasters[1:])],
        'jpeg': jpeg, 'apply': sum(c[0] == 'apply' for c in calls), 'render': sum(c[0] == 'render' for c in calls),
        'pending': bool(session.state.get('pending')), 'prepared': bool(session.state.get('prepared_native_proposal')),
        'observations': observations, 'sources_unchanged': all((p.read_bytes(), p.stat().st_mtime_ns) == before for p, before in fixture.protected.items()),
        'legacy_imports': sorted(name for name in sys.modules if name in ('dsh_module_worker', 'dsh_photo_worker', 'dsh_module_validation', 'check_dsh_module_worker', 'check_dsh_stage_validation'))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round', required=True)
    parser.add_argument('--photo', choices=['DSC_3734'], required=True)
    parser.add_argument('--variant', choices=['module_support'], required=True)
    args = parser.parse_args()
    latest = SRC.parent/'experiments/validation/latest'
    if not Path(args.round).resolve().is_relative_to(latest.resolve()):
        raise ValueError('Test helper must be launched below validation/latest')
    session = None
    with tempfile.TemporaryDirectory(prefix='stage-SYNTHETIC-test-', dir=latest) as temporary, ExitStack() as guards:
        blocked = AssertionError('Offline fixture: real native/provider/network/subprocess forbidden')
        for owner, name in ((lab, 'Native'), (lab, 'api'), (lab.LightCraft, 'call'),
                            (socket, 'create_connection'), (socket.socket, 'connect'), (subprocess, 'Popen')):
            guards.enter_context(patch.object(owner, name, side_effect=blocked))
        fixture = StrictSyntheticFixture(Path(temporary))
        guards.enter_context(patch.object(lab, 'ROOT', fixture.root))
        guards.enter_context(patch.object(stage, 'StageBackend', SyntheticBackend))
        def inspect_synthetic_raw(path):
            if Path(path) != fixture.raw:
                raise AssertionError('Only the labelled synthetic RAW boundary is fake')
            return {'fixture_only': True, 'dimensions': fixture.dimensions}
        guards.enter_context(patch.object(lab, 'inspect_dng', side_effect=inspect_synthetic_raw))
        try:
            session = stage.StageSession(fixture.round, validation.jobs()[0])
            for line in sys.stdin:
                try:
                    request = json.loads(line)
                    op = request['op']
                    if op == 'fixture_info': value = {'root': str(fixture.root), 'round': str(fixture.round)}
                    elif op == 'schemas': value = session.tool_schemas()
                    elif op == 'snapshot': value = session.snapshot()
                    elif op == 'project_snapshot': value = session.project_snapshot()
                    elif op == 'stage_turn': value = session.stage_turn(request['arguments'], request.get('source_observation_id'))
                    elif op == 'fixture_stats': value = fixture_stats(session, fixture)
                    else: raise ValueError('Unknown stage fixture operation; legacy action dispatch forbidden')
                    print(json.dumps({'ok': True, 'value': value}, ensure_ascii=False), flush=True)
                except Exception as exc:
                    print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), flush=True)
                    # Preserve the isolated files until DSH has closed its locks;
                    # the bridge has failed and cannot dispatch another action.
                    for _ in sys.stdin:
                        pass
                    break
        finally:
            if session is not None and session.native is not None:
                session.native.close()


if __name__ == '__main__':
    main()
