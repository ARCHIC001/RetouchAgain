"""Offline schema/operation boundary parity; no backend, render, transport or IO."""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import photo_stage_operations as stage
from photo_stage_schema import pre_tone_wb_schema, stage_edit_schema
from photo_capability_catalog import _check, _object
from check_photo_stage_operations import baseline, controls, create, dependencies, mask


PHASES = ('tone', 'color', 'joint', 'integrated')


def nested(path, value):
    result = node = {}
    parts = path.split('.')
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return result


class StageSchemaChecks(unittest.TestCase):
    def setUp(self):
        self.base = baseline()
        self.base['masks'] = [mask()]
        self.deps = dependencies()

    def schema_check(self, args, phase):
        _check(args, _object(stage_edit_schema(phase)), 'edit')

    def actual_check(self, args, phase, base=None, native=None):
        stage.validate_stage_operation(args, self.base if base is None else base,
            phase, self.deps, native)

    def parity(self, args, phase, accepted=True, base=None):
        for validator in (lambda: self.schema_check(args, phase),
                          lambda: self.actual_check(args, phase, base)):
            if accepted:
                validator()
            else:
                with self.assertRaises(ValueError):
                    validator()

    def test_exact_ids_snake_paths_ranges_all_phases(self):
        for phase in PHASES:
            props = stage_edit_schema(phase)
            self.assertEqual(set(props), {'values', 'settings', 'commands', 'mask_plan'})
            self.assertEqual(set(props['values']['properties']), stage._allowed_ids(phase))
            for ident, (lo, hi) in stage.CONTROL_RANGES.items():
                path = next(p for p, i in stage.SETTINGS_PATHS.items() if i == ident)
                base = copy.deepcopy(self.base)
                base['curve'].update(split_shadows=10, split_mid=50, split_highlights=90)
                if ident == 'curve.splitShadows': base['curve']['split_mid'] = 80
                if ident == 'curve.splitHighlights': base['curve']['split_mid'] = 20
                for value in (lo, hi):
                    for args in ({'values': {ident: value}}, {'settings': nested(path, value)}):
                        with self.subTest(phase=phase, ident=ident, value=value):
                            self.parity(args, phase, ident in stage._allowed_ids(phase), base)
                if ident in stage._allowed_ids(phase):
                    for value in (lo - .1, hi + .1, True, None, '1', {}, float('nan'), float('inf'), -float('inf')):
                        for args in ({'values': {ident: value}}, {'settings': nested(path, value)}):
                            self.parity(args, phase, False)
            for path in ('profile.amount', 'curve.red', 'curve.refine_saturation',
                         'calibration.redHue', 'grading.global.hue', 'masks', 'geometry', 'light.exposure.extra'):
                self.parity({'settings': nested(path, 0)}, phase, False)
            self.parity({'values': {'unknown.control': 0}}, phase, False)
            self.parity({'settings': {'light.exposure': 1}}, phase, False)

    def test_command_enums_and_unknown_params(self):
        for phase in PHASES:
            branches = stage_edit_schema(phase)['commands']['items']['oneOf']
            ids = {b['properties']['id']['const'] for b in branches}
            expected = {'mask.adjust', 'mask.update', 'mask.select', 'mask.delete', 'mask.visible', 'mask.rename'}
            if phase != 'color': expected |= {'develop.curve', 'curve.reset'}
            if phase != 'tone': expected.add('develop.wbPick')
            self.assertEqual(ids, expected)
            field = stage._local_fields(phase)[0]
            commands = [
                {'id': 'develop.curve', 'params': {'points': [[0, 0], [1, 1]]}},
                {'id': 'curve.reset', 'params': {'channel': 'parametric'}},
                {'id': 'develop.wbPick', 'params': {'x': .23, 'y': .71}},
                {'id': 'mask.adjust', 'params': {'id': 73, 'values': {field: .2}}},
                {'id': 'mask.select', 'params': {'id': 73}},
                {'id': 'mask.delete', 'params': {'id': 73}},
                {'id': 'mask.visible', 'params': {'id': 73, 'visible': False}},
                {'id': 'mask.rename', 'params': {'id': 73, 'name': 'sky ' * 20000}},
            ]
            for kind in ('linear', 'radial'):
                shape = stage._created_shape(create(kind)['mask_plan'])
                commands.append({'id': 'mask.update', 'params': {'id': 73, 'shape': shape}})
            for command in commands:
                self.parity({'commands': [command]}, phase, command['id'] in expected)
                extra = copy.deepcopy(command); extra['params']['unknown'] = 1
                self.parity({'commands': [extra]}, phase, False)
                extra = copy.deepcopy(command); extra['unknown'] = 1
                self.parity({'commands': [extra]}, phase, False)
            for ident in ('mask.add', 'mask.invert', 'mask.deleteAll', 'pointColor.pick', 'no.such.command'):
                self.parity({'commands': [{'id': ident, 'params': {}}]}, phase, False)
            for field, bounds in stage._LOCAL.items():
                for value in (bounds['minimum'], bounds['maximum']):
                    self.parity({'commands': [{'id': 'mask.adjust', 'params': {
                        'id': 73, 'values': {field: value}}}]}, phase, field in stage._local_fields(phase))
            for channel in ('all', 'point', 'red', 'rgb', None):
                self.parity({'commands': [{'id': 'curve.reset', 'params': {'channel': channel}}]}, phase, False)
            for ident in ('mask.adjust', 'mask.select', 'mask.delete', 'mask.visible', 'mask.rename', 'mask.update'):
                for value in (None, True, -1, 4294967296):
                    self.parity({'commands': [{'id': ident, 'params': {'id': value}}]}, phase, False)

    def test_wb_picker_schema_coordinate_and_phase_parity(self):
        for phase in PHASES:
            for params in ({'x': 0, 'y': 1}, {'x': 1, 'y': 0}, {'x': .23, 'y': .71}):
                self.parity({'commands': [{'id': 'develop.wbPick', 'params': params}]}, phase, phase != 'tone')
            for key in ('x', 'y'):
                for value in (float('nan'), float('inf'), -float('inf'), -.001, 1.001, True, '0.5', None):
                    params = {'x': .23, 'y': .71, key: value}
                    self.parity({'commands': [{'id': 'develop.wbPick', 'params': params}]}, phase, False)
            for params in ({}, {'x': .23}, {'y': .71}, {'x': .23, 'y': .71, 'channel': 'master'}):
                self.parity({'commands': [{'id': 'develop.wbPick', 'params': params}]}, phase, False)
            self.parity({'commands': [{'id': 'develop.wb', 'params': {'mode': 'auto'}}]}, phase, False)
        spec = next(b for b in stage_edit_schema('color')['commands']['items']['oneOf']
                    if b['properties']['id']['const'] == 'develop.wbPick')['properties']['params']
        self.assertEqual(set(spec['required']), {'x', 'y'})
        self.assertEqual(set(spec['properties']), {'x', 'y'})
        self.assertIn('SourceLevel::Thumb', spec['description'])
        self.assertIn('ROI', spec['description'])

    def test_curve_representation_and_physical_cardinality(self):
        for phase in PHASES:
            for points in ([], [{'x': 0, 'y': 0}, {'x': 1, 'y': 1}]):
                self.parity({'settings': {'curve': {'master': points}}}, phase, phase != 'color')
            for points in ([[0, 0]], [{'x': 0}], [{'x': 0, 'y': 1, 'z': 0}], [{'x': float('nan'), 'y': 0}]):
                self.parity({'settings': {'curve': {'master': points}}}, phase, False)
            for points in ([[0]], [[0, 0, 0]], [[0, 2]], [[True, 0]], [{'x': 0, 'y': 0}]):
                self.parity({'commands': [{'id': 'develop.curve', 'params': {'points': points}}]}, phase, False)
            for kind in ('linear', 'radial'):
                args = create(kind, {stage._local_fields(phase)[0]: .2})
                self.parity(args, phase)
                geometry = args['mask_plan']['geometry']
                for key in tuple(geometry):
                    bad = copy.deepcopy(args); del bad['mask_plan']['geometry'][key]
                    self.parity(bad, phase, False)
                point_key = 'start' if kind == 'linear' else 'center'
                for point in ([.5], [.5, .5, .5], [True, .5], [float('inf'), .5]):
                    bad = copy.deepcopy(args); bad['mask_plan']['geometry'][point_key] = point
                    self.parity(bad, phase, False)
        shape = stage._created_shape(create('radial')['mask_plan'])
        shape['rx'] = 1.4
        self.parity({'commands': [{'id': 'mask.update', 'params': {'id': 73, 'shape': shape}}]}, 'joint')
        for key, value in (('invert', True), ('rx', 0), ('ry', -.1), ('angle', float('inf')), ('feather', 101)):
            bad = copy.deepcopy(shape); bad[key] = value
            self.parity({'commands': [{'id': 'mask.update', 'params': {'id': 73, 'shape': bad}}]}, 'joint', False)
        self.parity({'commands': [{'id': 'mask.update', 'params': {'id': 73, 'component': 1, 'shape': shape}}]}, 'joint', False)

    def test_tone_curve_small_reset_schema_and_runtime_ordering(self):
        def operations(points):
            return ({'settings': {'curve': {'master': [dict(zip(('x', 'y'), p)) for p in points]}}},
                    {'commands': [{'id': 'develop.curve', 'params': {'points': points}}]})
        for points in ([], [[.1, .3], [.9, .8]], [[.1, .3], [.3, .3], [.8, .9]],
                       [[0, .2], [.2, .3], [.4, .4], [.6, .6], [.9, .8]]):
            for args in operations(points): self.parity(args, 'tone')
        for points in ([[.5, .4]], [[i / 5, i / 5] for i in range(6)]):
            for args in operations(points):
                self.parity(args, 'tone', False)
                for phase in ('joint', 'integrated'): self.parity(args, phase)
        for points in ([[0, .7], [1, .4]], [[.2, 0], [.2, 1]], [[.8, 0], [.2, 1]]):
            for args in operations(points):
                self.schema_check(args, 'tone')
                with self.assertRaises(ValueError): self.actual_check(args, 'tone')
        for args in operations([[i / 7, 1 - i / 7] for i in range(8)]):
            for phase in ('joint', 'integrated'): self.parity(args, phase)
        spec = next(b for b in stage_edit_schema('tone')['commands']['items']['oneOf']
                    if b['properties']['id']['const'] == 'develop.curve')['properties']['params']
        self.assertIn('nondecreasing', spec['description'])
        self.assertNotIn('need not be monotonic', spec['description'])

    def test_pre_tone_wb_values_only_schema_ranges_and_metadata(self):
        props = stage_edit_schema('pre_tone_wb')
        self.assertEqual(props, pre_tone_wb_schema())
        self.assertEqual(set(props), {'values'})
        self.assertEqual(set(props['values']['properties']), {'wb.temp', 'wb.tint'})
        for ident in ('wb.temp', 'wb.tint'):
            lo, hi = stage.CONTROL_RANGES[ident]
            for value in (lo, hi): self.parity({'values': {ident: value}}, 'pre_tone_wb')
            for value in (lo - .1, hi + .1, True, None, '3', float('nan'), float('inf')):
                self.parity({'values': {ident: value}}, 'pre_tone_wb', False)
        self.parity({'values': {'wb.temp': 5900, 'wb.tint': 4}}, 'pre_tone_wb')
        self.parity({'values': {}}, 'pre_tone_wb', False)
        for ident in ('wb.mode', 'wb.scale', 'light.exposure', 'curve.highlights', 'color.saturation', 'mask.temp'):
            self.parity({'values': {'wb.tint': 4, ident: 0}}, 'pre_tone_wb', False)
        for key, value in (('settings', {}), ('commands', []), ('mask_plan', {}), ('phase', 'pre_tone_wb')):
            self.parity({'values': {'wb.tint': 4}, key: value}, 'pre_tone_wb', False)
        native = controls(); next(c for c in native if c['id'] == 'wb.temp').update(min=-100, max=100)
        with self.assertRaises(ValueError): self.actual_check({'values': {'wb.temp': 5900}}, 'pre_tone_wb', native=native)
        self.actual_check({'values': {'wb.temp': -30}}, 'pre_tone_wb', native=native)
        args = {'values': {'wb.temp': 5900}, 'rationale': 'useful ' * 20000,
                'review': {'remaining_gaps': 'x' * 200000}, 'predicted_effect': ['x'] * 5000}
        self.actual_check(args, 'pre_tone_wb')
        # The caller merges operation properties into its unchanged metadata contract.
        _check(args, _object({**props, 'rationale': {}, 'review': {}, 'predicted_effect': {}}, ('values',)), 'edit')
        props['values']['properties'].clear()
        self.assertEqual(set(pre_tone_wb_schema()['values']['properties']), {'wb.temp', 'wb.tint'})

    def test_readback_dependent_constraints_remain_runtime_guards(self):
        cases = [
            {'settings': {'wb': {'temp': 5800, 'mode': 'auto'}}},
            {'settings': {'wb': {'scale': 'kelvin'}}},
            {'settings': {'wb': {'temp': 5800, 'scale': 'legacy'}}},
            {'values': {'curve.splitShadows': 60}},
            {'commands': [{'id': 'mask.select', 'params': {'id': 999}}]},
            {'commands': [{'id': 'develop.curve', 'params': {'points': [[.8, 0], [.2, 1]]}}]},
        ]
        for args in cases:
            self.schema_check(args, 'joint')
            with self.assertRaises(ValueError): self.actual_check(args, 'joint')
        for phase in ('color', 'joint', 'integrated'):
            self.parity({'settings': {'wb': {'temp': 5800, 'mode': 'custom', 'scale': 'kelvin'}}}, phase)
            self.parity({'values': {'wb.temp': 5800}, 'settings': {'wb': {'mode': 'custom'}}}, phase)
            current = copy.deepcopy(self.base); current['wb']['mode'] = 'daylight'
            self.parity({'settings': {'wb': {'temp': 5800, 'mode': 'daylight'}}}, phase, base=current)
            self.parity({'settings': {'wb': {'temp': 5800, 'scale': 'relative'}}}, phase, False)
            self.parity({'settings': {'wb': {'temp': 5800, 'mode': 1}}}, phase, False)
        args = {'values': {'wb.temp': 5800}}
        native = controls(); next(c for c in native if c['id'] == 'wb.temp').update(min=-100, max=100)
        self.schema_check(args, 'color')
        with self.assertRaises(ValueError): self.actual_check(args, 'color', native=native)

    def test_fresh_json_schemas_no_narrative_caps_or_hidden_defaults(self):
        def visit(value):
            if isinstance(value, dict):
                self.assertNotIn('default', value)
                self.assertNotIn('maxLength', value)
                if 'maxItems' in value:
                    self.assertIn((value.get('minItems', 0), value['maxItems']), ((2, 2), (0, 0), (2, 5)))
                for child in value.values(): visit(child)
            elif isinstance(value, list):
                for child in value: visit(child)
        for phase in PHASES:
            props = stage_edit_schema(phase)
            json.dumps(props, allow_nan=False)
            visit(props)
            props['values']['properties'].clear()
            self.assertTrue(stage_edit_schema(phase)['values']['properties'])
        for phase in ('bad', None):
            with self.assertRaises(ValueError): stage_edit_schema(phase)
        args = {'values': {'light.blacks': -10}, 'rationale': 'useful ' * 20000,
                'review': {'remaining_gaps': 'x' * 200000}, 'predicted_effect': ['x'] * 5000}
        self.schema_check({'values': args['values']}, 'tone')
        self.actual_check(args, 'tone')


if __name__ == '__main__':
    unittest.main(verbosity=2)
