// Current single-tool facade. Worker owns phase semantics and photographic state.
import {messageContent} from './dsh_photo_bridge.mjs';
import {replaceCompletedSurface} from './dsh_photo_runtime.mjs';

export const STAGE_TOOL='photo_stage_turn';
export const STAGE_TOOL_DESCRIPTION='Submit one public stage turn: exact contract_revision, review, optional bank_delta/review_debt_updates, and one explicit action {name,arguments}. DSH judges the supplied images; the worker validates phase authority and executes the named action. advance requests a phase transition, not an image edit. Finish only after viewing and confirming the actual JPEG.';
export function stageContractRevision(context){
  const direct=context?.contract_revision,nested=context?.workflow?.contract_revision,revision=direct??nested;
  if(typeof revision!=='string'||!revision.trim()||direct!==undefined&&nested!==undefined&&direct!==nested)throw Error('Stage context contract_revision missing or inconsistent');
  return revision;
}
export function stageRecoverableRejection(snapshot){
  const result=snapshot?.stage_turn_result;if(result===undefined){
    if(snapshot?.context?.stage_turn_result!==undefined||snapshot?.context?.tool_result?.kind==='rejected_before_execution')throw Error('Incomplete worker stage rejection envelope');
    return null;
  }
  if(result?.kind!=='rejected_before_execution')throw Error('Unknown worker stage_turn_result');
  const proof=result.no_effect;
  if(snapshot.status!=='working'||result.recoverable!==true||result.execution_status!=='not_executed'||
    !['code','message','field_path','rejection_ref'].every(k=>typeof result[k]==='string'&&result[k].trim())||
    !Number.isSafeInteger(result.accepted_bank_revision)||result.accepted_bank_revision<0||
    proof?.passed!==true||proof.native_execution_started!==false||proof.pending!==null||proof.versions_unchanged!==true||proof.ledger_unchanged!==true||proof.photography_unchanged!==true||
    JSON.stringify(result)!==JSON.stringify(snapshot.context?.stage_turn_result)||JSON.stringify(result)!==JSON.stringify(snapshot.context?.tool_result)||
    snapshot.context?.proposal_bank?.revision!==result.accepted_bank_revision)
    throw Error('Unproven worker recoverable stage rejection');
  return result;
}
function catalog(specs,snapshot){
  if(!specs||Object.keys(specs).length!==1||!Object.hasOwn(specs,STAGE_TOOL)||!specs[STAGE_TOOL]||typeof specs[STAGE_TOOL]!=='object'||Array.isArray(specs[STAGE_TOOL]))throw Error('Stage requires exactly one worker photo_stage_turn schema');
  const revision=stageContractRevision(snapshot.context),constraint=specs[STAGE_TOOL].properties?.contract_revision;
  if(constraint?.const!==undefined&&constraint.const!==revision||constraint?.enum&&!constraint.enum.includes(revision))throw Error('Worker stage schema/context revision mismatch');
  return structuredClone(specs[STAGE_TOOL]);
}
export async function installStageTools(ctx,dsh,worker,registry,hooks={}){
  let fault=null,snapshot=await worker.call({op:'snapshot'}),callsThisStep=0,sourceObservation;
  const project=async()=>{snapshot=hooks.prepareSnapshot?await hooks.prepareSnapshot(snapshot):await worker.call({op:'project_snapshot'});};
  await project();let parameters=catalog(await worker.call({op:'schemas'}),snapshot);sourceObservation=snapshot.observation_id;
  const assemblies=new WeakMap(),revisions=new WeakMap();
  ctx.on('system-prompt/assemble',async(_assembly,{agent},next)=>{const assembly=await next();if(agent)assemblies.set(agent,assembly.tools);return assembly;});
  ctx.tools.register({name:STAGE_TOOL,description:STAGE_TOOL_DESCRIPTION,get parameters(){return parameters;},
    output:{schema:{type:'object',additionalProperties:true},render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]},
    execute:async(args,exec)=>{
      try{
        if(++callsThisStep!==1)throw Error('Exactly one photo_stage_turn may execute per model step');
        if(snapshot.status!=='working')throw Error('Stage tool cannot execute after terminal state');
        const revision=revisions.get(exec.agent)??stageContractRevision(snapshot.context);
        if(args.contract_revision!==revision||revision!==stageContractRevision(snapshot.context))throw Error('Stage call contract_revision differs from the supplied context');
        snapshot=await worker.call({op:'stage_turn',arguments:args,source_observation_id:sourceObservation});
        const rejection=stageRecoverableRejection(snapshot);
        if(rejection){
          if(!hooks.onRejected)throw Error('Recoverable stage rejection has no receipt settlement hook');
          await hooks.onRejected(STAGE_TOOL,snapshot,exec,args);
          return {status:snapshot.status,observation_id:snapshot.observation_id,stage_turn_result:rejection};
        }
        if(hooks.onAction)await hooks.onAction(STAGE_TOOL,snapshot,exec);
        return {status:snapshot.status,observation_id:snapshot.observation_id};
      }catch(error){fault=error;throw error;}
    },
  });
  ctx.on('tools/result',(exec,result)=>{if(!fault&&exec.name===STAGE_TOOL&&result.isError)fault=Error('Stage tool rejected or failed; stop for offline diagnosis');});
  ctx.on('agent/pre-step',async({agent},next)=>{
    if(fault||snapshot.status!=='working')return {kind:'reject'};
    try{
      await project();if(snapshot.status!=='working')return {kind:'reject'};
      parameters=catalog(await worker.call({op:'schemas'}),snapshot);
      const tools=assemblies.get(agent);if(!tools)throw Error('Stage pre-step has no captured assembled tool schemas');
      // DSH has already cloned these schemas before pre-step; mutate that exact array.
      tools.splice(0,tools.length,{name:STAGE_TOOL,description:STAGE_TOOL_DESCRIPTION,parameters:structuredClone(parameters)});
      revisions.set(agent,stageContractRevision(snapshot.context));callsThisStep=0;sourceObservation=snapshot.observation_id;
      const decision=await next();if(decision.kind==='reject')return decision;
      const content=messageContent(hooks.decorate?hooks.decorate(snapshot):snapshot,registry);
      if(agent.session.surface.nodes.length>1)replaceCompletedSurface(agent,content,dsh.llm.createUserMessage);
      else return {...decision,messages:[dsh.llm.createUserMessage({content,source:{kind:'plugin',plugin:'photo-stage'}})]};
      return decision;
    }catch(error){fault=error;return {kind:'reject'};}
  });
  return {initial:()=>messageContent(hooks.decorate?hooks.decorate(snapshot):snapshot,registry),status:()=>snapshot.status,fault:()=>fault};
}
