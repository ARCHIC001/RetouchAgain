"""Current compact LAB workflow and independent diagnostics; no hash verification."""
import argparse, base64, copy, concurrent.futures, contextlib, io, json, math, os, random, re, shutil, subprocess, sys, threading, time, traceback, uuid
from pathlib import Path
import numpy as np
from PIL import Image, ImageChops, ImageCms, ImageDraw, ImageFont, TiffImagePlugin
from photo_tools import LightCraft, structured, strict_json
from dsh_photo_color import validate as validate_srgb_profile
from raw_render_contract import CONTRACT as RAW_RENDER_CONTRACT, inspect_dng
from photo_image_contract import read_srgb, inspect_image, assert_same_geometry, assert_scaled_geometry
from photo_resize_contract import canonical_preview

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'src/photo_tools.py').is_file())
PYTHON=sys.executable
PHOTO_IDS=['0294','1179','3734','3209','8819','6268']
DEV_IDS=['7179','1126']
CONDITIONS={'visual':'无外部 LAB','prior':'仅事前指导','verify':'仅事后核验','combined':'事前＋事后'}
COLORS=['#7B2CBF','#007EA8','#D66B00','#228B4E','#B62C73']
IMPLEMENTATION_REVISION='r16-opacity-typed-infrastructure-20261111'
IMPLEMENTATION_BUILD=IMPLEMENTATION_REVISION
WORKFLOW='compact-step/v1'
ALLOW={'wb','light','color','mixer','grading','curve','masks','point_colors','pointColor','effects','calibration','profile'}
COMMANDS={'develop.curve','curve.reset','pointColor.pick','pointColor.delete','mask.add','mask.addComponent','mask.brushStroke','mask.update','mask.sampleColor','mask.objectPoint','mask.refineDetail','mask.refine','mask.adjust','mask.select','mask.delete','mask.deleteAll','mask.rename','mask.invert','mask.visible','mask.duplicate','mask.component','mask.move'}
FONT='C:/Windows/Fonts/msyh.ttc'
PRINCIPLES='LAB 用来定影调，每张图在画面上打五个点：highlight、shadow 是高光和阴影区域的中位数，定曝光、对比和黑白场；其余 2–3 个是画面里真正定调的主色区域，定白平衡和主色方向。点打在相同内容上才有目标残差，只凭角色对应时不编造残差。数字不是审美答案，实际画面优先；不要求每步残差下降。初始数据不随新候选更新。'
SIDE_LIMITS={'overview':512,'detail':384}
EXTRA_VIEWS_PER_VERSION=2
RENDER_EDGE=2561
DSH_RENDER_EDGE=2561
SRGB_PROFILE=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
SYSTEM='你是摄影后期 agent。根据作者成片匹配 RAW 的色彩和影调，不改变裁切、透视、旋转和内容。只用提供的真实图像和原生控件；返回简短公开依据，不输出内部思维过程，未知就说明。\n编辑顺序：第一步做一次大幅全局影调调整（曝光、对比、高光/阴影、白色/黑色、白平衡），先让高光与阴影接近作者；之后再调 2–3 个主色，最后才做局部。每次编辑可同时改多个滑杆，不要只动一两格。\n观察规则：每个新版本自动提供一次总览，影调判断看总览足够。每个版本最多补看 '+str(EXTRA_VIEWS_PER_VERSION)+' 次局部（observation_budget 显示剩余次数和已看区域）；与已看区域重叠过半的请求会被拒绝。side 为长边像素，总览不超过 '+str(SIDE_LIMITS['overview'])+'，局部不超过 '+str(SIDE_LIMITS['detail'])+'。observe 只返回像素，不提供数字。\n每次调用一个 photo_* 工具，一次提交观察判断与下一动作。'

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
class SafetyStop(RuntimeError):pass
class ModelResponseError(SafetyStop):pass
class LocalDispatchDenied(SafetyStop):pass
def reviewed(observation):return bool(observation.get('decision_judgment'))

def require_workflow(round_dir,build_bound=False):
 protocol=read(Path(round_dir)/'protocol.json')
 if protocol.get('workflow')!=WORKFLOW:raise LocalDispatchDenied('Archived timing protocol cannot run in the compact implementation; preserve it and use a separately qualified new round')
 if build_bound and protocol.get('build')!=IMPLEMENTATION_BUILD:raise LocalDispatchDenied('Round was prepared by a different build; preserve it and prepare a new round instead of rewriting its inputs')
 return protocol

def page_rows(rows,args):
 start=args.get('offset',0);end=start+args.get('page_size',16)
 return {'items':rows[start:end],'total':len(rows),'next_offset':end if end<len(rows) else None}

def dispatch_precheck():
 for name in ['lab-timing-transport-hold.json','lab-timing-circuit.json']:
  if (ROOT/'experiments'/name).exists():raise LocalDispatchDenied('Paid execution stopped: '+name)
 policy=ROOT/'experiments/config/lab-paid-dispatch-policy.local.json'
 if not policy.exists() or read(policy).get('enabled') is not True:raise LocalDispatchDenied('No active operator-paid authorization')

def trip_circuit(reason):
 p=ROOT/'experiments/lab-timing-circuit.json';p.parent.mkdir(parents=True,exist_ok=True)
 try:
  with p.open('x',encoding='utf-8') as f:json.dump({'schema':'lab-security/v1','time':time.time(),'reason':str(reason)[:2000],'resume':'Offline diagnosis, regression proof and explicit operator authorization required'},f,ensure_ascii=False,indent=2)
 except FileExistsError:pass

@contextlib.contextmanager
def trajectory_lease(folder):
 p=Path(folder)/'worker.lease';p.parent.mkdir(parents=True,exist_ok=True)
 def cleanup_unknown():
  # Only this lease's contained run/library tree, never ROOT or sibling runs.
  root=p.parent.resolve()
  def unreadable(exc):raise LocalDispatchDenied('Cannot inspect native cleanup state; retain worker lease') from exc
  for directory,dirs,files in os.walk(root,followlinks=False,onerror=unreadable):
   if 'native-cleanup-unknown.json' in files or 'native-cleanup-unknown.json' in dirs:return True
   dirs[:]=[name for name in dirs if not (Path(directory)/name).is_symlink() and not (Path(directory)/name).is_junction() and (Path(directory)/name).resolve().is_relative_to(root)]
  return False
 if cleanup_unknown():raise LocalDispatchDenied('Unknown native cleanup requires reconciliation; worker lease must be retained')
 try:fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
 except FileExistsError as exc:raise LocalDispatchDenied('Trajectory already owned; stale ownership requires manual reconciliation') from exc
 release=False
 try:
  os.write(fd,json.dumps({'pid':os.getpid(),'time':time.time()}).encode());yield
  release=not cleanup_unknown()
 finally:
  os.close(fd)
  if release:p.unlink()

def write(p,x,*,retry_seconds=5.):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.tmp-'+uuid.uuid4().hex);tmp.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 # Windows readers/AV can temporarily deny DELETE sharing during os.replace.
 # Persist the same prepared checkpoint; never replay a native edit to retry IO.
 delay=.02;deadline=time.monotonic()+retry_seconds
 while True:
  try:tmp.replace(p);break
  except PermissionError as exc:
   if time.monotonic()>=deadline:raise SafetyStop('Checkpoint replace deadline reached; prepared temp retained, native action not replayed') from exc
   time.sleep(delay);delay=min(1.,delay*2)
def event(folder,kind,**data):
 with (Path(folder)/'events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps({'time':time.time(),'kind':kind,**data},ensure_ascii=False)+'\n')
def srgb(p,required=False):
 return read_srgb(p,required=required)
def save_srgb_png(image,dest):
 # Pixels have already been converted to sRGB, not merely relabelled.
 image.save(dest,format='PNG',icc_profile=SRGB_PROFILE)
def require_srgb_tag(path):
 return inspect_image(path,require_srgb=True,require_upright=True)
def pixel_box(size,roi):
 if len(roi)!=4 or not all(isinstance(x,(int,float)) and math.isfinite(x) and 0<=x<=1 for x in roi) or roi[0]>=roi[2] or roi[1]>=roi[3]:raise ValueError('Invalid independent ROI')
 box=tuple(min(size[i%2],max(0,math.floor(x*size[i%2]+.5))) for i,x in enumerate(roi))
 if box[2]<=box[0] or box[3]<=box[1]:raise ValueError('ROI smaller than one master pixel')
 return box
def lab_array(rgb):
 a=np.asarray(rgb,dtype=np.float64)/255.;a=np.where(a<=.04045,a/12.92,((a+.055)/1.055)**2.4)
 xyz=a@np.array([[.4124564,.2126729,.0193339],[.3575761,.7151522,.1191920],[.1804375,.0721750,.9503041]])
 xyz/=np.array([.95047,1.,1.08883]);f=np.where(xyz>(6/29)**3,np.cbrt(xyz),xyz/(3*(6/29)**2)+4/29)
 return np.stack([116*f[...,1]-16,500*(f[...,0]-f[...,1]),200*(f[...,1]-f[...,2])],axis=-1)
def measure(image,roi):
 # Tone anchors only need the region median; percentiles and pixel boxes were never used for a decision.
 box=pixel_box(image.size,roi);flat=lab_array(image.crop(box)).reshape(-1,3)
 return {'median':np.median(flat,axis=0).tolist(),'pixels':len(flat)}
def measurements(image,anchors):
 return {a['id']:measure(image,a['source_roi']) for a in anchors}
def lab1(v):return [round(float(x),1) for x in v]
def feedback(current,parent,reference,anchors,include_eval=False):
 rows=[]
 for a in anchors:
  if a.get('evaluation_only') and not include_eval:continue
  ident=a['id'];m=current[ident]['median'];p=parent[ident]['median'];r=reference[ident]['median']
  rows.append({'anchor_id':ident,'role':a['role'],'current':lab1(m),'reference':lab1(r),
    'change_current_minus_parent':lab1(np.array(m)-p),'residual_target_minus_current':lab1(np.array(r)-m) if a['qualification']=='same_region' else None})
 return {'axes':['L*','a*','b*'],'note':'五点区域中位数；只凭角色对应时不给目标残差。ICC→sRGB→D65 CIELAB。','rows':rows}
def iou(a,b):
 w=min(a[2],b[2])-max(a[0],b[0]);h=min(a[3],b[3])-max(a[1],b[1])
 if w<=0 or h<=0:return 0.
 inter=w*h;return inter/((a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter)
def same_region(a,b):return a is b is None or (a is not None and b is not None and iou(a,b)>.5)
def thumb(im,side):
 out=im.copy();out.thumbnail((side,side),Image.Resampling.LANCZOS);out.info.clear();return out
def board(paths,labels,dest,side=850,rois=None,grid=False):
 images=[srgb(p) for p in paths];w=side*len(images);height=side+85
 out=Image.new('RGB',(w,height),'#f5f6f8');d=ImageDraw.Draw(out);font=ImageFont.truetype(FONT,24)
 for i,(im,label) in enumerate(zip(images,labels)):
  shown=thumb(im,side-24);x=i*side+(side-shown.width)//2;y=70+(side-shown.height)//2;out.paste(shown,(x,y));d.text((i*side+12,12),label,fill='#17212c',font=font)
  if grid:
   for k in range(1,10):
    xx=x+shown.width*k/10;yy=y+shown.height*k/10;d.line((xx,y,xx,y+shown.height),fill='#eeeeee',width=1);d.line((x,yy,x+shown.width,yy),fill='#eeeeee',width=1)
   for k in range(10):d.text((x+shown.width*k/10,y),str(k/10),fill='red',font=font)
  if rois:
   for a in rois[i]:
    b=a['roi'];rect=(x+b[0]*shown.width,y+b[1]*shown.height,x+b[2]*shown.width,y+b[3]*shown.height);d.rectangle(rect,outline=a.get('color','#E94A39'),width=3);d.text(rect[:2],a['id'],fill=a.get('color','#E94A39'),font=font)
 dest=Path(dest);dest.parent.mkdir(parents=True,exist_ok=True);save_srgb_png(out,dest)
 return str(dest)

class Native:
 def __init__(self,folder,source):
  self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True);self.source=Path(source);self.lc=LightCraft(ROOT/'external/lightcraft/bin/lightcraft-cli.exe',self.source,self.folder)
 def call(self,name,args):
  t=time.monotonic();result=self.lc.call(name,args);event(self.folder,'native',name=name,arguments=args,seconds=time.monotonic()-t)
  return structured(result) if name!='command_run' else result
 def settings(self):return self.call('get_develop',{})
 def restore(self,settings):
  self.call('set_develop',{'settings':settings,'label':'LAB timing isolated branch'})
  if self.settings()!=settings:raise ValueError('Full settings restore/readback mismatch')
 def render(self,path,longedge=RENDER_EDGE,fmt='png'):
  path=Path(path)
  if path.exists():raise ValueError('Refusing to overwrite actual render '+str(path))
  if fmt not in ('png','jpeg') or type(longedge) is not int or longedge<0:raise ValueError('Unsupported render format or size')
  # Render the same full-resolution RAW path as final delivery. A 2561 export
  # still bins some sensors and can alter narrow clipped fireworks/highlights.
  full=path.with_name(path.name+'.full-'+uuid.uuid4().hex+'.png') if longedge>0 else path
  render_format='png' if full!=path else fmt
  self.call('command_run',{'id':'app.export','params':{'path':str(full),'format':render_format,'longEdge':0,'bitDepth':8,'quality':100,'colorSpace':'srgb','metadata':'none','sharpen':'none'}})
  facts=inspect_image(full,require_srgb=True,require_upright=True,expected_format='PNG' if render_format=='png' else 'JPEG')
  opacity=None
  if hasattr(self,'source'):
   from photo_opacity_contract import apply_opacity
   opacity=apply_opacity(self.source,full,render_format)
  full_dimensions=facts['dimensions']
  if full!=path:
   image=srgb(full,True);image.thumbnail((longedge,longedge),Image.Resampling.LANCZOS)
   if fmt=='png':save_srgb_png(image,path)
   else:image.save(path,format='JPEG',quality=100,subsampling=0,icc_profile=SRGB_PROFILE)
   assert_scaled_geometry(full_dimensions,list(image.size),longedge)
   full.unlink()
  color=inspect_image(path,require_srgb=True,require_upright=True,expected_format='PNG' if fmt=='png' else 'JPEG');dims=color['dimensions']
  event(self.folder,'render',path=str(path),dimensions=dims,original_size=longedge==0,full_raster_dimensions=full_dimensions,raw_render_long_edge=0,opacity=opacity,output_icc=color['profile']);return dims
 def controls(self):return self.call('list_controls',{})
 def apply(self,base,args):
  values=args.get('values') or {};settings=args.get('settings') or {};commands=args.get('commands') or []
  if not(values or settings or commands):raise ValueError('No native edit supplied')
  if any(k.split('.')[0] not in ALLOW for k in values) or set(settings)-ALLOW:raise ValueError('Outside color/tone editing')
  if any(c['id'] not in COMMANDS for c in commands):raise ValueError('Command outside color/tone task')
  self.restore(base)
  controls=self.controls();controls=controls.get('controls',controls) if isinstance(controls,dict) else controls;index={c['id']:c for c in controls}
  for k,v in values.items():
   if k not in index or isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not index[k]['min']<=v<=index[k]['max']:raise ValueError('Invalid native value '+k)
  if values or settings:self.call('set_develop',{'values':values,'settings':settings,'label':'LAB timing candidate'})
  for c in commands:self.call('command_run',c)
  actual=self.settings()
  for k in ['crop','geometry','orientation','optics']:
   if actual.get(k)!=base.get(k):raise ValueError('Out-of-task geometry/optics changed')
  if values:
   got=self.controls();got=got.get('controls',got) if isinstance(got,dict) else got;got={c['id']:c['value'] for c in got}
   for k,v in values.items():
    if k not in got or not math.isclose(got[k],v,abs_tol=1e-5,rel_tol=1e-5):raise ValueError('Setting value not read back: '+k)
  def verify(want,got):
   if isinstance(want,dict):
    for k,v in want.items():verify(v,got[k])
   elif isinstance(want,list):
    if len(want)!=len(got):raise ValueError('Settings length readback mismatch')
    for a,b in zip(want,got):verify(a,b)
   elif isinstance(want,(float,int)) and not isinstance(want,bool):
    if not math.isclose(want,got,abs_tol=1e-5,rel_tol=1e-5):raise ValueError('Settings readback mismatch')
   elif want!=got:raise ValueError('Settings readback mismatch')
  verify(settings,actual);return actual
 def close(self):self.lc.close()

def make_jobs(photo_ids,conditions,repeats):
 if not photo_ids or len(set(photo_ids))!=len(photo_ids) or any(p not in PHOTO_IDS for p in photo_ids):raise ValueError('Choose known unique photos explicitly')
 if not conditions or len(set(conditions))!=len(conditions) or any(c not in CONDITIONS for c in conditions):raise ValueError('Choose known unique conditions explicitly')
 if not isinstance(repeats,int) or repeats<1:raise ValueError('Repeats must be a positive integer')
 rng=random.Random(20261110);jobs=[]
 for ident in photo_ids:
  for repeat in range(1,repeats+1):
   order=list(conditions);rng.shuffle(order)
   jobs.extend({'photo_id':'DSC_'+ident,'condition':condition,'repeat':repeat} for condition in order)
 return jobs

def author_jpg(stem,full_size=False):
 """Author-delivered ordinary JPG, never the oDSC_ RAW-preview sibling. The 512-short-side copy is the default: tone matching needs no more pixels. Full size only when explicitly requested."""
 roots=[(ROOT/'paired photos','author_512_short_side_copy')]
 if full_size:roots=[(Path(os.environ['VISHARNESS_FULL_AUTHOR_ROOT']),'author_original_full_size')]
 for root,role in roots:
  p=root/stem/(stem+'.jpg')
  if p.is_file():return p,role
 raise ValueError('Missing author-delivered ordinary JPG for '+stem+(' (full size explicitly requested)' if full_size else ''))

def prepare(round_dir,photo_ids=None,conditions=None,repeats=1,full_author=False,preview_long_edge=RENDER_EDGE):
 if (round_dir/'protocol.json').exists():raise SafetyStop('Existing protocol is immutable; use a new directory for the compact workflow')
 photo_ids=photo_ids or ['6268'];conditions=conditions or ['visual'];jobs=make_jobs(photo_ids,conditions,repeats)
 round_dir.mkdir(parents=True,exist_ok=True)
 visual_only=conditions==['visual'];development_ids=[] if visual_only else DEV_IDS
 write(round_dir/'protocol.json',{'schema':'lab-compact/v2','workflow':WORKFLOW,'build':IMPLEMENTATION_BUILD,'visual_only':visual_only,'comparison_scope':'Visual-only engineering batch; no LAB sampling.' if visual_only else 'Explicit small engineering batch. Candidate LAB is released before one integrated decision.','model':'deepseek-flash','provider':'deepseek-official','reasoning':'high','max_output_tokens_per_request':4096,'author_reference':'full_size' if full_author else '512_short_side','render_long_edge':preview_long_edge,'side_limits':SIDE_LIMITS,'trajectory_caps':{'extra_views_per_version':EXTRA_VIEWS_PER_VERSION},'hash_verification':False,'requires_full_raw_decode':True,'raw_render_contract':RAW_RENDER_CONTRACT,'jobs':jobs,'photo_ids':photo_ids,'development_ids':development_ids,'condition_names':{c:CONDITIONS[c] for c in conditions},'status':'preparing'})
 for ident in development_ids+photo_ids:
  stem='DSC_'+ident;folder=round_dir/'inputs'/stem;folder.mkdir(parents=True,exist_ok=True)
  if (folder/'input.json').exists():continue
  from photo_source_router import RAW_EXTENSIONS,prepare_source
  sources=[p for p in (ROOT/'paired photos'/stem).iterdir() if p.is_file() and not p.name.startswith('._') and p.suffix.lower() in RAW_EXTENSIONS]
  if len(sources)!=1:raise ValueError('Missing or ambiguous actual RAW for '+stem)
  original_raw=sources[0];author,author_role=author_jpg(stem,full_author)
  preparation=prepare_source(original_raw,folder/'source-preparation')
  raw=Path(preparation['backend_source'])
  raw_render=inspect_dng(raw);write(folder/'raw-render-contract.json',raw_render)
  write(folder/'raw-derivation.json',preparation)
  ref=srgb(author,True);save_srgb_png(ref,folder/'author-srgb.png')
  native=Native(folder,raw)
  try:
   photo=native.call('query_photos',{})['photos'][0]
   write(folder/'backend-source.json',{k:photo.get(k) for k in ['fileName','width','height','kind','previewOnly']})
   if photo.get('previewOnly') or photo.get('kind')!='raw':raise ValueError('RAW decode qualification failed before rendering/model dispatch')
   native.call('command_run',{'id':'develop.reset','params':{}});settings=native.settings();native.restore(settings);dims=native.render(folder/'v0.png',preview_long_edge);original=native.render(folder/'baseline-original.jpg',0,'jpeg')
   if original!=[photo['width'],photo['height']]:raise ValueError('Original export size differs from independent native RAW catalog dimensions')
   write(folder/'initial-settings.json',settings)
   write(folder/'input.json',{'photo_id':stem,'source':str(raw),'original_source_nef':str(original_raw),'original_source_raw':str(original_raw),'source_kind':raw_render['source_kind'],'source_role':'Full typed CFA/LinearRaw editing input; declared fixed display reference is never editable pixels or author target','raw_render_contract':RAW_RENDER_CONTRACT,'raw_render_contract_path':str(folder/'raw-render-contract.json'),'raw_decode_qualified':True,'backend_previewOnly':photo.get('previewOnly'),'author_target':str(author),'author_source_role':author_role,'reference':str(folder/'author-srgb.png'),'reference_role':'photographer_reference','author_dimensions':list(ref.size),'v0':str(folder/'v0.png'),'v0_dimensions':dims,'original_dimensions':original,'catalog_raw_dimensions':[photo['width'],photo['height']],'initial_settings':str(folder/'initial-settings.json'),'reviewed_roles':False,'role_note':'author-srgb.png is the ICC-converted author-delivered ordinary JPG. oDSC_ RAW-preview siblings and backend v0 renders are never the author target.'})
   board([folder/'author-srgb.png',folder/'v0.png'],['作者成片（ICC 转换）','本轮实际后端 v0'],folder/'selection-grid.png',820,grid=True)
   print('prepared',stem,flush=True)
  finally:native.close()

# Five tone anchors per photo, placed on the 512-short-side author JPG and checked against the frame.
# A row is (id, role, author_roi, source_roi, qualification). same_region means the boxes cover the same content;
# role_only means only the role matches, so no target residual is invented. highlight/shadow set exposure and contrast,
# the other 2-3 are the colors that actually set the picture's tone.
ANCHORS={
 '0294':[('highlight','烟火高光',[.27,.08,.33,.15],[.27,.08,.33,.15],'same_region'),('shadow','前景人群',[.47,.86,.62,.96],[.47,.86,.62,.96],'same_region'),('smoke','青烟',[.12,.38,.19,.45],[.12,.38,.19,.45],'same_region'),('sky','夜空',[.89,.05,.96,.18],[.89,.05,.96,.18],'same_region'),('fire','红焰',[.33,.20,.40,.30],[.33,.20,.40,.30],'same_region')],
 '1179':[('highlight','金发高光',[.60,.15,.66,.22],[.60,.15,.66,.22],'same_region'),('shadow','暗背景',[.05,.77,.19,.87],[.05,.77,.19,.87],'same_region'),('skin','肤色',[.425,.43,.45,.48],[.425,.43,.45,.48],'same_region'),('wing','白羽',[.80,.34,.83,.43],[.80,.34,.83,.43],'same_region'),('background','暖色背景',[.02,.12,.12,.25],[.02,.12,.12,.25],'same_region')],
 '3734':[('highlight','钟楼受光',[.25,.26,.285,.32],[.26,.27,.295,.33],'same_region'),('shadow','路面阴影',[.60,.86,.72,.93],[.60,.86,.72,.93],'role_only'),('sky','天空',[.08,.025,.23,.08],[.09,.025,.24,.08],'same_region'),('foliage','树丛',[.70,.47,.79,.51],[.72,.49,.81,.53],'role_only'),('taxi','红出租车',[.38,.84,.44,.88],[.38,.84,.44,.88],'role_only')],
 '3209':[('highlight','楼群灯光',[.67,.60,.76,.71],[.67,.60,.76,.71],'same_region'),('shadow','前景植被',[.88,.83,.96,.94],[.88,.83,.96,.94],'same_region'),('upper_sky','上部夜空',[.18,.04,.36,.15],[.18,.04,.36,.15],'same_region'),('dusk','暮色',[.39,.30,.59,.37],[.39,.30,.59,.37],'same_region'),('water','水面',[.43,.55,.48,.61],[.43,.55,.48,.61],'same_region')],
 '8819':[('highlight','船身受光',[.33,.535,.45,.555],[.25,.55,.30,.575],'role_only'),('shadow','山体',[.10,.13,.25,.22],[.12,.14,.20,.23],'role_only'),('water','水面',[.45,.70,.66,.79],[.30,.73,.42,.83],'role_only'),('building','楼群',[.22,.30,.33,.38],[.22,.33,.27,.43],'role_only'),('sky','云层',[.72,.02,.88,.09],[.72,.02,.88,.09],'role_only')],
 '6268':[('highlight','地平线亮带',[.41,.34,.57,.385],[.41,.34,.57,.385],'same_region'),('shadow','前景暗部',[.42,.76,.50,.86],[.42,.76,.50,.86],'same_region'),('cloud','云层',[.18,.07,.34,.17],[.18,.07,.34,.17],'same_region'),('river','河面',[.36,.85,.385,.94],[.36,.85,.385,.94],'same_region'),('lights','城市灯光',[.47,.63,.58,.68],[.47,.63,.58,.68],'same_region')],
 '7179':[('highlight','云',[.30,.08,.45,.18],[.30,.08,.45,.18],'same_region'),('shadow','山体',[.55,.22,.75,.42],[.55,.22,.75,.42],'same_region'),('sky','天空',[.06,.08,.25,.2],[.06,.08,.25,.2],'same_region'),('water','河面',[.35,.74,.55,.86],[.35,.74,.55,.86],'same_region'),('foliage','近岸树丛',[.01,.41,.07,.46],[.01,.41,.07,.46],'same_region')],
 '1126':[('highlight','白球',[.78,.82,.84,.87],[.78,.82,.84,.87],'same_region'),('shadow','暗背景',[.85,.05,.98,.15],[.85,.05,.98,.15],'same_region'),('skin','肤色',[.38,.25,.41,.29],[.38,.25,.41,.29],'same_region'),('cloth','红衣服',[.64,.425,.68,.445],[.64,.425,.68,.445],'same_region'),('table','台面',[.10,.80,.40,.90],[.10,.80,.40,.90],'same_region')]}
def qualify_anchors(round_dir):
 protocol=require_workflow(round_dir,build_bound=True)
 for ident in protocol.get('development_ids',[])+protocol.get('photo_ids',[]):
  folder=round_dir/'inputs'/('DSC_'+ident);input=read(folder/'input.json')
  if protocol.get('visual_only'):
   for name,value in [('anchors.json',[]),('reference-measurements.json',{}),('v0-measurements.json',{})]:write(folder/name,value)
   input['reviewed_roles']=False;input['anchor_review']='Visual-only: no LAB regions or measurements; independently verify author and v0 roles.';write(folder/'input.json',input)
   continue
  ref=srgb(input['reference']);src=srgb(input['v0']);rows=ANCHORS[ident]
  stored=[{'id':a[0],'role':a[1],'reference_roi':a[2],'source_roi':a[3],'qualification':a[4],'method':'placed_on_frame'} for a in rows]
  write(folder/'anchors.json',stored)
  write(folder/'reference-measurements.json',{a['id']:measure(ref,a['reference_roi']) for a in stored})
  write(folder/'v0-measurements.json',measurements(src,stored))
  board([input['reference'],input['v0']],['作者成片','本轮后端 v0'],folder/'anchor-overview.png',820,rois=[[{'id':a['id'],'roi':a['reference_roi']} for a in stored],[{'id':a['id'],'roi':a['source_roi']} for a in stored]])
  input['reviewed_roles']=False;input['anchor_review']='Five tone anchors placed on the 512-short-side author JPG: highlight, shadow and 2-3 colors that set the picture tone. Boxes must be checked on anchor-overview.png before the round is frozen.';write(folder/'input.json',input)
 print('visual roles require review; LAB sampling disabled' if protocol.get('visual_only') else 'five tone anchors placed; check anchor-overview.png before freezing',flush=True)

# Older rounds still carry evaluation_only/protected; they are read but never projected.
def public_anchors(anchors):return [{k:a[k] for k in ['id','role','qualification'] if k in a} for a in anchors if not a.get('evaluation_only')]

def verify_raw_contract(round_dir):
 protocol=require_workflow(round_dir,build_bound=True)
 rows=[]
 for ident in protocol['development_ids']+protocol['photo_ids']:
  folder=round_dir/'inputs'/('DSC_'+ident);inp=read(folder/'input.json');p=Path(inp['source'])
  display_contract=inspect_dng(p)
  geometry=display_contract['geometry'];tags=geometry['camera_tags']
  color_tags={'camera_model':tags.get('UniqueCameraModel',tags.get('Model')),
              'color_matrix_1':tags.get('ColorMatrix1',tags.get('ColorMatrix2')),
              'as_shot_neutral':tags.get('AsShotNeutral'),
              'baseline_exposure':tags.get('BaselineExposure',[0])[0]}
  if protocol.get('raw_render_contract')!=RAW_RENDER_CONTRACT or inp.get('raw_render_contract')!=RAW_RENDER_CONTRACT:
   raise ValueError('RAW display baseline contract missing; prepare a new input instead of relabelling old evidence')
  if protocol.get('runner')=='dsh-native/v1':
   if (not color_tags['camera_model'] or not color_tags['color_matrix_1'] or len(color_tags['color_matrix_1'])!=9 or
       not all(math.isfinite(v) for v in color_tags['color_matrix_1']) or
       not color_tags['as_shot_neutral'] or len(color_tags['as_shot_neutral'])!=3 or
       not all(math.isfinite(v) and v>0 for v in color_tags['as_shot_neutral']) or
       not math.isfinite(color_tags['baseline_exposure'])):
    raise ValueError('DNG camera colour matrix or as-shot white balance is missing or invalid')
   for image_path in (inp['reference'],inp['v0'],str(folder/'baseline-original.jpg')):require_srgb_tag(image_path)
  assert inp['raw_decode_qualified'] and inp['backend_previewOnly'] is None
  assert_same_geometry(display_contract['expected_developed_dimensions'],inp['catalog_raw_dimensions'],context='Independent DNG/catalog dimensions')
  assert_same_geometry(display_contract['expected_developed_dimensions'],inp['original_dimensions'],context='Independent DNG/full export dimensions')
  row={'photo_id':inp['photo_id'],'source_original':inp.get('original_source_nef',inp.get('original_source_raw')),
       'compatible_dng':str(p),'source_kind':display_contract['source_kind'],
       'selected_raw_ifd':{'offset':display_contract['selected_ifd_offset'],'index':display_contract['selected_ifd_index'],
                           'sample_layout':display_contract['sample_layout'],'geometry':geometry},
       'camera_color_metadata':color_tags,'backend_source':read(folder/'backend-source.json'),
       'original_dimensions':inp['original_dimensions'],'display_contract':display_contract,'passed':True,
       'proof':'Selected full CFA or LinearRaw data, declared camera colour, fixed source display and backend previewOnly=None; no hash verification'}
  write(folder/'raw-decode-contract.json',row);rows.append(row)
 write(round_dir/'qualification/raw-decode.json',{'passed':True,'photos':rows});print(str(len(rows))+' actual typed RAW decode contracts passed',flush=True)
def initial_pack(folder):
 a=read(folder/'anchors.json');m=read(folder/'v0-measurements.json');r=read(folder/'reference-measurements.json')
 return {'principles':PRINCIPLES,'initial_only':True,'version':'v0',**feedback(m,m,r,a)}

class Trajectory:
 def __init__(self,round_dir,job):
  self.protocol=require_workflow(round_dir,build_bound=True)
  self.round=round_dir;self.job=job;self.input_folder=round_dir/'inputs'/job['photo_id'];self.input=read(self.input_folder/'input.json');self.anchors=read(self.input_folder/'anchors.json')
  if not self.input.get('raw_decode_qualified'):raise ValueError('This source has not passed full RAW decode qualification; preserve old preview runs, do not resume as RAW')
  self.folder=round_dir/'editing'/job['photo_id']/('r'+str(job['repeat']))/job['condition'];self.folder.mkdir(parents=True,exist_ok=True)
  self.file=self.folder/'state.json';self.native=None
  self.state=read(self.file) if self.file.exists() else {'job':job,'workflow':self.protocol.get('workflow'),'status':'working','current':'v0','versions':{'v0':{'settings':read(self.input['initial_settings']),'master':self.input['v0'],'parent':None,'measurements':read(self.input_folder/'v0-measurements.json')}},'observations':[],'history':[],'reviewed':[],'export':None,'visible_packets':[]}
  self.reference=read(self.input_folder/'reference-measurements.json')
 def save(self):write(self.file,self.state)
 def backend(self):
  if self.native is None:self.native=Native(self.folder,self.input['source'])
  return self.native
 def context(self):
  # Archive size must not determine the default request size. No growing index.
  o=self.state['observations'][-1] if self.state['observations'] else None;history=self.state['history'];versions=self.state['versions'];packets=self.state['visible_packets'];active=self.state['current']
  ids={'v0',active,versions[active].get('parent')}
  if o:ids.update([o['version'],o.get('parent')])
  if self.state.get('export'):ids.add(self.state['export']['version'])
  ids.discard(None)
  last=next(({'history_index':i,**{k:history[i][k] for k in ['action','version','parent'] if k in history[i]},**{k:history[i].get('arguments',{}).get(k) for k in ['rationale','predicted_effect']}} for i in range(len(history)-1,-1,-1) if history[i].get('action') in ['edit','observe','discover','finish']),None)
  latest=next((p for p in reversed(packets) if o and p['observation_id']==o['id']),None) if self.job['condition'] in ['verify','combined'] else None
  export=self.state.get('export')
  value={'task':'匹配作者成片颜色、影调及共同摄影关系，保持源图几何。','anchors':public_anchors(self.anchors),
   'delivery_contract':'一次提交本次公开观察和下一动作；可交付 partial/unresolved 并说明剩余问题，不要求证明全局最优。工程故障转诊断，不通过重复提交恢复。',
   'current':active,'versions':{k:{'parent':versions[k]['parent'],'reviewed':k in self.state['reviewed']} for k in sorted(ids) if k in versions},
   'current_settings':{k:v for k,v in versions[active]['settings'].items() if k in ALLOW},
   'current_observation':{k:o.get(k) for k in ['id','version','parent','artifact','question','numeric_disclosed']} if o else None,
   'last_action':last,'tool_result':self.state.get('tool_result'),'disclosed_evidence':[latest] if latest else [],
   'active_export':None if not export else {'version':export['version'],'review_complete':any(r['artifact']=='export' and r['path']==export['path'] and reviewed(r) for r in self.state['observations'])},
   'observation_budget':self.budget(o['version'] if o else active)}
  if o and o['version']!=active:value['observed_version_settings']={k:v for k,v in versions[o['version']]['settings'].items() if k in ALLOW}
  if self.job['condition'] in ['prior','combined']:
   if not hasattr(self,'_initial_card'):self._initial_card=initial_pack(self.input_folder)
   value['initial_LAB']=self._initial_card
  return value
 def budget(self,version):
  # Only this version's views, so the context stays flat as the archive grows.
  seen=[o for o in self.state['observations'] if o['version']==version and o.get('artifact','canonical')=='canonical']
  used=sum(1 for o in seen if o.get('requested'))
  return {'version':version,'extra_views_left':max(0,EXTRA_VIEWS_PER_VERSION-used),'seen_regions':[{k:o.get('view_spec',{}).get(k) for k in ['candidate_roi','reference_roi']} for o in seen]}
 def recall(self,args):
  result={'history':[],'versions':{},'observations':[],'disclosed_evidence':[]}
  if args.get('query'):
   query=args['query'].casefold();result['query']=args['query'];matches=[{'history_index':i,**{k:h[k] for k in ['version','phase','action','observation_id'] if k in h}} for i,h in enumerate(self.state['history']) if query in json.dumps({k:v for k,v in h.items() if k!='settings_readback'},ensure_ascii=False).casefold()]
   start=args.get('offset',0);end=start+args.get('page_size',16);result.update(query_matches=matches[start:end],query_total=len(matches),query_next_offset=end if end<len(matches) else None)
  for i in args.get('history_indices',[]):
   if not isinstance(i,int) or not 0<=i<len(self.state['history']):raise ValueError('Unknown history index')
   result['history'].append({'history_index':i,**self.state['history'][i]})
  for vid in args.get('version_ids',[]):
   v=self.state['versions'][vid];result['versions'][vid]={k:v[k] for k in ['parent','settings']}
  for oid in args.get('observation_ids',[]):
   o=next(o for o in self.state['observations'] if o['id']==oid);result['observations'].append({k:v for k,v in o.items() if k not in ['path','views','report_board']})
  for oid in args.get('evidence_ids',[]):
   p=next((p for p in self.state['visible_packets'] if p['observation_id']==oid),None) if self.job['condition'] in ['verify','combined'] else None
   result['disclosed_evidence'].append(p if p else {'observation_id':oid,'available':False,'reason':'No previously disclosed packet; cannot request hidden numbers'})
  return result
 def ask(self,instruction,images=None,phase='step'):
  retrieved=None;seen_history=[]
  while True:
   context=self.context()
   stable={k:context.pop(k) for k in ['task','anchors','delivery_contract','initial_LAB'] if k in context}
   if retrieved is not None:context['requested_archive']=retrieved
   text=json.dumps(context,ensure_ascii=False,separators=(',',':'))+'\n'+instruction
   result=api(self.folder,SYSTEM,text,images,phase=phase,stable_context=stable,monitor_round=self.round if getattr(self,'protocol',{}).get('visual_only') else None)
   result=normalize_action(result,phase);validate_action(result,phase);event(self.folder,'model_action',result=result)
   if result['action']=='history':
    if result['arguments'] in seen_history:raise SafetyStop('Cyclic archive requests without a new decision; no further model call')
    seen_history.append(copy.deepcopy(result['arguments']))
    fetched=self.recall(result['arguments'])
    retrieved=fetched;event(self.folder,'archive_retrieval',arguments=result['arguments']);continue
   if any(h.get('action_error') and h.get('proposed')==result and h.get('observation_id')==self.state['observations'][-1]['id'] for h in self.state['history']):raise RuntimeError('Identical failed proposal without new observation; preserve checkpoint for control diagnosis, not repeated paid retries')
   return result
 def observe(self,version=None,path=None,artifact='canonical',args=None,requested=False):
  args=args or {};version=version or self.state['current'];v=self.state['versions'][version];path=path or v['master']
  parent=v if artifact=='export' else self.state['versions'].get(v['parent'])
  question=args.get('question','观察颜色影调')
  view={k:args.get(k) for k in ['reference_roi','candidate_roi']}
  for roi in view.values():
   if roi is not None:pixel_box((10000,10000),roi)
  limit=SIDE_LIMITS['detail' if any(view.values()) else 'overview'];view['side']=min(int(args.get('side',limit)),limit)
  # Tone needs few views: reject overlapping repeats and cap model-requested views per version.
  same=[o for o in self.state['observations'] if o['version']==version and o['path']==str(path) and o['artifact']==artifact]
  if not getattr(self,'allow_overlapping_views',False) and any(all(same_region(o.get('view_spec',{}).get(k),view[k]) for k in ['reference_roi','candidate_roi']) for o in same):raise ValueError('Identical observation already exists (same version, region overlap above half); use its evidence instead of paying for a repeat view')
  if requested and self.budget(version)['extra_views_left']<=0:raise ValueError('Extra view budget for '+version+' is used up; edit, finish or view another version')
  oid='o'+str(len(self.state['observations'])+1).zfill(4);out=self.folder/'observations'/oid;out.mkdir(parents=True,exist_ok=True)
  inputs=[self.input['reference']]+([parent['master']] if parent else [])+[path]
  labels=['作者成片（独立画幅）']+([('同版本预览 '+version if artifact=='export' else '父版本 '+v['parent'])] if parent else [])+['当前 '+version+(' 实际导出 JPEG' if artifact=='export' else '')]
  board(inputs,labels,out/'report-board.png',768)
  views=[]
  for i,(source,label) in enumerate(zip(inputs,labels)):
   im=srgb(source,required=self.protocol.get('runner')=='dsh-native/v1');roi=view['reference_roi' if i==0 else 'candidate_roi'];box=pixel_box(im.size,roi) if roi else None
   if box:im=im.crop(box)
   if i>0 and not box and self.input.get('original_dimensions'):
    shown,resize_facts=canonical_preview(im,self.input['original_dimensions'],view['side'])
   else:shown=thumb(im,view['side'])
   p=out/f'view-{i}.png';save_srgb_png(shown,p);views.append((str(p),label+(' 局部' if box else ' 总览')))
  if parent:
   # Parent and current share one board so the change is read side by side.
   paired=board([views[1][0],views[2][0]],[labels[1],labels[2]],out/'delivered-before-after.png',min(view['side'],512))
   save_srgb_png(thumb(srgb(paired),512),paired)
   delivered=[views[0],(paired,'左为父版本，右为当前版本；作者图单独提供')]
  else:delivered=views
  record={'id':oid,'version':version,'parent':version if artifact=='export' else v['parent'],'artifact':artifact,'path':str(path),'question':question,'views':delivered,'report_board':str(out/'report-board.png'),'view_spec':view,'requested':requested,'decision_judgment':None,'numeric_disclosed':False}
  self.state['observations'].append(record);self.save()
  return record
 def disclose(self,o):
  enabled=self.job['condition'] in ['verify','combined'] and (o['version']!='v0' or o['artifact']=='export')
  if not enabled or o['numeric_disclosed']:return
  current=self.state['versions'][o['version']]['measurements'] if o['artifact']=='canonical' else measurements(srgb(o['path']),self.anchors)
  parent=self.state['versions'].get(o['parent'],self.state['versions']['v0'])['measurements']
  packet={'observation_id':o['id'],'version':o['version'],'parent':o['parent'],'disclosure_stage':'before_integrated_decision','released_after_history_index':len(self.state['history']),**feedback(current,parent,self.reference,self.anchors)}
  self.state['visible_packets'].append(packet);o['numeric_disclosed']=True;self.save()
 def record_decision(self,o,result):
  judgment=result['arguments']['review'];o['decision_judgment']=judgment
  self.state['history'].append({'observation_id':o['id'],'version':o['version'],'phase':'integrated_decision','judgment':judgment,'next_action':result['action']})
  self.state['reviewed']=list(dict.fromkeys(self.state['reviewed']+[o['version']]));self.save()
 def run(self):
  with trajectory_lease(self.folder):return self._run()
 def _run(self):
  if self.state['status']=='complete':return self.state
  if getattr(self,'protocol',{}).get('workflow')!=WORKFLOW or self.state.get('workflow')!=WORKFLOW:raise LocalDispatchDenied('Legacy checkpoints are read-only under the new workflow')
  if self.state['status'] not in ['working','paused_authorization']:raise LocalDispatchDenied('Diagnosed checkpoints require offline reconciliation, not automatic retry')
  dispatch_precheck()
  try:
   if self.state.get('pending'):raise SafetyStop('Pending native outcome requires offline reconciliation; do not restore, discard or replay it automatically')
   self.state['status']='working';self.save()
   if not self.state['observations']:self.observe()
   while self.state['status']=='working':
    o=self.state['observations'][-1]
    self.disclose(o)
    instruction='一次调用提交 review（visible_comparison、remaining_gaps、uncertainty）及下一动作。先比较高光、阴影和主色与作者的差距。edit 给 base_version、values、rationale、predicted_effect；observe 只在总览看不清时用，给 version、question 与 ROI，受 observation_budget 限制；finish 给 selected_version、rationale、goal_status、remaining_gaps、stop_basis，允许 partial/unresolved。首次 finish 导出 JPEG，看过实际 JPEG 后 confirm_export=true。工程异常停止诊断，不靠反复提交恢复。'
    if not any(h.get('action')=='edit' for h in self.state['history']):instruction+=' 尚无编辑：除非需要确认颜色，下一步应直接做一次大幅全局影调调整。'
    if self.state['export']:instruction+=' 当前已导出版本 '+self.state['export']['version']+'，若实际 JPEG 复核可接受，finish 时给 confirm_export=true。'
    result=self.ask(instruction,o['views'],phase='step')
    result=normalize_action(result,'step');validate_action(result,'step');action=result['action'];args=result['arguments'];self.record_decision(o,result)
    try:
     if action=='observe':self.observe(args.get('version',self.state['current']),args=args,requested=True)
     elif action=='discover':
      controls=self.backend().controls();commands=read(ROOT/'experiments/config/backend-evidence-2026-10-28/commands.json');query=str(args.get('query','')).lower();tokens=re.findall(r'[\w]+',query)
      rows={kind:[r for r in items if (r['id'].split('.')[0] in ALLOW if kind=='controls' else r['id'] in COMMANDS) and (not tokens or any(t in json.dumps(r).lower() for t in tokens))] for kind,items in [('controls',controls.get('controls',controls) if isinstance(controls,dict) else controls),('commands',commands)]}
      page={k:page_rows(v,args) for k,v in rows.items()}
      if any(h.get('action')=='discover' and h.get('version')==self.state['current'] and h.get('query')==query and h.get('offset',0)==args.get('offset',0) for h in self.state['history']):raise SafetyStop('Repeated discovery at unchanged version; retrieve the recorded result')
      self.state['history'].append({'action':'discover','version':self.state['current'],'query':query,'offset':args.get('offset',0),'result':page});self.state['tool_result']={'kind':'discover','query':query,**page}
     elif action=='edit':
      base=args.get('base_version',self.state['current'])
      if base not in self.state['reviewed']:raise ValueError('Base must have a recorded integrated review before editing')
      if not args.get('rationale') or not args.get('predicted_effect'):raise ValueError('Public rationale and prediction required before actual render')
      operation_fields=['values','settings','commands']
      if any(h.get('action')=='edit' and h.get('parent')==base and all(h['arguments'].get(k)==args.get(k) for k in operation_fields) for h in self.state['history']):raise ValueError('Native operation already tested from this base; use its evidence instead of repeating it')
      operation='op_'+uuid.uuid4().hex;self.state['pending']={'operation_id':operation,'arguments':args,'base':base};self.save();actual=self.backend().apply(self.state['versions'][base]['settings'],args)
      if actual==self.state['versions'][base]['settings']:raise ValueError('Actual full settings equal base; no new candidate rendered. Diagnose control response or choose a different hypothesis.')
      seq=len(self.state['versions']);version='v'+str(seq);path=self.folder/f'{version}.png'
      while path.exists():seq+=1;version='v'+str(seq);path=self.folder/f'{version}.png'
      self.backend().render(path)
      m={} if self.protocol.get('visual_only') else measurements(srgb(path),self.anchors);self.state['versions'][version]={'settings':actual,'master':str(path),'parent':base,'measurements':m,'operation_id':operation};self.state['current']=version;self.state['export']=None;self.state.pop('pending',None)
      self.state['history'].append({'action':'edit','version':version,'parent':base,'arguments':args,'settings_readback':actual});self.state.pop('tool_result',None);self.save()
      before=srgb(self.state['versions'][base]['master']);after=srgb(path)
      if before.size==after.size and ImageChops.difference(before,after).getbbox() is None:
       self.state.update(status='engineering_diagnosis_required',interruption='Actual rendered pixels equal the base at delivered resolution; candidate preserved for response diagnosis, no further paid decision');self.save();event(self.folder,'identical_pixels',version=version,base=base);trip_circuit('Identical rendered pixels for '+version+' from '+base);break
      self.observe()
     elif action=='finish':
      version=args.get('selected_version',self.state['current'])
      if version not in self.state['reviewed']:raise ValueError('Select a reviewed version')
      if args.get('confirm_export'):
       export=self.state['export']
       if not export or export['version']!=version or not any(o['artifact']=='export' and o['path']==export['path'] and reviewed(o) for o in self.state['observations']):raise ValueError('Review actual current JPEG first')
       self.state.update(status='complete',selected_version=version,final_rationale=args.get('rationale'),model_goal_claim=args.get('goal_status','not_explicit'),model_remaining_gaps=args.get('remaining_gaps'),model_stop_basis=args.get('stop_basis'),author_accepted=None);self.save();break
      path=self.folder/('export_'+uuid.uuid4().hex[:10]+'.jpg');self.state['pending']={'operation_id':'op_'+uuid.uuid4().hex,'kind':'export','base':version,'path':str(path)};self.save()
      self.backend().restore(self.state['versions'][version]['settings']);dims=self.backend().render(path,0,'jpeg')
      if dims!=self.input['original_dimensions']:raise ValueError('Original-size export mismatch')
      self.state['current']=version;self.state['export']={'version':version,'path':str(path),'dimensions':dims};self.state.pop('tool_result',None);self.observe(version,path,'export');self.state.pop('pending');self.save()
     elif action=='unable':
      self.state.update(status='diagnosed_unfinished',diagnosis=args);self.save();break
     else:raise ValueError('Choose an editing workflow action; reviews already recorded')
    except Exception as exc:
     self.state['history'].append({'action_error':str(exc),'proposed':result,'observation_id':o['id']});event(self.folder,'action_error',error=str(exc),proposed=result)
     if self.state.get('pending'):self.state['pending']['outcome']='unknown; reconcile before any native restore or replay'
     self.save();raise SafetyStop('Action rejected; checkpoint requires diagnosis before another paid proposal: '+str(exc)) from exc
    self.save()
   print('trajectory',self.job,self.state['status'],len(self.state['versions'])-1,'edits',flush=True)
   return self.state
  except Exception as exc:
   if not isinstance(exc,LocalDispatchDenied):trip_circuit(str(exc))
   if self.state.get('status')!='engineering_diagnosis_required':self.state.update(status='paused_authorization' if isinstance(exc,LocalDispatchDenied) else 'engineering_diagnosis_required',interruption=str(exc))
   else:self.state['interruption']=str(exc)
   self.save();event(self.folder,'interruption',error=str(exc));print('interrupted',self.job,str(exc),flush=True);return self.state
  finally:
   if self.native:
    try:self.native.close()
    except Exception as exc:
     trip_circuit('Native cleanup failure: '+str(exc));event(self.folder,'native_cleanup_failure',error=str(exc));raise SafetyStop('Native cleanup failed; no further paid dispatch until reconciliation') from exc

def run_editing(round_dir,workers=1,development=False,only_photo=None):
 require_workflow(round_dir,build_bound=True)
 dispatch_precheck()
 frozen=round_dir/'qualification/frozen.json'
 if not frozen.exists():raise ValueError('Finish qualification/freeze before dispatch')
 snapshot=read(frozen)
 if snapshot.get('build')!=IMPLEMENTATION_BUILD or snapshot.get('status')!='qualified':raise LocalDispatchDenied('Runtime build differs from the qualified snapshot')
 jobs=read(round_dir/'protocol.json')['jobs']
 if development:jobs=[{'photo_id':'DSC_1126','condition':c,'repeat':'dev'} for c in CONDITIONS]
 else:
  if not jobs:raise ValueError('Empty batch cannot dispatch')
  if only_photo:
   jobs=[j for j in jobs if j['photo_id']=='DSC_'+only_photo]
   if not jobs:raise ValueError('Photo is outside the frozen batch')
  jobs=[j for j in jobs if not (round_dir/'editing'/j['photo_id']/('r'+str(j['repeat']))/j['condition']/'state.json').exists() or read(round_dir/'editing'/j['photo_id']/('r'+str(j['repeat']))/j['condition']/'state.json')['status']!='complete']
 if read(round_dir/'protocol.json').get('visual_only') and workers!=1:raise LocalDispatchDenied('Visual spending monitor requires exactly one worker')
 def run(job):return Trajectory(round_dir,job).run()
 with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:list(pool.map(run,jobs))

def stimulus(base,kind):
 if kind=='noop':return None
 if kind=='exposure':return {'values':{'light.exposure':base['light']['exposure']-.75}}
 if kind=='white_balance':return {'values':{'wb.temp':base['wb']['temp']+600},'settings':{'wb':{'mode':'custom'}}}
 if kind=='blue_saturation':return {'values':{'mixer.blue.sat':base['mixer']['blue']['sat']+20}}
 raise ValueError(kind)

def qualify_native(round_dir):
 protocol=require_workflow(round_dir,build_bound=True)
 if protocol.get('visual_only'):
  results=[]
  for ident in protocol['photo_ids']:
   stem='DSC_'+ident;folder=round_dir/'inputs'/stem;inp=read(folder/'input.json');base=read(inp['initial_settings']);before=srgb(inp['v0']);out=round_dir/'qualification'/stem;out.mkdir(parents=True,exist_ok=True);native=Native(out,inp['source'])
   try:
    for kind in ['noop','exposure','white_balance','blue_saturation']:
     native.restore(base);args=stimulus(base,kind)
     if args:native.apply(base,args)
     dest=out/(kind+'.png')
     if dest.exists():raise SafetyStop('Existing native stimulus requires inspection; do not blindly replay')
     dims=native.render(dest,protocol.get('render_long_edge',RENDER_EDGE));after=srgb(dest)
     changed=before.size==after.size and ImageChops.difference(before,after).getbbox() is not None
     if dims!=inp['v0_dimensions'] or (kind=='noop' and changed) or (kind=='exposure' and not changed):raise SafetyStop('Native visual qualification failed for '+stem+' '+kind)
     results.append({'photo_id':stem,'kind':kind,'parameters':args,'readback':native.settings(),'geometry':dims==inp['v0_dimensions'],'pixels_changed':changed})
   finally:native.close()
  if len(results)!=4*len(protocol['photo_ids']):raise SafetyStop('Incomplete native visual qualification')
  write(round_dir/'qualification/native.json',results)
  write(round_dir/'qualification/contracts.json',{'passed':True,'checks':['selected photos only','four native stimuli read back per photo','noop stable and exposure changes pixels','geometry unchanged','no LAB sampling'],'native_results':len(results),'model_requests':0})
  print('native visual qualification passed',flush=True);return
 results=[]
 for ident in DEV_IDS:
  stem='DSC_'+ident;folder=round_dir/'inputs'/stem;input=read(folder/'input.json');base=read(input['initial_settings']);anchors=read(folder/'anchors.json');before=read(folder/'v0-measurements.json');out=round_dir/'qualification'/stem;out.mkdir(parents=True,exist_ok=True);native=Native(out,input['source'])
  try:
   for kind in ['noop','exposure','white_balance','blue_saturation']:
    native.restore(base);args=stimulus(base,kind)
    if args:native.apply(base,args)
    dest=out/(kind+'.png')
    if not dest.exists():native.render(dest)
    after=measurements(srgb(dest),anchors)
    results.append({'photo_id':stem,'kind':kind,'parameters':args,'readback':native.settings(),'measurements':after,'changes':{k:None if after[k]['median'] is None or before[k]['median'] is None else (np.array(after[k]['median'])-before[k]['median']).tolist() for k in before},'geometry':list(srgb(dest).size)==input['v0_dimensions']})
  finally:native.close()
 assert all(r['geometry'] for r in results),'Native stimulus changed canvas dimensions'
 write(round_dir/'qualification/native.json',results)
 for condition in CONDITIONS:
  t=Trajectory(round_dir,{'photo_id':'DSC_1126','condition':condition,'repeat':'projection-check'});ctx=t.context()
  assert ('initial_LAB' in ctx)==(condition in ['prior','combined'])
  assert all('measurements' not in v for v in ctx['versions'].values())
 write(round_dir/'qualification/contracts.json',{'passed':True,'checks':['all four native stimuli read back','geometry unchanged','initial card projection per condition','private measurements excluded from state context'],'native_results':len(results),'model_requests':0})
 print('native qualification passed',flush=True)

def freeze(round_dir):
 protocol=require_workflow(round_dir,build_bound=True)
 assert read(round_dir/'qualification/contracts.json')['passed']
 assert read(round_dir/'qualification/raw-decode.json')['passed']
 protocol=read(round_dir/'protocol.json');assert protocol['jobs'],'Empty batch cannot qualify'
 for ident in protocol['development_ids']+protocol['photo_ids']:
  f=round_dir/'inputs'/('DSC_'+ident);assert read(f/'input.json')['reviewed_roles']
 write(round_dir/'qualification/frozen.json',{'time':time.time(),'build':IMPLEMENTATION_BUILD,'workflow':WORKFLOW,'protocol':protocol['schema'],'code_location':str(ROOT/'src'),'code_policy':'Current source only; build changes require a new round. No source-version copies or hashes.','photography_acceptance_thresholds':None,'status':'qualified'})
 print('frozen',round_dir,flush=True)

def judge_call(folder,system,context,images,phase):
 return api(folder,system,context,images,phase=phase)['arguments']

def prepare_diagnostics(round_dir):
 protocol=require_workflow(round_dir,build_bound=True)
 for ident in protocol['photo_ids']:
  stem='DSC_'+ident;input_folder=round_dir/'inputs'/stem;input=read(input_folder/'input.json');base=read(input['initial_settings']);anchors=read(input_folder/'anchors.json');before=read(input_folder/'v0-measurements.json');ref=read(input_folder/'reference-measurements.json');out=round_dir/'diagnostic-images'/stem;out.mkdir(parents=True,exist_ok=True)
  native=Native(out,input['source'])
  try:
   for kind in ['noop','exposure','white_balance','blue_saturation']:
    if (out/(kind+'.json')).exists():continue
    native.restore(base);args=stimulus(base,kind)
    if args:native.apply(base,args)
    path=out/(kind+'.png');native.render(path);m=measurements(srgb(path),anchors);packet=feedback(m,before,ref,anchors)
    preview=out/(kind+'-before-after.png');board([input['v0'],path],['实际前图','实际后图'],preview,SIDE_LIMITS['overview'])
    author=out/'author-view.png'
    if not author.exists():thumb(srgb(input['reference']),SIDE_LIMITS['overview']).save(author)
    write(out/(kind+'.json'),{'kind':'known_action_consequence_not_author_target','stimulus':kind,'parameters':args,'readback':native.settings(),'after':str(path),'measurements':m,'packet':packet,'images':[(str(author),'作者目标，画幅独立'),(str(preview),'同源前图在左、后图在右；判断真实变化')],'generated_by':'actual LightCraft native render'})
    print('diagnostic images',stem,kind,flush=True)
  finally:native.close()

DIAG_SYSTEM='你是图像观察诊断员。作者目标与实际前后图职责不同。只报告图像证据及提供的数值，允许不确定；不猜生成配方，不输出内部思维过程。调用 photo_judge 提交明确的公开判断参数。'
DIAG_FORMAT='arguments 包含 visible_comparison、anchor_axes 数组（每项 anchor_id、L、a、b，方向只能 increase/decrease/same/uncertain）、changed（true/false/null）、next_decision（continue/keep/revert/inspect）、rationale、uncertainty。anchor_axes 的统计对象是每个所列区域的中位 Lab 向量，方向为实际后图减前图；a增大偏红、b增大偏黄。changed 另行描述全图是否看见变化；部分细节改变可能不改变区域中位数。不要将作者目标减后图当成前后变化；不能判定区域中位方向时保留 uncertain。'
def run_diagnostics(round_dir,workers=1):
 dispatch_precheck()
 jobs=[]
 for ident in require_workflow(round_dir,build_bound=True)['photo_ids']:
  for kind in ['noop','exposure','white_balance','blue_saturation']:
   for timing in ['visual_revisit','numbers_first','numbers_after']:
    for repeat in [1,2]:jobs.append((ident,kind,timing,repeat))
 random.Random(917).shuffle(jobs)
 def execute(job):
  ident,kind,timing,repeat=job;folder=round_dir/'diagnostics'/('DSC_'+ident)/kind/timing/str(repeat);folder.mkdir(parents=True,exist_ok=True);file=folder/'result.json'
  if file.exists() and read(file).get('status')=='complete':return
  data=read(round_dir/'diagnostic-images'/('DSC_'+ident)/(kind+'.json'));anchors=read(round_dir/'inputs'/('DSC_'+ident)/'anchors.json');context={'task':'判断实际同源前后图的变化及下一步处置，作者成片仅说明编辑目标。','anchors':public_anchors(anchors)}
  result=read(file) if file.exists() else {'job':job,'status':'working'}
  if result['status'] not in ['working','paused_authorization']:raise LocalDispatchDenied('Diagnosed evaluation requires reconciliation; no automatic repeat')
  try:
   if 'initial' not in result:
    first={**context,**({'numeric_packet':data['packet']} if timing=='numbers_first' else {})}
    result['initial']=judge_call(folder,DIAG_SYSTEM,json.dumps(first,ensure_ascii=False)+'\n第一次判断。'+DIAG_FORMAT,data['images'],'diagnostic');write(file,result)
   second={**context,'initial_judgment':result['initial'],**({'numeric_packet':data['packet']} if timing!='visual_revisit' else {})}
   result['final']=judge_call(folder,DIAG_SYSTEM,json.dumps(second,ensure_ascii=False)+'\n第二次复核。说明维持或修订初判的证据。'+DIAG_FORMAT,data['images'],'diagnostic');result['status']='complete';write(file,result)
  except Exception as exc:
   if not isinstance(exc,LocalDispatchDenied):trip_circuit(str(exc))
   result.update(status='paused_authorization' if isinstance(exc,LocalDispatchDenied) else 'engineering_diagnosis_required',interruption=str(exc));write(file,result)
  print('diagnostic',job,result['status'],flush=True)
 def run(job):
  ident,kind,timing,repeat=job;folder=round_dir/'diagnostics'/('DSC_'+ident)/kind/timing/str(repeat)
  with trajectory_lease(folder):
   try:return execute(job)
   except LocalDispatchDenied:raise
   except Exception as exc:trip_circuit(str(exc));raise
 with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:list(pool.map(run,jobs))

def run_blind(round_dir,workers=1):
 dispatch_precheck()
 import itertools
 jobs=[]
 groups={}
 for job in require_workflow(round_dir,build_bound=True)['jobs']:groups.setdefault((job['photo_id'],job['repeat']),[]).append(job['condition'])
 for (photo_id,repeat),conditions in groups.items():
  for pair in itertools.combinations(conditions,2):
   for swap in [False,True]:jobs.append((photo_id.removeprefix('DSC_'),repeat,pair,swap))
 random.Random(743).shuffle(jobs)
 def execute(job):
  ident,repeat,pair,swap=job;stem='DSC_'+ident;folder=round_dir/'blind-review'/stem/('r'+str(repeat))/('-'.join(pair))/('swap' if swap else 'forward');folder.mkdir(parents=True,exist_ok=True);file=folder/'result.json'
  if file.exists() and read(file).get('status')=='complete':return
  if file.exists() and read(file).get('status') not in ['working','paused_authorization']:raise LocalDispatchDenied('Diagnosed evaluation requires reconciliation; no automatic repeat')
  ordered=list(reversed(pair)) if swap else list(pair);states=[read(round_dir/'editing'/stem/('r'+str(repeat))/c/'state.json') for c in ordered]
  if any(s['status']!='complete' for s in states):write(file,{'status':'not_evaluable','reason':'One or both assigned trajectories incomplete','job':job});return
  input=read(round_dir/'inputs'/stem/'input.json');anchors=read(round_dir/'inputs'/stem/'anchors.json')
  # Opaque labels in delivered images/text; treatment mapping stays report-side.
  images=[]
  for name,path in [('作者目标',input['reference']),('共同起点',input['v0']),('候选 左',states[0]['export']['path']),('候选 右',states[1]['export']['path'])]:
   dest=folder/(uuid.uuid4().hex[:12]+'.png');thumb(srgb(path),SIDE_LIMITS['overview']).save(dest);images.append((str(dest),name))
  board([x[0] for x in images],[x[1] for x in images],folder/'anonymous-board.png',700)
  context=json.dumps({'task':'匿名比较左右候选与作者成片的色彩、影调及共同可见内容关系。不评价构图/裁切差异。','regional_goals':public_anchors(anchors)},ensure_ascii=False)+'\n调用 photo_judge，参数包含 match_winner、protection_winner（left/right/tie/uncertain）、left_match、right_match（close/partial/off/uncertain）、visible_artifacts、evidence、uncertainty。分开判断参考接近与保护关系，不将自由偏好当成匹配。'
  try:
   answer=judge_call(folder,'你是独立摄影评图员。仅依据本会话图像与定性目标，未知则不确定；不推测实验条件，不输出内部思维过程。',context,images,'blind')
   write(file,{'status':'complete','job':job,'mapping':{'left':ordered[0],'right':ordered[1]},'answer':answer,'is_human_rating':False});print('blind',stem,repeat,pair,swap,flush=True)
  except Exception as exc:
   if not isinstance(exc,LocalDispatchDenied):trip_circuit(str(exc))
   write(file,{'status':'paused_authorization' if isinstance(exc,LocalDispatchDenied) else 'engineering_diagnosis_required','job':job,'interruption':str(exc)})
 def run(job):
  ident,repeat,pair,swap=job;folder=round_dir/'blind-review'/('DSC_'+ident)/('r'+str(repeat))/('-'.join(pair))/('swap' if swap else 'forward')
  with trajectory_lease(folder):
   try:return execute(job)
   except LocalDispatchDenied:raise
   except Exception as exc:trip_circuit(str(exc));raise
 with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:list(pool.map(run,jobs))

def image_block(path,label):
 with Image.open(path) as im:
  if max(im.size)>512:raise SafetyStop('Model image exceeds 512-pixel long-edge limit')
 return [{'type':'text','text':label},{'type':'image','source':{'type':'base64','media_type':'image/png','data':base64.b64encode(Path(path).read_bytes()).decode()}}]
def phase_schema(phase):
 if phase not in ['step','diagnostic','blind']:raise ValueError('Unknown current response phase')
 string={'type':'string'};public={'type':['string','array','object']};roi={'type':'array','items':{'type':'number','minimum':0,'maximum':1},'minItems':4,'maxItems':4,'description':'Normalized fractions [left, top, right, bottom], each in [0,1], with left < right and top < bottom; coordinates are relative to the respective reference or candidate image, not pixels. Example: [0.2, 0.3, 0.8, 0.9].'};strings={'type':'array','items':string}
 specs={
  'history':({'query':string,'offset':{'type':'integer','minimum':0},'page_size':{'type':'integer','minimum':1},'history_indices':{'type':'array','items':{'type':'integer','minimum':0}},'version_ids':strings,'observation_ids':strings,'evidence_ids':strings},[]),
  'judge':({k:public for k in ['visible_comparison','remaining_gaps','next_action','revision_reason','uncertainty']},['visible_comparison']),
  'edit':({'base_version':string,'values':{'type':'object','additionalProperties':{'type':'number'},'description':'Flat map of dotted control IDs from current_settings to numbers, e.g. {"light.contrast":8,"mixer.blue.sat":25}. Use this for all slider controls.'},'settings':{'type':'object','description':'Nested settings fragment, e.g. {"wb":{"mode":"custom"}}.'},'commands':{'type':'array','description':'Only for native curve, point-color and mask commands; use discover for their params. Sliders go in values.','items':{'type':'object','properties':{'id':{'type':'string','enum':sorted(COMMANDS)},'params':{'type':'object'}},'required':['id'],'additionalProperties':False}},'rationale':string,'predicted_effect':string},['base_version','rationale','predicted_effect']),
  'observe':({'version':string,'question':string,'reference_roi':roi,'candidate_roi':roi,'side':{'type':'integer','minimum':64,'maximum':SIDE_LIMITS['overview'],'description':'Long edge in pixels; overview at most '+str(SIDE_LIMITS['overview'])+', ROI crops are capped at '+str(SIDE_LIMITS['detail'])+'.'}},['version','question']),
  'discover':({'query':string,'offset':{'type':'integer','minimum':0},'page_size':{'type':'integer','minimum':1}},['query']),
  'finish':({'selected_version':string,'rationale':string,'confirm_export':{'type':'boolean'},'goal_status':{'type':'string','enum':['matched','partial','unresolved']},'remaining_gaps':public,'stop_basis':public},['selected_version','rationale']),
  'unable':({'reason':string,'evidence':string,'missing_capability':string},['reason','evidence','missing_capability'])}
 if phase=='diagnostic':
  direction={'type':'string','enum':['increase','decrease','same','uncertain']}
  props={k:public for k in ['visible_comparison','rationale','uncertainty']};props.update(anchor_axes={'type':'array','items':{'type':'object','properties':{'anchor_id':string,**{k:direction for k in ['L','a','b']}},'required':['anchor_id','L','a','b'],'additionalProperties':False}},changed={'type':['boolean','null']},next_decision={'type':'string','enum':['continue','keep','revert','inspect']})
  specs['judge']=(props,list(props))
 if phase=='blind':
  props={k:{'type':'string','enum':['left','right','tie','uncertain']} for k in ['match_winner','protection_winner']};props.update({k:{'type':'string','enum':['close','partial','off','uncertain']} for k in ['left_match','right_match']});props.update({k:public for k in ['visible_artifacts','evidence','uncertainty']});specs['judge']=(props,list(props))
 actions=['judge'] if phase in ['diagnostic','blind'] else ['observe','edit','discover','finish','unable','history']
 if phase=='step':
  review={'type':'object','properties':{k:{'type':'string','minLength':1} if k=='visible_comparison' else string for k in ['visible_comparison','remaining_gaps','uncertainty']},'required':['visible_comparison','remaining_gaps','uncertainty'],'additionalProperties':True}
  for a in actions:
   if a!='history':specs[a][0]['review']=review;specs[a][1].append('review')
  specs['finish'][1].extend(['goal_status','remaining_gaps','stop_basis'])
 return {'type':'object','oneOf':[{'type':'object','properties':{'action':{'type':'string','enum':[a]},'arguments':{'type':'object','properties':specs[a][0],'required':specs[a][1],'additionalProperties':True}},'required':['action','arguments'],'additionalProperties':False} for a in actions]}

def validate_action(result,phase,*,allow_empty_review=False):
 # Minimal local validation of the same emitted schema, before native mutation.
 def matches(value,schema):
  if 'oneOf' in schema:return sum(matches(value,s) for s in schema['oneOf'])==1
  types=schema.get('type',[]);types=[types] if isinstance(types,str) else types
  checks={'object':isinstance(value,dict),'array':isinstance(value,list),'string':isinstance(value,str),'integer':isinstance(value,int) and not isinstance(value,bool),'number':isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value),'boolean':isinstance(value,bool),'null':value is None}
  if types and not any(checks[t] for t in types):return False
  if 'enum' in schema and value not in schema['enum']:return False
  if isinstance(value,str) and len(value)<schema.get('minLength',0):return False
  if isinstance(value,dict):
   if any(k not in value for k in schema.get('required',[])):return False
   props=schema.get('properties',{})
   for k,v in value.items():
    if k in props:
     if not matches(v,props[k]):return False
    elif schema.get('additionalProperties') is False:return False
    elif isinstance(schema.get('additionalProperties'),dict) and not matches(v,schema['additionalProperties']):return False
  if isinstance(value,list) and (len(value)<schema.get('minItems',0) or len(value)>schema.get('maxItems',math.inf) or any(not matches(v,schema.get('items',{})) for v in value)):return False
  if isinstance(value,(int,float)) and not isinstance(value,bool) and not schema.get('minimum',-math.inf)<=value<=schema.get('maximum',math.inf):return False
  return True
 schema=phase_schema(phase)
 if allow_empty_review and phase=='step':
  for branch in schema['oneOf']:
   review=branch['properties']['arguments']['properties'].get('review',{})
   review.get('properties',{}).get('visible_comparison',{}).pop('minLength',None)
 if not matches(result,schema):raise ModelResponseError('Structured output failed phase schema; no action executed. Preserve request for engineering diagnosis instead of blind retry.')
 if result['action']=='observe':
  for key in ['reference_roi','candidate_roi']:
   if key in result['arguments']:
    try:pixel_box((10000,10000),result['arguments'][key])
    except (TypeError,ValueError) as exc:raise ModelResponseError('ROI must be normalized, ordered coordinates for the respective image') from exc
 if result['action']=='edit':
  args=result['arguments']
  if not any(args.get(k) for k in ['values','settings','commands']):raise ModelResponseError('No explicit native operation; do not infer one from prose')
  known={'base_version','values','settings','commands','rationale','predicted_effect','review'}
  if any(k not in known and (not k.endswith('_note') or not isinstance(v,str)) for k,v in args.items()):raise ModelResponseError('Unknown operational argument; only text notes may be ignored')
  for c in args.get('commands',[]):
   if set(c)-{'id','params'} or c.get('id') not in COMMANDS or not isinstance(c.get('params',{}),dict):raise ModelResponseError('Unrecognized native command envelope')

def normalize_action(result,phase=None):
 """Lossless envelope/flat-ID normalization; never invent parameter values."""
 if not isinstance(result,dict) or not isinstance(result.get('arguments'),dict):return result
 result=copy.deepcopy(result);args=result['arguments']
 if args.get('action')==result.get('action') and isinstance(args.get('arguments'),dict):
  if set(args)-{'action','arguments'}:raise ModelResponseError('Ambiguous mixed action envelopes')
  args=result['arguments']=args['arguments']
 if result.get('action')=='edit' and isinstance(args.get('values'),dict):
  flat={}
  def flatten(prefix,value):
   if isinstance(value,dict):
    for k,v in value.items():flatten(prefix+'.'+k if prefix else k,v)
   else:
    if prefix in flat:raise ModelResponseError('Conflicting nested/dotted control IDs')
    flat[prefix]=value
  flatten('',args['values']);args['values']=flat
 return result

class OutputCapacityError(ModelResponseError):pass

def exact_json(text):
 value=text.strip()
 if value.startswith('```'):
  lines=value.splitlines()
  if lines[0] not in ['```','```json'] or lines[-1]!='```':raise ModelResponseError('Ambiguous JSON framing')
  value='\n'.join(lines[1:-1])
 try:return strict_json(value)
 except json.JSONDecodeError:raise
 except ValueError as exc:raise ModelResponseError(str(exc)) from exc

def visual_usage(round_dir):
 """Reconcile every reservation with a saved receipt before the next paid request."""
 round_dir=Path(round_dir);policy=read(ROOT/'experiments/config/lab-paid-dispatch-policy.local.json');limits=policy.get('usage_limits')
 required=['single_input','single_output','total_input','total_output','estimated_cny']
 if not isinstance(limits,dict) or any(isinstance(limits.get(k),bool) or not isinstance(limits.get(k),(int,float)) or not math.isfinite(limits[k]) or limits[k]<=0 for k in required):raise SafetyStop('Explicit visual usage limits missing')
 ledger=ROOT/'experiments/lab-paid-admission-state.json';state=read(ledger) if ledger.exists() else {'policies':{}};pool=state.get('policies',{}).get(policy['policy_id'],{'issued':0,'claims':{}});claims=pool.get('claims',{})
 if pool.get('issued')!=len(claims):raise SafetyStop('Reservation ledger mismatch')
 fields=['input_tokens','cache_read_input_tokens','cache_creation_input_tokens','output_tokens'];totals={k:0 for k in fields};found=set();rows=[]
 for q in (round_dir/'editing').glob('*/r*/visual/requests/*.json'):
  if not q.stem.isdigit():continue
  request=read(q);rid=request.get('request_id');response=q.with_name(q.stem+'-response.json')
  if rid not in claims:
   if response.exists():raise SafetyStop('Saved response has no matching reservation')
   continue
  if rid in found or not response.exists():raise SafetyStop('Reserved request missing a unique receipt; billing unknown')
  found.add(rid);receipt=read(response);usage=receipt.get('usage')
  if not isinstance(usage,dict) or any(type(usage.get(k)) is not int or usage[k]<0 for k in fields):raise SafetyStop('Missing or invalid token usage; billing unknown')
  if usage.get('server_tool_use'):raise SafetyStop('Unexpected server-side tool usage; reconcile billing')
  total_in=sum(usage[k] for k in fields[:3]);out=usage['output_tokens']
  for k in fields:totals[k]+=usage[k]
  rows.append({'request_id':rid,'photo':q.parents[3].name,'input':total_in,'output':out})
 if found!=set(claims):raise SafetyStop('Reserved requests are not fully reconciled')
 total_in=sum(totals[k] for k in fields[:3]);estimated=(2*(totals['input_tokens']+totals['cache_creation_input_tokens'])+.04*totals['cache_read_input_tokens']+8*totals['output_tokens'])/1000000
 snapshot={'requests':len(found),'usage':totals,'total_input':total_in,'estimated_peak_cny':estimated,'warning_cny_1':estimated>=1,'limits':limits,'receipts':rows,'pricing':'conservative 2026-11-11 CNY peak; not provider bill'};write(round_dir/'usage-live.json',snapshot)
 print('usage',json.dumps({'requests':len(found),'input':total_in,'output':totals['output_tokens'],'estimated_peak_cny':round(estimated,6)},ensure_ascii=False),flush=True)
 if any(r['input']>=limits['single_input'] or r['output']>=limits['single_output'] for r in rows) or total_in>=limits['total_input'] or totals['output_tokens']>=limits['total_output'] or estimated>=limits['estimated_cny']:raise SafetyStop('Visual usage threshold reached; stop for offline review')
 return snapshot

def api(folder,system,context,images=None,phase='step',stable_context=None,monitor_round=None):
 schema=phase_schema(phase)
 dispatch_precheck()
 if monitor_round:
  try:visual_usage(monitor_round)
  except Exception as exc:trip_circuit(str(exc));raise
 folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);requests=folder/'requests';requests.mkdir(exist_ok=True)
 sequence=max([int(p.stem) for p in requests.glob('*.json') if p.stem.isdigit()],default=0)+1
 content=[{'type':'text','text':context}]
 for p,label in images or []:content.extend(image_block(p,label))
 tools=[{'name':'photo_'+branch['properties']['action']['enum'][0],'description':'Submit one public decision with explicit parameters.','input_schema':branch['properties']['arguments']} for branch in schema['oneOf']]
 if stable_context is not None:system+='\n固定任务资料：'+json.dumps(stable_context,ensure_ascii=False,separators=(',',':'))
 system+=' 每次调用一个已提供的 photo_* 工具，使用明确参数并给出简短公开依据。'
 body={'model':'deepseek-flash','max_tokens':4096,'stream':False,'thinking':{'type':'enabled'},'output_config':{'effort':'high'},'system':system,'messages':[{'role':'user','content':content}],'tools':tools}
 request_id='call_'+uuid.uuid4().hex
 wire=json.dumps(body);packet={'sequence':sequence,'request_id':request_id,'system':system,'stable_context':stable_context,'context':context,'phase':phase,'images':[{'path':str(p),'label':label,'dimensions':list(Image.open(p).size),'file_bytes':Path(p).stat().st_size} for p,label in images or []],'wire_bytes':len(wire.encode('utf-8')),'model':'deepseek-flash','effort':'high','started':time.time(),'implementation_revision':IMPLEMENTATION_REVISION,'implementation_build':IMPLEMENTATION_BUILD,'max_tokens':body['max_tokens'],'tools':tools}
 write(requests/f'{sequence:05}.json',packet);t=time.monotonic()
 try:
  scope=folder.resolve().relative_to(ROOT).as_posix()
  process=subprocess.run(['node',str(ROOT/'src/lab_timing_api.mjs'),'--scope',scope,'--build',IMPLEMENTATION_BUILD,'--request-id',request_id],input=wire,text=True,encoding='utf-8',capture_output=True,timeout=270,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  if not process.stdout:raise RuntimeError('Model transport failure: '+process.stderr[-500:])
  try:
   response=exact_json(process.stdout)
   if not isinstance(response,dict) or not isinstance(response.get('content',[]),list) or any(not isinstance(b,dict) for b in response.get('content',[])):raise ModelResponseError('Invalid transport envelope')
  except (ValueError,ModelResponseError):
   quarantine=folder/'quarantine';quarantine.mkdir(exist_ok=True);(quarantine/(request_id+'-untrusted-wire.txt')).write_text(process.stdout,encoding='utf-8')
   event(folder,'transport_quarantine',request_id=request_id,note='Untrusted wire data; never render or re-ingest automatically')
   raise
  if response.get('error'):
   outcomes=folder/'outcomes';outcomes.mkdir(exist_ok=True);write(outcomes/(request_id+'.json'),{'request_id':request_id,'transport_error':response['error'],'billing':'unknown unless explicitly denied locally before HTTP'})
   if response['error'].get('status')=='local_security_hold' and not response['error'].get('http_may_have_been_sent'):raise LocalDispatchDenied(str(response['error']))
   raise SafetyStop('Provider/transport '+json.dumps(response['error'],ensure_ascii=False))
  public=[b for b in response.get('content',[]) if b.get('type') in ['text','tool_use']]
  write(requests/f'{sequence:05}-response.json',{'content':public,'usage':response.get('usage'),'stop_reason':response.get('stop_reason'),'id':response.get('id'),'response_wire_bytes':len(process.stdout.encode('utf-8')),'seconds':time.monotonic()-t})
  if getattr(process,'returncode',0)!=0:raise SafetyStop('Unsuccessful transport; response retained, no action execution')
  event(folder,'model_usage',sequence=sequence,usage=response.get('usage'),stop_reason=response.get('stop_reason'),seconds=time.monotonic()-t)
  if monitor_round:visual_usage(monitor_round)
  if response.get('stop_reason')=='max_tokens':raise OutputCapacityError('Output capacity reached; no action executed and no automatic replay')
  calls=[b for b in public if b.get('type')=='tool_use'];allowed={t['name'] for t in tools}
  if len(calls)>1:raise ModelResponseError('Multiple tool calls are ambiguous')
  if calls:
   call=calls[0]
   if call.get('name') not in allowed or not isinstance(call.get('input'),dict):raise ModelResponseError('Unknown tool or non-object parameters')
   result={'action':call['name'][6:],'arguments':call['input']}
  else:
   text='\n'.join(b.get('text','') for b in public if b.get('type')=='text')
   if not text.strip():raise ModelResponseError('No explicit public decision')
   result=exact_json(text)
   if phase in ['diagnostic','blind'] and isinstance(result,dict) and 'action' not in result:result={'action':'judge','arguments':result}
  result=normalize_action(result,phase);validate_action(result,phase);return result
 except Exception as exc:
  if not isinstance(exc,LocalDispatchDenied):trip_circuit(str(exc))
  kind='local_dispatch_denied' if isinstance(exc,LocalDispatchDenied) else 'response_contract_error' if isinstance(exc,(ValueError,ModelResponseError)) else 'transport_outcome_unknown' if isinstance(exc,subprocess.TimeoutExpired) else 'provider_or_transport_error'
  event(folder,kind,sequence=sequence,seconds=time.monotonic()-t,error=str(exc))
  if isinstance(exc,ValueError):raise ModelResponseError('Response parsing failed; no action executed or paid repair: '+str(exc)) from exc
  raise
def smoke_test():
 a=lab_array(Image.new('RGB',(2,2),'white'));b=lab_array(Image.new('RGB',(2,2),'black'))
 assert np.max(np.abs(a-[100,0,0]))<.00003 and np.max(np.abs(b))==0
 for rgb,expected in [((255,0,0),[53.2408,80.0925,67.2032]),((0,255,0),[87.7347,-86.1827,83.1793]),((0,0,255),[32.2970,79.1875,-107.8602])]:
  assert np.max(np.abs(lab_array(Image.new('RGB',(1,1),rgb))[0,0]-expected))<.001
 assert pixel_box((101,53),[.1,.2,.8,.9])==(10,11,81,48)
 im=Image.new('RGB',(7,5),(123,90,17));m=measure(im,[0,0,1,1]);assert m['pixels']==35
 anchors=[{'id':'x','role':'test','qualification':'same_region'}]
 ref={'x':{**m,'median':[50,3,7]}};cur={'x':{**m,'median':[40,1,2]}};old={'x':{**m,'median':[35,2,0]}}
 f=feedback(cur,old,ref,anchors)['rows'][0];assert f['change_current_minus_parent']==[5,-1,2] and f['residual_target_minus_current']==[10,2,5]
 anchors[0]['qualification']='role_only';assert feedback(cur,old,ref,anchors)['rows'][0]['residual_target_minus_current'] is None
 assert iou([0,0,.5,.5],[0,0,.5,.5])==1 and iou([0,0,.5,.5],[.5,.5,1,1])==0 and same_region(None,None) and not same_region(None,[0,0,1,1]) and not same_region([0,0,.4,.4],[.2,.2,.6,.6])
 assert set(ANCHORS)==set(PHOTO_IDS+DEV_IDS) and all(len(v)==5 and v[0][0]=='highlight' and v[1][0]=='shadow' and all(len(a)==5 and a[4] in ['same_region','role_only'] for a in v) for v in ANCHORS.values())
 print(json.dumps({'passed':True,'checks':['D65 white/black and primary reference values','ROI half-up','all-pixel sample count','difference signs','role-only residual withheld','region overlap','five placed tone anchors'],'model_requests':0}))

def main():
 sys.stdout.reconfigure(encoding='utf-8');p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('--round',type=Path);p.add_argument('--workers',type=int,default=1);p.add_argument('--photo');p.add_argument('--photos',nargs='+',default=['6268']);p.add_argument('--conditions',nargs='+',default=['visual']);p.add_argument('--repeats',type=int,default=1);p.add_argument('--full-author',action='store_true',help='Use the full-size author JPG backup instead of the default 512-short-side copy');args=p.parse_args()
 if args.command=='selftest':smoke_test()
 elif args.command=='prepare':prepare(args.round.resolve(),args.photos,args.conditions,args.repeats,args.full_author)
 elif args.command=='anchors':qualify_anchors(args.round.resolve())
 elif args.command=='raw-contract':verify_raw_contract(args.round.resolve())
 elif args.command=='qualify':qualify_native(args.round.resolve())
 elif args.command=='freeze':freeze(args.round.resolve())
 elif args.command=='development':run_editing(args.round.resolve(),args.workers,True)
 elif args.command=='editing':run_editing(args.round.resolve(),args.workers,only_photo=args.photo)
 elif args.command=='diagnostic-images':prepare_diagnostics(args.round.resolve())
 elif args.command=='diagnostics':run_diagnostics(args.round.resolve(),args.workers)
 elif args.command=='blind':run_blind(args.round.resolve(),args.workers)
 else:raise ValueError('Unknown command')
if __name__=='__main__':main()
