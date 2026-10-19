async(page)=>{
 const base='http://127.0.0.1:18872',project='native-browser-check';
 const check=(ok,message)=>{if(!ok)throw Error(message)};
 const read=async id=>(await page.request.get(`${base}/api/v1/drafts/${id}`)).json();
 await page.goto(`${base}/?project=${project}`);
 await page.waitForFunction(()=>document.querySelector('#transcribeState').textContent==='ยังใช้ไม่ได้');
 check(await page.locator('#transcribe').isDisabled(),'missing model allowed start');
 await page.waitForFunction(()=>!document.querySelector('#asrDownload').hidden);
 check(/^ดาวน์โหลดตัวถอดเสียง \(\d+\.\d GB\)$/.test(await page.locator('#asrDownloadStart').textContent()),'missing model has no download button');
 check(await page.locator('#asrDownloadStart').evaluate(b=>b.classList.contains('primary')&&!b.disabled),'download is not the one primary action');
 check(await page.locator('.primary:visible').count()===1,'more than one primary action while the model is missing');
 check(await page.locator('#transcribeSetup').count()===0,'old setup explainer still present');
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
 // In-app download (fixture serves the tiny model locally): progress, then usable without a restart.
 await page.locator('[data-step="1"]').click();
 await page.locator('#asrDownloadStart').click();
 await page.waitForFunction(()=>document.querySelector('#transcribeState').textContent!=='ยังใช้ไม่ได้',null,{timeout:60000});
 check(await page.locator('#asrDownload').isHidden(),'download panel stayed after success');
 const settings=await (await page.request.get(`${base}/api/v1/transcription/settings`)).json();
 check(settings&&/ggml-tiny\.bin$/.test(settings.model.path),'downloaded model not switched on');
 const status=await (await page.request.get(`${base}/api/v1/transcription/download`)).json();
 check(status.state==='done','download status not done');
 console.log(JSON.stringify({project,restoredId,recoveredId,checks:['missing model truthful disabled state','single primary download button with size','recheck settings','packet disabled','plain backup without origin restored','IndexedDB failure preserves snapshot via acknowledged server copy','in-app download switches transcription on without restart']}));
}
