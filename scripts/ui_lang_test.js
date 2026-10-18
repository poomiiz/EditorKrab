// The TH/EN switch wins; with no choice saved, the page follows the browser's language.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app/static/ui.js','utf8');
function start(saved,languages,language) {
  const store={...(saved === undefined ? {} : {ck_lang:saved})},root={lang:'th',classList:{add(){},remove(){}}};
  const context={window:{},navigator:{languages,language},setTimeout(){},console,
    localStorage:{getItem:key=>key in store ? store[key] : null,setItem(key,value){store[key]=value;}},
    fetch:()=>new Promise(()=>{}),location:{reload(){}},
    document:{head:{insertAdjacentHTML(){}},documentElement:root,body:null,addEventListener(){}}};
  vm.runInNewContext(source,context);
  return {lang:context.window.ckLang,html:root.lang,store,set:context.window.ckSetLang};
}
assert.equal(start(undefined,['th-TH','en-US'],'th-TH').lang,'th');
assert.equal(start(undefined,undefined,'th').lang,'th');
assert.equal(start(undefined,['th_TH'],'th_TH').lang,'th');
assert.equal(start(undefined,['thx'],'thx').lang,'en');
assert.equal(start(undefined,['en-US','th'],'en-US').lang,'en');
assert.equal(start(undefined,['ja-JP'],'ja-JP').lang,'en');
assert.equal(start(undefined,[],'').lang,'en');
assert.equal(start('en',['th-TH'],'th-TH').lang,'en','a saved English choice beats a Thai browser');
assert.equal(start('th',['en-US'],'en-US').lang,'th','a saved Thai choice beats an English browser');
assert.equal(start('xx',['th-TH'],'th-TH').lang,'th','an unknown saved value falls back to the browser');
assert.equal(start(undefined,['en-US'],'en-US').html,'en');
assert.equal(start(undefined,['th-TH'],'th-TH').html,'th');
const page=start(undefined,['th-TH'],'th-TH');page.set('en');assert.equal(page.store.ck_lang,'en');
console.log('PASS ui language: saved choice first, then the browser language');
