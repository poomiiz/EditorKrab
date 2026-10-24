import assert from 'node:assert/strict';
import {captionJoin,captionRows,fittedCaptionSize,captionMeasure,captionWidth,defaults} from '../design/rust-flow/text-style.js';
import {validateEnglishCueReply,englishCueAt,captionInputRevision,resolveWordSelection,captionKeptLine,validateEmphasisSelections,cameraTurns,cameraPersonAt,emphasisGroups,emphasisCueWindows,quietTimingKey} from '../design/rust-flow/stories.js';
assert.equal(captionJoin('Hello,','world!'),'Hello, world!');
const line={segment_id:'a',start_ms:100,end_ms:1100,words:[{text:'ดี',start_ms:100,end_ms:300},{text:'ดี',start_ms:800,end_ms:1000}]};
assert.deepEqual(resolveWordSelection(line,['w1']),{text:'ดี',start_ms:800,end_ms:1000});
for(const ids of [['w0','w0'],['w1','w0'],['w2'],['w01'],[]])assert.equal(resolveWordSelection(line,ids),null);
assert.equal(resolveWordSelection({...line,start_ms:200},['w0']),null);
const revision=captionInputRevision('p',{},[line]);
assert.equal(revision,captionInputRevision('p',{},[line]));
assert.notEqual(revision,captionInputRevision('other',{},[line]));
assert.equal(revision,captionInputRevision('p',{dead_min_ms:300,quiet:{source:[[0,100]]}},[line]),'analysis settings cannot invalidate identical public word anchors');
assert.notEqual(revision,captionInputRevision('p',{},[{...line,text:'แก้'}]));
assert.notEqual(revision,captionInputRevision('p',{},[{...line,words:line.words.map(w=>({...w,start_ms:w.start_ms+1}))}]));
const rows=captionRows(line.words,{},2200,()=>10);
assert.equal(rows.length,2,'separate speech beats after a pause');
const text='WWWWWWWWWWWWWWWW',style={font:'tahoma',size:125,x:32,spacing:100};
const fit=fittedCaptionSize(text,style);
assert.ok(captionMeasure(text,{...style,size:fit,spacing:style.spacing*fit/style.size})<=captionWidth(style));
assert.equal(defaults().speech.enter,undefined,'new work stays still until an animation is chosen');
const trimmed=captionKeptLine({...line,start_ms:700});
assert.equal(trimmed.words.length,1);
const packet=trimmed.words.map((word,i)=>({id:'w'+i,...word}));
assert.deepEqual(resolveWordSelection(trimmed,[packet[0].id]),{text:'ดี',start_ms:800,end_ms:1000});
assert.equal(resolveWordSelection(trimmed,['w1']),null);
assert.equal(resolveWordSelection({...line,words:[line.words[1],line.words[0]]},['w0','w1']),null);
assert.ok(fittedCaptionSize('test',{size:160,x:50,y:95},9/16)<70,'keep letters inside short frames');
console.log('Caption exchange and layout checks passed');

const zeroLine={segment_id:'s16',start_ms:50660,end_ms:52840,words:[{text:'สัก',start_ms:52480,end_ms:52840},{text:'10',start_ms:52840,end_ms:52840}]};
assert.equal(resolveWordSelection(zeroLine,['w1']),null,'never invent a clock for 10');
assert.equal(resolveWordSelection(zeroLine,['w0','w1']),null,'a zero-duration phrase endpoint must not borrow the preceding word clock');
const boundedZero={segment_id:'bounded',start_ms:0,end_ms:1000,words:[{text:'a',start_ms:100,end_ms:200},{text:'b',start_ms:200,end_ms:200},{text:'c',start_ms:200,end_ms:500}]};
assert.deepEqual(resolveWordSelection(boundedZero,['w0','w1','w2']),{text:'a b c',start_ms:100,end_ms:500});
assert.equal(resolveWordSelection(boundedZero,['w1','w2']),null,'a missing first anchor must be reviewed');
const partial=validateEmphasisSelections({a:[{word_ids:['w1']}],s16:[{word_ids:['w1']}]},[{...line,segment_id:'a'},zeroLine]);
assert.equal(partial.count,1);assert.equal(partial.issues[0].text,'10');assert.equal(partial.picked.a[0].start_ms,800);
const voice={faces:[{},{}],seen:[{start_ms:0,end_ms:9000,person:0},{start_ms:10000,end_ms:12000,person:0},{start_ms:14000,end_ms:16000,person:0},{start_ms:16000,end_ms:17000,person:1},{start_ms:18000,end_ms:19000,person:1},{start_ms:19000,end_ms:33000,person:0},{start_ms:41000,end_ms:42000,person:1}]};
assert.equal(cameraTurns(voice).length,3,'adjacent same-face detections form stable shots');
assert.equal(cameraPersonAt(voice,17000),1);
assert.equal(cameraPersonAt(voice,42000),null,'ignore isolated mouth movement');
assert.equal(cameraPersonAt(voice,35000),null,'return wide after unknown gap');

assert.doesNotThrow(()=>validateEmphasisSelections({a:[{word_ids:'w5'}]},[{...line,segment_id:'a'}]));
assert.doesNotThrow(()=>validateEmphasisSelections({a:[{word_ids:['w0'],text:10}]},[{...line,segment_id:'a'}]));
const mixed=validateEmphasisSelections({s16:[{word_ids:['w0']},{word_ids:['w1']}]},[zeroLine]);
assert.equal(mixed.picked,undefined,'failed line must not replace prior picks with a partial set');
console.log('Reported zero-duration selection and camera cadence regressions passed');

assert.equal(cameraPersonAt({faces:[{}],seen:[],turns:[{start_ms:0,end_ms:10000,speaker:0}]},3000),null,'uncertain visual analysis stays wide');

const phraseLine={segment_id:'a',words:[{text:'Brand',start_ms:1000,end_ms:1100},{text:'Manager',start_ms:1230,end_ms:1500},{text:'today',start_ms:3000,end_ms:3300}]};
const legacy=phraseLine.words.slice(0,2);
assert.equal(emphasisGroups(phraseLine,legacy).length,1,'small alignment gaps do not split a complete selected phrase');
assert.equal(emphasisGroups(phraseLine,legacy.map((x,i)=>({...x,word_ids:['w'+i]}))).length,2,'explicit separate selections retain identity');
const groups=emphasisGroups(phraseLine,legacy);
assert.deepEqual(emphasisCueWindows([{segment_id:'a',start_ms:0,end_ms:1200,groups},{segment_id:'a',start_ms:1200,end_ms:5000,groups}]),[{start_ms:1000,end_ms:1680,text:'Brand Manager',segment_id:'a'}]);
assert.deepEqual(emphasisCueWindows([{segment_id:'a',start_ms:0,end_ms:1200,groups},{segment_id:'a',start_ms:1230,end_ms:5000,groups}]),[{start_ms:1000,end_ms:1650,text:'Brand Manager',segment_id:'a'}]);
assert.notEqual(quietTimingKey(1000,4,[],[[100,300]]),quietTimingKey(1000,4,[],[[100,400]]),'retiming invalidates quiet analysis');
assert.deepEqual(validateEmphasisSelections({a:[{word_ids:['w0','w1']}]},[{...phraseLine,start_ms:0,end_ms:5000}]).picked.a[0].word_ids,['w0','w1']);
console.log('Phrase identity, playback-clock emphasis and quiet revision regressions passed');

assert.notEqual(captionInputRevision('p',{speakers_keep:{v:[0]}},[line]),captionInputRevision('p',{speakers_keep:{v:[1]}},[line]),'actual speaker keep controls stale-reply validation');

const enManifest={input_revision:'final',cues:[{id:'e_one',story_id:'story',source_id:'source',ranges:[{start_ms:100,end_ms:300},{start_ms:500,end_ms:700}]}]};
const enReply={project_id:'p',input_revision:'final',subtitles:{e_one:'A short translation.'}};
assert.deepEqual(validateEnglishCueReply(enReply,'p',enManifest).picked,enReply.subtitles);
for(const bad of [{...enReply,input_revision:'old'},{...enReply,project_id:'other'},{...enReply,subtitles:{}},{...enReply,subtitles:{e_one:'x'.repeat(61)}},{...enReply,subtitles:{e_one:'two\nlines'}}])assert.ok(validateEnglishCueReply(bad,'p',enManifest).error);
assert.equal(englishCueAt(enManifest,enReply.subtitles,'story','source',150),'A short translation.');
assert.equal(englishCueAt(enManifest,enReply.subtitles,'story','source',400),'','removed silence never receives guessed English timing');
assert.equal(englishCueAt(enManifest,enReply.subtitles,'story','source',550),'A short translation.');
assert.equal(englishCueAt(enManifest,{},'story','source',150),'','incomplete cue translations are not silently mixed with legacy text');
console.log('Final English cue identity and source-clock checks passed');

const replayEn={input_revision:'replay',cues:[
 {id:'e_first',story_id:'story',segment_id:'s',occurrence:1,source_id:'source',output_start_ms:0,output_end_ms:200,ranges:[{start_ms:100,end_ms:300}]},
 {id:'e_second',story_id:'story',segment_id:'s',occurrence:2,source_id:'source',output_start_ms:200,output_end_ms:400,ranges:[{start_ms:100,end_ms:300}]}
]};
const replayText={e_first:'Right.',e_second:'Correct.'};
assert.equal(englishCueAt(replayEn,replayText,'story','source',150),'','source time alone cannot guess a replay occurrence');
assert.equal(englishCueAt(replayEn,replayText,'story','source',150,250),'Correct.');
assert.deepEqual(validateEnglishCueReply({project_id:'p',input_revision:'replay',subtitles:replayText},'p',replayEn).picked,replayText);
console.log('Replayed English cues use output occurrence clocks');

assert.equal(englishCueAt(replayEn,replayText,'story','source',150,{segment_id:'s',occurrence:2}),'Correct.','preview disambiguates source replay using its active kept occurrence');
