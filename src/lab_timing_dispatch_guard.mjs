// Host-owned admission. Model text cannot grant or replenish paid authority.
import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto'; // IDs only; no file hashing.

export class DispatchDenied extends Error {}
const read=p=>JSON.parse(fs.readFileSync(p,'utf8').replace(/^\uFEFF/,''));
export function assertNotHeld(root){
  for(const name of ['lab-timing-transport-hold.json','lab-timing-circuit.json']){
    const p=path.join(root,'experiments',name);
    if(fs.existsSync(p))throw new DispatchDenied('Paid dispatch stopped: '+name);
  }
}
export function tripCircuit(root,reason,requestId=null){
  const p=path.join(root,'experiments','lab-timing-circuit.json');
  fs.mkdirSync(path.dirname(p),{recursive:true});
  try{fs.writeFileSync(p,JSON.stringify({schema:'lab-security/v1',time:new Date().toISOString(),request_id:requestId,reason:String(reason).slice(0,2000),resume:'Offline diagnosis and explicit operator authorization required; never auto-clear on top-up.'},null,2),{encoding:'utf8',flag:'wx'});}
  catch(e){if(e.code!=='EEXIST')throw e;}
}
export function requestMass(body){
  let images=0;
  const projected=JSON.parse(JSON.stringify(body,(_k,v)=>{
    if(v&&v.type==='image'){images++;return {type:'image',source:'omitted from text mass'};}
    return v;
  }));
  return {body_bytes:Buffer.byteLength(JSON.stringify(body)),text_bytes:Buffer.byteLength(JSON.stringify(projected)),images};
}
function inside(root,relative){
  if(typeof relative!=='string'||path.isAbsolute(relative))throw new DispatchDenied('Scope must be workspace-relative');
  const resolved=path.resolve(root,relative),r=path.relative(root,resolved);
  if(!r||r.startsWith('..')||path.isAbsolute(r))throw new DispatchDenied('Invalid workspace scope');
  return r.split(path.sep).join('/');
}
function positive(value){return Number.isSafeInteger(value)&&value>0;}
function stageResources(root,p){
  if(p.resource_profile===undefined)return null;
  if(p.resource_profile!=='stage-resource/v13'||p.approved_build!=='dsh-stage-runtime-v13-20261113')throw new DispatchDenied('Unknown stage resource profile/build');
  // Host-owned fixed lookup; policy/model text cannot choose a resource file.
  let r;
  try{r=read(path.join(root,'src/photo_stage_resources.json'));}
  catch{throw new DispatchDenied('Trusted stage resources unavailable');}
  const t=r?.transport,m=r?.model;
  if(r?.schema!=='photo-stage-resources/v1'||r.id!=='stage-resource/v13'||r.build!=='dsh-stage-runtime-v13-20261113'||
      !t||!m||!['max_body_bytes','max_text_bytes','max_response_bytes','authorization_hours'].every(k=>positive(t[k]))||
      t.authorization_hours!==24||t.timeout_ms!==600000||t.images!==2||!positive(m.requested_output_tokens)||
      !positive(m.maximum_output_tokens)||m.requested_output_tokens>m.maximum_output_tokens)throw new DispatchDenied('Invalid trusted stage resource profile');
  for(const k of ['max_body_bytes','max_text_bytes','max_response_bytes']){
    if(!positive(p[k])||p[k]>t[k])throw new DispatchDenied('Policy exceeds trusted stage resources: '+k);
  }
  if(!positive(p.max_output_tokens)||p.max_output_tokens>m.requested_output_tokens||
      !Number.isSafeInteger(p.max_images)||p.max_images<0||p.max_images>t.images||
      p.transport_timeout_ms!==t.timeout_ms)throw new DispatchDenied('Policy exceeds trusted stage interface capacity');
  return r;
}
export function authorizedBodyCapacity(root){
  assertNotHeld(root);
  const f=path.join(root,'experiments/config/lab-paid-dispatch-policy.local.json');
  if(!fs.existsSync(f))throw new DispatchDenied('No paid authorization policy');
  const p=read(f);
  if(p.enabled!==true||!positive(p.max_body_bytes))throw new DispatchDenied('Paid authorization disabled or body capacity missing');
  stageResources(root,p);
  return p.max_body_bytes;
}
export function reserve(root,body,{scope,build,requestId}){
  assertNotHeld(root);
  const file=path.join(root,'experiments/config/lab-paid-dispatch-policy.local.json');
  if(!fs.existsSync(file))throw new DispatchDenied('No paid authorization policy');
  const p=read(file),normalized=inside(root,scope);
  if(p.schema!=='lab-paid-admission/v1'||p.enabled!==true)throw new DispatchDenied('Paid authorization disabled');
  const stage=stageResources(root,p);
  if(typeof requestId!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(requestId))throw new DispatchDenied('Invalid request ID');
  if(typeof p.policy_id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(p.policy_id)||['__proto__','constructor','prototype'].includes(p.policy_id))throw new DispatchDenied('Invalid policy ID');
  if(['__proto__','constructor','prototype'].includes(requestId))throw new DispatchDenied('Invalid request ID');
  if(p.approved_build!==build||!Array.isArray(p.allowed_scopes)||!p.allowed_scopes.includes(normalized))throw new DispatchDenied('Build/scope not authorized');
  if(!Number.isFinite(Date.parse(p.expires_at))||Date.parse(p.expires_at)<=Date.now())throw new DispatchDenied('Authorization expired');
  if(stage&&Date.parse(p.expires_at)>Date.now()+stage.transport.authorization_hours*3600000)throw new DispatchDenied('Stage authorization exceeds approved time window');
  if(!(stage&&p.max_requests===null)&&!positive(p.max_requests))throw new DispatchDenied('Missing explicit resource authorization: max_requests');
  for(const k of ['max_body_bytes','max_text_bytes','max_output_tokens','max_response_bytes']){
    if(!positive(p[k]))throw new DispatchDenied('Missing explicit resource authorization: '+k);
  }
  if(stage&&p.max_requests_per_scope!=null&&!positive(p.max_requests_per_scope))throw new DispatchDenied('Invalid per-scope request limit');
  if(!Number.isSafeInteger(p.max_images)||p.max_images<0)throw new DispatchDenied('Invalid image capacity');
  const proof=read(path.join(root,inside(root,p.preflight_report)));
  if(proof.passed!==true||proof.build!==build||proof.paid_model_calls!==0)throw new DispatchDenied('Offline preflight not passed for this build');
  const mass=requestMass(body);
  if(mass.body_bytes>p.max_body_bytes||mass.text_bytes>p.max_text_bytes||mass.images>p.max_images||!positive(body.max_tokens)||body.max_tokens>p.max_output_tokens)throw new DispatchDenied('Request exceeds authorized interface capacity');
  const statePath=path.join(root,'experiments/lab-paid-admission-state.json'),lock=statePath+'.lock';
  let fd;const deadline=Date.now()+1000;
  while(fd===undefined){
    try{fd=fs.openSync(lock,'wx');}
    catch(e){if(e.code!=='EEXIST'||Date.now()>=deadline)throw new DispatchDenied('Admission ledger unavailable; no HTTP sent');Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,10);}
  }
  try{
    assertNotHeld(root);
    const state=fs.existsSync(statePath)?read(statePath):{schema:'lab-paid-ledger/v1',policies:{}};
    if(state.schema!=='lab-paid-ledger/v1'||!state.policies||Array.isArray(state.policies)||typeof state.policies!=='object')throw new DispatchDenied('Corrupt admission ledger; manual reconciliation required');
    const pool=Object.hasOwn(state.policies,p.policy_id)?state.policies[p.policy_id]:{issued:0,claims:{}};
    if(!Number.isSafeInteger(pool.issued)||pool.issued<0||!pool.claims||Array.isArray(pool.claims)||typeof pool.claims!=='object'||Object.keys(pool.claims).length!==pool.issued)throw new DispatchDenied('Invalid reservation accounting; do not reset automatically');
    if(Object.hasOwn(pool.claims,requestId))throw new DispatchDenied('Request already reserved; reconcile outcome instead of replay');
    if(p.max_requests!==null&&pool.issued>=p.max_requests)throw new DispatchDenied('Operator-authorized request pool exhausted; not a photography failure');
    if(p.max_requests_per_scope!==undefined&&!(stage&&p.max_requests_per_scope===null)){
      if(!positive(p.max_requests_per_scope))throw new DispatchDenied('Invalid per-scope request limit');
      if(Object.values(pool.claims).filter(c=>c.scope===normalized).length>=p.max_requests_per_scope)throw new DispatchDenied('Photo request pool exhausted; not a photography failure');
    }
    if(stage&&!Number.isSafeInteger(pool.issued+1))throw new DispatchDenied('Admission accounting exceeds safe integer range');
    pool.issued++;pool.claims[requestId]={time:new Date().toISOString(),scope:normalized,build,mass,max_output_tokens:body.max_tokens};
    state.policies[p.policy_id]=pool;
    const tmp=statePath+'.tmp-'+randomUUID();
    fs.writeFileSync(tmp,JSON.stringify(state),'utf8');fs.renameSync(tmp,statePath);
    return {request_id:requestId,policy_id:p.policy_id,max_response_bytes:p.max_response_bytes,mass,
      ...(stage?{transport_timeout_ms:stage.transport.timeout_ms}:{})};
  }finally{fs.closeSync(fd);fs.unlinkSync(lock);}
}

export async function boundedResponse(response,maxBytes){
  const reader=response.body.getReader();let length=0;const chunks=[];
  try{
    while(true){const {done,value}=await reader.read();if(done)break;length+=value.length;if(length>maxBytes)throw new DispatchDenied('Response byte capacity exceeded; billing outcome requires reconciliation');chunks.push(value);}
  }catch(e){await reader.cancel().catch(()=>{});throw e;}
  return Buffer.concat(chunks).toString('utf8');
}
