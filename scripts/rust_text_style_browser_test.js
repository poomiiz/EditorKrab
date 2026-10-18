async (page)=>{
 const base='http://127.0.0.1:8871',project='text-style-'+Date.now();
 const check=(v,m)=>{if(!v)throw Error(m)};
 const saved=()=>page.waitForFunction(()=>['บันทึกแล้ว','พร้อมแก้ไข'].includes(document.querySelector('#saved').textContent),null,{polling:100});
 const read=async()=>await(await page.request.get(`${base}/api/v1/drafts/${project}`)).json();
 await page.goto(`${base}/?project=${project}`);
 await page.locator('[data-step="4"]').click();await saved();
 for(const role of ['speech','emphasis','english'])await page.locator(`[data-role-option=${role}]`).check();await saved();
 await page.locator('#textFrame').waitFor();
 check(await page.locator('video,audio,canvas').count()===0,'style preview loaded media or inaccessible canvas');
 const before=await read();await page.locator('[data-text-role="emphasis"]').click();
 check(await page.locator('#textRoleTitle').textContent()==='คำสำคัญ','frame selection not connected to inspector');
 check((await read()).revision===before.revision,'layer selection wrote a draft revision');
 await page.locator('#text-text').fill('โปรโมตฟรีของจริง');
 await page.locator('#text-font').fill('Tahoma');
 await page.locator('#text-size').fill('92');
 await page.locator('#text-color').fill('#f59e0b');
 await saved();
 const draft=await read(),text=draft.document.text_styles.emphasis;
 check(text.text==='โปรโมตฟรีของจริง'&&text.font==='tahoma'&&text.size===92&&text.x===50&&text.y===55,'parameters not saved');
 check(await page.locator('[data-text-role="emphasis"] span').textContent()===text.text,'frame text mismatch');
 await page.reload();await page.locator('#textFrame').waitFor();await page.locator('[data-text-role=emphasis]').click();
 check(await page.locator('#text-size').inputValue()==='92','reload lost parameters');
 const revision=(await read()).revision;
 await page.locator('#text-size').fill('0');
 check(await page.locator('#text-size').getAttribute('aria-invalid')==='true','invalid size accepted');
 check((await read()).revision===revision,'invalid size persisted');
 await page.locator('#text-size').fill('96');await saved();
 await page.locator('#undo').click();await saved();check(await page.locator('#text-size').inputValue()==='92','style undo failed');
 await page.locator('#redo').click();await saved();check(await page.locator('#text-size').inputValue()==='96','style redo failed');
 await page.locator('#text-text').fill('ตัวอย่าง'.repeat(10));await saved();
 await page.locator('#textBounds').waitFor();
 check((await read()).document.text_styles.emphasis.text==='ตัวอย่าง'.repeat(10),'overflow silently shortened text');
 await page.locator('#text-text').fill('โปรโมตฟรีของจริง');await saved();
 const good=await read(),bad=structuredClone(good.document);bad.text_styles.speech.size=-10;
 await page.locator('#openFile').setInputFiles({name:'bad-style.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(bad))});
 await page.waitForFunction(()=>document.querySelector('#error').textContent.includes('เปิดไม่ได้'));
 check((await read()).revision===good.revision,'invalid style import replaced draft');
 // Clear test-only alert with a reload; the valid stored style must remain available.
 await page.reload();await page.locator('#textFrame').waitFor();
 for(const width of [1440,375]){
  await page.setViewportSize({width,height:1000});
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'horizontal overflow');
  const small=await page.locator('[data-panel="4"] button,[data-panel="4"] input:not([type=checkbox]),[data-panel="4"] select').evaluateAll(ns=>ns.filter(n=>n.getClientRects().length).filter(n=>n.getBoundingClientRect().height<44||n.getBoundingClientRect().width<44).map(n=>n.id));
  check(!small.length,'short controls: '+small);
  await page.locator('[data-text-role="emphasis"]').click();
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:`output/text-style-${width===375?'mobile':'desktop'}.png`,fullPage:true});
 }
 console.log(JSON.stringify({project,checks:['no media load','direct text selection','per-role parameters','Rust readback/reload','no revision on layer selection','invalid size/import preserved','undo/redo','overflow warning without truncation','375/1440 layout','44px targets']}));
}
