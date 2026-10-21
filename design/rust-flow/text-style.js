// Style samples live in a separate draft field; they never replace dialogue or the AI plan.
export const roles=['headline','speech','emphasis','english'];
const fonts={system:'"Segoe UI","Leelawadee UI",Tahoma,system-ui,sans-serif',tahoma:'Tahoma,"Leelawadee UI",sans-serif',thai:'"Leelawadee UI",Tahoma,sans-serif'};
const fontLabels={system:'ฟอนต์ระบบ',tahoma:'Tahoma',thai:'Leelawadee UI'};
export function validFont(value){return typeof value==='string' && (Object.hasOwn(fonts,value) || (value.startsWith('family:') && value.slice(7).length>0 && value.slice(7).length<=128 && value.slice(7).trim()===value.slice(7) && !/[\p{Cc}"'\\,;{}<>]/u.test(value.slice(7))));}
function fontValue(name){return Object.keys(fontLabels).find(key=>fontLabels[key]===name) || 'family:'+name;}
function fontName(value){return fontLabels[value] ?? value.slice(7);}
function fontCss(value){return fonts[value] ?? JSON.stringify(value.slice(7))+','+fonts.system;}
const labels={headline:'หัวคลิป',speech:'คำพูด',emphasis:'คำสำคัญ',english:'คำแปลอังกฤษ'};
export function defaults(){
 // Clip text colours are video output, not UI theme tokens.
 const white='#ececf1',orange='#f59e0b';
 return {headline:{text:'หัวข้อคลิป',font:'system',size:80,color:white,x:50,y:16},speech:{text:'ตัวอย่างข้อความพูด',font:'system',size:64,color:white,x:50,y:69},
  emphasis:{text:'คำเน้นสำคัญ',font:'system',size:84,color:orange,x:50,y:55},english:{text:'English subtitle',font:'system',size:56,color:white,x:50,y:78}};
}
// optional text effects; absent fields mean the export defaults (outline 4 black, shadow 1, no box, no entrance)
export const effectDefaults={outline:4,outline_color:'#000000',shadow:1,box_on:false,box_color:'#000000',enter:'none'};
const effectValid={outline:v=>Number.isInteger(v)&&v>=0&&v<=20,shadow:v=>Number.isInteger(v)&&v>=0&&v<=20,outline_color:v=>/^#[a-fA-F0-9]{6}$/.test(v),
 box_color:v=>/^#[a-fA-F0-9]{6}$/.test(v),box_on:v=>typeof v==='boolean',enter:v=>['none','fade','pop','wipe','words'].includes(v)};
// speech appears word by word unless another entrance is picked
export const roleEffects=key=>key==='speech'?{...effectDefaults,enter:'words'}:effectDefaults;
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
 function paint(){
  if(!frame.clientWidth)return;
  for(const [key,node] of nodes){
   node.hidden=!visibleRoles().includes(key);if(node.hidden)continue;
   const v=values()[key];node.setAttribute('aria-label',`ปรับ${label(key)}`);
   node.firstElementChild.textContent=live.get(key) || v.text || '…';
   node.style.left=v.x+'%';node.style.top=v.y+'%';node.style.color=v.color;
   node.firstElementChild.style.fontFamily=fontCss(v.font);node.firstElementChild.style.fontSize=(v.size*frame.clientWidth/1080)+'px';
   const e={...roleEffects(key),...v},k=frame.clientWidth/1080,span=node.firstElementChild;
   span.style.webkitTextStroke=!e.box_on&&e.outline?`${e.outline*2*k}px ${e.outline_color}`:'';span.style.paintOrder='stroke fill';
   span.style.textShadow=!e.box_on&&e.shadow?`${e.shadow*k}px ${e.shadow*k}px 0 rgb(0 0 0 / .6)`:'';
   span.style.background=e.box_on?e.box_color:'';span.style.padding=e.box_on?`${Math.max(4,e.outline || 12)*k}px`:'';
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
  const headline=layout().stage===5,host=$(headline?'headlineHost':layout().stage===3?'editStyleHost':'captionHost');
  const workspace=$('textStyleWorkspace');if(workspace.parentElement!==host){endDrag();host.append(workspace);}
  const visible=visibleRoles();if(!visible.includes(selected)){endDrag();selected=visible[0] || null;}
  $('textComponents').hidden=headline;$('headlineActions').hidden=!headline;$('addHeadline').disabled=!enabled || layout().items.length>=100;$('removeHeadline').hidden=!selected?.startsWith('hl:');$('removeHeadline').disabled=!enabled;
  frame.dataset.empty=String(!visible.length);
  $('textFieldError').hidden=true;
  for(const control of Object.values(controls))control.removeAttribute('aria-invalid');
  const current=values()[selected];
  $('textControls').hidden=!current;$('textSelectionHint').hidden=!!current;
  $('textSelectionHint').textContent=headline?'ยังไม่มีหัวคลิป':'ไม่ติ๊กเลย คลิปจะไม่มีซับ';
  $('sampleLabel').textContent=headline?'ข้อความบนหัวคลิป':'ข้อความตัวอย่าง';
  for(const key of fieldNames){controls[key].value=key==='font'?(current?fontName(current.font):''):(current?.[key] ?? (key==='color'?fallback.speech.color:''));controls[key].disabled=!enabled || !current;}
  for(const input of document.querySelectorAll('[data-role-option]')){input.checked=layout().roles.includes(input.dataset.roleOption);input.disabled=!enabled;}
  for(const row of document.querySelectorAll('[data-role-row]'))row.dataset.selected=String(row.dataset.roleRow===selected);
  $('text-color-hex').removeAttribute('aria-invalid');
  $('text-color-hex').value=current?.color ?? '';$('text-color-hex').disabled=!enabled || !current;
  $('localFonts').disabled=!enabled || !current || fontLoading;checkFont(current?.font);
  $('textRoleTitle').textContent=selected?label(selected):'เลือกข้อความบนจอ';
  showEffects();

  paint();
 }
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
   $('textFieldError').hidden=true;commit(next);$('text-color-hex').removeAttribute('aria-invalid');$('text-color-hex').value=next[selected].color;checkFont(next[selected].font);paint();
  });
 }
 const effectInputs={outline:$('fx-outline'),outline_color:$('fx-outline-color'),shadow:$('fx-shadow'),box_on:$('fx-box'),box_color:$('fx-box-color'),enter:$('fx-enter')};
 function showEffects(){
  const current=values()[selected],e={...roleEffects(selected),...(current || {})};
  for(const [key,input] of Object.entries(effectInputs)){if(input.type==='checkbox')input.checked=!!e[key];else input.value=e[key];input.disabled=!enabled || !current;}
  effectInputs.outline_color.disabled=effectInputs.shadow.disabled=!enabled || !current || e.box_on;effectInputs.box_color.disabled=!enabled || !current || !e.box_on;
 }
 for(const [key,input] of Object.entries(effectInputs))input.addEventListener(input.type==='checkbox'||input.tagName==='SELECT'?'change':'input',()=>{
  if(!enabled || !selected)return;
  const value=input.type==='checkbox'?input.checked:input.type==='number'?input.valueAsNumber:input.value;
  const next=structuredClone(values());next[selected][key]=value;
  const {id,...style}=next[selected];if(!validTextStyle(style)){input.setAttribute('aria-invalid','true');return;}
  input.removeAttribute('aria-invalid');commit(next);showEffects();paint();if(key==='enter')replay(selected);
 });
 let fontLoading=false,fontRequest=0;
 const fontChecks=new Map();
 function checkFont(value){
  const request=++fontRequest;
  if(!value || !value.startsWith('family:')){$('fontStatus').textContent='';return;}
  if(!fontChecks.has(value))fontChecks.set(value,new FontFace('EditorKrabCheck','local('+JSON.stringify(value.slice(7))+')').load().then(()=>true,()=>false));
  fontChecks.get(value).then(found=>{if(request===fontRequest){$('fontStatus').textContent=found?'ใช้ฟอนต์ในเครื่อง':'ไม่พบฟอนต์นี้ · แสดงฟอนต์สำรอง';paint();}});
 }
 $('localFonts').onclick=async()=>{
  if(!enabled || !selected || fontLoading)return;
  if(!window.queryLocalFonts){$('fontStatus').textContent='เบราว์เซอร์นี้อ่านรายชื่อไม่ได้ · พิมพ์ชื่อฟอนต์แทน';return;}
  fontLoading=true;$('localFonts').disabled=true;$('localFonts').textContent='กำลังอ่านฟอนต์';
  try{
   const entries=await window.queryLocalFonts();
   const names=[...new Set(entries.map(v=>v.family).filter(v=>validFont('family:'+v)))].sort((a,b)=>a.localeCompare(b));
   const options=names.map(name=>{const option=document.createElement('option');option.value=name;return option;});
   $('textFonts').replaceChildren(...Object.values(fontLabels).map(name=>{const option=document.createElement('option');option.value=name;return option;}),...options);
   $('fontStatus').textContent=names.length?'อ่านแล้ว '+names.length+' ฟอนต์ · เลือกจากช่องฟอนต์':'ไม่พบรายชื่อ · พิมพ์ชื่อฟอนต์แทน';
  }catch(_){$('fontStatus').textContent='อ่านรายชื่อไม่ได้ · ลองใหม่หรือพิมพ์ชื่อฟอนต์';}
  finally{fontLoading=false;$('localFonts').disabled=!enabled || !selected;$('localFonts').textContent='อ่านรายชื่อฟอนต์';}
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
