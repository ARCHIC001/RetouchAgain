"""Reusable photo-session behavior, extracted without legacy admission or CLI.

Source: src/dsh_photo_worker.py before archival. Foundation identity is never
assigned here. Current StageSession owns admission, projections and execution;
this base retains observation/export/error handling and neutral compatibility
helpers. Public narrative is kept intact instead of using legacy length gates.
"""
import copy
import json
import uuid
from pathlib import Path
from PIL import ImageChops
import lab_timing_experiment as lab
import photo_decision_memory as memory

def changed_paths(before, after, prefix=''):
    if isinstance(before,dict) and isinstance(after,dict):
        paths=[]
        for key in sorted(set(before)|set(after)):
            name=prefix+'.'+key if prefix else key
            if key not in before or key not in after: paths.append(name)
            else: paths.extend(changed_paths(before[key],after[key],name))
        return paths
    return [prefix] if before != after else []


def normalize_review(raw, state):
    """Retain incomplete public descriptions without inventing an observation."""
    original=raw
    if isinstance(raw,str): raw={'visible_comparison':raw}
    if not isinstance(raw,dict): raw={}
    normalized={}; unresolved={}
    if original is not None and not isinstance(original,(dict,str)): unresolved['review']=original
    for key in ('visible_comparison','remaining_gaps','uncertainty'):
        value=raw.get(key)
        if isinstance(value,list) and all(isinstance(x,str) for x in value): value='；'.join(value)
        if isinstance(value,str): normalized[key]=value
        else:
            normalized[key]=''
            if value is not None: unresolved[key]=value
    outcome=raw.get('previous_outcome','uncertain')
    outcome={'改善':'improved','变差':'worse','混合':'mixed','不确定':'uncertain'}.get(outcome,outcome) if isinstance(outcome,str) else outcome
    if not isinstance(outcome,str) or outcome not in memory.OUTCOMES:
        unresolved['previous_outcome']=raw.get('previous_outcome'); outcome='uncertain'
    normalized['previous_outcome']=outcome
    for key in ('preferred_version','preference_reason'):
        value=raw.get(key)
        if value is not None:
            if isinstance(value,str): normalized[key]=value
            else: unresolved[key]=value
    updates=raw.get('memory_updates',[])
    if not isinstance(updates,list): unresolved['memory_updates']=updates; updates=[]
    known={x['id']:x for x in state.get('decision_memory',[])}
    valid=[]; rejected=[]
    for item in updates:
        if not isinstance(item,dict): rejected.append(item);continue
        ident=item.get('id'); prior=known.get(ident) if isinstance(ident,str) else None
        ok=(isinstance(item.get('kind'),str) and item['kind'] in memory.KINDS and
            isinstance(item.get('status','open'),str) and item.get('status','open') in memory.STATUSES and
            isinstance(item.get('text'),str) and bool(item['text']) and
            isinstance(item.get('focus',''),str) and
            (ident is None or (prior is not None and prior['kind']==item.get('kind'))))
        if ok: valid.append(item)
        else: rejected.append(item)
    if valid: normalized['memory_updates']=valid
    if rejected: unresolved['memory_updates']=rejected
    for key,value in raw.items():
        if key not in ('visible_comparison','remaining_gaps','uncertainty','previous_outcome','preferred_version','preference_reason','memory_updates'):
            unresolved[key]=value
    if unresolved: normalized['unparsed_fields']=unresolved
    normalized['review_quality']='explicit' if normalized['visible_comparison'] else 'missing_visible_comparison'
    return normalized


def normalize_action_metadata(arguments):
    """Move a misplaced public memory field without interpreting edit controls."""
    arguments=copy.deepcopy(arguments)
    if 'memory_updates' not in arguments: return arguments
    updates=arguments.pop('memory_updates')
    review=arguments.get('review')
    if isinstance(review,str): review={'visible_comparison':review}
    elif review is None: review={}
    elif not isinstance(review,dict): review={'original_review':review}
    if 'memory_updates' not in review:
        review['memory_updates']=updates
    elif review['memory_updates'] != updates:
        review['additional_top_level_memory_updates']=updates
    arguments['review']=review
    return arguments


class NoProgress(lab.SafetyStop): pass


def schemas():
    branches = lab.phase_schema('step')['oneOf']
    result = {}
    for branch in branches:
        name = branch['properties']['action']['enum'][0]
        args = branch['properties']['arguments']
        review = args['properties'].setdefault('review', {
            'type':'object', 'properties':{k:{'type':'string'} for k in ('visible_comparison','remaining_gaps','uncertainty')},
            'required':['visible_comparison','remaining_gaps','uncertainty']})
        review['properties'].update({
            'previous_outcome': {'type':'string','enum':sorted(memory.OUTCOMES)},
            'preferred_version': {'type':'string','description':'仅填写已存在且看过的完整版本 ID，例如 v0 或 v1；不确定时省略。不要附加解释，理由写在 preference_reason。'},
            'preference_reason': {'type':'string','description':'说明为什么保留此候选。'},
            'memory_updates': {'type':'array','items':{'type':'object','properties':{
                'id':{'type':'string'}, 'kind':{'type':'string','enum':sorted(memory.KINDS)},
                'status':{'type':'string','enum':sorted(memory.STATUSES)},
                'text':{'type':'string','description':'一条公开问题或尝试经验。'},
                'focus':{'type':'string'}},'required':['kind','text']}},
        })
        # These fields are useful evidence, not operational edit parameters.
        review['required']=[]
        review['properties'].get('visible_comparison',{}).pop('minLength',None)
        if 'review' in args['required']: args['required'].remove('review')
        if name=='edit': args['required']=[x for x in args['required'] if x not in ('rationale','predicted_effect')]
        if name=='finish': args['required']=[x for x in args['required'] if x not in ('rationale','goal_status','remaining_gaps','stop_basis')]
        if name in ('edit','observe'):
            key='base_version' if name=='edit' else 'version'
            args['properties'][key]['description']='不填时使用当前活动版本；如填写须是确切版本 ID。'
            args['required']=[x for x in args['required'] if x!=key]
        if name=='finish':
            args['properties']['confirm_export']['description']='首次选择候选导出实际JPEG时为false；已收到actual JPEG/export观察并复核后，确认交付必须显式为true。false不会完成交付，也不会再次导出同版本。'
            args['properties']['selected_version']['description']='不填时使用当前版本；确认导出时使用已导出版本。'
            args['required']=[x for x in args['required'] if x!='selected_version']
        if name == 'history':
            args['properties']={k:v for k,v in args['properties'].items() if k in ('review','query','offset','page_size')}
            args['properties'].update({'ids':{'type':'array','items':{'type':'string'}}, 'version':{'type':'string'}})
        result['photo_'+name] = args
    return result


class BasePhotoSession(lab.Trajectory):
    def __init__(self, round_dir, job, condition='B'):
        raise lab.LocalDispatchDenied('use StageSession admission')

    def budget(self, version):
        # The per-photo request budget already bounds views. Keep the activity
        # projection compact without refusing a useful third local observation.
        seen=[o for o in self.state['observations'] if o['version']==version and o.get('artifact','canonical')=='canonical']
        used=sum(1 for o in seen if o.get('requested'))
        return {'version':version,'extra_views_left':max(0,24-used),'prior_view_count':len(seen),
                'seen_regions':[{k:o.get('view_spec',{}).get(k) for k in ('candidate_roi','reference_roi')} for o in seen[-3:]]}

    def validate_views(self, views):
        for filename, _ in views:
            lab.require_srgb_tag(filename)
            with lab.Image.open(filename) as image:
                if max(image.size) > 512: raise ValueError('Model view exceeds 512 pixels')

    def observe(self, *args, **kwargs):
        # Reuse an exact previously delivered view without another backend render.
        # A second consecutive request for that same view pauses as no progress.
        requested = kwargs.get('requested',False)
        spec = kwargs.get('args') or {}
        version = (args[0] if args else None) or self.state['current']
        if requested and self.state['observations']:
            limit = lab.SIDE_LIMITS['detail' if spec.get('reference_roi') or spec.get('candidate_roi') else 'overview']
            view = {'reference_roi':spec.get('reference_roi'),'candidate_roi':spec.get('candidate_roi'),
                    'side':min(int(spec.get('side',limit)),limit)}
            for roi in (view['reference_roi'],view['candidate_roi']):
                if roi is not None: lab.pixel_box((10000,10000),roi)
            previous = next((o for o in reversed(self.state['observations']) if o['version']==version and
                             o['artifact']=='canonical' and o.get('view_spec')==view),None)
            if previous:
                last=self.state['observations'][-1]
                if last['id']==previous['id'] and last.get('reused'):
                    raise NoProgress('Consecutive repeated view without new evidence; preserve candidate for review')
                if self.budget(version)['extra_views_left']<=0: raise NoProgress('View allowance reached; preserve candidate for review')
                self.validate_views(previous['views'])
                observation=copy.deepcopy(previous)
                observation.update(id='o'+str(len(self.state['observations'])+1).zfill(4),requested=True,
                                   question=spec.get('question','重新查看'),decision_judgment=None,
                                   revisit_of=previous['id'],reused=True)
                self.state['observations'].append(observation);self.save()
                return observation
        observation = super().observe(*args, **kwargs)
        folder = self.folder/'observations'/observation['id']
        candidate_index = 2 if observation['parent'] else 1
        label=('实际 JPEG ' if observation['artifact']=='export' else '观察版本 ')+observation['version']
        observation['views'] = [(str(folder/'view-0.png'), '作者成片'),
                                (str(folder/f'view-{candidate_index}.png'), label)]
        self.validate_views(observation['views'])
        self.save()
        return observation

    def snapshot(self):
        observation = self.state['observations'][-1]
        context = self.context()
        context.pop('anchors', None)
        context.pop('disclosed_evidence', None)
        if context.get('active_export'):
            required=self.state['status']!='complete'
            context['active_export'].update(confirmation_required=required,
                required_next_action={'tool':'photo_finish','selected_version':self.state['export']['version'],'confirm_export':True} if required else None,
                note='实际JPEG已生成；复核可接受后需显式确认，不会自动认定目标匹配。')
        recent = [{'action': h['action'], 'version':h.get('version'), 'parent':h.get('parent'),
                   'changed_controls': h.get('changed_controls',[])}
                  for h in self.state['history'] if h.get('action') in ('edit','observe','history','discover','finish','unable')]
        # A compact record of recently delivered views is an execution fact,
        # shared by A/B/C; it contains no inferred aesthetic judgment.
        context['recent_observations']=[{'id':item['id'],'version':item['version'],
            'view_spec':item.get('view_spec',{}),'reused':bool(item.get('reused')),
            'revisit_of':item.get('revisit_of')} for item in self.state['observations']
            if item.get('requested') and item.get('artifact')=='canonical'][-4:]
        if self.condition == 'A':
            # Public chronological records only; full native readbacks and private paths stay outside.
            context['public_history'] = [{'action':h.get('action'), 'version':h.get('version'),
                'parent':h.get('parent'), 'judgment':h.get('judgment'), 'next_action':h.get('next_action'),
                'arguments':h.get('arguments')} for h in self.state['history']]
        elif self.condition == 'C':
            context['decision_memory'] = memory.card(self.state, observation, recent)
        return {'context':context,'views':observation['views'], 'status':self.state['status'],
                'observation_id':observation['id'], 'memory_bytes':memory.size(context.get('decision_memory',{}))}

    def budget_state(self, action, note=''):
        if self.state.get('pending'): raise lab.SafetyStop('Pending native outcome requires reconciliation')
        if action == 'pause' and self.state['status'] == 'working':
            self.state.update(status='paused_budget',interruption=str(note))
        elif action == 'resume' and self.state['status'] == 'paused_budget':
            self.state.update(status='working',interruption=None)
        else: raise lab.SafetyStop('Invalid budget state transition')
        self.save(); return self.snapshot()

    def combine_observations(self, ids):
        if not isinstance(ids,list) or not 2<=len(ids)<=4 or len(set(ids))!=len(ids):
            raise ValueError('A batch requires two to four distinct observations')
        observations=[]
        for ident in ids:
            item=next((o for o in self.state['observations'] if o['id']==ident),None)
            if not item or item.get('artifact')!='canonical' or len(item.get('views',[]))!=2:
                raise ValueError('Batch contains an unavailable canonical observation')
            observations.append(item)
        if len({o['version'] for o in observations})!=1:
            raise ValueError('A visual batch must use one version')
        ident='o'+str(len(self.state['observations'])+1).zfill(4)
        folder=self.folder/'observations'/ident; folder.mkdir(parents=True,exist_ok=False)
        views=[]
        for index,label in enumerate(('作者成片','当前版本')):
            dest=folder/f'batch-{index}.png'
            images=[lab.thumb(lab.srgb(o['views'][index][0],required=True),496) for o in observations]
            max_height=(512-8-len(images)*34)//len(images)
            for image in images: image.thumbnail((496,max_height),lab.Image.Resampling.LANCZOS)
            width=max(256,max(image.width for image in images)+16)
            height=8+sum(image.height+34 for image in images)
            board=lab.Image.new('RGB',(width,height),'#f5f6f8');draw=lab.ImageDraw.Draw(board)
            font=lab.ImageFont.truetype(lab.FONT,20);y=8
            for pos,image in enumerate(images,1):
                draw.text((8,y),f'局部{pos}',fill='#17212c',font=font);y+=26
                board.paste(image,((width-image.width)//2,y));y+=image.height+8
            lab.save_srgb_png(board,dest)
            lab.require_srgb_tag(dest)
            with lab.Image.open(dest) as image:
                if max(image.size)>512:raise ValueError('Combined view exceeds 512 pixels')
            views.append((str(dest),label+'：从上到下依次为局部1至局部'+str(len(images))))
        last=observations[-1]
        item={'id':ident,'version':last['version'],'parent':last['parent'],'artifact':'batch',
              'path':last['path'],'question':'；'.join('局部'+str(i)+'：'+str(o['question']) for i,o in enumerate(observations,1)),
              'views':views,'view_spec':{'batch_of':ids},'batch_of':ids,'requested':True,
              'decision_judgment':None,'numeric_disclosed':False}
        self.state['observations'].append(item); self.save()
        return self.snapshot()

    def act(self, name, args, source_observation_id=None):
        if self.state.get('pending') or self.state['status'] != 'working':
            raise lab.SafetyStop('Trajectory is not eligible for another action')
        if not name.startswith('photo_'): raise ValueError('unknown tool')
        action = name[6:]
        result = lab.normalize_action({'action':action,'arguments':args}, 'step')
        if isinstance(result.get('arguments'),dict):
            raw_args=result['arguments']=normalize_action_metadata(result['arguments'])
            raw_args['review']=normalize_review(raw_args.get('review'),self.state)
            uncertain=raw_args['review'].setdefault('unparsed_fields',{})
            if action in ('edit','observe'):
                key='base_version' if action=='edit' else 'version'
                if key not in raw_args:
                    raw_args[key]=self.state['current'];uncertain['action.'+key]='defaulted to active version'
            elif action=='finish' and 'selected_version' not in raw_args:
                export=self.state.get('export')
                raw_args['selected_version']=export['version'] if raw_args.get('confirm_export') and export else self.state['current']
                uncertain['action.selected_version']='defaulted to exported or active version'
            narrative=('rationale','predicted_effect') if action=='edit' else ('rationale',) if action=='finish' else ()
            for key in narrative:
                if not isinstance(raw_args.get(key),str):
                    if key in raw_args: uncertain['action.'+key]=raw_args[key]
                    else: uncertain['action.'+key]='not provided'
                    raw_args[key]=''
            if action=='finish':
                goal=raw_args.get('goal_status')
                goal={'匹配':'matched','部分':'partial','未解决':'unresolved'}.get(goal,goal) if isinstance(goal,str) else goal
                if goal not in ('matched','partial','unresolved'):
                    uncertain['action.goal_status']=raw_args.get('goal_status','not provided');goal='unresolved'
                raw_args['goal_status']=goal
                for key in ('remaining_gaps','stop_basis'):
                    if not isinstance(raw_args.get(key),(str,list,dict)):
                        uncertain['action.'+key]=raw_args.get(key,'not provided');raw_args[key]='未说明'
            if not uncertain: raw_args['review'].pop('unparsed_fields',None)
        lab.validate_action(result, 'step', allow_empty_review=True)
        args = result['arguments']; observation = self.state['observations'][-1]
        if source_observation_id is not None:
            source=next((o for o in self.state['observations'] if o['id']==source_observation_id),None)
            if source is None or (source['id']!=observation['id'] and action not in ('observe','history','discover')):
                raise ValueError('Action refers to an unavailable source observation')
            observation=source
        last = next((h for h in reversed(self.state['history']) if h.get('action') == 'edit' and h.get('version') == observation['version']), {})
        controls = last.get('changed_controls',[])
        memory.record(self.state, args['review'], observation, controls)
        observation['review_evidence']=args['review']['review_quality']
        self.record_decision(observation, result)
        try:
            if action == 'history':
                request={k:args.get(k) for k in ('ids','version','query','offset','page_size')}
                if any(h.get('action')=='history' and h.get('observation_id')==observation['id'] and h.get('request')==request for h in self.state['history']):
                    raise NoProgress('Repeated history request without a new observation')
                requested = memory.history(self.state, ids=args.get('ids'), version=args.get('version'),
                                           query=args.get('query'), offset=args.get('offset',0),
                                           page_size=args.get('page_size',5))
                self.state['history'].append({'action':'history','version':observation['version'],
                                              'observation_id':observation['id'],'request':request})
                self.state['tool_result'] = {'kind':'history',**requested}
            elif action == 'observe':
                viewed=self.observe(args.get('version',self.state['current']), args=args, requested=True)
                self.state['history'].append({'action':'observe','version':viewed['version'],
                                              'parent':viewed.get('parent'),'observation_id':viewed['id']})
                if viewed.get('reused'):
                    self.state['tool_result']={'kind':'observation_reused','note':'已复用同一 512 画面；没有新增视觉证据。'}
                else: self.state.pop('tool_result', None)
            elif action == 'discover':
                controls = self.backend().controls(); controls = controls.get('controls',controls) if isinstance(controls,dict) else controls
                commands = lab.read(lab.ROOT/'experiments/config/backend-evidence-2026-10-28/commands.json')
                query = str(args.get('query','')).lower(); tokens = lab.re.findall(r'[\w]+',query)
                rows = {kind:[r for r in items if (r['id'].split('.')[0] in lab.ALLOW if kind == 'controls' else r['id'] in lab.COMMANDS)
                              and (not tokens or any(t in json.dumps(r).lower() for t in tokens))]
                        for kind,items in [('controls',controls),('commands',commands)]}
                page = {k:lab.page_rows(v,args) for k,v in rows.items()}
                if any(h.get('action')=='discover' and h.get('version')==self.state['current'] and h.get('query')==query and h.get('offset',0)==args.get('offset',0) for h in self.state['history']):
                    raise NoProgress('Repeated discovery at unchanged version')
                self.state['history'].append({'action':'discover','version':self.state['current'],'query':query,'offset':args.get('offset',0),'result':page})
                self.state['tool_result'] = {'kind':'discover','query':query,**page}
            elif action == 'edit':
                base = args.get('base_version',self.state['current'])
                if base not in self.state['reviewed']: raise ValueError('Base needs an actual review')
                defaults = {'values':{},'settings':{},'commands':[]}
                if any(h.get('action') == 'edit' and h.get('parent') == base and
                       all(h['arguments'].get(k,empty) == args.get(k,empty) for k,empty in defaults.items())
                       for h in self.state['history']):
                    raise NoProgress('Same operation already tested from this base')
                op = 'op_'+uuid.uuid4().hex
                self.state['pending'] = {'operation_id':op,'arguments':args,'base':base}; self.save()
                actual = self.backend().apply(self.state['versions'][base]['settings'],args)
                if actual == self.state['versions'][base]['settings']: raise ValueError('Settings did not change')
                seq = len(self.state['versions']); version = 'v'+str(seq); dest = self.folder/(version+'.png')
                if dest.exists(): raise ValueError('Candidate path already exists')
                self.backend().render(dest,self.protocol.get('render_long_edge',lab.DSH_RENDER_EDGE))
                self.state['versions'][version] = {'settings':actual,'master':str(dest),'parent':base,'measurements':{},'operation_id':op}
                self.state['current'] = version; self.state['export'] = None
                self.state['history'].append({'action':'edit','version':version,'parent':base,'arguments':args,
                    'changed_controls':changed_paths(self.state['versions'][base]['settings'],actual),'settings_readback':actual})
                self.state.pop('tool_result',None)
                before, after = lab.srgb(self.state['versions'][base]['master']), lab.srgb(dest)
                if before.size != after.size or ImageChops.difference(before,after).getbbox() is None:
                    raise ValueError('Candidate geometry changed or pixels did not change')
                self.save(); self.observe(); self.state.pop('pending')
            elif action == 'finish':
                version = args.get('selected_version',self.state['current'])
                if version not in self.state['reviewed']: raise ValueError('Select a reviewed version')
                if args.get('confirm_export'):
                    export = self.state.get('export')
                    if not export or export['version'] != version or not any(o['artifact']=='export' and o['path']==export['path'] and lab.reviewed(o) for o in self.state['observations']):
                        raise ValueError('Review the actual JPEG first')
                    self.state.update(status='complete',selected_version=version,model_goal_claim=args['goal_status'],
                                      model_remaining_gaps=args['remaining_gaps'],model_stop_basis=args['stop_basis'],author_accepted=None)
                elif (self.state.get('export') or {}).get('version') == version:
                    export=self.state['export']
                    facts=lab.require_srgb_tag(export['path'])
                    if facts['dimensions'] != self.input['original_dimensions']: raise ValueError('Existing JPEG dimensions mismatch')
                    self.state['current']=version
                    latest=self.state['observations'][-1]
                    if latest['artifact']!='export' or latest['path']!=export['path']:
                        self.observe(version,Path(export['path']),'export')
                    self.state['tool_result']={'kind':'export_reused','note':'同版本实际 JPEG 已存在，未重复导出；请复核后用 confirm_export:true 确认。'}
                else:
                    dest = self.folder/('export_'+uuid.uuid4().hex[:10]+'.jpg')
                    self.state['pending']={'operation_id':'op_'+uuid.uuid4().hex,'kind':'export','base':version,'path':str(dest)}; self.save()
                    self.backend().restore(self.state['versions'][version]['settings'])
                    dims = self.backend().render(dest,0,'jpeg')
                    if dims != self.input['original_dimensions']: raise ValueError('JPEG dimensions mismatch')
                    self.state['current']=version; self.state['export']={'version':version,'path':str(dest),'dimensions':dims}
                    self.state.pop('tool_result',None); self.observe(version,dest,'export'); self.state.pop('pending')
                self.state['history'].append({'action':'finish','version':version,'confirmed':bool(args.get('confirm_export'))})
            elif action == 'unable':
                self.state.update(status='diagnosed_unfinished',diagnosis=args)
                self.state['history'].append({'action':'unable','version':observation['version']})
            else: raise ValueError('Unknown photo action')
            self.save()
            return self.snapshot()
        except NoProgress as exc:
            self.state['history'].append({'action_paused':str(exc),'proposed':result,'observation_id':observation['id']})
            self.state.update(status='paused_nonprogress',interruption=str(exc)); self.save()
            return self.snapshot()
        except Exception as exc:
            self.state['history'].append({'action_error':str(exc),'proposed':result,'observation_id':observation['id']})
            if self.state.get('pending'): self.state['pending']['outcome']='unknown; reconcile before restore or replay'
            self.state.update(status='engineering_diagnosis_required',interruption=str(exc)); self.save()
            raise


# Existing shared method references use original.PhotoSession.
PhotoSession = BasePhotoSession
