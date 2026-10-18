// EditorKrab shared UI helpers, loaded in <head> of every page (also inside the editor iframe):
// friendly error text and in-app dialogs instead of the browser's alert / confirm / prompt boxes.
(() => {
  // what a person reads when something failed: the server's own message, or a plain sentence for app bugs
  window.ckErr = e => {
    const msg = (e && e.message) || String(e);
    if (e instanceof TypeError && /fetch|network|load failed/i.test(msg))
      return 'ติดต่อ EditorKrab ไม่ได้ ตรวจว่าโปรแกรมยังเปิดอยู่ แล้วลองอีกครั้ง';
    if (e instanceof TypeError || e instanceof ReferenceError || e instanceof SyntaxError || e instanceof RangeError) {
      return 'หน้านี้เกิดข้อผิดพลาด ลองรีเฟรชหรือแจ้งผ่าน GitHub';
    }
    return msg;
  };

  const css = `.ck-dlg{border:1px solid #2f3139;border-radius:12px;padding:0;background:#16171b;color:#ececf1;width:min(420px,92vw);
    box-shadow:0 24px 64px rgba(0,0,0,.55);font:14px/1.55 "Inter","Segoe UI Variable Text","Segoe UI","Leelawadee UI",sans-serif}
  .ck-dlg::backdrop{background:rgba(0,0,0,.55)}
  .ck-dlg form{display:flex;flex-direction:column;gap:14px;padding:18px}
  .ck-dlg p{margin:0;white-space:pre-line;overflow-wrap:anywhere;max-height:50vh;overflow:auto}
  .ck-dlg .ck-dlg-quote{color:#b4b6c0;font-weight:600}
  .ck-dlg input{font:inherit;color:inherit;background:#1d1f24;border:1px solid #2a2c33;border-radius:8px;padding:8px 10px;width:100%;box-sizing:border-box}
  .ck-dlg input:focus{outline:none;border-color:#7c5cff}
  .ck-dlg .ck-dlg-row{display:flex;flex-direction:row-reverse;justify-content:flex-start;gap:8px}  /* ok first in the DOM: Enter submits ok */
  .ck-dlg button{font:inherit;font-size:13.5px;font-weight:600;min-height:34px;padding:6px 14px;border-radius:8px;cursor:pointer;
    border:1px solid #2a2c33;background:#1d1f24;color:#ececf1}
  .ck-dlg button[value=ok]{background:#6c4cf0;border-color:#6c4cf0;color:#fff}
  .ck-dlg.danger button[value=ok]{background:#dc2626;border-color:#dc2626}
  .ck-dlg button:focus-visible{outline:2px solid #b9a8ff;outline-offset:2px}`;
  document.head.insertAdjacentHTML('beforeend', `<style>${css}</style>`);

  // one dialog for all three; resolves true / false, or the typed text / null
  // quote: the person's own words (a title, a name) shown under the question and never translated
  const ask = (text, {input = null, ok = 'ตกลง', cancel = 'ยกเลิก', danger = false, quote = ''} = {}) => new Promise(resolve => {
    const d = document.createElement('dialog');
    d.className = 'ck-dlg' + (danger ? ' danger' : '');
    d.innerHTML = '<form method="dialog"><p></p>' + (quote ? '<p class="ck-dlg-quote" translate="no"></p>' : '') + (input === null ? '' : '<input>') +
      '<div class="ck-dlg-row"><button value="ok"></button>' + (cancel ? '<button value="cancel"></button>' : '') + '</div></form>';
    d.querySelector('p').textContent = text;
    if (quote) d.querySelector('.ck-dlg-quote').textContent = quote;
    d.querySelector('[value=ok]').textContent = ok;
    if (cancel) d.querySelector('[value=cancel]').textContent = cancel;
    const field = d.querySelector('input');
    if (field) field.value = input;
    d.addEventListener('close', () => {
      d.remove();
      const yes = d.returnValue === 'ok';
      resolve(input === null ? yes : (yes ? field.value : null));
    });
    document.body.append(d);
    d.showModal();
    if (field) field.select(); else d.querySelector(danger && cancel ? '[value=cancel]' : '[value=ok]').focus();   // a delete starts on Cancel
  });
  window.ckConfirm = (text, opt) => ask(text, opt);
  window.ckPrompt = (text, value = '') => ask(text, {input: value});
  window.ckAlert = text => ask(text, {cancel: ''});
})();

// Language: the pages are written in Thai; English comes from a phrase table (/i18n/en.json, Thai fragment -> English)
// applied to the rendered page, so text the code builds later (toasts, lists, dialogs) is translated too.
// People's own text is left alone: typed fields, translate="no", and any text still holding Thai after the swap.
(() => {
  let lang = null;
  try { lang = localStorage.getItem('ck_lang'); } catch (e) { /* private window: follow the browser */ }
  // the TH/EN switch wins; until it is used, a Thai browser gets Thai and any other language gets English
  if (lang !== 'th' && lang !== 'en') lang = /^th([-_]|$)/i.test((navigator.languages || [])[0] || navigator.language || '') ? 'th' : 'en';
  window.ckLang = lang;
  window.ckSetLang = next => { try { localStorage.setItem('ck_lang', next); } catch (e) { /* not kept */ } location.reload(); };
  if (lang !== 'en') return;
  const root = document.documentElement;
  root.lang = 'en';
  root.classList.add('ck-i18n-wait');   // hide the Thai page for the moment the table takes to load
  document.head.insertAdjacentHTML('beforeend', '<style>html.ck-i18n-wait body{visibility:hidden}' +
    'body.dropping::after{content:"Drop video files or folders here"!important}</style>');
  const reveal = () => root.classList.remove('ck-i18n-wait');
  const TH = /[฀-๿]/, ATTRS = ['title', 'placeholder', 'aria-label', 'alt'];
  const SKIP = 'script,style,textarea,[contenteditable],[translate=no]';
  fetch('/i18n/en.json').then(r => { if (!r.ok) throw new Error('i18n ' + r.status); return r.json(); }).then(dict => {
    const keys = Object.keys(dict).sort((a, b) => b.length - a.length);
    const re = new RegExp(keys.map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'g');
    // all or nothing: if Thai is left after the swap, the text holds someone's own Thai words (a subtitle, a name), keep it whole
    // Thai runs words together, English needs a space where two swapped pieces meet
    const tr = s => {
      if (!TH.test(s)) return s;
      const t = s.replace(re, m => '\u0001' + dict[m] + '\u0001');
      if (TH.test(t)) return s;
      return t.replace(/([\w.,)!?])\u0001+(?=[\w(])|\u0001+([\w(])/g, (x, a, b) => a ? a + ' ' : x).replace(/\u0001/g, '')
        .replace(/\b(\d+) Projects?\b/g, (x, n) => n + (n === '1' ? ' project' : ' projects'));
    };
    const el = n => n.nodeType === 1 ? n : n.parentElement;
    const doNode = n => {
      const e = el(n);
      if (!e || e.closest(SKIP)) return;
      if (n.nodeType === 3) { const t = tr(n.nodeValue); if (t !== n.nodeValue) n.nodeValue = t; return; }
      for (const a of ATTRS) if (n.hasAttribute(a)) { const v = n.getAttribute(a), t = tr(v); if (t !== v) n.setAttribute(a, t); }
      if ((n.tagName === 'INPUT' && (n.type === 'button' || n.type === 'submit'))) n.value = tr(n.value);
    };
    const doTree = top => {
      doNode(top);
      if (top.nodeType !== 1) return;
      const w = document.createTreeWalker(top, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
      while (w.nextNode()) doNode(w.currentNode);
    };
    const start = () => {
      document.title = tr(document.title);
      doTree(document.body);
      reveal();
      new MutationObserver(list => {
        for (const m of list) {
          if (m.type === 'childList') m.addedNodes.forEach(doTree);
          else doNode(m.target);
        }
      }).observe(document.body, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS});
    };
    if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
  }).catch(e => { reveal(); console.error(e); });   // no table: the page stays Thai rather than blank
  setTimeout(reveal, 3000);
})();
