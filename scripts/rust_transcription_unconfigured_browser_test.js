async(page)=>{
 const base='http://127.0.0.1:18872',project='native-browser-check';
 const check=(ok,message)=>{if(!ok)throw Error(message)};
 const read=async id=>(await page.request.get(`${base}/api/v1/drafts/${id}`)).json();
 await page.goto(`${base}/?project=${project}`);
 await page.waitForFunction(()=>document.querySelector('#transcribeState').textContent==='ยังใช้ไม่ได้');
 check(await page.locator('#transcribe').isDisabled(),'missing model allowed start');
 check(await page.locator('#transcribeSetup').isVisible(),'missing model has no next step');
 await page.locator('#reloadAsr').click();
 await page.waitForFunction(()=>document.querySelector('#transcribeState').textContent==='ยังใช้ไม่ได้');
 await page.locator('[data-step="2"]').click();check(await page.locator('#transcriptPacket').isDisabled(),'missing transcript produced packet');
 const before=await read(project),backup={schema_version:1,project_id:'missing-origin',document:before.document};
 await page.locator('#openFile').setInputFiles({name:'plain-restore.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(backup))});
 await page.waitForURL(url=>url.searchParams.get('project')!==project);
 await page.waitForFunction(()=>document.querySelector('body').dataset.loading==='false');
 const restoredId=new URL(page.url()).searchParams.get('project');
 check((await read(restoredId)).document.files.length===2,'plain backup requires missing origin');
 // Simulate only this test tab's local storage write failure; Rust remains reachable.
 await page.evaluate(()=>{const transaction=IDBDatabase.prototype.transaction;IDBDatabase.prototype.transaction=function(...args){if(args[1]==='readwrite')throw new DOMException('fixture','QuotaExceededError');return transaction.apply(this,args)}});
 await page.locator('#title').fill('กู้ snapshot เมื่อพื้นที่บันทึกพัง');
 await page.waitForFunction(()=>document.querySelector('#saved').textContent==='บันทึกไม่สำเร็จ');
 await page.locator('#fork').click();await page.waitForURL(url=>![project,restoredId].includes(url.searchParams.get('project')));
 const recoveredId=new URL(page.url()).searchParams.get('project');
 check((await read(recoveredId)).document.title==='กู้ snapshot เมื่อพื้นที่บันทึกพัง','volatile recovery lost in-memory snapshot');
 await page.waitForFunction(()=>document.querySelector('#transcribeState').textContent==='ยังใช้ไม่ได้');
 await page.screenshot({path:'output/native-transcription-unconfigured.png',fullPage:true});
 console.log(JSON.stringify({project,restoredId,recoveredId,checks:['missing model truthful disabled state','recheck settings','packet disabled','plain backup without origin restored','IndexedDB failure preserves snapshot via acknowledged server copy']}));
}
