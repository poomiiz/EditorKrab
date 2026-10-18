// EditorKrab app shell: draws the sidebar on every page and marks where you are.
(() => {
  if (window !== window.top) return;   // embedded inside the editor: no second sidebar
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    if (response.status === 401) location.replace('/login.html');
    return response;
  };
  const I = {
    home: '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>',
    clip: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 9l5 3-5 3z"/>',
    cover: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>',
    motion: '<path d="M4 7h10M4 12h16M4 17h7"/><path d="M18 4l1 2 2 1-2 1-1 2-1-2-2-1 2-1z"/>',
    style: '<path d="M4 20l4-1 11-11-3-3L5 16z"/><path d="M14 6l3 3"/>',
    drafts: '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    bug: '<rect x="8" y="6" width="8" height="14" rx="4"/><path d="M9 6a3 3 0 016 0M3 13h5M16 13h5M4 8l4 2M20 8l-4 2M4 19l4-2M20 19l-4-2"/>',
    restart: '<path d="M20 12a8 8 0 11-2.3-5.7"/><path d="M20 4v5h-5"/>',
    power: '<path d="M12 3v9"/><path d="M6.3 6.3a8 8 0 1011.4 0"/>',
    undo: '<path d="M9 14l-5-5 5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z"/>',
  };
  window.CLIPKIT_ICONS = I;
  document.head.insertAdjacentHTML('beforeend', '<link rel="icon" href="/logo.svg?v=editorkrab-crab" type="image/svg+xml">');
  const svg = k => `<svg viewBox="0 0 24 24">${I[k]}</svg>`;
  const NAV = [
    // starting or continuing a clip lives on the home tiles; the sidebar only moves between pages
    ['', [['home', 'หน้าแรก', '/video-editor.html'], ['style', 'คลังสไตล์', '/styles.html'], ['drafts', 'ทีมตัดต่อ', '/team.html'], ['clip', 'ตรวจงานทีม', '/review.html']]],
  ];
  const here = location.pathname + location.hash;
  const isOn = href => href === here || (href === location.pathname && !location.hash && !href.includes('#'));
  const aside = document.createElement('nav');
  aside.className = 'sb';
  aside.innerHTML = `<a class="sb-brand" href="/video-editor.html"><img class="sb-logo" src="/logo.svg?v=editorkrab-crab" alt="">
      <span class="sb-name">EditorKrab<small>ตัดคลิป · จัดงานทีม</small></span></a>` +
    '<button class="sb-fold" id="sbFold" title="ย่อ / ขยายแถบซ้าย"><svg viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg></button>' +
    NAV.map(([g, items]) => (g ? `<div class="sb-group">${g}</div>` : '<div style="height:8px"></div>') + items.map(([k, label, href]) =>
      `<a class="sb-item${isOn(href) ? ' on' : ''}" href="${href}" title="${label}">${svg(k)}<span>${label}</span></a>`).join('')).join('') +
    `<button type="button" class="sb-item" id="ckCreateProject" title="สร้างโปรเจกต์ใหม่">${svg('plus')}<span>สร้างโปรเจกต์</span></button>` +
    '<div class="sb-foot" id="sbVer">EditorKrab</div>';
  aside.querySelector('a[href="/styles.html"]').after(aside.querySelector('#ckCreateProject'));
  document.body.prepend(aside);
  // team review is for leads and admins: hide its links for everyone else (a failed check keeps them)
  fetch('/api/team/review-access').then(r => r.ok ? r.json() : null).then(j => {
    if (j && j.allowed === false) document.querySelectorAll('a[href="/review.html"]').forEach(a => { a.hidden = true; });
  }).catch(() => {});
  const openPicked = picked => {
    if (typeof window.openPicked === 'function') window.openPicked(picked);
    else location.href = '/video-editor.html?' + new URLSearchParams({[picked.kind === 'folder' ? 'folder' : 'raw']: picked.path});
  };
  document.getElementById('ckCreateProject').onclick = async () => {
    if (typeof window.openPicker === 'function') { window.openPicker(); return; }
    try {
      const response = await fetch('/api/kit/pick', {method: 'POST'});
      const picked = await response.json();
      if (!response.ok) throw new Error(picked.detail || 'เลือกไฟล์ไม่ได้');
      if (picked.path) openPicked(picked);
    } catch (e) { ckAlert(ckErr(e)); }
  };
  // One page-wide drop handler serves home, styles and team pages.
  let innerDrag = false;
  document.addEventListener('dragstart', () => { innerDrag = true; });
  document.addEventListener('dragend', () => { innerDrag = false; });
  document.addEventListener('dragover', e => {
    if (innerDrag || ![...e.dataTransfer.types].includes('Files')) return;
    e.preventDefault(); document.body.classList.add('dropping');
  });
  document.addEventListener('dragleave', e => { if (!e.relatedTarget) document.body.classList.remove('dropping'); });
  document.addEventListener('drop', async e => {
    document.body.classList.remove('dropping');
    if (innerDrag) { innerDrag = false; return; }
    if (!e.dataTransfer.files.length) return;
    e.preventDefault();
    const item = e.dataTransfer.items?.[0], entry = item?.webkitGetAsEntry?.(), file = e.dataTransfer.files[0];
    try {
      const response = await fetch('/api/kit/locate', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: file.name, size: entry?.isDirectory ? -1 : file.size})});
      const picked = await response.json();
      if (!response.ok) throw new Error(picked.detail || 'หาไฟล์ที่วางไม่พบ');
      openPicked(picked);
    } catch (e) { ckAlert(ckErr(e)); }
  });
  // app-style icon bar, top right: report a bug, settings, restart, shut down
  const top = document.createElement('div');
  top.className = 'ck-top';
  top.innerHTML = `<a href="https://github.com/poomiiz/EditorKrab/issues/new" target="_blank" rel="noopener noreferrer" id="sbBug" title="แจ้งปัญหา">${svg('bug')}</a>` +
    `<a href="/settings.html" title="ตั้งค่า"${isOn('/settings.html') ? ' class="on"' : ''}>${svg('settings')}</a>` +
    `<a href="#" id="ckLang" class="ck-lang" translate="no" title="${window.ckLang === 'en' ? 'ภาษาไทย' : 'English'}">${window.ckLang === 'en' ? 'TH' : 'EN'}</a>`;
  top.querySelector('#ckLang').onclick = e => { e.preventDefault(); window.ckSetLang(window.ckLang === 'en' ? 'th' : 'en'); };
  const pageTop = document.querySelector('.top');   // a page header: sit at its right end, else float top right
  (pageTop || document.body).append(top);
  top.classList.toggle('float', !pageTop);
  // Manual system controls: closing a page no longer owns the server lifetime.
  const controls = document.createElement('div');
  controls.className = 'sb-controls';
  controls.innerHTML = '<div class="sb-control-buttons"><button type="button" id="ckRestart" title="รีสตาร์ต EditorKrab">' + svg('restart') + '</button>' +
    '<button type="button" id="ckShutdown" title="ปิดระบบ EditorKrab (หน่วง 1 นาที)">' + svg('power') + '</button>' +
    '<button type="button" id="ckCancelShutdown" title="ยกเลิกการปิด" hidden>' + svg('undo') + '</button></div>' +
    '<div id="ckSystemStatus" role="status" aria-live="polite"></div>';
  aside.insertBefore(controls, document.getElementById('sbVer'));
  const systemStatus = document.getElementById('ckSystemStatus');
  const systemButtons = controls.querySelectorAll('button');
  // the optional helper (config.json "bot_api_url") can restart a hung server; remembered from the last health check
  let helper = '';
  try { helper = localStorage.getItem('ck.helper') || ''; } catch {}
  const health = () => fetch('/api/window/health', {cache: 'no-store', signal: AbortSignal.timeout(2000)})
    .then(async r => {
      const h = r.ok ? await r.json() : null;
      if (h) { helper = h.helper || ''; try { localStorage.setItem('ck.helper', helper); } catch {} }
      return h;
    }).catch(() => null);
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  // the server is gone: close the window (the EditorKrab icon opens a fresh one); a window the browser
  // will not let a script close shows one plain line instead of a page whose lists and covers fail
  const stopped = () => {
    window.ckLoadProjects = () => {};
    window.close();
    document.body.innerHTML = '<div style="position:fixed;inset:0;display:grid;place-items:center;background:#0b0e16;' +
      'color:#c5cde0;font-size:16px;text-align:center;padding:24px">ปิด EditorKrab แล้ว<br>' +
      '<span style="color:#828ca4;font-size:14px">เปิดอีกครั้งจากไอคอน EditorKrab หรือ widget MoonRacle</span></div>';
  };
  async function systemControl(action) {
    if ([...systemButtons].some(b => b.disabled)) return;
    systemButtons.forEach(b => b.disabled = true);
    try {
      const before = await health();
      if ((!before || before.busy) && !await ckConfirm((action === 'restart' ? 'รีสตาร์ต' : 'ปิด') +
          ' EditorKrab? งานที่กำลังประมวลผลจะหยุดเมื่อครบ 1 นาที')) return;
      systemStatus.textContent = action === 'restart' ? 'กำลังรีสตาร์ต…' : 'กำลังตั้งเวลาปิด · ยกเลิกได้ใน 1 นาที';
      let accepted, queued;
      try {
        const response = await fetch('/api/window/' + action + '?control=v2', {method: 'POST', signal: AbortSignal.timeout(3000)});
        if (!response.ok) throw new Error('HTTP ' + response.status);
        accepted = await response.json();
        if (action === 'shutdown' && accepted.scheduled_in) {
          cancelShutdown.hidden = false;
          systemStatus.textContent = 'จะปิดใน 1 นาที · กด ↩ เพื่อยกเลิก';
          return;
        }
      } catch (error) {
        // The dashboard's host worker remains available when this server is hung.
        if (location.port !== '8770' || !helper) throw error;
        const response = await fetch(helper + '/bot/api/video-editor/control', {
          method: 'POST', headers: {'Content-Type': 'application/json'}, signal: AbortSignal.timeout(5000),
          body: JSON.stringify({action, request_id: crypto.randomUUID()})
        });
        if (!response.ok) throw new Error('ส่งคำสั่งกู้คืนไม่ได้ (' + response.status + ')');
        queued = await response.json();
        systemStatus.textContent = 'รอคำสั่งจากเครื่อง · ไม่เกิน 1 นาที';
      }
      const deadline = Date.now() + (queued ? 90000 : 30000);
      while (Date.now() < deadline) {
        await pause(1000);
        if (queued) {
          const response = await fetch(helper + '/bot/api/video-editor/control/' + queued.request_id,
            {cache: 'no-store', signal: AbortSignal.timeout(3000)});
          if (!response.ok) throw new Error('อ่านผลคำสั่งไม่ได้ (' + response.status + ')');
          const result = await response.json();
          if (result.state === 'failed' || result.state === 'superseded') throw new Error(result.error || 'คำสั่งไม่สำเร็จ');
          if (result.state !== 'succeeded') continue;
          if (action === 'shutdown') { stopped(); return; }
        }
        const current = await health();
        if (action === 'shutdown' && !current) { stopped(); return; }
        if (action === 'restart' && current?.ready && current.instance !== (accepted?.instance ?? before?.instance)) {
          location.reload();
          return;
        }
      }
      throw new Error('ยังยืนยันผลไม่ได้ ลองอีกครั้งหรือใช้ปุ่มบนหน้า MoonRacle');
    } catch (error) {
      systemStatus.textContent = 'ไม่สำเร็จ: ' + ckErr(error);
    } finally {
      systemButtons.forEach(b => b.disabled = false);
    }
  }
  document.getElementById('ckRestart').onclick = () => systemControl('restart');
  const cancelShutdown = document.getElementById('ckCancelShutdown');
  cancelShutdown.onclick = async () => {
    try {
      const response = await fetch('/api/window/cancel-shutdown?control=v2', {method: 'POST'});
      if (!response.ok) throw new Error('ยกเลิกไม่สำเร็จ');
      cancelShutdown.hidden = true;
      systemStatus.textContent = 'ยกเลิกการปิดแล้ว';
    } catch (error) { systemStatus.textContent = ckErr(error); }
  };
  document.getElementById('ckShutdown').onclick = () => systemControl('shutdown');
  // End manual system controls.
  top.append(controls);
  // fold the sidebar to icons only (remembered on this machine)
  let mini = false;
  try { mini = localStorage.getItem('ck_sb_mini') === '1'; } catch {}
  document.documentElement.classList.toggle('sb-mini', mini);
  document.getElementById('sbFold').onclick = () => {
    mini = !mini;
    document.documentElement.classList.toggle('sb-mini', mini);
    try { localStorage.setItem('ck_sb_mini', mini ? '1' : '0'); } catch {}
  };
  // drag the right edge to widen the sidebar (remembered on this machine)
  const SB_MIN = 180, SB_MAX = 520;
  let sbW = 0;
  const setW = w => { sbW = Math.min(SB_MAX, Math.max(SB_MIN, w)); document.documentElement.style.setProperty('--sb-user-w', sbW + 'px'); };
  try { const w = +localStorage.getItem('ck_sb_w'); if (w) setW(w); } catch {}
  const grip = document.createElement('div');
  grip.className = 'sb-grip';
  grip.title = 'ลากเพื่อขยาย / ย่อแถบซ้าย · ดับเบิลคลิก = ขนาดเดิม';
  aside.append(grip);
  grip.onpointerdown = e => {
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    document.documentElement.classList.add('sb-drag');
    const w0 = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--sb-w')), x0 = e.clientX;
    grip.onpointermove = ev => setW(w0 + ev.clientX - x0);
    grip.onpointerup = () => {
      grip.onpointermove = grip.onpointerup = null;
      document.documentElement.classList.remove('sb-drag');
      try { if (sbW) localStorage.setItem('ck_sb_w', Math.round(sbW)); } catch {}
    };
  };
  grip.ondblclick = () => {
    document.documentElement.style.removeProperty('--sb-user-w');
    try { localStorage.removeItem('ck_sb_w'); } catch {}
  };
  // a new version installed while this page was open: one small bar to reload, nothing reloads by itself
  let seenCommit = null;
  setInterval(() => fetch('/api/kit/version').then(r => r.json()).then(v => {
    if (seenCommit === null) { seenCommit = v.commit; return; }
    if (v.commit !== seenCommit && !document.getElementById('ckReload')) {
      const bar = document.createElement('div');
      bar.id = 'ckReload';
      bar.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:200;background:#1d2236;border:1px solid #7c5cff;' +
        'border-radius:10px;padding:8px 12px;color:#f1f4fb;font-size:13.5px;display:flex;gap:10px;align-items:center';
      bar.innerHTML = 'หน้านี้มีเวอร์ชันใหม่ <button style="background:#7c5cff;color:#fff;border:0;border-radius:7px;padding:4px 12px;cursor:pointer;font:inherit">รีเฟรช</button>';
      bar.querySelector('button').onclick = () => location.reload();
      document.body.appendChild(bar);
    }
  }).catch(() => {}), 30000);
  document.body.classList.add('shell');
  // repaint the active item when video-editor switches screens through the hash
  window.addEventListener('hashchange', () => {
    const now = location.pathname + location.hash;
    aside.querySelectorAll('.sb-item').forEach(a => a.classList.toggle('on',
      a.getAttribute('href') === now || (!location.hash && a.getAttribute('href') === location.pathname)));
  });
  // new version on GitHub: one banner at the top when the app opens, one click to update
  if (!sessionStorage.getItem('ck_upd_checked')) {
    sessionStorage.setItem('ck_upd_checked', '1');
    fetch('/api/kit/update-check').then(r => r.json()).then(u => {
      if (!u.available) return;
      const bar = document.createElement('div');
      bar.style.cssText = 'position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:200;background:#1d2236;' +
        'border:1px solid #7c5cff;border-radius:12px;padding:10px 14px;display:flex;gap:12px;align-items:center;' +
        'color:#f1f4fb;font-size:14px;box-shadow:0 8px 30px rgba(0,0,0,.5);max-width:92vw';
      bar.innerHTML = '<span>มี EditorKrab เวอร์ชันใหม่ (' + u.count + ' รายการ)</span>' +
        '<button style="background:#7c5cff;color:#fff;border:0;border-radius:8px;padding:6px 14px;font:inherit;font-weight:600;cursor:pointer">อัปเดต</button>' +
        '<button style="background:none;border:0;color:#828ca4;cursor:pointer;font:inherit">ภายหลัง</button>';
      bar.title = (u.changes || []).join('\n');
      const [go, later] = bar.querySelectorAll('button');
      later.onclick = () => bar.remove();
      go.onclick = async () => {
        go.disabled = true; go.textContent = 'กำลังอัปเดต…';
        try {
          const r = await fetch('/api/kit/update', {method: 'POST'});
          if (!r.ok) throw new Error((await r.json()).detail);
          let j;
          do { await new Promise(x => setTimeout(x, 1500)); j = await fetch('/api/kit/job/update').then(x => x.json()); }
          while (j.status === 'running');
          if (j.status !== 'done') throw new Error((j.log || '').trim().split('\n').pop());
          bar.firstChild.textContent = '✅ อัปเดตแล้ว · ปิดแล้วเปิด EditorKrab ใหม่ให้ครบทุกส่วน';
          go.remove(); later.textContent = 'ปิด';
        } catch (e) { bar.firstChild.textContent = '❌ อัปเดตไม่สำเร็จ: ' + ckErr(e); go.disabled = false; go.textContent = 'ลองอีกครั้ง'; }
      };
      document.body.appendChild(bar);
    }).catch(() => {});
  }
  fetch('/api/kit/version').then(r => r.ok ? r.json() : null).then(v => {
    if (v) document.getElementById('sbVer').innerHTML = `เวอร์ชัน <b>${v.version || ''}</b>${v.commit ? ' · ' + v.commit : ''}`;
  }).catch(() => {});
})();
