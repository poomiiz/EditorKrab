// Source clocks and raw transcripts stay immutable; manual text corrections live in the draft.
const $=id=>document.getElementById(id);
const stamp=(v)=>[v?.id,v?.size,v?.modified_ns].join('|');
const configKey=v=>v ? JSON.stringify([v.threads,...['engine','model','ffmpeg'].map(k=>{const a=v[k];return [a?.path,a?.size,a?.modified_ns,a?.sha256]})]) : '';
const clock=ms=>`${Math.floor(ms/60000)}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}.${String(ms%1000).padStart(3,'0')}`;
export function validTranscriptEdits(value){
 if(value===undefined)return true;
 return Array.isArray(value)&&value.length<=10000&&new Set(value.map(v=>JSON.stringify([v?.job_id,v?.segment_id]))).size===value.length&&value.every(v=>v&&Object.keys(v).length===5&&['job_id','segment_id'].every(k=>typeof v[k]==='string'&&v[k].length>0&&v[k].length<=256&&!/[\u0000-\u001f\u007f-\u009f]/.test(v[k]))&&/^[a-fA-F0-9]{64}$/.test(v.source_sha256)&&['text','original_text'].every(k=>typeof v[k]==='string'&&v[k].length<=2000));
}
export function createTranscription({project,read,state,writeEdit,changed}){
 const jobs=new Map();let settings=null,configuration='loading',download=null,downloadError='',downloadTimer=0,busy=false,stopStarting=false,page=0,chosen='',rowsKey='',audio,stopAt=0;
 let settingsGeneration=0,notice='',packetNotice='',playing=null;const suggestions=new Map(),fixAsked=new Set();
 const failed=new Set(['failed','cancelled']);
 const messages={transcription_not_configured:'ยังไม่ได้ติดตั้งตัวถอดเสียง',transcription_settings_changed:'ตัวถอดเสียงเปลี่ยนแล้ว กดลองอีกครั้ง',source_changed:'ไฟล์เปลี่ยนแล้ว เลือกไฟล์ใหม่',transcription_source_changed:'ไฟล์เปลี่ยนแล้ว เลือกไฟล์ใหม่',draft_conflict:'งานเปลี่ยนแล้ว รอให้บันทึกก่อนลองใหม่',revision_conflict:'งานเปลี่ยนแล้ว รอให้บันทึกก่อนลองใหม่',transcription_chunk_limit_exceeded:'ไฟล์ยาวเกิน 500 นาที แบ่งไฟล์ก่อนถอดเสียง',transcription_cache_corrupt:'ข้อความที่ถอดไว้เสีย ถอดเสียงใหม่'};
 async function request(url,body){
  const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(120000)});
  const value=await response.json();if(!response.ok)throw Error(messages[value.error] || 'เตรียมงานไม่ได้ ตรวจไฟล์และลองใหม่');return value;
 }
 function active(){return [...jobs.values()].filter(j=>j.task.kind==='transcribe'&&['queued','running'].includes(j.status));}
 function probe(file){return [...jobs.values()].find(j=>j.task.kind==='probe'&&j.task.path===file.path&&stamp(j.task.source)===stamp({id:file.source_id,size:file.size,modified_ns:file.modified_ns})&&j.status==='succeeded');}
 function eligible(){return read().files.filter(f=>f.source_id&&probe(f)?.result?.streams?.some(s=>s.codec_type==='audio'));}
 function ranges(file){const duration=Math.ceil((probe(file)?.result?.duration_s || 0)*1000);if(!duration || duration>30000000)return [];
  return Array.from({length:Math.ceil(duration/300000)},(_,n)=>[n*300000,Math.min(duration,(n+1)*300000)]);
 }
 function matching(file){return [...jobs.values()].filter(j=>j.task.kind==='transcribe'&&j.task.path===file.path&&stamp(j.task.context.source)===stamp({id:file.source_id,size:file.size,modified_ns:file.modified_ns})&&j.task.context.language===read().language&&configKey(j.task.settings)===configKey(settings));}
 function chunks(file){return ranges(file).map(([start,end])=>matching(file).filter(j=>j.task.context.start_ms===start&&j.task.context.end_ms===end).sort((a,b)=>(a.status==='succeeded'?-1:0)-(b.status==='succeeded'?-1:0)||b.created_at-a.created_at||a.id.localeCompare(b.id))[0]);}
 function complete(){const files=eligible();return configuration==='ready'&&files.length>0&&read().files.every(f=>probe(f))&&files.every(f=>{const list=chunks(f);return list.length>0&&list.every(j=>j?.status==='succeeded');});}
 function segments(file){return chunks(file).filter(j=>j?.status==='succeeded').flatMap(job=>(job.result?.transcript?.segments || []).map(segment=>({job,segment})));}
 function correction(job,segment){return (read().transcript_edits || []).find(v=>v.job_id===job.id&&v.segment_id===segment.id&&v.source_sha256===job.result.source.sha256&&v.original_text===segment.text);}
 function clearAudio(){if(audio){audio.pause();audio.removeAttribute('src');audio.load();audio.remove();audio=null;}}
 function play(file,segment){
  $('transcriptAudioError').hidden=true;
  const src=`/api/v1/projects/${encodeURIComponent(project)}/sources/${encodeURIComponent(file.source_id)}/audio`;
  if(!audio||audio.getAttribute('src')!==src){clearAudio();audio=document.createElement('audio');audio.controls=true;audio.preload='none';audio.src=src;
   audio.onerror=()=>{$('transcriptAudioError').textContent='เล่นไฟล์นี้ไม่ได้ ตรวจไฟล์หรือรูปแบบเสียง';$('transcriptAudioError').hidden=false;};
   audio.ontimeupdate=event=>{if(audio!==event.currentTarget)return;if(stopAt&&audio.currentTime>=stopAt)audio.pause();highlight(audio.currentTime*1000);};
   audio.onpause=()=>highlight(-1);$('transcriptAudioHost').append(audio);
  }
  stopAt=segment.end_ms/1000;playing=segment.id;const player=audio,seek=()=>{if(audio===player)player.currentTime=segment.start_ms/1000;};
  if(audio.readyState)seek();else player.addEventListener('loadedmetadata',seek,{once:true});
  return player.play().then(()=>true,()=>{$('transcriptAudioError').textContent='กดเล่นที่แถบเสียงเพื่อตรวจช่วงนี้';$('transcriptAudioError').hidden=false;return false;});
 }
 // Words light up while their stretch of the line plays; timing is interpolated from engine tokens.
 function highlight(ms){for(const w of $('transcriptRows').querySelectorAll('.transcript-words span'))w.toggleAttribute('data-on',w.parentElement.dataset.segmentId===playing&&ms>=+w.dataset.start&&ms<+w.dataset.end);}
 // Corrections learned from earlier hand fixes are offered per line; the person applies them, so undo stays theirs.
 async function suggest(list){
  const todo=list.filter(({job,segment})=>!fixAsked.has(job.id+'|'+segment.id));if(!todo.length)return;
  for(const {job,segment} of todo)fixAsked.add(job.id+'|'+segment.id);
  try{
   const response=await fetch('/api/v1/spelling/fix',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lines:todo.map(({segment})=>segment.text)})});
   if(!response.ok)throw Error('fix');const {lines}=await response.json();
   todo.forEach(({job,segment},n)=>{if(lines[n]!==segment.text)suggestions.set(job.id+'|'+segment.id,lines[n]);});
   paintRows(true);
  }catch(_){for(const {job,segment} of todo)fixAsked.delete(job.id+'|'+segment.id);notice='อ่านคำที่เคยแก้ไม่ได้';refresh();}
 }
 function learn(original,before,after){
  // Reverting to what was heard teaches nothing.
  if(!after.trim()||before===after||after===original)return;
  fetch('/api/v1/spelling/learn',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({old:before,new:after})})
   .then(r=>{if(!r.ok)throw Error('learn');}).catch(()=>{notice='จำคำที่แก้ไม่ได้ แต่บันทึกข้อความแล้ว';refresh();});
 }
 function paintRows(force=false){
  const files=eligible(),file=files.find(f=>f.source_id===chosen);if(!file){$('transcriptRows').replaceChildren();rowsKey='';return;}
  const all=segments(file);page=Math.min(page,Math.max(0,Math.ceil(all.length/50)-1));
  $('transcriptPage').textContent=all.length?`ช่วง ${page*50+1}–${Math.min(all.length,(page+1)*50)} จาก ${all.length}`:'ยังไม่ได้ถอดเสียง';
  $('transcriptPrevious').disabled=page===0;$('transcriptNext').disabled=(page+1)*50>=all.length;
  const language=[...new Set(chunks(file).filter(j=>j?.result).map(j=>j.result.transcript.detected_language))].map(v=>({th:'ไทย',en:'อังกฤษ'}[v] || v)).join(', ');
  $('transcriptLanguage').textContent=language?'ภาษาที่ตรวจพบ: '+language:'';
  const list=all.slice(page*50,(page+1)*50);suggest(list);const key=JSON.stringify(list.map(({job,segment})=>[job.id,segment.id,correction(job,segment)?.text ?? segment.text,suggestions.get(job.id+'|'+segment.id)]));
  if(!force&&($('transcriptRows').contains(document.activeElement)||key===rowsKey))return;
  rowsKey=key;
  $('transcriptRows').replaceChildren(...list.map(({job,segment})=>{
   const li=document.createElement('li'),line=document.createElement('div');line.className='row';
   const time=document.createElement('span');time.className='transcript-time caption';time.textContent=`${clock(segment.start_ms)}–${clock(segment.end_ms)}`;
   const listen=document.createElement('button');listen.type='button';listen.textContent='ฟังช่วงนี้';listen.onclick=()=>play(file,segment);line.append(time,listen);
   const label=document.createElement('label'),title=document.createElement('span');title.className='caption';title.textContent='คำพูดช่วงนี้';
   const field=document.createElement('textarea');field.maxLength=2000;field.value=correction(job,segment)?.text ?? segment.text;field.disabled=!state().editing || segment.text.length>2000;
   field.dataset.segmentId=segment.id;field.oninput=()=>{if(!state().editing)return;writeEdit({job_id:job.id,segment_id:segment.id,source_sha256:job.result.source.sha256,original_text:segment.text,text:field.value});};
   const shown=field.value;field.onchange=()=>{learn(segment.text,shown,field.value);};
   const offer=suggestions.get(job.id+'|'+segment.id);
   if(offer&&offer!==field.value&&!correction(job,segment)){const use=document.createElement('button');use.type='button';use.className='transcript-suggest';use.textContent='แก้เป็น: '+offer;use.disabled=!state().editing;
    use.onclick=()=>{writeEdit({job_id:job.id,segment_id:segment.id,source_sha256:job.result.source.sha256,original_text:segment.text,text:offer});if(!correction(job,segment)){notice='ใช้คำที่แก้ไม่ได้ ตรวจว่าแก้ไขได้อยู่';refresh();}else paintRows(true);};line.append(use);}
   const words=document.createElement('div');words.className='transcript-words caption';words.dataset.segmentId=segment.id;words.setAttribute('aria-hidden','true');
   words.replaceChildren(...(segment.words || []).map(w=>{const s=document.createElement('span');s.textContent=w.text;s.dataset.start=w.start_ms;s.dataset.end=w.end_ms;return s;}));
   label.append(title,field);li.append(line,label,words);return li;
  }));
 }
 function refresh(force=false){
  const editable=state().editing,ready=state().saved,files=eligible(),all=files.flatMap(chunks),completed=all.filter(j=>j?.status==='succeeded').length,running=active(),retry=all.filter(j=>j&&failed.has(j.status));
  const waiting=read().files.filter(f=>!probe(f)).length,silent=read().files.length-waiting-files.length;
  $('transcribe').disabled=!editable||!ready||busy||configuration!=='ready'||!files.length||running.length>0;
  $('transcribe').textContent=busy?'กำลังเตรียมงาน':complete()?'ใช้ข้อความที่ถอดไว้':'ถอดเสียง';
  $('transcribe').classList.toggle('primary',!$('transcribe').disabled&&!complete());$('pick').classList.toggle('primary',configuration!=='missing'&&($('transcribe').disabled||complete()));
  $('cancelTranscription').hidden=!running.length;$('cancelTranscription').disabled=!editable;
  $('retryTranscription').hidden=!retry.length;$('retryTranscription').disabled=!editable||!ready||busy||!!running.length;
  $('reloadAsr').hidden=configuration==='ready'||configuration==='loading';$('reloadAsr').disabled=busy;
  paintDownload();
  $('transcribeError').textContent=notice;$('transcribeError').hidden=!notice;
  $('transcriptPacketError').textContent=packetNotice;$('transcriptPacketError').hidden=!packetNotice;
  const phases={verifying:'กำลังตรวจไฟล์',extracting:'กำลังเตรียมเสียง',transcribing:'กำลังถอดเสียง',cache:'กำลังเปิดข้อความที่ถอดไว้'};
  $('transcribeState').textContent=busy?'เตรียมงาน':running.length?'กำลังทำ':complete()?'ถอดครบแล้ว':configuration==='loading'?'กำลังตรวจ':configuration==='ready'?'พร้อม':'ยังใช้ไม่ได้';
  $('transcribeHint').textContent=configuration==='missing'?'ดาวน์โหลดตัวถอดเสียงก่อนใช้ครั้งแรก':configuration==='error'?'ตรวจตัวถอดเสียงไม่ได้ กดลองอีกครั้ง':configuration==='loading'?'กำลังเตรียม':!read().files.length?'เลือกไฟล์ที่มีเสียงก่อน':!files.length?'รออ่านไฟล์ และตรวจว่ามีเสียง':!ready?'รอให้บันทึกงานสำเร็จก่อนเริ่ม':running.length?(phases[running.find(j=>j.status==='running')?.step] || 'รอคิว')+` · สำเร็จ ${completed}/${all.length} ช่วง`:all.length?`สำเร็จ ${completed}/${all.length} ช่วง`+(waiting?' · มีไฟล์ที่ยังตรวจไม่สำเร็จ':'')+(silent?` · ข้าม ${silent} ไฟล์ไม่มีเสียง`:''):'เลือกไฟล์ที่มีเสียงก่อน';
  const progress=$('transcribeProgress');progress.hidden=!all.length&&!busy;if(all.length){progress.max=all.length;progress.value=completed;}else progress.removeAttribute('value');
  const selected=chosen;if(!files.some(f=>f.source_id===chosen))chosen=files[0]?.source_id || '';
  if(selected!==chosen){page=0;rowsKey='';clearAudio();}
  const optionsKey=JSON.stringify(files.map(f=>[f.source_id,f.name]));if($('transcriptFile').dataset.options!==optionsKey){$('transcriptFile').replaceChildren(...files.map(f=>{const option=document.createElement('option');option.value=f.source_id;option.textContent=f.name;return option;}));$('transcriptFile').dataset.options=optionsKey;}
  $('transcriptFile').value=chosen;$('transcriptFile').disabled=!editable;
  $('transcriptResult').hidden=!files.some(f=>segments(f).length>0);paintRows(force);
  $('transcriptPacket').disabled=!editable||!ready||!complete()||busy;$('transcriptPacket').hidden=!complete();$('transcriptGoPrepare').hidden=complete();
  $('transcriptPacketTitle').textContent=complete()?'พร้อมส่งให้ AI':'ถอดเสียงในขั้น 1 ก่อน';
  $('transcriptPacketHint').textContent=complete()?'ไฟล์เล็ก มีแค่ข้อความ ไม่มีวิดีโอ':'ถอดเสียงเสร็จแล้วจึงดาวน์โหลดได้';
  changed();
 }
 async function loadSettings(){const generation=++settingsGeneration;configuration='loading';refresh();try{const response=await fetch('/api/v1/transcription/settings',{cache:'no-store',signal:AbortSignal.timeout(10000)});if(!response.ok)throw Error('settings');const value=await response.json();if(generation!==settingsGeneration)return;if(value&&(!Number.isInteger(value.threads)||!['engine','model','ffmpeg'].every(key=>typeof value[key]?.path==='string'&&/^[a-f0-9]{64}$/i.test(value[key]?.sha256))))throw Error('settings');settings=value;configuration=value?'ready':'missing';if(!value&&!download)queueMicrotask(pollDownload);}catch(_){if(generation!==settingsGeneration)return;configuration='error';}refresh();}
 $('transcribe').onclick=async()=>{
  if($('transcribe').disabled)return;busy=true;stopStarting=false;notice='';const snapshot=structuredClone(read()),saved=state(),configured=structuredClone(settings);refresh();
  try{
   for(const file of eligible()){
    if(stopStarting)break;
    if(JSON.stringify(snapshot.files)!==JSON.stringify(read().files)||snapshot.language!==read().language||!state().saved)throw Error('ไฟล์หรือภาษาเปลี่ยนแล้ว ตรวจงานและเริ่มใหม่');
    const returned=await request(`/api/v1/projects/${encodeURIComponent(project)}/transcription`,{request_id:crypto.randomUUID(),revision:saved.revision,source_id:file.source_id,settings:configured});
    if(!Array.isArray(returned))throw Error('รูปแบบงานไม่ถูกต้อง');for(const job of returned)jobs.set(job.id,job);refresh();
    if(stopStarting)for(const job of returned.filter(j=>['queued','running'].includes(j.status)))await request(`/api/v1/jobs/${encodeURIComponent(job.id)}/cancel`,{});
   }
  }catch(error){notice=error.name==='TimeoutError'?'ยังไม่ได้รับคำตอบ งานอาจเริ่มแล้ว ตรวจสถานะก่อนลองใหม่':error.message;}
  finally{busy=false;refresh();}
 };
 $('cancelTranscription').onclick=async()=>{stopStarting=true;notice='';try{for(const job of active()){const next=await request(`/api/v1/jobs/${encodeURIComponent(job.id)}/cancel`,{});jobs.set(next.id,next);}}catch(_){notice='ส่งคำสั่งหยุดไม่สำเร็จ ตรวจสถานะก่อนลองใหม่';}refresh();};
 $('retryTranscription').onclick=async()=>{busy=true;notice='';try{for(const job of eligible().flatMap(chunks).filter(j=>j&&failed.has(j.status))){const next=await request(`/api/v1/jobs/${encodeURIComponent(job.id)}/retry`,{});jobs.set(next.id,next);}}catch(_){notice='ลองใหม่ไม่ได้ ตรวจไฟล์วิดีโอ';}finally{busy=false;refresh();}};
 $('reloadAsr').onclick=loadSettings;
 // First run: fetch the speech model (and runtime) in the app, then switch on without a restart.
 const gb=n=>(n/1e9).toFixed(1)+' GB',mb=n=>(n/1e6).toFixed(1)+' MB';
 const downloadMessages={ffmpeg_missing:'ไม่พบ ffmpeg ในโฟลเดอร์แอป ติดตั้งแอปใหม่แล้วลองอีกครั้ง',runtime_missing:'เครื่องนี้ต้องติดตั้งตัวถอดเสียงเอง',checksum_mismatch:'ไฟล์ที่ได้ไม่ตรงต้นฉบับ กดลองต่อเพื่อโหลดใหม่',curl_missing:'เครื่องนี้ไม่มี curl สำหรับดาวน์โหลด',invalid_tools:'ไฟล์ที่โหลดมาใช้ถอดเสียงไม่ได้'};
 function downloadText(error){if(!error)return '';if(error.error==='disk_space')return `พื้นที่ไดรฟ์ ${error.drive} ไม่พอ ต้องการอีก ${gb(error.needed_bytes)}`;const code=String(error.error||error).split(':')[0];return code==='network'?'เชื่อมต่อไม่ได้ ตรวจอินเทอร์เน็ตแล้วกดลองต่อ':downloadMessages[code]||'ดาวน์โหลดไม่สำเร็จ กดลองต่อ';}
 function paintDownload(){
  const show=configuration==='missing'&&!!download,running=download?.state==='running',resumable=download&&download.received>0&&download.received<download.total;
  $('asrDownload').hidden=!show;if(!show)return;
  $('asrDownloadStart').textContent=(resumable?'ดาวน์โหลดต่อ':'ดาวน์โหลดตัวถอดเสียง')+(download.total?` (${gb(download.total)})`:'');
  $('asrDownloadStart').hidden=running;$('asrDownloadStart').disabled=busy||download.error==='ffmpeg_missing'||download.error==='runtime_missing';
  $('asrDownloadStart').classList.toggle('primary',!running);$('asrDownloadCancel').hidden=!running;
  $('asrDownloadNote').textContent=download.total?`ใช้ครั้งแรกต้องดาวน์โหลดตัวถอดเสียงก่อน ใช้พื้นที่ ${gb(download.total)}`:'ใช้ครั้งแรกต้องดาวน์โหลดตัวถอดเสียงก่อน';
  $('asrDownloadProgress').hidden=!running&&!resumable;$('asrDownloadProgress').value=download.total?download.received/download.total:0;
  $('asrDownloadRate').hidden=!running&&!resumable;
  $('asrDownloadRate').textContent=`${Math.floor(100*(download.total?download.received/download.total:0))}% · ${gb(download.received)} จาก ${gb(download.total)}`+(running?` · ${mb(download.bytes_per_second)}/วิ`:'');
  const message=downloadError||(download.state==='failed'?downloadText(download.error):'');$('asrDownloadError').textContent=message;$('asrDownloadError').hidden=!message;
 }
 async function pollDownload(){
  clearTimeout(downloadTimer);
  try{const response=await fetch('/api/v1/transcription/download',{cache:'no-store',signal:AbortSignal.timeout(10000)});if(!response.ok)throw Error('download');download=await response.json();}catch(_){downloadError='อ่านสถานะดาวน์โหลดไม่ได้';}
  if(download?.state==='done'){download=null;await loadSettings();return;}
  refresh();if(download?.state==='running')downloadTimer=setTimeout(pollDownload,700);
 }
 async function downloadCall(path){downloadError='';try{const response=await fetch(path,{method:'POST',signal:AbortSignal.timeout(30000)});const body=await response.json();if(!response.ok){downloadError=downloadText(body);refresh();return;}download=body;}catch(_){downloadError='เริ่มดาวน์โหลดไม่ได้ กดลองอีกครั้ง';}pollDownload();}
 $('asrDownloadStart').onclick=()=>downloadCall('/api/v1/transcription/download');
 $('asrDownloadCancel').onclick=()=>downloadCall('/api/v1/transcription/download/cancel');
 $('transcriptFile').onchange=()=>{chosen=$('transcriptFile').value;page=0;rowsKey='';clearAudio();refresh(true);};
 $('transcriptPrevious').onclick=()=>{page=Math.max(0,page-1);paintRows(true);};$('transcriptNext').onclick=()=>{page++;paintRows(true);};
 $('transcriptRows').onfocusout=event=>{if(!$('transcriptRows').contains(event.relatedTarget))queueMicrotask(()=>paintRows());};
 $('transcriptPacket').onclick=async()=>{
  if($('transcriptPacket').disabled)return;
  busy=true;packetNotice='';refresh();
  try{
   const saved=state(),snapshotDraft=structuredClone(read());
   for(const file of eligible())await request(`/api/v1/projects/${encodeURIComponent(project)}/transcription`,{request_id:crypto.randomUUID(),revision:saved.revision,source_id:file.source_id,settings});
   const sources=eligible().map(file=>({id:file.source_id,name:file.name,sha256:chunks(file)[0].result.source.sha256,duration_ms:ranges(file).at(-1)[1],segments:segments(file).map(({job,segment})=>({...segment,text:correction(job,segment)?.text ?? segment.text,original_text:segment.text,text_corrected:!!correction(job,segment),alignment:'unverified'}))}));
   if(sources.some(source=>source.segments.some(segment=>!segment.text.trim())))throw Error('มีคำพูดว่าง ตรวจข้อความก่อนส่งให้ AI');
   const content={language:read().language,settings:{engine_sha256:settings.engine.sha256,model_sha256:settings.model.sha256,ffmpeg_sha256:settings.ffmpeg.sha256,threads:settings.threads},sources};
   const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(content))),hash=[...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,'0')).join('');
   if(!state().saved||state().revision!==saved.revision||JSON.stringify(snapshotDraft)!==JSON.stringify(read())||!complete())throw Error('งานเปลี่ยนแล้ว ดาวน์โหลดใหม่อีกครั้ง');
   const packet={schema_version:1,project_id:project,draft_revision:saved.revision,content_hash:hash,content,instructions:'แบ่งเรื่องและเรียงช่วงพูดโดยอ้าง segment_ids ที่ให้เท่านั้น ห้ามสร้างเวลาใหม่ รักษาตอนจบให้ครบ ไม่ถือว่าคนละไฟล์คือกล้องซิงก์กัน เวลาระดับคำและคนพูดยังไม่ยืนยัน ส่ง JSON ตาม response_schema',response_schema:{schema_version:1,project_id:project,draft_revision:saved.revision,content_hash:hash,stories:[{id:'story-1',title:'ชื่อเรื่อง',segment_ids:[]}]}};
   const url=URL.createObjectURL(new Blob([JSON.stringify(packet,null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download='editorkrab-transcript-ai.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }catch(error){packetNotice=error.message;}finally{busy=false;refresh();}
 };
 queueMicrotask(loadSettings);
 // Every subtitle line as it will show (hand edit if any), timed on its own source file.
 function texts(){return eligible().flatMap(file=>segments(file).map(({job,segment})=>({kind:'subtitle',id:job.id+'|'+segment.id,text:correction(job,segment)?.text ?? segment.text,start_ms:segment.start_ms,end_ms:segment.end_ms,source:file.name})));}
 function find(id){for(const file of eligible())for(const row of segments(file))if(row.job.id+'|'+row.segment.id===id)return {file,...row};throw Error('line_not_found');}
 // For checking lines from the subtitle step: hear one line, or replace its text (kept as a hand edit and learned).
 function listen(id){const {file,segment}=find(id);return play(file,segment);}
 function edit(id,text){
  const {job,segment}=find(id),before=correction(job,segment)?.text ?? segment.text;
  writeEdit({job_id:job.id,segment_id:segment.id,source_sha256:job.result.source.sha256,original_text:segment.text,text});
  if((correction(job,segment)?.text ?? segment.text)!==text)throw Error('edit_not_saved');
  learn(segment.text,before,text);rowsKey='';paintRows(true);
 }
 return {changed:job=>{jobs.set(job.id,job);refresh();},reset:()=>{jobs.clear();refresh();},refresh,complete,texts,listen,edit};
}
