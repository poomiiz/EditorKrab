import {DraftSession} from './autosave.js';
import {watchJobs} from './jobs.js';
import {uploadFile} from './uploads.js';
import {createTranscription,validTranscriptEdits} from './transcription.js';
import {createTextQc} from './text-qc.js';
import {createExport} from './export.js';
import {createStyleEditor,validTextStyles,validHeadlineItems,defaults} from './text-style.js';
const $ = id => document.getElementById(id);
const key = 'editorkrab-rust-flow-prototype-v1';
let draft = {flow_version:2,headline_items:[],step: 1, title: '', language: 'auto', files: [], plan: '', style: 'bold16'};
let unsaved = false;
let session, pendingLocal = 0, editingAllowed = false, jobWatcher;
let activeUpload;
const textEditor=createStyleEditor(()=>draft.text_styles,value=>{draft.text_styles=value;save();},()=>({stage:activeStep(draft),roles:draft.text_roles || [],items:headlineItems()}),items=>{draft.headline_items=items;save();renderHeadline();renderSteps();});
function headlineItems(){return draft.headline_items ?? (draft.text_styles?.headline ? [{id:'headline-main',...draft.text_styles.headline}] : []);}
function headlinePreset(){return defaults().headline;}
function activeStep(d){return d.flow_version===2?d.step:d.step===5?6:d.step;}
function upgradeFlow(){if(draft.flow_version!==2){draft.step=activeStep(draft);draft.flow_version=2;}}
// Moving between steps is view state: it is remembered per project but never becomes an undoable edit.
const stepKey=()=>'editorkrab-step-'+projectId;
function rememberStep(){try{localStorage.setItem(stepKey(),String(draft.step));}catch(_){/* Step memory is optional. */}}
function recallStep(){let step;try{step=Number(localStorage.getItem(stepKey()));}catch(_){return;}if([1,2,3,4,5,6].includes(step)){upgradeFlow();draft.step=step;}}
const probes=new Map();
let filesFrame=0;
function queueFilesRender() {if (!filesFrame) filesFrame=requestAnimationFrame(()=>{filesFrame=0;renderFiles();});}
// index.html loads this module only when the URL names a project; the start screen picks one.
const projectId = new URL(location.href).searchParams.get('project');
const json = body => ({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
const linked = f => ({path:f.path,size:f.size,modified_ns:f.modified_ns});
// path -> 'missing' | 'changed' for linked videos that are not where the project last saw them
const lost = new Map();
const speech=createTranscription({project:projectId,read:()=>draft,
 state:()=>({editing:editingAllowed,saved:!!session?.record&&!session.blocked&&!session.volatile&&!session.record.pending.length&&!unsaved,revision:session?.record?.revision || 0}),
 writeEdit:edit=>{if(!editingAllowed)return;const entries=(draft.transcript_edits || []).filter(v=>v.job_id!==edit.job_id||v.segment_id!==edit.segment_id);if(edit.text!==edit.original_text)entries.push(edit);if(!validTranscriptEdits(entries))return;draft.transcript_edits=entries;save();},
 changed:()=>queueMicrotask(renderSteps)});

window.addEventListener('beforeunload', event => { if (unsaved || activeUpload) { event.preventDefault(); event.returnValue = ''; } });
function valid(d) {
  // one built-in look; drafts saved under its earlier id move to the current one
  if (typeof d?.style === 'string' && /^[a-z]+16$/.test(d.style)) d.style = 'bold16';
  if (!Array.isArray(d?.files) || !validTranscriptEdits(d.transcript_edits) || !validTextStyles(d.text_styles) || !validHeadlineItems(d.headline_items) || (d.flow_version!==undefined && d.flow_version!==2) ||
    (d.text_roles!==undefined && (!Array.isArray(d.text_roles) || d.text_roles.some(r=>!['speech','emphasis','english'].includes(r)) || new Set(d.text_roles).size!==d.text_roles.length)) ||
    (d.headline_template!==undefined && !['plain','plate','emphasis'].includes(d.headline_template)) ||
    (d.headline_reply!==undefined && (typeof d.headline_reply!=='string' || d.headline_reply.length>32000))) return false;
  const ids=d.files.filter(f=>f?.source_id!==undefined).map(f=>f.source_id);
  if (new Set(ids).size!==ids.length) return false;
  const uploads=d.files.filter(f=>f?.upload_id!==undefined).map(f=>f.upload_id);
  if (new Set(uploads).size!==uploads.length) return false;
  return d && (d.flow_version===2?[1,2,3,4,5,6]:[1,2,3,4,5]).includes(d.step) && typeof d.title === 'string' && d.title.length <= 120 &&
    typeof d.plan === 'string' && ['auto','th','en'].includes(d.language) && d.style === 'bold16' &&
    Array.isArray(d.files) && d.files.every(f => f && typeof f.name === 'string' && Number.isFinite(f.size) && f.size >= 0 &&
      (f.upload_id===undefined || (typeof f.upload_id==='string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(f.upload_id) &&
      Number.isSafeInteger(f.last_modified) && f.last_modified>=0 && Number.isSafeInteger(f.size))) &&
      (f.source_id===undefined || (typeof f.source_id==='string' && /^[A-Za-z0-9_-]{1,128}$/.test(f.source_id) &&
      typeof f.path==='string' && !!f.path.trim() && !f.path.includes('\0') &&
      typeof f.modified_ns==='string' && /^-?\d+$/.test(f.modified_ns) && Number.isSafeInteger(f.size))));
}
function error(message) { $('error').textContent = message; $('error').hidden = !message; }
function editing(enabled) {
  editingAllowed = enabled;
  textEditor.editing(enabled);
  for (const input of document.querySelectorAll('[data-step],[data-go],#title,#language,#plan,#pick,#folder,#open,.source-remove button,#headlineReply,#applyHeadline,#headlinePacket')) input.disabled = !enabled;
  renderHeadline();speech.refresh();
  $('undo').disabled = !enabled || !session?.record?.undo.length || !!session?.blocked || !!session?.volatile;
  $('redo').disabled = !enabled || !session?.record?.redo.length || !!session?.blocked || !!session?.volatile;
}
function status(info) {
  jobWatcher?.refresh();speech.refresh();
  $('saved').dataset.state=info.state;
  const labels = {pending:'รอส่ง', saved:'บันทึกแล้ว', ready:'พร้อมแก้ไข', offline:'รอเชื่อมต่อ',
    conflict:'งานชนกัน', storage_error:'บันทึกไม่สำเร็จ', rejected:'ส่งไม่สำเร็จ'};
  $('saved').textContent = labels[info.state] || 'กำลังบันทึก';
  $('undo').disabled = !editingAllowed || !info.undo || !!session?.blocked || !!session?.volatile;
  $('redo').disabled = !editingAllowed || !info.redo || !!session?.blocked || !!session?.volatile;
  if (['conflict','storage_error','rejected'].includes(info.state)) {
    editing(false); $('fork').hidden = false;
    error(info.state === 'conflict' ? 'งานนี้ถูกแก้จากอีกหน้าต่าง กด "บันทึกเป็นงานใหม่" เพื่อทำต่อ' :
      'บันทึกไม่สำเร็จ ดาวน์โหลดร่างเพื่อสำรองก่อนทำต่อ');
  }
}
async function save() {
  if (!session || session.blocked) return;
  upgradeFlow();
  pendingLocal++; unsaved = true;
  $('saved').dataset.state='pending';
  $('saved').textContent = 'กำลังบันทึก';
  const snapshot = structuredClone(draft);
  try {
    await session.edit(snapshot); pendingLocal--; unsaved = pendingLocal > 0;
  } catch (_) { pendingLocal--; unsaved = true; }
}
function renderSteps() {
  const done=stepsDone();
  exporter?.refresh();
  for (const b of document.querySelectorAll('[data-step]')) {
    const n=Number(b.dataset.step),finished=!!done[n] && n!==activeStep(draft);
    if (n === activeStep(draft)) b.setAttribute('aria-current', 'step');
    else b.removeAttribute('aria-current');
    b.toggleAttribute('data-done',finished);
    b.querySelector('.step-number').textContent=finished ? '✓' : '0'+n;
    b.setAttribute('aria-label',`ขั้น ${n} ${b.querySelector('small').textContent}`+(finished ? ' เสร็จแล้ว' : ''));
  }
  // Next is the primary action only once its step's work is done; otherwise say what is missing.
  const needs={1:[done[1],''],2:[done[1],''],3:[done[3],'วางคำตอบจาก AI ก่อน'],5:[false,'']};
  for (const foot of document.querySelectorAll('.stage-foot')) {
    const next=foot.querySelector('.actions [data-go]');if(!next)continue;
    const [ready,hint]=needs[Number(foot.closest('[data-panel]').dataset.panel)] || [true,''];
    next.classList.toggle('primary',ready);
    let note=foot.querySelector('.foot-hint');
    if(!note){note=document.createElement('span');note.className='foot-hint caption';foot.querySelector('.actions').before(note);}
    note.textContent=ready ? '' : hint;
  }
}
const textQc=createTextQc({summary:$('textQcSummary'),go:$('textQcGo'),list:$('textQcList'),listen:id=>speech.listen(id),
 texts:()=>{
  const subtitles=speech.texts(),end=Math.max(0,...subtitles.map(t=>t.end_ms));
  return [...headlineItems().filter(h=>h.text?.trim()).map(h=>({kind:'headline',id:'headline:'+h.id,text:h.text,start_ms:0,end_ms:end})),...subtitles];
 },
 edit:(finding,text)=>{
  if(!editingAllowed)throw Error('not_editable');
  if(finding.kind!=='headline'){speech.edit(finding.id,text);return;}
  const id=finding.id.slice('headline:'.length);draft.headline_items=headlineItems().map(h=>h.id===id?{...h,text}:h);save();textEditor.render();renderHeadline();
 }});
function render() {
  renderSteps();
  for (const p of document.querySelectorAll('[data-panel]')) p.hidden = Number(p.dataset.panel) !== activeStep(draft);
  // Placing subtitles runs the text check; only flagged lines ask to be heard.
  if (activeStep(draft)===4) textQc.run();
  renderFiles();
  textEditor.render();
  for (const id of ['title', 'language', 'plan']) $(id).value = draft[id];
  $('headlineReply').value=draft.headline_reply || '';
  renderHeadline();speech.refresh(true);
  validate();
}
for(const button of document.querySelectorAll('[data-go]'))button.onclick=()=>{document.querySelector(`[data-step="${button.dataset.go}"]`).click();$('workspace').focus();$('workspace').scrollIntoView({block:'start'});};
function planReady(){try{return !!draft.plan.trim() && !!JSON.parse(draft.plan);}catch(_){return false;}}
function stepsDone(){return {1:speech.complete(),3:planReady(),4:!!draft.text_roles?.length,5:headlineItems().length>0};}
function validate() {
  $('planStatus').hidden=!draft.plan.trim();
  $('planStatus').textContent=planReady() ? 'อ่านคำตอบได้' : 'คำตอบยังไม่ครบ ลองคัดลอกใหม่ทั้งหมด';
}
function renderFiles() {
  let duration=0,ready=0;
  $('fileList').replaceChildren(...draft.files.map((file,index)=>{
    const li=document.createElement('li');
    const icon=document.createElement('span');icon.className='file-icon';icon.setAttribute('aria-hidden','true');
    icon.textContent=file.name.includes('.') ? file.name.split('.').pop().toUpperCase().slice(0,4) : 'FILE';
    const name=document.createElement('span');name.className='file-name';name.textContent=file.name;name.title=file.name;
    li.append(icon,name);
    const probe=probes.get(file.source_id);
    li.dataset.state=probe?.status || 'pending';
    const info=document.createElement('p');info.className='file-info';
    if (lost.has(file.path)) {li.dataset.state='failed';info.textContent=lost.get(file.path)==='changed' ? 'ไฟล์ถูกแก้ไขหรือแทนที่' : 'ไฟล์หาย';}
    else if (probe?.status==='succeeded') {
      const video=probe.result.streams.find(s=>s.codec_type==='video');
      info.textContent=`${probe.result.duration_s.toFixed(1)} วินาที`+(video ? ` · ${video.width} × ${video.height}` : ' · เสียง');
      if(!probe.result.streams.some(s=>s.codec_type==='audio'))info.textContent+=' · ไม่มีเสียง ข้ามถอดเสียง';
      duration+=probe.result.duration_s;ready++;
    } else if (probe?.status==='failed') info.textContent=probe.error==='source_changed' ? 'วิดีโอถูกแก้ไขแล้ว เลือกใหม่' : 'ตรวจข้อมูลไม่สำเร็จ';
    else if (probe?.status==='cancelled') info.textContent='ยกเลิกการตรวจ';
    else info.textContent=file.source_id ? 'กำลังตรวจข้อมูล' : file.upload_id ? 'ลากไฟล์เดิมมาวางเพื่อส่งต่อ' : 'ยังไม่พบวิดีโอต้นฉบับ';
    info.textContent=`${(file.size/1048576).toFixed(1)} MB · ${info.textContent}`;
    li.append(info);
    const actions=document.createElement('span');actions.className='source-remove row';
    if (lost.has(file.path)) {
      const find=document.createElement('button');find.textContent='หาไฟล์';find.disabled=!editingAllowed;
      find.setAttribute('aria-label',`หาที่อยู่ใหม่ของ ${file.name}`);find.onclick=()=>relink(file);actions.append(find);
    }
    const remove=document.createElement('button');remove.textContent='ลบ';remove.disabled=!editingAllowed;
    remove.setAttribute('aria-label',`ลบ ${file.name} ออกจากโปรเจกต์`);
    remove.onclick=()=>removeFile(file);
    actions.append(remove);li.append(actions);return li;
  }));
  $('empty').hidden=draft.files.length>0;
  document.querySelector('[data-panel="1"]').toggleAttribute('data-has-files',draft.files.length>0);
  $('fileCount').textContent=`${draft.files.length} ไฟล์`;
  const totalSeconds=Math.round(duration),minutes=Math.floor(totalSeconds/60),seconds=totalSeconds%60;
  $('fileTotal').textContent=ready ? `รวม ${minutes}:${String(seconds).padStart(2,'0')}` : '';
  renderSteps();speech.refresh();
}
for (const b of document.querySelectorAll('[data-step]')) b.onclick = () => { upgradeFlow();draft.step = Number(b.dataset.step); rememberStep(); render(); };
for (const id of ['title', 'language', 'plan']) $(id).addEventListener('input', () => { draft[id] = $(id).value; save(); if (id === 'plan') {validate();renderHeadline();renderSteps();} });
for(const input of document.querySelectorAll('[data-role-option]'))input.onchange=()=>{
 if(!editingAllowed)return;draft.text_roles=[...document.querySelectorAll('[data-role-option]:checked')].map(n=>n.dataset.roleOption);save();textEditor.render();renderSteps();
};
$('addHeadline').onclick=()=>{
 if(!editingAllowed || headlineItems().length>=100)return;
 const items=structuredClone(headlineItems()),item={...headlinePreset(),id:crypto.randomUUID(),text:'ข้อความ '+(items.length+1),y:Math.min(90,headlinePreset().y+(items.length%6)*13)};
 items.push(item);draft.headline_items=items;save();textEditor.render();textEditor.selectHeadline(item.id);renderHeadline();renderSteps();
};
$('headlineReply').oninput=()=>{if(editingAllowed){draft.headline_reply=$('headlineReply').value;$('headlineStatus').textContent='';save();}};
function renderHeadline(){
 const ready=planReady();
 $('headlinePacket').disabled=!editingAllowed || !ready;
 $('headlinePrerequisite').textContent=ready?'ส่งไฟล์นี้ให้ AI แล้ววางคำตอบด้านบน':'วางคำตอบ AI ในขั้น 3 ก่อน';
}
function headlineContent(){return {title:draft.title,plan:JSON.parse(draft.plan),headline_slots:headlineItems().map(v=>({id:v.id,text:v.text})),sources:draft.files.map(f=>({id:f.source_id || f.upload_id,name:f.name,size:f.size,modified_ns:f.modified_ns}))};}
async function headlineInput(){
 const content=headlineContent();
 const bytes=new TextEncoder().encode(JSON.stringify(content));const hash=await crypto.subtle.digest('SHA-256',bytes);
 return {content,content_hash:[...new Uint8Array(hash)].map(v=>v.toString(16).padStart(2,'0')).join('')};
}
$('headlinePacket').onclick=async()=>{
 if(!editingAllowed)return;
 try{
  const input=await headlineInput();
  if(!editingAllowed || JSON.stringify(input.content)!==JSON.stringify(headlineContent()))throw Error('draft_changed');
  const packet={schema_version:1,project_id:projectId,draft_revision:session.record.revision,pending_changes:session.record.pending.length,...input,
   instructions:'สรุปหัวคลิปจากร่างที่ให้เท่านั้น ให้สั้น ชัด ไม่แต่งข้อมูลใหม่ ส่ง JSON ตาม response_schema โดยคง project_id, content_hash เดิม ข้อความไม่เกิน 80 ตัวอักษร ไม่มีขึ้นบรรทัดใหม่',
   response_schema:{schema_version:1,project_id:projectId,content_hash:input.content_hash,...(input.content.headline_slots.length?{texts:input.content.headline_slots.map(v=>({id:v.id,text:'หัวคลิปที่สรุป'}))}:{headline:'หัวคลิปที่สรุป'})}};
  const url=URL.createObjectURL(new Blob([JSON.stringify(packet,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='editorkrab-headline-ai.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  $('headlineStatus').textContent='ดาวน์โหลดแล้ว';
 }catch(_){$('headlineStatus').textContent='ดาวน์โหลดไม่ได้ ลองใหม่';}
};
$('applyHeadline').onclick=async()=>{
 if(!editingAllowed)return;
 try{
  const reply=draft.headline_reply,answer=JSON.parse(reply || ''),input=await headlineInput();
  if(!editingAllowed || draft.headline_reply!==reply || JSON.stringify(input.content)!==JSON.stringify(headlineContent()))throw Error('draft_changed');
  if(answer.schema_version!==1 || answer.project_id!==projectId || answer.content_hash!==input.content_hash)throw Error('invalid_headline');
  let items=structuredClone(headlineItems());
  const validText=text=>typeof text==='string' && !!text.trim() && text.length<=80 && !/[\r\n]/.test(text);
  if(Array.isArray(answer.texts)){
   if(!answer.texts.length || answer.texts.length>100 || new Set(answer.texts.map(v=>v?.id)).size!==answer.texts.length || answer.texts.some(v=>!v || !items.some(item=>item.id===v.id) || !validText(v.text)))throw Error('invalid_headline_items');
   items=items.map(item=>({...item,text:answer.texts.find(v=>v.id===item.id)?.text ?? item.text}));
  }else{
   if(!validText(answer.headline))throw Error('invalid_headline');
   if(items.length)items[0].text=answer.headline;else items=[{...headlinePreset(),id:crypto.randomUUID(),text:answer.headline}];
  }
  if(!validHeadlineItems(items))throw Error('invalid_headline_items');
  draft.headline_items=items;await save();textEditor.render();$('headlineStatus').textContent='ใช้แล้ว';
 }catch(_){$('headlineStatus').textContent='ใช้ไม่ได้ ลองคัดลอกคำตอบใหม่ทั้งหมด';}
};
async function addFiles(files) {
  if (!editingAllowed || !session || session.blocked) return;
  const all=Array.from(files);
  let supported=all.filter(f=>/\.(mp4|mov|m4v|mkv|avi|webm|mts|m2ts|wav|mp3|m4a|aac|flac|ogg|opus)$/i.test(f.name));
  if (!supported.length) {error('เลือกไฟล์วิดีโอหรือเสียง');return;}
  editing(false);$('sourceStatus').textContent='กำลังหาไฟล์ต้นฉบับในเครื่อง';
  let found=[];
  try {
    const response=await fetch(`/api/v1/projects/${encodeURIComponent(projectId)}/sources/locate`,json({files:supported.slice(0,1000).map(f=>({name:f.name,size:f.size,last_modified:f.lastModified}))}));
    if (response.ok) found=(await response.json()).files;
  } catch (_) { /* Not found means the copy below keeps the video safe in the project. */ }
  editing(!session.blocked && !session.volatile);
  if (!editingAllowed) return;
  const links=found.filter(source=>source && !draft.files.some(f=>f.path===source.path));
  if (draft.files.length+links.length>1000) {error('เลือกได้ครั้งละไม่เกิน 1,000 ไฟล์');return;}
  if (links.length) {draft.files.push(...links);await save();renderFiles();}
  supported=supported.filter((_,i)=>!found[i]);
  if (!supported.length) {$('sourceStatus').textContent=`ลิงก์ ${links.length} ไฟล์`;return;}
  const sameUpload=(entry,file)=>entry.upload_id && entry.name===(file.webkitRelativePath || file.name) && entry.size===file.size && entry.last_modified===file.lastModified;
  const additions=supported.filter(file=>!draft.files.some(entry=>sameUpload(entry,file))).length;
  if (draft.files.length+additions>1000) {error('เลือกได้ครั้งละไม่เกิน 1,000 ไฟล์');return;}
  editing(false);error('');activeUpload=new AbortController();$('cancelUpload').hidden=false;
  let completed=0;
  try {
    const selections=[];
    for (const file of supported) {
      const name=file.webkitRelativePath || file.name;
      let pending=draft.files.find(entry=>sameUpload(entry,file));
      if (!pending) {
        pending={name,size:file.size,last_modified:file.lastModified,upload_id:crypto.randomUUID()};
        draft.files.push(pending);
      }
      if (!selections.some(selection=>selection.pending.upload_id===pending.upload_id)) selections.push({file,pending});
    }
    // Persist the entire selection before the first transfer, including files waiting their turn.
    await save();renderFiles();
    for (const {file,pending} of selections) {
      if (activeUpload.signal.aborted) break;
      if (session.blocked || session.volatile) throw Error('draft_not_saved');
      const show=(bytes,total)=>{$('sourceStatus').textContent=`ตรวจและรับ ${completed+1}/${selections.length} · ${(bytes/1048576).toFixed(1)}/${(total/1048576).toFixed(1)} MB`;};
      show(0,file.size);
      const source=await uploadFile(projectId,file,pending,activeUpload.signal,show);
      if (session.blocked || session.volatile) throw Error('draft_not_saved');
      const index=draft.files.findIndex(f=>f.upload_id===pending.upload_id);
      if (index<0) throw Error('draft_changed');
      draft.files[index]={...source,upload_id:pending.upload_id,last_modified:pending.last_modified};await save();
      if (session.blocked || session.volatile) throw Error('draft_not_saved');
      renderFiles();completed++;
    }
    $('sourceStatus').textContent=activeUpload.signal.aborted ? 'หยุดรับไฟล์แล้ว · ลากไฟล์เดิมมาส่งต่อได้' : `รับแล้ว ${completed} ไฟล์`+(supported.length<all.length ? ` · ข้ามไฟล์อื่น ${all.length-supported.length} รายการ` : '');
  } catch (cause) {
    $('sourceStatus').textContent='รับไฟล์ยังไม่ครบ';
    const messages={upload_bytes_conflict:'ไฟล์ที่เลือกไม่ตรงกับส่วนที่รับไว้ เลือกต้นฉบับเดิม',
      source_storage_failed:'บันทึกไฟล์ไม่ได้ ตรวจว่าดิสก์ยังมีที่ว่าง',
      upload_checkpoint_invalid:'วิดีโอไม่ตรงกับที่บันทึกไว้ เลือกวิดีโอใหม่'};
    error(activeUpload.signal.aborted ? 'หยุดรับไฟล์แล้ว ลากไฟล์เดิมมาส่งต่อได้' : messages[cause.message] || 'รับไฟล์ไม่สำเร็จ ลากไฟล์เดิมมาวางเพื่อลองต่อ');
  } finally {
    activeUpload=undefined;$('cancelUpload').hidden=true;
    editing(!session.blocked && !session.volatile);renderFiles();
    $('files').value='';$('folders').value='';
  }
}
$('cancelUpload').onclick=()=>activeUpload?.abort();
// Never touches the original video: only copies the app kept inside the project folder go to the Recycle Bin.
async function removeFile(file) {
  if (!editingAllowed || !confirm(`ลบ "${file.name}" ออกจากโปรเจกต์?
ไฟล์ต้นฉบับไม่ถูกลบ`)) return;
  const index=draft.files.indexOf(file);if (index<0) return;
  draft.files.splice(index,1);await save();renderFiles();
  if (file.path) fetch(`/api/v1/projects/${encodeURIComponent(projectId)}/trash-file`,json({path:file.path})).catch(()=>{});
}
async function checkLinks() {
  const files=draft.files.filter(f=>f.path);
  if (!files.length) {lost.clear();return;}
  try {
    const response=await fetch('/api/v1/sources/check',json({files:files.map(linked)}));
    if (!response.ok) return;
    const {states}=await response.json();
    lost.clear();files.forEach((f,i)=>{if (states[i]!=='ok') lost.set(f.path,states[i]);});
    if (lost.size) error(`หาวิดีโอไม่เจอ ${lost.size} ไฟล์ กด "หาไฟล์" เพื่อชี้ที่อยู่ใหม่`);
    renderFiles();
  } catch (_) { /* The check is advisory; export re-checks every file before it starts. */ }
}
// Pointing at one moved video also relinks the others that moved into the same folder.
async function relink(file) {
  if (!editingAllowed) return;
  editing(false);error('');
  try {
    const others=draft.files.filter(f=>f!==file && lost.has(f.path));
    const response=await fetch('/api/v1/sources/relink',json({missing:[file,...others].map(linked)}));
    const found=await response.json();
    if (!response.ok) throw Error(found.error || 'relink_failed');
    if (found.cancelled) return;
    const swap=(path,source)=>{const i=draft.files.findIndex(f=>f.path===path);if (i>=0 && !draft.files.some(f=>f.path===source.path)) {draft.files[i]=source;lost.delete(path);}};
    swap(file.path,found.picked);for (const other of found.others) swap(other.path,other.source);
    await save();
    $('sourceStatus').textContent=`เจอแล้ว ${1+found.others.length} ไฟล์`;
  } catch (cause) {
    error(cause.message==='picker_busy' ? 'หน้าต่างเลือกไฟล์เปิดอยู่ กลับไปเลือกหรือกดยกเลิก' : 'ชี้ไฟล์ใหม่ไม่ได้ ลองอีกครั้ง');
  } finally {editing(!session.blocked && !session.volatile);renderFiles();}
}
async function pickNative(kind) {
  if (!editingAllowed) return;
  editing(false);$('sourceStatus').textContent='กำลังเลือกวิดีโอ';error('');
  try {
    const response=await fetch('/api/v1/sources/pick',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind})});
    const selected=await response.json();
    if (!response.ok) throw Error(selected.error || 'source_pick_failed');
    if (session.blocked || session.volatile) return;
    if (!selected.cancelled) {
      for (const file of selected.files) {
        const index=draft.files.findIndex(old=>old.path===file.path);
        if (index<0) draft.files.push(file);
        else if (draft.files[index].size!==file.size || draft.files[index].modified_ns!==file.modified_ns) draft.files[index]=file;
      }
      await save();renderFiles();
    }
    $('sourceStatus').textContent=selected.cancelled ? 'ยกเลิกการเลือก' : `เลือก ${selected.files.length} ไฟล์`+(selected.skipped ? ` · ข้ามไฟล์อื่น ${selected.skipped} รายการ` : '');
  } catch (cause) {
    const messages={picker_busy:'หน้าต่างเลือกไฟล์เปิดอยู่ กลับไปเลือกหรือกดยกเลิก',source_limit_exceeded:'เลือกได้ครั้งละไม่เกิน 1,000 ไฟล์'};
    error(messages[cause.message] || 'เลือกวิดีโอไม่ได้ ลองอีกครั้ง');$('sourceStatus').textContent='เลือกวิดีโอไม่สำเร็จ';
  } finally {editing(!session.blocked && !session.volatile);renderFiles();}
}
$('pick').onclick = () => pickNative('files'); $('folder').onclick = () => pickNative('folder');
for (const id of ['files', 'folders']) $(id).onchange = e => addFiles(e.target.files);
$('drop').ondragover = e => { e.preventDefault(); $('drop').classList.add('over'); };
$('drop').ondragleave = () => $('drop').classList.remove('over');
$('drop').ondrop = e => { e.preventDefault(); $('drop').classList.remove('over'); addFiles(e.dataTransfer.files); };
function downloadDraft() {
  const url = URL.createObjectURL(new Blob([JSON.stringify({schema_version:1,project_id:projectId,document:draft}, null, 2)], {type: 'application/json'}));
  const a = document.createElement('a'); a.href = url; a.download = 'editorkrab-ui-draft.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('backup').onclick = downloadDraft;
$('allProjects').onclick = () => location.assign('/');
$('theme').onclick = () => {
  const root=document.documentElement;
  const dark=root.dataset.theme ? root.dataset.theme==='dark' : !matchMedia('(prefers-color-scheme: light)').matches;
  root.dataset.theme=dark?'light':'dark';
  try{localStorage.setItem('editorkrab-theme',root.dataset.theme)}catch{}
};
for (const direction of ['undo','redo']) $(direction).onclick = async () => {
  if (!editingAllowed || session?.volatile || session?.blocked) return;
  pendingLocal++; unsaved = true; editing(false); $('undo').disabled = true; $('redo').disabled = true;
  $('saved').textContent = 'กำลังบันทึก';
  $('saved').dataset.state='pending';
  try { const step=draft.step; draft = await session.history(direction); upgradeFlow(); draft.step=step; render(); pendingLocal--; unsaved = pendingLocal > 0; }
  catch (_) { pendingLocal--; unsaved = true; }
  finally { editing(!session.blocked && !session.volatile); }
};
let copyingDraft=false,copyPending;
async function openCopy(document,sourceProject){
  if(copyingDraft)return;
  const editable=editingAllowed;copyingDraft=true;editing(false);$('fork').disabled=true;
  try{
    const key=JSON.stringify({document,source_project:sourceProject});
    if(copyPending?.key!==key)copyPending={key,id:crypto.randomUUID(),operation:crypto.randomUUID()};
    const response=await fetch(`/api/v1/drafts/${copyPending.id}/copy`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation_id:copyPending.operation,expected_revision:0,document,source_project:sourceProject}),signal:AbortSignal.timeout(120000)});
    if(!response.ok)throw Error('copy_failed');const ack=await response.json();
    if(ack.project_id!==copyPending.id||ack.revision!==1||!valid(ack.document))throw Error('invalid_ack');
    const target=new URL(location.href);target.searchParams.set('project',copyPending.id);
    unsaved=false;location.assign(target);
  }catch(_){error('เปิดสำเนาไม่ได้ ร่างเดิมยังอยู่ กดสำรองแล้วลองใหม่');editing(editable&&!session.blocked&&!session.volatile);}
  finally{copyingDraft=false;$('fork').disabled=false;}
}
$('fork').onclick=()=>openCopy(structuredClone(draft),projectId);
$('open').onclick = () => $('openFile').click();
for (const id of ['open','backup','allProjects']) $(id).addEventListener('click', () => { $(id).closest('details').open = false; });
$('openFile').onchange = async e => {
  const file = e.target.files[0]; if (!file) return;
  try {
    const value=JSON.parse(await file.text()),wrapped=value?.schema_version===1&&typeof value.project_id==='string'&&value.document;
    const incoming=wrapped?value.document:value;
    if (!valid(incoming)) throw Error('ไฟล์งานเสีย');
    if (!editingAllowed) return;
    await openCopy(incoming,wrapped?value.project_id:undefined);
  } catch (_) { error('เปิดไม่ได้ เลือกไฟล์สำรองจาก EditorKrab เท่านั้น งานเดิมยังอยู่'); }
};
async function start() {
editing(false);
try {
  const initial = () => {
    try {
      if (!localStorage.getItem(key + '-migrated')) {
        const raw = localStorage.getItem(key);
        if (raw) {
          try { const old = JSON.parse(raw); if (valid(old)) return old; }
          catch (_) { localStorage.setItem(key + '-recovery-' + Date.now(), raw); }
        }
      }
    } catch (_) { /* Legacy preferences cannot block IndexedDB recovery. */ }
    return draft;
  };
  session = new DraftSession(projectId, initial, status, valid);
  const restored = await session.open();
  if (!valid(restored)) throw Error('invalid_restored_draft');
  draft = restored; recallStep(); render(); editing(!session.blocked);
  jobWatcher=watchJobs(projectId,()=>session.record.revision,$('jobProgress'),job=>{
    speech.changed(job);
    if (job.task.kind==='probe' && job.task.source?.id) {probes.set(job.task.source.id,job);queueFilesRender();}
  },()=>{probes.clear();speech.reset();queueFilesRender();});
  if (session.created) {
    try { localStorage.setItem(key + '-migrated', projectId); } catch (_) { /* Raw legacy data remains untouched. */ }
  }
} catch (_) {
  try {
    const original = localStorage.getItem(key);
    if (original) localStorage.setItem(key + '-recovery-' + Date.now(), original);
    editing(false); $('saved').textContent = 'เปิดร่างไม่ได้';
    error('เปิดร่างไม่ได้ เก็บข้อมูลเดิมไว้ ดาวน์โหลดร่างก่อนลองใหม่');
  } catch (_) {
    for (const input of document.querySelectorAll('input,textarea,select,button')) input.disabled = true;
    error('พื้นที่บันทึกไม่พร้อม หยุดแก้ไขเพื่อรักษาร่างเดิม');
  }
} finally {document.body.dataset.loading='false';}
}
render(); start().then(checkLinks);
// Step 6 builds from the saved draft only, so it waits for autosave like the AI packet does.
var exporter=createExport({project:projectId,planReady,saved:()=>!!session?.record&&!session.blocked&&!session.volatile&&!session.record.pending.length&&!unsaved});
