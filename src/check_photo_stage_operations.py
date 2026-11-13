"""Fake-only stage operation checks. No live backend, model, render or file IO."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_stage_operations as stage
from photo_capability_catalog import _check, COMMAND_IDS


def dependencies():
    return {'commands': [{'id': ident, 'enabled': True} for ident in (*COMMAND_IDS, 'library.state')]}


def controls():
    return [{'id': ident, 'min': lo, 'max': hi, 'value': 0}
            for ident, (lo, hi) in stage.CONTROL_RANGES.items()]


def baseline():
    return {'light': {f: 0 for f in stage.TONE_FIELDS},
        'wb': {'mode': 'custom', 'scale': 'kelvin', 'temp': 6500, 'tint': 0},
        'color': {'vibrance': 0, 'saturation': 0},
        'curve': {'master': [], 'red': [], 'green': [], 'blue': [],
            'highlights': 0, 'lights': 0, 'darks': 0, 'shadows': 0,
            'split_shadows': 25, 'split_mid': 50, 'split_highlights': 75, 'refine_saturation': 100},
        'grading': {b: {'hue': 0, 'sat': 0, 'lum': 0} for b in ('shadows', 'midtones', 'highlights', 'global')},
        'masks': [], 'point_colors': [], 'crop': {}, 'geometry': {}, 'orientation': 'Normal', 'optics': {}}


def create(kind='linear', values=None):
    geometry = {'start': [.5, .1], 'end': [.5, .7]} if kind == 'linear' else {
        'center': [.5, .4], 'rx': .2, 'ry': .15, 'angle': 25, 'feather': 60}
    return {'mask_plan': {'action': 'create', 'kind': kind, 'geometry': geometry,
                         'values': values if values is not None else {'exposure': .4}}}


def mask(ident=73, kind='linear'):
    p = create(kind)['mask_plan']
    return {'id': ident, 'name': 'Accepted previous mask', 'visible': True, 'invert': False,
        'components': [{'op': 'add', 'invert': False, 'shape': stage._created_shape(p)}],
        'adjust': {**stage.NEUTRAL_LOCAL, 'temp': -18, 'exposure': .65}}


def merge(target, patch_value):
    for key, value in patch_value.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


class FakeNative:
    def __init__(self, base=None):
        self.value = copy.deepcopy(base if base is not None else baseline())
        self.calls = []
        self.active = None
        self.counter = 3017
        self.stage_phase = 'joint'
        self.failure = None
        self.native_controls = controls()

    def settings(self):
        return copy.deepcopy(self.value)

    def controls(self):
        return copy.deepcopy(self.native_controls)

    def restore(self, base):
        self.calls.append(('restore', copy.deepcopy(base)))
        self.value = copy.deepcopy(base)
        self.active = None
        if self.failure == 'restore':
            self.value['geometry']['rotate'] = 1

    def apply(self, base, args):
        self.calls.append(('apply', copy.deepcopy(args)))
        self.restore(base)
        for ident, value in args.get('values', {}).items():
            path = next((k for k, v in stage.SETTINGS_PATHS.items() if v == ident), ident).split('.')
            target = self.value
            for part in path[:-1]:
                target = target.setdefault(part, {})
            target[path[-1]] = value
        merge(self.value, args.get('settings', {}))
        return self.settings()

    def call(self, tool, command):
        self.calls.append((tool, copy.deepcopy(command)))
        assert tool == 'command_run', tool
        ident, params = command['id'], command.get('params', {})
        if ident == 'library.state':
            return {'structuredContent': {'activeMask': self.active}}
        if self.failure == 'throw':
            raise RuntimeError('Unknown native outcome')
        if ident == 'mask.add':
            self.counter += 29
            plan = {'kind': params['kind'], 'geometry': {k: v for k, v in params.items() if k != 'kind'}}
            new = mask(self.counter, params['kind'])
            new['components'][0]['shape'] = stage._created_shape(plan)
            # Every field is deliberately inherited nonneutral, including detail4.
            new['adjust'] = {f: 33 for f in stage.NEUTRAL_LOCAL}
            new['adjust'].update(exposure=1.25, amount=71)
            self.value['masks'].append(new)
            self.active = self.counter
            if self.failure == 'shape':
                new['components'][0]['shape']['start'] = {'x': .2, 'y': .2}
            if self.failure == 'geometry':
                self.value['crop']['angle'] = 2
            if self.failure == 'oldmask':
                self.value['masks'][0]['adjust']['temp'] = 19
            if self.failure == 'hidden':
                new['visible'] = False
            result = {'activeMask': 5 if self.failure == 'bad_id' else self.active}
            if self.failure == 'missing_id':
                self.value['masks'].clear()
            if self.failure == 'active_id':
                self.active = None
        elif ident == 'mask.adjust':
            current = next(m for m in self.value['masks'] if m['id'] == params['id'])
            current['adjust'].update(params['values'])
            if self.failure == 'patch':
                current['adjust']['contrast'] = 81
            if self.failure == 'detail' and len(params['values']) == 20:
                current['adjust']['noise'] = 17
            result = {'activeMask': self.active}
        elif ident in ('mask.update', 'mask.visible', 'mask.select', 'mask.delete', 'mask.rename'):
            current = next(m for m in self.value['masks'] if m['id'] == params['id'])
            if ident == 'mask.update': current['components'][params.get('component', 0)]['shape'] = copy.deepcopy(params['shape'])
            if ident == 'mask.visible': current['visible'] = params['visible']
            if ident == 'mask.rename': current['name'] = params['name']
            if ident == 'mask.select': self.active = params['id']
            if ident == 'mask.delete':
                self.value['masks'].remove(current)
                self.active = self.value['masks'][-1]['id'] if self.value['masks'] else None
            result = None if ident == 'mask.select' else {'activeMask': self.active}
        elif ident == 'develop.curve':
            self.value['curve']['master'] = [dict(zip(('x', 'y'), point)) for point in params['points']]
            result = None
        elif ident == 'curve.reset':
            if params['channel'] == 'master':
                self.value['curve']['master'] = []
            else:
                self.value['curve'].update(highlights=0, lights=0, darks=0, shadows=0,
                    split_shadows=25, split_mid=50, split_highlights=75)
            result = {'channel': params['channel']}
        else:
            raise AssertionError('Unexpected command ' + ident)
        if self.failure == 'pending':
            result = {'pending': True}
        if self.failure == 'started':
            result = {'started': True}
        if self.failure == 'envelope':
            return {'isError': True}
        return {'isError': False, 'content': [{'type': 'text', 'text': 'ok'}]} if result is None else {'structuredContent': result}

    def render(self, *args, **kwargs):
        raise AssertionError('Outer worker owns render')


class StageChecks(unittest.TestCase):
    def setUp(self):
        self.base = baseline()
        self.deps = dependencies()

    def validate(self, arguments, phase='joint', current=None, native_controls=None):
        return stage.validate_stage_operation(arguments, self.base if current is None else current,
            phase, self.deps, native_controls)

    def test_precise_ranges_all_scalar_paths_and_modules(self):
        for ident, (lo, hi) in stage.CONTROL_RANGES.items():
            for value in (lo, hi):
                with self.subTest(ident=ident, value=value):
                    current = baseline()
                    current['curve'].update(split_shadows=10, split_mid=50, split_highlights=90)
                    if ident == 'curve.splitShadows': current['curve']['split_mid'] = 80
                    if ident == 'curve.splitMid': current['curve'].update(split_shadows=10, split_highlights=90)
                    if ident == 'curve.splitHighlights': current['curve']['split_mid'] = 20
                    self.validate({'values': {ident: value}}, current=current)
            for value in (lo - .01, hi + .01, True, float('nan'), float('inf'), '1', None, {}):
                with self.subTest(ident=ident, invalid=repr(value)):
                    with self.assertRaises(ValueError): self.validate({'values': {ident: value}})
        self.assertEqual(self.validate({'values': {'wb.tint': 3, 'light.blacks': -99, 'grading.shadows.hue': 210}}),
                         ['grading', 'light', 'wb'])

    def test_current_control_data_overrides_wb_and_availability(self):
        native = controls()
        next(row for row in native if row['id'] == 'wb.temp').update(min=-100, max=100)
        self.validate({'values': {'wb.temp': -90}}, native_controls=native)
        with self.assertRaises(ValueError): self.validate({'values': {'wb.temp': 6500}}, native_controls=native)
        next(row for row in native if row['id'] == 'light.blacks').update(min=-60, max=80)
        with self.assertRaises(ValueError): self.validate({'settings': {'light': {'blacks': -61}}}, native_controls=native)
        next(row for row in native if row['id'] == 'light.blacks')['enabled'] = False
        with self.assertRaises(ValueError): self.validate({'values': {'light.blacks': 0}}, native_controls=native)
        with self.assertRaises(ValueError): self.validate({'values': {'light.blacks': 0}}, native_controls=[])
        self.deps['controls'] = native
        with self.assertRaises(ValueError): self.validate({'settings': {'light': {'blacks': 0}}})

    def test_settings_leaves_snake_names_and_empty_context(self):
        for path, ident in stage.SETTINGS_PATHS.items():
            value = 0 if stage.CONTROL_RANGES[ident][0] <= 0 else sum(stage.CONTROL_RANGES[ident]) / 2
            obj = {}; target = obj
            for part in path.split('.')[:-1]: target = target.setdefault(part, {})
            target[path.split('.')[-1]] = value
            self.validate({'settings': obj})
            target[path.split('.')[-1]] = stage.CONTROL_RANGES[ident][1] + 1
            with self.assertRaises(ValueError): self.validate({'settings': obj})
        inherited = baseline()
        inherited.update(vignette={'amount': -73}, profile={'amount': 180}, point_colors=[{'hue': 22}])
        inherited['curve'].update(red=[{'x': 0, 'y': .2}], split_shadows=200)
        self.validate({'settings': {'light': {'blacks': -100}, 'mixer': {}, 'grading': {}}}, current=inherited)
        for args in ({}, {'settings': {}}, {'values': {}}, {'commands': []}, {'settings': {'curve': {}}}):
            with self.assertRaises(ValueError): self.validate(args)
        with self.assertRaises(ValueError): self.validate({'settings': {'calibration': {'redHue': 3}}})
        with self.assertRaises(ValueError): self.validate({'values': {'light.blacks': -2}, 'settings': {'light': {'blacks': 2}}})

    def test_scopes_cannot_bypass_values_settings_or_commands(self):
        excluded = ('profile.amount', 'calibration.shadowsTint', 'curve.refineSaturation',
            'grading.global.hue', 'vignette.amount', 'pointColor.0.hueShift', 'detail.sharpness')
        for ident in excluded:
            with self.subTest(ident=ident):
                with self.assertRaises(ValueError): self.validate({'values': {ident: 0}})
        for settings in ({'curve': {'red': []}}, {'masks': []}, {'profile': {}}, {'geometry': {}},
                         {'grading': {'global': {'hue': 0}}}, {'point_colors': []}):
            with self.assertRaises(ValueError): self.validate({'settings': settings})
        for phase, ident in (('tone', 'wb.temp'), ('tone', 'effects.texture'), ('color', 'light.blacks'), ('color', 'effects.dehaze')):
            with self.assertRaises(ValueError): self.validate({'values': {ident: 0}}, phase)
        for phase in ('joint', 'integrated'):
            self.validate({'values': {'effects.texture': 4, 'light.blacks': -2, 'wb.tint': 2}}, phase)
        for ident in COMMAND_IDS:
            if ident in ('develop.curve', 'curve.reset', 'mask.adjust'): continue
            with self.subTest(command=ident):
                with self.assertRaises(ValueError): self.validate({'commands': [{'id': ident, 'params': {}}]})
        with self.assertRaises(ValueError): self.validate({'phase': 'joint', 'values': {'light.blacks': 0}}, 'tone')

    def test_wb_metadata_only_with_explicit_current_context(self):
        self.validate({'settings': {'wb': {'temp': 5800, 'tint': 3, 'mode': 'custom', 'scale': 'kelvin'}}}, 'color')
        self.validate({'values': {'wb.temp': 5800}, 'settings': {'wb': {'mode': 'custom', 'scale': 'kelvin'}}}, 'color')
        for wb in ({'mode': 'custom'}, {'scale': 'kelvin'}, {'temp': 5800, 'mode': 'auto'},
                   {'temp': 5800, 'scale': 'relative'}):
            with self.assertRaises(ValueError): self.validate({'settings': {'wb': wb}}, 'color')
        legacy = baseline(); legacy['wb'].pop('scale')
        with self.assertRaises(ValueError): self.validate({'settings': {'wb': {'temp': 5800, 'scale': 'kelvin'}}}, 'color', legacy)

    def test_master_curve_and_atomic_reset(self):
        self.validate({'settings': {'curve': {'master': [{'x': 0, 'y': 0}, {'x': .5, 'y': .7}, {'x': 1, 'y': 1}]}}}, 'tone')
        self.validate({'commands': [{'id': 'develop.curve', 'params': {'channel': 'master', 'points': [[0, 0], [1, 1]]}}]}, 'tone')
        self.validate({'commands': [{'id': 'curve.reset', 'params': {'channel': 'parametric'}}]}, 'tone')
        for channel in ('all', 'point', 'red', 'green', 'blue', 'rgb', 'luma', None):
            params = {} if channel is None else {'channel': channel}
            with self.assertRaises(ValueError): self.validate({'commands': [{'id': 'curve.reset', 'params': params}]})
        for points in ([[.5, 0], [.4, 1]], [[0, 0], [0, 1]], [[0, -1]], [[True, 0]]):
            with self.assertRaises(ValueError): self.validate({'commands': [{'id': 'develop.curve', 'params': {'points': points}}]})
        with self.assertRaises(ValueError): self.validate({'values': {'curve.master': []}})
        with self.assertRaises(ValueError): self.validate({'values': {'curve.splitShadows': 60}})
        with self.assertRaises(ValueError): self.validate({'commands': [{'id': 'curve.reset', 'params': {'channel': 'master'}}], 'values': {'light.exposure': 1}})

    def test_long_auxiliary_fields_are_not_operational_limits(self):
        args = {'values': {'light.blacks': -100}, 'rationale': 'useful ' * 20000,
                'review': {'remaining_gaps': 'a' * 200000}, 'predicted_effect': ['x'] * 5000}
        self.assertEqual(self.validate(args, 'tone'), ['light'])

    def test_mask_schema_explicit_geometry_native_units(self):
        for kind in ('linear', 'radial'):
            args = create(kind)
            _check(args['mask_plan'], stage.mask_plan_schema('joint'), 'mask_plan')
            self.validate(args)
            for key in tuple(args['mask_plan']['geometry']):
                missing = copy.deepcopy(args); missing['mask_plan']['geometry'].pop(key)
                with self.assertRaises(ValueError): self.validate(missing)
        for radius in (0, -.1, float('inf'), True):
            bad = create('radial'); bad['mask_plan']['geometry']['rx'] = radius
            with self.assertRaises(ValueError): self.validate(bad)
        large = create('radial'); large['mask_plan']['geometry']['rx'] = 1.4; self.validate(large)
        for key in ('invert', 'op', 'addComponent', 'id', 'name'):
            bad = create(); bad['mask_plan'][key] = False
            with self.assertRaises(ValueError): self.validate(bad)
        bad = create(); bad['mask_plan']['geometry']['end'] = bad['mask_plan']['geometry']['start']
        with self.assertRaises(ValueError): self.validate(bad)
        for field in stage.NEUTRAL_LOCAL:
            if field not in stage.LOCAL_FIELDS:
                bad = create(values={field: 0})
                with self.assertRaises(ValueError): self.validate(bad)
        for key, value in (('values', {'light.blacks': 0}), ('settings', {'light': {'blacks': 0}}),
                           ('commands', [{'id': 'curve.reset', 'params': {'channel': 'master'}}])):
            with self.assertRaises(ValueError): self.validate({**create(), key: value})
        for phase, values in (('tone', {'temp': 2}), ('color', {'exposure': .2})):
            with self.assertRaises(ValueError): self.validate(create(values=values), phase)
        self.deps['commands'] = [r for r in self.deps['commands'] if r['id'] != 'mask.adjust']
        with self.assertRaises(ValueError): self.validate(create())

    def test_mask_create_neutralizes_all20_preserves_old_and_no_render(self):
        for kind in ('linear', 'radial'):
            base = baseline(); base['masks'] = [mask()]
            native = FakeNative(base); native.stage_phase = 'color'
            args = create(kind, {'temp': -8, 'tint': 3})
            trace, snapshots = [], []
            result = stage.apply_stage_operation(native, base, args, dependencies(), trace,
                lambda: snapshots.append(copy.deepcopy(trace)))
            self.assertEqual(result['masks'][0], base['masks'][0])
            new = result['masks'][1]
            self.assertEqual(new['id'], 3046)
            self.assertEqual(new['adjust'], {**stage.NEUTRAL_LOCAL, 'temp': -8, 'tint': 3})
            commands = [args for tool, args in native.calls if tool == 'command_run' and args['id'] != 'library.state']
            self.assertEqual([c['id'] for c in commands], ['mask.add', 'mask.adjust', 'mask.adjust'])
            self.assertEqual(len(commands[1]['params']['values']), 20)
            self.assertEqual(commands[1]['params']['values'], stage.NEUTRAL_LOCAL)
            self.assertEqual(commands[2]['params'], {'id': 3046, 'values': {'temp': -8, 'tint': 3}})
            self.assertTrue(all(row['status'] == 'complete' for row in trace))
            self.assertEqual(snapshots[0][0]['status'], 'pending')
            self.assertTrue(any(s[-1]['status'] == 'pending' and s[-1].get('kind') == 'mask_neutral_init' for s in snapshots))
            self.assertEqual(base['masks'][0]['adjust']['exposure'], .65)

    def test_existing_update_patch_and_native_commands_preserve_prior_fields(self):
        base = baseline(); base['masks'] = [mask()]
        for use_plan in (True, False):
            native = FakeNative(base); native.stage_phase = 'color'; trace = []
            args = {'mask_plan': {'action': 'adjust', 'id': 73, 'values': {'temp': 5}}} if use_plan else {
                'commands': [{'id': 'mask.adjust', 'params': {'id': 73, 'values': {'temp': 5}}}]}
            actual = stage.apply_stage_operation(native, base, args, dependencies(), trace)
            self.assertEqual(actual['masks'][0]['adjust'], {**base['masks'][0]['adjust'], 'temp': 5})
            self.assertFalse(any(c.get('id') == 'mask.add' for tool, c in native.calls if tool == 'command_run'))
            self.assertTrue(all(row['status'] == 'complete' for row in trace))
        for ident in (74, None, True, -1):
            with self.assertRaises(ValueError): self.validate({'mask_plan': {'action': 'adjust', 'id': ident, 'values': {'temp': 5}}}, current=base)
        invalid = copy.deepcopy(base); invalid['masks'][0]['components'].append(copy.deepcopy(invalid['masks'][0]['components'][0]))
        with self.assertRaises(ValueError): self.validate({'mask_plan': {'action': 'adjust', 'id': 73, 'values': {'temp': 5}}}, current=invalid)

    def test_simple_geometry_and_lifecycle_commands(self):
        base = baseline(); base['masks'] = [mask()]
        radial = stage._created_shape(create('radial')['mask_plan'])
        linear = stage._created_shape(create()['mask_plan'])
        for command in ({'id': 'mask.update', 'params': {'id': 73, 'component': 0, 'shape': radial}},
                        {'id': 'mask.update', 'params': {'id': 73, 'component': 0, 'shape': linear}},
                        {'id': 'mask.visible', 'params': {'id': 73, 'visible': False}},
                        {'id': 'mask.rename', 'params': {'id': 73, 'name': 'Reviewed sky'}},
                        {'id': 'mask.select', 'params': {'id': 73}},
                        {'id': 'mask.delete', 'params': {'id': 73}}):
            with self.subTest(command=command['id']):
                native = FakeNative(base); trace = []
                result = stage.apply_stage_operation(native, base, {'commands': [command]}, dependencies(), trace)
                if result['masks']:
                    self.assertEqual(result['masks'][0]['adjust'], base['masks'][0]['adjust'])
                self.assertTrue(all(row['status'] == 'complete' for row in trace))
        for shape in ({'kind': 'sky'}, {**radial, 'invert': True}, {'kind': 'radial', 'center': {'x': .5, 'y': .4}},
                      {**radial, 'rx': 0}, {**radial, 'feather': 101}):
            with self.assertRaises(ValueError):
                self.validate({'commands': [{'id': 'mask.update', 'params': {'id': 73, 'shape': shape}}]}, current=base)
        for command in ({'id': 'mask.update', 'params': {'id': 73, 'component': 1, 'shape': radial}},
                        {'id': 'mask.visible', 'params': {'id': 73}},
                        {'id': 'mask.select', 'params': {}},
                        {'id': 'mask.rename', 'params': {'id': 999, 'name': 'No guessed ID'}}):
            with self.assertRaises(ValueError): self.validate({'commands': [command]}, current=base)
        with self.assertRaises(ValueError):
            self.validate({'commands': [{'id': 'mask.delete', 'params': {'id': 73}},
                {'id': 'mask.adjust', 'params': {'id': 73, 'values': {'temp': 2}}}]}, current=base)

    def test_pending_bad_actual_ids_and_invariants_retained_without_replay(self):
        for failure in ('pending', 'started', 'bad_id', 'missing_id', 'active_id', 'geometry', 'shape',
                        'hidden', 'oldmask', 'restore', 'detail', 'envelope', 'throw'):
            with self.subTest(failure=failure):
                base = baseline(); base['masks'] = [mask()]
                native = FakeNative(base); native.failure = failure; trace = []
                with self.assertRaises((ValueError, RuntimeError)):
                    stage.apply_stage_operation(native, base, create(), dependencies(), trace)
                self.assertEqual(trace[0]['status'], 'pending')
                count = len(native.calls)
                with self.assertRaises(ValueError): stage.apply_stage_operation(native, base, create(), dependencies(), trace)
                self.assertEqual(len(native.calls), count)

    def test_adjust_readback_mismatch_keeps_pending(self):
        base = baseline(); base['masks'] = [mask()]
        for operation in ({'mask_plan': {'action': 'adjust', 'id': 73, 'values': {'temp': 2}}},
                          {'commands': [{'id': 'mask.adjust', 'params': {'id': 73, 'values': {'temp': 2}}}]}):
            native = FakeNative(base); native.failure = 'patch'; trace = []
            with self.assertRaises(ValueError): stage.apply_stage_operation(native, base, operation, dependencies(), trace)
            self.assertEqual(trace[0]['status'], 'pending')

    def test_ordinary_edits_delegate_strict_with_current_controls(self):
        native = FakeNative(); native.stage_phase = 'tone'; trace = []
        args = {'settings': {'light': {'blacks': -100}}}
        original = stage.apply_strict
        with patch.object(stage, 'apply_strict', wraps=original) as strict:
            actual = stage.apply_stage_operation(native, self.base, args, self.deps, trace)
            self.assertEqual(strict.call_count, 1)
        self.assertEqual(actual['light']['blacks'], -100)
        self.assertTrue(all(row['status'] == 'complete' for row in trace))
        native = FakeNative(); native.stage_phase = 'tone'
        with self.assertRaises(ValueError):
            stage.apply_stage_operation(native, self.base, {'phase': 'joint', 'values': {'wb.tint': 2}}, self.deps, [])
        self.assertEqual(native.calls, [])
        native = FakeNative(); next(row for row in native.native_controls if row['id'] == 'light.blacks')['min'] = -30
        with self.assertRaises(ValueError): stage.apply_stage_operation(native, self.base, args, self.deps, [])
        self.assertEqual(native.calls, [])

    def test_atomic_curve_commands_read_back_and_preserve_rgb(self):
        for command in ({'id': 'develop.curve', 'params': {'channel': 'master', 'points': [[0, 0], [.5, .6], [1, 1]]}},
                        {'id': 'curve.reset', 'params': {'channel': 'master'}},
                        {'id': 'curve.reset', 'params': {'channel': 'parametric'}}):
            base = baseline(); base['curve']['red'] = [{'x': 0, 'y': .3}]
            base['curve']['master'] = [{'x': 0, 'y': .1}]
            native = FakeNative(base); trace = []
            actual = stage.apply_stage_operation(native, base, {'commands': [command]}, dependencies(), trace)
            self.assertEqual(actual['curve']['red'], base['curve']['red'])
            self.assertTrue(all(row['status'] == 'complete' for row in trace))


if __name__ == '__main__':
    unittest.main(verbosity=2)
