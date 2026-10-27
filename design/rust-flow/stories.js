import {captionRows,captionJoin,defaults} from './text-style.js';
// Step 2's story cards, grouped by the video each story comes from, and step 3's editor: a 360p preview, the
// story's subtitle on top of it, and a zoomable timeline where the AI's cuts can be kept or dropped line by line.
const $=id=>document.getElementById(id);
const clock=ms=>`${Math.floor(ms/60000)}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}`;
// The AI's pasted answer, checked before it counts. A ```json fence, words around the JSON and a bare list of stories
// (some AIs drop the {"stories": ...} wrapper) are fixed into the one shape export reads (`fixed`); anything else names
// the first thing missing so the paste can be redone, never guessed at.
// the JSON inside a chat answer: a ```json fence or words around it are dropped; json is undefined when none parses
export function readJson(raw){
 // Gemini's copy indents with non-breaking spaces (U+00A0), which JSON does not allow as spacing; chat pages also
 // slip in other wide or zero-width spaces. They become plain spaces before reading.
 raw=raw.replace(/[\u00a0\u1680\u2000-\u200b\u202f\u205f\u3000\ufeff]/g,' ');
 const fence=raw.match(/```(?:json)?\s*([\s\S]*?)```/),body=(fence?fence[1]:raw).trim();
 const from=body.search(/[[{]/),to=Math.max(body.lastIndexOf('}'),body.lastIndexOf(']')),json=from<0?body:body.slice(from,to+1);
 try{return {value:JSON.parse(json),json};}catch(_){return {};}
}
// P'Ohm 26 Oct: no copy and paste with the browser AI. Every request goes out as a .md file and the AI sends a .md
// file back, which is read without showing its text.
export const MD_REPLY='ส่งคำตอบกลับเป็นไฟล์ .md หนึ่งไฟล์ ข้างในเป็น JSON ตาม response_schema อย่างเดียว ไม่ต้องพิมพ์คำตอบหรือคำอธิบายในแชท';
export function saveMd(name,text){
 const url=URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'})),a=document.createElement('a');
 a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
// the text of the answer file the person picks; null when they pick none
export function pickMd(){
 return new Promise((done,fail)=>{
  const input=document.createElement('input');input.type='file';input.accept='.md,.markdown,.txt,.json,text/markdown,text/plain,application/json';
  input.onchange=()=>{const file=input.files?.[0];if(!file)return done(null);
   if(file.size>5*1024*1024)return fail(Error('ไฟล์คำตอบใหญ่เกินไป เลือกไฟล์ .md ที่ AI ส่งมา'));
   file.text().then(done,()=>fail(Error('อ่านไฟล์คำตอบไม่ได้')));};
  input.click();
 });
}
// chat AIs rename fields (Gemini answered story_id and no title); take the usual synonyms, else the summary's opening
export function storyFields(s,index){
 const summary=String(s?.summary || s?.description || '').trim();
 const title=String(s?.title || s?.name || s?.topic || s?.headline || '').trim() || (summary.split(/[ ,.]/).filter(Boolean).slice(0,6).join(' ').slice(0,48) || `เรื่อง ${index+1}`);
 return {id:String(s?.id || s?.story_id || `story-${index+1}`),title,summary};
}
export function checkPlan(text,lines){
 const raw=String(text || '').trim();if(!raw)return {error:''};
 const {value,json}=readJson(raw);if(json===undefined)return {error:'ในไฟล์ไม่มีคำตอบ JSON ที่ครบ ให้ AI ส่งไฟล์ .md ใหม่'};
 if(value?.response_schema)return {error:'นี่คือไฟล์ที่ส่งให้ AI ยังไม่ใช่คำตอบ อัปโหลดไฟล์ .md ที่ AI ส่งกลับมา'};
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

// This exact trimmed input is used both when copying a packet and when resolving its reply.
export function captionKeptLine(line){
 const words=(line.words || []).filter(w=>w.start_ms>=line.start_ms&&w.end_ms<=line.end_ms);
 return {...line,words,text:words.length?words.map(w=>w.text.trim()).reduce(captionJoin,''):line.text};
}
// A content identity, not a security token: changing cuts, text or word clocks invalidates an AI reply.
export function captionInputRevision(project,state,kept){
 // Hash the exact public anchors sent to the AI. Recounting silence or changing a font
 // does not make an unchanged word selection stale; changed text, trims and clocks do.
 const input=JSON.stringify([3,project,state.speakers_keep ?? null,kept.map(line=>({id:line.segment_id,source_id:line.source_id,text:line.text,start_ms:line.start_ms,end_ms:line.end_ms,
  words:(line.words || []).map((w,i)=>({id:'w'+i,text:w.text,start_ms:w.start_ms,end_ms:w.end_ms})),
  speakers:(state.voice?.[line.source_id]?.turns || []).filter(t=>t.start_ms<line.end_ms&&t.end_ms>line.start_ms).map(t=>({speaker:t.speaker,start_ms:t.start_ms,end_ms:t.end_ms}))}))]);
 let hash=14695981039346656037n;for(const byte of new TextEncoder().encode(input))hash=BigInt.asUintN(64,(hash^BigInt(byte))*1099511628211n);
 return hash.toString(16).padStart(16,'0');
}
export function resolveWordSelection(line,ids){
 if(!Array.isArray(ids)||!ids.length||ids.length>30)return null;
 const indices=ids.map(id=>typeof id==='string'&&/^w(0|[1-9][0-9]*)$/.test(id)?Number(id.slice(1)):-1);
 if(indices.some((n,i)=>n<0||n>=(line.words?.length || 0)||(i>0&&n!==indices[i-1]+1)))return null;
 const words=indices.map(i=>line.words[i]);
 if(words.some((w,i)=>(i>0&&w.start_ms<words[i-1].end_ms)||!w.text?.trim()||!Number.isFinite(w.start_ms)||!Number.isFinite(w.end_ms)||w.end_ms<w.start_ms||w.start_ms<line.start_ms||w.end_ms>line.end_ms))return null;
 if(words.at(-1).end_ms<=words[0].start_ms)return null;
 if(words[0].end_ms<=words[0].start_ms||words.at(-1).end_ms<=words.at(-1).start_ms)return null;
 return {text:words.map(w=>w.text.trim()).reduce(captionJoin,''),start_ms:words[0].start_ms,end_ms:words.at(-1).end_ms};
}
export function validateEnglishCueReply(value,project,manifest){
 if(!manifest?.cues?.length)return {error:'รอจัดช่วงซับให้ครบก่อน'};
 if(value?.project_id!==project||value?.input_revision!==manifest.input_revision)return {error:'คำตอบเป็นร่างเก่า ดาวน์โหลดไฟล์ .md ใหม่แล้วให้ AI ตอบอีกครั้ง'};
 const answer=value.subtitles;if(!answer||typeof answer!=='object'||Array.isArray(answer))return {error:'ไม่พบคำแปลในคำตอบ'};
 const ids=new Set(manifest.cues.map(c=>c.id)),picked={};
 for(const [id,text] of Object.entries(answer)){
  if(!ids.has(id)||typeof text!=='string'||!text.trim()||text.trim().length>60||/[\r\n]/.test(text))return {error:'รหัสช่วงซับหรือคำแปลไม่ตรง ใช้คำแปลบรรทัดเดียวไม่เกิน 60 ตัวอักษร'};
  picked[id]=text.trim();
 }
 if(Object.keys(picked).length!==ids.size)return {error:`คำแปลขาด ${ids.size-Object.keys(picked).length} ช่วง ให้ตอบให้ครบก่อนใช้`};
 return {picked,note:`แปลตามจังหวะ ${ids.size} ช่วงแล้ว`};
}
export function englishCueAt(manifest,translations,story,source,ms,position=null){
 const cues=(manifest?.cues || []).filter(c=>c.story_id===story);
 if(!cues.length||cues.some(c=>typeof translations?.[c.id]!=='string'||!translations[c.id].trim()))return '';
 const matches=cues.filter(c=>Number.isFinite(position)?position>=c.output_start_ms&&position<c.output_end_ms:
  c.source_id===source&&(!position||(c.segment_id===position.segment_id&&(c.occurrence || 1)===position.occurrence))&&c.ranges.some(r=>ms>=r.start_ms&&ms<r.end_ms));
 const cue=matches.length===1?matches[0]:null;
 return cue?translations[cue.id].trim():'';
}
export function validateEmphasisSelections(answer,input){
 if(!answer||typeof answer!=='object'||Array.isArray(answer))return {error:'ไม่พบคำเน้นในคำตอบ'};
 const byId=new Map(input.map(line=>[line.segment_id,line])),picked={},issues=[];
 for(const [id,list] of Object.entries(answer)){
  const line=byId.get(id);if(!line||!Array.isArray(list)||list.length>2)return {error:'รหัสท่อนหรือจำนวนคำเน้นไม่ถูกต้อง'};
  const found=[];
  for(const pick of list){
   const word=resolveWordSelection(line,pick?.word_ids);
   if(!word||found.some(w=>word.start_ms<w.end_ms&&word.end_ms>w.start_ms)){
    const text=(Array.isArray(pick?.word_ids)?pick.word_ids:[]).map(id=>line.words?.[Number(String(id).slice(1))]?.text || id).join('');
    issues.push({id,text,reason:'ช่วงเวลาคำไม่ครบหรือซ้อนกัน'});continue;
   }
   if(pick.text!==undefined&&(typeof pick.text!=='string'||pick.text.replace(/\s/g,'')!==word.text.replace(/\s/g,'')))return {error:'ข้อความคำเน้นไม่ตรงกับรหัสคำ'};
   found.push({...word,word_ids:[...pick.word_ids]});
  }
  if(found.length)picked[id]=found.sort((a,b)=>a.start_ms-b.start_ms);
 }
 for(const issue of issues)delete picked[issue.id];
 if(issues.length&&!Object.keys(picked).length)return {error:`คำ “${issues[0].text}” ยังไม่มีช่วงเวลาที่ใช้ได้ จัดเวลาคำนี้ก่อน`,issues};
 const count=Object.values(picked).reduce((n,v)=>n+v.length,0);
 return {picked,issues,count,note:`พร้อม ${count} คำ`+(issues.length?` · ต้องตรวจ ${issues.length} คำ`:'')};
}
// Caption timing contract: selected word IDs remain one phrase; only older split picks may coalesce.
export const EMPHASIS_GAP_MS=160,EMPHASIS_HOLD_MS=450,EMPHASIS_TAIL_MS=180,VISUAL_RULES_VERSION=5;
export function emphasisGroups(line,list){
 const letters=(line.words || []).flatMap(w=>{const text=[...w.text.trim()].filter(c=>! /\s/.test(c));return text.map((c,i)=>({c,start:w.start_ms+(w.end_ms-w.start_ms)*i/text.length}));}),flat=letters.map(x=>x.c).join('');
 const starts=(text,near)=>{let chosen=-1,gap=Infinity;for(let i=flat.indexOf(text);i>=0;i=flat.indexOf(text,i+1)){const d=Math.abs(letters[i].start-near);if(d<gap){chosen=i;gap=d;}}return chosen;};
 const out=[];
 for(const p of [...list].sort((a,b)=>a.start_ms-b.start_ms)){
  const last=out.at(-1),a=last?.at(-1),joined=a?last.map(x=>x.text).reduce(captionJoin,''):'',candidate=captionJoin(joined,p.text).replace(/\s/g,'');
  const first=last?.[0],aStart=first?starts(joined.replace(/\s/g,''),first.start_ms):-1,pStart=starts(p.text.replace(/\s/g,''),p.start_ms);
  const adjacent=aStart>=0&&pStart===aStart+joined.replace(/\s/g,'').length&&flat.slice(aStart,pStart+p.text.replace(/\s/g,'').length)===candidate;
  if(a&&!Array.isArray(a.word_ids)&&!Array.isArray(p.word_ids)&&p.start_ms>=a.end_ms&&p.start_ms-a.end_ms<=EMPHASIS_GAP_MS&&adjacent)last.push(p);
  else out.push([p]);
 }
 return out;
}
// Pieces are source ranges in playback order; the same mapping is used by export::emphasis_lines.
export function emphasisCueWindows(pieces){
 const events=new Map();let at=0;
 for(const p of pieces){
  for(const group of p.groups || []){
   const first=group[0],last=group.at(-1),start=first.start_ms,end=last.end_ms;
   if(start>=p.end_ms||end<=p.start_ms)continue;
   const key=JSON.stringify([p.segment_id,start,group.map(x=>x.text)]),a=at+Math.max(start,p.start_ms)-p.start_ms,b=at+Math.min(end,p.end_ms)-p.start_ms;
   const old=events.get(key);if(old)old.end_ms=Math.max(old.end_ms,b);else events.set(key,{start_ms:a,end_ms:b,text:group.map(x=>x.text).reduce(captionJoin,''),segment_id:p.segment_id});
  }
  at+=p.end_ms-p.start_ms;
 }
 const out=[...events.values()].sort((a,b)=>a.start_ms-b.start_ms);
 for(let i=0;i<out.length;i++)out[i].end_ms=Math.min(at,out[i+1]?.start_ms ?? at,Math.max(out[i].end_ms+EMPHASIS_TAIL_MS,out[i].start_ms+EMPHASIS_HOLD_MS));
 return out.filter(x=>x.end_ms>x.start_ms);
}
export function quietTimingKey(min,rule,keep,words){return JSON.stringify([min,rule,keep,words]);}
// Mouth detections are noisy. Merge adjacent evidence, ignore flashes, never hold a close-up through a long unknown gap.
export function cameraTurns(voice={}){
 const visual=Array.isArray(voice.seen)&&Array.isArray(voice.faces)&&voice.faces.length>0;
 const input=visual?voice.seen.map(t=>({...t,who:t.person})):(voice.turns || []).map(t=>({...t,who:t.speaker}));
 const turns=[];
 for(const t of input.filter(t=>Number.isFinite(t.start_ms)&&t.end_ms>t.start_ms).sort((a,b)=>a.start_ms-b.start_ms)){
  const last=turns.at(-1);
  if(last&&last.who===t.who&&t.start_ms-last.end_ms<=1200)last.end_ms=Math.max(last.end_ms,t.end_ms);
  else turns.push({...t});
 }
 // a cut lands where speech starts or stops (within 1 s), not on the 1 s grid mouth motion is measured in (export.rs snap)
 const speech=voice.speech || [],snap=(at,edges)=>edges.filter(e=>Math.abs(e-at)<=1000).sort((x,y)=>Math.abs(x-at)-Math.abs(y-at))[0] ?? at;
 return turns.filter(t=>t.end_ms-t.start_ms>=(visual?2500:1000)).filter(t=>!visual||!speech.length||speech.some(([a,b])=>Math.min(t.end_ms,b)-Math.max(t.start_ms,a)>=600))
  .map(t=>({...t,start_ms:snap(t.start_ms,speech.map(s=>s[0])),end_ms:snap(t.end_ms,speech.map(s=>s[1]))})).filter(t=>t.end_ms>t.start_ms);
}
export function cameraPersonAt(voice,ms){
 const turns=cameraTurns(voice),inside=turns.filter(t=>t.start_ms<=ms&&ms<t.end_ms).sort((a,b)=>a.start_ms-b.start_ms)[0];
 const before=turns.filter(t=>t.end_ms<=ms&&ms-t.end_ms<=1200).sort((a,b)=>b.end_ms-a.end_ms)[0];
 return (inside||before)?.who ?? null;
}
export function createStories({project,read,lines,write:writeDraft,go,editable,caption,emphasis=()=>{},subtitle=()=>{},editLine=()=>{},listen=()=>{},captionState=()=>({saved:true,revision:0})}){
 // quietKey's per-run answers (below) end with any write from this file
 let keyNow=null;const write=change=>{keyNow=null;return writeDraft(change);};
 let queue=[];
 let englishManifest=null,manifestDraft='',manifestCurrent='',manifestBusy=false,manifestError='',manifestRetry=0;
 function manifestInput(){return JSON.stringify([read(),captionRevision()]);}
 function manifestReady(){return englishManifest&&manifestDraft===manifestCurrent&&captionState().saved&&!analysisPending();}
 function queueManifest(){clearTimeout(manifestRetry);manifestRetry=setTimeout(()=>{if(model())loadManifest();},500);}
 async function loadManifest(){
  if(!model())return;
  const snapshot=manifestInput();manifestCurrent=snapshot;
  if(manifestBusy)return;
  if(!captionState().saved||analysisPending()){queueManifest();return;}
  if(englishManifest&&manifestDraft===snapshot)return;
  manifestBusy=true;manifestError='';
  try{
   const state=captionState(),response=await fetch(`/api/v1/projects/${encodeURIComponent(project)}/caption-cues?draft_revision=${state.revision}`,{cache:'no-store',signal:AbortSignal.timeout(10000)});
   const value=await response.json();
   if(response.status===409){queueManifest();return;}
   if(!response.ok)throw Error(value.error || 'caption_cues_failed');
   if(value.project_id!==project||value.draft_revision!==state.revision||!Array.isArray(value.cues)||!value.input_revision)throw Error('caption_cues_invalid');
   const currentInput=manifestInput();if(!captionState().saved||snapshot!==currentInput){manifestCurrent=currentInput;queueManifest();return;}
   englishManifest=value;manifestDraft=snapshot;
  }catch(_){manifestError='อ่านช่วงซับไม่ได้ ลองเปิดขั้นนี้ใหม่';}
  finally{manifestBusy=false;drawLayerActions();if(editView)drawWords();if(video.readyState>=1)step();}
 }
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
 // Nina clips keep breaths (pauses of 0.3-0.5 s stay in) and cut gaps over about a second, and keep the listener
 // LEAD: more is kept before speech starts again, as VAD hears a word late (export.rs LEAD_PAD_MS)
 const PAD=100,LEAD=250,AIR_MIN=1000,AIR_RULE=7,AIR_LEAST=0.3;
 const airMin=()=>{const v=read().dead_min_ms;return !Number.isFinite(v)?AIR_MIN:v===0?0:Math.max(v,AIR_LEAST*1000);};
 function quietOf(source){
  if(airMin()===0)return [];
  if(read().voice?.[source]&&(read().quiet_key || {})[source]!==quietKey(source))return [];
  const spans=(read().quiet || {})[source],keep=(read().air_keep || {})[source] || [];
  return Array.isArray(spans)?spans.filter(([s])=>!keep.includes(s)):[];
 }
 const FILLERS=new Set(['เออ','เอ่อ','เอิ่ม','อืม','อือ','อ่า','อ้า','อะ','อ่ะ','คือ','แบบ','แบบว่า']);
 function pastFillers(words,start,end){
  const said=words.filter(w=>(w.start_ms+w.end_ms)/2>=start&&w.start_ms<end&&w.text.trim());let i=0;
  while(i<said.length){const t=said[i].text.trim();if(!FILLERS.has(t)&&said[i+1]?.text.trim()!==t)break;i++;}
  // the cut lands before the real word but never inside the filler said just before it (export.rs)
  return i>0&&i<said.length?Math.max(said[i].start_ms-100,Math.min(said[i-1].end_ms,said[i].start_ms))+100:null;
 }
 function unquiet(line,span,trim){
  const spans=quietOf(line.source_id);
  let a=span.start_ms,b=span.end_ms;
  if(!Number.isFinite(trim?.start_ms))for(const [s,e] of spans)if(s<=a+PAD&&e>a)a=Math.max(a,e-LEAD);
  // filler and stammer at the start go: "เออ คือ คือ มีโมเม้นต์" starts at "มีโมเม้นต์" (export.rs past_fillers)
  let floor=-Infinity;
  if(!Number.isFinite(trim?.start_ms)){const at=pastFillers(line.words || [],a,b);if(at!==null){a=Math.max(a,at-100);floor=Math.max(0,...(line.words || []).filter(w=>w.start_ms<a).map(w=>Math.min(w.end_ms,a)));}}
  // a line that starts while its sound is already on starts where it began, up to 300 ms back (export.rs onset),
  // never back into the fillers just cut
  if(!Number.isFinite(trim?.start_ms)){const l=(read().voice?.[line.source_id]?.loud || []).find(([x,y])=>x<a&&a<y);if(l)a=Math.max(l[0],a-300,floor);}
  if(!Number.isFinite(trim?.end_ms))for(const [s,e] of [...spans].reverse())if(s>a&&s<b&&e>=b-PAD)b=Math.min(b,s+PAD);
  return b-a>=SHORTEST?{start_ms:a,end_ms:b}:span;
 }
 // the dead air cut out of a kept line's middle, as [from, to] in the video's time
 function inner(line){
  const out=[];let at=line.start_ms;
  for(const [s,e] of quietOf(line.source_id)){const a=s+PAD,b=e-LEAD;if(a>at&&b<line.end_ms&&b>=a+200){out.push([a,b]);at=b;}}
  return out;
 }
 // every quiet stretch inside a kept line, cut or clicked back, for the timeline marks
 function marks(line){
  const keep=(read().air_keep || {})[line.source_id] || [],all=airMin()===0?[]:(read().quiet || {})[line.source_id] || [];
  return all.filter(([s,e])=>s+PAD>line.start_ms&&e-LEAD<line.end_ms&&e-s>=200+PAD+LEAD).map(([s,e])=>({s,e,from:s+PAD,to:e-LEAD,kept:keep.includes(s)}));
 }
 const playLength=l=>l.end_ms-l.start_ms-inner(l).reduce((n,[a,b])=>n+b-a,0);
 // a moment of a kept line as played (its dead air cut out), and back: the timeline draws lines this long
 const playedIn=(l,ms)=>Math.max(0,Math.min(ms,l.end_ms)-l.start_ms)-inner(l).reduce((n,[a,b])=>n+Math.max(0,Math.min(ms,b)-a),0);
 function sourceIn(l,off){let t=l.start_ms,left=Math.max(0,off);for(const [a,b] of inner(l)){if(t+left<a)return t+left;left-=a-t;t=b;}return Math.min(l.end_ms-1,t+left);}
 function toggleAir(source,start){
  if(!editable())return;
  write(d=>{const keep={...(d.air_keep || {})},list=new Set(keep[source] || []);if(list.has(start))list.delete(start);else list.add(start);keep[source]=[...list];d.air_keep=keep;});
 }
 // each video is listened to once (about a fifth of its length on CPU); a new shortest pause only recounts
 const listening=new Set();let airRetry=0,airNote='';
 // each transcribed word as [start, end]: dead air never cuts into one (voice.rs)
 const wordStarts=source=>lines().filter(l=>l.source_id===source).flatMap(l=>(l.words || []).map(w=>[w.start_ms,Number.isFinite(w.end_ms)?w.end_ms:w.start_ms])).filter(([a])=>Number.isFinite(a));
 function airStatus(text){airNote=text;const el=$('airStatus');if(el)el.textContent=text;}
 // every line asks for this key and it spells out every word of the video: it is rebuilt only when the lines'
 // word lists (kept as the same objects while unchanged, transcription.js timedWords), the shortest pause or the
 // kept pauses change
 const keySeen=new Map();
 // drawing step 4 asks once per line; within one run of code the words cannot change unless this file writes,
 // so the key is kept until the run ends (or a write)
 function quietKey(source){
  if(!keyNow){keyNow=new Map();queueMicrotask(()=>{keyNow=null;});}
  if(!keyNow.has(source))keyNow.set(source,quietKeyBuilt(source));
  return keyNow.get(source);
 }
 function quietKeyBuilt(source){
  const words=lines().filter(l=>l.source_id===source).map(l=>l.words || []),min=airMin(),keep=JSON.stringify(keepOf(source)),seen=keySeen.get(source);
  if(seen&&seen.min===min&&seen.keep===keep&&seen.words.length===words.length&&seen.words.every((w,i)=>w===words[i] || (!w.length&&!words[i].length)))return seen.key;
  const key=quietTimingKey(min,AIR_RULE,keepOf(source),wordStarts(source));
  keySeen.set(source,{min,keep,words,key});return key;
 }
 function analysisPending(){return read().files.some(f=>{if(!f.source_id)return false;const v=read().voice?.[f.source_id];return listening.has(f.source_id)||(airMin()>0&&v&&(read().quiet_key || {})[f.source_id]!==quietKey(f.source_id))||(read().follow_speaker&&v?.visual_rules_version!==VISUAL_RULES_VERSION);});}
 async function findQuiet(){
  if(!editable()){clearTimeout(airRetry);airRetry=setTimeout(findQuiet,1000);return;}
  if(Number.isFinite(read().dead_min_ms)&&read().dead_min_ms>0&&read().dead_min_ms<AIR_LEAST*1000){write(d=>{d.dead_min_ms=AIR_LEAST*1000;});return;}
  const voice=read().voice || {},files=read().files.filter(f=>f.source_id&&lines().some(l=>l.source_id===f.source_id));
  // newer face rules matter only to a camera that follows the talker: other projects are not looked at again
  const needsVisual=f=>!voice[f.source_id] || !Array.isArray(voice[f.source_id].seen) || (read().follow_speaker&&voice[f.source_id].visual_rules_version!==VISUAL_RULES_VERSION);
  const todo=files.filter(f=>needsVisual(f)&&!listening.has(f.source_id));
  const keyOf=f=>quietKey(f.source_id);
  const stale=files.filter(f=>voice[f.source_id]&&!needsVisual(f)&&(read().quiet_key || {})[f.source_id]!==keyOf(f)&&airMin()>0&&!listening.has(f.source_id));
  for(const f of stale){listening.add(f.source_id);const requestedKey=keyOf(f);
   try{const v=voice[f.source_id];const r=await fetch('/api/v1/voice/dead-air',{method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({speech:v.speech,turns:v.turns,words:wordStarts(f.source_id),total_ms:v.total_ms,min_ms:airMin(),keep:keepOf(f.source_id),loud:v.loud || [],hush:v.hush || []}),signal:AbortSignal.timeout(30000)});
    const a=await r.json();if(!r.ok || !Array.isArray(a.dead))throw Error(a.error || 'dead_air_failed');
    if(keyOf(f)===requestedKey)write(d=>{d.quiet={...(d.quiet || {}),[f.source_id]:a.dead};d.quiet_key={...(d.quiet_key || {}),[f.source_id]:requestedKey};});}
   catch(_){airStatus('นับช่วงเงียบใหม่ไม่สำเร็จ ลองเปลี่ยนค่าอีกครั้ง');}
   finally{listening.delete(f.source_id);if(keyOf(f)!==requestedKey)queueMicrotask(findQuiet);}
  }
  if(!todo.length)return;
  const status=await fetch('/api/v1/voice',{cache:'no-store'}).then(r=>r.json()).catch(()=>null);
  if(!status?.ready){airStatus(status?`ตัดช่วงเงียบต้องใช้ตัวแยกเสียง (${Math.round((status.download_bytes || 0)/1048576)} MB ครั้งเดียว)`:'อ่านสถานะตัวแยกเสียงไม่ได้');const b=$('airDownload');if(b)b.hidden=!status;return;}
  for(const f of todo){listening.add(f.source_id);const requestedKey=keyOf(f);airStatus('กำลังฟังเสียงและดูหน้าคนในภาพ…');
   try{const r=await fetch(`/api/v1/projects/${encodeURIComponent(project)}/sources/${encodeURIComponent(f.source_id)}/voice`,{method:'POST',headers:{'Content-Type':'application/json'},
     // a 50-minute 1080p50 file took 33 minutes (C4926, 26 Oct 2026); the old 15-minute cut-off failed it every time
     body:JSON.stringify({words:wordStarts(f.source_id),min_ms:airMin() || AIR_MIN}),signal:AbortSignal.timeout(4*3600000)});
    const a=await r.json();if(!r.ok || !Array.isArray(a.dead))throw Error(a.error || 'voice_failed');
    write(d=>{d.voice={...(d.voice || {}),[f.source_id]:{speech:a.speech,turns:a.turns,total_ms:a.total_ms,main:a.main,faces:a.faces || [],seen:a.seen || [],loud:a.loud || [],hush:a.hush || [],visual_rules_version:VISUAL_RULES_VERSION}};
     d.quiet={...(d.quiet || {}),[f.source_id]:a.dead};d.quiet_key={...(d.quiet_key || {}),[f.source_id]:airMin()&&keyOf(f)===requestedKey?requestedKey:''};});
    airStatus(`เจอช่วงเงียบ ${a.dead.length} ช่วง`);}
   catch(e){airStatus('ฟังเสียงไม่สำเร็จ ('+(e.message || 'ไม่ทราบสาเหตุ')+') ท่อนยังไม่ถูกตัด');}
   finally{listening.delete(f.source_id);if(keyOf(f)!==requestedKey)queueMicrotask(findQuiet);}
  }
 }
 if($('airDownload'))$('airDownload').onclick=async()=>{
  const b=$('airDownload');b.disabled=true;airStatus('กำลังดาวน์โหลดตัวแยกเสียง…');
  try{const r=await fetch('/api/v1/voice/download',{method:'POST',signal:AbortSignal.timeout(900000)});const a=await r.json();if(!r.ok || !a.ready)throw Error(a.error || 'download_failed');b.hidden=true;airStatus('');findQuiet();}
  catch(e){airStatus('ดาวน์โหลดไม่สำเร็จ ลองอีกครั้ง');}finally{b.disabled=false;}
 };
 // any length in seconds, 0.1 s steps from AIR_LEAST (shorter gaps are between words); 0 turns the cut off;
 // a value out of range is put back to what is saved
 if($('airMin'))$('airMin').onchange=()=>{if(!editable())return;const v=Number($('airMin').value);
  if(!Number.isFinite(v) || (v!==0&&v<AIR_LEAST) || v>10){$('airMin').value=String(airMin()/1000);airStatus('ใส่ 0.3 ถึง 10 วินาที (สั้นกว่านี้จะตัดโดนคำพูด) หรือ 0 = ไม่ตัด');return;}
  write(d=>{d.dead_min_ms=Math.round(v*10)*100;});findQuiet();};
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
  const refine=document.createElement('button');refine.type='button';refine.dataset.icon='cut';refine.textContent=story.refined?'เรียบเรียงใหม่':'ตัดต่อเรียบเรียง';refine.title=story.refined?'ดาวน์โหลดไฟล์ .md เรื่องนี้ให้ AI เรียบเรียงอีกครั้ง':'ดาวน์โหลดไฟล์ .md เรื่องนี้ให้ AI เลือกและเรียงท่อน';
  refine.disabled=!story.kept.length || !editable();refine.onclick=()=>openRefine(story);
  // two stories that read as one half each become one: the next story of the same video joins this one
  const next=model()?.stories.slice(story.index+1).find(s=>s.source_id===story.source_id);
  const join=document.createElement('button');join.type='button';join.dataset.icon='plus';join.textContent='รวมกับเรื่องถัดไป';
  join.title=next?`รวม "${next.title}" เข้ากับเรื่องนี้ แล้วกดตัดต่อเรียบเรียงใหม่`:'ไม่มีเรื่องถัดไปจากวิดีโอนี้';join.hidden=!next;join.disabled=!editable();
  join.onclick=()=>next&&joinStories(story,next);
  row.append(button,refine,join);return row;
 }
 // the joined story keeps every line either had in round 1, in the video's order, and is arranged again with the AI
 function joinStories(story,next){
  if(!editable())return;
  write(d=>{
   const plan=parsePlan(d.plan),a=plan?.stories[story.index],b=plan?.stories[next.index];if(!a || !b)return;
   const at=new Map(lines().map(l=>[l.segment_id,l.start_ms]));
   const ids=[...new Set([...(a.round1_ids || a.segment_ids || []),...(b.round1_ids || b.segment_ids || [])])].sort((x,y)=>(at.get(x) ?? 0)-(at.get(y) ?? 0));
   a.segment_ids=ids;delete a.round1_ids;
   a.trims={...(b.trims || {}),...(a.trims || {})};
   const fields=storyFields(b,next.index);a.summary=[storyFields(a,story.index).summary,fields.summary].filter(Boolean).join(' ');
   plan.stories.splice(next.index,1);d.plan=JSON.stringify(plan,null,2);
   const reviewsNow={...(d.story_reviews || {})};delete reviewsNow[story.id];delete reviewsNow[next.id];d.story_reviews=reviewsNow;
   if(d.edit_story===next.id)d.edit_story=story.id;
  });
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
  // the shape of a finished Nina clip (NinaStyleBook, 5 CapCut drafts measured 23 Oct): hook, setup, proof, twist,
  // lesson, button; 60-130 s; the listener's questions and laughs stay in
  return 'เรียบเรียงเรื่องนี้ให้เป็นคลิปสั้นที่ดูแล้วรู้เรื่องในตัวเอง ยาวประมาณ 60-130 วินาที ลำดับการเล่า: '
   +'1) เปิดด้วยประโยคที่บอกว่าคนดูจะได้อะไร (ใน 5 วินาทีแรก ไม่เปิดด้วยการเกริ่น) 2) ปูเรื่อง 3) หลักฐานจริง เช่น ตัวเลข ชื่อ สถานที่ ตัวอย่างเปรียบเทียบ '
   +'4) จุดหักมุม 5) บทเรียนประโยคเดียว 6) ปิดด้วยมุก คำคม หรือประโยคที่อยากให้ดูต่อ '
   +'เก็บคำถาม ข้อสงสัย และเสียงหัวเราะของคนถาม/คนฟังไว้ (เป็นเสียงแทนคนดู) เก็บรายละเอียดเฉพาะ ตัดคำพูดกว้างๆ ที่ไม่มีสาระ ตัดท่อนที่พูดซ้ำหรือพูดติดขัด '
   +'เรียงลำดับใหม่ได้ถ้าเล่าแล้วดีขึ้นแต่ต้องคงความหมายเดิม ห้ามจบกลางประโยค อ้างท่อนด้วย id ใน lines เท่านั้น ห้ามสร้างท่อนหรือเวลาใหม่ '
   +'segment_ids คือท่อนที่เก็บ เรียงตามลำดับที่จะเล่น title ต้องเป็นคำที่พูดจริงในท่อนที่เก็บ ไม่เกิน 40 ตัวอักษร ปรับ summary ให้ตรงกับเรื่องที่เรียบเรียงแล้ว\n'
   +MD_REPLY+'\n\n'+JSON.stringify(packet,null,1);
 }
 const refineStory=()=>model()?.stories.find(s=>s.id===refining) || null;
 function sendRefine(){
  const story=refineStory();if(!story)return;
  saveMd(`editorkrab-เรียบเรียง-เรื่อง${story.index+1}.md`,refineText(story));
  $('refineCopyNote').textContent=`ดาวน์โหลดไฟล์ .md แล้ว (${offered(story).length} ท่อน) แนบไฟล์นี้ในแชท AI`;
 }
 function openRefine(story){
  refining=story.id;$('refineTitle').textContent=`ตัดต่อเรียบเรียง · ${story.index+1}. ${story.title}`;
  $('refineAnswer').value='';checkRefineUi();$('refineDialog').showModal();sendRefine();
 }
 $('refineUpload').onclick=async()=>{
  let text;try{text=await pickMd();}catch(error){$('refineAnswer').value='';checkRefineUi();$('refineStatus').textContent=error.message;$('refineStatus').hidden=false;return;}
  if(text===null)return;
  $('refineAnswer').value=text;checkRefineUi();
  if(!checkRefine().ids){$('refineStatus').textContent='ไฟล์คำตอบใช้ไม่ได้: '+($('refineStatus').textContent || 'ไฟล์ว่าง');$('refineStatus').dataset.state='error';$('refineStatus').hidden=false;}
 };
 function checkRefine(){
  const story=refineStory(),raw=$('refineAnswer').value.trim();if(!story || !raw)return {error:''};
  const {value,json}=readJson(raw);if(json===undefined)return {error:'ในไฟล์ไม่มีคำตอบ JSON ที่ครบ ให้ AI ส่งไฟล์ .md ใหม่'};
  if(value?.response_schema)return {error:'นี่คือไฟล์ที่ส่งให้ AI ยังไม่ใช่คำตอบ อัปโหลดไฟล์ .md ที่ AI ส่งกลับมา'};
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
 $('refineCopy').onclick=sendRefine;
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
  loadManifest();
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
  $('editOptions').hidden=textMode;$('captionOptions').hidden=!textMode;
  document.querySelector('.edit-stage').append($('editOptions'));
  document.querySelector('#textStyleWorkspace .text-inspector').prepend($('captionOptions'));
  if(video.readyState<1){caption('\u00a0');emphasis('\u00a0');subtitle('\u00a0');}
  if(textMode)$('captionOptionsPanel').append(document.querySelector('.caption-ai'),document.querySelector('.sub-timing'));
  const title=$(textMode?'captionsTitle':'editTitle');if(pick.parentElement!==title)title.append(pick);
  cropSource=story.source_id;showCrop();
  const src=`/api/v1/projects/${encodeURIComponent(project)}/sources/${encodeURIComponent(story.source_id)}/preview`;
  if(video.dataset.src!==src){video.dataset.src=src;$('editVideoNote').textContent='กำลังเตรียมพรีวิว 360p ครั้งแรกอาจรอสักครู่';video.src=src;video.load();}
  // the clip as it plays: kept lines end to end in story order; the switch adds the cut lines back at their own time
  const zoom=Number($('editZoom').value),showCut=$('editShowCut').checked,px=ms=>ms/1000*zoom;
  const order=lines().filter(l=>l.source_id===story.source_id),keep=new Map(story.kept.map(l=>[l.segment_id,l]));
  const boxes=showCut ? order.map(l=>({line:l,kept:keep.get(l.segment_id),at:(keep.get(l.segment_id) || l).start_ms}))
   : story.kept.reduce((list,k)=>{const at=list.length?list.at(-1).at+playLength(list.at(-1).kept):0;list.push({line:k,kept:k,at});return list;},[]);
  // a box's width: as played (dead air cut out, P'Ohm 24 Oct: "ยุบหายไปเลยเหมือนเนื้อหาที่ตัดทิ้ง"), or as recorded with the cut lines shown
  const widthOf=(span,kept)=>kept&&!showCut?playLength(kept):span.end_ms-span.start_ms;
  const end=showCut ? Math.max(0,...order.map(l=>l.end_ms),...story.kept.map(k=>k.end_ms)) : length(story.kept);
  const lane=document.createElement('div');lane.className='edit-lane';lane.style.width=Math.ceil(px(end)+40)+'px';
  const every=zoom>=30?5:zoom>=10?15:zoom>=4?30:60;
  for(let s=0;s<=end/1000;s+=every){const mark=document.createElement('span');mark.className='edit-mark caption';mark.style.left=px(s*1000)+'px';mark.textContent=clock(s*1000);lane.append(mark);}
  for(const [boxIndex,{line,kept,at}] of boxes.entries()){
   const span=kept || line,box=document.createElement('div');box.className='edit-seg';box.dataset.keep=!!kept;
   box.style.left=px(at)+'px';box.style.width=Math.max(6,px(widthOf(span,kept))-2)+'px';box.title=line.text;
   const text=document.createElement('button');text.type='button';text.className='edit-text';
   text.textContent=textMode&&(line.words || []).length?speechText(line,null) || '·':line.text;
   text.onclick=()=>{if(scrubbed)return;if(textMode){if(kept)seekTo(story,kept.start_ms,kept.segment_id,false,showCut?null:boxIndex);wordLine=lines().find(l=>l.segment_id===line.segment_id) || line;drawWords();showLine();return;}if(showCut)playRaw(line.start_ms,true);else seekTo(story,kept.start_ms,kept.segment_id,true,boxIndex);};
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
   // dead air inside the line: a thin mark where it is cut (the line is drawn without it), dashed where it was
   // clicked back in; with the cut lines shown, the cut stretch is hatched at its own length
   if(kept)for(const m of marks(kept)){const cut=document.createElement('button');cut.type='button';cut.className='edit-air';cut.dataset.kept=String(m.kept);
    const thin=!showCut&&!m.kept;cut.dataset.thin=String(thin);
    cut.style.left=px(showCut?m.from-span.start_ms:playedIn(kept,m.from))-(thin?6:0)+'px';cut.style.width=(thin?12:Math.max(3,px(m.to-m.from)))+'px';
    cut.title=(m.kept?'ช่วงเงียบที่เก็บไว้ ':'ตัดช่วงเงียบ ')+((m.e-m.s)/1000).toFixed(1)+' วิ · กดเพื่อดู หรือ'+(m.kept?'ตัดออก':'เอากลับมา');cut.setAttribute('aria-label',cut.title);cut.disabled=!editable();
    cut.dataset.key=kept.source_id+'|'+m.s;cut.reopen=()=>airMenu(cut,kept,m);
    cut.onclick=e=>{e.stopPropagation();airMenu(cut,kept,m);};box.append(cut);}
   lane.append(box);
  }
  const head=document.createElement('div');head.className='edit-head';head.id='editHead';lane.append(head);
  const track=$('editTrack'),scroll=track.scrollLeft;track.replaceChildren(lane);track.scrollLeft=scroll;
  // the timeline is drawn again on every save and tick: an open dead-air menu stays open on its mark
  // the same menu element moves to the new lane, so a press on its buttons is not lost to a redraw
  if(airOpen){const mark=[...lane.querySelectorAll('.edit-air')].find(x=>x.dataset.key===airOpen),menu=document.querySelector('.air-menu');
   if(mark&&menu){const box=mark.parentElement;menu.style.left=(box.offsetLeft+mark.offsetLeft)+'px';menu.style.top=(box.offsetTop+box.offsetHeight+2)+'px';lane.append(menu);}
   else if(mark)mark.reopen();}
  editView={story,showCut,boxes,zoom};
  wordLine=wordLine?lines().find(l=>l.segment_id===wordLine.segment_id) || null:null;drawWords();if(textMode)drawHeads();
  const seekAt=x=>{
   const at=Math.max(0,(x-lane.getBoundingClientRect().left)/zoom*1000);
   if(showCut){playRaw(at,false);return;}
   const box=boxes.find(b=>at<b.at+playLength(b.kept)) || boxes.at(-1);
   if(box)seekTo(story,sourceIn(box.kept,at-box.at),box.kept.segment_id,false,boxes.indexOf(box));
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
  $('editClock').textContent=clock(played(story.kept));
  $('editClock').title=`${story.kept.length} บรรทัด · ${pieces(story)} ท่อน`;
  if(video.readyState>=1)step();
  if($('airMin')&&document.activeElement!==$('airMin'))$('airMin').value=String(airMin()/1000);if($('airStatus')&&airNote)$('airStatus').textContent=airNote;
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
    const bar=document.createElement('div');bar.className='edit-vid';{const who=speakerAt(line.source_id,span.start_ms),n=seenOf(line.source_id)?who ?? -1:people(line.source_id).findIndex(p=>p.id===who);if(n>=0)bar.dataset.speaker=String(n%4);}bar.style.left=px(at)+'px';bar.style.width=Math.max(4,px(kept&&!editView.showCut?playLength(kept):span.end_ms-span.start_ms)-2)+'px';lane.append(bar);
    const said=!read().english_cues?subs[line.segment_id]:'';
    if(said){const sub=document.createElement('div');sub.className='edit-sub';sub.textContent=said;sub.title=said;sub.lang=read().subtitle_language || 'en';
     sub.style.left=px(at)+'px';sub.style.width=Math.max(24,px(kept&&!editView.showCut?playLength(kept):span.end_ms-span.start_ms)-2)+'px';lane.append(sub);}
   }
   if(read().english_cues&&manifestReady()){
    const storyCues=englishManifest.cues.filter(c=>c.story_id===current()?.id),translations=read().english_cues.translations || {};
    if(storyCues.every(c=>translations[c.id]))for(const cue of storyCues){
     const at=editView.showCut?cue.start_ms:cue.output_start_ms;
     const sub=document.createElement('div');sub.className='edit-sub';sub.textContent=translations[cue.id];sub.title=translations[cue.id];sub.lang='en';
     sub.style.left=px(at)+'px';sub.style.width=Math.max(24,px(editView.showCut?cue.end_ms-cue.start_ms:cue.output_end_ms-cue.output_start_ms)-2)+'px';lane.append(sub);
    }
   }
   if(roles.includes('emphasis'))for(const cue of emphasisTimeline(current())){
    const clip=document.createElement('button');clip.type='button';clip.className='edit-emph';clip.textContent=cue.text;clip.title='คำเน้น · คลิกเพื่อแก้คำ';
    clip.style.left=px(cue.start_ms)+'px';clip.style.width=Math.max(24,px(cue.end_ms-cue.start_ms)-2)+'px';
    clip.onclick=e=>{e.stopPropagation();wordLine=current().kept.find(l=>l.segment_id===cue.segment_id);drawWords();};lane.append(clip);
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
  const roles=read().text_roles || [],rows=[['วิดีโอ',null,22,16],['คำพูด','speech',38,26],['คำเน้น','emphasis',64,54],['อังกฤษ','english',118,54]];
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
  const {text,from,to,said}=timedText(line);if(!text)return line?.text || '';
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
  const words=[];[...text].forEach((c,i)=>{if(!keep[i])return;const last=words.at(-1);if(last&&last.at===said[i]){last.text+=c;last.end_ms=Math.max(last.end_ms,to[i]);}else words.push({at:said[i],start_ms:said[i],end_ms:to[i],text:c});});
  // grouped on the played clock (dead air inside the line cut out), so a card runs across a cut (export.rs speech_cues)
  const rows=captionRows(words.map(w=>({...w,text:w.text.trim(),src_end:w.end_ms,start_ms:playedIn(line,w.start_ms),end_ms:playedIn(line,w.end_ms)})).filter(w=>w.text),read().text_styles?.speech || defaults().speech);
  if(!rows.length)return '';
  if(now===null)return rows.flat().map(w=>w.text).reduce(captionJoin,'');
  const row=[...rows].reverse().find(r=>r[0].at<=now);
  // Nina's stack: the white setup line stays on while the orange punch line said after it is on (export.rs hold_for_emphasis)
  const held=row&&list.length&&pickGroups(line).some(g=>{const a=g[0].start_ms,b=Math.max(g.at(-1).end_ms+EMPHASIS_TAIL_MS,a+EMPHASIS_HOLD_MS);return a>=row[0].at&&a<=row.at(-1).src_end+1500&&now<b;});
  // a card stays on until the next one when the gap is a second or less (export.rs CARD_HOLD_MS)
  const next=row&&rows[rows.indexOf(row)+1]?.[0]?.at,last=row?.at(-1).src_end,until=row&&(Number.isFinite(next)&&playedIn(line,next)-playedIn(line,last)<=1000?next:last+180);
  if(!row||(now>=until&&!held))return '';
  // the orange said under this card stays until the card goes (export.rs emphasis_cues)
  shownRow={id:line.segment_id,from:row[0].at,until:Math.max(until,held?Infinity:0)};
  return row.filter(w=>!reveal || w.at<=now).map(w=>w.text).reduce(captionJoin,'');
 }
 // The preview and timeline use final playback-time windows, including dead-air removal.
 function pickGroups(line){return emphasisGroups(line,picks(line));}
 function emphasisTimeline(story){
  const pieces=[];for(const line of story.kept){let at=line.start_ms;
   for(const [a,b] of inner(line)){pieces.push({...line,start_ms:at,end_ms:a,groups:pickGroups(line)});at=b;}
   if(line.end_ms>at)pieces.push({...line,start_ms:at,groups:pickGroups(line)});
  }
  return emphasisCueWindows(pieces);
 }
 function playbackAt(story,now){let at=0;for(const line of story.kept){if(now>=line.start_ms&&now<line.end_ms)return at+now-line.start_ms-inner(line).reduce((n,[a,b])=>n+Math.max(0,Math.min(now,b)-a),0);at+=playLength(line);}return -1;}
 let shownRow=null;
 function emphasisAt(story,said,now){const at=playbackAt(story,now);const on=emphasisTimeline(story).find(x=>x.start_ms<=at&&at<x.end_ms)?.text;if(on)return on;
  // after its own words, an orange card stays while the white card it sits under is still on (Nina 16)
  if(!said || shownRow?.id!==said.segment_id || now>=shownRow.until)return '';
  const g=pickGroups(said).filter(g=>g[0].start_ms>=shownRow.from-150&&g[0].start_ms<=now).at(-1);
  return g?g.map(x=>x.text).reduce(captionJoin,''):'';}
 const SHORT=4,BACKCHANNEL=new Set(['ใช่','อืม','อือ','เออ','อ๋อ','อ้อ','เฮ้ย','ค่ะ','ครับ','จ้ะ','จ้า','โห','ว้าว','หา','ฮะ','อ่าว']);
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
 // zoom scales the picture about the same point the window sits at, which is the export's smaller crop window;
 // a window centred on a face is moved so the face sits mid-frame, kept inside the picture like the export's
 function showCrop(at=cropAt(cropSource,video.currentTime*1000)){
  const z=at.zoom || 1,frame=video.parentElement;
  if(at.centred&&frame&&video.videoWidth){
   const fw=frame.clientWidth,fh=frame.clientHeight,s=Math.max(fw/video.videoWidth,fh/video.videoHeight),W=video.videoWidth*s,H=video.videoHeight*s;
   const px=(fw-W)/2+at.x*W,py=(fh-H)/2+at.y*H,clamp=(v,a,b)=>Math.min(b,Math.max(a,v));
   const tx=clamp(fw/2-px*z,fw-((fw-W)/2+W)*z,-((fw-W)/2)*z),ty=clamp(fh/2-py*z,fh-((fh-H)/2+H)*z,-((fh-H)/2)*z);
   video.style.objectPosition='50% 50%';video.style.transformOrigin='0 0';video.style.transform=`translate(${tx}px,${ty}px) scale(${z})`;return;
  }
  video.style.objectPosition=`${at.x*100}% ${at.y*100}%`;video.style.transformOrigin=`${at.x*100}% ${at.y*100}%`;video.style.transform=z>1?`scale(${z})`:'';
 }
 // who talks when: who is seen talking (voice[id].seen, by face and mouth movement) when faces were found, else who
 // is heard (voice[id].turns); a turn under FOLLOW_MIN does not move the camera (speaker_at in export.rs)
 const FOLLOW_MIN=1000,FOLLOW_ZOOM=1.2;
 const seenOf=source=>{const s=(read().voice || {})[source]?.seen;return Array.isArray(s)&&s.length?s:null;};
 function followTurns(source){return cameraTurns((read().voice || {})[source] || {});}
 function speakerAt(source,ms){return cameraPersonAt((read().voice || {})[source] || {},ms);}
 const following=()=>read().follow_speaker===true;
 // the window at a moment: the person's dragged window, else centred on their face, else the video's window
 function cropAt(source,ms){
  const base=cropOf(source),who=following()?speakerAt(source,ms):null;
  if(who===null)return {...base,zoom:1};
  // an entry of exactly x 0.5, y 0.5 was written by the first zoom picker (when people were voices, not faces): unset
  const raw=read().crops?.[source]?.speakers?.[String(who)],c=raw&&raw.x===0.5&&raw.y===0.5&&!Number.isFinite(raw.zoom)?null:raw,f=(v,d)=>Number.isFinite(v)?Math.min(1,Math.max(0,v)):d;
  // a person with no zoom of their own is shown 20% closer, so following shows even on a clip that fits
  const zoom=Number.isFinite(c?.zoom)?Math.min(3,Math.max(1,c.zoom)):FOLLOW_ZOOM;
  if(Number.isFinite(c?.x) || Number.isFinite(c?.y))return {x:f(c.x,base.x),y:f(c.y,base.y),zoom};
  const face=seenOf(source)&&(read().voice || {})[source]?.faces?.[who];
  if(face&&Number.isFinite(face.cx))return {x:f(face.cx,0.5),y:f(face.cy,0.5),zoom,centred:true};
  return {...base,zoom};
 }
 // the people heard in a video, by how long each talks, with whether their talk is kept from the dead-air cut
 // every voice is kept unless the person unticks one: a Nina clip keeps the listener's lines (NinaStyleBook)
 const keepOf=source=>{const k=(read().speakers_keep || {})[source];return Array.isArray(k)?k:((read().voice || {})[source]?.turns || []).map(t=>t.speaker).filter((v,i,a)=>a.indexOf(v)===i).sort((a,b)=>a-b);};
 function people(source){
  const v=(read().voice || {})[source];if(!v)return [];
  const talk=new Map();for(const t of v.turns || [])talk.set(t.speaker,(talk.get(t.speaker) || 0)+t.end_ms-t.start_ms);
  return [...talk].sort((a,b)=>b[1]-a[1]).map(([id,ms],n)=>({id,ms,n,main:id===v.main,kept:keepOf(source).includes(id)}));
 }
 // the people on screen, left to right, with how long each is seen talking
 function onScreen(source){
  const faces=(read().voice || {})[source]?.faces || [],seen=seenOf(source) || [];
  return faces.map((f,id)=>({id,face:f,ms:seen.filter(t=>t.person===id).reduce((n,t)=>n+t.end_ms-t.start_ms,0)}));
 }
 const sideName=(i,n)=>n===1?'':n===2?(i===0?' (ซ้าย)':' (ขวา)'):i===0?' (ซ้าย)':i===n-1?' (ขวา)':' (กลาง)';
 function zoomPick(source,id,label){
  const stored=read().crops?.[source]?.speakers?.[String(id)],framed=stored&&stored.x===0.5&&stored.y===0.5?null:stored;
  const zoom=document.createElement('select');zoom.title='ซูมเข้าหาคนนี้';zoom.setAttribute('aria-label',`ซูมเข้าหา${label}`);zoom.disabled=!editable() || !following();
  for(const z of [1,1.2,1.3,1.5,1.75,2]){const o=document.createElement('option');o.value=String(z);o.textContent=z===1?'ไม่ซูม':`ซูม +${Math.round((z-1)*100)}%`;zoom.append(o);}
  zoom.value=String(framed?.zoom ?? FOLLOW_ZOOM);
  zoom.onchange=()=>write(d=>{const c={...(d.crops?.[source] || {x:0.5,y:0.5})};c.speakers={...(c.speakers || {}),[String(id)]:{...(framed || {}),zoom:Number(zoom.value)}};d.crops={...(d.crops || {}),[source]:c};});
  return zoom;
 }
 function drawPeople(){
  const host=$('speakerList');if(!host)return;
  const story=current(),source=story?.source_id,list=source?people(source):[],screen=source?onScreen(source):[];
  $('speakerBox').hidden=!list.length&&!screen.length;
  host.replaceChildren(...list.map(p=>{
   const row=document.createElement('label');row.className='speaker-row';row.dataset.speaker=String(p.n%4);
   const box=document.createElement('input');box.type='checkbox';box.checked=p.kept;box.disabled=!editable();
   box.onchange=()=>{const next=new Set(keepOf(source));if(box.checked)next.add(p.id);else next.delete(p.id);
    write(d=>{d.speakers_keep={...(d.speakers_keep || {}),[source]:[...next].sort((a,b)=>a-b)};});findQuiet();};
   const dot=document.createElement('span');dot.className='speaker-dot';dot.setAttribute('aria-hidden','true');
   const name=document.createElement('span');name.textContent=`เสียงที่ ${p.n+1}${p.main?' (หลัก)':''} · พูด ${clock(p.ms)}`;
   // with faces found the camera follows faces (zoom is set per face below); otherwise per voice here
   row.append(box,dot,name,screen.length?document.createElement('span'):zoomPick(source,p.id,`เสียงที่ ${p.n+1}`));return row;
  }));
  const cams=$('faceList');
  if(cams){cams.hidden=!screen.length;cams.replaceChildren(...screen.map(p=>{
   const row=document.createElement('div');row.className='speaker-row face-row';row.dataset.speaker=String(p.id%4);
   const dot=document.createElement('span');dot.className='speaker-dot';dot.setAttribute('aria-hidden','true');
   const name=document.createElement('span');name.textContent=`คนที่ ${p.id+1}${sideName(p.id,screen.length)} · เห็นพูด ${clock(p.ms)}`;
   row.append(document.createElement('span'),dot,name,zoomPick(source,p.id,`คนที่ ${p.id+1}`));return row;}));}
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
 // a dead-air mark's menu (P'Ohm 24 Oct): watch the stretch with a little either side, or put it back / cut it again
 let airOpen=null,airClose=()=>{};
 function airMenu(mark,kept,m){
  document.querySelector('.air-menu')?.remove();airOpen=mark.dataset.key;
  const shut=()=>{airOpen=null;menu.remove();removeEventListener('pointerdown',close,true);};
  const menu=document.createElement('div');menu.className='air-menu';menu.setAttribute('role','menu');
  const box=mark.parentElement;menu.style.left=(box.offsetLeft+mark.offsetLeft)+'px';menu.style.top=(box.offsetTop+box.offsetHeight+2)+'px';
  const watch=document.createElement('button');watch.type='button';watch.dataset.icon='play';watch.textContent=`ดูช่วงนี้ (${((m.e-m.s)/1000).toFixed(1)} วิ)`;
  watch.onclick=e=>{e.stopPropagation();shut();playSpan(Math.max(0,m.from-600),m.to+600);};
  const flip=document.createElement('button');flip.type='button';flip.dataset.icon=m.kept?'x':'plus';flip.textContent=m.kept?'ตัดออก':'เอากลับมา';flip.disabled=!editable();
  flip.onclick=e=>{e.stopPropagation();shut();toggleAir(kept.source_id,m.s);};
  menu.append(watch,flip);box.parentElement.append(menu);
  // a press outside the menu and its mark closes it (the mark's own click opens it again)
  function close(e){if(!menu.contains(e.target)&&!e.target.closest?.('.edit-air'))shut();}
  removeEventListener('pointerdown',airClose,true);airClose=close;addEventListener('pointerdown',close,true);
  menu.onkeydown=e=>{if(e.key==='Escape'){shut();mark.focus();}};
 }
 // plays the video's own time from `a` to `b`, then stops (the cut stretch included)
 function playSpan(a,b){queue=[{start_ms:a,end_ms:b}];video.currentTime=a/1000;video.play().catch(()=>{});}
 function playRaw(ms,play){
  queue=[{start_ms:ms,end_ms:Math.max(ms+1,(video.duration || 0)*1000 || Infinity)}];video.currentTime=ms/1000;
  showWords(lines().find(l=>l.source_id===cropSource&&ms>=l.start_ms&&ms<l.end_ms));
  if(play)video.play().catch(()=>{});else{if(!video.paused)video.pause();step();}
 }
 // the playhead goes to `ms` inside the kept line `id`; play continues the story from there, paused shows that frame
 function seekTo(story,ms,id,play,fromIndex=null){
  const from=Number.isInteger(fromIndex)&&story.kept[fromIndex]?.segment_id===id?fromIndex:story.kept.findIndex(k=>k.segment_id===id);if(from<0)return;
  queue=story.kept.slice(from);video.currentTime=ms/1000;
  caption(story.kept[from].text);showWords(story.kept[from]);
  if(play){video.play().catch(()=>{});}else{if(!video.paused)video.pause();cancelAnimationFrame(ticking);ticking=0;}
  const head=$('editHead');if(head&&editView){const box=editView.showCut?editView.boxes.find(b=>b.kept?.segment_id===id):editView.boxes[from];
   const x=editView.showCut?ms:box?box.at+playedIn(box.kept,ms):-1;head.hidden=x<0;if(x>=0)head.style.left=(x/1000*editView.zoom)+'px';}
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
   // Forward overlaps continue into the retained passage, like native overlap normalization.
   // A genuine source rewind is an intentional replay and seeks to its own in-point.
   if(!next)video.pause();else {
    let replay=false;
    if(next.start_ms===line.start_ms&&next.segment_id){
     if(!manifestReady()){queue.unshift(line);video.pause();queueManifest();return;}
     const story=current(),index=story.kept.length-queue.length;
     const occurrence=story.kept.slice(0,index+1).filter(l=>l.segment_id===next.segment_id&&l.source_id===next.source_id).length;
     replay=englishManifest.cues.some(c=>c.story_id===story.id&&c.segment_id===next.segment_id&&c.occurrence===occurrence);
    }
    // a gap of 250 ms or less to the next line is played, as the export does (export.rs JOIN_GAP_MS)
    if(next.start_ms>now+250||next.start_ms<line.start_ms||replay){video.currentTime=next.start_ms/1000;return;}
   }
  }
  const story=current(),activeIndex=story&&queue[0]?.segment_id?story.kept.length-queue.length:-1;
  const queued=activeIndex>=0&&story.kept[activeIndex]?.segment_id===queue[0]?.segment_id?story.kept[activeIndex]:null;
  const said=story&&(!editView?.showCut&&queued&&now>=queued.start_ms&&now<queued.end_ms?queued:(editView?.showCut?lines().filter(l=>l.source_id===story.source_id):story.kept).find(l=>now>=l.start_ms&&now<l.end_ms));
  if(following()&&cropSource&&!document.querySelector('[data-cropping=true]'))showCrop();
  // the dead air cut out of the middle of a line is jumped over, as the export leaves it out
  // (not while a cut stretch is watched on purpose: playSpan queues a span with no line)
  if(said&&queue[0]?.segment_id&&!video.paused&&!editView?.showCut){const gap=inner(said).find(([a,b])=>now>=a&&now<b-20);if(gap){video.currentTime=gap[1]/1000;return;}}
  const words=said?.words || [],wordByWord=read().text_styles?.speech?.enter==='words'&&words.length;
  // while a line plays, an empty box stays empty (a no-break space) instead of falling back to the sample text
  const shown=l=>(l.words?.length?speechText(l,now,wordByWord&&l===said):l.text) || '';
  // Each transcript cue keeps its own beat; never preview words from the next speaker/cue.
  shownRow=null;caption(!said?'\u00a0':shown(said) || '\u00a0');
  const held=story?emphasisAt(story,said,now):'';
  emphasis(held || '\u00a0');
  const occurrence=queued&&said===queued&&!editView?.showCut?{segment_id:queued.segment_id,occurrence:story.kept.slice(0,activeIndex+1).filter(l=>l.segment_id===queued.segment_id&&l.source_id===queued.source_id).length}:null;
  const translated=read().english_cues?(manifestReady()?englishCueAt(englishManifest,read().english_cues.translations,story?.id,said?.source_id || story?.source_id,now,occurrence):''):
   said?(read().subtitles || {})[said.segment_id] || '':'';
  subtitle(translated || '\u00a0');
  if(said&&said.segment_id!==wordLine?.segment_id&&!video.paused)showWords(said);
  const head=$('editHead');
  if(head&&editView){
   let x=now;
   if(!editView.showCut){const box=queued?editView.boxes[activeIndex]:editView.boxes.find(b=>now>=b.kept.start_ms&&now<b.kept.end_ms);x=box?box.at+playedIn(box.kept,now):-1;}
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
 function keptLines(){const m=model();if(!m)return [];const seen=new Set();return m.stories.flatMap(s=>s.kept.map(l=>({story:s,line:captionKeptLine(l)}))).filter(({line})=>!seen.has(line.segment_id)&&seen.add(line.segment_id));}
 function captionRevision(){return captionInputRevision(project,read(),keptLines().map(({line})=>line));}
 // only another project's reply is refused: every pick is checked by its words against the line as it is now
 // (validateEmphasisSelections), so re-timed words (24 Oct: fitted to speech) do not make a good reply stale
 function checkReply(value){return value?.project_id!==project?'คำตอบเป็นร่างเก่า ดาวน์โหลดไฟล์ .md ใหม่แล้วให้ AI ตอบอีกครั้ง':'';}
 function emphasisText(part=false){
  const sent=new Map(keptLines().map(({line})=>[line.segment_id,line]));
  const packet={schema_version:3,step:'emphasis',project_id:project,input_revision:captionRevision(),speakers_keep:read().speakers_keep ?? null,
   stories:(model()?.stories || []).filter(s=>s.kept.length).map(s=>({title:s.title,lines:s.kept.map(k=>{const l=sent.get(k.segment_id);return {id:l.segment_id,text:l.text,start_ms:l.start_ms,end_ms:l.end_ms,source_id:l.source_id,speakers:(read().voice?.[l.source_id]?.turns || []).filter(t=>t.start_ms<l.end_ms&&t.end_ms>l.start_ms),words:(l.words || []).map((w,i)=>({id:'w'+i,...w,has_duration:w.end_ms>w.start_ms}))};})})),
   response_schema:{project_id:project,input_revision:captionRevision(),emphasis:{'<line id>':[{word_ids:['w0','w1'],text:'ข้อความตามรหัสคำ'}]}}};
  // Nina 16-19 (591 CapCut cards): the orange line is the second half of a sentence, the punch, under a white
  // setup line; about 4 in 10 sentences have one, a median of 16 letters, at most about 24
  return ['เลือก "บรรทัดเน้น" (สีส้ม) สำหรับซับคลิปสั้นแบบคลิป Nina: ประโยคถูกแบ่งเป็น 2 บรรทัด บรรทัดบนสีขาวปูเรื่อง แล้วบรรทัดล่างสีส้มตัวใหญ่ต่อยท้าย ขึ้นตอนคนพูดถึงช่วงนั้น',
   'ตัวอย่างจากคลิปจริง: "เพราะว่าเขาก็ไม่รู้สึกว่า" + ส้ม "ถูกยัดเยียดไง" · "มันเหมือนเรา…" + ส้ม "ไม่ได้ไปสอนเขา" · "เราจะแคร์ว่าคนอื่นคิดยังไง" + ส้ม "ลูกค้ามาเห็นจะคิดยังไง"',
   'กติกา:',
   '1. เลือกท่อนที่มีใจความเด่นหรือจุดหักมุม ประมาณ 4 ใน 10 ของท่อนทั้งหมด ท่อนอื่นไม่ต้องใส่ในคำตอบ',
   '2. ท่อนละ 1 ช่วงเท่านั้น เป็นช่วงท้ายของประโยค (ครึ่งหลัง) ที่เป็นใจความหรือคำตอบ ยาวประมาณ 8-24 ตัวอักษร ไม่ใช่คำเดี่ยว และต้องเหลือข้อความด้านหน้าไว้ปูเรื่อง',
   '3. ช่วงที่เลือกต้องอ่านจบในตัว: ชื่อเฉพาะเอาทั้งชื่อ ตัวเลขพร้อมหน่วย คำอังกฤษที่พูดเป็นอังกฤษไม่ตัดครึ่ง',
   '4. ห้ามเริ่มหรือจบด้วยคำเติมหรือคำเชื่อม เช่น แบบ คือ อ่ะ อืม เออ ที่ ว่า แล้ว ก็ มัน นี้ เนี้ย ใช่ ปะ (ข้อความด้านหน้าที่จบด้วยคำเชื่อม เช่น "ว่า" "คือ" เป็นบรรทัดขาวที่ดี)',
   '5. เลือกด้วย word_ids ที่ติดกันจาก words เท่านั้น ให้รวมรหัสจนได้คำหรือชื่อเต็ม ห้ามแต่งคำ แปล หรือเดาเวลา ถ้าคำถอดผิดให้ข้ามไป',
   'คำที่ has_duration=false รวมได้เฉพาะด้านในระหว่างคำที่มีเวลาจริง ถ้าอยู่ต้นหรือท้ายวลีให้ข้าม ห้ามเดาเวลา',
   '6. คืน project_id และ input_revision ตามที่ให้ ห้ามแก้รหัส ถ้าไม่มีคำเหมาะสมคืน emphasis เป็น {}',
   ...(part?[]:[MD_REPLY]),''].join('\n')+'\n'+JSON.stringify(packet,null,1);
 }
 function pasted(){
  const raw=$('emphasisAnswer').value.trim();if(!raw)return {error:''};
  const {value,json}=readJson(raw);if(json===undefined)return {error:'ในไฟล์ไม่มีคำตอบ JSON ที่ครบ ให้ AI ส่งไฟล์ .md ใหม่'};
  if(value?.response_schema)return {error:'นี่คือไฟล์ที่ส่งให้ AI ยังไม่ใช่คำตอบ อัปโหลดไฟล์ .md ที่ AI ส่งกลับมา'};
  return {value};
 }
 function checkEmphasis(){const p=pasted();return p.value?emphasisFrom(p.value):p;}
 function emphasisFrom(value){
  const stale=checkReply(value);if(stale)return {error:stale};
  const answer=value.emphasis;
  if(!answer || typeof answer!=='object' || Array.isArray(answer))return {error:'ไม่พบ emphasis ในคำตอบ'};
  return validateEmphasisSelections(answer,keptLines().map(({line})=>line));
 }
 // step 4 makes two layers with a chat AI: emphasis words, and subtitles translated to English. An empty layer
 // shows its "create" button in the middle of its row; pressing it opens the send-and-paste box there
 function subtitleText(part=false){
  if(!manifestReady())throw Error(manifestError || 'รอบันทึกงานและคำนวณช่วงซับให้เสร็จก่อน');
  const packet={schema_version:4,step:'subtitles',project_id:project,input_revision:englishManifest.input_revision,language:'en',
   cues:englishManifest.cues.map(({id,story_id,text,output_start_ms,output_end_ms,timing})=>({id,story_id,text,start_ms:output_start_ms,end_ms:output_end_ms,timing})),
   response_schema:{project_id:project,input_revision:englishManifest.input_revision,subtitles:{'<cue id>':'English subtitle'}}};
  return ['แปลซับคลิปสั้นเป็นภาษาอังกฤษตามช่วงซับที่ตัดแล้ว',
   'กติกา:',
   '1. แปลทุก cue ที่ส่งไป ช่วงละ 1 บรรทัดสั้นๆ อ่านทันในเวลาที่ให้ ไม่เกิน 60 ตัวอักษร',
   '2. แปลตามความหมาย เป็นภาษาพูดที่เป็นธรรมชาติ ไม่ต้องแปลคำเติม เช่น อ่ะ อืม เออ',
   '3. ชื่อเฉพาะและชื่อแบรนด์เขียนแบบที่คนพูดอังกฤษใช้',
   '4. อ้างแต่ละช่วงด้วย id ที่ให้ คืน project_id และ input_revision ให้ตรง ห้ามรวม เพิ่ม หรือข้ามช่วง ห้ามเดาหรือเปลี่ยนเวลา',
   ...(part?[]:[MD_REPLY]),''].join('\n')+'\n'+JSON.stringify(packet,null,1);
 }
 function checkSubtitles(){const p=pasted();return p.value?englishFrom(p.value):p;}
 function englishFrom(value){
  if(!manifestReady())return {error:manifestError || 'รอบันทึกงานและคำนวณช่วงซับก่อนใช้คำตอบ'};
  return validateEnglishCueReply(value,project,englishManifest);
 }
 const rounds={
  emphasis:{make:'สร้างคำเน้น',title:'วางคำตอบคำเน้นจาก AI',text:emphasisText,check:checkEmphasis,file:'editorkrab-คำเน้น.md',
   has:()=>Object.keys(read().emphasis || {}).length>0,
   // the answer covers every line that was sent: lines it left out lose their old emphasis (the AI now picks
   // only the stand-out lines); lines that were not sent keep theirs
   apply:c=>{const sent=new Set(keptLines().map(({line})=>line.segment_id));
    for(const issue of c.issues || [])sent.delete(issue.id);
    write(d=>{const all=Object.fromEntries(Object.entries(d.emphasis || {}).filter(([id])=>!sent.has(id)));d.emphasis={...all,...c.picked};if(d.caption_reply_drafts)delete d.caption_reply_drafts.emphasis;
    if(!(d.text_roles || []).includes('emphasis'))d.text_roles=[...(d.text_roles || []),'emphasis'];});}},
  english:{make:'สร้างซับอังกฤษ',title:'วางคำตอบซับอังกฤษจาก AI',text:subtitleText,check:checkSubtitles,file:'editorkrab-ซับอังกฤษ.md',
   has:()=>!!read().english_cues||Object.keys(read().subtitles || {}).length>0,
   apply:c=>write(d=>{d.english_cues={input_revision:englishManifest.input_revision,translations:c.picked};d.subtitle_language='en';
    if(!(d.text_roles || []).includes('english'))d.text_roles=[...(d.text_roles || []),'english'];})},
  // one trip to the chat for both layers (P'Ohm 24 Oct: too many copy-paste rounds)
  both:{make:'ให้ AI ทำคำเน้นและซับอังกฤษ',title:'วางคำตอบจาก AI (คำเน้นและซับอังกฤษ)',file:'editorkrab-คำเน้นและอังกฤษ.md',
   has:()=>rounds.emphasis.has()||rounds.english.has(),
   text:()=>{
    return ['ทำ 2 งานแล้วตอบในคำตอบเดียว',
     'ส่งคำตอบกลับเป็นไฟล์ .md หนึ่งไฟล์ ข้างในเป็น JSON ก้อนเดียวอย่างเดียว ไม่ต้องพิมพ์คำตอบหรือคำอธิบายในแชท: {"emphasis_reply": คำตอบงานที่ 1 ตาม response_schema ของงานที่ 1, "english_reply": คำตอบงานที่ 2 ตาม response_schema ของงานที่ 2}',
     '','=== งานที่ 1: คำเน้น ===',emphasisText(true),'','=== งานที่ 2: ซับอังกฤษ ===',subtitleText(true)].join('\n');},
   check:()=>{const p=pasted();if(!p.value)return p;
    if(!p.value.emphasis_reply||!p.value.english_reply)return {error:'คำตอบต้องมีทั้ง emphasis_reply และ english_reply ให้ AI ส่งไฟล์ .md ใหม่'};
    const e=emphasisFrom(p.value.emphasis_reply),s=englishFrom(p.value.english_reply);
    if(!e.picked)return {error:'คำเน้น: '+e.error};if(!s.picked)return {error:'ซับอังกฤษ: '+s.error};
    return {...e,note:e.note+' · '+s.note,parts:{e,s}};},
   apply:c=>{rounds.emphasis.apply(c.parts.e);rounds.english.apply(c.parts.s);}}
 };
 let round='emphasis';const opened={emphasis:false,english:false,both:false};
 function say(text,ok){$('emphasisStatus').textContent=text;$('emphasisStatus').dataset.state=ok?'ok':'error';$('emphasisStatus').hidden=false;}
 function action(label,icon,run,needsEdit){const b=document.createElement('button');b.type='button';b.textContent=label;b.dataset.icon=icon;b.onclick=run;if(needsEdit)b.disabled=!editable();return b;}
 // English needs the subtitle cues: a press while they are not ready waits for them instead of doing nothing
 async function cuesReady(){
  for(let tries=0;tries<240&&!manifestReady();tries++){
   if(manifestError)return false;
   if(captionState().saved&&!analysisPending()&&!manifestBusy)await loadManifest();
   // a busy answer (409) returns at once: wait before asking again
   if(!manifestReady())await new Promise(done=>setTimeout(done,500));
  }
  return !!manifestReady();
 }
 async function downloadRound(key){
  if(key!=='emphasis'&&!manifestReady()){say(analysisPending()?'รอฟังเสียงในคลิปเสร็จ แล้วจะดาวน์โหลดให้เอง…':'กำลังเตรียมช่วงซับ แล้วจะดาวน์โหลดให้เอง…',true);
   if(!await cuesReady()){say(manifestError || 'ช่วงซับยังไม่พร้อม ลองกดอีกครั้ง',false);return;}
   $('emphasisStatus').hidden=true;}
  try{saveMd(rounds[key].file,rounds[key].text());}catch(error){say(error.message,false);loadManifest();}}
 // the AI's answer file is checked and applied as a pasted answer was; its text is not shown, only the result
 async function uploadRound(key){
  let text;try{text=await pickMd();}catch(error){say(error.message,false);return;}
  if(text===null)return;
  if(key!=='emphasis'&&!manifestReady()){say('กำลังเตรียมช่วงซับก่อนอ่านคำตอบ…',true);
   if(!await cuesReady()){say(manifestError || 'ช่วงซับยังไม่พร้อม ลองอัปโหลดอีกครั้ง',false);return;}}
  round=key;$('emphasisAnswer').value=text;
  const c=rounds[key].check();
  if(!c.picked){say('ไฟล์คำตอบใช้ไม่ได้: '+(c.error || 'ไม่พบคำตอบในไฟล์'),false);return;}
  opened[key]=false;for(const k of key==='both'?['both','emphasis','english']:[key])sessionStorage.removeItem('caption-reply:'+project+':'+k);
  rounds[key].apply(c);say(c.note,true);drawLayerActions();
 }
 function drawLayerActions(){
  const host=$('layerActions');if(!host)return;
  const inStep4=!!$('editTrack').closest('#captionTransport')&&!!model();
  // nothing made yet: one button does both; afterwards each layer can be made on its own
  const shown=rounds.both.has()||opened.emphasis||opened.english?['emphasis','english']:['both'];
  host.replaceChildren(...shown.map(key=>[key,rounds[key]]).map(([key,r])=>{
   const slot=document.createElement('div');slot.className='layer-action';slot.dataset.role=key;
   slot.hidden=!inStep4 || (r.has()&&!opened[key]);
   if(!opened[key]){const make=action(r.make,'plus',()=>{opened[key]=true;drawLayerActions();},true);make.classList.add('primary');slot.append(make);}
   else{const label=document.createElement('span');label.textContent='ส่งข้อความให้ AI';
    // P'Ohm 26 Oct: no copy and paste; the request goes out as a .md file and the AI sends a .md file back.
    slot.append(label,action('ดาวน์โหลดไฟล์ .md','download',()=>downloadRound(key)),action('อัปโหลดคำตอบ .md','upload',()=>uploadRound(key),true),action('ปิด','x',()=>{opened[key]=false;drawLayerActions();}));}
   return slot;}));
  // a layer already made can be made again from the play row
  const redo=$('captionRedo');
  if(redo){redo.replaceChildren(...Object.entries(rounds).filter(([key,r])=>key!=='both'&&inStep4&&r.has()&&!opened[key])
   .map(([key,r])=>action(r.make+'ใหม่','retry',()=>{opened[key]=true;drawLayerActions();},true)));
   if(inStep4&&(read().text_roles || []).includes('english')){
    const badge=document.createElement('span');badge.className='badge';badge.setAttribute('role','status');
    const missing=(englishManifest?.cues || []).filter(c=>!read().english_cues?.translations?.[c.id]).length;
    badge.textContent=manifestError?'อ่านช่วงซับไม่ได้':!read().english_cues&&Object.keys(read().subtitles || {}).length?'อังกฤษเดิม · ท่อนยาว':!manifestReady()?'รอช่วงซับ':missing?`อังกฤษขาด ${missing} ช่วง`:'อังกฤษตามช่วงซับ';
    badge.title=manifestError || (!read().english_cues?'คำแปลเดิมยังเก็บไว้ ใช้ทั้งท่อนโดยไม่เดาเวลาแบ่งคำ':'ใช้ช่วงเวลาเดียวกับโปรเจกต์ CapCut');redo.append(badge);
   }
  }
 }
 function checkEmphasisUi(){const c=rounds[round].check();
  const preview=$('emphasisPreview');preview.replaceChildren();preview.hidden=!c.picked;
  if(c.picked){for(const [id,items] of Object.entries(c.picked)){const row=document.createElement('p');row.className='caption';row.textContent=round==='english'?items:items.map(p=>p.text).join(' · ');preview.append(row);}
   // the combined round also shows the English lines, so the person sees what both halves of the answer will do
   if(round==='both')for(const text of Object.values(c.parts.s.picked)){const row=document.createElement('p');row.className='caption';row.textContent='EN · '+text;preview.append(row);}
   for(const issue of c.issues || []){const row=document.createElement('p');row.className='field-warning';row.textContent=`“${issue.text}” · ${issue.reason} · เก็บคำเดิมของท่อนนี้`;preview.append(row);}}
  $('emphasisApply').textContent=c.issues?.length?`ใช้ ${c.count} คำที่พร้อม`:'ใช้คำตอบนี้';$('emphasisCheck').textContent=c.picked?c.note:c.error;$('emphasisCheck').dataset.state=c.picked?'ok':'error';$('emphasisCheck').hidden=!$('emphasisCheck').textContent;$('emphasisApply').disabled=!c.picked || !editable();}
 $('emphasisAnswer').oninput=()=>{try{sessionStorage.setItem('caption-reply:'+project+':'+round,$('emphasisAnswer').value);}catch(_){}checkEmphasisUi();};
 $('emphasisApply').onclick=event=>{
  const c=rounds[round].check();if(!c.picked){event.preventDefault();return;}
  opened[round]=false;for(const k of round==='both'?['both','emphasis','english']:[round])sessionStorage.removeItem('caption-reply:'+project+':'+k);rounds[round].apply(c);say(c.note,true);drawLayerActions();
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
 return {renderCards,renderEditor,allReviewed,warm,stop,summary,qc,analysisPending};
}
