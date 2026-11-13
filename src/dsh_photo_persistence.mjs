// Minimal session persistence for this single-process experiment. IDs, ordered
// events, byte lengths and fsync are used; no content hash is computed.
import fs from 'node:fs';
import path from 'node:path';

export function noHashPersistence(Base) {
  return class PhotoSessionPersistence extends Base {
    constructor(ctx, config) {
      super(ctx);
      this.root = path.resolve(config.root);
      this.writers = new Map();
      fs.mkdirSync(this.root, {recursive:true});
      ctx.on('session/event', (session, event) => {
        const handle = this.writers.get(String(session.id));
        if (handle) {
          try { handle.appendSync([event]); }
          catch (error) { handle.failure = error; }
        }
      });
      ctx.on('session/flush', session => this.writers.get(String(session.id))?.flush());
      ctx.on('session/disposed', session => this.writers.get(String(session.id))?.close());
      ctx.effect(() => async () => { for (const h of [...this.writers.values()]) await h.close(); });
    }
    filename(id) {
      if (!/^[A-Za-z0-9_-]{1,100}$/.test(String(id))) throw Error('Unsafe session identifier');
      return path.join(this.root, String(id)+'.jsonl');
    }
    async create(header, options={}) {
      options.signal?.throwIfAborted();
      if (options.inheritedEventCount) throw Error('Forked sessions are outside this experiment');
      const file = this.filename(header.id);
      const lock = fs.openSync(file+'.lock', 'wx');
      try {
        const fd = fs.openSync(file, 'wx');
        fs.writeSync(fd, JSON.stringify({header, inheritedEventCount:0})+'\n'); fs.fsyncSync(fd);
        const handle = this.makeHandle(header, file, fd, lock, 'write');
        this.writers.set(String(header.id), handle);
        return handle;
      } catch (error) { fs.closeSync(lock); fs.unlinkSync(file+'.lock'); throw error; }
    }
    readFile(file) {
      const text = fs.readFileSync(file, 'utf8');
      if (!text.endsWith('\n')) throw Error('Incomplete session tail; offline reconciliation required');
      const rows = text.trimEnd().split('\n').map(JSON.parse);
      const header = rows.shift();
      if (!header?.header || header.inheritedEventCount !== 0 || rows.some((event, i) => event.seq !== i))
        throw Error('Invalid or non-contiguous session log');
      return {header:header.header,events:rows};
    }
    makeHandle(header,file,fd,lock,access) {
      const owner = this;
      let closed = false;
      let count = this.readFile(file).events.length;
      const handle = {
        id:header.id, header, inheritedEventCount:0, access, failure:null,
        async read(offset=0,length) {
          if (closed) throw Error('Closed session handle');
          const {events} = owner.readFile(file);
          return {eventState:'detached',events:events.slice(offset,length === undefined ? undefined : offset+length)};
        },
        appendSync(events) {
          if (closed || access !== 'write' || handle.failure) throw handle.failure || Error('Session handle unavailable');
          if (events.some((event,i)=>event.seq !== count+i)) throw Error('Session event sequence mismatch');
          if (!events.length) return;
          const bytes = Buffer.from(events.map(event=>JSON.stringify(event)+'\n').join(''),'utf8');
          const previous = fs.fstatSync(fd).size;
          try { let n=0; while(n<bytes.length) n+=fs.writeSync(fd,bytes,n,bytes.length-n,null); fs.fsyncSync(fd); count+=events.length; }
          catch (error) { try { fs.ftruncateSync(fd,previous); fs.fsyncSync(fd); } catch {} throw error; }
        },
        async append(events, options={}) { options.signal?.throwIfAborted(); handle.appendSync(events); },
        async flush(options={}) { options.signal?.throwIfAborted(); if (handle.failure) throw handle.failure; if (closed) throw Error('Closed session handle'); if (fd !== null) fs.fsyncSync(fd); },
        async close() {
          if (closed) return;
          closed = true;
          const failures = handle.failure ? [handle.failure] : [];
          const attempt = action => {
            try { action(); }
            catch (error) { if (!failures.includes(error)) failures.push(error); }
          };
          if (fd !== null) { attempt(() => fs.fsyncSync(fd)); attempt(() => fs.closeSync(fd)); }
          if (lock !== null) {
            attempt(() => fs.closeSync(lock));
            // Failed logs require reconciliation; release descriptors, not the
            // on-disk ownership barrier that prevents an unsafe automatic resume.
            if (!failures.length) attempt(() => fs.unlinkSync(file+'.lock'));
            owner.writers.delete(String(header.id));
          }
          if (failures.length === 1) throw failures[0];
          if (failures.length > 1) throw new AggregateError(failures, 'Session persistence close failed', {cause:failures[0]});
        },
        async [Symbol.asyncDispose]() { await handle.close(); },
      };
      return handle;
    }
    async open(id, access, options={}) {
      options.signal?.throwIfAborted();
      const file = this.filename(id);
      const lock = access === 'write' ? fs.openSync(file+'.lock','wx') : null;
      try {
        const {header} = this.readFile(file);
        if (String(header.id) !== String(id)) throw Error('Session identity mismatch');
        const fd = access === 'write' ? fs.openSync(file,'a') : null;
        const h = this.makeHandle(header,file,fd,lock,access);
        if (access === 'write') this.writers.set(String(id),h);
        return h;
      } catch (error) { if (lock !== null) {fs.closeSync(lock);fs.unlinkSync(file+'.lock');} throw error; }
    }
    async flush() { for (const handle of this.writers.values()) await handle.flush(); }
    async stat(id) {
      const file=this.filename(id);
      if (!fs.existsSync(file)) return undefined;
      const {header,events}=this.readFile(file), sizeBytes=fs.statSync(file).size;
      return {header,eventCount:events.length,sizeBytes,revision:String(events.length)+':'+sizeBytes};
    }
    async list() {
      const rows=[];
      for (const file of fs.readdirSync(this.root)) if (file.endsWith('.jsonl')) rows.push(await this.stat(file.slice(0,-6)));
      return rows;
    }
  };
}
