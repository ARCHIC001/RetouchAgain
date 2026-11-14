// New resource-aware DSH path. Legacy module scopes and ledgers are never resumed.
import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {spawn,spawnSync} from 'node:child_process';
import {buildBody,moduleCost,loadModuleImageAccounting,moduleEffectiveInput,compactModuleSchema} from './photo_request_primitives.mjs';
import {STAGE_TOOL,STAGE_TOOL_DESCRIPTION,stageContractRevision,stageRecoverableRejection} from './photo_stage_bridge.mjs';
import {assertNotHeld,tripCircuit,requestMass} from './lab_timing_dispatch_guard.mjs';
import {BudgetPause} from './photo_runtime_errors.mjs';

export const BUILD='dsh-stage-runtime-v20-20261114';
export const PROTOCOL='dsh-stage-runtime/v2';
export const PREFLIGHT='experiments/validation/latest/dsh-stage-preflight.json';
const read=file=>JSON.parse(fs.readFileSync(file,'utf8').replace(/^\uFEFF/,''));
const object=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
const equal=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
function canonical(x){return Array.isArray(x)?x.map(canonical):object(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,canonical(x[k])])):x;}
function atomic(file,value,exclusive=false){fs.mkdirSync(path.dirname(file),{recursive:true});const data=JSON.stringify(value,null,2);if(exclusive)return fs.writeFileSync(file,data,{encoding:'utf8',flag:'wx'});const t=file+'.tmp-'+randomUUID();fs.writeFileSync(t,data,'utf8');fs.renameSync(t,file);}
export function stageResources(){const value=read(path.join(import.meta.dirname,'photo_stage_resources.json'));if(value.schema!=='photo-stage-resources/v1'||value.id!=='stage-resource/v20'||value.build!==BUILD)throw Error('Trusted stage resources unavailable');return value;}
export function validateStageRound(root,round){
  root=fs.realpathSync(root);round=fs.realpathSync(round);
  const python=read(path.join(root,'experiments/config/runtime.local.json')).python||(process.env.VISHARNESS_PYTHON||'python');
  const script='import sys,json;sys.path.insert(0,sys.argv[1]);from dsh_stage_validation import validate_stage_frozen;print(json.dumps(validate_stage_frozen(sys.argv[2],sys.argv[3]),ensure_ascii=False))';
  const checked=spawnSync(python,['-I','-B','-X','utf8','-c',script,path.join(import.meta.dirname),root,round],{encoding:'utf8',windowsHide:true});
  if(checked.error||checked.status!==0)throw Error('Stage frozen qualification failed: '+String(checked.stderr||checked.error));
  const protocol=JSON.parse(checked.stdout);return {root,round,protocol};
}
function validateProviderUsage(receipt,resources){
  moduleCost(receipt.usage,receipt.service_tier);const u=receipt.usage,input=u.input_tokens+u.cache_creation_input_tokens+u.cache_read_input_tokens;
  if(input>resources.model.context_tokens||u.output_tokens>resources.model.maximum_output_tokens||input+u.output_tokens>resources.model.context_tokens)throw Error('Provider-impossible actual token usage; preserve receipt for reconciliation');
}
function checkProtocol(protocol){if(protocol.schema!==PROTOCOL||protocol.build!==BUILD||protocol.resource_profile_id!=='stage-resource/v20'||!equal(protocol.resources_snapshot,stageResources())||protocol.prior_task_rounds?.length)throw Error('Independent stage resource protocol required');}
export function stageResponseCalls(receipt){
  const calls=receipt.public_content?.filter(b=>b.type==='tool_use');
  if(!Array.isArray(calls)||calls.length)return calls;
  const blocks=receipt.public_content.filter(b=>b.type==='text'&&b.text?.trim());
  if(blocks.length!==1)return calls;
  let text=blocks[0].text.trim();
  const fenced=text.match(/^```(?:json)?\s*\n([\s\S]*?)\n```$/i);
  if(fenced)text=fenced[1];
  try{
    const input=JSON.parse(text);
    if(!object(input)||!object(input.action)||typeof input.contract_revision!=='string'||!object(input.review))return calls;
    assertFinitePublic(input);
    return [{type:'tool_use',id:'text-call:'+receipt.request_id,name:STAGE_TOOL,input,
      source:'exact-public-json-text'}];
  }catch{return calls;}
}
export function validateStageActions(receipt){
  const calls=stageResponseCalls(receipt);if(!Array.isArray(calls)||!Array.isArray(receipt.actions)||receipt.actions.length>calls.length||calls.length>1||calls.some(c=>c.name!==STAGE_TOOL))throw Error('Invalid stage receipt actions');let terminal=null;
  for(const [i,a]of receipt.actions.entries()){const c=calls[i];if(a.name!==c.name||a.call_id!==c.id)throw Error('Stage action/proposal identity mismatch');
    if(a.disposition==='skipped_after_terminal'){if(!terminal||a.status!=='not_executed'||a.observation_id!==null||a.terminal_call_id!==terminal.call_id||a.terminal_status!==terminal.status||a.terminal_observation_id!==terminal.observation_id)throw Error('Stage skip without matching terminal action');}
    else {if(terminal||!['working','complete','diagnosed_unfinished','paused_nonprogress','paused_budget'].includes(a.status)||!/^o\d+$/.test(a.observation_id??''))throw Error('Invalid stage executed action');
      if(a.disposition==='rejected_before_execution'){
        if(a.recoverable===true){
          if(!stageRecoverableRejection({status:a.status,stage_turn_result:a.stage_turn_result,context:{stage_turn_result:a.stage_turn_result,tool_result:a.stage_turn_result,proposal_bank:{revision:a.accepted_bank_revision}}}))throw Error('Missing stage rejection proof');
          if(a.stage_turn_result.code==='response_schema_invalid'||a.proof_origin_adapter!==undefined){
            if(a.proof_origin_adapter!==true||a.proof!=='adapter-before-sdk-no-native-call'||a.stage_turn_result.code!=='response_schema_invalid'||a.rejection_ref!=='response-rejection:'+receipt.request_id||a.stage_turn_result.contract_revision!==receipt.contract_revision)throw Error('Invalid adapter response rejection proof');
          }
          if(a.status!=='working'||a.execution_status!=='not_executed'||a.progress!==false||a.rejection_ref!==a.stage_turn_result?.rejection_ref||!equal(a.no_effect,a.stage_turn_result?.no_effect)||a.accepted_bank_revision!==a.stage_turn_result?.accepted_bank_revision)throw Error('Invalid stage recoverable rejection receipt');
        }else if(a.status!=='diagnosed_unfinished'||typeof a.diagnosis!=='string'||!a.diagnosis)throw Error('Invalid stage rejection diagnosis');
      }else if(a.disposition!=='executed')throw Error('Invalid stage action disposition');if(a.status!=='working')terminal=a;}
  }return calls.length>0&&receipt.actions.length===calls.length;
}
export function reconcileStageTask(root,round,protocol){
  checkProtocol(protocol);const ledgerPath=path.join(root,'experiments/lab-paid-admission-state.json');
  const ledger=fs.existsSync(ledgerPath)?read(ledgerPath):{policies:{}};const pool=ledger.policies?.[protocol.task_id]??{issued:0,claims:{}};
  const ids=Object.keys(pool.claims??{});if(!Number.isSafeInteger(pool.issued)||pool.issued!==ids.length)throw Error('Invalid stage reservation ledger');
  const receipts=[];
  for(const id of ids){
    if(!/^[A-Za-z0-9_-]+$/.test(id))throw Error('Unsafe stage request ID');
    const claim=pool.claims[id],rp=path.join(round,'receipts',id+'.json'),qp=path.join(round,'requests',id+'.json');
    if(!fs.existsSync(rp)||!fs.existsSync(qp))throw Error('Stage reserved/request outcome unknown; no replay');
    const receipt=read(rp),request=read(qp);
    if(receipt.request_id!==id||receipt.task_id!==protocol.task_id||receipt.build!==BUILD||receipt.scope!==claim.scope||claim.build!==BUILD||receipt.reserved_output_tokens!==claim.max_output_tokens||!equal(request,Object.fromEntries(Object.keys(request).map(k=>[k,receipt[k]]))))throw Error('Stage reservation/request/receipt identity mismatch');
    validateProviderUsage(receipt,protocol.resources_snapshot);validateStageActions(receipt);receipts.push(receipt);
  }
  for(const dir of ['requests','receipts'])for(const name of fs.existsSync(path.join(round,dir))?fs.readdirSync(path.join(round,dir)):[])if(name.endsWith('.json')&&!ids.includes(name.slice(0,-5)))throw Error('Stage unreserved request/receipt outcome unknown');
  receipts.sort((a,b)=>a.sequence-b.sequence);const scopes={};for(const [i,row]of receipts.entries()){scopes[row.scope]=(scopes[row.scope]??0)+1;if(row.sequence!==i+1||row.scope_sequence!==scopes[row.scope])throw Error('Stage request sequence mismatch');}
  return {receipts,sources:[{round,protocol}],current_requests:receipts.length,summary:{requests:receipts.length,estimated_peak_cny:receipts.reduce((n,r)=>n+moduleCost(r.usage,r.service_tier),0)}};
}
export function stageBudgetStatus(protocol,scope,result){
  checkProtocol(protocol);const rows=result.receipts,config=protocol.resources_snapshot.cost_monitor;
  let spent=0,totalInput=0,totalOutput=0;for(const r of rows){validateProviderUsage(r,protocol.resources_snapshot);spent+=moduleCost(r.usage,r.service_tier);totalInput+=r.usage.input_tokens+r.usage.cache_creation_input_tokens+r.usage.cache_read_input_tokens;totalOutput+=r.usage.output_tokens;}
  const recent=rows.slice(-config.no_progress_window),recentCost=recent.reduce((n,r)=>n+moduleCost(r.usage,r.service_tier),0);
  const progress=r=>(r.actions??[]).some(a=>a.progress===true||a.status==='complete'&&a.disposition==='executed');
  const noProgress=recent.length===config.no_progress_window&&recentCost>=config.no_progress_cost&&!recent.some(progress);
  const single=rows.length?moduleCost(rows.at(-1).usage,rows.at(-1).service_tier):0;
  const reasons=[];const unsettled=rows.filter(r=>Array.isArray(r.public_content)&&!validateStageActions(r)).map(r=>r.request_id);if(unsettled.length)reasons.push('actual provider actions are not reconciled');if(spent>config.clearly_excessive_at)reasons.push('actual spending clearly exceeds the broad single-photo envelope');if(noProgress)reasons.push('sustained actual spending without recorded task progress');if(single>config.single_request_anomaly)reasons.push('abnormal actual single-request spend');
  return {used:rows.filter(r=>r.scope===scope).length,task_requests:rows.length,revision_requests:rows.length,prior_requests:0,task_root_id:protocol.task_id,
    planning_requests:protocol.resources_snapshot.planning.requests,planning_only:true,hard_remaining:null,task_remaining:null,closing_only:false,
    estimated_peak_cny:spent,total_input_tokens:totalInput,total_output_tokens:totalOutput,
    cost_band:spent<config.notice_at?'normal':spent<=config.review_reference?'wide_margin':'review_reference_exceeded',
    warning:spent>=config.notice_at,next_allowed:reasons.length===0,intervention_reasons:reasons,
    maximum_reserve_controls_next_request:false,pricing:config.pricing_claim};
}
export function buildStageBody(options,registry){
  const resources=stageResources(),body=buildBody(options,registry,false,{outputTokens:resources.model.requested_output_tokens,toolNames:[STAGE_TOOL]});
  body.tools=body.tools.map(t=>({...t,description:STAGE_TOOL_DESCRIPTION,input_schema:compactModuleSchema(t.input_schema)}));return body;
}
export async function stageInputDiagnostic(body,options,accounting){
  const resources=stageResources(),refs=options.messages.flatMap(m=>m.content).filter(b=>b.type==='image').map(b=>b.attachment);
  if(refs.length!==2||refs.some(r=>!Number.isSafeInteger(r.width)||!Number.isSafeInteger(r.height)||r.width<1||r.height<1||Math.max(r.width,r.height)>resources.transport.overview_long_edge))throw Error('Stage requires two registered in-range actual images');
  let content=0;const projected=JSON.parse(JSON.stringify(body,(_k,v)=>{if(v?.type==='image')return {type:'image'};if(v?.type==='text'&&typeof v.text==='string'){content+=Buffer.byteLength(v.text);return {...v,text:''};}return v;}));
  if(typeof projected.system==='string'){content+=Buffer.byteLength(projected.system);projected.system='';}
  const metadata=Buffer.byteLength(JSON.stringify(projected)),imageTokens=refs.reduce((n,r)=>n+accounting.imageTokens(r.width,r.height),0),byteAllowance=content+metadata+imageTokens+2048;
  return {method:'stage-context-diagnostic/v1',text_json_utf8_bytes:content+metadata,image_tokens:imageTokens,legacy_byte_allowance:byteAllowance,
    legacy_16000_would_block:byteAllowance>16000,byte_allowance_is_admission_gate:false,supplier_certified_bound:false,
    provider_context_tokens:resources.model.context_tokens,normal_context_tokens:resources.projection.normal_context_tokens,context_guidance_tokens:resources.projection.context_guidance_tokens,
    guidance_is_hard_limit:false,allowed:true,image_accounting_source:accounting.source};
}
function assertFinitePublic(value){
  if(typeof value==='number'&&!Number.isFinite(value))throw Error('Nonfinite operation value');
  if(Array.isArray(value))for(const item of value)assertFinitePublic(item);
  else if(object(value))for(const [key,item]of Object.entries(value)){if(['__proto__','prototype','constructor','thinking','reasoning_content','hidden_cot','analysis','private_content'].includes(key.toLowerCase()))throw Error('Unsafe/private public field');assertFinitePublic(item);}
}
class StageSchemaInvalid extends Error{constructor(message,fieldPath){super(message);this.field_path=fieldPath;}}
function invalidValue(name,reason){throw new StageSchemaInvalid(name+': '+reason,name);}
function checkValue(value,schema,name,root=schema){
  if(schema===true)return;if(schema===false)invalidValue(name,'forbidden schema');
  if(!object(schema))throw Error(name+': invalid schema');
  if(schema.$ref){if(!schema.$ref.startsWith('#/'))throw Error(name+': nonlocal schema reference');const target=schema.$ref.slice(2).split('/').map(k=>k.replaceAll('~1','/').replaceAll('~0','~')).reduce((v,k)=>v?.[k],root);if(target===undefined)throw Error(name+': missing schema reference');checkValue(value,target,name,root);}
  if(schema.allOf)for(const part of schema.allOf)checkValue(value,part,name,root);
  for(const k of ['oneOf','anyOf'])if(schema[k]){
    let pass=0;const errors=[];
    for(const branch of schema[k]){try{checkValue(value,branch,name,root);pass++;}catch(e){if(e instanceof StageSchemaInvalid)errors.push(e);else throw e;}}
    if(pass<1){
      const relevant=errors.filter(e=>!['.action.name','.name','.op','.decision'].some(s=>e.field_path.endsWith(s)));
      if(relevant.length)throw relevant.sort((a,b)=>b.field_path.length-a.field_path.length)[0];
      invalidValue(name,'schema branch mismatch');
    }
    if(k==='oneOf'&&pass!==1)invalidValue(name,'ambiguous schema branches');
  }
  const type=schema.type;if(type){const types=Array.isArray(type)?type:[type];if(!types.some(t=>({object:object(value),array:Array.isArray(value),string:typeof value==='string',boolean:typeof value==='boolean',integer:Number.isSafeInteger(value),number:typeof value==='number'&&Number.isFinite(value),null:value===null})[t]))invalidValue(name,'invalid type');}
  if(schema.enum&&!schema.enum.includes(value)||Object.hasOwn(schema,'const')&&!equal(schema.const,value))invalidValue(name,'enum/constant mismatch');
  if(object(value)){if(Object.keys(value).length<(schema.minProperties??0))invalidValue(name,'empty operation object');const missing=(schema.required??[]).find(k=>!Object.hasOwn(value,k));if(missing!==undefined)invalidValue(name+'.'+missing,'missing required field');for(const [k,v]of Object.entries(value)){if(schema.properties?.[k])checkValue(v,schema.properties[k],name+'.'+k,root);else if(schema.additionalProperties===false)invalidValue(name+'.'+k,'unknown field '+k);else if(object(schema.additionalProperties))checkValue(v,schema.additionalProperties,name+'.'+k,root);}}
  if(Array.isArray(value)){if(value.length<(schema.minItems??0)||value.length>(schema.maxItems??Infinity))invalidValue(name,'array cardinality');if(schema.items)value.forEach((v,i)=>checkValue(v,schema.items,name+'['+i+']',root));}
  if(typeof value==='string'&&(value.trim().length<(schema.minLength??0)||value.length>(schema.maxLength??Infinity)))invalidValue(name,'string contract');
  if(typeof value==='number'&&(value<(schema.minimum??-Infinity)||value>(schema.maximum??Infinity)||schema.exclusiveMinimum!==undefined&&value<=schema.exclusiveMinimum||schema.exclusiveMaximum!==undefined&&value>=schema.exclusiveMaximum))invalidValue(name,'native range');
}
function stageContextBlock(options){
  const contexts=options.messages.filter(m=>m.role==='user').flatMap(m=>m.content).filter(b=>b.type==='text').map(block=>{try{return {block,value:JSON.parse(block.text)};}catch{return null;}}).filter(v=>v&&object(v.value)&&typeof v.value.current==='string'&&object(v.value.current_observation));
  if(contexts.length!==1)throw Error('Stage requires one authoritative current context');return contexts[0];
}
function stageContext(options){return stageContextBlock(options).value;}
export function normalizeStageSubmission(input,python){
  assertFinitePublic(input);
  const checked=spawnSync(python,['-I','-B','-X','utf8',path.join(import.meta.dirname,'photo_stage_submission.py')],
    {input:JSON.stringify(input),encoding:'utf8',windowsHide:true,maxBuffer:16*1024*1024});
  if(checked.error||checked.status!==0)throw Error('Stage normalization unavailable: '+String(checked.stderr||checked.error));
  const result=JSON.parse(checked.stdout);
  if(!object(result.canonical)||!Array.isArray(result.warnings))throw Error('Invalid stage normalization result');
  assertFinitePublic(result.canonical);return result;
}
function executionCall(receipt,index){
  const raw=stageResponseCalls(receipt)[index];
  const normalized=receipt.normalized_calls?.find(x=>x.id===raw?.id&&x.name===raw?.name);
  return normalized?{...raw,input:normalized.input}:raw;
}
function responseFeedbackOptions(options,result){
  const {block,value}=stageContextBlock(options),text=JSON.stringify({...value,tool_result:result,response_feedback:result});
  return {...options,messages:options.messages.map(m=>({...m,content:m.content.map(b=>b===block?{...b,text}:b)}))};
}
export function validateStageCalls(calls,tools,options){
  if(!Array.isArray(calls)||calls.length!==1||calls.some(c=>typeof c.id!=='string'||!c.id.trim()||!object(c.input)))throw Error('Stage requires one nonempty actual photo_stage_turn call ID');
  if(!Array.isArray(tools)||tools.length!==1||tools[0].name!==STAGE_TOOL||calls[0].name!==STAGE_TOOL)throw Error('Unknown stage tool; only photo_stage_turn is callable');
  const c=calls[0];assertFinitePublic(c.input);const revision=stageContractRevision(stageContext(options));checkValue(c.input,tools[0].input_schema,c.name);
  if(c.input.contract_revision!==revision)invalidValue(c.name+'.contract_revision','Stage call contract_revision differs from supplied context');
  const action=c.input.action;
  if(!object(c.input.review)||!object(action)||!['photo_edit','pre_tone_wb','photo_observe','photo_history','photo_discover','photo_finish','photo_unable','advance'].includes(action.name)||!object(action.arguments))invalidValue(c.name,'Stage turn requires public review and one explicit nested action');
}
function send(root,body,scope,id,signal,limit){return new Promise((resolve,reject)=>{
  const child=spawn(process.execPath,[path.join(root,'src/lab_timing_api.mjs'),'--scope',scope,'--build',BUILD,'--request-id',id],{cwd:root,stdio:['pipe','pipe','pipe'],windowsHide:true});let size=0,chunks=[],done=false;
  const finish=(e,v)=>{if(done)return;done=true;clearTimeout(timer);signal?.removeEventListener('abort',abort);e?reject(e):resolve(v);};
  const abort=()=>{child.kill();finish(Error('Stage transport cancelled; outcome unknown'));};const timer=setTimeout(()=>{child.kill();finish(Error('Stage transport deadline; outcome unknown'));},stageResources().transport.timeout_ms+15000);
  signal?.addEventListener('abort',abort,{once:true});child.stdout.on('data',b=>{size+=b.length;if(size>limit){child.kill();finish(Error('Stage response capacity; outcome unknown'));}else chunks.push(b);});child.stderr.resume();child.on('error',e=>finish(e));child.stdin.on('error',e=>finish(e));child.on('close',code=>{try{finish(null,{response:JSON.parse(Buffer.concat(chunks).toString('utf8')),wireBytes:size,code});}catch{finish(Error('Stage response unavailable; outcome unknown'));}});if(signal?.aborted){abort();return;}child.stdin.end(JSON.stringify(body));
});}
export function stageAdapter(dsh,{root,round,folder,scope,registry,context={}}){
  const admitted=validateStageRound(root,round);({root,round}=admitted);const protocol=admitted.protocol,resources=stageResources();
  if(scope!==path.relative(root,path.join(round,'editing/DSC_3734/r1/module_support')).split(path.sep).join('/')||path.resolve(folder)!==path.resolve(root,scope))throw Error('Stage exact scope mismatch');
  if(context.transport&&(!protocol.validation_only||context.offlineFixture!==true)||protocol.validation_only&&!context.transport)throw Error('Stage fake transport requires explicit offline qualification');
  const transport=context.transport??send,pending=path.join(round,'stage-dispatch-pending.json'),lease=path.join(round,'stage-dispatch.lease');
  function policy(){const p=read(path.join(root,'experiments/config/lab-paid-dispatch-policy.local.json'));if(p.enabled!==true||p.policy_id!==protocol.task_id||p.approved_build!==BUILD||p.resource_profile!==resources.id||!equal(p.allowed_scopes,[scope])||p.max_requests!==null||p.max_requests_per_scope!==null||p.max_output_tokens!==resources.model.requested_output_tokens||p.max_images!==2||p.preflight_report!==PREFLIGHT||!Number.isFinite(Date.parse(p.expires_at))||Date.parse(p.expires_at)<=Date.now())throw Error('Stage exact resource authorization missing');return p;}
  return new class extends dsh.llm.LlmAdapter{
    constructor(){super();this.lastRequest=null;this.problem=null;this.batchTerminal=null;this.skippedCalls=new Map();this.tools=null;this.requestTools=new Map();}
    async resolveModel(provider,id){if(provider!=='photo-local'||id!==resources.model.id)throw Error('Unknown stage provider route');return {provider,id,name:id,reasoning:{efforts:[{id:'high',name:'High'}],defaultEffort:'high'}};}
    statusInfo(){policy();return stageBudgetStatus(protocol,scope,reconcileStageTask(root,round,protocol));}
    validateExecution(name,args,callId){assertFinitePublic(args);if(this.problem||!this.lastRequest||this.batchTerminal)throw Error('Stage execution unavailable');assertNotHeld(root);policy();const r=read(path.join(round,'receipts',this.lastRequest+'.json')),p=read(pending),c=executionCall(r,r.actions.length),tool=this.requestTools.get(this.lastRequest)?.find(t=>t.name===name);if(p.request_id!==this.lastRequest||!c||c.id!==callId||c.name!==name||name!==STAGE_TOOL||!equal(c.input,args)||!tool)throw Error('Stage execution differs from actual provider proposal');checkValue(args,tool.input_schema,name);if(args.contract_revision!==r.contract_revision)throw Error('Stage execution contract_revision mismatch');}
    skippedAfterTerminal(name,args,id){const c=this.skippedCalls.get(id);if(!this.batchTerminal||!c||c.name!==name||!equal(c.input,args))throw Error('Unknown stage skipped proposal');return {...this.batchTerminal,disposition:'skipped_after_terminal',execution_status:'not_executed'};}
    markAction(name,status,observationId,callId,progress=false){
      const file=path.join(round,'receipts',this.lastRequest+'.json'),r=read(file),calls=stageResponseCalls(r),c=calls[r.actions.length];if(!c||c.id!==callId||c.name!==name||!/^o\d+$/.test(observationId??''))throw Error('Stage action receipt mismatch');
      r.actions.push({name,call_id:callId,status,observation_id:observationId,disposition:'executed',progress});
      if(status!=='working'){this.batchTerminal={status,observation_id:observationId,terminal_call_id:callId};const tail=calls.slice(r.actions.length);this.skippedCalls=new Map(tail.map(c=>[c.id,c]));for(const later of tail)r.actions.push({name:later.name,call_id:later.id,status:'not_executed',observation_id:null,disposition:'skipped_after_terminal',terminal_call_id:callId,terminal_status:status,terminal_observation_id:observationId});}
      const complete=validateStageActions(r);atomic(file,r);if(complete)fs.unlinkSync(pending);
      atomic(path.join(round,'stage-usage-live.json'),stageBudgetStatus(protocol,scope,reconcileStageTask(root,round,protocol)));
    }
    markRecoverableRejected(name,args,callId,snapshot,requestId=this.lastRequest){
      if(requestId!==this.lastRequest)throw Error('Stage rejection request identity mismatch');
      this.validateExecution(name,args,callId);const result=stageRecoverableRejection(snapshot);
      if(!result||snapshot.observation_id!==snapshot.context.current_observation?.id||!/^o\d+$/.test(snapshot.observation_id??''))throw Error('Stage rejection snapshot identity mismatch');
      const file=path.join(round,'receipts',requestId+'.json'),r=read(file),p=read(pending),before=read(path.join(round,r.effective_input_ref)).effective_context;
      if(p.status!=='awaiting_actions'||!equal(p.call_ids,[callId])||snapshot.observation_id!==before.current_observation?.id||snapshot.context.current!==before.current)throw Error('Stage rejection pending proposal or unchanged-state mismatch');
      r.actions.push({name,call_id:callId,status:'working',observation_id:snapshot.observation_id,
        disposition:'rejected_before_execution',recoverable:true,execution_status:'not_executed',progress:false,
        rejection_ref:result.rejection_ref,accepted_bank_revision:result.accepted_bank_revision,
        no_effect:structuredClone(result.no_effect),stage_turn_result:structuredClone(result)});
      const complete=validateStageActions(r);if(!complete)throw Error('Stage rejection did not settle every actual call');
      atomic(file,r);fs.unlinkSync(pending);
      atomic(path.join(round,'stage-usage-live.json'),stageBudgetStatus(protocol,scope,reconcileStageTask(root,round,protocol)));
    }
    settleSchemaRejected(name,args,callId,requestId=this.lastRequest){
      if(this.problem||this.batchTerminal||requestId!==this.lastRequest)throw Error('Stage response rejection request identity mismatch');
      const file=path.join(round,'receipts',requestId+'.json'),r=read(file),p=read(pending),rawCalls=stageResponseCalls(r),calls=rawCalls.map((_,i)=>executionCall(r,i)),c=calls[0];
      if(calls.length!==1||r.actions.length!==0||!c||name!==STAGE_TOOL||c.name!==name||c.id!==callId||!equal(c.input,args)||p.request_id!==requestId||!['dispatching','awaiting_actions'].includes(p.status)||p.status==='awaiting_actions'&&!equal(p.call_ids,[callId]))throw Error('Stage response rejection proposal identity mismatch');
      const before=read(path.join(round,r.effective_input_ref)).effective_context,tools=this.requestTools.get(requestId);let error;
      try{validateStageCalls(calls,tools,{messages:[{role:'user',content:[{type:'text',text:JSON.stringify(before)}]}]});}catch(e){if(!(e instanceof StageSchemaInvalid))throw e;error=e;}
      if(!error||!/^o\d+$/.test(before.current_observation?.id??'')||r.contract_revision!==stageContractRevision(before))throw Error('Stage response rejection requires original schema-invalid context');
      const result={kind:'rejected_before_execution',recoverable:true,execution_status:'not_executed',code:'response_schema_invalid',message:error.message,field_path:error.field_path,
        contract_revision:r.contract_revision,no_effect:{passed:true,native_execution_started:false,pending:null,versions_unchanged:true,ledger_unchanged:true,photography_unchanged:true},
        accepted_bank_revision:before.proposal_bank?.revision,rejection_ref:'response-rejection:'+requestId};
      r.actions.push({name,call_id:callId,status:'working',observation_id:before.current_observation.id,disposition:'rejected_before_execution',recoverable:true,execution_status:'not_executed',progress:false,
        proof_origin_adapter:true,proof:'adapter-before-sdk-no-native-call',rejection_ref:result.rejection_ref,accepted_bank_revision:result.accepted_bank_revision,no_effect:structuredClone(result.no_effect),stage_turn_result:result});
      if(!validateStageActions(r))throw Error('Stage response rejection did not settle actual call');
      atomic(file,r);fs.unlinkSync(pending);return result;
    }
    markRejected(name,note){const file=path.join(round,'receipts',this.lastRequest+'.json'),r=read(file),c=stageResponseCalls(r)[r.actions.length];if(c?.name!==name)throw Error('Stage rejected-action identity mismatch');r.rejected_action={name,call_id:c.id,status:'unknown_tool_failure',execution_status:'unknown',note};atomic(file,r);this.problem=Error('Stage tool failed; reconcile without replay');tripCircuit(root,this.problem.message,this.lastRequest);}
    async *stream(options){let fd,id,sent=false,requestOptions=options;const usage={inputTokens:0,outputTokens:0,cacheReadTokens:0,cacheWriteTokens:0};try{
      if(this.problem)throw this.problem;fd=fs.openSync(lease,'wx');
      while(true){
        if(fs.existsSync(pending))throw Error('Stage pending unknown action/transport; no retry');policy();assertNotHeld(root);
        const existing=reconcileStageTask(root,round,protocol);if(existing.receipts.some(r=>!validateStageActions(r)))throw Error('Stage prior actions unsettled');
        const status=stageBudgetStatus(protocol,scope,existing);if(!status.next_allowed)throw new BudgetPause('Stage actual cost anomaly review: '+status.intervention_reasons.join('; '));
        if(existing.receipts.some(r=>r.actions.some(a=>a.status!=='working'&&a.disposition!=='skipped_after_terminal')))throw new BudgetPause('Stage scope already terminal');
        const body=buildStageBody(requestOptions,registry),accounting=await loadModuleImageAccounting(read(path.join(root,'experiments/config/runtime.local.json')).dshRoot),diagnostic=await stageInputDiagnostic(body,requestOptions,accounting),mass=requestMass(body),p=policy();
        if(mass.body_bytes>p.max_body_bytes||mass.text_bytes>p.max_text_bytes||mass.images!==2)throw Error('Stage interface resource capacity exceeded');
        if(requestOptions.signal?.aborted)throw new BudgetPause('Stage cancelled before dispatch');id='stage_'+randomUUID().replaceAll('-','');
        const projection={schema:'dsh-stage-projection/v1',status:'unchanged',byte_diagnostic_not_a_gate:true,lesson:{status:body.messages.some(m=>m.content.some(b=>b.type==='text'&&b.text.includes('approvedlesson')))?'retained_full':'no_active_lesson'}};
        const request={request_id:id,task_id:protocol.task_id,photo_id:'DSC_3734',condition:'module_support',contract_revision:stageContractRevision(stageContext(requestOptions)),scope,sequence:existing.receipts.length+1,scope_sequence:status.used+1,images:requestOptions.messages.flatMap(m=>m.content).filter(b=>b.type==='image').map(b=>b.attachment),wire_bytes:mass.body_bytes,sent_at:new Date().toISOString(),build:BUILD,reserved_output_tokens:body.max_tokens,input_diagnostic:diagnostic,effective_input_ref:'projected-inputs/'+id+'.json',projection};
        atomic(path.join(round,request.effective_input_ref),{schema:'dsh-stage-projected-input/v1',request_id:id,scope,sent_at:request.sent_at,build:BUILD,...moduleEffectiveInput(body,requestOptions,projection)},true);
        atomic(pending,{...request,status:'dispatching'},true);atomic(path.join(round,'requests',id+'.json'),request,true);sent=true;const start=Date.now(),result=await transport(root,body,scope,id,requestOptions.signal,p.max_response_bytes),response=result.response;
        const publicResponse=object(response)?{...response,content:Array.isArray(response.content)?response.content.filter(b=>!['thinking','redacted_thinking'].includes(b?.type)):response.content}:response;
        atomic(path.join(round,'responses',id+'.json'),{request_id:id,build:BUILD,response:publicResponse},true);if(response?.error)throw Error('Stage transport/provider error; outcome retained');
        const receipt={...request,usage:response?.usage,service_tier:response?.service_tier??response?.usage?.service_tier,provider_id:response?.id,stop_reason:response?.stop_reason,public_content:publicResponse?.content??[],response_wire_bytes:result.wireBytes,seconds:(Date.now()-start)/1000,actions:[]};atomic(path.join(round,'receipts',id+'.json'),receipt,true);this.lastRequest=id;
        const after=reconcileStageTask(root,round,protocol);atomic(path.join(round,'stage-usage-live.json'),stageBudgetStatus(protocol,scope,after));
        if(result.code!==0||!['tool_use','end_turn'].includes(response.stop_reason)||!Array.isArray(response.content)||result.wireBytes>p.max_response_bytes)throw Error(response.stop_reason==='max_tokens'?'Stage output allocation exhausted; no format-repair request':'Invalid stage response');
        usage.inputTokens+=receipt.usage.input_tokens;usage.outputTokens+=receipt.usage.output_tokens;usage.cacheReadTokens+=receipt.usage.cache_read_input_tokens;usage.cacheWriteTokens+=receipt.usage.cache_creation_input_tokens;
        const rawCalls=stageResponseCalls(receipt);
        const python=read(path.join(root,'experiments/config/runtime.local.json')).python||(process.env.VISHARNESS_PYTHON||'python');
        const calls=rawCalls.map(c=>{
          if(c.name!==STAGE_TOOL||!object(c.input))return c;
          const normalized=normalizeStageSubmission(c.input,python);
          return {...c,input:normalized.canonical,normalization_warnings:normalized.warnings};
        });
        if(calls.some((c,i)=>!equal(c.input,rawCalls[i].input))){
          receipt.normalized_calls=calls.map(c=>({id:c.id,name:c.name,input:c.input,warnings:c.normalization_warnings??[]}));
          atomic(path.join(round,'receipts',id+'.json'),receipt);
        }
        this.requestTools.set(id,structuredClone(body.tools));
        try{validateStageCalls(calls,body.tools,requestOptions);}catch(e){
          if(!(e instanceof StageSchemaInvalid))throw e;
          const c=calls[0],feedback=this.settleSchemaRejected(c.name,c.input,c.id,id);sent=false;
          requestOptions=responseFeedbackOptions(requestOptions,feedback);
          atomic(path.join(round,'stage-usage-live.json'),stageBudgetStatus(protocol,scope,reconcileStageTask(root,round,protocol)));continue;
        }
        this.tools=structuredClone(body.tools);atomic(pending,{...request,status:'awaiting_actions',call_ids:calls.map(c=>c.id)});
        for(const [index,c]of calls.entries()){const text=JSON.stringify(c.input);yield {type:'block-start',index,blockType:'tool-call'};yield {type:'tool-call-delta',index,id:c.id,name:c.name,argumentsDelta:text};yield {type:'block-end',index,block:{type:'tool-call',id:c.id,name:c.name,arguments:text}};}
        yield {type:'usage',usage};yield {type:'finish',reason:{kind:'tool-calls'}};sent=false;return;
      }
    }catch(e){this.problem=e;if(sent||!(e instanceof BudgetPause))tripCircuit(root,'Stage attempt stopped: '+e.message,id);throw e;}finally{if(fd!==undefined){fs.closeSync(fd);fs.unlinkSync(lease);}}}
  }();
}
