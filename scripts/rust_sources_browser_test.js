async (page) => {
  const base='http://127.0.0.1:8871';
  const project='sources-ui-'+Date.now();
  const check=(condition,message)=>{if (!condition) throw Error(message);};
  await page.goto(base+'/?project='+project);
  await page.locator('#title').fill('ร่างที่ต้องรักษา');
  await page.waitForFunction(()=>document.getElementById('saved').textContent==='บันทึกแล้ว',null,{polling:100});
  const original=await (await page.request.get(base+'/api/v1/drafts/'+project)).json();
  const source={source_id:'source1',name:'ทดสอบ.wav',path:'fixture.wav',size:12,modified_ns:'123'};
  for (const files of [
    [{...source,path:'bad\0.wav'}],
    [source,{...source,path:'another.wav'}],
    [{...source,modified_ns:''}],
    [{...source,path:'  '}]
  ]) {
    await page.locator('#openFile').setInputFiles({name:'invalid.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({...original.document,files}))});
    await page.waitForFunction(()=>document.getElementById('error').textContent.includes('เปิดไม่ได้'),null,{polling:100});
    check(await page.locator('#title').inputValue()===original.document.title,'invalid import replaced current draft');
    const stored=await (await page.request.get(base+'/api/v1/drafts/'+project)).json();
    check(stored.revision===original.revision,'invalid import reached durable outbox');
    await page.locator('#openFile').setInputFiles([]);
  }
  await page.locator('#title').fill('แก้ต่อได้หลังปฏิเสธ');
  await page.waitForFunction(()=>document.getElementById('saved').textContent==='บันทึกแล้ว',null,{polling:100});
  const updated=await (await page.request.get(base+'/api/v1/drafts/'+project)).json();
  check(updated.document.title==='แก้ต่อได้หลังปฏิเสธ','outbox blocked after invalid import');

  // An authoritative replacement must clear consumers as well as the jobs list.
  const result=await page.evaluate(async()=>{
    const {watchJobs}=await import('/jobs.js');
    const actualFetch=window.fetch, ActualEventSource=window.EventSource;
    let stream;
    window.EventSource=class extends EventTarget {constructor(){super();stream=this;}close(){}};
    const job={id:'job',task:{path:'fixture.wav',source:{id:'source1'}},status:'succeeded',revision:1,result:{duration_s:1}};
    window.fetch=async()=>new Response(JSON.stringify([job]),{headers:{'x-job-cursor':'1'}});
    const rows=new Map();let replacements=0;
    const container=document.createElement('div');
    const watcher=watchJobs('fixture',()=>1,container,j=>rows.set(j.id,j),()=>{replacements++;rows.clear();});
    try {
      for (let n=0;n<50 && !stream;n++) await new Promise(resolve=>setTimeout(resolve,10));
      if (rows.size!==1) throw Error('snapshot missing');
      stream.dispatchEvent(new MessageEvent('reset',{data:'[]'}));
      return {remaining:rows.size,replacements};
    } finally {watcher.close();window.fetch=actualFetch;window.EventSource=ActualEventSource;}
  });
  check(result.remaining===0 && result.replacements===2,'reset retained old source metadata');
  await page.setViewportSize({width:375,height:1000});
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile overflow');
  await page.screenshot({path:'output/rust-sources-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:'output/rust-sources-desktop.png',fullPage:true});
  console.log(JSON.stringify({project,checks:['invalid imports preserve draft','outbox remains editable','snapshot reset clears metadata','375px layout']}));
}
