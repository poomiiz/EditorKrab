// Run with playwright-cli run-code --filename scripts/rust_autosave_browser_test.js
async (page) => {
  const origin = 'http://127.0.0.1:8871';
  const project = 'browser-autosave-' + Date.now();
  const address = origin + '/?project=' + project;
  const check = (value, message) => { if (!value) throw Error(message); };
  const until = async (target, predicate) => {
    await target.bringToFront();
    return target.waitForFunction(predicate, null, {polling:100});
  };
  const saved = async target => until(target, () => document.getElementById('saved').textContent === 'บันทึกแล้ว')
    .catch(async () => { throw Error('save did not settle: ' + await target.locator('#saved').textContent()); });
  const document = async () => (await (await page.request.get(origin + '/api/v1/drafts/' + project)).json());
  await page.goto(address);
  await page.locator('#title').fill('ร่างต้นฉบับ');
  await saved(page);
  check((await document()).document.title === 'ร่างต้นฉบับ', 'UI change not persisted in Rust');

  const offline = route => route.abort('connectionfailed');
  await page.route('**/api/v1/drafts/**', async route => {
    if (route.request().method() === 'PUT') await offline(route); else await route.continue();
  });
  await page.locator('nav button').nth(2).click();
  await page.locator('#plan').fill('{ยังพิมพ์ไม่จบ');
  await until(page, () => document.getElementById('saved').textContent === 'รอเชื่อมต่อ');
  await page.reload();
  await page.locator('#plan').waitFor({state:'visible'});
  check(await page.locator('#plan').inputValue() === '{ยังพิมพ์ไม่จบ', 'offline input lost on reload');
  await page.unroute('**/api/v1/drafts/**');
  await saved(page);
  check((await document()).document.plan === '{ยังพิมพ์ไม่จบ', 'offline outbox not replayed');

  let committed;
  await page.route('**/api/v1/drafts/**', async route => {
    if (route.request().method() !== 'PUT') return route.continue();
    const response = await route.fetch();
    committed = await response.json();
    await route.abort('connectionfailed');
  });
  await page.locator('#plan').fill('{lost-response');
  await until(page, () => document.getElementById('saved').textContent === 'รอเชื่อมต่อ');
  check(Number.isInteger(committed?.revision), 'lost-ack request did not commit');
  await page.unroute('**/api/v1/drafts/**');
  await page.reload(); await saved(page);
  check((await document()).revision === committed.revision, 'lost acknowledgement created another revision');
  check(await page.locator('#plan').inputValue() === '{lost-response', 'lost-ack document changed');

  await page.evaluate(() => {
    document.getElementById('undo').click();
    const transfer = new DataTransfer();
    transfer.items.add(new File(['clip'],'drop-during-undo.mp4',{type:'video/mp4'}));
    document.getElementById('drop').dispatchEvent(new DragEvent('drop',{dataTransfer:transfer,cancelable:true}));
  });
  await until(page, () => document.getElementById('plan').value === '{ยังพิมพ์ไม่จบ'); await saved(page);
  check(await page.locator('#plan').inputValue() === '{ยังพิมพ์ไม่จบ', 'undo failed');
  check(!(await document()).document.files.some(f => f.name === 'drop-during-undo.mp4'), 'drop bypassed history lock');
  await page.locator('#redo').click();
  await until(page, () => document.getElementById('plan').value === '{lost-response'); await saved(page);
  check(await page.locator('#plan').inputValue() === '{lost-response', 'redo failed');

  const second = await page.context().newPage();
  try {
    await second.goto(address);
    await second.locator('#plan').waitFor({state:'visible'});
    await until(second, () => !document.getElementById('plan').disabled);
    await page.bringToFront();
    await page.locator('#plan').fill('winner'); await saved(page);
    await second.bringToFront();
    await second.locator('#plan').fill('stale tab');
    await until(second, () => document.getElementById('saved').textContent === 'งานชนกัน')
      .catch(async () => { throw Error('stale tab status: ' + await second.locator('#saved').textContent()); });
    check((await document()).document.plan === 'winner', 'stale tab overwrote newer draft');
    check(await second.locator('#fork').isVisible(), 'conflict recovery missing');
  } finally { await second.close(); }

  await page.evaluate(() => localStorage.setItem('editorkrab-rust-flow-prototype-v1','{broken legacy'));
  await page.reload();
  check(await page.locator('#plan').inputValue() === 'winner', 'legacy corruption blocked current recovery');

  await page.locator('nav button').nth(5).click(); await saved(page); await page.reload();
  await page.locator('[data-panel="6"]').waitFor({state:'visible'});
  const corruptProject = project + '-corrupt';
  const recovered = await page.evaluate(async id => {
    const {DraftSession} = await import('/autosave.js');
    const db = await new Promise((resolve,reject) => {
      const request = indexedDB.open('editorkrab-drafts-v1',1);
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
    });
    await new Promise((resolve,reject) => {
      const tx = db.transaction('drafts','readwrite');
      tx.objectStore('drafts').put({id,generation:0,revision:0,document:{text:'keep'},undo:[],redo:[],
        pending:[{operation_id:'corrupt',expected_revision:99,document:{text:'must not send'}}]});
      tx.oncomplete = resolve; tx.onabort = () => reject(tx.error);
    });
    const session = new DraftSession(id,{text:'blank'},()=>{});
    let rejected = false;
    try { await session.open(); } catch (_) { rejected = true; } finally { session.close(); }
    const kept = await new Promise((resolve,reject) => {
      const request = db.transaction('drafts').objectStore('drafts').get(id);
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
    });
    db.close(); return {rejected,kept};
  },corruptProject);
  check(recovered.rejected && recovered.kept.pending[0].expected_revision === 99, 'corrupt queue not preserved');
  check((await page.request.get(origin + '/api/v1/drafts/' + corruptProject)).status() === 404, 'corrupt queue reached backend');
  await page.setViewportSize({width:375,height:900});
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'mobile overflow');
  await page.screenshot({path:'output/rust-autosave-mobile.png', fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:'output/rust-autosave-desktop.png', fullPage:true});
  console.log(JSON.stringify({project,revision:(await document()).revision,
    checks:['Rust readback','offline reload/replay','lost acknowledgement retry','undo/redo',
      'stale tab rejected','malformed legacy recovery','corrupt queue quarantined','step 06 restore','375px layout']}));
}
