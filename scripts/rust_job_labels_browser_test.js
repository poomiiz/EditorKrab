async(page)=>{
 await page.goto('http://127.0.0.1:8871/?project=checkbox-proof-'+Date.now());await page.locator('[data-step="4"]').click();
 await page.waitForFunction(()=>['บันทึกแล้ว','พร้อมแก้ไข'].includes(document.querySelector('#saved').textContent),null,{polling:100});
 for(const width of [1440,375]){await page.setViewportSize({width,height:1050});await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:`output/six-step-captions-${width===375?'mobile':'desktop'}.png`,fullPage:true});}
 const result=await page.evaluate(async()=>{
  const actual=window.fetch,RealSource=window.EventSource;
  const base={batch_id:'b',project_id:'mixed',attempt:1,cancel_requested:false,created_at:0,revision:1};
  const jobs=[{...base,id:'probe',status:'succeeded',task:{kind:'probe',path:'file.wav'},result:{duration_s:11}},
   {...base,id:'asr',status:'running',step:'transcribing',task:{kind:'transcribe',path:'file.wav'}},
   {...base,id:'asr-fail',status:'failed',error:'transcription_settings_changed',task:{kind:'transcribe',path:'file.wav'}}];
  window.fetch=async()=>new Response(JSON.stringify(jobs),{headers:{'x-job-cursor':'1'}});
  window.EventSource=class extends EventTarget{close(){}};
  const node=document.createElement('div');node.id='mixedProof';document.body.append(node);
  const {watchJobs}=await import('/jobs.js');const watcher=watchJobs('mixed',()=>1,node);
  try {await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);return node.textContent;}
  finally{watcher.close();node.remove();window.fetch=actual;window.EventSource=RealSource;}
 });
 if(!result.includes('อ่านวิดีโอสำเร็จ 1/1')||!result.includes('ถอดเสียงสำเร็จ 0/2 ช่วง')||!result.includes('ตัวถอดเสียงเปลี่ยนแล้ว'))throw Error('mixed job labels invalid: '+result);
}
