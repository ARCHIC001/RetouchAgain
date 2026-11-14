// Resource-aware single-job entry. Does not grant authority or resume old work.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createDshRuntime,loadDsh,cleanupPhotoRun} from './dsh_photo_runtime.mjs';
import {ImageRegistry} from './dsh_photo_bridge.mjs';
import {installStageTools} from './photo_stage_bridge.mjs';
import {LocalWorkerBridge} from './photo_worker_bridge.mjs';
import {stageAdapter,stageResources,validateStageRound,BUILD} from './dsh_stage_adapter.mjs';
import {BudgetPause} from './photo_runtime_errors.mjs';
import {assertNotHeld,tripCircuit} from './lab_timing_dispatch_guard.mjs';
const read=file=>JSON.parse(fs.readFileSync(file,'utf8').replace(/^\uFEFF/,''));
export const STAGE_SYSTEM='DSH independently compares the actual author/current images supplied. Chinese public answers; preserve geometry/content. '+
  'Use exactly one photo_stage_turn with the supplied contract_revision, public review, bank_delta when updating issues, and action {name,arguments}. advance requests a worker phase transition, not an edit or an issue-completion shortcut. '+
  'Follow phase_guidance, public_contract_help and the current work_packet. G1 diagnoses and organizes tone/color issues; tone works on actual grayscale; G2 reassesses full-color consequences and color issues; color preserves accepted tone; G3 integrates and prioritizes residual joint issues; joint refines them; G4 reviews actual JPEG or explicitly requests repair. '+
  'Organize each issue as ordered module-specific fine groups with exact controls. Explain why the selected current group is useful now. The default module/group order is guidance, not a hard gate: revisit an earlier group when actual evidence warrants it. One issue and one group may take multiple review/edit rounds; neither parameter count nor a single edit defines completion. '+
  'Review actual prior effects, then select the next justified small operation, including review and next edit in the same turn when supported. Each edit binds selected_issue_id and the declared selected_group_id, module_decisions, five-field effect_expectation, rationale and predicted_effect. '+
  'Use exact native IDs and absolute values, or one explicit mask_plan. Local masks: linear/radial, exposure/contrast/highlights/shadows/whites/blacks/temp/tint only. Report observations as public judgment_items, not hidden reasoning. '+
  'Tone: G1 records tone_assessment structural/chroma/uncertain attribution and neutral anchors or explicit unavailable evidence. Carry color-related lightness protection into gray Tone, never infer author HSL settings or automatically choose a channel. Neutral-first endpoint support, midtone skeleton/simple 2-5 point Master curve, then broad local lighting; no complex waves or pixel-contour gradients. Local effect explicitly reviews protected/feather/outside regions and may hand off a same-issue Joint residual. Optional pre_tone_wb is once-only G1 scalar WB for a severe cast with explicit tone_basis_impact; review color effect then fresh G1 target before Tone. '+
  'White balance: G2 declares qualified midtone low-saturation reference pairs or no reliable reference, separating target neutrality from intended environment color. Use actual WB readback and soft small-step guidance. Optional develop.wbPick samples the oriented source thumbnail; provide a physically neutral source point in full-image coordinates, never target/crop coordinates. Reuse a suitable existing mask ID for local WB, keeping prior tone. Supported parent/child response is not author matching; accepted WB remains revisable on new evidence. '+
  'Memory: context.work_packet is the single packet body. selected_baseline and BRANCH RESET NOTICE identify the actual selected operation chain; observing an old version is not selecting it. Excluded branch operations are historical, not current effects. active_masks_summary lists actual IDs and geometry; same-kind center proximity is only a warning, never semantic overlap or automatic rejection. Explicit skip is not execution. Known fresh soft dependencies may be reordered with reason; hard evidence, unknown references and cycles cannot be waived by narration. New debt resolution checks each bound focus with no_regression/minor_acceptable/spawn_joint_task and actual visible evidence; a spawned Joint diagnosis has no controls until explicitly revised/selected. Missing evidence remains pending without blocking progress. '+
  'Keep complete public evidence; stored references and cached pixels are not new observations. Model judgment is not a backend fact. Never guess a new mask ID. A trusted rejected_before_execution result means no edit ran: use its code, field_path and rejection_ref to correct the next turn, not to claim a new version or repeat the unchanged rejected action. '+
  'The 50 observations are planning guidance, not a forced stop. Cost is monitored from actual receipts, not worst-case reservations. Finish only after viewing the actual JPEG and explicitly confirming it; partial/unable is valid.';
const stable=value=>JSON.stringify(value,(_key,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
function bankChanged(before,after){
  if(!before||!after||before.revision===after.revision)return false;
  const fields=['id','branch','status','base_version','needs_reassessment','diagnosis','priority','proposed_controls','groups'];
  const semantic=row=>Object.fromEntries(fields.filter(k=>Object.hasOwn(row,k)).map(k=>[k,k==='groups'?row[k].map(g=>Object.fromEntries(['id','group_id','module','step','status','priority','base_version','needs_reassessment','dependencies','proposed_controls'].filter(f=>Object.hasOwn(g,f)).map(f=>[f,g[f]]))):row[k]]));
  const summaries=bank=>(bank.summary??[]).map(semantic).sort((a,b)=>a.id.localeCompare(b.id));
  if(stable(summaries(before))!==stable(summaries(after)))return true;
  return (after.active??[]).some(row=>{const prior=(before.active??[]).find(r=>r.id===row.id);return prior&&stable(semantic(prior))!==stable(semantic(row));});
}
export function stageActionProgress(_name,snapshot,previous,seenHistory=new Set()){
  if(snapshot.stage_turn_result?.kind==='rejected_before_execution')return false;
  const c=snapshot.context,p=previous?.context;
  let progress=!!p&&(c.current!==p.current||c.workflow?.phase!==p.workflow?.phase||bankChanged(p.proposal_bank,c.proposal_bank)||
    !!p.pending_effect_review?.id&&c.pending_effect_review?.id!==p.pending_effect_review.id||
    snapshot.observation_id!==previous.observation_id&&c.evidence_state?.current_view?.reused!==true);
  const history=c.tool_result;
  if(history?.kind==='history'){
    const relevant=new Set(['proposal_bank','prediction','effect_review','textbook_card','discovery']);
    const ids=history.mode==='detail_get'&&history.text?.length&&(/^(bank:|card:|rejection:)/.test(history.record_id)||history.record_id===p?.pending_effect_review?.prediction_history_id)
      ?[[history.record_id.replace(/^(bank:.*):\d+$/,'$1'),history.char_offset??0]]:(history.records??[]).filter(r=>relevant.has(r.kind)&&!r.detail_required).map(r=>[r.kind==='proposal_bank'?r.id.replace(/:\d+$/,''):r.id]);
    for(const id of ids){const key=stable(id);if(!seenHistory.has(key)){seenHistory.add(key);progress=true;}}
  }
  return progress||snapshot.status==='complete';
}
export function decorateStageSnapshot(snapshot,budget){return {...snapshot,context:{...snapshot.context,resource_budget:budget,
  runtime_scope:'Unified G1/tone/G2/color/G3/joint/G4 workflow. Worker owns phase, version-bound proposal bank, grayscale tone observations and selected native operation. No archive or legacy-controller fallback.',
  context_policy:'Full public records remain local and retrievable. Normal working context32-64K, guidance128K, neither is a byte-estimate hard gate.'}};}
export async function runStageJob({root,round,photoId='DSC_3734',variant='module_support'}){
  const admitted=validateStageRound(root,round);({root,round}=admitted);const protocol=admitted.protocol,resources=stageResources();
  if(protocol.validation_only)throw Error('Production stage runner refuses validation-only fixtures');
  if(photoId!=='DSC_3734'||variant!=='module_support')throw Error('Stage runner requires its exact independent single job');
  const folder=path.join(round,'editing',photoId,'r1',variant),scope=path.relative(root,folder).split(path.sep).join('/');
  for(const name of ['state.json','stage-attempt.json','module-attempt.json','worker.lease','dsh-session','dsh-images.json'])if(fs.existsSync(path.join(folder,name)))throw Error('Stage refuses existing attempt or state; no replay');
  for(const name of ['stage-dispatch-pending.json','stage-dispatch.lease'])if(fs.existsSync(path.join(round,name)))throw Error('Stage task has unknown pending/lease');
  for(const dir of ['requests','receipts'])if(fs.existsSync(path.join(round,dir))&&fs.readdirSync(path.join(round,dir)).some(n=>n.endsWith('.json')&&read(path.join(round,dir,n)).scope===scope))throw Error('Stage refuses an already attempted scope');
  assertNotHeld(root);const runtime=read(path.join(root,'experiments/config/runtime.local.json')),dsh=await loadDsh(runtime.dshRoot),registry=new ImageRegistry(round,folder);
  const adapter=stageAdapter(dsh,{root,round,folder,scope,registry});adapter.statusInfo();fs.mkdirSync(folder,{recursive:true});
  const attempt={build:BUILD,photo_id:photoId,scope,started_at:new Date().toISOString(),status:'started'},file=path.join(folder,'stage-attempt.json');
  fs.writeFileSync(file,JSON.stringify(attempt,null,2),{encoding:'utf8',flag:'wx'});let worker,ctx,handle,failure,result,previous;const seenHistory=new Set();
  try{
    worker=new LocalWorkerBridge(runtime.python||(process.env.VISHARNESS_PYTHON||'python'),path.join(root,'src/dsh_stage_worker.py'),round,photoId,variant,{timeoutMs:resources.transport.timeout_ms});
    ({ctx}=await createDshRuntime(runtime.dshRoot,adapter,STAGE_SYSTEM,path.join(folder,'dsh-session')));
    const register=ctx.tools.register.bind(ctx.tools);ctx.tools.register=spec=>register({...spec,execute:async(args,exec)=>{if(adapter.batchTerminal)return adapter.skippedAfterTerminal(spec.name,args,String(exec.callId));adapter.validateExecution(spec.name,args,String(exec.callId));return spec.execute(args,exec);}});
    let photo;try{photo=await installStageTools(ctx,dsh,worker,registry,{
      prepareSnapshot:async()=>{const s=await worker.call({op:'project_snapshot'});previous=structuredClone(s);return s;},decorate:s=>decorateStageSnapshot(s,adapter.statusInfo()),
      onAction:async(name,s,exec)=>{const progress=stageActionProgress(name,s,previous,seenHistory);adapter.markAction(name,s.status,s.observation_id,String(exec.callId),progress);previous=structuredClone(s);},
      onRejected:async(name,s,exec,args)=>{adapter.markRecoverableRejected(name,args,String(exec.callId),s);previous=structuredClone(s);}
    });}finally{ctx.tools.register=register;}
    ctx.on('tools/result',(exec,value)=>{if(value.isError&&String(exec.name??'').startsWith('photo_')&&adapter.lastRequest&&!adapter.problem)adapter.markRejected(exec.name,'Stage tool rejected; preserve the original call and reconcile without replay.');});
    handle=await ctx.agents.create({sessionId:dsh.session.SessionId('stage-'+photoId+'-'+variant),agentOptions:{provider:'photo-local',model:resources.model.id,reasoningEffort:resources.model.effort,maxTokens:resources.model.requested_output_tokens}});
    handle.agent.followup(dsh.llm.createUserMessage({content:photo.initial(),source:{kind:'user'}}));await handle.agent.whenIdle();await ctx.sessions.flush(handle.agent.session);
    if(photo.fault())throw photo.fault();
    if(adapter.problem instanceof BudgetPause){await worker.call({op:'budget_state',action:'pause',note:adapter.problem.message});result={status:'paused_budget',reason:adapter.problem.message};}
    else if(adapter.problem)throw adapter.problem;
    else if(!['complete','diagnosed_unfinished','paused_nonprogress','paused_budget'].includes(photo.status()))throw Error('Stage loop ended without a terminal photography decision');
    else result={status:photo.status(),usage:adapter.statusInfo()};attempt.status=result.status;attempt.result=result;
  }catch(e){failure=e;attempt.status='engineering_diagnosis_required';attempt.error=e.message;tripCircuit(root,'Stage job stopped: '+e.message,adapter.lastRequest);}
  finally{attempt.finished_at=new Date().toISOString();fs.writeFileSync(file,JSON.stringify(attempt,null,2),'utf8');await cleanupPhotoRun({handle,ctx,worker},failure);}
  return result;
}
if(process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url){
  const argv=process.argv.slice(2);try{if(argv.length!==2||argv[0]!=='--round')throw Error('Use --round <new qualified stage round>');console.log(JSON.stringify(await runStageJob({root:path.resolve(import.meta.dirname,'..'),round:path.resolve(argv[1])})));}catch(e){console.error(e.message);process.exitCode=1;}
}
