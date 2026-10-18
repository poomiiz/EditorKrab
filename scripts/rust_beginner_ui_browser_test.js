async(page)=>{
 const base='http://127.0.0.1:8871',project='beginner-ui-'+Date.now();
 const check=(v,m)=>{if(!v)throw Error(m)};
 const saved=()=>page.waitForFunction(()=>['บันทึกแล้ว','พร้อมแก้ไข'].includes(document.querySelector('#saved').textContent),null,{polling:100});
 const read=async()=>await(await page.request.get(`${base}/api/v1/drafts/${project}`)).json();
 await page.goto(`${base}/?project=${project}`);await page.setViewportSize({width:1440,height:1100});
 check((await page.locator('[data-step] small').allTextContents()).join('|')==='ใส่วิดีโอ|ส่ง AI|วางคำตอบ|ซับ|หัวคลิป|รับคลิป','beginner rail labels missing');
 await page.locator('[data-step="4"]').click();await saved();
 check(!await page.locator('#textControls').isVisible(),'empty inspector shows fields');
 check(await page.locator('#textSelectionHint').isVisible(),'empty instruction missing');
 check(await page.locator('#textFrame').innerText()==='','unchecked frame not blank');
 await page.locator('[data-role-option=speech]').check();await saved();
 check(await page.locator('#textControls').isVisible(),'selected text controls hidden');
 check(await page.locator('#sampleLabel').textContent()==='ข้อความตัวอย่าง','captions not labeled as sample');
 check(!await page.locator('#text-color-hex').isVisible(),'advanced HEX starts open');
 await page.locator('#text-text').fill('ข้อความพูดตัวอย่าง');await saved();
 await page.locator('[data-panel="4"] [data-go="5"]').click();await saved();
 check(await page.locator('#headlineTitle').isVisible(),'next step failed');
 check(!await page.locator('#textControls').isVisible(),'empty headline shows controls');
 check(!await page.locator('#headlineReply').isVisible(),'optional AI expanded by default');
 await page.locator('#addHeadline').click();await saved();await page.locator('#text-text').fill('เริ่มจากสิ่งที่มี');await saved();
 check(await page.locator('#sampleLabel').textContent()==='ข้อความบนหัวคลิป','headline input unclear');
 await page.locator('#text-font').fill('Arial');await saved();await page.locator('#text-color').fill('#24cbaa');await saved();
 await page.locator('#addHeadline').click();await saved();await page.locator('#text-text').fill('ข้อความนิ่ง ปรับเอง');await saved();
 await page.locator('#text-color').fill('#e67a3c');await saved();
 const before=await read();
 await page.locator('#headlineAi > summary').click();check(await page.locator('#headlineReply').isVisible(),'optional AI inaccessible');
 await page.locator('#headlineAi > summary').click();
 check((await read()).revision===before.revision,'disclosure changed draft');
 for(const width of [1440,375]){
  await page.setViewportSize({width,height:1100});
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'page overflow');
  check(await page.locator('[data-step],#addHeadline,#text-font,#text-color').evaluateAll(nodes=>nodes.every(n=>n.getBoundingClientRect().height>=44&&n.getBoundingClientRect().width>=44)),'small targets');
  check(await page.locator('[data-step] small').evaluateAll(nodes=>nodes.every(n=>n.scrollWidth<=n.parentElement.clientWidth)),'rail label overflow');
  check(await page.locator('[data-panel="5"] button.primary:visible').count()===1,'multiple primary actions');
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:`output/beginner-after-${width}.png`,fullPage:true});
 }
 check(await page.locator('[data-step="4"][data-done]').count()===1&&await page.locator('[data-step="5"][aria-current=step]').count()===1,'finished step not marked');
 const navBefore=(await read()).revision;await page.locator('[data-step="2"]').click();await page.locator('[data-step="5"]').click();
 check((await read()).revision===navBefore,'step navigation created revision');
 await page.locator('#undo').click();await saved();check(await page.locator('#headlineTitle').isVisible(),'undo changed step');
 await page.locator('#redo').click();await saved();
 await page.locator('[data-panel="5"] .back').click();check(await page.locator('#captionsTitle').isVisible(),'back failed');await page.locator('[data-step="5"]').click();
 await page.locator('[data-panel="5"] [data-go="6"]').click();await saved();
 check((await page.locator('[data-panel="6"]').innerText()).includes('ยังสร้างคลิปไม่ได้'),'unimplemented output masked as missing prerequisites');
 await page.reload();await page.locator('#createTitle').waitFor();
 check((await read()).document.headline_items.length===2,'reload lost headline data');
 await page.locator('[data-step="1"]').click();await saved();
 for(const width of [1440,375]){await page.setViewportSize({width,height:1100});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'start overflow');await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:`output/beginner-start-${width}.png`,fullPage:true});}
 console.log(JSON.stringify({project,checks:['plain labels','six horizontal stages','no blank disabled inspector','checkbox-only overlays','sample/headline meaning','advanced/AI collapsed but accessible','one primary','next focus/navigation','draft unchanged by disclosure','reload preserves data','truthful unavailable output','375px/desktop']}));
}
