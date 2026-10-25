// The project screen: make a new project or open one. A project holds any number of videos.
const $=id=>document.getElementById(id);
const steps=['ใส่วิดีโอ','สรุปเนื้อหา','ตัดต่อ','ตรวจซับ','หัวคลิป','รับคลิป'];
const film='<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="m10 9 5 3-5 3Z"/></svg>';

function card(project){
  const li=document.createElement('li'),link=document.createElement('a');
  link.className='project';link.href='/?project='+encodeURIComponent(project.project_id);
  const thumb=document.createElement('span');thumb.className='thumb';thumb.innerHTML=film;
  if(project.cover){
    const still=document.createElement('video');still.muted=true;still.preload='metadata';still.setAttribute('aria-hidden','true');
    still.src=`/api/v1/projects/${encodeURIComponent(project.project_id)}/sources/${encodeURIComponent(project.cover)}/preview#t=2`;
    still.onerror=()=>still.remove();thumb.append(still);
  }
  const name=document.createElement('span'),meta=document.createElement('span'),title=project.title.trim();
  name.className='name';name.textContent=title || 'ไม่มีชื่อ';name.title=name.textContent;if(!title)name.dataset.untitled='';
  const step=Math.min(Math.max(project.step,1),6);
  meta.className='meta';meta.textContent=(project.videos?`${project.videos} วิดีโอ`:'ยังไม่มีวิดีโอ')+` · ขั้น ${step} ${steps[step-1]}`;
  link.append(thumb,name,meta);li.append(link,actions(project,li,name));return li;
}

const pencil='<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16v4Z"/><path d="m13.5 6.5 4 4"/></svg>';
const bin='<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>';
const label=project=>project.title.trim() || 'ไม่มีชื่อ';

function actions(project,li,name){
  const box=document.createElement('div');box.className='actions';
  const rename=document.createElement('button');rename.type='button';rename.innerHTML=pencil;
  rename.title='แก้ชื่อ';rename.setAttribute('aria-label',`แก้ชื่อ ${label(project)}`);
  const remove=document.createElement('button');remove.type='button';remove.className='danger';remove.innerHTML=bin;
  remove.title='ลบ';remove.setAttribute('aria-label',`ลบ ${label(project)}`);
  rename.onclick=()=>startRename(project,name);
  remove.onclick=()=>hide(project,li);
  box.append(rename,remove);return box;
}

// the name turns into a box in place; Enter or leaving it saves, Esc keeps the old name
function startRename(project,name){
  const input=document.createElement('input');input.className='rename';input.maxLength=120;input.value=project.title;
  input.setAttribute('aria-label','ชื่อโปรเจกต์');
  let done=false;
  const finish=async keep=>{
    if(done)return;done=true;
    const title=input.value.trim().slice(0,120);
    if(keep && title!==project.title){
      try{await saveTitle(project.project_id,title);project.title=title;}
      catch(_){showToast('แก้ชื่อไม่สำเร็จ ลองอีกครั้ง');}
    }
    name.textContent=label(project);name.title=name.textContent;name.toggleAttribute('data-untitled',!project.title.trim());
    input.replaceWith(name);
  };
  input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();finish(true);}if(e.key==='Escape')finish(false);};
  input.onblur=()=>finish(true);
  name.replaceWith(input);input.focus();input.select();
}

async function saveTitle(id,title){
  const url='/api/v1/drafts/'+encodeURIComponent(id);
  const read=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(10000)});
  if(!read.ok)throw Error('read_failed');
  const draft=await read.json();
  const response=await fetch(url,{method:'PUT',headers:{'Content-Type':'application/json'},signal:AbortSignal.timeout(10000),
    body:JSON.stringify({operation_id:crypto.randomUUID(),expected_revision:draft.revision,document:{...draft.document,title}})});
  if(!response.ok)throw Error('save_failed');
}

// deleting hides the project; its videos and transcripts stay, so undo brings it back whole
let toastTimer;
function showToast(text,undo){
  clearTimeout(toastTimer);$('toastText').textContent=text;$('undo').hidden=!undo;$('undo').onclick=undo;$('toast').hidden=false;
  toastTimer=setTimeout(()=>{$('toast').hidden=true;},8000);
}
async function hide(project,li){
  const id=encodeURIComponent(project.project_id);
  try{
    const response=await fetch(`/api/v1/drafts/${id}/hide`,{method:'POST',signal:AbortSignal.timeout(10000)});
    if(!response.ok)throw Error('hide_failed');
    li.remove();
    showToast(`ลบ "${label(project)}" แล้ว`,async()=>{
      $('toast').hidden=true;
      try{const r=await fetch(`/api/v1/drafts/${id}/restore`,{method:'POST',signal:AbortSignal.timeout(10000)});if(!r.ok)throw Error();load();}
      catch(_){showToast('เลิกทำไม่สำเร็จ ลองอีกครั้ง');}
    });
  }catch(_){showToast('ลบไม่สำเร็จ ลองอีกครั้ง');}
}

async function load(){
  const list=$('projects'),first=list.firstElementChild;
  $('state').hidden=true;
  try{
    const response=await fetch('/api/v1/drafts',{cache:'no-store',signal:AbortSignal.timeout(10000)});
    if(!response.ok)throw Error('list_failed');
    const projects=await response.json();
    if(!Array.isArray(projects))throw Error('list_invalid');
    list.replaceChildren(first,...projects.map(card));
    if(!projects.length){$('state').textContent='ยังไม่มีโปรเจกต์ กดโปรเจกต์ใหม่เพื่อเริ่ม';$('state').hidden=false;}
  }catch(_){
    list.replaceChildren(first);
    $('state').replaceChildren('อ่านรายการโปรเจกต์ไม่ได้');
    const retry=document.createElement('button');retry.type='button';retry.textContent='ลองอีกครั้ง';retry.onclick=load;
    $('state').append(retry);$('state').hidden=false;
  }
}

// one click makes the project, named by today's date like CapCut; rename it at the top of the editor
$('create').onclick=async()=>{
  const id=crypto.randomUUID(),now=new Date();
  const title='โปรเจกต์ '+now.toLocaleDateString('th-TH',{day:'numeric',month:'short'})+' '+now.toLocaleTimeString('th-TH',{hour:'2-digit',minute:'2-digit'});
  $('create').disabled=true;$('createError').hidden=true;
  try{
    const document={flow_version:2,headline_items:[],step:1,title,language:'auto',files:[],plan:'',style:'bold16'};
    const response=await fetch('/api/v1/drafts/'+encodeURIComponent(id),{method:'PUT',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({operation_id:crypto.randomUUID(),expected_revision:0,document}),signal:AbortSignal.timeout(10000)});
    if(!response.ok)throw Error('create_failed');
    try{localStorage.setItem('editorkrab-last-project',id);}catch(_){/* the address carries the project */}
    location.href='/?project='+encodeURIComponent(id);
  }catch(_){
    $('createError').textContent='สร้างโปรเจกต์ไม่สำเร็จ ลองอีกครั้ง';$('createError').hidden=false;$('create').disabled=false;
  }
};
load();
