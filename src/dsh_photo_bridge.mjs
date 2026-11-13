// DSH tool facade and bounded image references. No hashing or model transport.
import fs from 'node:fs';
import path from 'node:path';
import {spawn,spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {randomUUID} from 'node:crypto';
import {replaceCompletedSurface} from './dsh_photo_runtime.mjs';

export class WorkerBridge {
  constructor(python, script, round, photo, condition) {
    this.child = spawn(python, ['-X','utf8',script,'--round',round,'--photo',photo,'--condition',condition],
      {stdio:['pipe','pipe','pipe'],windowsHide:true});
    this.pending=[]; this.error='';
    this.child.stderr.setEncoding('utf8');
    this.child.stderr.on('data',s=>{this.error=(this.error+s).slice(-4000);});
    createInterface({input:this.child.stdout}).on('line',line=>{
      const pending=this.pending.shift();
      if (!pending) {this.error='Unsolicited worker output';this.child.kill();return;}
      try {const value=JSON.parse(line); if (!value.ok) throw Error(value.error); pending.resolve(value.value);}
      catch(e) {pending.reject(e);}
    });
    this.child.on('error',e=>{this.error=String(e); for (const p of this.pending.splice(0)) p.reject(e);});
    this.child.on('exit',()=>{for(const p of this.pending.splice(0))p.reject(Error('Worker exited: '+this.error));});
  }
  call(request) {
    if (this.child.exitCode !== null || this.child.killed) return Promise.reject(Error('Worker unavailable: '+this.error));
    return new Promise((resolve,reject)=>{this.pending.push({resolve,reject});this.child.stdin.write(JSON.stringify(request)+'\n');});
  }
  async close() {
    if (this.child.exitCode !== null) return;
    this.child.stdin.end();
    await new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>{this.child.kill();reject(Error('Worker close deadline'));},10000);
      this.child.once('exit',code=>{clearTimeout(timeout); code===0?resolve():reject(Error('Worker exit '+code+': '+this.error));});
    });
  }
}

// Decode and validate every byte stream through the shared trusted contract.
// A valid/cached ICC profile never establishes that a PNG has valid pixels.
function pngFacts(bytes) {
  const python=(process.env.VISHARNESS_PYTHON||'python');
  const check=spawnSync(python,['-I','-X','utf8',path.join(import.meta.dirname,'photo_image_contract.py'),'--attachment'],
    {cwd:import.meta.dirname,input:bytes,encoding:'utf8',timeout:15000,maxBuffer:65536,windowsHide:true});
  if(check.error||check.status!==0) {
    const reason=check.error?.message||check.stderr?.trim()||'attachment rejected';
    // Keep the established ICC diagnostic prefix; pass specific contract errors
    // through unchanged so callers retain no-explicit-ICC and colour diagnostics.
    if(/ICC|iCCP|profile/i.test(reason))throw Error('Invalid sRGB ICC: '+reason);
    throw Error('Invalid PNG view: '+reason);
  }
  let facts;
  try {facts=JSON.parse(check.stdout);} catch {throw Error('Invalid PNG view: invalid image contract response');}
  if(!facts || facts.pixel_decode_complete!==true || facts.format!=='PNG' || facts.mode!=='RGB' || facts.orientation!==1 ||
     !Number.isSafeInteger(facts.width) || !Number.isSafeInteger(facts.height) ||
     facts.width<1 || facts.height<1 || Math.max(facts.width,facts.height)>512 || facts.bytes!==bytes.length)
    throw Error('Invalid PNG view: inconsistent image contract response');
  return {width:facts.width,height:facts.height,bytes:bytes.length};
}

// Keep this boundary aligned with photo_image_contract.ATTACHMENT_BYTES. Read
// at most the stated file size plus one sentinel byte, even if a file grows.
const ATTACHMENT_BYTES=8_000_000;
function readRegisteredImage(round,file) {
  const absolute=fs.realpathSync(file), relative=path.relative(round,absolute);
  if (!relative || relative.startsWith('..'+path.sep) || path.isAbsolute(relative)) throw Error('View outside round');
  const fd=fs.openSync(absolute,'r');
  let data;
  try {
    const stat=fs.fstatSync(fd);
    if(!stat.isFile())throw Error('Invalid PNG view: not a regular file');
    if(stat.size>ATTACHMENT_BYTES)throw Error('Attachment byte capacity exceeded');
    const buffer=Buffer.allocUnsafe(stat.size+1);
    let length=0;
    while(length<buffer.length) {
      const count=fs.readSync(fd,buffer,length,buffer.length-length,length);
      if(count===0)break;
      length+=count;
    }
    if(length!==stat.size)throw Error('Image changed during bounded read');
    data=buffer.subarray(0,length);
  } finally {fs.closeSync(fd);}
  const facts=pngFacts(data);
  return {data,facts:{relative,...facts}};
}

export class ImageRegistry {
  constructor(round, folder) {
    this.round=fs.realpathSync(round);
    this.file=path.join(folder,'dsh-images.json');
    this.records=fs.existsSync(this.file)?JSON.parse(fs.readFileSync(this.file,'utf8')):{};
  }
  inspect(file) {
    return readRegisteredImage(this.round,file).facts;
  }
  blocks(snapshot) {
    if (!/^o\d+$/.test(snapshot.observation_id) || snapshot.views.length!==2) throw Error('Unexpected observation identity');
    const blocks=[],records={...this.records};
    for (const [index, [file,label]] of snapshot.views.entries()) {
      const id='img_'+snapshot.observation_id+'_'+index, facts=this.inspect(file);
      if (records[id] && JSON.stringify(records[id])!==JSON.stringify(facts)) throw Error('Image record changed; reconcile before continuing');
      records[id]=facts;
      blocks.push({type:'text',text:label});
      blocks.push({type:'image',attachment:{attachmentId:id,mediaType:'image/png',bytes:facts.bytes,width:facts.width,height:facts.height,name:label}});
    }
    const temp=this.file+'.tmp-'+randomUUID();
    fs.writeFileSync(temp,JSON.stringify(records),'utf8'); fs.renameSync(temp,this.file);
    this.records=records;
    return blocks;
  }
  read(ref) {
    const record=this.records[ref.attachmentId];
    if (!record) throw Error('Unknown image id');
    const file=path.join(this.round,record.relative), {data,facts}=readRegisteredImage(this.round,file);
    if (JSON.stringify(facts)!==JSON.stringify(record) || ref.width!==facts.width || ref.height!==facts.height || ref.bytes!==facts.bytes)
      throw Error('Image identity or dimensions mismatch');
    return data;
  }
}

export function messageContent(snapshot, registry) {
  return [{type:'text',text:JSON.stringify(snapshot.context)}, ...registry.blocks(snapshot)];
}

export async function installPhotoTools(ctx, dsh, worker, registry, hooks={}) {
  const specs=await worker.call({op:'schemas'});
  const expected=['photo_edit','photo_observe','photo_history','photo_discover','photo_finish','photo_unable'];
  if (Object.keys(specs).sort().join(',')!==expected.sort().join(',')) throw Error('Unexpected tool catalog');
  let fault=null, snapshot=await worker.call({op:'snapshot'}), callsThisStep=0;
  if(hooks.prepareSnapshot) snapshot=await hooks.prepareSnapshot(snapshot);
  const readOnly=new Set(['photo_observe','photo_history','photo_discover']);
  const maxReadOnlyCalls=hooks.maxReadOnlyCalls===null?Infinity:(hooks.maxReadOnlyCalls??4);
  let firstAction=null, sourceObservation=snapshot.observation_id, batchObservations=[], batchResults=[];
  const descriptions={
    photo_observe:'每个新版本已自动提供作者图和该版本总览。此工具只返回图片，不代替你比较判断；需要更细局部或重访版本时可调用。',
    photo_edit:'根据实际观察提出修图控件，并提交公开判断及下一动作。',
    photo_history:'按记录 ID、版本或关键词查找过去的公开判断和尝试。',
    photo_discover:'按关键词查找后台允许使用的摄影控件。',
    photo_finish:'首次选已看过版本并confirm_export:false导出实际JPEG；收到实际JPEG预览并复核后必须confirm_export:true确认交付。重复false不会完成。',
    photo_unable:'当前无法继续时记录原因和依据。',
  };
  for (const [name,parameters] of Object.entries(specs)) {
    ctx.tools.register({name,description:descriptions[name],parameters,
      output:{schema:{type:'object',additionalProperties:true},render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]},
      execute:async (args,exec)=>{
        callsThisStep++;
        if(callsThisStep===1) firstAction=name;
        if(callsThisStep>maxReadOnlyCalls || (callsThisStep>1 && (!readOnly.has(firstAction)||!readOnly.has(name)))) {
          fault=Error('Only read-only photo actions may share a model step'); throw fault;
        }
        try {
          if(snapshot.status!=='working') return {status:snapshot.status,observation_id:snapshot.observation_id};
          snapshot=await worker.call({op:'action',name,arguments:args,source_observation_id:sourceObservation});
          if(name==='photo_observe' && snapshot.status==='working') batchObservations.push(snapshot.observation_id);
          if(readOnly.has(name)) batchResults.push({name,observation_id:snapshot.observation_id,
            ...(hooks.resultIdentity ? hooks.resultIdentity(name,args,exec,snapshot) : {}),
            result:snapshot.context.tool_result||null,question:name==='photo_observe'?snapshot.context.current_observation?.question:null});
          if (hooks.onAction) await hooks.onAction(name,snapshot,exec);
          return {status:snapshot.status,observation_id:snapshot.observation_id};
        } catch(e) {fault=e;throw e;}
      },
    });
  }
  ctx.on('tools/result',(exec,result)=>{
    if (!fault && String(exec.name||'').startsWith('photo_') && result.isError)
      fault=Error('Photo tool rejected or failed; stop for offline diagnosis');
  });
  ctx.on('agent/pre-step',async ({agent,step},next)=>{
    if (fault || snapshot.status!=='working') return {kind:'reject'};
    if(batchObservations.length>=2) {
      try {snapshot=await worker.call({op:'combine_observations',ids:batchObservations});}
      catch(e) {fault=e;return {kind:'reject'};}
    }
    if(hooks.prepareSnapshot) {
      try {snapshot=await hooks.prepareSnapshot(snapshot);}
      catch(e) {fault=e;return {kind:'reject'};}
    }
    if(callsThisStep>1) snapshot={...snapshot,context:{...snapshot.context,batched_tool_results:batchResults}};
    callsThisStep=0;
    firstAction=null; batchObservations=[];batchResults=[];sourceObservation=snapshot.observation_id;
    if (step>1 || agent.session.surface.nodes.length>1)
      replaceCompletedSurface(agent,messageContent(hooks.decorate ? hooks.decorate(snapshot) : snapshot,registry),dsh.llm.createUserMessage);
    return next();
  });
  return {initial:()=>messageContent(hooks.decorate ? hooks.decorate(snapshot) : snapshot,registry),status:()=>snapshot.status,fault:()=>fault};
}
