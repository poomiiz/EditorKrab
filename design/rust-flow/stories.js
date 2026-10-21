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

export function createStories({project,read,lines,write,go,editable,caption,emphasis=()=>{}}){
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
  // step 3 cuts the video in its own frame; step 4 plays it under the subtitle frame with the emphasis track
  const textMode=!$('captionDock').closest('[data-panel]').hidden,frame=$(textMode?'textFrame':'editFrame');
  if(frame&&video.parentElement!==frame)frame.prepend(video);
  const controls=$('editPlay').closest('.editor-controls'),dock=textMode?$('captionTransport'):$('editor');
  if(controls.parentElement!==dock){dock.append(controls,$('editTrack'));}
  if(textMode)controls.querySelector('.editor-zoom').before(document.querySelector('.caption-ai'));
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
   const text=document.createElement('button');text.type='button';text.className='edit-text';text.textContent=line.text;text.onclick=()=>{if(showCut)playRaw(line.start_ms,true);else seekTo(story,kept.start_ms,kept.segment_id,true);};
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
   lane.append(box);
  }
  const head=document.createElement('div');head.className='edit-head';head.id='editHead';lane.append(head);
  const track=$('editTrack'),scroll=track.scrollLeft;track.replaceChildren(lane);track.scrollLeft=scroll;
  editView={story,showCut,boxes,zoom};
  wordLine=wordLine?lines().find(l=>l.segment_id===wordLine.segment_id) || null:null;drawWords();if(textMode)drawHeads();
  lane.onclick=e=>{
   if(e.target!==lane&&!e.target.classList.contains('edit-mark'))return;
   const at=(e.clientX-lane.getBoundingClientRect().left)/zoom*1000;
   if(showCut){playRaw(at,false);return;}
   const box=boxes.find(b=>at<b.at+b.kept.end_ms-b.kept.start_ms) || boxes.at(-1);
   if(box)seekTo(story,box.kept.start_ms+Math.max(0,at-box.at),box.kept.segment_id,false);
  };
  $('editClock').textContent=`ใช้ ${story.kept.length} บรรทัด · ${pieces(story)} ท่อน · ยาว ${clock(length(story.kept))}`;
 }
 let editView=null,refocus=null,cropSource=null,wordLine=null;
 // Thai runs on without spaces; a space only between two non-Thai words
 const thai=c=>c>='\u0E00'&&c<='\u0E7F';
 function joinWords(list,sep){return list.reduce((out,w)=>{w=String(w).trim();if(!w)return out;if(sep!==undefined)return out?out+sep+w:w;return out&&!thai(out.at(-1))&&!thai(w[0])?out+' '+w:out+w;},'');}
 // the words row: the line under the playhead (or the one clicked); each word toggles as emphasis
 function showWords(line){
  const same=(wordLine?.segment_id || null)===(line?.segment_id || null);wordLine=line || null;if(!same)drawWords();
 }
 // step 4 layers: a video bar and a speech clip per kept line, and emphasis clips where picked words are said;
 // the panel lists the words of the line picked on the speech layer
 function drawWords(){
  const lane=$('editTrack').querySelector('.edit-lane');if(!lane || !editView)return;
  lane.querySelectorAll('.edit-emph,.edit-vid').forEach(n=>n.remove());
  const textMode=lane.closest('#captionTransport'),emphasis=read().emphasis || {},z=editView.zoom,px=ms=>ms/1000*z;
  if(textMode){
   const roles=read().text_roles || [];lane.classList.toggle('edit-layer-off',!roles.includes('emphasis'));lane.classList.toggle('edit-layer-off-speech',!roles.includes('speech'));
   for(const {line,kept,at} of editView.boxes){
    const span=kept || line;
    const bar=document.createElement('div');bar.className='edit-vid';bar.style.left=px(at)+'px';bar.style.width=Math.max(4,px(span.end_ms-span.start_ms)-2)+'px';lane.append(bar);
    const words=picks(line).filter(w=>w.start_ms<span.end_ms&&(w.end_ms ?? w.start_ms+1)>span.start_ms);
    if(words.length){
     const first=Math.max(words[0].start_ms,span.start_ms),clip=document.createElement('button');clip.type='button';clip.className='edit-emph';
     clip.textContent=words.map(w=>w.text).join(' ');clip.title='คำเน้น · คลิกเพื่อแก้คำ';
     clip.style.left=px(at+first-span.start_ms)+'px';clip.style.width=Math.max(24,px(span.end_ms-first)-2)+'px';
     clip.onclick=e=>{e.stopPropagation();wordLine=line;drawWords();};lane.append(clip);
    }
   }
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
  const roles=read().text_roles || [],rows=[['วิดีโอ',null,22],['คำพูด','speech',38],['คำเน้น','emphasis',64],['อังกฤษ','english',90]];
  heads.replaceChildren(...rows.map(([label,role,top])=>{
   const row=document.createElement('div');row.className='layer-head';row.style.top=top+'px';row.style.height=(role==='english'?22:role?26:16)+'px';
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
 // the engine's timed words are sometimes phrases ("ยกตัวอย่าง"); split them into dictionary words with time
 // shared by length, so emphasis can be one word. Picks are stored as {text,start_ms,end_ms}; a bare number (older
 // drafts) is the whole timed word starting there.
 const segmenter=typeof Intl!=='undefined'&&Intl.Segmenter?new Intl.Segmenter('th',{granularity:'word'}):null;
 function subWords(line){
  return (line?.words || []).flatMap(w=>{
   const parts=segmenter?[...segmenter.segment(w.text.trim())].filter(p=>p.isWordLike).map(p=>p.segment):[w.text.trim()];
   if(parts.length<2)return [{text:w.text.trim(),start_ms:w.start_ms,end_ms:w.end_ms}];
   const total=parts.reduce((n,p)=>n+p.length,0),span=w.end_ms-w.start_ms;let at=0;
   return parts.map(p=>{const start=w.start_ms+Math.round(span*at/total);at+=p.length;return {text:p,start_ms:start,end_ms:w.start_ms+Math.round(span*at/total)};});
  }).filter(w=>w.text);
 }
 function picks(line){
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
 function showCrop(at=cropOf(cropSource)){video.style.objectPosition=`${at.x*100}% ${at.y*100}%`;}
 {
  let drag=null;
  for(const frame of [$('textFrame'),$('editFrame')]){
  frame.addEventListener('pointerdown',e=>{
   if(e.target!==video || !cropSource || !editable() || e.button!==0)return;
   const fw=frame.clientWidth,fh=frame.clientHeight,vw=video.videoWidth,vh=video.videoHeight;if(!vw || !vh)return;
   const scale=Math.max(fw/vw,fh/vh);
   drag={id:e.pointerId,x0:e.clientX,y0:e.clientY,start:cropOf(cropSource),ox:vw*scale-fw,oy:vh*scale-fh,at:null};
   frame.setPointerCapture(e.pointerId);frame.dataset.cropping='true';e.preventDefault();
  });
  frame.addEventListener('pointermove',e=>{
   if(!drag || e.pointerId!==drag.id)return;
   const f=v=>Math.min(1,Math.max(0,v));
   drag.at={x:drag.ox>1?f(drag.start.x-(e.clientX-drag.x0)/drag.ox):0.5,y:drag.oy>1?f(drag.start.y-(e.clientY-drag.y0)/drag.oy):0.5};
   showCrop(drag.at);
  });
  const end=e=>{
   if(!drag || e.pointerId!==drag.id)return;
   const at=drag.at,id=cropSource;drag=null;delete frame.dataset.cropping;
   if(at&&id)write(d=>{d.crops={...(d.crops || {}),[id]:{x:Math.round(at.x*1000)/1000,y:Math.round(at.y*1000)/1000}};});
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
  const words=said?.words || [],wordByWord=(read().text_styles?.speech?.enter ?? 'words')==='words'&&words.length;
  // while a line plays, an empty box stays empty (a no-break space) instead of falling back to the sample text
  caption(!said?'':(wordByWord?joinWords(words.filter(w=>w.start_ms<=now).map(w=>w.text)):said.text) || '\u00a0');
  emphasis(said?picks(said).filter(w=>w.start_ms<=now).map(w=>w.text).join(' ') || '\u00a0':'');
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
 $('editStory').onchange=()=>{const id=$('editStory').value;if(video&&!video.paused)video.pause();queue=[];write(d=>{d.edit_story=id;});};
 $('editShowCut').onchange=renderEditor;
 $('editReviewed').onclick=()=>{const story=current();if(!story)return;write(d=>{const next={...(d.story_reviews || {})};if(next[story.id])delete next[story.id];else next[story.id]=true;d.story_reviews=next;});};
 // make each video's preview as soon as it is in the project, so step 3 opens without waiting
 const warmed=new Set();
 function warm(){for(const f of read().files.filter(f=>f.source_id&&!warmed.has(f.source_id))){warmed.add(f.source_id);
  fetch(`/api/v1/projects/${encodeURIComponent(project)}/sources/${encodeURIComponent(f.source_id)}/preview`,{headers:{Range:'bytes=0-0'}}).catch(()=>warmed.delete(f.source_id));}}
 function allReviewed(){const m=model();return !!m&&m.stories.length>0&&m.stories.every(s=>reviews()[s.id]);}
 // emphasis round: every story's kept lines go to an AI, which names the words worth emphasising per line
 function keptLines(){const m=model();if(!m)return [];const seen=new Set();return m.stories.flatMap(s=>s.kept.map(l=>({story:s,line:lines().find(x=>x.segment_id===l.segment_id) || l}))).filter(({line})=>!seen.has(line.segment_id)&&seen.add(line.segment_id));}
 function emphasisText(){
  const packet={schema_version:1,step:'emphasis',project_id:project,
   stories:(model()?.stories || []).filter(s=>s.kept.length).map(s=>({title:s.title,lines:s.kept.map(k=>{const l=lines().find(x=>x.segment_id===k.segment_id) || k;return {id:l.segment_id,text:l.text,words:(l.words || []).map(w=>w.text)};})})),
   response_schema:{emphasis:{'<line id>':['คำที่เน้น']}}};
  return 'เลือกคำเน้นสำหรับซับ: แต่ละท่อนเลือกได้ 0-2 คำที่สำคัญที่สุด (ชื่อแบรนด์ ตัวเลข คำที่ให้อารมณ์ หรือใจความหลัก) ท่อนที่ไม่มีอะไรเด่นไม่ต้องใส่ '
   +'ใช้คำตาม words ของท่อนนั้นเท่านั้น อ้างท่อนด้วย id ที่ให้\nตอบกลับเป็น JSON ตาม response_schema อย่างเดียว ไม่ต้องมีคำอธิบาย\n\n'+JSON.stringify(packet,null,1);
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
   const found=subWords(line).filter(w=>list.some(p=>typeof p==='string'&&p.trim()&&(p.trim()===w.text || (p.includes(w.text)&&w.text.length>1))));
   if(found.length){picked[id]=found;words+=found.length;}
  }
  if(!words)return {error:'คำตอบไม่ตรงกับท่อนหรือคำที่ส่งไป ลองให้ AI ตอบใหม่'};
  return {picked,note:`เน้น ${words} คำ ใน ${Object.keys(picked).length} ท่อน`};
 }
 function checkEmphasisUi(){const c=checkEmphasis();$('emphasisCheck').textContent=c.picked?c.note:c.error;$('emphasisCheck').dataset.state=c.picked?'ok':'error';$('emphasisCheck').hidden=!$('emphasisCheck').textContent;$('emphasisApply').disabled=!c.picked || !editable();}
 $('emphasisCopy').onclick=async()=>{
  try{await navigator.clipboard.writeText(emphasisText());$('emphasisStatus').textContent=`คัดลอก ${keptLines().length} ท่อนแล้ว วางในแชท AI ได้เลย`;$('emphasisStatus').dataset.state='ok';}
  catch(_){$('emphasisStatus').textContent='คัดลอกไม่ได้ ลองกดอีกครั้ง';$('emphasisStatus').dataset.state='error';}
  $('emphasisStatus').hidden=false;
 };
 $('emphasisOpen').onclick=()=>{$('emphasisAnswer').value='';checkEmphasisUi();$('emphasisDialog').showModal();};
 $('emphasisAnswer').oninput=checkEmphasisUi;
 $('emphasisApply').onclick=event=>{
  const c=checkEmphasis();if(!c.picked){event.preventDefault();return;}
  // the AI's picks replace the emphasis of the lines it answered for; other lines keep theirs
  write(d=>{d.emphasis={...(d.emphasis || {}),...c.picked};});
  $('emphasisStatus').textContent=c.note;$('emphasisStatus').dataset.state='ok';$('emphasisStatus').hidden=false;
 };
 // leaving step 3: stop the preview and take its line off the subtitle frame
 function stop(){queue=[];if(!video.paused)video.pause();caption('');emphasis('');}
 // per video: how many stories came from it, and how many are arranged with the AI and reviewed
 function summary(){
  const out=new Map(),m=model();
  for(const s of m?.stories || []){const v=out.get(s.source_id) || {stories:0,refined:0,reviewed:0};v.stories++;if(s.refined)v.refined++;if(reviews()[s.id])v.reviewed++;out.set(s.source_id,v);}
  return out;
 }
 return {renderCards,renderEditor,allReviewed,warm,stop,summary};
}
