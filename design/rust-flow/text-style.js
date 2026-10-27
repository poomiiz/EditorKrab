// Style samples live in a separate draft field; they never replace dialogue or the AI plan.
export const roles=['headline','speech','emphasis','english'];
const fonts={system:'"Leelawadee UI",Tahoma,system-ui,sans-serif',tahoma:'Tahoma,"Leelawadee UI",sans-serif',thai:'"Leelawadee UI",Tahoma,sans-serif'};
// a font hovered in the font list, tried on the preview text being edited
let hoverFont=null;
const fontLabels={system:'ฟอนต์ระบบ',tahoma:'Tahoma',thai:'Leelawadee UI'};
export function validFont(value){return typeof value==='string' && (Object.hasOwn(fonts,value) || (value.startsWith('family:') && value.slice(7).length>0 && value.slice(7).length<=128 && value.slice(7).trim()===value.slice(7) && !/[\p{Cc}"'\\,;{}<>]/u.test(value.slice(7))));}
function fontValue(name){return Object.keys(fontLabels).find(key=>fontLabels[key]===name) || 'family:'+name;}
function fontName(value){return fontLabels[value] ?? value.slice(7);}
function fontCss(value){return fonts[value] ?? JSON.stringify(value.slice(7))+','+fonts.system;}

let measureContext;
export function captionMeasure(text,style={}){
 if(!measureContext&&typeof document!=='undefined')measureContext=document.createElement('canvas').getContext('2d');
 const size=Number(style.size)||64,shown=style.case==='upper'?text.toUpperCase():style.case==='lower'?text.toLowerCase():text;
 if(measureContext)measureContext.font=(style.italic?'italic ':'')+(style.bold===false?400:700)+' '+size+'px '+fontCss(style.font || 'system');
 const plain=[...shown].filter(c=>!/[\u0E31\u0E34-\u0E3A\u0E47-\u0E4E]/.test(c)).length;
 return (measureContext?measureContext.measureText(shown).width:plain*size)+(Number(style.spacing)||0)*Math.max(0,plain-1);
}
export function captionWidth(style={}){
 const x=(Number.isFinite(style.x)?style.x:50)*10.8,edge=54+(style.outline ?? 4)+(style.box_on?(style.box_pad_x ?? 12):0);
 return Math.max(24,style.align==='left'?1080-edge-x:style.align==='right'?x-edge:2*Math.min(x-edge,1080-edge-x));
}
export const captionJoin=(a,b)=>a+(a&&!/[\u0E00-\u0E7F\s]$/.test(a)&&b&&!/^[\u0E00-\u0E7F]/.test(b)?' ':'')+b;
// maxMs: longest speech one card holds (export.rs CARD_MAX_MS; Nina 16 white cards median 1.9 s, p75 3 s)
// a breath under 650 ms stays inside a card; a card too long or too wide ends at its widest pause (120 ms+),
// not before the word that did not fit; a short tail (5 letters or fewer) joins the card before
// (export.rs card_groups, from Nina 16's timeline transcribed 24 Oct)
export function captionRows(words,style={},maxMs=style.card_ms ?? 3000,measure=captionMeasure){
 const fits=g=>measure(g.map(x=>x.text).reduce(captionJoin,''),style)<=captionWidth(style)*.96;
 const rows=[];let cur=[];
 for(const w of words){
  if(cur.length){
   const last=cur.at(-1),pause=w.start_ms-last.end_ms>=650,over=w.end_ms-cur[0].start_ms>maxMs || !fits([...cur,w]);
   if(pause){rows.push(cur);cur=[];}
   else if(over){
    let k=-1,gap=119;for(let i=1;i<cur.length;i++){const g=cur[i].start_ms-cur[i-1].end_ms;if(g>gap || (g===gap&&k>=0)){gap=g;k=i;}}
    const rest=k>0?[...cur.slice(k),w]:null;
    if(rest&&fits(rest)&&w.end_ms-rest[0].start_ms<=maxMs){rows.push(cur.slice(0,k));cur=cur.slice(k);}else{rows.push(cur);cur=[];}
   }
  }
  cur.push(w);
 }
 if(cur.length)rows.push(cur);
 // a card said in no time (its words in cut dead air) joins the card after it (export.rs FLASH_MS)
 for(let k=0;k+1<rows.length;){if(rows[k].at(-1).end_ms-rows[k][0].start_ms<120&&fits([...rows[k],...rows[k+1]]))rows.splice(k,2,[...rows[k],...rows[k+1]]);else k++;}
 if(rows.length>1){const tail=rows.at(-1),prev=rows.at(-2),letters=tail.reduce((n,x)=>n+[...x.text].length,0);
  if(letters<=5&&tail[0].start_ms-prev.at(-1).end_ms<650&&fits([...prev,...tail])){rows.pop();rows[rows.length-1]=[...prev,...tail];}}
 return rows;
}
export function fittedCaptionSize(text,style={},aspect=16/9){
 const size=Number(style.size)||64,width=captionMeasure(text,style);
 const y=(Number.isFinite(style.y)?style.y:50)/100,pad=(style.outline ?? 4)+(style.box_on?(style.box_pad_y ?? 12):0);
 const vertical=Math.max(1,1080*aspect*Math.min(y,1-y)-16-pad)*2/1.5;
 return Math.max(1,Math.floor(Math.min(vertical,size*Math.min(1,captionWidth(style)*.96/Math.max(1,width)))));
}

const labels={headline:'หัวคลิป',speech:'คำพูด',emphasis:'คำสำคัญ',english:'คำแปลอังกฤษ'};
export function defaults(){
 // Clip text colours are video output, not UI theme tokens.
 // orange, size and place of Nina's punch line (white 21 at y -0.5, orange 24 at y -0.615 in CapCut)
 const white='#ececf1',orange='#ff8000';
 return {headline:{text:'หัวข้อคลิป',font:'system',size:80,color:white,x:50,y:16},speech:{text:'ตัวอย่างข้อความพูด',font:'family:DB Heavent',size:125,color:white,x:50,y:75,outline:4,shadow:1},
  emphasis:{text:'คำเน้นสำคัญ',font:'family:DB Heavent',size:143,color:orange,x:50,y:81,outline:4,shadow:1},english:{text:'English subtitle',font:'system',size:47,color:white,x:50,y:90,outline:2}};
}
// optional text effects; absent fields mean the export defaults: every effect off (no stroke, shadow, box or glow)
export const effectDefaults={outline:0,outline_color:'#000000',shadow:0,shadow_blur:0,shadow_color:'#000000',shadow_opacity:60,shadow_angle:45,box_on:false,box_color:'#000000',box_opacity:100,enter:'none',blend:'normal',
 bold:true,italic:false,underline:false,case:'none',spacing:0,align:'center',glow_on:false,glow_color:'#ffffff',glow_strength:60,glow_range:20};
const effectValid={outline:v=>Number.isInteger(v)&&v>=0&&v<=20,shadow:v=>Number.isInteger(v)&&v>=0&&v<=20,outline_color:v=>/^#[a-fA-F0-9]{6}$/.test(v),
 box_color:v=>/^#[a-fA-F0-9]{6}$/.test(v),box_on:v=>typeof v==='boolean',box_pad_x:v=>Number.isInteger(v)&&v>=0&&v<=120,box_pad_y:v=>Number.isInteger(v)&&v>=0&&v<=120,enter:v=>['none','fade','pop','wipe','words'].includes(v),
 shadow_blur:v=>Number.isInteger(v)&&v>=0&&v<=20,shadow_opacity:v=>Number.isInteger(v)&&v>=0&&v<=100,shadow_color:v=>/^#[a-fA-F0-9]{6}$/.test(v),blend:v=>['normal','add','screen','multiply'].includes(v),
 bold:v=>typeof v==='boolean',italic:v=>typeof v==='boolean',underline:v=>typeof v==='boolean',glow_on:v=>typeof v==='boolean',
 case:v=>['none','upper','lower','title'].includes(v),align:v=>['left','center','right'].includes(v),spacing:v=>Number.isInteger(v)&&v>=-20&&v<=100,
 glow_color:v=>/^#[a-fA-F0-9]{6}$/.test(v),glow_strength:v=>Number.isInteger(v)&&v>=0&&v<=100,glow_range:v=>Number.isInteger(v)&&v>=0&&v<=60,
 shadow_angle:v=>Number.isInteger(v)&&v>=-180&&v<=180,box_opacity:v=>Number.isInteger(v)&&v>=0&&v<=100,
 card_ms:v=>Number.isInteger(v)&&v>=1000&&v<=8000};
// every role, speech included, shows its whole line at once unless an entrance is picked (P'Ohm 23 Oct)
export const roleEffects=()=>effectDefaults;
// one press sets speech, emphasis and English together (P'Ohm 24 Oct: step 4 too hard); positions stay
const looks={
 nina:{speech:{font:'family:DB Heavent',size:125,color:'#ececf1',outline:4,shadow:1,box_on:false},emphasis:{font:'family:DB Heavent',size:143,color:'#ff8000',outline:4,shadow:1,box_on:false},english:{font:'system',size:47,color:'#ececf1',outline:2,shadow:0,box_on:false}},
 plain:{speech:{font:'system',size:110,color:'#ececf1',outline:0,shadow:4,box_on:false},emphasis:{font:'system',size:120,color:'#ffd400',outline:0,shadow:4,box_on:false},english:{font:'system',size:44,color:'#ececf1',outline:0,shadow:3,box_on:false}},
 box:{speech:{font:'system',size:105,color:'#ececf1',outline:0,shadow:0,box_on:true,box_color:'#000000',box_opacity:70},emphasis:{font:'system',size:115,color:'#ffd400',outline:0,shadow:0,box_on:true,box_color:'#000000',box_opacity:70},english:{font:'system',size:42,color:'#ececf1',outline:0,shadow:0,box_on:true,box_color:'#000000',box_opacity:70}}
};
const baseKeys=['text','font','size','color','x','y'];
export function validTextStyle(v){
 return v && typeof v==='object' && !Array.isArray(v) && Object.keys(v).every(k=>baseKeys.includes(k) || (Object.hasOwn(effectValid,k) && effectValid[k](v[k]))) && baseKeys.every(k=>Object.hasOwn(v,k)) && typeof v.text==='string' && v.text.length<=80 && !/[\r\n]/.test(v.text) && validFont(v.font) &&
 Number.isInteger(v.size) && v.size>=24 && v.size<=160 && typeof v.color==='string' && /^#[a-fA-F0-9]{6}$/.test(v.color) && Number.isFinite(v.x) && v.x>=5 && v.x<=95 && Number.isFinite(v.y) && v.y>=5 && v.y<=95;
}
export function validTextStyles(value){
 return value===undefined || (value && typeof value==='object' && !Array.isArray(value) && Object.keys(value).length===roles.length && roles.every(key=>validTextStyle(value[key])));
}
export function validHeadlineItems(value){
 if(value===undefined)return true;
 return Array.isArray(value) && value.length<=100 && new Set(value.map(v=>v?.id)).size===value.length && value.every(v=>{
  if(!v || typeof v.id!=='string' || !/^[A-Za-z0-9_-]{1,128}$/.test(v.id))return false;
  const {id,...style}=v;return validTextStyle(style);
 });
}
export function createStyleEditor(read,write,layout,writeItems){
 const $=id=>document.getElementById(id),frame=$('textFrame');
 let selected='speech',enabled=false,drag=null,preview=null;
 // on step 3 the speech box shows the line being said in the video under it
 const live=new Map();
 const fallback=defaults();
 const stored=()=>({...(read() || fallback),...Object.fromEntries(layout().items.map(v=>['hl:'+v.id,v]))});
 const values=()=>preview || stored();
 const label=key=>key.startsWith('hl:')?'ข้อความหัวคลิป '+(layout().items.findIndex(v=>'hl:'+v.id===key)+1):labels[key];
 function commit(next){
  if(selected?.startsWith('hl:'))writeItems(layout().items.map(v=>next['hl:'+v.id]));
  else write(Object.fromEntries(roles.map(key=>[key,next[key]])));
 }
 const fieldNames=['text','font','size','color'];
 const controls=Object.fromEntries(fieldNames.map(key=>[key,$('text-'+key)]));
 const nodes=new Map();
 function createNode(key){
  const button=document.createElement('button');button.type='button';button.className='preview-text';button.dataset.textRole=key.startsWith('hl:')?'headline':key;if(key.startsWith('hl:'))button.dataset.textId=key.slice(3);
  button.setAttribute('aria-label',`ปรับ${label(key)}`);button.setAttribute('aria-describedby','textDragHint');
  if(key==='english')button.lang='en';
  const span=document.createElement('span');button.append(span);frame.append(button);nodes.set(key,button);
  button.onclick=()=>{if(button.dataset.passed){delete button.dataset.passed;return;}select(key);};
  button.onpointerdown=event=>{
   if(!enabled || drag || event.button!==0 || !event.isPrimary)return;
   const was=key===selected;select(key);button.focus({preventScroll:true});
   const value=values()[key];
   drag={was,key,pointer:event.pointerId,clientX:event.clientX,clientY:event.clientY,x:value.x,y:value.y,moved:false};
   button.setPointerCapture(event.pointerId);event.preventDefault();
  };
  button.onpointermove=event=>{
   if(!drag || drag.pointer!==event.pointerId || !enabled)return;
   const dx=event.clientX-drag.clientX,dy=event.clientY-drag.clientY;
   if(!drag.moved && Math.hypot(dx,dy)<4)return;
   drag.moved=true;preview=structuredClone(stored());
   Object.assign(preview[key],position(key,drag.x+dx/frame.clientWidth*100,drag.y+dy/frame.clientHeight*100,true));
   button.dataset.dragging='true';paint();
  };
  button.onpointerup=event=>{
   if(!drag || drag.pointer!==event.pointerId)return;
   const next=preview,changed=drag.moved && next && (next[key].x!==drag.x || next[key].y!==drag.y),moved=drag.moved,was=drag.was;
   endDrag();if(changed && enabled)commit(next);
   // a click (no drag) on the text already selected picks the one lying under it, so overlapping texts stay reachable
   if(!moved&&was){const under=document.elementsFromPoint(event.clientX,event.clientY).find(n=>n!==button&&n.classList?.contains('preview-text')&&!n.hidden);
    const other=under&&[...nodes].find(([,n])=>n===under)?.[0];if(other){button.dataset.passed='1';select(other);return;}}
   render();
  };
  button.onpointercancel=button.onlostpointercapture=event=>{if(drag?.key===key && drag.pointer===event.pointerId){endDrag();render();}};
  button.onkeydown=event=>{
   if(event.key==='Escape' && drag){event.preventDefault();endDrag();render();return;}
   if(!enabled || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
   event.preventDefault();endDrag();select(key);
   const next=structuredClone(values()),v=next[key],step=event.shiftKey?5:1;
   const placed=position(key,v.x+(event.key==='ArrowRight'?step:event.key==='ArrowLeft'?-step:0),v.y+(event.key==='ArrowDown'?step:event.key==='ArrowUp'?-step:0));
   if(placed.x===v.x && placed.y===v.y)return;
   Object.assign(v,placed);commit(next);render();
  };
 }
 for(const key of roles.filter(v=>v!=='headline'))createNode(key);
 function select(key){if(drag && drag.key!==key)endDrag();selected=key;render();}
 function endDrag(){
  if(!drag)return;
  const node=drag.handle || nodes.get(drag.key),pointer=drag.pointer;drag=null;preview=null;if(node)node.dataset.dragging='false';$('alignX').hidden=true;$('alignY').hidden=true;
  if(node?.hasPointerCapture(pointer))node.releasePointerCapture(pointer);
 }
 function position(key,x,y,snap=false){
  if(snap){if(Math.abs(x-50)*frame.clientWidth/100<=7)x=50;if(Math.abs(y-50)*frame.clientHeight/100<=7)y=50;}
  const node=nodes.get(key),halfX=Math.min(50,Math.max(5,node.offsetWidth/frame.clientWidth*50)),halfY=Math.min(50,Math.max(5,node.offsetHeight/frame.clientHeight*50));
  return {x:Math.round(Math.max(halfX,Math.min(100-halfX,x))*10)/10,y:Math.round(Math.max(halfY,Math.min(100-halfY,y))*10)/10};
 }

 function inspectBounds(){
  if(!frame.clientWidth)return;
  const bounds=frame.getBoundingClientRect();
  const overflow=[];
  for(const [key,node] of nodes){
   if(node.hidden)continue;
   const text=node.firstElementChild.getBoundingClientRect();
   const bad=text.left<bounds.left || text.right>bounds.right || text.top<bounds.top || text.bottom>bounds.bottom;
   node.dataset.overflow=String(bad);if(bad)overflow.push(label(key));
  }
  $('textBounds').textContent=overflow.length ? `${overflow.join(' · ')} ล้นจอ ลดขนาดหรือแบ่งข้อความ` : '';
  $('textBounds').hidden=!overflow.length;
 }
 // a round outline: copies of the text all around it, a second ring inside for thick outlines
 function boxPad(e,axis){const own=e['box_pad_'+axis];return Number.isInteger(own)?own:Math.max(4,e.outline || 12);}
 function ring(r,colour){
  const out=[];for(const radius of r>3?[r,r/2]:[r])for(let i=0;i<24;i++){const a=i/24*Math.PI*2;out.push(`${(Math.cos(a)*radius).toFixed(2)}px ${(Math.sin(a)*radius).toFixed(2)}px 0 ${colour}`);}
  return out.join(',');
 }
 function paint(){
  if(!frame.clientWidth)return;
  for(const [key,node] of nodes){
   node.hidden=!visibleRoles().includes(key)||(live.has(key)&&!live.get(key).trim());if(node.hidden)continue;
   const v=values()[key];node.setAttribute('aria-label',`ปรับ${label(key)}`);
   ensureFont(v.font,v.bold!==false,!!v.italic);
   node.firstElementChild.textContent=live.get(key) || v.text || '…';
   node.style.left=v.x+'%';node.style.top=v.y+'%';node.style.color=v.color;
   node.firstElementChild.style.fontFamily=key===selected&&hoverFont?JSON.stringify(hoverFont)+','+fonts.system:fontCss(v.font);node.firstElementChild.style.fontSize=(fittedCaptionSize(node.firstElementChild.textContent,v,frame.clientHeight/frame.clientWidth)*frame.clientWidth/1080)+'px';
   const e={...roleEffects(key),...v},k=frame.clientWidth/1080,span=node.firstElementChild;
   span.style.webkitTextStroke='';span.style.textShadow=!e.box_on&&e.outline?ring(e.outline*k,e.outline_color):'';
   const alpha=p=>Math.round(p*2.55).toString(16).padStart(2,'0'),a=(Number.isInteger(e.shadow_angle)?e.shadow_angle:45)*Math.PI/180,d=e.shadow*k*Math.SQRT2;
   span.style.filter=[!e.box_on&&e.glow_on?`drop-shadow(0 0 ${e.glow_range*k/2}px ${e.glow_color}${alpha(e.glow_strength)})`:'',
    !e.box_on&&e.shadow?`drop-shadow(${(d*Math.cos(a)).toFixed(2)}px ${(d*Math.sin(a)).toFixed(2)}px ${e.shadow_blur*k}px ${e.shadow_color}${alpha(e.shadow_opacity)})`:''].filter(Boolean).join(' ');
   span.style.fontWeight=e.bold===false?'400':'700';span.style.fontStyle=e.italic?'italic':'';span.style.textDecoration=e.underline?'underline':'';
   span.style.textTransform={upper:'uppercase',lower:'lowercase',title:'capitalize'}[e.case] || '';span.style.letterSpacing=e.spacing?(e.spacing*k*fittedCaptionSize(span.textContent,v,frame.clientHeight/frame.clientWidth)/v.size)+'px':'';
   node.style.transform={left:'translate(0,-50%)',right:'translate(-100%,-50%)'}[e.align] || '';
   node.style.mixBlendMode={add:'plus-lighter',screen:'screen',multiply:'multiply'}[e.blend] || '';
   span.style.background=e.box_on?e.box_color+alpha(Number.isInteger(e.box_opacity)?e.box_opacity:100):'';span.style.padding=e.box_on?`${boxPad(e,'y')*k}px ${boxPad(e,'x')*k}px`:'';
   span.dataset.enter=e.enter;
   node.dataset.selected=String(key===selected);node.disabled=!enabled;
   if(key===selected)node.setAttribute('aria-current','true');else node.removeAttribute('aria-current');
  }
  $('alignX').hidden=!(drag?.moved && !drag.handle && values()[drag.key].x===50);
  $('alignY').hidden=!(drag?.moved && !drag.handle && values()[drag.key].y===50);
  const node=nodes.get(selected),handle=$('textResize');
  handle.hidden=!enabled || !node || node.hidden;handle.disabled=!enabled;
  if(!handle.hidden){handle.style.left=Math.max(22,Math.min(frame.clientWidth-22,node.offsetLeft+node.offsetWidth/2))+'px';handle.style.top=Math.max(22,Math.min(frame.clientHeight-22,node.offsetTop+node.offsetHeight/2))+'px';}
  inspectBounds();
 }
 function visibleRoles(){return layout().stage===5 ? layout().items.map(v=>'hl:'+v.id) : layout().roles;}
 function render(){
  endDrag();
  const ids=new Set(layout().items.map(v=>'hl:'+v.id));
  for(const [key,node] of nodes){if(key.startsWith('hl:') && !ids.has(key)){if(drag?.key===key)endDrag();node.remove();nodes.delete(key);}}
  for(const key of ids){if(!nodes.has(key))createNode(key);}
  const headline=layout().stage===5,host=$(headline?'headlineHost':'captionHost');
  const workspace=$('textStyleWorkspace');if(workspace.parentElement!==host){endDrag();host.append(workspace);}
  const visible=visibleRoles();if(!visible.includes(selected)){endDrag();selected=visible[0] || null;}
  $('textComponents').hidden=headline;$('textPresetRow').hidden=headline;for(const b of document.querySelectorAll('[data-look]'))b.disabled=!enabled;$('headlineActions').hidden=!headline;$('addHeadline').disabled=!enabled || layout().items.length>=100;$('removeHeadline').hidden=!selected?.startsWith('hl:');$('removeHeadline').disabled=!enabled;
  frame.dataset.empty=String(!visible.length);
  $('textFieldError').hidden=true;
  for(const control of Object.values(controls))control.removeAttribute('aria-invalid');
  const current=values()[selected];
  $('textControls').hidden=!current;$('textSelectionHint').hidden=!!current;
  $('textSelectionHint').textContent=headline?'ยังไม่มีหัวคลิป':'ไม่ติ๊กเลย คลิปจะไม่มีซับ';
  $('sampleLabel').textContent=headline?'ข้อความบนหัวคลิป':'ข้อความตัวอย่าง';
  queueMicrotask(()=>{const r=document.querySelector('input[type=range][data-for="text-size"]');if(r){r.value=$('text-size').value;r.disabled=$('text-size').disabled;}});
  for(const key of fieldNames){controls[key].value=key==='font'?(current?fontName(current.font):''):(current?.[key] ?? (key==='color'?fallback.speech.color:''));controls[key].disabled=!enabled || !current;}
  for(const input of document.querySelectorAll('[data-role-option]')){input.checked=layout().roles.includes(input.dataset.roleOption);input.disabled=!enabled;}
  for(const row of document.querySelectorAll('[data-role-row]'))row.dataset.selected=String(row.dataset.roleRow===selected);
  $('text-color-hex').removeAttribute('aria-invalid');
  $('text-color-hex').value=current?.color ?? '';$('text-color-hex').disabled=!enabled || !current;
  $('localFonts').disabled=!enabled || !current || fontLoading;checkFont(current?.font,current?.bold!==false,!!current?.italic);
  $('textRoleTitle').textContent=selected?label(selected):'เลือกข้อความบนจอ';
  showEffects();

  paint();
 }
 for(const b of document.querySelectorAll('[data-look]'))b.onclick=()=>{if(!enabled)return;const next=structuredClone(read() || fallback);
  for(const [role,look] of Object.entries(looks[b.dataset.look]))Object.assign(next[role],look);write(next);render();};
 for(const [key,control] of Object.entries(controls)){
  control.addEventListener('input',()=>{
   if(!enabled || !selected)return;
   const value=key==='size' ? control.valueAsNumber : key==='font'?fontValue(control.value):control.value;
   const next=structuredClone(values());next[selected][key]=value;
   const {id,...style}=next[selected];
   if(!validTextStyle(style)){
    control.setAttribute('aria-invalid','true');$('textFieldError').textContent=key==='font'?'ใช้ชื่อฟอนต์ 1–128 ตัวอักษร ไม่มีอัญประกาศหรือเครื่องหมายพิเศษ':'ใช้ขนาด 24–160 และข้อความไม่เกิน 80 ตัวอักษร';$('textFieldError').hidden=false;return;
   }
   for(const input of Object.values(controls))input.removeAttribute('aria-invalid');
   $('textFieldError').hidden=true;commit(next);$('text-color-hex').removeAttribute('aria-invalid');$('text-color-hex').value=next[selected].color;checkFont(next[selected].font,next[selected].bold!==false,!!next[selected].italic);paint();
  });
 }
 const effectInputs={outline:$('fx-outline'),outline_color:$('fx-outline-color'),shadow:$('fx-shadow'),shadow_blur:$('fx-shadow-blur'),shadow_color:$('fx-shadow-color'),shadow_opacity:$('fx-shadow-opacity'),box_on:$('fx-box'),box_color:$('fx-box-color'),box_pad_x:$('fx-box-x'),box_pad_y:$('fx-box-y'),box_opacity:$('fx-box-opacity'),shadow_angle:$('fx-shadow-angle'),
  bold:$('fx-bold'),italic:$('fx-italic'),underline:$('fx-underline'),case:$('fx-case'),spacing:$('fx-spacing'),align:$('fx-align'),
  glow_on:$('fx-glow'),glow_color:$('fx-glow-color'),glow_strength:$('fx-glow-strength'),glow_range:$('fx-glow-range'),enter:$('fx-enter'),blend:$('fx-blend')};
 function showEffects(){
  const current=values()[selected],e={...roleEffects(selected),...(current || {})};
  for(const [key,input] of Object.entries(effectInputs)){if(input.type==='checkbox')input.checked=!!e[key];else input.value=key.startsWith('box_pad_')?boxPad(e,key.slice(-1)):e[key];input.disabled=!enabled || !current;}
  effectInputs.box_pad_x.disabled=effectInputs.box_pad_y.disabled=!enabled || !current || !e.box_on;
  effectInputs.outline_color.disabled=effectInputs.shadow.disabled=effectInputs.shadow_blur.disabled=effectInputs.shadow_color.disabled=effectInputs.shadow_opacity.disabled=!enabled || !current || e.box_on;effectInputs.box_color.disabled=!enabled || !current || !e.box_on;
  $('fx-stroke-on').checked=e.outline>0;$('fx-shadow-on').checked=e.shadow>0;
  for(const [box,on] of [['fx-stroke-on',e.outline>0&&!e.box_on],['fx-box',e.box_on],['fx-glow',e.glow_on&&!e.box_on],['fx-shadow-on',e.shadow>0&&!e.box_on]])$(box).closest('.fx-sec')?.setAttribute('data-off',String(!on));
  $('fx-stroke-on').disabled=$('fx-shadow-on').disabled=!enabled || !current;
  for(const b of document.querySelectorAll('[data-pick]'))b.setAttribute('aria-pressed',String($(b.dataset.for).value===b.dataset.pick));
  for(const r of document.querySelectorAll('input[type=range][data-for]')){const n=$(r.dataset.for);r.value=n.value;r.disabled=n.disabled;}
  for(const tile of document.querySelectorAll('[data-enter-pick]')){tile.setAttribute('aria-pressed',String(tile.dataset.enterPick===e.enter));tile.disabled=!enabled || !current;}
 }
 for(const [key,input] of Object.entries(effectInputs))input.addEventListener(input.type==='checkbox'||input.tagName==='SELECT'?'change':'input',()=>{
  if(!enabled || !selected)return;
  const value=input.type==='checkbox'?input.checked:input.type==='number'?input.valueAsNumber:input.value;
  const next=structuredClone(values());next[selected][key]=value;
  const {id,...style}=next[selected];if(!validTextStyle(style)){input.setAttribute('aria-invalid','true');return;}
  input.removeAttribute('aria-invalid');commit(next);showEffects();if(key==='bold'||key==='italic')checkFont(next[selected].font,next[selected].bold!==false,!!next[selected].italic);paint();if(key==='enter')replay(selected);
 });
 // entrance tiles: each plays its own effect on a small sample; picking one sets the hidden select
 // sliders drive their number box; segmented buttons set their hidden select; the stroke and shadow ticks
 // switch those effects off (0) or back to a visible default
 for(const r of document.querySelectorAll('input[type=range][data-for]'))r.oninput=()=>{const n=$(r.dataset.for);n.value=r.value;n.dispatchEvent(new Event('input'));};
 for(const b of document.querySelectorAll('[data-pick]'))b.onclick=()=>{const s=$(b.dataset.for);s.value=b.dataset.pick;s.dispatchEvent(new Event('change'));};
 for(const [tick,id,on] of [['fx-stroke-on','fx-outline',4],['fx-shadow-on','fx-shadow',4]])$(tick).onchange=()=>{const n=$(id);n.value=$(tick).checked?on:0;n.dispatchEvent(new Event('input'));};
 for(const tile of document.querySelectorAll('[data-enter-pick]'))tile.onclick=()=>{const pick=$('fx-enter');pick.value=tile.dataset.enterPick;pick.dispatchEvent(new Event('change'));};
 let fontLoading=false,fontRequest=0;
 const fontChecks=new Map();
 function ensureFont(value,bold=true,italic=false){
  if(!value)return Promise.resolve(false);
  const cacheKey=value+'|'+bold+'|'+italic;
  if(!fontChecks.has(cacheKey)){
   const family=value==='tahoma'?'Tahoma':value==='system'||value==='thai'?'Leelawadee UI':value.slice(7),query=new URLSearchParams({family,bold:String(bold),italic:String(italic)});
   const face=new FontFace(family,`url("/api/v1/fonts/preview?${query}")`,{weight:bold?'700':'400',style:italic?'italic':'normal'});
   fontChecks.set(cacheKey,face.load().then(loaded=>{document.fonts.add(loaded);document.dispatchEvent(new Event('caption-font-ready'));return true;},()=>false));
  }
  return fontChecks.get(cacheKey);
 }
 function checkFont(value,bold=true,italic=false){
  const request=++fontRequest;if(!value){$('fontStatus').textContent='';return;}
  ensureFont(value,bold,italic).then(found=>{if(request===fontRequest){$('fontStatus').textContent=found?'':'ไม่พบฟอนต์นี้ · แสดงฟอนต์สำรอง';paint();}});
 }
 // the read fonts as a list under the field, each shown in its own face and narrowed by what is typed
 let localNames=[];
 // the list opens as a dropdown under the field, over the panel (fixed, so the panel's scroll never cuts it);
 // picking a font or clicking anywhere else closes it
 function closeFontList(){$('fontList').hidden=true;if(hoverFont){hoverFont=null;paint();}}
 function placeFontList(){
  const list=$('fontList'),box=$('text-font').closest('.tc-font') || $('text-font'),r=box.getBoundingClientRect();
  const below=innerHeight-r.bottom-8,above=r.top-8,up=below<240&&above>below;
  list.style.left=r.left+'px';list.style.width=r.width+'px';
  list.style.maxHeight=Math.max(160,Math.min(360,up?above:below))+'px';
  list.style.top=up?'':(r.bottom+4)+'px';list.style.bottom=up?(innerHeight-r.top+4)+'px':'';
 }
 document.addEventListener('pointerdown',e=>{if(!$('fontList').hidden&&!e.target.closest('#fontList,.tc-font'))closeFontList();});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('fontList').hidden)closeFontList();});
 addEventListener('resize',()=>{if(!$('fontList').hidden)placeFontList();});
 $('fontList').addEventListener('pointerleave',()=>{if(hoverFont){hoverFont=null;paint();}});
 function showFontList(filter=false){
  const list=$('fontList');if(!localNames.length){list.hidden=true;return;}
  const current=$('text-font').value.trim(),typed=filter?current.toLowerCase():'';
  const shown=localNames.filter(n=>!typed || n.toLowerCase().includes(typed) || n===current).slice(0,200);
  const names=shown.length?shown:localNames.slice(0,200);
  list.replaceChildren(...names.map(name=>{const b=document.createElement('button');b.type='button';b.setAttribute('role','option');b.setAttribute('aria-selected',String(name===current));
   b.style.fontFamily=JSON.stringify(name)+',system-ui,sans-serif';b.append(name);const sample=document.createElement('small');sample.textContent='สวัสดี Aa';sample.style.fontFamily=b.style.fontFamily;b.append(sample);
   b.onpointerenter=()=>{hoverFont=name;paint();};
   b.onclick=()=>{const input=$('text-font');hoverFont=null;input.value=name;input.dispatchEvent(new Event('input'));closeFontList();};return b;}));
  list.hidden=false;placeFontList();
 }
 $('text-font').addEventListener('input',e=>{if(e.isTrusted)showFontList(true);});
 $('text-font').addEventListener('focus',()=>{if(localNames.length)showFontList();});
 $('localFonts').onclick=async()=>{
  if(!enabled || !selected || fontLoading)return;
  if(localNames.length){if($('fontList').hidden)showFontList();else closeFontList();return;}
  if(!window.queryLocalFonts){$('fontStatus').textContent='เบราว์เซอร์นี้อ่านรายชื่อไม่ได้ · พิมพ์ชื่อฟอนต์แทน';return;}
  fontLoading=true;$('localFonts').disabled=true;$('localFonts').textContent='กำลังอ่านฟอนต์';
  try{
   const entries=await window.queryLocalFonts();
   const names=[...new Set(entries.map(v=>v.family).filter(v=>validFont('family:'+v)))].sort((a,b)=>a.localeCompare(b));
   const options=names.map(name=>{const option=document.createElement('option');option.value=name;return option;});
   $('textFonts').replaceChildren(...Object.values(fontLabels).map(name=>{const option=document.createElement('option');option.value=name;return option;}),...options);
   localNames=names;showFontList();
   $('fontStatus').textContent=names.length?'อ่านแล้ว '+names.length+' ฟอนต์ · คลิกชื่อในรายการ หรือพิมพ์เพื่อค้น':'ไม่พบรายชื่อ · พิมพ์ชื่อฟอนต์แทน';
  }catch(_){$('fontStatus').textContent='อ่านรายชื่อไม่ได้ · ลองใหม่หรือพิมพ์ชื่อฟอนต์';}
  finally{fontLoading=false;$('localFonts').disabled=!enabled || !selected;$('localFonts').textContent='ฟอนต์ในเครื่อง';}
 };
 $('text-color-hex').oninput=()=>{
  if(!enabled || !selected)return;
  const input=$('text-color-hex');
  if(!/^#[a-fA-F0-9]{6}$/.test(input.value)){input.setAttribute('aria-invalid','true');$('textFieldError').textContent='รหัสสีใช้ # ตามด้วยเลขหรือตัวอักษร 6 ตัว';$('textFieldError').hidden=false;return;}
  input.removeAttribute('aria-invalid');$('textFieldError').hidden=true;controls.color.value=input.value;
  const next=structuredClone(values());next[selected].color=input.value;commit(next);paint();
 };
 const resize=$('textResize');
 resize.onpointerdown=event=>{
  if(!enabled || !selected || drag || event.button!==0 || !event.isPrimary)return;
  const node=nodes.get(selected),box=node.getBoundingClientRect(),cx=box.x+box.width/2,cy=box.y+box.height/2;
  drag={key:selected,pointer:event.pointerId,handle:resize,clientX:event.clientX,clientY:event.clientY,size:values()[selected].size,cx,cy,distance:Math.max(1,Math.hypot(event.clientX-cx,event.clientY-cy)),moved:false};
  resize.setPointerCapture(event.pointerId);event.preventDefault();
 };
 resize.onpointermove=event=>{
  if(!enabled || drag?.handle!==resize || drag.pointer!==event.pointerId)return;
  if(!drag.moved && Math.hypot(event.clientX-drag.clientX,event.clientY-drag.clientY)<4)return;
  drag.moved=true;preview=structuredClone(stored());preview[selected].size=Math.max(24,Math.min(160,Math.round(drag.size*Math.hypot(event.clientX-drag.cx,event.clientY-drag.cy)/drag.distance)));
  controls.size.value=preview[selected].size;paint();
 };
 resize.onpointerup=event=>{
  if(drag?.handle!==resize || drag.pointer!==event.pointerId)return;
  const next=preview,changed=drag.moved && next && next[selected].size!==drag.size;endDrag();if(changed && enabled)commit(next);render();
 };
 resize.onpointercancel=resize.onlostpointercapture=event=>{if(drag?.handle===resize && drag.pointer===event.pointerId){endDrag();render();}};
 resize.onkeydown=event=>{
  if(event.key==='Escape' && drag){event.preventDefault();endDrag();render();return;}
  if(!enabled || !selected || !['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.key))return;
  event.preventDefault();const next=structuredClone(stored()),step=event.shiftKey?10:1;
  next[selected].size=Math.max(24,Math.min(160,next[selected].size+(['ArrowUp','ArrowRight'].includes(event.key)?step:-step)));commit(next);render();
 };
 $('removeHeadline').onclick=()=>{if(enabled && selected?.startsWith('hl:')){endDrag();writeItems(layout().items.filter(v=>'hl:'+v.id!==selected));render();}};
 const observer=new ResizeObserver(paint);observer.observe(frame);
 document.fonts.ready.then(paint);
  // a new line on the live preview replays the entrance effect
  function replay(key){const span=nodes.get(key)?.firstElementChild;if(!span || ['none','words'].includes(span.dataset.enter))return;span.classList.remove('entering');void span.offsetWidth;span.classList.add('entering');}
  return {render,select,live:(key,text)=>{const changed=(live.get(key) || '')!==(text || '');if(text)live.set(key,text);else live.delete(key);paint();if(changed&&text)replay(key);},selectHeadline:id=>select('hl:'+id),editing:value=>{if(!value)endDrag();enabled=value;render();}};
}
