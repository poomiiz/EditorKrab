// Step 2's story cards, grouped by the video each story comes from, and step 3's editor: a 360p preview, the
// story's subtitle on top of it, and a zoomable timeline where the AI's cuts can be kept or dropped line by line.
const $=id=>document.getElementById(id);
const clock=ms=>`${Math.floor(ms/60000)}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}`;
// The AI's pasted answer, checked before it counts. A ```json fence, words around the JSON and a bare list of stories
// (some AIs drop the {"stories": ...} wrapper) are fixed into the one shape export reads (`fixed`); anything else names
// the first thing missing so the paste can be redone, never guessed at.
// the JSON inside a chat answer: a ```json fence or words around it are dropped; json is undefined when none parses
function readJson(raw){
 // Gemini's copy indents with non-breaking spaces (U+00A0), which JSON does not allow as spacing; chat pages also
 // slip in other wide or zero-width spaces. They become plain spaces before reading.
 raw=raw.replace(/[   -​  　﻿]/g,' ');
 const fence=raw.match(/```(?:json)?\s*([\s\S]*?)```/),body=(fence?fence[1]:raw).trim();
 const from=body.search(/[[{]/),to=Math.max(body.lastIndexOf('}'),body.lastIndexOf(']')),json=from<0?body:body.slice(from,to+1);
 try{return {value:JSON.parse(json),json};}catch(_){return {};}
}
// chat AIs rename fields (Gemini answered story_id and no title); take the usual synonyms, else the summary's opening
export function storyFields(s,index){
 const summary=String(s?.summary || s?.description || '').trim();
 const title=String(s?.title || s?.name || s?.topic || s?.headline || '').trim() || (summary.split(/[ ,.]/).filter(Boolean).slice(0,6).join(' ').slice(0,48) || `เรื่อง ${index+1}`);
 return {id:String(s?.id || s?.story_id || `story-${index+1}`),title,summary};
}
export function checkPlan(text,lines){
 const raw=String(text || '').trim();if(!raw)return {error:''};
 const {value,json}=readJson(raw);if(json===undefined)return {error:'คำตอบไม่ครบ ลองคัดลอกใหม่ทั้งหมด'};
 if(value?.response_schema)return {error:'นี่คือข้อความที่ส่งให้ AI ยังไม่ใช่คำตอบ วางในแชท AI ก่อน แล้วคัดลอกคำตอบของ AI กลับมา'};
 const plan=Array.isArray(value)?{stories:value}:value;
 if(!Array.isArray(plan?.stories) || !plan.stories.length)return {error:'ไม่พบรายการเรื่องในคำตอบ ลองให้ AI ตอบใหม่ตามไฟล์ที่ส่ง'};
 const known=new Set((lines || []).map(l=>l.segment_id));
 for(const [i,s] of plan.stories.entries()){
  const ids=s?.segment_ids;
  if(!Array.isArray(ids) || !ids.length)return {error:`เรื่อง ${i+1} ไม่มีช่วงพูด (segment_ids)`};
  const unknown=known.size?ids.filter(id=>!known.has(id)).length:0;
  if(unknown)return {error:`เรื่อง ${i+1} อ้างช่วงพูดที่ไม่มีในงานนี้ ${unknown} ช่วง ใช้คำตอบของงานอื่นหรือเปล่า`};
 }
 // speech no story uses, as runs of neighbouring lines in each video: round 1 should cover the whole clip
 // a story trimmed in round 2 still covers the stretch round 1 gave it
 const used=new Set(plan.stories.flatMap(s=>[...s.segment_ids,...(Array.isArray(s.round1_ids)?s.round1_ids:[])])),unused=[];let run=null;
 for(const l of [...(lines || [])].sort((a,b)=>a.source_id===b.source_id?a.start_ms-b.start_ms:a.source_id<b.source_id?-1:1)){
  if(used.has(l.segment_id) || run?.source!==l.source_id)run=null;
  if(used.has(l.segment_id))continue;
  if(run){run.end_ms=l.end_ms;run.count++;}else unused.push(run={source:l.source_id,start_ms:l.start_ms,end_ms:l.end_ms,count:1});
 }
 // store the id and title the cards show, so export names files the same way
 const named=plan.stories.some((s,i)=>{const f=storyFields(s,i);return s.id!==f.id || s.title!==f.title;});
 for(const [i,s] of plan.stories.entries()){const f=storyFields(s,i);s.id=f.id;s.title=f.title;}
 return {plan,fixed:json!==raw || Array.isArray(value) || named,unused};
}
export function parsePlan(text){return checkPlan(text).plan || null;}

export function createStories({project,read,lines,write,go,editable,caption,emphasis=()=>{},subtitle=()=>{},editLine=()=>{},listen=()=>{}}){
 let queue=[];
 const video=$('editVideo');
 const reviews=()=>read().story_reviews || {};
 const length=list=>list.reduce((sum,l)=>sum+l.end_ms-l.start_ms,0);
 // what plays: the lines with their inner dead air cut out
 const played=list=>list.reduce((sum,l)=>sum+playLength(l),0);
 // a kept line's in/out points, moved by dragging its edges: at most 2 s past the line, at least 0.2 s long
 const REACH=2000,SHORTEST=200;
 function trimmed(line,trim){
  const n=v=>Number.isFinite(v)?Math.round(v/100)*100:null;
  const start=Math.max(0,line.start_ms-REACH,Math.min(n(trim?.start_ms) ?? line.start_ms,line.end_ms-SHORTEST));
  const end=Math.max(start+SHORTEST,Math.min(n(trim?.end_ms) ?? line.end_ms,line.end_ms+REACH));
  return {start_ms:start,end_ms:end};
 }
 // dead air, from each video's speech and speakers (voice.rs): stretches where the main speaker is not talking
 // (a laugh or someone off camera counts as dead air) and no transcribed word starts. It is cut at a line's
 // edges (unquiet) and inside it (inner), leaving PAD ms of it; an edge the person dragged stays where they put
 // it, and a cut clicked back stays in (air_keep). The export cuts the same (unquiet / inner_cuts, export.rs).
 // AIR_RULE goes up when voice.rs counts dead air differently, so stored stretches are counted again
 const PAD=100,AIR_MIN=500,AIR_RULE=2;
 const airMin=()=>Number.isFinite(read().dead_min_ms)?read().dead_min_ms:AIR_MIN;
 function quietOf(source){
  if(airMin()===0)return [];
  const spans=(read().quiet || {})[source],keep=(read().air_keep || {})[source] || [];
  return Array.isArray(spans)?spans.filter(([s])=>!keep.includes(s)):[];
 }
 function unquiet(line,span,trim){
  const spans=quietOf(line.source_id);
  let a=span.start_ms,b=span.end_ms;
  if(!Number.isFinite(trim?.start_ms))for(const [s,e] of spans)if(s<=a+PAD&&e>a)a=Math.max(a,e-PAD);
  if(!Number.isFinite(trim?.end_ms))for(const [s,e] of [...spans].reverse())if(s>a&&s<b&&e>=b-PAD)b=Math.min(b,s+PAD);
  return b-a>=SHORTEST?{start_ms:a,end_ms:b}:span;
 }
 // the dead air cut out of a kept line's middle, as [from, to] in the video's time
 function inner(line){
  const out=[];let at=line.start_ms;
  for(const [s,e] of quietOf(line.source_id)){const a=s+PAD,b=e-PAD;if(a>at&&b<line.end_ms&&b>=a+200){out.push([a,b]);at=b;}}
  return out;
 }
 // every quiet stretch inside a kept line, cut or clicked back, for the timeline marks
 function marks(line){
  const keep=(read().air_keep || {})[line.source_id] || [],all=airMin()===0?[]:(read().quiet || {})[line.source_id] || [];
  return all.filter(([s,e])=>s+PAD>line.start_ms&&e-PAD<line.end_ms&&e-s>=200+2*PAD).map(([s,e])=>({s,e,from:s+PAD,to:e-PAD,kept:keep.includes(s)}));
 }
 const playLength=l=>l.end_ms-l.start_ms-inner(l).reduce((n,[a,b])=>n+b-a,0);
 function toggleAir(source,start){
  if(!editable())return;
  write(d=>{const keep={...(d.air_keep || {})},list=new Set(keep[source] || []);if(list.has(start))list.delete(start);else list.add(start);keep[source]=[...list];d.air_keep=keep;});
 }
 // each video is listened to once (about a fifth of its length on CPU); a new shortest pause only recounts
 const listening=new Set();let airRetry=0,airNote='';
 const wordStarts=source=>lines().filter(l=>l.source_id===source).flatMap(l=>(l.words || []).map(w=>w.start_ms)).filter(Number.isFinite);
 function airStatus(text){airNote=text;const el=$('airStatus');if(el)el.textContent=text;}
 async function findQuiet(){
  if(!editable()){clearTimeout(airRetry);airRetry=setTimeout(findQuiet,1000);return;}
  const voice=read().voice || {},files=read().files.filter(f=>f.source_id&&lines().some(l=>l.source_id===f.source_id));
  const todo=files.filter(f=>!voice[f.source_id]&&!listening.has(f.source_id));
  const keyOf=f=>`${airMin()}|${AIR_RULE}|${keepOf(f.source_id).join(',')}`;
  const stale=files.filter(f=>voice[f.source_id]&&(read().quiet_key || {})[f.source_id]!==keyOf(f)&&airMin()>0&&!listening.has(f.source_id));
  for(const f of stale){listening.add(f.source_id);
   try{const v=voice[f.source_id];const r=await fetch('/api/v1/voice/dead-air',{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({speech:v.speech,turns:v.turns,words:wordStarts(f.source_id),total_ms:v.total_ms,min_ms:airMin(),keep:keepOf(f.source_id)}),signal:AbortSignal.timeout(30000)});
    const a=await r.json();if(!r.ok || !Array.isArray(a.dead))throw Error(a.error || 'dead_air_failed');
    write(d=>{d.quiet={...(d.quiet || {}),[f.source_id]:a.dead};d.quiet_key={...(d.quiet_key || {}),[f.source_id]:keyOf(f)};});}
   catch(_){airStatus('นับช่วงเงียบใหม่ไม่สำเร็จ ลองเปลี่ยนค่าอีกครั้ง');}
   finally{listening.delete(f.source_id);}
  }
  if(!todo.length)return;
  const status=await fetch('/api/v1/voice',{cache:'no-store'}).then(r=>r.json()).catch(()=>null);
  if(!status?.ready){airStatus(status?`ตัดช่วงเงียบต้องใช้ตัวแยกเสียง (${Math.round((status.download_bytes || 0)/1048576)} MB ครั้งเดียว)`:'อ่านสถานะตัวแยกเสียงไม่ได้');const b=$('airDownload');if(b)b.hidden=!status;return;}
  for(const f of todo){listening.add(f.source_id);airStatus('กำลังฟังเสียงหาช่วงเงียบ…');
   try{const r=await fetch(`/api/v1/projects/${encodeURIComponent(project)}/sources/${encodeURIComponent(f.source_id)}/voice`,{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({words:wordStarts(f.source_id),min_ms:airMin() || AIR_MIN}),signal:AbortSignal.timeout(900000)});
    const a=await r.json();if(!r.ok || !Array.isArray(a.dead))throw Error(a.error || 'voice_failed');
    write(d=>{d.voice={...(d.voice || {}),[f.source_id]:{speech:a.speech,turns:a.turns,total_ms:a.total_ms,main:a.main}};
     d.quiet={...(d.quiet || {}),[f.source_id]:a.dead};d.quiet_key={...(d.quiet_key || {}),[f.source_id]:airMin()?keyOf(f):''};});
    airStatus(`เจอช่วงเงียบ ${a.dead.length} ช่วง`);}
   catch(e){airStatus('ฟังเสียงไม่สำเร็จ ('+(e.message || 'ไม่ทราบสาเหตุ')+') ท่อนยังไม่ถูกตัด');}
   finally{listening.delete(f.source_id);}
  }
 }
 if($('airDownload'))$('airDownload').onclick=async()=>{
  const b=$('airDownload');b.disabled=true;airStatus('กำลังดาวน์โหลดตัวแยกเสียง…');
  try{const r=await fetch('/api/v1/voice/download',{method:'POST',signal:AbortSignal.timeout(900000)});const a=await r.json();if(!r.ok || !a.ready)throw Error(a.error || 'download_failed');b.hidden=true;airStatus('');findQuiet();}
  catch(e){airStatus('ดาวน์โหลดไม่สำเร็จ ลองอีกครั้ง');}finally{b.disabled=false;}
 };
 if($('airMin'))$('airMin').onchange=()=>{if(!editable())return;const v=Number($('airMin').value);write(d=>{d.dead_min_ms=v;});findQuiet();};
 function model(){
  const plan=parsePlan(read().plan);if(!plan)return null;
  const all=lines(),bySegment=new Map(all.map(l=>[l.segment_id,l]));
  const stories=plan.stories.map((s,index)=>{
   const trims=s?.trims && typeof s.trims==='object' ? s.trims : {};
   const kept=(Array.isArray(s?.segment_ids)?s.segment_ids:[]).map(id=>bySegment.get(id)).filter(Boolean)
    .map(l=>({...l,from_ms:l.start_ms,to_ms:l.end_ms,...unquiet(l,trimmed(l,trims[l.segment_id]),trims[l.segment_id])}));
   const counts=new Map();for(const l of kept)counts.set(l.source_id,(counts.get(l.source_id) || 0)+1);
   const {id,title,summary}=storyFields(s,index);
   return {index,id,title,summary,refined:Array.isArray(s?.round1_ids),
    kept,source_id:[...counts].sort((a,b)=>b[1]-a[1])[0]?.[0] || ''};
  });
  return {all,stories};
 }
 // the story's lines as runs of consecutive kept lines in the video's own order: each run is one piece of the clip
 function pieces(story){
  const keep=new Set(story.kept.map(l=>l.segment_id));let count=0,inside=false;
  for(const l of lines().filter(l=>l.source_id===story.source_id)){const k=keep.has(l.segment_id);if(k&&!inside)count++;inside=k;}
  return count;
 }
 function renderCards(){
  const host=$('storyCards'),m=model();host.hidden=!m;$('timeline').hidden=!!m;
  if(!m){host.replaceChildren();return;}
  const files=read().files.filter(f=>f.source_id),groups=files.map(file=>({file,stories:m.stories.filter(s=>s.source_id===file.source_id)})).filter(g=>g.stories.length);
  const loose=m.stories.filter(s=>!files.some(f=>f.source_id===s.source_id));
  const head=document.createElement('p');head.className='caption';head.hidden=true;
  head.textContent=`ได้ ${m.stories.length} เรื่อง จาก ${groups.length} ไฟล์ · ตรวจแล้ว ${m.stories.filter(s=>reviews()[s.id]).length} เรื่อง · กดเรื่องเพื่อตัดต่อ`;
  host.replaceChildren(head,...groups.map(g=>group(g.file.name,g.stories)),...(loose.length?[group('ไม่พบช่วงพูดของเรื่องนี้',loose)]:[]));
 }
 function group(name,stories){
  const box=document.createElement('section');box.className='story-group';
  const title=document.createElement('h3'),seen=stories.filter(s=>reviews()[s.id]).length;
  title.textContent=`${name} · ${stories.length} เรื่อง`+(seen?` · ตรวจแล้ว ${seen}`:'')+' · กดเรื่องเพื่อตัดต่อ';
  const grid=document.createElement('div');grid.className='story-grid';grid.append(...stories.map(card));
  box.append(title,grid);return box;
 }
 function card(story){
  const row=document.createElement('div');row.className='story-row';row.dataset.refined=story.refined;
  const button=document.createElement('button');button.type='button';button.className='story-card';button.disabled=!story.kept.length;
  const done=!!reviews()[story.id],status=document.createElement('span');status.className='badge story-status';status.dataset.done=done;status.textContent='ตรวจแล้ว';status.hidden=!done;
  const title=document.createElement('strong');title.textContent=`${story.index+1}. ${story.title}`;
  const summary=document.createElement('span');summary.className='story-summary';summary.textContent=story.summary || story.kept.map(l=>l.text).join(' ').slice(0,100);
  const meta=document.createElement('span');meta.className='caption story-meta';meta.textContent=`${clock(played(story.kept))} · ${pieces(story)} ท่อน`;
  const marks=document.createElement('span');marks.className='story-marks';marks.append(status);
  if(story.refined){const done=document.createElement('span');done.className='badge story-refined';done.textContent='เรียบเรียงแล้ว';marks.append(done);}
  button.title=`${story.title}\n${summary.textContent}`;button.append(marks,title,summary,meta);
  button.onclick=()=>{write(d=>{d.edit_story=story.id;});go(3);};
  const refine=document.createElement('button');refine.type='button';refine.dataset.icon='cut';refine.textContent=story.refined?'เรียบเรียงใหม่':'ตัดต่อเรียบเรียง';refine.title=story.refined?'คัดลอกเรื่องนี้ให้ AI เรียบเรียงอีกครั้ง':'คัดลอกเรื่องนี้ให้ AI เลือกและเรียงท่อน';
  refine.disabled=!story.kept.length || !editable();refine.onclick=()=>openRefine(story);
  row.append(button,refine);return row;
 }
 // round 2: one story's lines go to an AI, which keeps and orders the ones worth using; its answer changes only that story
 let refining=null;
 function offered(story){
  const entry=parsePlan(read().plan)?.stories[story.index],ids=new Set(entry?.round1_ids || entry?.segment_ids || []);
  const own=lines().filter(l=>l.source_id===story.source_id),hit=own.filter(l=>ids.has(l.segment_id));
  if(!hit.length)return [];
  const from=Math.min(...hit.map(l=>l.start_ms)),to=Math.max(...hit.map(l=>l.end_ms));
  return own.filter(l=>l.start_ms>=from&&l.end_ms<=to);
 }
 function refineText(story){
  const packet={schema_version:1,step:'refine_story',project_id:project,story_id:story.id,title:story.title,summary:story.summary,
   lines:offered(story).map(l=>({id:l.segment_id,start_ms:l.start_ms,end_ms:l.end_ms,text:l.text})),
   response_schema:{story_id:story.id,title:'หัวข้อเรื่อง',summary:'สรุปเนื้อหา 1-2 ประโยค',segment_ids:[]}};
  return 'เรียบเรียงเรื่องนี้ให้เป็นคลิปที่ดูแล้วรู้เรื่องในตัวเอง เลือกเฉพาะท่อนที่ดีที่สุด ตัดท่อนที่พูดซ้ำ พูดติดขัด หรือออกนอกเรื่อง '
   +'เรียงลำดับใหม่ได้ถ้าเล่าแล้วดีขึ้น เปิดเรื่องให้น่าสนใจและรักษาตอนจบให้ครบ อ้างท่อนด้วย id ใน lines เท่านั้น ห้ามสร้างท่อนหรือเวลาใหม่ '
   +'segment_ids คือท่อนที่เก็บ เรียงตามลำดับที่จะเล่น ปรับ title และ summary ให้ตรงกับเรื่องที่เรียบเรียงแล้ว\n'
   +'ตอบกลับเป็น JSON ตาม response_schema อย่างเดียว ไม่ต้องมีคำอธิบาย\n\n'+JSON.stringify(packet,null,1);
 }
 const refineStory=()=>model()?.stories.find(s=>s.id===refining) || null;
 async function copyRefine(){
  const story=refineStory();if(!story)return;
  try{await navigator.clipboard.writeText(refineText(story));$('refineCopyNote').textContent=`คัดลอก ${offered(story).length} ท่อนแล้ว วางในแชท AI ได้เลย`;}
  catch(_){$('refineCopyNote').textContent='คัดลอกไม่ได้ กด "คัดลอกอีกครั้ง"';}
 }
 function openRefine(story){
  refining=story.id;$('refineTitle').textContent=`ตัดต่อเรียบเรียง · ${story.index+1}. ${story.title}`;
  $('refineAnswer').value='';checkRefineUi();$('refineDialog').showModal();copyRefine();
 }
 function checkRefine(){
  const story=refineStory(),raw=$('refineAnswer').value.trim();if(!story || !raw)return {error:''};
  const {value,json}=readJson(raw);if(json===undefined)return {error:'คำตอบไม่ครบ ลองคัดลอกใหม่ทั้งหมด'};
  if(value?.response_schema)return {error:'นี่คือข้อความที่ส่งให้ AI ยังไม่ใช่คำตอบ วางในแชท AI ก่อน แล้วคัดลอกคำตอบของ AI กลับมา'};
  const answer=Array.isArray(value)?{segment_ids:value}:Array.isArray(value?.stories)?value.stories[0]:value;
  if(answer?.story_id&&String(answer.story_id)!==story.id)return {error:'คำตอบนี้เป็นของเรื่องอื่น'};
  const ids=answer?.segment_ids,allowed=new Set(offered(story).map(l=>l.segment_id));
  if(!Array.isArray(ids) || !ids.length)return {error:'ไม่พบท่อนที่เก็บ (segment_ids) ในคำตอบ'};
  if(ids.some(id=>!allowed.has(id)))return {error:'คำตอบอ้างท่อนที่ไม่ได้ส่งไป ลองให้ AI ตอบใหม่'};
  if(new Set(ids).size!==ids.length)return {error:'คำตอบใช้ท่อนเดียวกันซ้ำ'};
  const byId=new Map(lines().map(l=>[l.segment_id,l]));
  return {ids,title:String(answer.title || '').trim(),summary:String(answer.summary || '').trim(),
   note:`เก็บ ${ids.length} จาก ${allowed.size} ท่อน · ยาว ${clock(length(ids.map(id=>byId.get(id))))}`};
 }
 function checkRefineUi(){
  const c=checkRefine();$('refineStatus').textContent=c.ids?c.note:c.error;$('refineStatus').dataset.state=c.ids?'ok':'error';
  $('refineStatus').hidden=!$('refineStatus').textContent;$('refineApply').disabled=!c.ids || !editable();
 }
 $('refineAnswer').oninput=checkRefineUi;
 $('refineCopy').onclick=copyRefine;
 $('refineApply').onclick=event=>{
  const story=refineStory(),c=checkRefine();if(!story || !c.ids){event.preventDefault();return;}
  write(d=>{
   const plan=parsePlan(d.plan),entry=plan?.stories[story.index];if(!entry)return;
   entry.round1_ids=entry.round1_ids || entry.segment_ids.slice();entry.segment_ids=c.ids;
   if(c.title)entry.title=c.title;if(c.summary)entry.summary=c.summary;d.plan=JSON.stringify(plan,null,2);
   if(d.story_reviews?.[story.id]){d.story_reviews={...d.story_reviews};delete d.story_reviews[story.id];}
  });
 };
 function current(){const m=model();if(!m)return null;return m.stories.find(s=>s.id===read().edit_story) || m.stories[0] || null;}
 function renderEditor(){
  const story=current();$('editEmpty').hidden=!!story;$('editor').hidden=!story;
  const pick=$('editStory');pick.hidden=!story;
  if(!story)return;
  // only stories already arranged with the AI (round 2) are offered; the one open now always is
  pick.replaceChildren(...model().stories.map(s=>{const o=document.createElement('option');o.value=s.id;o.selected=s.id===story.id;
   o.disabled=!s.refined&&s.id!==story.id;o.textContent=`${s.index+1}. ${s.title}`+(s.refined?'':' (ยังไม่เรียบเรียง)');return o;}));
  const done=!!reviews()[story.id];$('editReviewed').textContent='ตรวจเรื่องนี้แล้ว';if(done)$('editReviewed').dataset.icon='check';else delete $('editReviewed').dataset.icon;$('editReviewed').setAttribute('aria-pressed',String(done));$('editReviewed').title=done?'ตรวจแล้ว · กดอีกครั้งเพื่อยกเลิก':'';$('editReviewed').classList.toggle('primary',!done);$('editReviewed').disabled=!editable();
  // step 3 cuts the video in its own frame; step 4 plays it under the subtitle frame with the emphasis track
  const textMode=!$('captionDock').closest('[data-panel]').hidden,frame=$(textMode?'textFrame':'editFrame');
  if(frame&&video.parentElement!==frame)frame.prepend(video);
  const controls=$('editPlay').closest('.editor-controls'),dock=textMode?$('captionTransport'):$('editor');
  if(controls.parentElement!==dock){dock.append(controls,$('editTrack'));}
  if(textMode)controls.querySelector('.editor-zoom').before(document.querySelector('.frame-aspect'),document.querySelector('.caption-ai'),document.querySelector('.sub-timing'));
  const title=$(textMode?'captionsTitle':'editTitle');if(pick.parentElement!==title)title.append(pick);
  cropSource=story.source_id;showCrop();
  const src=`/api/v1/projects/${encodeURIComponent(project)}/sources/${encodeURIComponent(story.source_id)}/preview`;
  if(video.dataset.src!==src){video.dataset.src=src;$('editVideoNote').textContent='กำลังเตรียมพรีวิว 360p ครั้งแรกอาจรอสักครู่';video.src=src;video.load();}
  // the clip as it plays: kept lines end to end in story order; the switch adds the cut lines back at their own time
  const zoom=Number($('editZoom').value),showCut=$('editShowCut').checked,px=ms=>ms/1000*zoom;
  const order=lines().filter(l=>l.source_id===story.source_id),keep=new Map(story.kept.map(l=>[l.segment_id,l]));
  const boxes=showCut ? order.map(l=>({line:l,kept:keep.get(l.segment_id),at:(keep.get(l.segment_id) || l).start_ms}))
   : story.kept.reduce((list,k)=>{const at=list.length?list.at(-1).at+list.at(-1).kept.end_ms-list.at(-1).kept.start_ms:0;list.push({line:k,kept:k,at});return list;},[]);
  const end=showCut ? Math.max(0,...order.map(l=>l.end_ms),...story.kept.map(k=>k.end_ms)) : length(story.kept);
  const lane=document.createElement('div');lane.className='edit-lane';lane.style.width=Math.ceil(px(end)+40)+'px';
  const every=zoom>=30?5:zoom>=10?15:zoom>=4?30:60;
  for(let s=0;s<=end/1000;s+=every){const mark=document.createElement('span');mark.className='edit-mark caption';mark.style.left=px(s*1000)+'px';mark.textContent=clock(s*1000);lane.append(mark);}
  for(const {line,kept,at} of boxes){
   const span=kept || line,box=document.createElement('div');box.className='edit-seg';box.dataset.keep=!!kept;
   box.style.left=px(at)+'px';box.style.width=Math.max(6,px(span.end_ms-span.start_ms)-2)+'px';box.title=line.text;
   const text=document.createElement('button');text.type='button';text.className='edit-text';
   text.textContent=textMode&&(line.words || []).length?speechText(line,null) || '·':line.text;
   text.onclick=()=>{if(scrubbed)return;if(textMode){if(kept)seekTo(story,kept.start_ms,kept.segment_id,false);wordLine=lines().find(l=>l.segment_id===line.segment_id) || line;drawWords();showLine();return;}if(showCut)playRaw(line.start_ms,true);else seekTo(story,kept.start_ms,kept.segment_id,true);};
   const full=lines().find(l=>l.segment_id===line.segment_id) || line,verdict=textMode&&qcBy.get(full.id);if(verdict){box.dataset.qc=verdict.worst;box.title=line.text+'\n'+verdict.problems.join('\n');}
   const toggle=document.createElement('button');toggle.type='button';toggle.className='edit-toggle';toggle.dataset.icon=kept?'x':'plus';
   toggle.title=kept?'ตัดท่อนนี้ทิ้ง':'เก็บท่อนนี้';toggle.setAttribute('aria-label',toggle.title);toggle.disabled=!editable()||(kept&&story.kept.length===1);
   toggle.onclick=()=>flip(story,line);
   box.dataset.id=line.segment_id;
   if(textMode)box.append(text);else box.append(toggle,text);
   if(kept&&editable()&&!textMode)box.append(handle(story,kept,'start_ms',box,zoom),handle(story,kept,'end_ms',box,zoom));
   if(kept&&!showCut&&editable()&&!textMode){
    box.draggable=true;box.dataset.id=kept.segment_id;
    box.ondragstart=e=>{if(e.target.closest('.edit-grip')){e.preventDefault();return;}e.dataTransfer.setData('text/plain',kept.segment_id);e.dataTransfer.effectAllowed='move';box.classList.add('dragging');};
    box.ondragend=()=>box.classList.remove('dragging');
    box.ondragover=e=>{e.preventDefault();const r=box.getBoundingClientRect();box.dataset.drop=e.clientX<r.left+r.width/2?'before':'after';};
    box.ondragleave=()=>{delete box.dataset.drop;};
    box.ondrop=e=>{e.preventDefault();const side=box.dataset.drop;delete box.dataset.drop;move(story,e.dataTransfer.getData('text/plain'),kept.segment_id,side);};
   }
   // dead air inside the line: hatched where it is cut, dashed where it was clicked back in
   if(kept)for(const m of marks(kept)){const cut=document.createElement('button');cut.type='button';cut.className='edit-air';cut.dataset.kept=String(m.kept);
    cut.style.left=px(m.from-span.start_ms)+'px';cut.style.width=Math.max(3,px(m.to-m.from))+'px';
    cut.title=(m.kept?'ช่วงเงียบ ':'ตัดช่วงเงียบ ')+((m.e-m.s)/1000).toFixed(1)+' วิ · '+(m.kept?'กดเพื่อตัด':'กดเพื่อเก็บไว้');cut.setAttribute('aria-label',cut.title);cut.disabled=!editable();
    cut.onclick=e=>{e.stopPropagation();toggleAir(kept.source_id,m.s);};box.append(cut);}
   lane.append(box);
  }
  const head=document.createElement('div');head.className='edit-head';head.id='editHead';lane.append(head);
  const track=$('editTrack'),scroll=track.scrollLeft;track.replaceChildren(lane);track.scrollLeft=scroll;
  editView={story,showCut,boxes,zoom};
  wordLine=wordLine?lines().find(l=>l.segment_id===wordLine.segment_id) || null:null;drawWords();if(textMode)drawHeads();
  const seekAt=x=>{
   const at=Math.max(0,(x-lane.getBoundingClientRect().left)/zoom*1000);
   if(showCut){playRaw(at,false);return;}
   const box=boxes.find(b=>at<b.at+b.kept.end_ms-b.kept.start_ms) || boxes.at(-1);
   if(box)seekTo(story,box.kept.start_ms+Math.min(Math.max(0,at-box.at),box.kept.end_ms-box.kept.start_ms-1),box.kept.segment_id,false);
  };
  lane.onclick=e=>{if(scrubbed)return;if(e.target!==lane&&!e.target.classList.contains('edit-mark'))return;seekAt(e.clientX);};
  lane.onpointerdown=e=>{
   if(e.button!==0 || e.target.closest('.edit-grip,.edit-toggle,.edit-emph') || (!textMode&&e.target.closest('.edit-seg[draggable=true]')))return;
   const from=e.clientX;let moving=false;scrubbed=false;
   const move=m=>{if(!moving&&Math.abs(m.clientX-from)<4)return;moving=true;scrubbed=true;seekAt(m.clientX);};
   const up=()=>{removeEventListener('pointermove',move);removeEventListener('pointerup',up);setTimeout(()=>{scrubbed=false;},0);};
   addEventListener('pointermove',move);addEventListener('pointerup',up);
  };
  drawPeople();
  $('editClock').textContent=`ใช้ ${story.kept.length} บรรทัด · ${pieces(story)} ท่อน · ยาว ${clock(played(story.kept))}`;
  if($('airMin'))$('airMin').value=String(airMin());if($('airStatus')&&airNote)$('airStatus').textContent=airNote;
 }
 let editView=null,refocus=null,cropSource=null,wordLine=null,scrubbed=false,qcBy=new Map();
 // the line picked on the step 4 timeline in the panel's text box, with the check's notes for it
 function showLine(){
  const box=$('lineBox');if(!box)return;
  const line=wordLine,inStep4=!!$('editTrack').closest('#captionTransport');box.hidden=!inStep4 || !line;if(box.hidden)return;
  const verdict=qcBy.get(line.id);
  $('lineText').value=line.text;$('lineText').disabled=!editable();$('lineSave').disabled=true;
  // the words of this line that show as emphasis, so the full text in the box does not read as a repeat
  const shown=(read().text_roles || []).includes('emphasis')?picks(line):[];
  $('lineEmph').hidden=!shown.length;$('lineEmph').textContent=shown.length?`⭐ คำเน้น: ${joinPicks(shown)} · บนจอขึ้นตัวใหญ่แยก ไม่ซ้ำในซับ`:'';
  $('lineTime').textContent=`${clock(line.start_ms)}–${clock(line.end_ms)}`;
  $('lineProblems').replaceChildren(...(verdict?.problems || []).map(p=>{const li=document.createElement('li');li.textContent=p;li.dataset.severity=verdict.worst;return li;}));
 }
 $('lineText')?.addEventListener('input',()=>{$('lineSave').disabled=!wordLine || $('lineText').value.trim()===wordLine.text || !$('lineText').value.trim();});
 $('lineSave')?.addEventListener('click',()=>{if(!wordLine)return;const text=$('lineText').value.trim();if(!text)return;try{editLine(wordLine,text);$('lineSave').disabled=true;}catch(_){}});
 $('lineListen')?.addEventListener('click',()=>{if(wordLine)listen(wordLine);});
 // the words row: the line under the playhead (or the one clicked); each word toggles as emphasis
 function showWords(line){
  const same=(wordLine?.segment_id || null)===(line?.segment_id || null);wordLine=line || null;if(!same)drawWords();
 }
 // step 4 layers: a video bar and a speech clip per kept line, and emphasis clips where picked words are said;
 // the panel lists the words of the line picked on the speech layer
 function drawWords(){
  const lane=$('editTrack').querySelector('.edit-lane');if(!lane || !editView)return;
  lane.querySelectorAll('.edit-emph,.edit-vid,.edit-sub').forEach(n=>n.remove());
  const textMode=lane.closest('#captionTransport'),emphasis=read().emphasis || {},z=editView.zoom,px=ms=>ms/1000*z;
  if(textMode){
   const roles=read().text_roles || [],subs=read().subtitles || {};lane.classList.toggle('edit-layer-off',!roles.includes('emphasis'));lane.classList.toggle('edit-layer-off-sub',!roles.includes('english'));lane.classList.toggle('edit-layer-off-speech',!roles.includes('speech'));
   for(const {line,kept,at} of editView.boxes){
    const span=kept || line;
    const bar=document.createElement('div');bar.className='edit-vid';{const who=speakerAt(line.source_id,span.start_ms),n=people(line.source_id).findIndex(p=>p.id===who);if(n>=0)bar.dataset.speaker=String(n%4);}bar.style.left=px(at)+'px';bar.style.width=Math.max(4,px(span.end_ms-span.start_ms)-2)+'px';lane.append(bar);
    const words=picks(line).filter(w=>w.start_ms<span.end_ms&&(w.end_ms ?? w.start_ms+1)>span.start_ms);
    if(words.length){
     const first=Math.max(words[0].start_ms,span.start_ms),clip=document.createElement('button');clip.type='button';clip.className='edit-emph';
     clip.textContent=joinPicks(words);clip.title='คำเน้น · คลิกเพื่อแก้คำ';
     clip.style.left=px(at+first-span.start_ms)+'px';clip.style.width=Math.max(24,px(Math.max(span.end_ms-first,HOLD))-2)+'px';
     clip.onclick=e=>{e.stopPropagation();wordLine=line;drawWords();};lane.append(clip);
    }
    const said=subs[line.segment_id];
    if(said){const sub=document.createElement('div');sub.className='edit-sub';sub.textContent=said;sub.title=said;sub.lang=read().subtitle_language || 'en';
     sub.style.left=px(at)+'px';sub.style.width=Math.max(24,px(span.end_ms-span.start_ms)-2)+'px';lane.append(sub);}
   }
   drawLayerActions();
   for(const box of lane.querySelectorAll('.edit-seg'))box.dataset.lineSelected=String(box.dataset.id===wordLine?.segment_id || box.title===wordLine?.text);
  }
  const host=$('lineWords');if(!host)return;
  host.hidden=!textMode || !wordLine;if(host.hidden){host.replaceChildren();return;}
  const chosen=picks(wordLine),label=document.createElement('span');label.className='caption';
  label.textContent=wordLine.words?.length?'คำในท่อนนี้ · คลิกคำเพื่อเน้น':'ท่อนนี้แก้ข้อความแล้ว ไม่มีเวลารายคำให้เลือกเน้น';
  host.replaceChildren(label,...subWords(wordLine).map(w=>{const b=document.createElement('button');b.type='button';b.className='line-word';b.textContent=w.text;
   b.setAttribute('aria-pressed',String(chosen.some(p=>samePick(p,w))));b.disabled=!editable();b.onclick=()=>toggleEmphasis(wordLine,w);return b;}));
 }
 // track heads for the step 4 layers; the ticks are the "on screen" choices
 function drawHeads(){
  const heads=$('layerHeads');if(!heads)return;
  const roles=read().text_roles || [],rows=[['วิดีโอ',null,22,16],['คำพูด','speech',38,26],['คำเน้น','emphasis',64,54],['ซับไตเติ้ล','english',118,54]];
  heads.replaceChildren(...rows.map(([label,role,top,height])=>{
   const row=document.createElement('div');row.className='layer-head';row.style.top=top+'px';row.style.height=height+'px';
   if(role){
    const tick=document.createElement('input');tick.type='checkbox';tick.checked=roles.includes(role);tick.disabled=!editable();tick.title='แสดงบนจอ';tick.setAttribute('aria-label',`แสดง${label}บนจอ`);
    tick.onchange=()=>{const box=document.querySelector(`[data-role-option="${role}"]`);if(box){box.checked=tick.checked;box.dispatchEvent(new Event('change'));}};
    const name=document.createElement('button');name.type='button';name.textContent=label;name.title=`ปรับ${label}`;
    name.onclick=()=>document.querySelector(`[data-role-pick="${role}"]`)?.click();
    row.append(tick,name);
   }else row.textContent=label;
   return row;
  }));
  heads.style.height=$('editTrack').offsetHeight ? $('editTrack').offsetHeight+'px' : '';
 }
 // the line's characters, each timed by its share of its timed word. Picks are stored as
 // {text,start_ms,end_ms}; a bare number (older drafts) is the whole timed word starting there.
 const segmenter=typeof Intl!=='undefined'&&Intl.Segmenter?new Intl.Segmenter('th',{granularity:'word'}):null;
 const latin=/[A-Za-z0-9]/;
 function timedText(line){
  const words=(line?.words || []).map(w=>({text:w.text.trim(),start_ms:w.start_ms,end_ms:w.end_ms})).filter(w=>w.text);
  let text='';const from=[],to=[],said=[];
  for(const w of words){
   if(latin.test(text.at(-1) || '')&&latin.test(w.text[0])){text+=' ';from.push(w.start_ms);to.push(w.start_ms);said.push(w.start_ms);}
   const n=w.text.length,span=w.end_ms-w.start_ms;
   for(let i=0;i<n;i++){from.push(w.start_ms+Math.round(span*i/n));to.push(w.start_ms+Math.round(span*(i+1)/n));said.push(w.start_ms);}
   text+=w.text;
  }
  return {words,text,from,to,said};
 }
 // two picks a few letters apart are one name ("เจน" + "เทิล" + "มอนสเตอร์"): join them with the letters
 // between, so the speech line is not left with a stray piece of the name
 function bridge(line,list){
  const {text,from,to}=timedText(line),at=p=>{let best=-1,gap=Infinity;for(let i=text.indexOf(p.text);i>=0;i=text.indexOf(p.text,i+1)){const d=Math.abs(from[i]-p.start_ms);if(d<gap){gap=d;best=i;}}return best;};
  const out=[];
  for(const p of list){
   const prev=out.at(-1),a=prev?at(prev):-1,b=at(p);
   const between=a>=0&&b>=0?text.slice(a+prev.text.length,b):null;
   if(between!==null&&between.length>0&&between.length<=4&&!/\s/.test(between)){const s=text.slice(a,b+p.text.length);out[out.length-1]={text:s,start_ms:from[a],end_ms:to[b+p.text.length-1]};}
   else out.push(p);
  }
  return out;
 }
 // the speech line as shown: words said by `now`, without the ones on screen as emphasis (option 1, P'Ohm
 // 21 Oct: the same word twice reads as a stutter); each pick is cut where it is said closest to its time
 function speechText(line,now=Infinity,reveal=true){
  const {text,from,said}=timedText(line);if(!text)return line?.text || '';
  const keep=[...text].map(()=>true),list=(read().text_roles || []).includes('emphasis')?picks(line):[];
  // matched on letters only, so "Customer Experience" is found though the line holds a space between them
  const letters=[...text].map((c,i)=>({c,i})).filter(x=>!/\s/.test(x.c)),flat=letters.map(x=>x.c).join('');
  for(const p of list){
   const want=p.text.replace(/\s+/g,'');let best=-1,gap=Infinity;
   for(let i=flat.indexOf(want);i>=0&&want;i=flat.indexOf(want,i+1)){const d=Math.abs(from[letters[i].i]-p.start_ms);if(d<gap){gap=d;best=i;}}
   if(best>=0)for(let k=letters[best].i;k<=letters[best+want.length-1].i;k++)keep[k]=false;
  }
  // the kept letters of each timed word, then display lines that fit the frame, broken like the export
  // (lines() in export.rs): a line holds words while their letters fit `limit`
  const words=[];[...text].forEach((c,i)=>{if(!keep[i])return;const last=words.at(-1);if(last&&last.at===said[i])last.text+=c;else words.push({at:said[i],text:c});});
  const size=Number(read().text_styles?.speech?.size) || 64,limit=Math.max(4,Math.floor(0.86*1080/(size*0.55)));
  const units=t=>[...t].filter(c=>!/[\u0E31\u0E34-\u0E3A\u0E47-\u0E4E]/.test(c)).length;
  const rows=[];for(const w of words.map(w=>({...w,text:w.text.trim()})).filter(w=>w.text)){const row=rows.at(-1);
   if(row&&units(row.map(x=>x.text).join(''))+1+units(w.text)<=limit)row.push(w);else rows.push([w]);}
  if(!rows.length)return '';
  if(now===null)return rows.flat().map(w=>w.text).reduce((out,t)=>out+(out&&/[A-Za-z0-9]$/.test(out)&&/^[A-Za-z0-9]/.test(t)?' ':'')+t,'');
  const row=[...rows].reverse().find((r,n)=>n===rows.length-1 || r[0].at<=now) || rows[0];
  return row.filter(w=>!reveal || w.at<=now).map(w=>w.text).reduce((out,t)=>out+(out&&/[A-Za-z0-9]$/.test(out)&&/^[A-Za-z0-9]/.test(t)?' ':'')+t,'');
 }
 // emphasis on screen: the group being said (neighbouring picks read as one), until the next group starts
 function pickGroups(line){const groups=[];for(const p of picks(line)){const g=groups.at(-1),prev=g?.at(-1);if(prev&&p.start_ms<=prev.end_ms+1)g.push(p);else groups.push([p]);}return groups;}
 function emphasisNow(line,now){
  const current=[...pickGroups(line)].reverse().find(g=>g[0].start_ms<=now);
  return current?joinPicks(current):'';
 }
 // a word picked at the end of its line stays up at least HOLD ms, into the next line, until another pick
 // shows (the export does the same: EMPHASIS_HOLD_MS in export.rs)
 const HOLD=1200;
 // letters in a line short enough to join the line next to it on screen
 const SHORT=4;
 function emphasisAt(story,said,now){
  const own=said&&emphasisNow(said,now);if(own)return own;
  let best=null;
  for(const line of story.kept)for(const g of pickGroups(line)){const at=g[0].start_ms;if(at<=now&&now<at+HOLD&&(!best||at>best.at))best={at,text:joinPicks(g)};}
  return best?.text || '';
 }
 // chips: each timed word, split only when the dictionary cuts it cleanly ("ยกตัวอย่าง" -> "ยก|ตัวอย่าง");
 // splitting the whole line cut loan words apart ("เจน|เทิ|ลม|อนส|เต|อร์")
 function subWords(line){
  const {words}=timedText(line);
  if(!segmenter)return words;
  return words.flatMap(w=>{
   const parts=[...segmenter.segment(w.text)].filter(p=>p.isWordLike);
   if(parts.length<2 || parts.some(p=>p.segment.length<2) || parts.map(p=>p.segment).join('')!==w.text)return [w];
   const n=w.text.length,span=w.end_ms-w.start_ms;
   return parts.map(p=>({text:p.segment,start_ms:w.start_ms+Math.round(span*p.index/n),end_ms:w.start_ms+Math.round(span*(p.index+p.segment.length)/n)}));
  });
 }
 // an AI's word found in the line as written (the dictionary split can cut loan words like "มอนสเตอร์"),
 // timed from its characters
 function findSaid(line,want){
  const {text,from,to}=timedText(line),key=want.replace(/\s+/g,'').toLowerCase();if(!key)return null;
  const chars=[...text].map((c,i)=>({c:c.toLowerCase(),i})).filter(x=>!/\s/.test(x.c)),flat=chars.map(x=>x.c).join('');
  const at=flat.indexOf(key);if(at<0)return null;
  const a=chars[at].i,b=chars[at+key.length-1].i;
  return {text:text.slice(a,b+1),start_ms:from[a],end_ms:to[b]};
 }
 // picked words read as one phrase: neighbours join without a space unless both sides are Latin
 const joinPicks=list=>list.reduce((out,w,i)=>{const prev=list[i-1];const tight=prev&&w.start_ms<=prev.end_ms+1&&!(latin.test(prev.text.at(-1))&&latin.test(w.text[0]));return out+(i&&!tight?' ':'')+w.text;},'');
 function relocate(line,p){
  const {text,from,to}=timedText(line);const letters=[...text].map((c,i)=>({c,i})).filter(x=>!/\s/.test(x.c)),flat=letters.map(x=>x.c).join(''),want=p.text.replace(/\s+/g,'');
  let best=-1,gap=Infinity;for(let i=flat.indexOf(want);i>=0&&want;i=flat.indexOf(want,i+1)){const d=Math.abs(from[letters[i].i]-p.start_ms);if(d<gap){gap=d;best=i;}}
  return best<0?p:{...p,start_ms:from[letters[best].i],end_ms:to[letters[best+want.length-1].i]};
 }
 function picks(line){return rawPicks(line).map(p=>relocate(line,p)).sort((a,b)=>a.start_ms-b.start_ms);}
 function rawPicks(line){
  return (read().emphasis?.[line?.segment_id] || []).map(p=>typeof p==='number'?(()=>{const w=(line.words || []).find(w=>w.start_ms===p);return w&&{text:w.text.trim(),start_ms:w.start_ms,end_ms:w.end_ms};})():p)
   .filter(p=>p&&typeof p.text==='string'&&Number.isFinite(p.start_ms)).sort((a,b)=>a.start_ms-b.start_ms);
 }
 const samePick=(a,b)=>a.start_ms===b.start_ms&&a.text===b.text;
 function toggleEmphasis(line,word){
  const current=picks(line),next=current.some(p=>samePick(p,word))?current.filter(p=>!samePick(p,word)):[...current,{text:word.text,start_ms:word.start_ms,end_ms:word.end_ms}];
  write(d=>{const all={...(d.emphasis || {})};if(next.length)all[line.segment_id]=next.sort((a,b)=>a.start_ms-b.start_ms);else delete all[line.segment_id];d.emphasis=all;});
 }

 // crop: where the clip-shaped window sits on the video, 0..1 across the slack each way (0.5 = centred)
 const cropOf=id=>{const c=read().crops?.[id] || {};const f=v=>Number.isFinite(v)?Math.min(1,Math.max(0,v)):0.5;return {x:f(c.x),y:f(c.y)};};
 // zoom scales the picture about the same point the window sits at, which is the export's smaller crop window
 function showCrop(at=cropAt(cropSource,video.currentTime*1000)){const z=at.zoom || 1;video.style.objectPosition=`${at.x*100}% ${at.y*100}%`;
  video.style.transformOrigin=`${at.x*100}% ${at.y*100}%`;video.style.transform=z>1?`scale(${z})`:'';}
 // who talks when, from the stored diarization (voice[id].turns); a turn under FOLLOW_MIN does not move the camera
 // (the export does the same: speaker_at in export.rs)
 const FOLLOW_MIN=1000,FOLLOW_ZOOM=1.2;
 function speakerAt(source,ms){
  const turns=((read().voice || {})[source]?.turns || []).filter(t=>t.end_ms-t.start_ms>=FOLLOW_MIN);
  const inside=turns.filter(t=>t.start_ms<=ms&&ms<t.end_ms).sort((a,b)=>(a.end_ms-a.start_ms)-(b.end_ms-b.start_ms))[0];
  const before=turns.filter(t=>t.start_ms<=ms).sort((a,b)=>b.start_ms-a.start_ms)[0];
  return (inside || before || turns[0])?.speaker ?? null;
 }
 const following=()=>read().follow_speaker===true;
 // the crop at a moment: the speaker's own window when the camera follows them, else the video's window
 function cropAt(source,ms){
  const base=cropOf(source),who=following()?speakerAt(source,ms):null,c=who===null?null:read().crops?.[source]?.speakers?.[String(who)];
  const f=(v,d)=>Number.isFinite(v)?Math.min(1,Math.max(0,v)):d;
  if(who===null)return {...base,zoom:1};
  // a person with no window of their own yet is shown 20% closer, so following shows even on a clip that fits
  return {x:f(c?.x,base.x),y:f(c?.y,base.y),zoom:Number.isFinite(c?.zoom)?Math.min(3,Math.max(1,c.zoom)):FOLLOW_ZOOM};
 }
 // the people heard in a video, by how long each talks, with whether their talk is kept from the dead-air cut
 const keepOf=source=>{const k=(read().speakers_keep || {})[source];const main=(read().voice || {})[source]?.main;return Array.isArray(k)?k:Number.isInteger(main)?[main]:[];};
 function people(source){
  const v=(read().voice || {})[source];if(!v)return [];
  const talk=new Map();for(const t of v.turns || [])talk.set(t.speaker,(talk.get(t.speaker) || 0)+t.end_ms-t.start_ms);
  return [...talk].sort((a,b)=>b[1]-a[1]).map(([id,ms],n)=>({id,ms,n,main:id===v.main,kept:keepOf(source).includes(id)}));
 }
 function drawPeople(){
  const host=$('speakerList');if(!host)return;
  const story=current(),source=story?.source_id,list=source?people(source):[];
  $('speakerBox').hidden=!list.length;
  host.replaceChildren(...list.map(p=>{
   const row=document.createElement('label');row.className='speaker-row';row.dataset.speaker=String(p.n%4);
   const box=document.createElement('input');box.type='checkbox';box.checked=p.kept;box.disabled=!editable();
   box.onchange=()=>{const next=new Set(keepOf(source));if(box.checked)next.add(p.id);else next.delete(p.id);
    write(d=>{d.speakers_keep={...(d.speakers_keep || {}),[source]:[...next].sort((a,b)=>a-b)};});findQuiet();};
   const dot=document.createElement('span');dot.className='speaker-dot';dot.setAttribute('aria-hidden','true');
   const name=document.createElement('span');name.textContent=`คนที่ ${p.n+1}${p.main?' (หลัก)':''} · พูด ${clock(p.ms)}`;
   const framed=read().crops?.[source]?.speakers?.[String(p.id)];
   // how close the camera goes on this person; a clip that fits the frame exactly can only follow by zooming
   const zoom=document.createElement('select');zoom.title='ซูมเข้าหาคนนี้';zoom.setAttribute('aria-label',`ซูมเข้าหาคนที่ ${p.n+1}`);zoom.disabled=!editable() || !following();
   for(const z of [1,1.2,1.3,1.5,1.75,2]){const o=document.createElement('option');o.value=String(z);o.textContent=z===1?'ไม่ซูม':`ซูม +${Math.round((z-1)*100)}%`;zoom.append(o);}
   zoom.value=String(framed?.zoom ?? FOLLOW_ZOOM);
   zoom.onchange=()=>write(d=>{const c={...(d.crops?.[source] || {x:0.5,y:0.5})};c.speakers={...(c.speakers || {}),[String(p.id)]:{x:0.5,y:0.5,...(c.speakers?.[String(p.id)] || {}),zoom:Number(zoom.value)}};d.crops={...(d.crops || {}),[source]:c};});
   row.append(box,dot,name,zoom);return row;
  }));
  if($('followSpeaker')){$('followSpeaker').checked=following();$('followSpeaker').disabled=!editable();}
 }
 if($('followSpeaker'))$('followSpeaker').onchange=()=>{if(!editable())return;const on=$('followSpeaker').checked;write(d=>{d.follow_speaker=on;});};

 {
  let drag=null;
  for(const frame of [$('textFrame'),$('editFrame')]){
  frame.addEventListener('pointerdown',e=>{
   if(e.target!==video || !cropSource || !editable() || e.button!==0)return;
   const fw=frame.clientWidth,fh=frame.clientHeight,vw=video.videoWidth,vh=video.videoHeight;if(!vw || !vh)return;
   const scale=Math.max(fw/vw,fh/vh);
   const now=cropAt(cropSource,video.currentTime*1000),z=now.zoom || 1;
   drag={id:e.pointerId,x0:e.clientX,y0:e.clientY,start:now,zoom:z,ox:vw*scale*z-fw,oy:vh*scale*z-fh,at:null};
   frame.setPointerCapture(e.pointerId);frame.dataset.cropping='true';e.preventDefault();
  });
  frame.addEventListener('pointermove',e=>{
   if(!drag || e.pointerId!==drag.id)return;
   const f=v=>Math.min(1,Math.max(0,v));
   drag.at={x:drag.ox>1?f(drag.start.x-(e.clientX-drag.x0)/drag.ox):0.5,y:drag.oy>1?f(drag.start.y-(e.clientY-drag.y0)/drag.oy):0.5,zoom:drag.zoom};
   showCrop(drag.at);
  });
  const end=e=>{
   if(!drag || e.pointerId!==drag.id)return;
   const at=drag.at,id=cropSource;drag=null;delete frame.dataset.cropping;
   // with the camera following, the window dragged is the one of the person talking now
   const who=following()?speakerAt(id,video.currentTime*1000):null,r=v=>Math.round(v*1000)/1000;
   if(at&&id)write(d=>{const c={...(d.crops?.[id] || {x:0.5,y:0.5})};
    if(who===null){c.x=r(at.x);c.y=r(at.y);}else c.speakers={...(c.speakers || {}),[String(who)]:{...(c.speakers?.[String(who)] || {}),x:r(at.x),y:r(at.y)}};
    d.crops={...(d.crops || {}),[id]:c};});
  };
  frame.addEventListener('pointerup',end);frame.addEventListener('pointercancel',end);
  }
 }
 // with the cut lines shown, the preview is the video as filmed: from `ms` it plays on to the end, nothing skipped
 function playRaw(ms,play){
  queue=[{start_ms:ms,end_ms:Math.max(ms+1,(video.duration || 0)*1000 || Infinity)}];video.currentTime=ms/1000;
  showWords(lines().find(l=>l.source_id===cropSource&&ms>=l.start_ms&&ms<l.end_ms));
  if(play)video.play().catch(()=>{});else{if(!video.paused)video.pause();step();}
 }
 // the playhead goes to `ms` inside the kept line `id`; play continues the story from there, paused shows that frame
 function seekTo(story,ms,id,play){
  const from=story.kept.findIndex(k=>k.segment_id===id);if(from<0)return;
  queue=story.kept.slice(from);video.currentTime=ms/1000;
  caption(story.kept[from].text);showWords(story.kept[from]);
  if(play){video.play().catch(()=>{});}else{if(!video.paused)video.pause();cancelAnimationFrame(ticking);ticking=0;}
  const head=$('editHead');if(head&&editView){const box=editView.boxes.find(b=>b.kept?.segment_id===id);
   const x=editView.showCut?ms:box?box.at+ms-box.kept.start_ms:-1;head.hidden=x<0;if(x>=0)head.style.left=(x/1000*editView.zoom)+'px';}
 }
 function handle(story,kept,edge,box,zoom){
  const grip=document.createElement('button');grip.type='button';grip.className='edit-grip';grip.dataset.edge=edge;
  grip.title=edge==='start_ms'?'ลากเพื่อยืดหรือหดต้นท่อน':'ลากเพื่อยืดหรือหดท้ายท่อน';grip.setAttribute('aria-label',grip.title+' ใช้ลูกศรซ้ายขวาทีละ 0.1 วินาที');
  let from=null,value=kept[edge];
  const snaps=()=>[video.currentTime*1000,...(kept.words || []).flatMap(w=>[w.start_ms,w.end_ms])];
  const snap=v=>{let best=v,gap=Infinity;for(const t of snaps()){const d=Math.abs(t-v);if(d/1000*zoom<=8&&d<gap){best=t;gap=d;}}return best;};
  const preview=v=>{v=snap(v);value=trimmed({start_ms:kept.from_ms,end_ms:kept.to_ms},{start_ms:edge==='start_ms'?v:kept.start_ms,end_ms:edge==='end_ms'?v:kept.end_ms})[edge];
   const span=edge==='start_ms'?kept.end_ms-value:value-kept.start_ms;box.style.width=Math.max(6,span/1000*zoom-2)+'px';
   $('editClock').textContent=`${edge==='start_ms'?'ต้น':'ท้าย'}ท่อน ${clock(value)}.${Math.floor(value/100)%10} · ท่อนนี้ยาว ${(span/1000).toFixed(1)} วินาที`;};
  grip.onpointerdown=e=>{from=e.clientX;grip.setPointerCapture(e.pointerId);e.preventDefault();};
  grip.onpointermove=e=>{if(from!==null)preview(kept[edge]+(e.clientX-from)/zoom*1000);};
  grip.onpointerup=()=>{if(from===null)return;from=null;save(story,kept,edge,value);};
  grip.onkeydown=e=>{if(e.key!=='ArrowLeft'&&e.key!=='ArrowRight')return;e.preventDefault();refocus={id:kept.segment_id,edge};save(story,kept,edge,kept[edge]+(e.key==='ArrowLeft'?-100:100));};
  if(refocus&&refocus.id===kept.segment_id&&refocus.edge===edge)queueMicrotask(()=>{grip.focus();refocus=null;});
  return grip;
 }
 function save(story,kept,edge,value){
  write(d=>{
   const plan=parsePlan(d.plan),entry=plan?.stories[story.index];if(!entry)return;
   const trims={...(entry.trims || {})},next={start_ms:kept.start_ms,end_ms:kept.end_ms,[edge]:value},fit=trimmed({start_ms:kept.from_ms,end_ms:kept.to_ms},next);
   if(fit.start_ms===kept.from_ms&&fit.end_ms===kept.to_ms)delete trims[kept.segment_id];else trims[kept.segment_id]=fit;
   entry.trims=trims;d.plan=JSON.stringify(plan,null,2);
  });
 }
 function move(story,id,target,side){
  if(!id || id===target)return;
  write(d=>{
   const plan=parsePlan(d.plan),entry=plan?.stories[story.index];if(!entry)return;
   const ids=(entry.segment_ids || []).filter(x=>x!==id),at=ids.indexOf(target);if(at<0)return;
   ids.splice(side==='after'?at+1:at,0,id);entry.segment_ids=ids;d.plan=JSON.stringify(plan,null,2);
  });
 }
 // keep or drop one line; a line brought back goes where its time puts it among the lines already kept
 function flip(story,line){
  write(d=>{
   const plan=parsePlan(d.plan),entry=plan?.stories[story.index];if(!entry)return;
   const ids=(entry.segment_ids || []).slice(),at=ids.indexOf(line.segment_id);
   if(at>=0){if(ids.length>1)ids.splice(at,1);}
   else{const start=new Map(lines().map(l=>[l.segment_id,l.start_ms])),before=ids.findIndex(id=>(start.get(id) ?? 0)>line.start_ms);ids.splice(before<0?ids.length:before,0,line.segment_id);}
   entry.segment_ids=ids;d.plan=JSON.stringify(plan,null,2);
  });
 }
 let ticking=0;
 function start(){const line=queue[0];if(!line)return;video.currentTime=line.start_ms/1000;video.play().catch(()=>{});cancelAnimationFrame(ticking);ticking=requestAnimationFrame(tick);}
 // timeupdate fires about 4 times a second, late enough to play the start of a cut line; a frame callback is on time
 let stepped=0;
 function tick(){step();ticking=queue.length&&!video.paused?requestAnimationFrame(tick):0;}
 // frame callbacks stop when the window is hidden or busy; timeupdate keeps cuts and the playhead going then
 video.addEventListener('timeupdate',()=>{if(performance.now()-stepped>120)step();});
 function step(){
  stepped=performance.now();
  const now=video.currentTime*1000,line=queue[0];
  if(line&&now>=line.end_ms){
   queue.shift();const next=queue[0];
   // seek only for a real jump: a next line that starts here, or overlaps this one after a trim, just plays on
   if(!next)video.pause();else if(next.start_ms>now+40 || next.start_ms<line.start_ms)video.currentTime=next.start_ms/1000;
  }
  const story=current(),said=story&&(editView?.showCut?lines().filter(l=>l.source_id===story.source_id):story.kept).find(l=>now>=l.start_ms&&now<l.end_ms);
  if(following()&&cropSource&&!document.querySelector('[data-cropping=true]'))showCrop();
  // the dead air cut out of the middle of a line is jumped over, as the export leaves it out
  if(said&&!video.paused&&!editView?.showCut){const gap=inner(said).find(([a,b])=>now>=a&&now<b-20);if(gap){video.currentTime=gap[1]/1000;return;}}
  const words=said?.words || [],wordByWord=read().text_styles?.speech?.enter==='words'&&words.length;
  // while a line plays, an empty box stays empty (a no-break space) instead of falling back to the sample text
  const shown=l=>(l.words?.length?speechText(l,now,wordByWord&&l===said):l.text) || '';
  // a line of one short word ("\u0e01\u0e32\u0e23" before a pause) reads with the line it runs into (P'Ohm 23 Oct; the
  // export does the same: join_short_lines in export.rs)
  const list=story?(editView?.showCut?lines().filter(l=>l.source_id===story.source_id):story.kept):[],i=said?list.indexOf(said):-1;
  const short=l=>!!l&&!/\s/.test((l.text || '').trim())&&[...(l.text || '').trim()].length<=SHORT;
  const next=(a,b)=>!!a&&!!b&&a.source_id===b.source_id&&b.start_ms>=a.end_ms;
  const before=i>0&&short(list[i-1])&&next(list[i-1],said)?shown(list[i-1]):'';
  const after=i>=0&&short(said)&&next(said,list[i+1])?shown(list[i+1]):'';
  caption(!said?'':(before+shown(said)+after) || '\u00a0');
  const held=story?emphasisAt(story,said,now):'';
  emphasis(held || (said?'\u00a0':''));
  subtitle(said?(read().subtitles || {})[said.segment_id] || '\u00a0':'');
  if(said&&said.segment_id!==wordLine?.segment_id&&!video.paused)showWords(said);
  const head=$('editHead');
  if(head&&editView){
   let x=now;
   if(!editView.showCut){const box=editView.boxes.find(b=>now>=b.kept.start_ms&&now<b.kept.end_ms);x=box?box.at+now-box.kept.start_ms:-1;}
   head.hidden=x<0;if(x>=0)head.style.left=(x/1000*editView.zoom)+'px';
  }
 }
 video.addEventListener('play',()=>{cancelAnimationFrame(ticking);ticking=requestAnimationFrame(tick);});
 video.addEventListener('seeked',()=>{if(video.paused)tick();});
 video.addEventListener('loadeddata',()=>{$('editVideoNote').textContent='';});
 // the first open of a video waits for its preview to be made; if the browser gives up, ask again
 video.addEventListener('error',()=>{$('editVideoNote').textContent='กำลังเตรียมพรีวิว ลองใหม่อัตโนมัติ';setTimeout(()=>{if(!video.closest('[hidden]'))video.load();},5000);});
 function playPause(){
  const story=current();if(!story)return;
  if(!video.paused){video.pause();return;}
  const now=video.currentTime*1000,line=queue[0];
  if(line&&now>=line.start_ms-40&&now<line.end_ms){video.play().catch(()=>{});return;}
  queue=story.kept.slice();start();
 }
 $('editPlay').onclick=playPause;
 const playLabel=()=>{const playing=!video.paused;$('editPlay').dataset.icon=playing?'pause':'play';$('editPlay').textContent=playing?'หยุด':'เล่นทั้งเรื่อง';};
 video.addEventListener('play',playLabel);video.addEventListener('pause',playLabel);
 // space plays or pauses on step 3, except while typing or when a button already takes the key
 // keys on steps 3 and 4: Space play/pause, arrows step a frame (Shift: a second); on step 3 Q/W trim the line
 // under the playhead up to it from the start/end, Delete cuts that line
 document.addEventListener('keydown',e=>{
  const step3=!$('editor').closest('[hidden]')&&!$('editor').hidden,step4=!$('captionDock').closest('[hidden]');
  if((!step3&&!step4) || e.ctrlKey || e.metaKey || e.altKey)return;
  if(e.target.closest?.('input,textarea,select,[contenteditable="true"],dialog,.edit-grip'))return;
  const story=current();if(!story)return;
  if(e.code==='Space'){if(e.repeat)return;e.preventDefault();playPause();return;}
  if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();if(!video.paused)video.pause();video.currentTime=Math.max(0,video.currentTime+(e.key==='ArrowLeft'?-1:1)*(e.shiftKey?1:1/30));step();return;}
  if(!step3 || !editable())return;
  const now=Math.round(video.currentTime*1000),line=story.kept.find(k=>now>=k.start_ms&&now<k.end_ms);if(!line)return;
  if(e.key==='q'||e.key==='Q'){e.preventDefault();save(story,line,'start_ms',now);}
  else if(e.key==='w'||e.key==='W'){e.preventDefault();save(story,line,'end_ms',now);}
  else if(e.key==='Delete'||e.key==='Backspace'){if(story.kept.length>1){e.preventDefault();flip(story,line);}}
 });
 $('editZoom').oninput=renderEditor;
 $('editTrack').addEventListener('wheel',e=>{
  if(!e.ctrlKey)return;e.preventDefault();
  const zoom=$('editZoom'),next=Math.min(Number(zoom.max),Math.max(Number(zoom.min),Math.round(Number(zoom.value)*(e.deltaY<0?1.2:1/1.2))));
  if(next!==Number(zoom.value)){zoom.value=next;renderEditor();}
 },{passive:false});
 $('editStory').onchange=()=>{const id=$('editStory').value;if(video&&!video.paused)video.pause();queue=[];write(d=>{d.edit_story=id;});};
 $('editShowCut').onchange=renderEditor;
 $('editReviewed').onclick=()=>{const story=current();if(!story)return;write(d=>{const next={...(d.story_reviews || {})};if(next[story.id])delete next[story.id];else next[story.id]=true;d.story_reviews=next;});};
 // make each video's preview as soon as it is in the project, so step 3 opens without waiting
 const warmed=new Set();
 function warm(){for(const f of read().files.filter(f=>f.source_id&&!warmed.has(f.source_id))){warmed.add(f.source_id);
  fetch(`/api/v1/projects/${encodeURIComponent(project)}/sources/${encodeURIComponent(f.source_id)}/preview`,{headers:{Range:'bytes=0-0'}}).catch(()=>warmed.delete(f.source_id));}
  findQuiet();}
 function allReviewed(){const m=model();return !!m&&m.stories.length>0&&m.stories.every(s=>reviews()[s.id]);}
 // emphasis round: every story's kept lines go to an AI, which names the words worth emphasising per line
 function keptLines(){const m=model();if(!m)return [];const seen=new Set();return m.stories.flatMap(s=>s.kept.map(l=>({story:s,line:lines().find(x=>x.segment_id===l.segment_id) || l}))).filter(({line})=>!seen.has(line.segment_id)&&seen.add(line.segment_id));}
 // the AI gets each kept line's text only (the engine's word chunks cut words apart, "เทิ|ลม", and the AI
 // copied those fragments); it answers with whole words copied from the text, and few of them
 function emphasisText(){
  const packet={schema_version:2,step:'emphasis',project_id:project,
   stories:(model()?.stories || []).filter(s=>s.kept.length).map(s=>({title:s.title,lines:s.kept.map(k=>{const l=lines().find(x=>x.segment_id===k.segment_id) || k;return {id:l.segment_id,text:l.text};})})),
   response_schema:{emphasis:{'<line id>':['คำที่เน้น']}}};
  return ['เลือก "คำเน้น" สำหรับซับคลิปสั้น คือคำที่จะขึ้นตัวใหญ่บนจอตอนคนพูดคำนั้น',
   'กติกา:',
   '1. เลือกเฉพาะท่อนที่มีใจความเด่นจริง ประมาณ 1 ใน 3 ของท่อนทั้งหมด ท่อนอื่นไม่ต้องใส่ในคำตอบ',
   '2. ท่อนละ 1 คำ (มากสุด 2) แต่ละคำยาวไม่เกิน 12 ตัวอักษร',
   '3. เลือกคำที่มีความหมายครบในตัว: ชื่อแบรนด์หรือชื่อเฉพาะ (เอาทั้งชื่อ ไม่ตัดครึ่ง) ตัวเลข ราคา คำที่ให้อารมณ์ หรือใจความหลักของท่อน',
   '4. ห้ามเลือกคำเติมหรือคำเชื่อม เช่น แบบ คือ อ่ะ อืม เออ ที่ ว่า แล้ว ก็ มัน นี้ เนี้ย ใช่ ปะ',
   '5. คัดลอกคำให้ตรงกับข้อความใน text ของท่อนนั้นทุกตัวอักษร ถ้า text สะกดผิดก็ใช้ตามนั้น ห้ามแก้ ห้ามแปล',
   '6. อ้างท่อนด้วย id ที่ให้',
   'ตอบกลับเป็น JSON ตาม response_schema อย่างเดียว ไม่ต้องมีคำอธิบาย',''].join('\n')+'\n'+JSON.stringify(packet,null,1);
 }
 function checkEmphasis(){
  const raw=$('emphasisAnswer').value.trim();if(!raw)return {error:''};
  const {value,json}=readJson(raw);if(json===undefined)return {error:'คำตอบไม่ครบ ลองคัดลอกใหม่ทั้งหมด'};
  if(value?.response_schema)return {error:'นี่คือข้อความที่ส่งให้ AI ยังไม่ใช่คำตอบ วางในแชท AI ก่อน แล้วคัดลอกคำตอบของ AI กลับมา'};
  const answer=value?.emphasis && typeof value.emphasis==='object'?value.emphasis:value;
  if(!answer || typeof answer!=='object' || Array.isArray(answer))return {error:'ไม่พบ emphasis ในคำตอบ'};
  const byId=new Map(keptLines().map(({line})=>[line.segment_id,line])),picked={};let words=0;
  for(const [id,list] of Object.entries(answer)){
   const line=byId.get(id);if(!line?.words?.length || !Array.isArray(list))continue;
   // each answer is a whole word, or neighbouring words written together; first match in the line wins
   const all=subWords(line),squash=t=>t.replace(/\s+/g,'').toLowerCase(),found=[];
   for(const p of list){
    const want=typeof p==='string'?squash(p):'';if(!want)continue;
    let hit=false;
    for(let i=0;i<all.length&&!hit;i++){
     let got='',j=i;while(j<all.length&&got.length<want.length)got+=squash(all[j++].text);
     if(got===want){hit=true;for(const w of all.slice(i,j))if(!found.includes(w))found.push(w);}
    }
    // not on word edges: take the characters as said
    if(!hit){const said=findSaid(line,p);if(said&&!found.some(w=>samePick(w,said)))found.push(said);}
   }
   found.sort((a,b)=>a.start_ms-b.start_ms);
   const joined=bridge(line,found);
   if(joined.length){picked[id]=joined;words+=joined.length;}
  }
  if(!words)return {error:'คำตอบไม่ตรงกับท่อนหรือคำที่ส่งไป ลองให้ AI ตอบใหม่'};
  return {picked,note:`เน้นคำใน ${Object.keys(picked).length} ท่อน`};
 }
 // step 4 makes two layers with a chat AI: emphasis words, and subtitles translated to English. An empty layer
 // shows its "create" button in the middle of its row; pressing it opens the send-and-paste box there
 function subtitleText(){
  const packet={schema_version:2,step:'subtitles',project_id:project,language:'en',
   lines:keptLines().map(({line})=>({id:line.segment_id,text:line.text})),
   response_schema:{subtitles:{'<line id>':'English subtitle'}}};
  return ['แปลซับคลิปสั้นเป็นภาษาอังกฤษ ทีละท่อน',
   'กติกา:',
   '1. แปลทุกท่อนที่ส่งไป ท่อนละ 1 ประโยคสั้นๆ อ่านทันบนจอ ไม่เกิน 60 ตัวอักษร',
   '2. แปลตามความหมาย เป็นภาษาพูดที่เป็นธรรมชาติ ไม่ต้องแปลคำเติม เช่น อ่ะ อืม เออ',
   '3. ชื่อเฉพาะและชื่อแบรนด์เขียนแบบที่คนพูดอังกฤษใช้',
   '4. อ้างท่อนด้วย id ที่ให้',
   'ตอบกลับเป็น JSON ตาม response_schema อย่างเดียว ไม่ต้องมีคำอธิบาย',''].join('\n')+'\n'+JSON.stringify(packet,null,1);
 }
 function checkSubtitles(){
  const raw=$('emphasisAnswer').value.trim();if(!raw)return {error:''};
  const {value,json}=readJson(raw);if(json===undefined)return {error:'คำตอบไม่ครบ ลองคัดลอกใหม่ทั้งหมด'};
  if(value?.response_schema)return {error:'นี่คือข้อความที่ส่งให้ AI ยังไม่ใช่คำตอบ วางในแชท AI ก่อน แล้วคัดลอกคำตอบของ AI กลับมา'};
  const answer=value?.subtitles && typeof value.subtitles==='object'?value.subtitles:value;
  if(!answer || typeof answer!=='object' || Array.isArray(answer))return {error:'ไม่พบ subtitles ในคำตอบ'};
  const sent=new Set(keptLines().map(({line})=>line.segment_id)),picked={};
  for(const [id,text] of Object.entries(answer))if(sent.has(id)&&typeof text==='string'&&text.trim())picked[id]=text.trim().slice(0,200);
  const n=Object.keys(picked).length;if(!n)return {error:'คำตอบไม่ตรงกับท่อนที่ส่งไป ลองให้ AI ตอบใหม่'};
  const missing=sent.size-n;
  return {picked,note:`แปลแล้ว ${n} ท่อน`+(missing?` · ขาด ${missing} ท่อน`:'')};
 }
 const rounds={
  emphasis:{make:'สร้างคำเน้น',title:'วางคำตอบคำเน้นจาก AI',text:emphasisText,check:checkEmphasis,file:'editorkrab-คำเน้น.txt',
   has:()=>Object.keys(read().emphasis || {}).length>0,
   // the answer covers every line that was sent: lines it left out lose their old emphasis (the AI now picks
   // only the stand-out lines); lines that were not sent keep theirs
   apply:c=>{const sent=new Set(keptLines().map(({line})=>line.segment_id));
    write(d=>{const all=Object.fromEntries(Object.entries(d.emphasis || {}).filter(([id])=>!sent.has(id)));d.emphasis={...all,...c.picked};});}},
  english:{make:'สร้างซับไตเติ้ล',title:'วางคำตอบซับไตเติ้ลจาก AI',text:subtitleText,check:checkSubtitles,file:'editorkrab-ซับไตเติ้ล.txt',
   has:()=>Object.keys(read().subtitles || {}).length>0,
   apply:c=>write(d=>{d.subtitles={...(d.subtitles || {}),...c.picked};d.subtitle_language='en';
    if(!(d.text_roles || []).includes('english'))d.text_roles=[...(d.text_roles || []),'english'];})}
 };
 let round='emphasis';const opened={emphasis:false,english:false};
 function say(text,ok){$('emphasisStatus').textContent=text;$('emphasisStatus').dataset.state=ok?'ok':'error';$('emphasisStatus').hidden=false;}
 function action(label,icon,run,needsEdit){const b=document.createElement('button');b.type='button';b.textContent=label;b.dataset.icon=icon;b.onclick=run;if(needsEdit)b.disabled=!editable();return b;}
 async function copyRound(key){try{await navigator.clipboard.writeText(rounds[key].text());say(`คัดลอก ${keptLines().length} ท่อนแล้ว วางในแชท AI ได้เลย`,true);}catch(_){say('คัดลอกไม่ได้ ลองกดอีกครั้ง',false);}}
 function downloadRound(key){const url=URL.createObjectURL(new Blob([rounds[key].text()],{type:'text/plain;charset=utf-8'}));
  const a=document.createElement('a');a.href=url;a.download=rounds[key].file;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 function openRound(key){round=key;$('emphasisTitle').textContent=rounds[key].title;$('emphasisAnswer').value='';checkEmphasisUi();$('emphasisDialog').showModal();}
 function drawLayerActions(){
  const host=$('layerActions');if(!host)return;
  const inStep4=!!$('editTrack').closest('#captionTransport')&&!!model();
  host.replaceChildren(...Object.entries(rounds).map(([key,r])=>{
   const slot=document.createElement('div');slot.className='layer-action';slot.dataset.role=key;
   slot.hidden=!inStep4 || (r.has()&&!opened[key]);
   if(!opened[key]){const make=action(r.make,'plus',()=>{opened[key]=true;drawLayerActions();},true);make.classList.add('primary');slot.append(make);}
   else{const label=document.createElement('span');label.textContent='ส่งข้อความให้ AI';
    slot.append(label,action('คัดลอกให้ AI','copy',()=>copyRound(key)),action('ดาวน์โหลดไฟล์','download',()=>downloadRound(key)),
     action('วางคำตอบ','paste',()=>openRound(key),true),action('ปิด','x',()=>{opened[key]=false;drawLayerActions();}));}
   return slot;}));
  // a layer already made can be made again from the play row
  const redo=$('captionRedo');
  if(redo)redo.replaceChildren(...Object.entries(rounds).filter(([key,r])=>inStep4&&r.has()&&!opened[key])
   .map(([key,r])=>action(r.make+'ใหม่','retry',()=>{opened[key]=true;drawLayerActions();},true)));
 }
 function checkEmphasisUi(){const c=rounds[round].check();$('emphasisCheck').textContent=c.picked?c.note:c.error;$('emphasisCheck').dataset.state=c.picked?'ok':'error';$('emphasisCheck').hidden=!$('emphasisCheck').textContent;$('emphasisApply').disabled=!c.picked || !editable();}
 $('emphasisAnswer').oninput=checkEmphasisUi;
 $('emphasisApply').onclick=event=>{
  const c=rounds[round].check();if(!c.picked){event.preventDefault();return;}
  opened[round]=false;rounds[round].apply(c);say(c.note,true);drawLayerActions();
 };
 // leaving step 3: stop the preview and take its line off the subtitle frame
 function stop(){queue=[];if(!video.paused)video.pause();caption('');emphasis('');subtitle('');
  const size=document.querySelector('.frame-aspect'),home=document.querySelector('.preview-caption');if(size&&home&&size.parentElement!==home)home.append(size);}
 // per video: how many stories came from it, and how many are arranged with the AI and reviewed
 function summary(){
  const out=new Map(),m=model();
  for(const s of m?.stories || []){const v=out.get(s.source_id) || {stories:0,refined:0,reviewed:0};v.stories++;if(s.refined)v.refined++;if(reviews()[s.id])v.reviewed++;out.set(s.source_id,v);}
  return out;
 }
 // the subtitle check's verdicts by line id, shown on the step 4 timeline
 function qc(groups){qcBy=groups;if(editView)renderEditor();showLine();}
 return {renderCards,renderEditor,allReviewed,warm,stop,summary,qc};
}
