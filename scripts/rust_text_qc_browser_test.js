// Placing subtitles runs the text check; flagged lines can be heard, then left or retyped.
async(page)=>{
 const base='http://127.0.0.1:8871',errors=[];
 const check=(ok,message)=>{if(!ok)throw Error(message)};
 const fs=require('fs'),path=__FIXTURE_PATH_JSON__,st=fs.statSync(path,{bigint:true});
 const saved=()=>page.waitForFunction(()=>['บันทึกแล้ว','พร้อมแก้ไข'].includes(document.querySelector('#saved').textContent),null,{timeout:30000});
 const summary=text=>page.waitForFunction(t=>document.querySelector('#textQcSummary').textContent.includes(t),text,{timeout:15000});
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(`${base}/?project=qc-${Date.now()}`);await saved();
 await page.locator('[data-step="4"]').click();await summary('ยังไม่มีซับ');
 await page.locator('#title').fill('qc');await saved();
 let project=new URL(page.url()).searchParams.get('project');
 const original=await(await page.request.get(`${base}/api/v1/drafts/${project}`)).json();
 const file={source_id:'talk1',name:'talk.mp4',path,size:Number(st.size),modified_ns:st.mtimeNs.toString()};
 const headline={id:'h1',text:'ดู Youtube',font:'system',size:80,color:'#ececf1',x:50,y:16};
 await page.locator('#openFile').setInputFiles({name:'d.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({...original.document,step:1,files:[file],headline_items:[headline]}))});
 await saved();
 await page.locator('[data-step="1"]').click();project=new URL(page.url()).searchParams.get('project');
 await page.waitForFunction(()=>!document.querySelector('#transcribe').disabled,null,{timeout:30000});
 await page.locator('#transcribe').click();
 await page.waitForFunction(()=>document.querySelector('#transcribeState').textContent==='ถอดครบแล้ว',null,{timeout:60000});
 const line=page.locator('#transcriptRows textarea').nth(1);await line.fill('เราขออนุญาติคุยเรื่องกาแฟกัน');await line.blur();await saved();
 // Entering the subtitle step checks without a button.
 await page.locator('[data-step="4"]').click();await summary('ต้องฟัง 2 จุด');
 const rows=page.locator('#textQcList > li');
 check((await rows.allTextContents()).every(r=>!r.includes('สวัสดีครับ')),'passing line listed');
 const heads=await page.locator('#textQcList .qc-head').allTextContents();
 check(heads.every(r=>/^\d\d:\d\d\.\d–\d\d:\d\d\.\d/.test(r)),'time format '+heads);
 const sub=rows.filter({hasText:'อนุญาติ'});check(await sub.locator('button',{hasText:'ฟัง'}).count()===1,'no listen on subtitle');
 await sub.locator('button',{hasText:'แก้'}).click();
 check(await sub.locator('.qc-edit input').inputValue()==='เราขออนุญาตคุยเรื่องกาแฟกัน','fix not prefilled');
 await sub.locator('.qc-edit button.primary').click();await saved();await summary('ต้องฟัง 1 จุด');
 const doc=(await(await page.request.get(`${base}/api/v1/drafts/${project}`)).json()).document;
 check(doc.transcript_edits.some(e=>e.text==='เราขออนุญาตคุยเรื่องกาแฟกัน'),'subtitle edit not saved');
 const head=rows.filter({hasText:'Youtube'});await head.locator('button',{hasText:'แก้'}).click();
 await head.locator('.qc-edit button.primary').click();await saved();await summary('ผ่านทั้งหมด');
 const after=(await(await page.request.get(`${base}/api/v1/drafts/${project}`)).json()).document;
 check(after.headline_items[0].text==='ดู YouTube','headline edit not saved');
 check(!errors.length,'page errors '+errors.join(';'));
}
