// Current public request primitives. No controller, configuration or billing imports.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const read=file=>JSON.parse(fs.readFileSync(file,'utf8').replace(/^\uFEFF/,''));
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const USAGE=['input_tokens','cache_read_input_tokens','cache_creation_input_tokens','output_tokens'];

export const MODULE_TOOL_DESCRIPTIONS=Object.freeze({
  photo_edit:'DSH judges pixels. base_version; values=exact slider IDs; settings=nested object; commands=native id/params, not mixed with values/settings. In review: 1..8 module_decisions; USE modules must exactly equal actual operation modules; effect_expectation required. Aliases: color/mixer/pointColor=mixer_point; curve=curves; calibration/profile=basis; mask=masks. rationale/predicted_effect.',
  photo_observe:'Images already supplied; DSH judges. Only return/crop pixels, not analysis; question states view purpose, not an analysis request. Rephrasing question does not create a new image. target=author/current; effect=actual parent/current, parent is not author. Batch same-version target only; effect allows ONE observe. ROI=[L,T,R,B] 0..1.',
  photo_history:'Recall public records by query/IDs; not new pixels or a photographic judgment.',
  photo_discover:'kind:catalog exact interface IDs/units/limits; kind:rule frozen knowledge only when discovery_scope allows. Lookup is not image analysis.',
  photo_finish:'confirm_export:false exports actual JPEG; inspect supplied JPEG before true confirms. DSH judges target match; readback/export is not improvement. Partial/unresolved allowed.',
  photo_unable:'Stop with public evidence and remaining gaps; do not claim an unseen result.',
});

export function buildBody(options,registry,closingOnly=false,contract=null) {
  const outputTokens=contract?.outputTokens??4096;
  if(!Number.isSafeInteger(outputTokens)||outputTokens<1) throw Error('Invalid explicit output allocation');
  if (options.model!=='deepseek-flash' || options.reasoningEffort!=='high' || options.maxTokens!==outputTokens)
    throw Error('Unexpected DSH model, reasoning or output cap');
  const systems=options.messages.filter(m=>m.role==='system');
  const users=options.messages.filter(m=>m.role==='user');
  if (systems.length!==1 || users.length<1 || users.length>2 || options.messages.length!==systems.length+users.length) throw Error('Unexpected active DSH surface');
  if (systems[0].content.some(b=>b.type!=='text')) throw Error('Non-text system prompt');
  let images=0;
  const messages=users.map(user=>({role:'user',content:user.content.map(block=>{
    if(block.type==='text') return {type:'text',text:block.text};
    if(block.type==='image') {
      images++;
      const data=registry.read(block.attachment);
      return {type:'image',source:{type:'base64',media_type:'image/png',data:data.toString('base64')}};
    }
    throw Error('Unexpected active surface content');
  })}));
  if(images!==2) throw Error('Exactly two 512-pixel image occurrences are required');
  const tools=(options.tools||[]).map(t=>({name:t.name,description:t.description,input_schema:t.parameters}));
  const expected=[...(contract?.toolNames??(closingOnly ? ['photo_finish','photo_unable'] : ['photo_edit','photo_observe','photo_history','photo_discover','photo_finish','photo_unable']))].sort();
  if(tools.map(t=>t.name).sort().join(',')!==expected.join(',')) throw Error('Unexpected photo tool set');
  return {model:'deepseek-flash',max_tokens:outputTokens,stream:false,thinking:{type:'enabled'},output_config:{effort:'high'},
          system:systems[0].content.map(b=>b.text).join('\n'),messages,tools};
}

export function moduleCost(usage,serviceTier=usage?.service_tier) {
  if(!object(usage)||USAGE.some(key=>!integer(usage[key]))||usage.server_tool_use) throw Error('Invalid module actual usage receipt');
  if(serviceTier!=='standard'||(usage.service_tier!==undefined&&usage.service_tier!==serviceTier)) throw Error('Unknown or missing module service-tier pricing');
  for(const key of Object.keys(usage)) if(!USAGE.includes(key)&&!['cache_creation','service_tier','output_tokens_details'].includes(key)) throw Error('Unknown module usage/pricing meter: '+key);
  if(usage.output_tokens_details!==undefined){
    const details=usage.output_tokens_details;
    if(!object(details)||Object.keys(details).some(k=>k!=='thinking_tokens')||
       Object.values(details).some(v=>!integer(v))||Object.values(details).reduce((a,b)=>a+b,0)>usage.output_tokens)
      throw Error('Invalid output token detail breakdown');
    // Thinking tokens are a subset of output_tokens, not an additional meter.
  }
  if(usage.cache_creation!==undefined&&(!object(usage.cache_creation)||
     Object.keys(usage.cache_creation).some(k=>!['ephemeral_5m_input_tokens','ephemeral_1h_input_tokens'].includes(k))||
     Object.values(usage.cache_creation).some(v=>!integer(v))||
     Object.values(usage.cache_creation).reduce((a,b)=>a+b,0)!==usage.cache_creation_input_tokens)) throw Error('Invalid module cache creation breakdown');
  return (2*(usage.input_tokens+usage.cache_creation_input_tokens)+0.04*usage.cache_read_input_tokens+8*usage.output_tokens)/1e6;
}

export function compactModuleSchema(schema) {
  if(!object(schema)) return schema;
  const result={};
  for(const [key,value] of Object.entries(schema)) {
    if(['description','title','examples','$comment'].includes(key)||key==='required'&&Array.isArray(value)&&!value.length) continue;
    if(['properties','patternProperties','$defs','definitions'].includes(key)&&object(value))
      result[key]=Object.fromEntries(Object.entries(value).map(([name,child])=>[name,compactModuleSchema(child)]));
    else if(['items','additionalProperties','not','if','then','else','contains','propertyNames'].includes(key)) result[key]=compactModuleSchema(value);
    else if(['allOf','anyOf','oneOf','prefixItems'].includes(key)&&Array.isArray(value)) result[key]=value.map(compactModuleSchema);
    else result[key]=value;
  }
  return result;
}

export async function loadModuleImageAccounting(dshRoot) {
  const packageRoot=path.join(fs.realpathSync(dshRoot),'packages/llm/llm-deepseek');
  const manifest=read(path.join(packageRoot,'package.json'));
  const entry=path.join(packageRoot,'lib/index.js');
  const {deepSeekImageTokens}=await import(pathToFileURL(entry).href);
  if(manifest.name!=='@deepseek-ai/dsh-llm-deepseek'||typeof deepSeekImageTokens!=='function')
    throw Error('Verified local DeepSeek image accounting package unavailable');
  return {imageTokens:deepSeekImageTokens,source:{package:manifest.name,version:manifest.version,entry,
    source_file:path.join(packageRoot,'src/common/image-tokens.ts'),configuration:'v41',max_tokens_per_image:1024}};
}


const PRIVATE_CONTEXT_KEYS=new Set(['thinking','reasoning','reasoning_content','hidden_cot','hiddencot',
  'chain_of_thought','analysis','internal_reasoning','private','private_content','hidden']);
function assertPublicContext(value) {
  if(Array.isArray(value)) {for(const child of value) assertPublicContext(child);}
  else if(object(value)) for(const [key,child] of Object.entries(value)) {
    if(PRIVATE_CONTEXT_KEYS.has(key.toLowerCase())) throw Error('Private content is not a public module input field');
    assertPublicContext(child);
  }
}
function taskContexts(body) {
  const result=[];
  for(const message of body.messages) {
    if(message.role!=='user') throw Error('Module effective input must contain public user messages only');
    for(const block of message.content) {
      if(block.type==='image') continue;
      if(block.type!=='text'||typeof block.text!=='string') throw Error('Unexpected public module input block');
      let value;
      try {value=JSON.parse(block.text);} catch {continue;}
      assertPublicContext(value);
      if(object(value)&&typeof value.current==='string'&&object(value.current_observation)) result.push({block,value});
    }
  }
  if(result.length!==1) throw Error('Module effective input requires one authoritative task context');
  return result;
}
export function moduleEffectiveInput(body,options,projection) {
  const [{block:taskBlock,value:effective_context}]=taskContexts(body);
  const images=options.messages.flatMap(m=>m.content).filter(b=>b.type==='image').map(({attachment})=>{
    const ref=Object.fromEntries(['attachmentId','mediaType','bytes','width','height','name'].filter(key=>attachment[key]!==undefined).map(key=>[key,attachment[key]]));
    assertPublicContext(ref);return ref;
  });
  let index=0;
  const publicBody={...body,messages:body.messages.map(message=>({...message,content:message.content.map(block=>
    block.type==='image'?{type:'image',attachment:images[index++]}:block===taskBlock?{type:'text',text_ref:'effective_context'}:structuredClone(block))}))};
  if(index!==2||images.length!==2) throw Error('Module effective input image occurrences mismatch');
  const canonicalText=taskBlock.text===JSON.stringify(effective_context);
  if(!canonicalText) for(const message of publicBody.messages) for(const block of message.content)
    if(block.text_ref==='effective_context') block.text_ref='effective_context_text';
  return {effective_context,...(canonicalText?{}:{effective_context_text:taskBlock.text}),projection,images,public_body:publicBody,
    text_serialization:canonicalText?'JSON.stringify(effective_context)':'effective_context_text preserves original bytes',
    image_payloads:'excluded; ordered registry attachment metadata only',
    dispatch_status:projection.status==='blocked'?'blocked_before_dispatch':'prepared_before_dispatch'};
}

export {compactModuleSchema as compactPublicSchema,loadModuleImageAccounting as loadImageAccounting,
  moduleEffectiveInput as effectiveInput,moduleCost as actualUsageCost};
