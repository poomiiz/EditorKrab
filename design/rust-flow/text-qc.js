// Checks the clip's on-screen texts in the subtitle step; each flagged line can be heard, then left as is or retyped.
const clock=ms=>`${String(Math.floor(ms/60000)).padStart(2,'0')}:${(ms/1000%60).toFixed(1).padStart(4,'0')}`;
const kinds={headline:'หัวคลิป',super:'ข้อความซ้อน',subtitle:'ซับ',emphasis:'คำเน้น',english:'ซับอังกฤษ',graphic:'กราฟิก'};
const levels={critical:'ร้ายแรง',moderate:'ปานกลาง',minor:'เล็กน้อย'};
const order=['critical','moderate','minor'];
export function createTextQc({texts,summary,list,listen,edit,go}){
 let lastKey='',running=false,again=false;
 async function run(force=false){
  const items=texts(),key=JSON.stringify(items);
  if(!force&&key===lastKey)return;
  if(running){again=true;return;}
  running=true;lastKey=key;
  try{
   go.hidden=items.length>0;summary.closest('.qc').hidden=!items.length;
   if(!items.length){summary.textContent='ยังไม่มีซับ ถอดเสียงที่ขั้น 1 ก่อน';list.replaceChildren();return;}
   summary.textContent='กำลังตรวจ';
   // the server takes only these fields; subtitle lines also carry segment and source ids for steps 2 and 3
   const response=await fetch('/api/v1/text-qc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items:items.map(({kind,id,text,start_ms,end_ms,source})=>({kind,id,text,start_ms,end_ms,source}))})});
   const value=await response.json();if(!response.ok)throw Error(value.error || 'text_qc_failed');
   paint(items,value.findings);
  }catch(error){lastKey='';summary.textContent='ตรวจข้อความไม่ได้ ('+error.message+')';}
  finally{running=false;if(again){again=false;run();}}
 }
 function paint(items,findings){
  // One row per text that needs a look, worst first in time order; texts without findings pass silently.
  const groups=new Map();
  for(const f of findings){const key=f.id ?? `${f.kind}@${f.start_ms}`;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(f);}
  const count=level=>[...groups.values()].filter(g=>order.find(l=>g.some(f=>f.severity===l))===level).length;
  summary.textContent=groups.size?`🔴 ${count('critical')}   🟡 ${count('moderate')}   🟢 ${count('minor')}`:'✅ ผ่านทั้งหมด';summary.title=`ตรวจ ${items.length} ข้อความ · แดง ร้ายแรง · เหลือง ปานกลาง · เขียว เล็กน้อย`;
  list.replaceChildren(...[...groups.values()].map(group=>{
   const first=group[0],worst=order.find(level=>group.some(f=>f.severity===level));
   const row=document.createElement('li');row.className='qc-finding';row.dataset.severity=worst;
   const level=document.createElement('span');level.className='badge';level.textContent=levels[worst];
   const head=document.createElement('div');head.className='qc-head';
   head.textContent=`${clock(first.start_ms)}–${clock(first.end_ms)} · ${kinds[first.kind] || first.kind}${first.source?' · '+first.source:''}`;
   const shown=document.createElement('p');shown.className='qc-shown';shown.textContent=first.shown;
   const problems=document.createElement('ul');problems.className='qc-problems';
   problems.replaceChildren(...group.map(f=>{const li=document.createElement('li');li.textContent=f.problem+(f.category==='graphic'?' (งานกราฟิก)':'');return li;}));
   const actions=document.createElement('div');actions.className='qc-actions';
   const editable=first.id!==undefined&&first.id!==null||first.kind==='headline';
   if(first.kind==='subtitle'&&first.id){const hear=document.createElement('button');hear.type='button';hear.className='icon-only';hear.dataset.icon='play';hear.title='ฟัง';hear.setAttribute('aria-label','ฟัง');hear.onclick=async()=>{if(!await listen(first.id))summary.textContent='เล่นเสียงช่วงนี้ไม่ได้ ตรวจไฟล์ที่ขั้น 1';};actions.append(hear);}
   if(editable){
    const fix=document.createElement('button');fix.type='button';fix.className='icon-only';fix.dataset.icon='edit';fix.title='แก้';fix.setAttribute('aria-label','แก้');
    fix.onclick=()=>{
     const form=document.createElement('form');form.className='qc-edit';
     const input=document.createElement('input');input.value=group.find(f=>f.fix)?.fix ?? first.shown;input.maxLength=2000;input.setAttribute('aria-label','ข้อความที่แก้');
     const save=document.createElement('button');save.className='primary';save.dataset.icon='check';save.textContent='บันทึก';
     const cancel=document.createElement('button');cancel.type='button';cancel.dataset.icon='x';cancel.textContent='ยกเลิก';cancel.onclick=()=>{form.remove();fix.hidden=false;};
     form.onsubmit=event=>{event.preventDefault();const text=input.value.trim();if(!text){input.focus();return;}
      try{edit(first,text);run(true);}catch(_){summary.textContent='บันทึกข้อความไม่ได้ ตรวจว่าแก้ไขได้อยู่';}};
     form.append(input,save,cancel);fix.hidden=true;row.append(form);input.focus();
    };
    actions.append(fix);
   }
   row.append(level,head,shown,problems,actions);return row;
  }));
 }
 return {run};
}
