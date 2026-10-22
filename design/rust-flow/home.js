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
  link.append(thumb,name,meta);li.append(link);return li;
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
