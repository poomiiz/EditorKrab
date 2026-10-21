// Generate a runner replacing __FIXTURE_PATH_JSON__ with a disposable WAV's JSON path.
async (page) => {
  const fixture = __FIXTURE_PATH_JSON__;
  const project = 'jobs-ui-' + Date.now();
  const base = 'http://127.0.0.1:8871';
  const check = (value,message) => {if (!value) throw Error(message);};
  await page.goto(base+'/?project='+project);
  await page.locator('#title').fill('ทดสอบสถานะสด');
  await page.waitForFunction(()=>document.getElementById('saved').textContent==='บันทึกแล้ว',null,{polling:100});
  await page.getByText('ความคืบหน้างาน',{exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('jobProgress').textContent.includes('อัปเดตสด'),null,{polling:100});
  const draft=await (await page.request.get(base+'/api/v1/drafts/'+project)).json();
  const response=await page.request.post(base+'/api/v1/projects/'+project+'/jobs',{data:{request_id:'probe',revision:draft.revision,
    items:[{key:'missing',task:{kind:'probe',path:fixture+'.missing'}},{key:'valid',task:{kind:'probe',path:fixture}}]}});
  check(response.status()===200,'job creation failed');
  const jobs=await response.json();
  await page.waitForFunction(()=>document.getElementById('jobProgress').textContent.includes('อ่านวิดีโอสำเร็จ 1/2'),null,{polling:100});
  check(await page.getByText('ไม่พบวิดีโอต้นฉบับ',{exact:true}).isVisible(),'actionable failure missing');
  await page.getByRole('button',{name:'ลองใหม่',exact:true}).click();
  await page.waitForFunction(async project=>{
    const jobs=await (await fetch('/api/v1/projects/'+project+'/jobs')).json();
    return jobs[0].attempt===2 && jobs[0].status==='failed';
  },project,{polling:100});
  const after=await (await page.request.get(base+'/api/v1/projects/'+project+'/jobs')).json();
  check(after.length===2 && after[1].attempt===1,'retry repeated a successful item');
  await page.waitForFunction(()=>document.getElementById('jobProgress').textContent.includes('ไม่สำเร็จ'),null,{polling:100});
  await page.locator('#title').fill('เปลี่ยนร่าง');
  await page.waitForFunction(()=>document.getElementById('saved').textContent==='บันทึกแล้ว',null,{polling:100});
  check(await page.getByText('ร่างก่อนหน้า',{exact:true}).count()===2,'revision badge did not refresh');
  await page.setViewportSize({width:1280,height:1100});
  check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile overflow');
  await page.screenshot({path:'output/rust-jobs-mobile.png',fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:'output/rust-jobs-desktop.png',fullPage:true});
  console.log(JSON.stringify({project,jobs:jobs.map(j=>j.id),checks:['SSE progress','failed source message','retry only failed','revision refresh','1280px layout']}));
}
