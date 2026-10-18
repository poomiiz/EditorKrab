"""External sync workspace; only metadata crosses Supabase, projects stay local."""
import atomic
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import kit_settings as kit
import team_hub as hub

router=APIRouter(prefix='/api/team',tags=['team-workspace'])
BINDINGS=kit.KIT/'team_project_bindings.json'
_binding_lock=threading.RLock()

class Workspace(BaseModel):
 path:str=Field(max_length=2000)
 tool:Literal['syncthing','drive_desktop']

def workspace(org:UUID) -> Path:
 cfg=kit._read_config();value=cfg.get('team_workspaces',{}).get(str(org),{}).get('path')
 if not value:raise HTTPException(409,'เลือกโฟลเดอร์ทีมในเครื่องนี้ก่อน')
 root=Path(value).resolve()
 if not root.is_dir():raise HTTPException(409,'โฟลเดอร์ทีมยังไม่พร้อม')
 return validate_workspace(root)

def validate_workspace(root:Path) -> Path:
 for shared in (root/'footage',root/'exports'):
  shared=shared.resolve()
  if shared.parent!=root:raise HTTPException(409,'footage และ exports ต้องเป็นโฟลเดอร์ย่อยจริงของทีม')
  for private_root in [kit.KIT.resolve(),*kit._project_roots().values()]:
   if str(private_root) in ('','.'):continue
   private_root=private_root.resolve()
   if private_root==shared or shared in private_root.parents or private_root in shared.parents:
    raise HTTPException(409,'โปรเจกต์และข้อมูลเครื่องต้องอยู่แยกจากโฟลเดอร์ซิงก์')
 return root


def writable(root:Path):
 for folder in ('footage','exports'):
  target=root/folder;target.mkdir(exist_ok=True)
  with tempfile.TemporaryFile(dir=target) as probe:probe.write(b'check');probe.flush()

@router.get('/organizations/{org}/workspace')
def get_workspace(org:UUID,request:Request):
 hub.rpc(request,'clipkit_member_directory',{'org':str(org)})
 return kit._read_config().get('team_workspaces',{}).get(str(org),{})

@router.post('/organizations/{org}/workspace')
def save_workspace(org:UUID,body:Workspace,request:Request):
 hub.rpc(request,'clipkit_member_directory',{'org':str(org)})
 root=Path(body.path).resolve()
 if not root.is_dir():raise HTTPException(400,'โฟลเดอร์ทีมไม่มีอยู่')
 cfg=kit._read_config();cfg.setdefault('team_workspaces',{})[str(org)]={'path':str(root),'tool':body.tool}
 validate_workspace(root)
 try:writable(root)
 except OSError as exc:raise HTTPException(400,'เขียนโฟลเดอร์ทีมไม่ได้') from exc
 atomic.write_text(kit.CONFIG, json.dumps(cfg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 return {'saved':True}

def rows(request,org,table):
 return hub._provider('/rest/v1/'+table+'?organization_id=eq.'+str(org)+'&select=*&order=created_at',token=hub.session(request)['access_token'])

def file_at(root:Path,name:str,size:int):
 if not name or Path(name).name!=name or '/' in name or '\\' in name:raise HTTPException(400,'ชื่อไฟล์ไม่ถูกต้อง')
 path=(root/name).resolve()
 if path.parent!=root.resolve():raise HTTPException(400,'ไฟล์อยู่นอกโฟลเดอร์ทีม')
 return path if path.is_file() and path.stat().st_size==size else None

@router.get('/organizations/{org}/footage')
def footage(org:UUID,request:Request):
 result=rows(request,org,'footage')
 try:root=workspace(org)/'footage'
 except HTTPException:root=None
 for item in result:
  path=file_at(root,item['name'],item['size_bytes']) if root else None
  item['synced']=bool(path)
  item['path']=str(path) if path else None
 return result

class Footage(BaseModel):
 name:str=Field(min_length=1,max_length=500)
 size_bytes:int=Field(gt=0)

@router.post('/organizations/{org}/footage')
def add_footage(org:UUID,body:Footage,request:Request):
 root=workspace(org)/'footage'
 if not file_at(root,body.name,body.size_bytes):raise HTTPException(409,'วางฟุตใน footage แล้วรอซิงก์ให้ครบก่อนเพิ่ม')
 return {'id':hub.rpc(request,'clipkit_add_footage',{'org':str(org),'file_name':body.name,'file_size':body.size_bytes})}

@router.get('/organizations/{org}/projects')
def projects(org:UUID,request:Request):return rows(request,org,'projects')

class Story(BaseModel):
 name:str=Field(min_length=1,max_length=500)
 project_type:Literal['capcut','hyperframes','clipkit']='capcut'
class Stories(BaseModel):
 footage_id:UUID
 stories:list[Story]=Field(min_length=1,max_length=500)
@router.post('/organizations/{org}/projects')
def add_projects(org:UUID,body:Stories,request:Request):
 return {'added':hub.rpc(request,'clipkit_add_projects',{'org':str(org),'source':str(body.footage_id),'stories':[s.model_dump() for s in body.stories]})}

class Version(BaseModel):
 expected_revision:int=Field(gt=0)
class Transfer(Version):
 owner_id:UUID|None=None
 note:str=Field(min_length=1,max_length=5000)
@router.post('/organizations/{org}/projects/{project}/claim')
def claim(org:UUID,project:UUID,body:Version,request:Request):
 return {'revision':hub.rpc(request,'clipkit_claim_project',{'org':str(org),'project':str(project),'expected_revision':body.expected_revision})}
@router.post('/organizations/{org}/projects/{project}/transfer')
def transfer(org:UUID,project:UUID,body:Transfer,request:Request):
 return {'revision':hub.rpc(request,'clipkit_transfer_project',{'org':str(org),'project':str(project),'next_owner':str(body.owner_id) if body.owner_id else None,'expected_revision':body.expected_revision,'note':body.note})}

class Review(BaseModel):
 project_ids:list[UUID]=Field(min_length=1,max_length=500)
 expected_revisions:dict[UUID,int]
 status:Literal['editing','awaiting_review','changes_requested','approved']
 note:str=Field(default='',max_length=5000)
@router.post('/organizations/{org}/review')
def review(org:UUID,body:Review,request:Request):
 return {'updated':hub.rpc(request,'clipkit_project_action',{'org':str(org),'project_ids':[str(x) for x in body.project_ids],'expected_revisions':{str(k):v for k,v in body.expected_revisions.items()},'next_status':body.status,'note':body.note})}

def owned(org,project,request):
 item=next((p for p in projects(org,request) if p['id']==str(project)),None)
 if not item or item['owner_id']!=hub.session(request)['user']['id']:raise HTTPException(403,'เจ้าของโปรเจกต์เท่านั้น')
 if item['status'] not in ('editing','changes_requested'):raise HTTPException(409,'โปรเจกต์ล็อกระหว่างตรวจหรืออนุมัติ')
 return item
def begin_edit(org,project,item,request):
 if item['status']=='changes_requested':
  review(org,Review(project_ids=[project],expected_revisions={project:item['revision']},status='editing'),request)

class Bind(BaseModel):
 path:str=Field(max_length=2000)
@router.post('/organizations/{org}/projects/{project}/bind')
def bind(org:UUID,project:UUID,body:Bind,request:Request):
 item=owned(org,project,request);kind,path=kit._project(body.path)
 if kind!=item['project_type']:raise HTTPException(400,'ชนิดโปรเจกต์ไม่ตรงกับงาน')
 with _binding_lock:
  bindings=json.loads(BINDINGS.read_text(encoding='utf-8')) if BINDINGS.exists() else {}
  if any(k!=str(project) and v['path']==str(path.resolve()) for k,v in bindings.items()):raise HTTPException(409,'โปรเจกต์นี้ผูกกับงานอื่นแล้ว')
  bindings[str(project)]={'path':str(path.resolve()),'owner_id':item['owner_id'],'organization_id':str(org)}
  with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=BINDINGS.parent,delete=False) as out:
   tmp=Path(out.name);json.dump(bindings,out,ensure_ascii=False,indent=2)
  try:os.replace(tmp,BINDINGS)
  finally:tmp.unlink(missing_ok=True)
 return {'saved':True}
@router.post('/organizations/{org}/projects/{project}/open')
def open_project(org:UUID,project:UUID,body:Version,request:Request):
 item=owned(org,project,request)
 if item['revision']!=body.expected_revision:raise HTTPException(409,'ข้อมูลเปลี่ยนแล้ว กรุณารีเฟรช')
 bindings=json.loads(BINDINGS.read_text(encoding='utf-8')) if BINDINGS.exists() else {}
 binding=bindings.get(str(project))
 if not binding or binding['owner_id']!=item['owner_id']:raise HTTPException(409,'เลือกโปรเจกต์ส่วนตัวในเครื่องนี้ก่อน')
 begin_edit(org,project,item,request)
 return kit.draft_open(kit.DraftPath(path=binding['path']))
class Export(Version):
 name:str=Field(min_length=1,max_length=500)
 size_bytes:int=Field(gt=0)
@router.post('/organizations/{org}/projects/{project}/export')
def submit_export(org:UUID,project:UUID,body:Export,request:Request):
 owned(org,project,request)
 if not body.name.lower().endswith('.mp4') or not file_at(workspace(org)/'exports',body.name,body.size_bytes):raise HTTPException(409,'วาง MP4 ใน exports แล้วรอให้ไฟล์ครบก่อนส่งตรวจ')
 return {'updated':hub.rpc(request,'clipkit_project_action',{'org':str(org),'project_ids':[str(project)],'expected_revisions':{str(project):body.expected_revision},'next_status':'awaiting_review','note':'','export_file':body.name,'export_bytes':body.size_bytes})}

DRIVE_LINK=re.compile(r'https://(drive|docs)[.]google[.]com/\S+')
class ShareLink(BaseModel):
 link:str=Field(default='',max_length=500)
@router.post('/organizations/{org}/projects/{project}/share-link')
def share_link(org:UUID,project:UUID,body:ShareLink,request:Request):
 """A Google Drive link for reviewers on a phone; empty clears it. The database decides who may set it."""
 link=body.link.strip()
 if link and not DRIVE_LINK.fullmatch(link):raise HTTPException(400,'วางลิงก์แชร์จาก Google Drive (ขึ้นต้นด้วย https://drive.google.com/)')
 hub.rpc(request,'clipkit_set_share_link',{'org':str(org),'project':str(project),'link':link})
 return {'share_url':link or None}

@router.get('/organizations/{org}/workspace-files')
def workspace_files(org:UUID,request:Request):
 hub.rpc(request,'clipkit_member_directory',{'org':str(org)})
 root=workspace(org)
 return {folder:[{'name':p.name,'size_bytes':p.stat().st_size} for p in (root/folder).iterdir() if p.is_file() and p.resolve().parent==(root/folder).resolve() and p.suffix.lower() in (kit.VIDEO_EXT if folder=='footage' else {'.mp4'})] for folder in ('footage','exports')}

@router.get('/organizations/{org}/projects/{project}/preview')
def preview(org:UUID,project:UUID,request:Request):
 item=next((p for p in projects(org,request) if p['id']==str(project)),None)
 if not item:raise HTTPException(404,'ไม่พบงานในองค์กร')
 path=file_at(workspace(org)/'exports',item.get('export_name'),item.get('export_size'))
 if not path:raise HTTPException(409,'MP4 ยังไม่ได้ sync')
 return FileResponse(path,media_type='video/mp4')

def guard_paths(values,request):
 """One boundary for existing editor callers; database decides every bound project access."""
 if not BINDINGS.exists():return
 with _binding_lock:bindings=json.loads(BINDINGS.read_text(encoding='utf-8'))
 def strings(value):
  if isinstance(value,str):yield value
  elif isinstance(value,dict):
   for item in value.values():yield from strings(item)
  elif isinstance(value,list):
   for item in value:yield from strings(item)
 paths=[]
 for value in strings(values):
  try:paths.append(Path(value).resolve())
  except (OSError,ValueError):continue
 for project,binding in bindings.items():
  root=Path(binding['path']).resolve()
  if any(path==root or root in path.parents for path in paths):
   org=UUID(binding['organization_id']);project_id=UUID(project)
   item=owned(org,project_id,request)
   if request.method=='POST' and request.url.path in ('/api/video/draft','/api/kit/draft-open'):
    begin_edit(org,project_id,item,request)
   if request.url.path=='/api/kit/draft-rename':raise HTTPException(409,'โปรเจกต์ทีมให้เปลี่ยนชื่อเรื่องจากหน้าทีม เพื่อเก็บสิทธิ์ของไฟล์เดิม')

class ProjectName(Version):
 name:str=Field(min_length=1,max_length=500)
@router.post('/organizations/{org}/projects/{project}/rename')
def rename_project(org:UUID,project:UUID,body:ProjectName,request:Request):
 return {'revision':hub.rpc(request,'clipkit_rename_project',{'org':str(org),'project':str(project),'project_name':body.name.strip(),'expected_revision':body.expected_revision})}

def delivery_view(request:Request,historical:dict) -> dict:
 result={path:{**record,'source':'historical'} for path,record in historical.items()}
 if not BINDINGS.exists():return result
 with _binding_lock:bindings=json.loads(BINDINGS.read_text(encoding='utf-8'))
 organizations={}
 for project,binding in bindings.items():
  org=binding['organization_id']
  if org not in organizations:
   try:organizations[org]={item['id']:item for item in projects(UUID(org),request)}
   except HTTPException as exc:organizations[org]=exc
  rows=organizations[org]
  record={'source':'team','project_id':project}
  if isinstance(rows,HTTPException):
   record['error']='เข้าสู่ระบบทีมก่อนดูสถานะ' if rows.status_code==401 else 'อ่านสถานะทีมไม่ได้: '+str(rows.detail)
  elif project not in rows:record['error']='ไม่พบงานในองค์กรของบัญชีนี้'
  else:record.update({key:rows[project][key] for key in ('status','revision','owner_id','updated_by','updated_at')})
  result[str(Path(binding['path']).resolve()).lower()]=record
 return result

def require_review_access(request:Request) -> None:
 user=hub.session(request)['user']['id']
 if not any(member['user_id']==user and member['role'] in ('admin','lead') for org in hub.organizations(request) for member in org['clipkit_members']):
  raise HTTPException(403,'หน้าตรวจงานสำหรับหัวหน้าและผู้ดูแลองค์กร')

@router.get('/review-access')
def review_access(request:Request) -> dict[str,bool]:
 """Whether to show the team review link; other failures (provider down) stay errors."""
 try:require_review_access(request)
 except HTTPException as exc:
  if exc.status_code!=403:raise
  return {'allowed':False}
 return {'allowed':True}
