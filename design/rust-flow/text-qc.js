// Checks the clip's on-screen texts in the subtitle step; each flagged line can be heard, then left as is or retyped.
const clock=ms=>`${String(Math.floor(ms/60000)).padStart(2,'0')}:${(ms/1000%60).toFixed(1).padStart(4,'0')}`;
const kinds={headline:'หัวคลิป',super:'ข้อความซ้อน',subtitle:'ซับ',emphasis:'คำเน้น',english:'ซับอังกฤษ',graphic:'กราฟิก'};
const levels={critical:'ร้ายแรง',moderate:'ปานกลาง',minor:'เล็กน้อย'};
const order=['critical','moderate','minor'];
export function validEnglishCues(value){
 if(value===undefined)return true;
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(k=>!['input_revision','translations'].includes(k))||!/^[a-f0-9]{16}$/.test(value.input_revision || '')||!value.translations||typeof value.translations!=='object'||Array.isArray(value.translations))return false;
 const items=Object.entries(value.translations);return items.length<=20000&&items.every(([id,text])=>/^e_[a-f0-9]{16}$/.test(id)&&typeof text==='string'&&text.trim().length>0&&text.length<=60&&!/[\u0000-\u001f\u007f]/u.test(text));
}
// Only transfer clocks measured for complete source tokens. Missing text and token splits
// require review; interpolating characters would manufacture word-level precision.
const clockText=text=>String(text || '').normalize('NFKC').replace(/[\s\p{P}\p{S}]/gu,'').toLocaleLowerCase();
export function alignWordClocks(oldWords,fresh,start,end){
 const tokens=fresh.filter(w=>clockText(w.text));
 if(!oldWords.length || !tokens.length)return null;
 let joined='',bounds=new Map();
 for(const [i,w] of tokens.entries()){
  if(!Number.isFinite(w.start_ms)||!Number.isFinite(w.end_ms)||w.end_ms<=w.start_ms||w.start_ms<start||w.end_ms>end||(i&&w.start_ms<tokens[i-1].end_ms))return null;
  bounds.set(joined.length,{...bounds.get(joined.length),start:w.start_ms});joined+=clockText(w.text);bounds.set(joined.length,{...bounds.get(joined.length),end:w.end_ms});
 }
 if(oldWords.map(w=>clockText(w.text)).join('')!==joined)return null;
 const result=[];let cursor=0,lastEnd=start;
 for(const word of oldWords){const text=clockText(word.text);if(!text)return null;let at=joined.indexOf(text,cursor),found=null;
  while(at>=0){const a=bounds.get(at)?.start,b=bounds.get(at+text.length)?.end;
   if(a!==undefined&&b!==undefined&&a>=lastEnd&&b>a){found=[a,b];break;}at=joined.indexOf(text,at+1);}
  if(!found)return null;result.push(found);lastEnd=found[1];cursor=at+text.length;
 }
 return result;
}
export function wordClockIssues(lines,offset=0){
 return lines.flatMap(line=>!line.words?.length&&line.text?.trim()?[{id:line.segment_id || line.id,text:line.text,start_ms:line.start_ms,end_ms:line.end_ms,problem:'ท่อนนี้ยังไม่มีเวลาคำ'}]:(line.words || []).flatMap((w,i)=>{
  const span=w.end_ms-w.start_ms,previous=line.words[i-1];
  const problem=!Number.isFinite(w.start_ms)||!Number.isFinite(w.end_ms)?'ไม่มีเวลาคำ':span<=0?'ไม่มีระยะเวลาคำ':(Number.isFinite(line.start_ms)&&w.start_ms<line.start_ms+offset)||(Number.isFinite(line.end_ms)&&w.end_ms>line.end_ms+offset)?'คำเลยขอบท่อน':previous&&w.start_ms<previous.end_ms?'เวลาคำซ้อนกัน':span>=1000?'คำค้างเกิน 1 วินาที':null;
  return problem?[{id:line.segment_id || line.id,text:w.text,start_ms:w.start_ms,end_ms:w.end_ms,problem}]:[];
 }));
}
export function createTextQc({texts,summary,list,listen,edit,go,verdicts=()=>{}}){
 let lastKey='',running=false,again=false;
 async function run(force=false){
  const items=texts(),key=JSON.stringify(items);
  if(!force&&key===lastKey)return;
  if(running){again=true;return;}
  running=true;lastKey=key;
  try{
   go.hidden=items.length>0;
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
  verdicts(new Map([...groups].map(([id,g])=>[id,{worst:order.find(l=>g.some(f=>f.severity===l)),problems:g.map(f=>f.problem)}])));
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
