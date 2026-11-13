"""Caller-owned authorization window; no admission controller, CLI or dispatch.

Copied from src/dsh_photo_authorize.py before archival. The caller must first
validate its own current admission and obtain explicit user authorization. This
helper only records/restores a supplied policy and never resets cost accounting.
"""
import contextlib
import copy
import datetime
import json
import os
import uuid
import lab_timing_experiment as lab

POLICY_FIELDS = ('schema','enabled','policy_id','approved_build','allowed_scopes','expires_at',
                 'preflight_report','max_requests','max_requests_per_scope','max_body_bytes',
                 'max_text_bytes','max_output_tokens','max_response_bytes','max_images','usage_limits',
                 'resource_profile','transport_timeout_ms')
USAGE_FIELDS = ('single_input','single_output','total_input','total_output','estimated_cny')



def policy_snapshot(policy):
    snapshot={key:copy.deepcopy(policy[key]) for key in POLICY_FIELDS if key in policy}
    if isinstance(snapshot.get('usage_limits'),dict):
        snapshot['usage_limits']={key:snapshot['usage_limits'][key] for key in USAGE_FIELDS if key in snapshot['usage_limits']}
    if snapshot.get('enabled') is not True: raise ValueError('Issued policy must be explicitly enabled')
    return snapshot


def write_new(path, value):
    with path.open('x',encoding='utf-8') as output:
        json.dump(value,output,ensure_ascii=False,indent=2)
        output.write('\n')


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def restore_marker(path, data):
    # Exclusive creation must not replace a new incident, including a marker
    # created between the existence check and restoration.
    try:
        with path.open('xb') as output: output.write(data)
    except FileExistsError: pass


@contextlib.contextmanager
def temporary_authorization(root, round_dir, policy):
    policy=policy_snapshot(policy)
    exp=root/'experiments'; policy_path=exp/'config/lab-paid-dispatch-policy.local.json'
    hold=exp/'lab-timing-transport-hold.json'; circuit=exp/'lab-timing-circuit.json'; lease=exp/'dsh-photo-paid.lease'
    auth=round_dir/'authorization'; auth.mkdir(parents=True,exist_ok=True)
    fd=os.open(lease,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
    original_policy=original_hold=original_circuit=None
    changed=False; window=None
    try:
        os.write(fd,json.dumps({'pid':os.getpid(),'round':str(round_dir)}).encode('utf-8'))
        original_policy=policy_path.read_bytes(); original_hold=hold.read_bytes(); original_circuit=circuit.read_bytes()
        if json.loads(original_policy.decode('utf-8-sig')).get('enabled') is not False: raise ValueError('Existing paid policy is not disabled')
        for name,data in [('policy-before.json',original_policy),('hold-before.json',original_hold),('circuit-before.json',original_circuit)]:
            dest=auth/name
            if dest.exists() and dest.read_bytes()!=data: raise ValueError('Stop state changed since the previous authorization; diagnose first')
            if not dest.exists():
                with dest.open('xb') as output: output.write(data)
        windows=auth/'windows'; windows.mkdir(exist_ok=True)
        window_id=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+uuid.uuid4().hex
        window=windows/window_id; window.mkdir()
        relative=round_dir.resolve().relative_to(root.resolve()).as_posix()
        write_new(window/'policy.json',policy)
        write_new(window/'window.json',{'schema':'dsh-photo-authorization-window/v1','window_id':window_id,
                  'round':relative,'policy_id':policy.get('policy_id'),'build':policy.get('approved_build'),'opened_at':utc_now()})
        changed=True
        lab.write(policy_path,{**policy,'enabled':False})
        circuit.unlink();hold.unlink()
        lab.write(policy_path,policy)
        write_new(window/'issued.json',{'schema':'dsh-photo-authorization-issued/v1','window_id':window_id,'issued_at':utc_now()})
        yield
    finally:
        try:
            if changed:
                # Disable the new policy before restoring defaults; never reset
                # the ledger or overwrite a new hold/circuit created in-window.
                lab.write(policy_path,{**policy,'enabled':False})
                restore_marker(hold,original_hold)
                restore_marker(circuit,original_circuit)
                policy_path.write_bytes(original_policy)
                write_new(window/'closed.json',{'schema':'dsh-photo-authorization-closed/v1','window_id':window.name,
                          'closed_at':utc_now(),'restored':True,'new_hold':hold.read_bytes()!=original_hold,
                          'new_circuit':circuit.read_bytes()!=original_circuit})
        finally:
            os.close(fd); lease.unlink()
