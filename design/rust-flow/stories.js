// Step 2's story cards, grouped by the video each story comes from, and step 3's editor: a 360p preview, the
// story's subtitle on top of it, and a zoomable timeline where the AI's cuts can be kept or dropped line by line.
const $=id=>document.getElementById(id);
const clock=ms=>`${Math.floor(ms/60000)}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}`;
export function parsePlan(text){try{const plan=JSON.parse(text);return Array.isArray(plan?.stories)?plan:null;}catch(_){return null;}}

export function createStories({project,read,lines,write,go,editable}){
 let queue=[];
 const video=$('editVideo');
 const reviews=()=>read().story_reviews || {};
 const length=list=>list.reduce((sum,l)=>sum+l.end_ms-l.start_ms,0);
 function model(){
  const plan=parsePlan(read().plan);if(!plan)return null;
  const all=lines(),bySegment=new Map(all.map(l=>[l.segment_id,l]));
  const stories=plan.stories.map((s,index)=>{
   const kept=(Array.isArray(s?.segment_ids)?s.segment_ids:[]).map(id=>bySegment.get(id)).filter(Boolean);
   const counts=new Map();for(const l of kept)counts.set(l.source_id,(counts.get(l.source_id) || 0)+1);
   return {index,id:String(s?.id || `story-${index+1}`),title:String(s?.title || `เรื่อง ${index+1}`).trim(),summary:String(s?.summary || '').trim(),
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
  const button=document.createElement('button');button.type='button';button.className='story-card';button.disabled=!story.kept.length;
  const done=!!reviews()[story.id],status=document.createElement('span');status.className='badge story-status';status.dataset.done=done;status.textContent=done?'ตรวจแล้ว':'ยังไม่ดู';
  const title=document.createElement('strong');title.textContent=`${story.index+1}. ${story.title}`;
  const summary=document.createElement('span');summary.className='story-summary';summary.textContent=story.summary || story.kept.map(l=>l.text).join(' ').slice(0,100);
  const meta=document.createElement('span');meta.className='caption';meta.textContent=`${clock(length(story.kept))} · ${pieces(story)} ท่อน`;
  button.append(status,title,summary,meta);
  button.onclick=()=>{write(d=>{d.edit_story=story.id;});go(3);};
  return button;
 }
 function current(){const m=model();if(!m)return null;return m.stories.find(s=>s.id===read().edit_story) || m.stories[0] || null;}
 function renderEditor(){
  const story=current();$('editEmpty').hidden=!!story;$('editor').hidden=!story;
  if(!story){$('editTitle').textContent='ตัดต่อ';return;}
  $('editTitle').textContent=`ตัดต่อ · ${story.index+1}. ${story.title}`;
  const done=!!reviews()[story.id];$('editReviewed').textContent=done?'ตรวจแล้ว ✓':'ตรวจเรื่องนี้แล้ว';$('editReviewed').classList.toggle('primary',!done);$('editReviewed').disabled=!editable();
  const src=`/api/v1/projects/${encodeURIComponent(project)}/sources/${encodeURIComponent(story.source_id)}/preview`;
  if(video.dataset.src!==src){video.dataset.src=src;$('editVideoNote').textContent='กำลังเตรียมพรีวิว 360p ครั้งแรกอาจรอสักครู่';video.src=src;video.load();}
  const zoom=Number($('editZoom').value),order=lines().filter(l=>l.source_id===story.source_id),keep=new Set(story.kept.map(l=>l.segment_id));
  const end=order.at(-1)?.end_ms || 0,lane=document.createElement('div');lane.className='edit-lane';lane.style.width=Math.ceil(end/1000*zoom+40)+'px';
  const every=zoom>=30?5:zoom>=10?15:zoom>=4?30:60;
  for(let s=0;s<=end/1000;s+=every){const mark=document.createElement('span');mark.className='edit-mark caption';mark.style.left=(s*zoom)+'px';mark.textContent=clock(s*1000);lane.append(mark);}
  for(const line of order){
   const kept=keep.has(line.segment_id),box=document.createElement('div');box.className='edit-seg';box.dataset.keep=kept;
   box.style.left=(line.start_ms/1000*zoom)+'px';box.style.width=Math.max(6,(line.end_ms-line.start_ms)/1000*zoom-2)+'px';box.title=line.text;
   const text=document.createElement('button');text.type='button';text.className='edit-text';text.textContent=line.text;text.onclick=()=>{queue=[line];start();};
   const toggle=document.createElement('button');toggle.type='button';toggle.className='edit-toggle';toggle.textContent=kept?'✕':'+';
   toggle.title=kept?'ตัดท่อนนี้ทิ้ง':'เก็บท่อนนี้';toggle.setAttribute('aria-label',toggle.title);toggle.disabled=!editable()||(kept&&story.kept.length===1);
   toggle.onclick=()=>flip(story,line);
   box.append(toggle,text);lane.append(box);
  }
  const head=document.createElement('div');head.className='edit-head';head.id='editHead';lane.append(head);
  const track=$('editTrack'),scroll=track.scrollLeft;track.replaceChildren(lane);track.scrollLeft=scroll;
  $('editClock').textContent=`ใช้ ${story.kept.length} บรรทัด · ${pieces(story)} ท่อน · ยาว ${clock(length(story.kept))}`;
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
 function start(){const line=queue[0];if(!line)return;video.currentTime=line.start_ms/1000;video.play().catch(()=>{});}
 video.addEventListener('timeupdate',()=>{
  const now=video.currentTime*1000,line=queue[0];
  if(line&&now>=line.end_ms){queue.shift();if(queue.length)video.currentTime=queue[0].start_ms/1000;else video.pause();}
  const story=current(),said=story&&lines().find(l=>l.source_id===story.source_id&&now>=l.start_ms&&now<l.end_ms);
  $('editCaption').textContent=said?.text || '';$('editCaption').hidden=!said;
  const head=$('editHead');if(head)head.style.left=(now/1000*Number($('editZoom').value))+'px';
 });
 video.addEventListener('loadeddata',()=>{$('editVideoNote').textContent='';});
 // the first open of a video waits for its preview to be made; if the browser gives up, ask again
 video.addEventListener('error',()=>{$('editVideoNote').textContent='กำลังเตรียมพรีวิว ลองใหม่อัตโนมัติ';setTimeout(()=>{if(!video.closest('[hidden]'))video.load();},5000);});
 $('editPlay').onclick=()=>{const story=current();if(!story)return;queue=story.kept.slice();start();};
 $('editZoom').oninput=renderEditor;
 $('editReviewed').onclick=()=>{const story=current();if(!story)return;write(d=>{const next={...(d.story_reviews || {})};if(next[story.id])delete next[story.id];else next[story.id]=true;d.story_reviews=next;});};
 // make each video's preview as soon as it is in the project, so step 3 opens without waiting
 const warmed=new Set();
 function warm(){for(const f of read().files.filter(f=>f.source_id&&!warmed.has(f.source_id))){warmed.add(f.source_id);
  fetch(`/api/v1/projects/${encodeURIComponent(project)}/sources/${encodeURIComponent(f.source_id)}/preview`,{headers:{Range:'bytes=0-0'}}).catch(()=>warmed.delete(f.source_id));}}
 function allReviewed(){const m=model();return !!m&&m.stories.length>0&&m.stories.every(s=>reviews()[s.id]);}
 return {renderCards,renderEditor,allReviewed,warm};
}
