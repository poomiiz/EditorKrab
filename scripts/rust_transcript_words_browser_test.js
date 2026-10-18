// Word chips light up during line playback; hand fixes are learned and applied to other lines.
async(page)=>{
 const base='http://127.0.0.1:8871',errors=[];let project='words-'+Date.now();
 const check=(ok,message)=>{if(!ok)throw Error(message)};
 const fs=require('fs'),path=__FIXTURE_PATH_JSON__,st=fs.statSync(path,{bigint:true});
 const saved=()=>page.waitForFunction(()=>['บันทึกแล้ว','พร้อมแก้ไข'].includes(document.querySelector('#saved').textContent),null,{timeout:30000});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(`${base}/?project=${project}`);await saved();
 await page.locator('#title').fill('คำ');await saved();
 const original=await(await page.request.get(`${base}/api/v1/drafts/${project}`)).json();
 const file={source_id:'talk1',name:'talk.mp4',path,size:Number(st.size),modified_ns:st.mtimeNs.toString()};
 await page.locator('#openFile').setInputFiles({name:'d.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({...original.document,files:[file]}))});
 await saved();
 await page.waitForFunction(()=>!document.querySelector('#transcribe').disabled,null,{timeout:30000});
 await page.locator('#transcribe').click();
 await page.waitForFunction(()=>document.querySelector('#transcribeState').textContent==='ถอดครบแล้ว',null,{timeout:60000});
 const rows=page.locator('#transcriptRows li');check(await rows.count()===2,'expected 2 lines');
 check(await rows.first().locator('.transcript-words span').count()>=2,'no word chips');
 // Hand fix on line 1 is learned, then a learned pair applies to the same mistake elsewhere.
 const field=rows.first().locator('textarea');const before=await field.inputValue();
 await field.focus();await field.fill(before.replace('โอเคคัป','โอเคครับ'));await field.blur();await saved();
 const fixed=await(await page.request.post(`${base}/api/v1/spelling/fix`,{data:{lines:['โอเคคัป']}})).json();
 check(fixed.lines[0]==='โอเคครับ','learned fix not applied: '+fixed.lines[0]);
 // Undoing the hand fix leaves the line unedited; the learned fix is offered, not applied.
 await page.locator('#undo').click();await saved();await page.reload();
 await page.waitForFunction(()=>document.querySelector('#transcribeState').textContent==='ถอดครบแล้ว',null,{timeout:60000});
 const offer=page.locator('#transcriptRows .transcript-suggest').first();await offer.waitFor({timeout:10000});
 check(!(await rows.first().locator('textarea').inputValue()).includes('โอเคครับ'),'learned fix applied without asking');
 await offer.click();await saved();
 check((await rows.first().locator('textarea').inputValue()).includes('โอเคครับ'),'suggestion not applied');
 // Opening a draft file starts its own project.
 project=new URL(page.url()).searchParams.get('project');
 const pauses=await(await page.request.get(`${base}/api/v1/projects/${project}/sources/talk1/pauses?start_ms=0&end_ms=5000`)).json();
 check(pauses.pauses?.length>=1&&pauses.word_pad_ms===100,'pauses '+JSON.stringify(pauses));
 check(!errors.length,'page errors '+errors.join(';'));
}
