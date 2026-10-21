async(page)=>{
 const base='http://127.0.0.1:8871',project='six-step-'+Date.now();
 const check=(v,m)=>{if(!v)throw Error(m)};
 const saved=()=>page.waitForFunction(()=>['บันทึกแล้ว','พร้อมแก้ไข'].includes(document.querySelector('#saved').textContent),null,{polling:100});
 const read=async()=>await(await page.request.get(`${base}/api/v1/drafts/${project}`)).json();
 await page.goto(`${base}/?project=${project}`);await page.setViewportSize({width:1440,height:1100});
 check(await page.locator('[data-step]').count()===6,'six stages missing');
 await page.locator('[data-step="4"]').click();await saved();
 check(await page.locator('#textLayer,#text-x,#text-y').count()===0,'removed controls remain');
 for(const role of ['speech','emphasis','english','headline'])check(!await page.locator(`[data-text-role=${role}]`).isVisible(),'unchecked text visible: '+role);
 check(await page.locator('#textFrame').innerText()==='','initial frame not empty');
 await page.locator('[data-role-option=speech]').check();await saved();
 check(await page.locator('[data-text-role=speech]').isVisible(),'checked speech hidden');
 check(!await page.locator('[data-text-role=emphasis]').isVisible(),'unchecked emphasis visible');
 await page.locator('#text-text').fill('ข้อความที่ต้องเก็บ');await saved();
 await page.locator('[data-role-option=speech]').uncheck();await saved();
 check(!await page.locator('[data-text-role=speech]').isVisible(),'unticked text remains visible');
 await page.reload();await page.locator('#textFrame').waitFor();
 check(!await page.locator('[data-role-option=speech]').isChecked(),'unchecked state lost');
 await page.locator('[data-role-option=speech]').check();await saved();
 check(await page.locator('#text-text').inputValue()==='ข้อความที่ต้องเก็บ','untick destroyed settings');
 await page.locator('[data-step="5"]').click();await saved();
 check(await page.locator('#headlineTemplate,#style').count()===0,'templates not removed');
 await page.locator('#addHeadline').click();await saved();
 check(await page.locator('[data-text-role=headline]').isVisible(),'headline preview missing');
 check(!await page.locator('[data-text-role=speech]').isVisible(),'subtitle leaked into headline stage');
 await page.locator('#headlineAi > summary').click();
 check(await page.locator('#headlinePacket').isDisabled(),'empty plan exported');
 await page.locator('[data-step="3"]').click();await page.locator('#plan').fill(JSON.stringify({stories:[{title:'เริ่มทำคอนเทนต์',text:'เริ่มจากสิ่งที่มี',segment_ids:['s0']}]}));await saved();
 await page.locator('[data-step="5"]').click();await saved();
 await page.evaluate(()=>{const original=URL.createObjectURL;URL.createObjectURL=blob=>{window.headlineTestBlob=blob;return original(blob);};});
 const pending=page.waitForEvent('download');await page.locator('#headlinePacket').click();const download=await pending;await download.saveAs('output/headline-ai-test-packet.json');
 const packet=await page.evaluate(async()=>JSON.parse(await window.headlineTestBlob.text()));
 check(packet.project_id===project&&packet.content_hash.length===64,'packet not bound to current draft/template');
 const answer={...packet.response_schema,texts:packet.response_schema.texts.map(v=>({...v,text:'เริ่มจากสิ่งที่มี'}))};
 await page.locator('#headlineReply').fill(JSON.stringify(answer));await saved();await page.locator('#applyHeadline').click();await saved();
 check(await page.locator('[data-text-role=headline] span').textContent()===answer.texts[0].text,'AI headline not applied');
 const current=(await read()).document.headline_items[0].text;
 await page.locator('#text-text').fill('แก้หัวคลิปใหม่');await saved();await page.locator('#applyHeadline').click();
 await page.waitForFunction(()=>document.querySelector('#headlineStatus').textContent.includes('ใช้ไม่ได้'));
 check((await read()).document.headline_items[0].text==='แก้หัวคลิปใหม่','stale response overwrote headline');
 await page.locator('[data-step="6"]').click();await saved();check(await page.locator('#createTitle').isVisible(),'creation stage not moved');
 // Old step 05 was creation; preserve that meaning before first edit upgrades it.
 const legacy=project+'-legacy';const old={step:5,title:'ร่างเดิม',language:'auto',files:[],plan:'',style:'bold16'};
 check((await page.request.put(`${base}/api/v1/drafts/${legacy}`,{data:{operation_id:'old',expected_revision:0,document:old}})).status()===200,'legacy fixture save failed');
 await page.goto(`${base}/?project=${legacy}`);await page.locator('#createTitle').waitFor();
 check((await(await page.request.get(`${base}/api/v1/drafts/${legacy}`)).json()).revision===1,'view-only migration created revision');
 await page.locator('[data-step="5"]').click();check(await page.locator('#headlineTitle').isVisible(),'legacy headline navigation mapped to creation');
 check((await(await page.request.get(`${base}/api/v1/drafts/${legacy}`)).json()).revision===1,'step navigation created revision');
 await page.locator('#title').fill('ร่างเดิม แก้แล้ว');await saved();const migrated=await(await page.request.get(`${base}/api/v1/drafts/${legacy}`)).json();
 check(migrated.document.flow_version===2&&migrated.document.step===5,'first edit did not upgrade legacy step');
 for(const width of [1440,375]){
  await page.setViewportSize({width,height:1050});
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'six-step mobile overflow');
  await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:`output/six-step-hl-${width===375?'mobile':'desktop'}.png`,fullPage:true});
 }
 console.log(JSON.stringify({project,checks:['six stages','initial unchecked empty frame','check/uncheck real visibility','settings retained/reloaded','HL separated','no templates','AI packet roundtrip','stale response rejected','creation is 06','legacy 05 meaning preserved','375px layout']}));
}
