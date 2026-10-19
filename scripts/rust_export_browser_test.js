async(page)=>{
 // Needs a running app whose project already has a transcript and a pasted AI plan (EDITORKRAB_EXPORT_DIR set).
 const base=process.env.EK_BASE||'http://127.0.0.1:18898',project=process.env.EK_PROJECT||'flow4-th';
 const check=(ok,message)=>{if(!ok)throw Error(message)};
 await page.goto(`${base}/?project=${project}`);
 await page.waitForFunction(()=>document.body.dataset.loading==='false');
 await page.locator('[data-step="6"]').click();
 await page.waitForFunction(()=>!document.querySelector('#exportStart').disabled);
 check(await page.locator('[data-panel="6"] .primary:visible').count()===1,'step 6 needs exactly one primary action');
 check(!(await page.getByText('ยังสร้างคลิปไม่ได้').count()),'placeholder still shown');
 await page.locator('#exportStart').click();
 await page.waitForFunction(()=>/สร้างเสร็จ \d+ คลิป/.test(document.querySelector('#exportStatus').textContent)||!document.querySelector('#exportError').hidden,null,{timeout:600000});
 check(await page.locator('#exportError').isHidden(),'export failed: '+await page.locator('#exportError').textContent());
 const status=await (await page.request.get(`${base}/api/v1/projects/${project}/export`)).json();
 check(status.state==='done'&&status.files.length===status.stories,'not every story produced a file');
 check(await page.locator('#exportFiles li').count()===status.files.length,'file list does not match');
 check(await page.locator('#exportReveal').isVisible(),'no way to open the folder');
 await page.screenshot({path:'output/native-export.png',fullPage:true});
 console.log(JSON.stringify({project,files:status.files.map(f=>f.split(/[\\/]/).pop()),checks:['one primary action','placeholder gone','export done','file per story','open folder offered']}));
}
