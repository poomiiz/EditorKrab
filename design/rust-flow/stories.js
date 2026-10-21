// Step 2's story cards, grouped by the video each story comes from, and step 3's editor: a 360p preview, the
// story's subtitle on top of it, and a zoomable timeline where the AI's cuts can be kept or dropped line by line.
const $=id=>document.getElementById(id);
const clock=ms=>`${Math.floor(ms/60000)}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}`;
// The AI's pasted answer, checked before it counts. A ```json fence, words around the JSON and a bare list of stories
// (some AIs drop the {"stories": ...} wrapper) are fixed into the one shape export reads (`fixed`); anything else names
// the first thing missing so the paste can be redone, never guessed at.
// the JSON inside a chat answer: a ```json fence or words around it are dropped; json is undefined when none parses
function readJson(raw){
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

export function createStories({project,read,lines,write,go,editable,caption}){
 let queue=[];
 const video=$('editVideo');
 const reviews=()=>read().story_reviews || {};
 const length=list=>list.reduce((sum,l)=>sum+l.end_ms-l.start_ms,0);
 // a kept line's in/out points, moved by dragging its edges: at most 2 s past the line, at least 0.2 s long
 const REACH=2000,SHORTEST=200;
 function trimmed(line,trim){
  const n=v=>Number.isFinite(v)?Math.round(v/100)*100:null;
  const start=Math.max(0,line.start_ms-REACH,Math.min(n(trim?.start_ms) ?? line.start_ms,line.end_ms-SHORTEST));
  const end=Math.max(start+SHORTEST,Math.min(n(trim?.end_ms) ?? line.end_ms,line.end_ms+REACH));
  return {start_ms:start,end_ms:end};
 }
 function model(){
  const plan=parsePlan(read().plan);if(!plan)return null;
  const all=lines(),bySegment=new Map(all.map(l=>[l.segment_id,l]));
  const stories=plan.stories.map((s,index)=>{
   const trims=s?.trims && typeof s.trims==='object' ? s.trims : {};
   const kept=(Array.isArray(s?.segment_ids)?s.segment_ids:[]).map(id=>bySegment.get(id)).filter(Boolean)
    .map(l=>({...l,from_ms:l.start_ms,to_ms:l.end_ms,...trimmed(l,trims[l.segment_id])}));
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
  const head=document.createElement('p');head.className='caption';
  head.textContent=`ได้ ${m.stories.length} เรื่อง จาก ${groups.length} ไฟล์ · ตรวจแล้ว ${m.stories.filter(s=>reviews()[s.id]).length} เรื่อง · กดเรื่องเพื่อตัดต่อ`;
  host.replaceChildren(head,...groups.map(g=>group(g.file.name,g.stories)),...(loose.length?[group('ไม่พบช่วงพูดของเรื่องนี้',loose)]:[]));
 }
 function group(name,stories){
  const box=document.createElement('section');box.className='story-group';
  const title=document.createElement('h3');title.textContent=`${name} · ${stories.length} เรื่อง`;
  const grid=document.createElement('div');grid.className='story-grid';grid.append(...stories.map(card));
  box.append(title,grid);return box;
 }
 function card(story){
  const row=document.createElement('div');row.className='story-row';row.dataset.refined=story.refined;
  const button=document.createElement('button');button.type='button';button.className='story-card';button.disabled=!story.kept.length;
  const done=!!reviews()[story.id],status=document.createElement('span');status.className='badge story-status';status.dataset.done=done;status.textContent=done?'ตรวจแล้ว':'ยังไม่ดู';
  const title=document.createElement('strong');title.textContent=`${story.index+1}. ${story.title}`;
  const summary=document.createElement('span');summary.className='story-summary';summary.textContent=story.summary || story.kept.map(l=>l.text).join(' ').slice(0,100);
  const meta=document.createElement('span');meta.className='caption story-meta';meta.textContent=`${clock(length(story.kept))} · ${pieces(story)} ท่อน`;
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
  const done=!!reviews()[story.id];$('editReviewed').textContent=done?'ตรวจแล้ว':'ตรวจเรื่องนี้แล้ว';$('editReviewed').classList.toggle('primary',!done);$('editReviewed').disabled=!editable();
  // the preview plays under the subtitle frame, so subtitles are placed and styled on the real picture
  const frame=$('textFrame');if(frame&&video.parentElement!==frame)frame.prepend(video);
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
   const text=document.createElement('button');text.type='button';text.className='edit-text';text.textContent=line.text;text.onclick=()=>{queue=[span];start();};
   const toggle=document.createElement('button');toggle.type='button';toggle.className='edit-toggle';toggle.dataset.icon=kept?'x':'plus';
   toggle.title=kept?'ตัดท่อนนี้ทิ้ง':'เก็บท่อนนี้';toggle.setAttribute('aria-label',toggle.title);toggle.disabled=!editable()||(kept&&story.kept.length===1);
   toggle.onclick=()=>flip(story,line);
   box.append(toggle,text);
   if(kept&&editable())box.append(handle(story,kept,'start_ms',box,zoom),handle(story,kept,'end_ms',box,zoom));
   if(kept&&!showCut&&editable()){
    box.draggable=true;box.dataset.id=kept.segment_id;
    box.ondragstart=e=>{if(e.target.closest('.edit-grip')){e.preventDefault();return;}e.dataTransfer.setData('text/plain',kept.segment_id);e.dataTransfer.effectAllowed='move';box.classList.add('dragging');};
    box.ondragend=()=>box.classList.remove('dragging');
    box.ondragover=e=>{e.preventDefault();const r=box.getBoundingClientRect();box.dataset.drop=e.clientX<r.left+r.width/2?'before':'after';};
    box.ondragleave=()=>{delete box.dataset.drop;};
    box.ondrop=e=>{e.preventDefault();const side=box.dataset.drop;delete box.dataset.drop;move(story,e.dataTransfer.getData('text/plain'),kept.segment_id,side);};
   }
   lane.append(box);
  }
  const head=document.createElement('div');head.className='edit-head';head.id='editHead';lane.append(head);
  const track=$('editTrack'),scroll=track.scrollLeft;track.replaceChildren(lane);track.scrollLeft=scroll;
  editView={story,showCut,boxes,zoom};
  $('editClock').textContent=`ใช้ ${story.kept.length} บรรทัด · ${pieces(story)} ท่อน · ยาว ${clock(length(story.kept))}`;
 }
 let editView=null,refocus=null;
 function handle(story,kept,edge,box,zoom){
  const grip=document.createElement('button');grip.type='button';grip.className='edit-grip';grip.dataset.edge=edge;
  grip.title=edge==='start_ms'?'ลากเพื่อยืดหรือหดต้นท่อน':'ลากเพื่อยืดหรือหดท้ายท่อน';grip.setAttribute('aria-label',grip.title+' ใช้ลูกศรซ้ายขวาทีละ 0.1 วินาที');
  let from=null,value=kept[edge];
  const preview=v=>{value=trimmed({start_ms:kept.from_ms,end_ms:kept.to_ms},{start_ms:edge==='start_ms'?v:kept.start_ms,end_ms:edge==='end_ms'?v:kept.end_ms})[edge];
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
 function tick(){
  const now=video.currentTime*1000,line=queue[0];
  if(line&&now>=line.end_ms){
   queue.shift();const next=queue[0];
   // seek only for a real jump: a next line that starts here, or overlaps this one after a trim, just plays on
   if(!next)video.pause();else if(next.start_ms>now+40 || next.start_ms<line.start_ms)video.currentTime=next.start_ms/1000;
  }
  const story=current(),said=story&&story.kept.find(l=>now>=l.start_ms&&now<l.end_ms);
  caption(said?.text || '');
  const head=$('editHead');
  if(head&&editView){
   let x=now;
   if(!editView.showCut){const box=editView.boxes.find(b=>now>=b.kept.start_ms&&now<b.kept.end_ms);x=box?box.at+now-box.kept.start_ms:-1;}
   head.hidden=x<0;if(x>=0)head.style.left=(x/1000*editView.zoom)+'px';
  }
  ticking=queue.length&&!video.paused?requestAnimationFrame(tick):0;
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
 document.addEventListener('keydown',e=>{
  if(e.code!=='Space' || e.repeat || $('editor').hidden || $('editor').closest('[hidden]'))return;
  if(e.target.closest?.('input,textarea,select,button,[contenteditable="true"],dialog'))return;
  e.preventDefault();playPause();
 });
 $('editZoom').oninput=renderEditor;
 $('editStory').onchange=()=>{const id=$('editStory').value;if(video&&!video.paused)video.pause();queue=[];write(d=>{d.edit_story=id;});};
 $('editShowCut').onchange=renderEditor;
 $('editReviewed').onclick=()=>{const story=current();if(!story)return;write(d=>{const next={...(d.story_reviews || {})};if(next[story.id])delete next[story.id];else next[story.id]=true;d.story_reviews=next;});};
 // make each video's preview as soon as it is in the project, so step 3 opens without waiting
 const warmed=new Set();
 function warm(){for(const f of read().files.filter(f=>f.source_id&&!warmed.has(f.source_id))){warmed.add(f.source_id);
  fetch(`/api/v1/projects/${encodeURIComponent(project)}/sources/${encodeURIComponent(f.source_id)}/preview`,{headers:{Range:'bytes=0-0'}}).catch(()=>warmed.delete(f.source_id));}}
 function allReviewed(){const m=model();return !!m&&m.stories.length>0&&m.stories.every(s=>reviews()[s.id]);}
 // leaving step 3: stop the preview and take its line off the subtitle frame
 function stop(){queue=[];if(!video.paused)video.pause();caption('');}
 return {renderCards,renderEditor,allReviewed,warm,stop};
}
