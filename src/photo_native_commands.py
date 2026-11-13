"""Native envelope/readback helpers; no capability controller or execution CLI.

Copied from src/photo_command_atlas.py before archival. Calls are issued only
through an explicitly supplied backend; importing this module performs no IO.
"""
import math
from photo_tools import structured, strict_json

def finite(value):
    if isinstance(value, dict):
        for child in value.values(): finite(child)
    elif isinstance(value, (tuple, list)):
        for child in value: finite(child)
    elif type(value) in (int, float) and not math.isfinite(value):
        raise ValueError('Nonfinite command atlas state')


def command(ident, **params):
    return {'id': ident, 'params': params}


def decode_result(raw):
    # Null is a legitimate native result (develop.curve, pointColor.delete, mask.select).
    # The generic historical decoder discards text null; do not mistake it for error.
    if not isinstance(raw, dict) or raw.get('isError'): raise ValueError('Native command returned error/malformed envelope')
    if 'structuredContent' in raw: value = raw['structuredContent']
    else:
        texts = [r['text'] for r in raw.get('content', []) if r.get('type') == 'text']
        # Source-reviewed MCP ToolResult::json serializes Value::Null as plain
        # text "ok" (not JSON null), with isError=false and no structuredContent.
        if len(texts) == 1 and texts[0].strip() in ('null', 'ok'): return None
        return structured(raw)
    value = strict_json(value) if isinstance(value, str) else value
    finite(value); return value


def state(backend):
    settings = backend.settings(); finite(settings)
    control = decode_result(backend.call('command_run', command('library.state')))
    if not isinstance(control, dict) or 'activeMask' not in control: raise ValueError('Missing actual selection readback')
    current = {**settings, 'activeMask': control['activeMask']}
    if current['activeMask'] is not None and not any(m['id'] == current['activeMask'] for m in current['masks']):
        raise ValueError('Selection refers to an unobserved mask')
    return settings, control, current


def dependencies(backend):
    rows = backend.call('command_list', {})
    rows = rows.get('commands') if isinstance(rows, dict) else rows
    if not isinstance(rows, list): raise ValueError('Missing native command_list')
    deps = {'commands': rows}
    if any(r['id'] == 'segment.model.status' and r.get('enabled') is True for r in rows):
        deps['segment.model.status'] = decode_result(backend.call('command_run', command('segment.model.status')))
    return deps
