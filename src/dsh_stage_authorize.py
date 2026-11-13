"""Read-only stage policy construction; windows require an explicit caller.

The CLI only prints a prospective policy. It never enables dispatch, reads
credentials, starts a runner, or edits request/native/cost accounting.
"""
import argparse
import contextlib
import datetime
import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dsh_stage_validation import (BUILD, PREFLIGHT, ROOT, PHOTO, CONDITIONS,
                                  round_path, validate_stage_frozen)


def _budget_status(root, round_dir, scope):
    for name in ('stage-dispatch-pending.json', 'stage-dispatch.lease'):
        if (round_dir / name).exists():
            raise ValueError('Stage dispatch pending/lease requires reconciliation; never clear or replay')
    adapter = Path(__file__).with_name('dsh_stage_adapter.mjs').resolve().as_uri()
    script = ('import {reconcileStageTask,stageBudgetStatus} from ' + json.dumps(adapter) + ';'
              'import fs from "node:fs";import path from "node:path";'
              'const [root,round,scope]=process.argv.slice(1);'
              'const p=JSON.parse(fs.readFileSync(path.join(round,"protocol.json"),"utf8").replace(/^\\uFEFF/,""));'
              'console.log(JSON.stringify(stageBudgetStatus(p,scope,reconcileStageTask(root,round,p))));')
    result = subprocess.run(['node', '--input-type=module', '-e', script, str(root), str(round_dir), scope],
                            cwd=Path(__file__).resolve().parent.parent, capture_output=True,
                            encoding='utf-8', timeout=30, check=False)
    if result.returncode:
        raise ValueError('Stage task accounting is not reconciled: ' + result.stderr[-2000:])
    status = json.loads(result.stdout)
    if not isinstance(status, dict) or status.get('next_allowed') is not True:
        raise ValueError('Stage task requires operator review before another request')
    return status


def make_policy(root, round_dir, condition='module_support'):
    """Validate frozen evidence and actual reconciled cost; reserve no new cost."""
    root = Path(root).resolve(strict=True)
    round_dir = round_path(round_dir, root, exists=True)
    protocol = validate_stage_frozen(root, round_dir)
    if not isinstance(condition, str) or condition not in CONDITIONS:
        raise ValueError('Stage authorization permits only module_support')
    resources = protocol['resources_snapshot']
    transport, model = resources['transport'], resources['model']
    scope = round_dir.relative_to(root).as_posix() + '/editing/' + PHOTO + '/r1/' + condition
    _budget_status(root, round_dir, scope)
    return {'schema': 'lab-paid-admission/v1', 'enabled': True, 'policy_id': protocol['task_id'],
            'approved_build': BUILD, 'resource_profile': resources['id'], 'allowed_scopes': [scope],
            'expires_at': (datetime.datetime.now(datetime.timezone.utc) +
                           datetime.timedelta(hours=transport['authorization_hours'])).isoformat(),
            'preflight_report': PREFLIGHT, 'max_requests': None, 'max_requests_per_scope': None,
            'max_body_bytes': transport['max_body_bytes'], 'max_text_bytes': transport['max_text_bytes'],
            'max_response_bytes': transport['max_response_bytes'], 'max_images': transport['images'],
            'max_output_tokens': model['requested_output_tokens'],
            'transport_timeout_ms': transport['timeout_ms']}


@contextlib.contextmanager
def stage_authorization(root, round_dir, condition='module_support'):
    """Explicit caller-owned context, restoring disabled policy/holds on exit."""
    root = Path(root).resolve(strict=True)
    round_dir = round_path(round_dir, root, exists=True)
    policy = make_policy(root, round_dir, condition)
    protocol = validate_stage_frozen(root, round_dir)
    if protocol['validation_only']:
        raise ValueError('Validation-only stage round cannot open a real authorization window')
    from photo_authorization_window import temporary_authorization
    with temporary_authorization(root, round_dir, policy):
        yield policy


stage_authorization_context = stage_authorization


def main():
    parser = argparse.ArgumentParser(description='Read-only stage-policy dry run; never enables dispatch')
    parser.add_argument('--root', default=str(ROOT))
    parser.add_argument('--round', required=True)
    parser.add_argument('--condition', choices=CONDITIONS, default='module_support')
    parser.add_argument('--print-policy', action='store_true', required=True)
    args = parser.parse_args()
    print(json.dumps(make_policy(args.root, args.round, args.condition), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
