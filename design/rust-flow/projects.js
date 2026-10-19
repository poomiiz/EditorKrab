// Start screen: pick, create or remove a project folder. The workspace (flow.js) opens with ?project=<id>.
const $=id=>document.getElementById(id);
let parent;
async function api(url,body){
  const response=await fetch(url,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const value=response.status===204?{}:await response.json();
  if(!response.ok)throw Error(value.error || 'request_failed');
  return value;
}
function say(message){$('error').textContent=message;$('error').hidden=!message;}
const go=id=>location.assign('/?project='+encodeURIComponent(id));
function button(text,onclick,className=''){const b=document.createElement('button');b.type='button';b.textContent=text;b.className=className;b.onclick=onclick;return b;}
function row(name,detail,...buttons){
  const li=document.createElement('li'),text=document.createElement('div'),strong=document.createElement('strong'),small=document.createElement('small');
  strong.textContent=name;small.textContent=detail;text.append(strong,small);li.append(text,...buttons);return li;
}
async function busy(work){
  for(const b of document.querySelectorAll('#projects button'))b.disabled=true;
  say('');
  try{await work();}
  catch(cause){
    const messages={not_a_project:'โฟลเดอร์นี้ไม่ใช่โปรเจกต์ EditorKrab',picker_busy:'หน้าต่างเลือกโฟลเดอร์เปิดอยู่',invalid_input:'ตั้งชื่ออื่น ชื่อนี้ใช้เป็นโฟลเดอร์ไม่ได้'};
    say(messages[cause.message] || (cause.message.startsWith('trash_failed') ? 'ย้ายไปถังขยะไม่ได้ ไฟล์ยังอยู่ครบ' : 'ทำไม่สำเร็จ ลองอีกครั้ง'));
  }
  finally{for(const b of document.querySelectorAll('#projects button'))b.disabled=false;}
}
async function load(){
  const {projects,legacy,default_parent}=await api('/api/v1/projects');
  parent??=default_parent;$('projectParent').textContent=parent;
  $('projectEmpty').hidden=projects.length>0;
  $('projectList').replaceChildren(...projects.map(p=>{
    const open=button(p.exists?'เปิด':'โฟลเดอร์หาย',()=>go(p.id),'primary');open.disabled=!p.exists;
    const remove=button('ลบ',()=>{
      if(!confirm(`ย้ายโฟลเดอร์โปรเจกต์ "${p.name}" ไปถังขยะ?\nวิดีโอต้นฉบับที่ลิงก์ไว้ไม่ถูกลบ`))return;
      busy(async()=>{await api(`/api/v1/projects/${encodeURIComponent(p.id)}/trash`,{});await load();});
    },'danger');
    remove.setAttribute('aria-label',`ลบโปรเจกต์ ${p.name}`);
    return row(p.title || p.name,p.folder,open,remove);
  }));
  $('legacyBox').hidden=!legacy.length;
  $('legacyList').replaceChildren(...legacy.map(d=>row(d.title || 'งานไม่มีชื่อ','ยังเก็บในแอป',
    button('ย้ายเข้าโฟลเดอร์',()=>busy(async()=>go((await api('/api/v1/projects',{name:d.title || 'งานเก่า',parent,id:d.id})).id))))));
}
$('projectNew').onsubmit=e=>{e.preventDefault();busy(async()=>go((await api('/api/v1/projects',{name:$('projectName').value,parent})).id));};
$('projectParentPick').onclick=()=>busy(async()=>{const v=await api('/api/v1/projects/parent',{});if(!v.cancelled){parent=v.folder;$('projectParent').textContent=parent;}});
$('projectOpen').onclick=()=>busy(async()=>{const v=await api('/api/v1/projects/open',{});if(!v.cancelled)go(v.id);});
document.body.dataset.view='projects';$('projects').hidden=false;document.body.dataset.loading='false';
busy(load);
