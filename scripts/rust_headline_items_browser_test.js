async(page)=>{
 const base='http://127.0.0.1:8871',project='hl-items-'+Date.now();
 const check=(v,m)=>{if(!v)throw Error(m)};
 const saved=()=>page.waitForFunction(()=>['บันทึกแล้ว','พร้อมแก้ไข'].includes(document.querySelector('#saved').textContent),null,{polling:100});
 const read=async()=>await(await page.request.get(`${base}/api/v1/drafts/${project}`)).json();
 await page.goto(`${base}/?project=${project}`);await page.setViewportSize({width:1440,height:1100});
 await page.locator('[data-step="5"]').click();await saved();
 check(await page.locator('[data-text-role=headline]').count()===0,'new HL must be empty');
 for(let i=0;i<3;i++){await page.locator('#addHeadline').click();await saved();await page.locator('#text-text').fill('หัวข้อ '+(i+1));await saved();}
 let state=await read(),items=state.document.headline_items;
 check(items.length===3&&new Set(items.map(v=>v.id)).size===3,'independent IDs missing');
 const middle=page.locator(`[data-text-id="${items[1].id}"]`);
 await middle.click();let box=await middle.boundingBox();
 await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2+25,box.y+box.height/2+35,{steps:5});await page.mouse.up();await saved();
 state=await read();check(state.document.headline_items[1].y!==items[1].y,'HL drag not saved');
 check(JSON.stringify(state.document.headline_items[0])===JSON.stringify(items[0]),'drag changed another text');
 const before=state,handle=page.locator('#textResize');box=await handle.boundingBox();
 await page.mouse.move(box.x+22,box.y+22);await page.mouse.down();await page.mouse.move(box.x+62,box.y+52,{steps:5});await page.mouse.up();await saved();
 state=await read();check(state.document.headline_items[1].size>before.document.headline_items[1].size,'resize failed');check(state.revision===before.revision+1,'resize not one edit');
 const resized=state.document.headline_items[1];
 await handle.focus();await page.keyboard.press('ArrowLeft');await saved();check((await read()).document.headline_items[1].size===resized.size-1,'keyboard resize failed');
 await page.locator('#removeHeadline').click();await saved();check((await read()).document.headline_items.every(v=>v.id!==resized.id),'delete failed');
 await page.locator('#undo').click();await saved();check((await read()).document.headline_items[1].id===resized.id,'undo lost ID');
 await page.reload();await page.locator('[data-text-role=headline]').first().waitFor();check(await page.locator('[data-text-role=headline]').count()===3,'reload lost items');
 for(const width of [1440]){await page.setViewportSize({width,height:1100});check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'overflow');await page.screenshot({path:`output/headline-items-${width}.png`,fullPage:true});}
 console.log(JSON.stringify({project,checks:['three independent items','drag','resize one revision','keyboard resize','delete','undo stable ID','reload','desktop/mobile']}));
}
