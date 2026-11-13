// Current serialized JSON-line worker bridge; no controller or billing imports.
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';

export class LocalWorkerBridge {
  constructor(python,script,round,photo,variant,{spawnProcess=spawn,timeoutMs=270000}={}) {
    if(photo!=='DSC_3734'||!['baseline','module_support'].includes(variant)) throw Error('Explicit module photo and variant required');
    this.child=spawnProcess(python,['-I','-X','utf8',script,'--round',round,'--photo',photo,'--variant',variant],
      {cwd:path.dirname(script),stdio:['pipe','pipe','pipe'],windowsHide:true});
    this.active=null;this.failure=null;this.closed=false;this.tail=Promise.resolve();this.timeoutMs=timeoutMs;this.stderr='';
    this.exited=new Promise(resolve=>{this.resolveExit=resolve;});
    const fail=error=>{this.failure??=error;if(this.active){const active=this.active;this.active=null;clearTimeout(active.timer);active.reject(this.failure);}};
    this.fail=fail;
    this.child.stderr.setEncoding('utf8');this.child.stderr.on('data',s=>{this.stderr=(this.stderr+s).slice(-2000);});
    createInterface({input:this.child.stdout}).on('line',line=>{
      if(!this.active){fail(Error('Unsolicited photo worker output'));this.child.kill();return;}
      const active=this.active;this.active=null;clearTimeout(active.timer);
      let result;
      try {
        result=JSON.parse(line);
        if(result?.ok!==true&&(result?.ok!==false||typeof result.error!=='string')) throw Error('Invalid photo worker response');
      } catch(error){fail(error);active.reject(error);this.child.kill();return;}
      if(result.ok===false) {
        // The worker has reported rejection and is now running its native finalizer.
        const error=Error('Photo worker failed: '+result.error);fail(error);active.reject(error);return;
      }
      active.resolve(result.value);
    });
    this.child.on('error',error=>{fail(error);this.resolveExit({error});});
    this.child.stdin.on('error',error=>fail(error));
    this.child.on('exit',(code,signal)=>{this.closed=true;this.resolveExit({code,signal});if(this.active||code!==0)fail(Error('Photo worker exited '+code+': '+this.stderr));});
  }
  call(request) {
    const result=this.tail.then(()=>{
      if(this.failure||this.closed||this.child.exitCode!==null||this.child.killed) throw this.failure||Error('Photo worker unavailable');
      return new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>{this.fail(Error('Photo worker deadline; native outcome unknown, no retry'));this.child.kill();},this.timeoutMs);
        this.active={resolve,reject,timer};
        this.child.stdin.write(JSON.stringify(request)+'\n',error=>{if(error){this.fail(error);this.child.kill();}});
      });
    });
    this.tail=result.catch(()=>{});return result;
  }
  async close() {
    if(this.active){this.fail(Error('Photo worker closed during pending operation'));this.child.kill();}
    if(!this.closed)this.child.stdin.end();let timer;
    try {
      const ended=await Promise.race([this.exited,new Promise((_,reject)=>{timer=setTimeout(()=>{this.child.kill();reject(Error('Photo worker close deadline'));},10000);})]);
      if(ended.error)throw ended.error;if(ended.code!==0)throw Error('Photo worker exit '+ended.code+': '+this.stderr);
    } finally {clearTimeout(timer);}
  }
}
