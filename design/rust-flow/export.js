// Step 6: asks the app to build one 9:16 MP4 per AI story and reports progress until the files exist.
const messages={plan_missing:'ยังไม่มีคำตอบจาก AI กลับไปวางคำตอบที่ขั้น 3',plan_invalid:'คำตอบจาก AI อ่านไม่ได้ กลับไปวางใหม่ที่ขั้น 3',plan_has_no_stories:'คำตอบจาก AI ไม่มีเรื่องให้ตัด กลับไปวางใหม่ที่ขั้น 3',
 segment_unknown:'คำตอบจาก AI อ้างช่วงพูดที่ไม่มีแล้ว ส่งไฟล์ให้ AI ใหม่ที่ขั้น 2',story_empty:'มีเรื่องที่ไม่มีช่วงพูด ส่งไฟล์ให้ AI ใหม่ที่ขั้น 2',source_missing:'หาไฟล์วิดีโอต้นฉบับไม่เจอ ใส่วิดีโอใหม่ที่ขั้น 1',
 transcription_not_configured:'ยังไม่มีตัวถอดเสียง',output_folder:'สร้างโฟลเดอร์เก็บคลิปไม่ได้',ffmpeg_failed:'สร้างวิดีโอไม่สำเร็จ',export_check_failed:'ตรวจวิดีโอที่ได้แล้วไม่ผ่าน จึงไม่ได้บันทึก',not_found:'ยังไม่ได้บันทึกงาน รอสักครู่แล้วลองใหม่',
 capcut_not_found:'ไม่เจอ CapCut ในเครื่องนี้ ติดตั้งและเปิด CapCut หนึ่งครั้งก่อน',capcut_write:'เขียนโปรเจกต์ลงโฟลเดอร์ของ CapCut ไม่ได้',source_unreadable:'อ่านไฟล์วิดีโอต้นฉบับไม่ได้'};
const roleNames={emphasis:'คำสำคัญ',english:'คำแปลอังกฤษ'};
export function exportMessage(error){const code=String(error||'').split(':')[0];return messages[code]||'สร้างคลิปไม่สำเร็จ กดลองอีกครั้ง';}
export function createExport({project,saved,planReady}){
 const $=id=>document.getElementById(id),base=`/api/v1/projects/${encodeURIComponent(project)}/export`;
 let status={state:'idle'},error='',timer=0,busy=false;
 function paint(){
  const running=status.state==='running',done=status.state==='done',ready=planReady();
  $('exportStart').hidden=running||(!ready&&!done);$('exportGoPlan').hidden=running||done||ready;$('exportStart').disabled=busy||!ready||!saved();
  $('exportStart').textContent=done?'สร้างใหม่อีกครั้ง':'สร้างคลิป';
  $('exportStart').classList.toggle('primary',!done);$('exportReveal').classList.toggle('primary',done);
  $('exportCancel').hidden=!running;
  $('exportProgress').hidden=!running;$('exportProgress').value=status.progress||0;
  $('exportStatus').textContent=running?`กำลังสร้างคลิป ${status.story||1}/${status.stories} · ${Math.floor(100*(status.progress||0))}%`:done?`สร้างเสร็จ ${status.files.length} คลิป`:status.state==='cancelled'?'ยกเลิกแล้ว':!ready?'วางคำตอบจาก AI ที่ขั้น 3 ก่อน':'คลิปละเรื่องตามคำตอบจาก AI ขนาด 9:16 พร้อมซับและหัวคลิป';
  const message=error||(status.state==='failed'?exportMessage(status.error):'');$('exportError').textContent=message;$('exportError').hidden=!message;
  const skipped=(status.skipped||[]).map(r=>roleNames[r]||r);$('exportSkipped').hidden=!skipped.length;$('exportSkipped').textContent=skipped.length?`ยังใส่ ${skipped.join(' และ ')} ในวิดีโอไม่ได้ คลิปนี้มีแค่คำพูดและหัวคลิป`:'';
  const list=$('exportFiles');list.replaceChildren(...(status.files||[]).map(path=>{const li=document.createElement('li');li.textContent=path.split(/[\\/]/).pop();li.title=path;return li;}));
  list.hidden=!status.files?.length;$('exportReveal').hidden=!status.files?.length;
  paintCapcut();
 }
 async function poll(){
  clearTimeout(timer);
  try{const response=await fetch(base,{cache:'no-store',signal:AbortSignal.timeout(10000)});if(!response.ok)throw Error('status');status=await response.json();}catch(_){error='อ่านสถานะไม่ได้';}
  paint();if(status.state==='running')timer=setTimeout(poll,700);
 }
 async function call(path){
  busy=true;error='';paint();
  try{const response=await fetch(path,{method:'POST',signal:AbortSignal.timeout(30000)});const body=response.status===204?null:await response.json();if(!response.ok){error=exportMessage(body?.error);return;}if(body)status=body;}
  catch(_){error='ติดต่อแอปไม่ได้ กดลองอีกครั้ง';}
  finally{busy=false;}
  poll();
 }
 $('exportStart').onclick=()=>call(base);
 $('exportCancel').onclick=()=>call(base+'/cancel');
 $('exportReveal').onclick=()=>call(base+'/reveal');
 // CapCut projects are written in one request; no progress to poll
 let capcut='';
 function paintCapcut(){$('capcutStart').disabled=busy||!planReady()||!saved();$('capcutStatus').textContent=capcut;$('capcutStatus').hidden=!capcut;}
 $('capcutStart').onclick=async()=>{
  busy=true;error='';capcut='กำลังสร้างโปรเจกต์ CapCut...';paint();
  try{const response=await fetch(`/api/v1/projects/${encodeURIComponent(project)}/capcut`,{method:'POST',signal:AbortSignal.timeout(120000)});const body=await response.json();
   if(!response.ok){capcut='';error=exportMessage(body?.error);}
   else{const fonts=body.missing_fonts||[];capcut=`เพิ่ม ${body.folders.length} โปรเจกต์ใน CapCut แล้ว เปิด CapCut เพื่อแก้ต่อ`+(fonts.length?` (เครื่องนี้ไม่มีฟอนต์ ${fonts.join(', ')} จึงใช้ฟอนต์อื่นแทน)`:'');}}
  catch(_){capcut='';error='ติดต่อแอปไม่ได้ กดลองอีกครั้ง';}
  finally{busy=false;paint();}
 };
 poll();
 return {refresh:paint};
}
