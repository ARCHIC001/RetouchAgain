// Project-owned composition of the actual DSH agent loop. No default tools,
// retries, title models, compaction models, credentials or network adapters.
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {noHashPersistence} from './dsh_photo_persistence.mjs';

export async function loadDsh(root) {
  const load = async p => import(pathToFileURL(path.join(root, p, 'lib/index.js')).href);
  const [cordis, llm, session, projections, prompt, tools, agents, loop, persistence] = await Promise.all([
    load('vendor/cordis'), load('packages/llm/llm'), load('packages/core/session'),
    load('packages/session/session-projection'), load('packages/core/system-prompt'),
    load('packages/core/tools'), load('packages/core/agent'), load('packages/core/agent-loop'),
    load('packages/session/session-persistence'),
  ]);
  return {cordis, llm, session, projections, prompt, tools, agents, loop, persistence};
}

export async function createDshRuntime(root, adapter, system = '', sessionRoot = null) {
  const dsh = await loadDsh(root);
  const ctx = new dsh.cordis.Context();
  await ctx.plugin(dsh.llm.default);
  await ctx.plugin(dsh.session.default);
  if (sessionRoot) await ctx.plugin(noHashPersistence(dsh.persistence.default), {root:sessionRoot});
  await ctx.plugin(dsh.projections.default);
  await ctx.plugin(dsh.prompt.default, {includeHarnessIdentity: false, includeRuntimeContext: false, personaPrefix: system});
  await ctx.plugin(dsh.tools.default, {mode: 'native'});
  await ctx.plugin(dsh.agents.default);
  if (sessionRoot) {
    ctx.on('llm/stream', (options, next) => (async function*() {
      const session = ctx.sessions.get(options.sessionId);
      if (!session) throw Error('Model dispatch has no owned session');
      await ctx.sessions.flush(session);
      yield* next();
    })());
    ctx.on('tools/execute', async (exec, next) => {
      if (!exec.agent) throw Error('Photo tool has no owning agent');
      await ctx.sessions.flush(exec.agent.session);
      return next();
    });
  }
  await ctx.plugin(dsh.loop.default, {agents: [], maxParallelToolCalls: 1});
  ctx.llm.registerAdapter(['photo-local'], adapter);
  return {ctx, dsh};
}

// DSH snapshots tool schemas before pre-step. Retain that array so the boundary
// updates both the next request and execution visibility, never a live tool batch.
export function installClosingBoundary(ctx, remainingRequests) {
  const assemblies = new WeakMap(), restricted = new WeakSet();
  const allowed = ['photo_finish', 'photo_unable'];
  ctx.on('system-prompt/assemble', async (_assembly, {agent}, next) => {
    const assembly = await next();
    if (agent) assemblies.set(agent, assembly.tools);
    return assembly;
  });
  ctx.on('agent/pre-step', async ({agent}, next) => {
    const decision = await next();
    if (decision.kind === 'reject') return decision;
    if (!restricted.has(agent) && remainingRequests() <= 2) {
      agent.ctx.tools.restrict({allow:allowed});
      restricted.add(agent);
    }
    if (restricted.has(agent)) {
      const tools = assemblies.get(agent);
      if (!tools) throw Error('Closing boundary has no assembled tool schemas');
      const closing = tools.filter(tool => allowed.includes(tool.name));
      tools.splice(0, tools.length, ...closing);
    }
    return decision;
  });
}

// Finish every cleanup even if an earlier one fails. Keep the original failure
// as well as distinct teardown errors instead of replacing it in a finally.
export async function cleanupPhotoRun({handle, ctx, worker}, failure) {
  const failures = failure === undefined ? [] : [failure];
  for (const close of [() => handle?.dispose(), () => ctx?.fiber.dispose(), () => worker?.close()]) {
    try { await close(); }
    catch (error) { if (!failures.includes(error)) failures.push(error); }
  }
  if (failures.length === 1) throw failures[0];
  if (failures.length > 1) throw new AggregateError(failures, 'Photo run and cleanup failed', {cause:failures[0]});
}

// Replace only completed conversation units. DSH persists the replacement
// decision and retains the original log. Never replace a pending tool call.
export function replaceCompletedSurface(agent, content, createUserMessage) {
  const session = agent.session;
  const nodes = [...session.surface.nodes];
  if (nodes.length < 2) return false;
  const span = nodes.slice(1);
  const message = createUserMessage({content, source: {kind: 'plugin', plugin: 'dsh-photo'}});
  session.append('user/message', message, {
    surfaceOp: {op: 'replace', startSeq: span[0], endSeq: span.at(-1)},
    sourceEventSeqs: span,
  });
  return true;
}
