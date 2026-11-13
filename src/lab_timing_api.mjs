// Direct official transport. Admission precedes credentials and paid HTTP.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {assertNotHeld,authorizedBodyCapacity,reserve,boundedResponse,tripCircuit,DispatchDenied} from './lab_timing_dispatch_guard.mjs';
const root=path.resolve(import.meta.dirname,'..');
const balance=process.argv.includes('--balance');
const arg=name=>{const i=process.argv.indexOf(name);return i<0?undefined:process.argv[i+1];};
let sent=false,admission;
try{
  if(!balance)assertNotHeld(root);
  let body;
  if(!balance){
    const capacity=authorizedBodyCapacity(root);let count=0;const chunks=[];
    for await(const chunk of process.stdin){count+=chunk.length;if(count>capacity)throw new DispatchDenied('Inbound request byte capacity exceeded');chunks.push(chunk);}
    body=JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if(body.model!=='deepseek-flash'||body.output_config?.effort!=='high')throw new DispatchDenied('Model/effort mismatch');
    admission=reserve(root,body,{scope:arg('--scope'),build:arg('--build'),requestId:arg('--request-id')});
  }
  const runtime=JSON.parse(fs.readFileSync(path.join(root,'experiments/config/runtime.local.json'),'utf8'));
  const require=createRequire(path.join(runtime.dshRoot,'package.json')),yaml=require('js-yaml');
  const dshHome=process.env.DSH_HOME||path.join(os.homedir(),'.dsh');
  const config=yaml.load(fs.readFileSync(path.join(dshHome,'settings.yaml'),'utf8'));
  const store=yaml.load(fs.readFileSync(path.join(dshHome,'.credentials.yaml'),'utf8'));
  const key=process.env.DEEPSEEK_API_KEY||store.refs?.DEEPSEEK_API_KEY;
  if(typeof key!=='string'||!key)throw Error('Configured API credential unavailable');
  const endpoint=new URL(config['llm-deepseek']?.baseURL||'https://api.deepseek.com/anthropic');
  if(endpoint.protocol!=='https:'||endpoint.hostname!=='api.deepseek.com')throw Error('Official route required');
  if(balance){
    const r=await fetch(new URL('/user/balance',endpoint),{headers:{Authorization:'Bearer '+key},redirect:'error',signal:AbortSignal.timeout(30000)});
    process.stdout.write(await r.text());process.exit(r.ok?0:1);
  }
  endpoint.pathname=endpoint.pathname.replace(/\/$/,'').replace(/\/v1$/,'')+'/v1/messages';
  assertNotHeld(root);sent=true;
  const r=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','x-api-key':key,'anthropic-version':'2023-06-01'},body:JSON.stringify(body),redirect:'error',signal:AbortSignal.timeout(admission.transport_timeout_ms??240000)});
  const raw=await boundedResponse(r,admission.max_response_bytes);
  if(!r.ok){tripCircuit(root,'Provider HTTP '+r.status,admission.request_id);process.stdout.write(JSON.stringify({error:{status:r.status,message:'Provider error; shared circuit opened',request_id:admission.request_id}}));process.exitCode=1;}
  else process.stdout.write(raw);
}catch(e){
  if(sent)tripCircuit(root,'Transport or response failure: '+e.message,admission?.request_id);
  process.stdout.write(JSON.stringify({error:{status:e instanceof DispatchDenied?'local_security_hold':'transport_failure',message:String(e.message).slice(0,800),http_may_have_been_sent:sent,request_id:admission?.request_id}}));
  process.exitCode=1;
}
