async (page) => {
 const check=(v,m)=>{if(!v)throw Error(m)};
 await page.goto('http://127.0.0.1:8871/?project=design-b-'+Date.now());
 await page.waitForFunction(()=>document.body.dataset.loading==='false');
 await page.locator('#title').fill('โปรเจกต์ใหม่');
 await page.waitForFunction(()=>['บันทึกแล้ว','พร้อมแก้ไข'].includes(document.querySelector('#saved').textContent),null,{polling:100});
 for(const width of [1440]){
  await page.setViewportSize({width,height:1000});
  const bounds=await page.locator('[data-step]').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,w:r.width,h:r.height}}));
  check(bounds.every((r,i)=>r.h>=44&&(!i||r.x>bounds[i-1].x)),'step rail order/touch targets');
  for(let step=1;step<=6;step++){
   await page.locator(`[data-step="${step}"]`).click();
   check(await page.locator(`[data-panel="${step}"]`).isVisible(),'panel not visible');
   check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'horizontal overflow');
  }
  await page.locator('[data-step="1"]').click();
  const small=await page.locator('button,input:not([type=file]),select,summary').evaluateAll(ns=>ns.filter(n=>n.getClientRects().length).filter(n=>n.getBoundingClientRect().height<44).map(n=>n.id||n.textContent));
  check(!small.length,'short targets: '+small);
  check(await page.locator('#empty').isVisible(),'empty state absent');
  await page.screenshot({path:`output/impeccable-after-${width===375?'mobile':'desktop'}.png`,fullPage:true});
 }
 await page.emulateMedia({reducedMotion:'reduce'});
 check(await page.locator('#pick').evaluate(n=>getComputedStyle(n).transitionDuration)==='0s','reduced motion');
 await page.locator('#title').focus();
 await page.keyboard.press('Tab');
 check(await page.locator(':focus').evaluate(n=>getComputedStyle(n).outlineStyle)!=='none','keyboard focus');
 console.log('PASS: six panels, left-to-right rail, 1440 overflow, 44px controls, empty state, reduced motion, keyboard focus');
}
