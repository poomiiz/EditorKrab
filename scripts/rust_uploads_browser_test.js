// Replace __FIXTURE_PATH_JSON__ with output/rust-upload-test.wav's absolute JSON path.
async (page) => {
  const fixture=__FIXTURE_PATH_JSON__;
  const base='http://127.0.0.1:8871';
  const project='uploads-ui-'+Date.now();
  const check=(value,message)=>{if (!value) throw Error(message);};
  const until=predicate=>page.waitForFunction(predicate,null,{polling:100});
  const document=async()=> (await (await page.request.get(base+'/api/v1/drafts/'+project)).json());
  const chunks=/\/sources\/uploads\/[^/?]+\?offset=/;
  await page.goto(base+'/?project='+project);
  await page.locator('#title').fill('ตรวจลากวางและส่งต่อ');
  await until(()=>window.document.getElementById('saved').textContent==='บันทึกแล้ว');
  await page.evaluate(()=>window.document.getElementById('files').addEventListener('change',event=>{window.uploadFixture=event.target.files[0];}));
  let lost=false,firstAttempts=0;
  await page.route(chunks,async route=>{
    const offset=Number(new URL(route.request().url()).searchParams.get('offset'));
    if (offset===0) {
      firstAttempts++;
      if (!lost) {lost=true;await route.fetch();await route.abort('connectionfailed');return;}
      return route.continue();
    }
    return route.abort('connectionfailed');
  });
  await page.locator('#files').setInputFiles(fixture);
  await until(()=>window.document.getElementById('sourceStatus').textContent==='รับไฟล์ยังไม่ครบ');
  await until(()=>window.document.getElementById('saved').textContent==='บันทึกแล้ว');
  const pending=(await document()).document.files[0];
  check(pending.upload_id && !pending.source_id,'incomplete bytes became a usable source');
  check(firstAttempts===2,'lost chunk acknowledgement was not replayed');

  // Same browser name, size and mtime, different bytes: must fail the prefix check.
  await page.unroute(chunks);
  await page.evaluate(async()=>{
    const original=window.uploadFixture;
    const bytes=new Uint8Array(await original.arrayBuffer());bytes[100]^=255;
    const file=new File([bytes],original.name,{type:original.type,lastModified:original.lastModified});
    const transfer=new DataTransfer();transfer.items.add(file);
    window.document.getElementById('drop').dispatchEvent(new DragEvent('drop',{dataTransfer:transfer,cancelable:true}));
  });
  await until(()=>window.document.getElementById('error').textContent.includes('ไฟล์ที่เลือกไม่ตรง'));
  check((await document()).document.files.length===1,'wrong replay duplicated the source');
  await page.reload();
  await page.locator('.file-info').filter({hasText:'ลากไฟล์เดิมมาวางเพื่อส่งต่อ'}).waitFor();
  await page.evaluate(()=>window.document.getElementById('files').addEventListener('change',event=>{window.uploadFixture=event.target.files[0];}));
  await page.locator('#files').setInputFiles(fixture);
  await until(()=>window.document.getElementById('sourceStatus').textContent==='รับแล้ว 1 ไฟล์');
  await until(()=>window.document.getElementById('saved').textContent==='บันทึกแล้ว');
  const complete=(await document()).document.files[0];
  check(complete.source_id===pending.upload_id && complete.path,'reload did not resume the same upload');
  await page.waitForFunction(async project=>{
    const jobs=await (await fetch('/api/v1/projects/'+project+'/jobs')).json();
    return jobs.length===1 && jobs[0].status==='succeeded';
  },project,{polling:100});

  // A real drop re-verifies bytes and reuses the completed source/probe.
  await page.evaluate(()=>{
    const transfer=new DataTransfer();transfer.items.add(window.uploadFixture);
    window.document.getElementById('drop').dispatchEvent(new DragEvent('drop',{dataTransfer:transfer,cancelable:true}));
  });
  await until(()=>window.document.getElementById('cancelUpload').hidden && window.document.getElementById('sourceStatus').textContent==='รับแล้ว 1 ไฟล์');
  await until(()=>window.document.getElementById('saved').textContent==='บันทึกแล้ว');
  const repeated=await document();
  check(repeated.document.files.length===1 && repeated.document.files[0].source_id===complete.source_id,'repeated selection created a copy');
  check((await (await page.request.get(base+'/api/v1/projects/'+project+'/jobs')).json()).length===1,'repeated upload created another probe');
  await until(()=>window.document.getElementById('fileList').textContent.includes('วินาที'));
  await page.setViewportSize({width:1280,height:1100});
  check(await page.evaluate(()=>window.document.documentElement.scrollWidth<=innerWidth),'mobile overflow');
  await page.screenshot({path:'output/rust-uploads-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:'output/rust-uploads-desktop.png',fullPage:true});
  const cancelledProject=project+'-cancel';
  await page.goto(base+'/?project='+cancelledProject);
  await page.locator('#title').fill('หยุดรับหลายไฟล์');
  await until(()=>window.document.getElementById('saved').textContent==='บันทึกแล้ว');
  let held=false,release;
  const gate=new Promise(resolve=>{release=resolve;});
  await page.route(chunks,async route=>{held=true;await gate;await route.abort('connectionfailed').catch(()=>{});});
  await page.locator('#files').setInputFiles(['one.wav','two.wav','three.wav'].map(name=>({name,mimeType:'audio/wav',buffer:Buffer.from('test pending bytes')})));
  for (let n=0;n<100 && !held;n++) await new Promise(resolve=>setTimeout(resolve,10));
  check(held,'cancel fixture request did not start');
  await page.locator('#cancelUpload').click();release();
  await page.locator('#cancelUpload').waitFor({state:'hidden'});
  await until(()=>window.document.getElementById('saved').textContent==='บันทึกแล้ว');
  const cancelled=await (await page.request.get(base+'/api/v1/drafts/'+cancelledProject)).json();
  check(cancelled.document.files.length===3 && cancelled.document.files.every(f=>f.upload_id && !f.source_id),'cancel lost the remaining selected files');
  await page.unroute(chunks);await page.reload();
  await page.locator('.file-info').filter({hasText:'ลากไฟล์เดิมมาวางเพื่อส่งต่อ'}).first().waitFor();
  check(await page.locator('#fileList li').count()===3,'reload lost queued selection');
  console.log(JSON.stringify({project,cancelledProject,source:complete.source_id,checks:['lost chunk ack retry','pending upload durable','wrong bytes rejected','reload/reselect resumes','native probe','repeated selection idempotent','cancel retains all selected files','1280px layout']}));
}
