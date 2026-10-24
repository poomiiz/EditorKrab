use editorkrab::export::{Piece, ffmpeg_args, file_name, lines, plan, subtitles, unused_path};
use editorkrab::jobs::Job;
use serde_json::{Value, json};
use std::path::Path;

fn artifact(path: &str) -> Value {
    json!({"path": path, "size": 1, "modified_ns": "1", "sha256": "0".repeat(64)})
}
fn job(id: &str, status: &str, path: &str, segments: Value) -> Job {
    serde_json::from_value(json!({
        "id": id, "batch_id": "b", "project_id": "p", "position": 0, "status": status, "step": "",
        "attempt": 1, "cancel_requested": false, "revision": 1, "created_at": 0,
        "started_at": null, "finished_at": null, "heartbeat_at": null, "error": null,
        "task": {"kind": "transcribe", "path": path,
            "context": {"run_id": "r", "source": {"id": "s", "size": 1, "modified_ns": "1"},
                "start_ms": 0, "end_ms": 300000, "language": "th"},
            "settings": {"engine": artifact("e"), "model": artifact("m"), "ffmpeg": artifact("f"), "threads": 8}},
        "result": {"transcript": {"segments": segments}}
    }))
    .unwrap()
}
fn jobs() -> Vec<Job> {
    vec![
        job(
            "j1",
            "succeeded",
            "C:/clips/a.mp4",
            json!([
                {"id": "a0", "text": " สวัสดีครับ", "start_ms": 0, "end_ms": 1500},
                {"id": "a1", "text": " วันนี้", "start_ms": 1500, "end_ms": 4000},
                {"id": "a2", "text": " ตอนจบ", "start_ms": 9000, "end_ms": 10000}
            ]),
        ),
        job(
            "j2",
            "succeeded",
            "C:/clips/b.mp4",
            json!([{"id": "b0", "text": "second file", "start_ms": 200, "end_ms": 1200}]),
        ),
        job(
            "j3",
            "failed",
            "C:/clips/c.mp4",
            json!([{"id": "c0", "text": "x", "start_ms": 0, "end_ms": 1}]),
        ),
    ]
}
fn document(plan: Value) -> Value {
    json!({
        "title": "งาน/ทดสอบ", "plan": plan.to_string(), "text_roles": ["speech", "english"],
        "text_styles": {"speech": {"text": "", "font": "system", "size": 64, "color": "#f59e0b", "x": 50, "y": 69}},
        "headline_items": [{"id": "h", "text": "หัวข้อ {สำคัญ}", "font": "family:Kanit", "size": 80, "color": "#ffffff", "x": 50, "y": 16}],
        "transcript_edits": [
            {"job_id": "j1", "segment_id": "a1", "source_sha256": "x", "original_text": " วันนี้", "text": "วันนี้แก้แล้ว"},
            {"job_id": "j1", "segment_id": "a0", "source_sha256": "x", "original_text": "stale", "text": "ห้ามใช้"}
        ]
    })
}

#[test]
fn english_cue_anchors_survive_camera_cuts_and_map_retained_source_ranges() {
    use editorkrab::export::{Story,translation_cues,subtitle_lines};
    let p=|start,end,words|Piece{source:0,start_ms:start,end_ms:end,text:"Alpha Beta Gamma".into(),words,timed:true,segment:"s0".into()};
    let alpha=("Alpha".into(),1500,1900);let beta=("Beta".into(),1900,2200);let gamma=("Gamma".into(),3000,3200);
    let story=Story{id:"one".into(),title:String::new(),sources:vec!["a.mp4".into()],pieces:vec![
        p(1000,2000,vec![alpha.clone(),beta.clone()]),p(2000,2500,vec![beta.clone()]),p(3000,4000,vec![gamma.clone()])
    ]};
    let mut doc=json!({"files":[{"source_id":"source","path":"a.mp4"}],"text_roles":["english"],
        "text_styles":{"speech":{"size":24,"font":"system","x":50}},"subtitles":{"s0":"Preserved legacy."}});
    let cues=translation_cues(&story,&doc);assert_eq!(cues.len(),1);
    assert_eq!(cues[0].text,"Alpha Beta Gamma");assert_eq!((cues[0].output_start_ms,cues[0].output_end_ms),(500,1880));
    assert_eq!(cues[0].ranges.len(),2,"camera splits merge; removed source silence remains an explicit gap");
    assert_eq!((cues[0].ranges[0].start_ms,cues[0].ranges[0].end_ms),(1500,2500));
    assert_eq!((cues[0].ranges[1].start_ms,cues[0].ranges[1].end_ms),(3000,3380));
    let mut same=story.clone();same.pieces=vec![p(1000,2500,vec![alpha,beta]),p(3000,4000,vec![gamma])];
    assert_eq!(translation_cues(&same,&doc),cues,"camera geometry never changes translation anchors");
    doc["english_cues"]=json!({"input_revision":"checked","translations":{cues[0].id.clone():"Keep working."}});
    assert_eq!(subtitle_lines(&story,&doc),vec![(500,1880,"Keep working.".into())]);
    let mut changed=story.clone();changed.pieces[0].words[0].0="Changed".into();
    assert!(subtitle_lines(&changed,&doc).is_empty(),"an obsolete translation cannot attach to different Thai words");
    assert_eq!(doc["subtitles"]["s0"],"Preserved legacy.");
}

#[test]
fn legacy_english_remains_one_known_cue_without_proportional_word_pages() {
    let story=editorkrab::export::Story{id:"one".into(),title:String::new(),sources:vec!["a.mp4".into()],
        pieces:vec![Piece{source:0,start_ms:1000,end_ms:6000,text:"Thai".into(),words:vec![],timed:false,segment:"s0".into()}]};
    let text="This existing long translation stays on its original known segment rather than inventing the timing of these individual English words.";
    let doc=json!({"text_roles":["english"],"subtitles":{"s0":text},"text_styles":{"english":{"size":160,"x":50}}});
    assert_eq!(editorkrab::export::subtitle_lines(&story,&doc),vec![(0,5000,text.into())]);
}

#[test]
fn repeated_source_passages_have_distinct_translation_ids_and_output_clocks() {
    let p=Piece{source:0,start_ms:1000,end_ms:2000,text:"Again".into(),words:vec![("Again".into(),1100,1400)],timed:true,segment:"s0".into()};
    let story=editorkrab::export::Story{id:"one".into(),title:String::new(),sources:vec!["a.mp4".into()],pieces:vec![p.clone(),p]};
    let cues=editorkrab::export::translation_cues(&story,&json!({}));
    assert_eq!(cues.len(),2);assert_ne!(cues[0].id,cues[1].id);
    assert_eq!((cues[0].output_start_ms,cues[1].output_start_ms),(100,1100));
}

#[test]
fn replay_with_internal_dead_air_round_trips_every_english_cue() {
    let a=Piece{source:0,start_ms:0,end_ms:1000,text:"A B".into(),words:vec![("A".into(),100,500)],timed:true,segment:"s0".into()};
    let b=Piece{source:0,start_ms:2000,end_ms:3000,text:"A B".into(),words:vec![("B".into(),2400,2800)],timed:true,segment:"s0".into()};
    let story=editorkrab::export::Story{id:"one".into(),title:String::new(),sources:vec!["a.mp4".into()],pieces:vec![a.clone(),b.clone(),a,b]};
    let mut doc=json!({"text_roles":["english"]});let cues=editorkrab::export::translation_cues(&story,&doc);
    assert_eq!(cues.len(),4);assert_eq!(cues.iter().map(|c|&c.id).collect::<std::collections::HashSet<_>>().len(),4);
    assert_eq!(cues.iter().map(|c|c.occurrence).collect::<Vec<_>>(),vec![1,1,2,2]);
    let translations:serde_json::Map<String,Value>=cues.iter().enumerate().map(|(i,c)|(c.id.clone(),json!(format!("Cue {i}")))).collect();
    doc["english_cues"]=json!({"input_revision":"validated","translations":translations});
    assert_eq!(editorkrab::export::subtitle_lines(&story,&doc),cues.iter().enumerate().map(|(i,c)|(c.output_start_ms,c.output_end_ms,format!("Cue {i}"))).collect::<Vec<_>>());
}

#[test]
fn english_manifest_revision_tracks_final_anchors_not_the_translation_response() {
    let jobs=vec![job("j","succeeded","a.mp4",json!([{"id":"s0","text":"Alpha Beta","start_ms":1000,"end_ms":4000,"words":[
        {"text":"Alpha","start_ms":1100,"end_ms":1400},{"text":"Beta","start_ms":1900,"end_ms":2300}
    ]}]))];
    let mut doc=json!({"plan":json!({"stories":[{"id":"one","segment_ids":["s0"]}]}).to_string(),"files":[{"source_id":"s","path":"a.mp4"}]});
    let first=editorkrab::export::translation_manifest(&doc,&jobs).unwrap();assert!(!first.cues.is_empty());
    doc["english_cues"]=json!({"input_revision":first.input_revision,"translations":{first.cues[0].id.clone():"Translated"}});
    assert_eq!(editorkrab::export::translation_manifest(&doc,&jobs).unwrap().input_revision,first.input_revision);
    doc["word_times"]=json!({"s0":[[1200,1400],[1900,2300]]});
    assert_ne!(editorkrab::export::translation_manifest(&doc,&jobs).unwrap().input_revision,first.input_revision);
}

#[test]
fn stories_follow_the_plan_order_across_files_with_corrections() {
    let doc = document(json!({"stories": [
        {"id": "s1", "title": "One", "segment_ids": ["a2", "a0", "b0", "a1"]},
        {"id": "s2", "title": "", "segment_ids": ["a0"]}
    ]}));
    let stories = plan(&doc, &jobs()).unwrap();
    assert_eq!(stories.len(), 2);
    let one = &stories[0];
    assert_eq!(one.sources.len(), 2);
    let texts: Vec<_> = one.pieces.iter().map(|p| p.text.as_str()).collect();
    // a0's correction was made on other text, so it does not apply
    assert_eq!(texts, ["ตอนจบ", "สวัสดีครับ", "second file", "วันนี้แก้แล้ว"]);
    assert_eq!(one.pieces[2].source, 1);
    assert_eq!(one.duration_ms(), 1000 + 1500 + 1000 + 2500);
}

#[test]
fn trims_move_a_lines_in_and_out_points_within_limits() {
    let base = plan(&document(json!({"stories": [{"segment_ids": ["a0"]}]})), &jobs()).unwrap();
    let (s, e) = (base[0].pieces[0].start_ms, base[0].pieces[0].end_ms);
    let doc = document(json!({"stories": [{"segment_ids": ["a0"], "trims": {"a0": {"start_ms": s + 300, "end_ms": e + 9000}}}]}));
    let p = &plan(&doc, &jobs()).unwrap()[0].pieces[0];
    assert_eq!(p.start_ms, s + 300);
    assert_eq!(p.end_ms, e + 2000, "extension is capped at 2 s");
    let doc = document(json!({"stories": [{"segment_ids": ["a0"], "trims": {"a0": {"start_ms": e + 5000}}}]}));
    let p = &plan(&doc, &jobs()).unwrap()[0].pieces[0];
    assert!(p.end_ms >= p.start_ms + 200, "a span never collapses");
    let both = plan(&document(json!({"stories": [{"segment_ids": ["a0", "a1"]}]})), &jobs()).unwrap();
    let a1 = both[0].pieces[1].start_ms;
    let doc = document(json!({"stories": [{"segment_ids": ["a0", "a1"], "trims": {"a0": {"end_ms": a1 + 500}}}]}));
    let pieces = &plan(&doc, &jobs()).unwrap()[0].pieces;
    assert_eq!(pieces[1].start_ms, pieces[0].end_ms, "an overlap is not played twice");
    // a trim that covers the next kept line whole leaves no sliver of it
    let a1_end = both[0].pieces[1].end_ms;
    let doc = document(json!({"stories": [{"segment_ids": ["a0", "a1"], "trims": {"a0": {"end_ms": a1_end}}}]}));
    let pieces = &plan(&doc, &jobs()).unwrap()[0].pieces;
    assert!(pieces.iter().all(|p| p.end_ms >= p.start_ms + 200), "no piece shorter than 0.2 s: {pieces:?}");
}

#[test]
fn a_line_trimmed_past_all_its_words_shows_no_subtitle() {
    let piece = Piece { source: 0, start_ms: 0, end_ms: 800, text: "หนึ่ง สอง".into(), words: vec![], timed: true, segment: String::new() };
    assert!(lines(&piece, 20).is_empty());
    let untimed = Piece { timed: false, ..piece };
    assert!(!lines(&untimed, 20).is_empty(), "a line the engine never timed still spreads its text");
}

#[test]
fn a_bare_list_of_stories_is_a_plan_whose_wrapper_was_dropped() {
    let doc = document(json!([{"title": "One", "segment_ids": ["a0", "a1"]}]));
    let stories = plan(&doc, &jobs()).unwrap();
    assert_eq!(stories.len(), 1);
    assert_eq!(stories[0].pieces.len(), 2);
    assert_eq!(plan(&document(json!([])), &jobs()).unwrap_err(), "plan_has_no_stories");
}

#[test]
fn unknown_or_failed_segments_and_bad_plans_are_errors() {
    let bad = |p: Value| plan(&document(p), &jobs()).unwrap_err();
    assert_eq!(
        bad(json!({"stories": [{"segment_ids": ["a0", "c0"]}]})),
        "segment_unknown:c0"
    );
    assert_eq!(bad(json!({"stories": []})), "plan_has_no_stories");
    assert_eq!(
        bad(json!({"stories": [{"segment_ids": []}]})),
        "story_empty:1"
    );
    let mut doc = document(json!({}));
    doc["plan"] = json!("not json");
    assert_eq!(plan(&doc, &jobs()).unwrap_err(), "plan_invalid");
    doc["plan"] = json!("");
    assert_eq!(plan(&doc, &jobs()).unwrap_err(), "plan_missing");
}

#[test]
fn subtitles_place_speech_in_output_time_and_headline_for_the_whole_clip() {
    let doc = document(json!({"stories": [{"segment_ids": ["a2", "a1"]}]}));
    let story = &plan(&doc, &jobs()).unwrap()[0];
    let (ass, skipped) = subtitles(story, &doc);
    assert!(ass.contains("PlayResX: 1080\nPlayResY: 1920"));
    assert!(ass.contains("Dialogue: 1,0:00:00.00,0:00:01.00,Text,,0,0,0,,{\\an5\\pos(540,1325)\\fnLeelawadee UI\\fs64\\1c&H0B9EF5&\\bord0\\shad0\\3c&H000000&\\4c&H000000&}ตอนจบ"));
    assert!(ass.contains("Dialogue: 1,0:00:01.00,0:00:03.50,Text"));
    assert!(ass.contains("Dialogue: 3,0:00:00.00,0:00:03.50,Text,,0,0,0,,{\\an5\\pos(540,307)\\fnKanit\\fs80\\1c&HFFFFFF&\\bord0\\shad0\\3c&H000000&\\4c&H000000&}หัวข้อ (สำคัญ)"));
    assert_eq!(skipped, ["english"]);
    let mut quiet = doc.clone();
    quiet["text_roles"] = json!([]);
    assert!(!subtitles(story, &quiet).0.contains("Dialogue: 0,"));
}

#[test]
fn ffmpeg_cuts_each_piece_from_its_own_input_and_burns_subs() {
    let doc = document(json!({"stories": [{"segment_ids": ["b0", "a1"]}]}));
    let story = &plan(&doc, &jobs()).unwrap()[0];
    let args = ffmpeg_args(story, &doc, Path::new("out.mp4"));
    let graph = &args[args.iter().position(|a| a == "-filter_complex").unwrap() + 1];
    assert!(graph.starts_with("[0:v]trim=start=0.200:end=1.200"));
    assert!(graph.contains("[1:v]trim=start=1.500:end=4.000"));
    assert!(graph.contains("crop=w='min(iw,ih*0.562500)':h='min(ih,iw/0.562500)':x='(iw-ow)*0.5000'"), "9:16, centred by default: {graph}");
    assert!(graph.contains("scale=1080:1920"));
    assert!(graph.ends_with("[v0][a0][v1][a1]concat=n=2:v=1:a=1[cv][ca];[cv]ass=subs.ass[vout]"));
    assert_eq!(args.last().unwrap(), "out.mp4");
}

#[test]
fn the_picked_shape_and_crop_reach_ffmpeg_and_the_subtitles() {
    let mut doc = document(json!({"stories": [{"segment_ids": ["a1"]}]}));
    let story = &plan(&doc, &jobs()).unwrap()[0];
    let path = story.sources[0].display().to_string();
    doc["output"] = json!({"aspect": "1:1"});
    doc["files"] = json!([{"source_id": "s1", "path": path}]);
    doc["crops"] = json!({"s1": {"x": 0.25, "y": 2.0}});
    let args = ffmpeg_args(story, &doc, Path::new("out.mp4"));
    let graph = &args[args.iter().position(|a| a == "-filter_complex").unwrap() + 1];
    assert!(graph.contains("crop=w='min(iw,ih*1.000000)':h='min(ih,iw/1.000000)':x='(iw-ow)*0.2500':y='(ih-oh)*1.0000'"), "{graph}");
    assert!(graph.contains("scale=1080:1080"));
    let (ass, _) = subtitles(story, &doc);
    assert!(ass.contains("PlayResX: 1080\nPlayResY: 1080"));
    doc["output"] = json!({"aspect": "16:9"});
    let (ass, _) = subtitles(story, &doc);
    assert!(ass.contains("PlayResX: 1920\nPlayResY: 1080"));
    assert!(ass.contains("\\fs114"), "64 px on a 1080-wide frame is 114 on 1920: {ass}");
}

#[test]
fn text_effects_become_ass_tags_and_the_box_style() {
    let mut doc = document(json!({"stories": [{"segment_ids": ["a1"]}]}));
    let story = &plan(&doc, &jobs()).unwrap()[0];
    let (plain, _) = subtitles(story, &doc);
    assert!(plain.contains(",Text,,0,0,0,,{\\an5"), "no effect fields keep the old look: {plain}");
    assert!(plain.contains("\\bord0\\shad0\\3c&H000000&\\4c&H000000&"));
    doc["text_styles"]["speech"]["outline"] = json!(8);
    doc["text_styles"]["speech"]["outline_color"] = json!("#ff0000");
    doc["text_styles"]["speech"]["enter"] = json!("pop");
    let (ass, _) = subtitles(story, &doc);
    assert!(ass.contains("\\bord8\\shad0\\3c&H0000FF&\\4c&H000000&\\fad(120,0)\\fscx85\\fscy85\\t(0,300,\\fscx100\\fscy100)"), "{ass}");
    doc["text_styles"]["speech"]["box_on"] = json!(true);
    doc["text_styles"]["speech"]["box_color"] = json!("#112233");
    doc["text_styles"]["speech"]["enter"] = json!("wipe");
    let (ass, _) = subtitles(story, &doc);
    assert!(ass.contains("Style: Box,"));
    assert!(ass.contains(",Box,,0,0,0,,"), "{ass}");
    assert!(ass.contains("\\bord8\\shad0\\3c&H332211&\\clip(") && ass.contains("\\t(0,300,\\clip("), "{ass}");
    assert!(!ass.contains("\\clip(0,0,1080,1920)"), "reveal follows measured text bounds");
}

#[test]
fn output_names_are_safe_and_never_overwrite() {
    assert_eq!(file_name("งาน/ทดสอบ: ตอน 1?", "x"), "งาน ทดสอบ ตอน 1");
    assert_eq!(file_name(" .. ", "EditorKrab 1"), "EditorKrab 1");
    let dir = std::env::temp_dir().join(format!("ek-export-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("clip.mp4"), b"x").unwrap();
    assert_eq!(unused_path(&dir, "clip"), dir.join("clip (2).mp4"));
    assert_eq!(unused_path(&dir, "new"), dir.join("new.mp4"));
}

#[test]
fn speech_appears_word_by_word_and_picked_words_show_as_emphasis() {
    let piece = Piece {
        source: 0,
        start_ms: 1000,
        end_ms: 4000,
        text: "หนึ่งสองสาม".into(),
        words: vec![("หนึ่ง".into(), 1200, 1500), ("สอง".into(), 2000, 2400), ("สาม".into(), 3000, 3600)],
        timed: true,
        segment: "a1".into(),
    };
    let lines = editorkrab::export::karaoke(&piece, 40);
    // nothing before the first word is said; then the line grows a word at a time
    assert_eq!(
        lines,
        vec![(200, 1000, "หนึ่ง".to_owned()), (1000, 2000, "หนึ่งสอง".to_owned()), (2000, 3000, "หนึ่งสองสาม".to_owned())]
    );
    let story = editorkrab::export::Story { id: "s".into(), title: String::new(), sources: vec!["a.mp4".into()], pieces: vec![piece] };
    let doc = json!({"text_roles": ["speech", "emphasis"], "emphasis": {"a1": [2000]}});
    assert_eq!(editorkrab::export::emphasis_lines(&story, &doc), vec![(1000, 1580, "สอง".to_owned())]);
    // a dictionary word split out of a timed phrase is stored with its own text and start
    let split = json!({"text_roles": ["emphasis"], "emphasis": {"a1": [{"text": "สาม", "start_ms": 3100, "end_ms": 3400}, 2000]}});
    // picks apart in time show one at a time, each as it is said (clocked from the words), until the next one starts
    assert_eq!(
        editorkrab::export::emphasis_lines(&story, &split),
        vec![(1000, 1580, "สอง".to_owned()), (2000, 2780, "สาม".to_owned())]
    );
    // neighbouring dictionary words read as one name: no space between Thai words, a space between Latin ones
    let name = json!({"text_roles": ["emphasis"], "emphasis": {"a1": [{"text": "มอน", "start_ms": 3000, "end_ms": 3300}, {"text": "สเตอร์", "start_ms": 3300, "end_ms": 3600}]}});
    let mut names=story.clone();names.pieces[0].words=vec![("มอน".into(),3000,3300),("สเตอร์".into(),3300,3600)];
    assert_eq!(editorkrab::export::emphasis_lines(&names, &name), vec![(2000, 2780, "มอนสเตอร์".to_owned())]);
    let latin = json!({"text_roles": ["emphasis"], "emphasis": {"a1": [{"text": "Gentle", "start_ms": 3000, "end_ms": 3300}, {"text": "Monster", "start_ms": 3300, "end_ms": 3600}]}});
    names.pieces[0].words=vec![("Gentle".into(),3000,3300),("Monster".into(),3300,3600)];
    assert_eq!(editorkrab::export::emphasis_lines(&names, &latin), vec![(2000, 2780, "Gentle Monster".to_owned())]);
    // by default the speech line shows whole (P'Ohm 23 Oct), still without the picked word
    let (ass, _) = subtitles(&story, &doc);
    assert!(ass.contains("}หนึ่ง\n") && ass.contains("}สาม\n") && !ass.contains("}หนึ่งสาม"), "separate speech beats: {ass}");
    // word by word only when that entrance is picked
    let doc = json!({"text_roles": ["speech", "emphasis"], "emphasis": {"a1": [2000]},
        "text_styles": {"speech": {"font":"system","size":64,"color":"#ffffff","x":50,"y":69,"enter":"words"}}});
    let (ass, skipped) = subtitles(&story, &doc);
    // the picked word is on screen as emphasis, so the speech line leaves it out
    // and "หนึ่ง" stays on until the orange "สอง" after it ends (Nina stack)
    assert!(ass.contains("0:00:00.20,0:00:01.58,Text") && ass.contains("}หนึ่ง\n") && ass.contains("}สาม\n"), "{ass}");
    assert!(!ass.contains("}หนึ่งสอง"), "{ass}");
    assert!(ass.contains("0:00:01.00,0:00:01.58,Text") && ass.contains("}สอง\n"), "{ass}");
    assert!(skipped.is_empty(), "emphasis is drawn now: {skipped:?}");
}

#[test]
fn separate_transcript_beats_never_show_the_next_cues_words_early() {
    let piece = |segment: &str, start_ms, end_ms, text: &str| Piece {
        source: 0, start_ms, end_ms, text: text.into(), words: vec![], timed: false, segment: segment.into(),
    };
    let story = editorkrab::export::Story {
        id: "s".into(), title: String::new(), sources: vec!["a.mp4".into()],
        pieces: vec![
            piece("s8", 31420, 32840, "ว่าเรื่องนี้เป็นปัญหา"),
            piece("s9", 32840, 37040, "การ"),
            piece("s10", 37040, 39200, "ตอบคำถามไม่ได้"),
            // a short line joins the next line of its video even across a gap (the dead air there is cut)
            piece("s11", 40000, 40500, "คือ"),
            piece("s12", 41000, 42000, "ฉันน่ะคิด"),
            // a back-channel word from the listener stays its own card
            piece("s13", 42000, 42500, "ใช่"),
            piece("s14", 42500, 44000, "แล้วก็ต่อ"),
        ],
    };
    let (lines, _) = editorkrab::export::speech_lines(&story, &json!({"text_roles": ["speech"]}));
    let texts: Vec<&str> = lines.iter().map(|l| l.2.as_str()).collect();
    assert_eq!(texts, ["ว่าเรื่องนี้เป็นปัญหา", "การ", "ตอบคำถามไม่ได้", "คือ", "ฉันน่ะคิด", "ใช่", "แล้วก็ต่อ"], "{lines:?}");
    // No word clocks here: retain each supplied cue, never guess when the next word is spoken.
    assert_eq!((lines[1].0, lines[1].1), (1420, 5620));
    assert_eq!((lines[2].0, lines[2].1), (5620, 7780));
}

#[test]
fn dead_air_at_a_lines_ends_is_cut_unless_that_edge_was_dragged() {
    use editorkrab::export::unquiet;
    // "การ" runs 32.84-37.04 s but she stops talking at 33.2 s; another quiet stretch ends just before the line
    let quiet = [(30000, 32800), (33200, 37000)];
    assert_eq!(unquiet(&quiet, &json!(null), 32840, 37040), (32840, 33300));
    // a line that starts inside quiet starts LEAD_PAD_MS before that quiet ends (VAD hears a word late),
    // and ends PAD into the next one
    assert_eq!(unquiet(&quiet, &json!(null), 31000, 34000), (32550, 33300));
    // edges the person moved stay where they put them
    assert_eq!(unquiet(&quiet, &json!({"end_ms": 37040}), 32840, 37040), (32840, 37040));
    // a line that is all quiet is left whole, never cut to nothing
    assert_eq!(unquiet(&[(0, 10000)], &json!(null), 2000, 3000), (2000, 3000));
}

#[test]
fn the_camera_follows_whoever_is_talking() {
    use editorkrab::export::{crop_at, speaker_at, speaker_cuts};
    let path = std::path::Path::new("C:/v/a.mov");
    let doc = json!({
        "follow_speaker": true,
        "files": [{"source_id": "v1", "path": "C:/v/a.mov"}],
        "crops": {"v1": {"x": 0.5, "y": 0.5, "speakers": {"0": {"x": 0.2, "y": 0.5}, "1": {"x": 0.8, "y": 0.4, "zoom": 1.5}}}},
        "voice": {"v1": {"turns": [
            {"start_ms": 0, "end_ms": 5000, "speaker": 0},
            {"start_ms": 5000, "end_ms": 9000, "speaker": 1},
            {"start_ms": 9000, "end_ms": 9400, "speaker": 0},
            {"start_ms": 9400, "end_ms": 12000, "speaker": 1}
        ]}}
    });
    assert_eq!(speaker_at(&doc, "v1", 1000), Some(0));
    assert_eq!(speaker_at(&doc, "v1", 6000), Some(1));
    // a 0.4 s "yes" from the other side keeps the shot on the person talking
    assert_eq!(speaker_at(&doc, "v1", 9200), Some(1));
    // no zoom of their own: 20% closer
    assert_eq!(crop_at(&doc, path, 1000), (0.2, 0.5, 1.2, false));
    // a clip that already fits the frame still moves in on the speaker by zooming
    assert_eq!(crop_at(&doc, path, 6000), (0.8, 0.4, 1.5, false));
    assert_eq!(speaker_cuts(&doc, "v1", 2000, 11000), vec![(2000, 5000), (5000, 11000)]);
    // following off: one window for the whole video
    let mut off = doc.clone();
    off["follow_speaker"] = json!(false);
    assert_eq!(crop_at(&off, path, 6000), (0.5, 0.5, 1.0, false));
    // faces seen: the camera follows who is seen talking and centres on their face, 20% closer
    let mut faces = doc.clone();
    faces["crops"]["v1"]["speakers"] = json!({});
    faces["voice"]["v1"]["faces"] = json!([{"cx": 0.21, "cy": 0.51, "size": 0.3}, {"cx": 0.76, "cy": 0.45, "size": 0.2}]);
    faces["voice"]["v1"]["seen"] = json!([{"start_ms": 0, "end_ms": 16000, "person": 0}, {"start_ms": 16000, "end_ms": 30000, "person": 1}]);
    assert_eq!(crop_at(&faces, path, 20000), (0.76, 0.45, 1.2, true));
    assert_eq!(speaker_cuts(&faces, "v1", 10000, 20000), vec![(10000, 16000), (16000, 20000)]);
}

#[test]
fn dead_air_inside_a_line_is_cut_and_the_line_still_reads_as_one() {
    use editorkrab::export::inner_cuts;
    // a 1 s pause inside a 4 s line goes, 0.1 s of it kept after speech and 0.25 s before it starts again;
    // a short dip stays
    assert_eq!(inner_cuts(&[(11000, 12000), (13000, 13250)], 10000, 14000), vec![(10000, 11100), (11750, 14000)]);
    // quiet at the line's edges is unquiet's job, not a cut inside
    assert_eq!(inner_cuts(&[(9000, 10500)], 10000, 14000), vec![(10000, 14000)]);
    let piece = |start_ms, end_ms, text: &str, words: Vec<(String, u64, u64)>| Piece {
        source: 0, start_ms, end_ms, text: text.into(), words, timed: true, segment: "s1".into(),
    };
    let story = editorkrab::export::Story {
        id: "s".into(), title: String::new(), sources: vec!["a.mp4".into()],
        pieces: vec![
            piece(10000, 11100, "ตอบคำถาม", vec![("ตอบคำถาม".into(), 10000, 11000)]),
            piece(11900, 14000, "ไม่ได้", vec![("ไม่ได้".into(), 12000, 13000)]),
        ],
    };
    // a small size so the line fits whatever fonts the machine has (the Mac runners measured it too wide)
    let doc = json!({"text_roles": ["speech"], "text_styles": {"speech": {"text": "", "font": "system", "size": 24, "color": "#ffffff", "x": 50, "y": 75}}});
    let (lines, _) = editorkrab::export::speech_lines(&story, &doc);
    assert_eq!(lines, vec![(0, 2380, "ตอบคำถามไม่ได้".to_owned())]);
}

#[test]
fn an_emphasis_word_at_the_end_of_its_line_stays_up_long_enough_to_read() {
    // "ว่าเรื่องนี้เป็นปัญหา" then "การ": "ปัญหา" is said in the line's last 0.4 s (P'Ohm 23 Oct)
    let piece = |segment: &str, start_ms, end_ms, words: Vec<(String, u64, u64)>| Piece {
        source: 0, start_ms, end_ms, text: String::new(), words, timed: true, segment: segment.into(),
    };
    let story = editorkrab::export::Story {
        id: "s".into(), title: String::new(), sources: vec!["a.mp4".into()],
        pieces: vec![
            piece("s8", 31420, 32840, vec![("ว่า".into(), 31420, 31680), ("เป็นปัญหา".into(), 32100, 32840)]),
            piece("s9", 32840, 37040, vec![("การ".into(), 32840, 37040)]),
        ],
    };
    let doc = json!({"text_roles": ["emphasis"], "emphasis": {"s8": [{"text": "ปัญหา", "start_ms": 32429, "end_ms": 32840}]}});
    let lines = editorkrab::export::emphasis_lines(&story, &doc);
    assert_eq!(lines.len(), 1);
    let (from, to, text) = &lines[0];
    assert_eq!(text, "ปัญหา");
    assert!(to - from >= editorkrab::export::EMPHASIS_HOLD_MS, "{lines:?}");
}

#[test]
fn translated_subtitles_show_for_their_pieces_only_when_ticked() {
    let piece = |segment: &str, start_ms, end_ms| Piece {
        source: 0, start_ms, end_ms, text: "ไทย".into(), words: vec![], timed: false, segment: segment.into(),
    };
    let story = editorkrab::export::Story {
        id: "s".into(), title: String::new(), sources: vec!["a.mp4".into()],
        pieces: vec![piece("a1", 0, 1000), piece("a1", 1000, 1500), piece("a2", 3000, 4000), piece("a3", 5000, 5500)],
    };
    let doc = json!({"text_roles": ["english"], "subtitles": {"a1": "Hello there", "a2": "Second line"}});
    // a line cut into neighbouring pieces stays one subtitle; a line with no translation shows nothing
    assert_eq!(
        editorkrab::export::subtitle_lines(&story, &doc),
        vec![(0, 1500, "Hello there".to_owned()), (1500, 2500, "Second line".to_owned())]
    );
    let (ass, skipped) = subtitles(&story, &doc);
    assert!(ass.contains("Hello there") && ass.contains("Second line"), "{ass}");
    assert!(skipped.is_empty(), "{skipped:?}");
    let off = json!({"text_roles": [], "subtitles": {"a1": "Hello there"}});
    assert!(editorkrab::export::subtitle_lines(&story, &off).is_empty());
}

#[test]
fn blurred_shadow_goes_under_the_text_and_blend_layers_get_their_own_file() {
    let mut doc = document(json!({"stories": [{"segment_ids": ["a1"]}]}));
    let story = &plan(&doc, &jobs()).unwrap()[0];
    doc["text_styles"]["speech"]["enter"] = json!("none");
    doc["text_styles"]["speech"]["shadow"] = json!(4);
    doc["text_styles"]["speech"]["shadow_blur"] = json!(6);
    doc["text_styles"]["speech"]["shadow_color"] = json!("#ff0000");
    let (ass, _) = subtitles(story, &doc);
    // the shadow copy: no fill, no outline, blurred, one layer below the sharp text without a shadow
    assert!(ass.contains("Dialogue: 0,"), "{ass}");
    assert!(ass.contains("\\4c&H0000FF&\\1a&HFF&\\3a&HFF&\\blur6}"), "{ass}");
    assert!(ass.contains("\\shad0}"), "{ass}");
    // speech blended with Screen leaves the normal file and gets its own, mixed in RGB over the video
    doc["text_styles"]["speech"]["blend"] = json!("screen");
    let (normal, _) = subtitles(story, &doc);
    assert!(!normal.contains("Dialogue: 1,"), "{normal}");
    assert_eq!(editorkrab::export::blend_modes(&doc), ["screen"]);
    let (layer, _) = editorkrab::export::subtitles_in(story, &doc, "screen");
    assert!(layer.contains("Dialogue: 1,"), "{layer}");
    let args = ffmpeg_args(story, &doc, std::path::Path::new("out.mp4")).join(" ");
    assert!(args.contains("ass=subs_screen.ass,format=gbrp[l0]"), "{args}");
    assert!(args.contains("blend=all_mode=screen,format=yuv420p[vout]"), "{args}");
}

#[test]
fn emphasised_words_leave_the_speech_line() {
    let piece = Piece {
        source: 0,
        start_ms: 1000,
        end_ms: 4000,
        text: "หนึ่งสองสาม".into(),
        words: vec![("หนึ่ง".into(), 1200, 1500), ("สองสาม".into(), 2000, 3600)],
        timed: true,
        segment: "a1".into(),
    };
    let story = editorkrab::export::Story { id: "s".into(), title: String::new(), sources: vec!["a.mp4".into()], pieces: vec![piece] };
    let mut doc = json!({"text_roles": ["speech", "emphasis"], "text_styles": {"speech": {"font":"system","size":64,"color":"#ffffff","x":50,"y":69,"enter":"none"}},
        "emphasis": {"a1": [{"text": "สาม", "start_ms": 2800, "end_ms": 3600}]}});
    let (ass, _) = subtitles(&story, &doc);
    let speech: Vec<&str> = ass.lines().filter(|l| l.contains(r"\pos(540,1325)")).collect();
    // a 0.5 s breath stays inside the card (Nina 16), so "หนึ่ง" and "สอง" share one
    assert!(speech.iter().any(|l| l.ends_with("}หนึ่งสอง")), "{ass}");
    assert!(!speech.iter().any(|l| l.contains("สาม")), "pick must leave speech");
    assert!(ass.lines().any(|l| l.ends_with("}สาม")), "the pick still shows as emphasis: {ass}");
    // with emphasis off the speech keeps every word
    doc["text_roles"] = json!(["speech"]);
    let (ass, _) = subtitles(&story, &doc);
    assert!(ass.lines().any(|l| l.ends_with("}หนึ่งสองสาม")), "{ass}");
}

#[test]
fn shadow_opacity_becomes_ass_alpha() {
    let mut doc = document(json!({"stories": [{"segment_ids": ["a1"]}]}));
    let story = &plan(&doc, &jobs()).unwrap()[0];
    doc["text_styles"]["speech"]["shadow_opacity"] = json!(25);
    let (ass, _) = subtitles(story, &doc);
    assert!(ass.contains(r"&H000000&\4a&HBF&"), "{ass}");
}

#[test]
fn box_padding_across_and_up_down() {
    let mut doc = document(json!({"stories": [{"segment_ids": ["a1"]}]}));
    let story = &plan(&doc, &jobs()).unwrap()[0];
    doc["text_styles"]["speech"]["box_on"] = json!(true);
    doc["text_styles"]["speech"]["box_pad_x"] = json!(30);
    doc["text_styles"]["speech"]["box_pad_y"] = json!(8);
    let (ass, _) = subtitles(story, &doc);
    assert!(ass.contains(r"\xbord30\ybord8"), "{ass}");
}

#[test]
fn capcut_style_text_settings_become_ass_tags() {
    let mut doc = document(json!({"stories": [{"segment_ids": ["a1"]}]}));
    let story = &plan(&doc, &jobs()).unwrap()[0];
    let s = &mut doc["text_styles"]["speech"];
    s["enter"] = json!("none");
    s["bold"] = json!(false);
    s["italic"] = json!(true);
    s["underline"] = json!(true);
    s["spacing"] = json!(6);
    s["align"] = json!("left");
    s["shadow"] = json!(4);
    s["shadow_angle"] = json!(90);
    s["glow_on"] = json!(true);
    s["glow_color"] = json!("#00ff00");
    s["glow_range"] = json!(20);
    s["glow_strength"] = json!(100);
    let (ass, _) = subtitles(story, &doc);
    assert!(ass.contains(r"\an4\pos("), "{ass}");
    assert!(ass.contains(r"\xshad0.0\yshad5.7\b0\i1\u1\fsp6"), "{ass}");
    assert!(ass.contains(r"\1a&HFF&\shad0\bord20\3c&H00FF00&\3a&H00&\blur10"), "glow under the text: {ass}");
    doc["text_styles"]["speech"]["case"] = json!("upper");
    doc["text_styles"]["speech"]["box_on"] = json!(true);
    doc["text_styles"]["speech"]["box_opacity"] = json!(50);
    let (ass, _) = subtitles(story, &doc);
    assert!(ass.contains(r"\3a&H80&"), "{ass}");
}

#[test]
fn retimed_words_and_the_subtitle_shift_move_speech_and_emphasis() {
    let mut doc = document(json!({"stories": [{"segment_ids": ["a1"]}]}));
    doc["text_styles"]["speech"]["enter"] = json!("none");
    let base = plan(&doc, &jobs()).unwrap()[0].pieces[0].words.clone();
    // re-timed clocks replace the transcript's, one pair per word in order
    let times: Vec<_> = base.iter().map(|w| json!([w.1 + 100, w.2 + 100])).collect();
    doc["word_times"] = json!({"a1": times});
    let moved = plan(&doc, &jobs()).unwrap()[0].pieces[0].words.clone();
    assert_eq!(moved.iter().map(|w| w.1).collect::<Vec<_>>(), base.iter().map(|w| w.1 + 100).collect::<Vec<_>>());
    // the whole-project shift adds on top
    doc["subtitle_offset_ms"] = json!(-50);
    let shifted = plan(&doc, &jobs()).unwrap()[0].pieces[0].words.clone();
    assert_eq!(shifted.iter().map(|w| w.1).collect::<Vec<_>>(), base.iter().map(|w| w.1 + 50).collect::<Vec<_>>());
    // a list that does not match the words one for one is ignored
    doc["word_times"] = json!({"a1": [[0, 1]]});
    doc["subtitle_offset_ms"] = json!(0);
    assert_eq!(plan(&doc, &jobs()).unwrap()[0].pieces[0].words, base);
}

#[test]
fn repeated_asr_token_at_the_same_clock_does_not_create_a_one_ms_overlay() {
    let story=editorkrab::export::Story {id:"s".into(),title:"".into(),sources:vec!["a.mp4".into()],pieces:vec![Piece {
        source:0,start_ms:0,end_ms:3000,text:"คือ".into(),segment:"a".into(),timed:true,
        words:vec![("คือ".into(),0,0),("คือ".into(),0,2680)],
    }]};
    let cues=editorkrab::export::speech_cues(&story,&json!({"text_roles":["speech"]}));
    assert_eq!(cues,vec![(0,2860,"คือ".to_owned())]);
}

#[test]
fn emphasis_phrases_cross_small_gaps_and_camera_splits_but_end_on_the_spoken_beat() {
    use editorkrab::export::{emphasis_lines,Story};
    let piece=|a,b,words|Piece {source:0,start_ms:a,end_ms:b,text:String::new(),words,timed:true,segment:"a".into()};
    let mut story=Story{id:"s".into(),title:String::new(),sources:vec!["a.mp4".into()],pieces:vec![
        piece(0,1200,vec![("Brand".into(),1000,1100)]),
        piece(1200,5000,vec![("Manager".into(),1230,1500),("today".into(),3000,3300)])]};
    let mut doc=json!({"text_roles":["emphasis"],"emphasis":{"a":[
        {"text":"Brand","start_ms":1000,"end_ms":1100},
        {"text":"Manager","start_ms":1230,"end_ms":1500}]}});
    assert_eq!(emphasis_lines(&story,&doc),vec![(1000,1680,"Brand Manager".into())]);
    doc["emphasis"]["a"]=json!([{ "text":"Brand Manager","start_ms":1000,"end_ms":1500,"word_ids":["w0","w1"]},
        {"text":"today","start_ms":3000,"end_ms":3300,"word_ids":["w2"]}]);
    assert_eq!(emphasis_lines(&story,&doc),vec![(1000,1680,"Brand Manager".into()),(3000,3480,"today".into())]);
    // An actual removed source gap shortens the phrase clock; the phrase remains a single event.
    story.pieces[1].start_ms=1230;
    assert_eq!(emphasis_lines(&story,&doc)[0],(1000,1650,"Brand Manager".into()));
}

#[test]
fn retimed_words_cannot_export_using_a_previous_quiet_analysis() {
    let jobs=vec![job("j1","succeeded","C:/clips/a.mp4",json!([{
        "id":"a","text":"พูด","start_ms":0,"end_ms":2000,
        "words":[{"text":"พูด","start_ms":100,"end_ms":600}]}]))];
    let mut doc=json!({"plan":json!({"stories":[{"segment_ids":["a"]}]}).to_string(),
        "files":[{"path":"C:/clips/a.mp4","source_id":"src"}],"dead_min_ms":100,
        "voice":{"src":{"turns":[{"speaker":0,"start_ms":0,"end_ms":2000}]}},
        "quiet_key":{"src":json!([300,7,[0],[[100,600]]]).to_string()}});
    assert!(plan(&doc,&jobs).is_ok(),"effective minimum matches the UI's300ms");
    doc["word_times"]=json!({"a":[[150,650]]});
    assert_eq!(plan(&doc,&jobs).unwrap_err(),"pause_analysis_stale");
    doc["quiet_key"]["src"]=json!(json!([300,7,[0],[[150,650]]]).to_string());
    assert!(plan(&doc,&jobs).is_ok());
    doc["speakers_keep"]=json!({"src":[1]});
    assert_eq!(plan(&doc,&jobs).unwrap_err(),"pause_analysis_stale");
}

#[test]
fn camera_boundary_duplicates_one_clocked_word_once_but_keeps_a_real_repeat() {
    let piece=|a,b,words|Piece{source:0,start_ms:a,end_ms:b,text:"คำ".into(),words,timed:true,segment:"s".into()};
    let merged=editorkrab::export::join_short_lines(&[
        piece(0,4000,vec![("คำ".into(),3900,4300)]),
        piece(4000,6000,vec![("คำ".into(),3900,4300),("คำ".into(),5000,5200)])]);
    assert_eq!(merged.len(),1);assert_eq!(merged[0].words,vec![("คำ".into(),3900,4300),("คำ".into(),5000,5200)]);
}

#[test]
fn the_white_setup_line_stays_on_until_the_orange_punch_line_ends() {
    // Nina 16: "เพราะว่าเขาก็ไม่รู้สึกว่า" 12.2-14.6 s, orange "ถูกยัดเยียดไง" 14.63-15.33 s, both end together
    let mut cues = vec![(12_200, 14_600, "เพราะว่าเขาก็ไม่รู้สึกว่า".to_owned()), (15_330, 16_700, "มันเหมือนเรา".to_owned())];
    editorkrab::export::hold_for_emphasis(&mut cues, &[(14_630, 15_330, "ถูกยัดเยียดไง".to_owned())]);
    assert_eq!(cues[0].1, 15_330);
    // never into the next line, and an emphasis far from any line holds nothing
    let mut cues = vec![(0, 1_000, "ก".to_owned()), (1_200, 2_000, "ข".to_owned())];
    editorkrab::export::hold_for_emphasis(&mut cues, &[(500, 1_800, "ค".to_owned()), (9_000, 9_500, "ง".to_owned())]);
    assert_eq!((cues[0].1, cues[1].1), (1_200, 2_000));
}

#[test]
fn filler_and_stammer_at_a_lines_start_are_cut() {
    use editorkrab::export::past_fillers;
    let w = |t: &str, a: u64, b: u64| (t.to_owned(), a, b);
    // "เออ คือ คือ มีโมเม้นต์": the line starts at "มีโมเม้นต์"
    let line = vec![w("เออ", 1000, 1300), w("คือ", 1400, 1600), w("คือ", 1700, 1900), w("มี", 2000, 2200), w("โมเม้นต์", 2200, 2800)];
    assert_eq!(past_fillers(&line, 1000, 3000), Some(2000));
    // a word said twice: "มีไหม มีไหม คำถาม" keeps one
    let line = vec![w("มีไหม", 0, 400), w("มีไหม", 500, 900), w("คำถาม", 1000, 1500)];
    assert_eq!(past_fillers(&line, 0, 2000), Some(500));
    // a real first word: nothing goes; a line of fillers only (a listener's "อืม") stays
    assert_eq!(past_fillers(&[w("ฉัน", 0, 300), w("คือ", 300, 500)], 0, 600), None);
    assert_eq!(past_fillers(&[w("อืม", 0, 300)], 0, 600), None);
}

#[test]
fn a_camera_change_lands_in_the_widest_pause_between_words() {
    // the turn changes at 4.0 s, in the middle of "ปี"; the pause before "ที่แล้ว" (4.6-4.9 s) is the widest near
    let words: Vec<(String, u64, u64)> = vec![("สมัย".into(), 3000, 3500), ("สัก".into(), 3520, 3900), ("ปี".into(), 3920, 4600), ("ที่แล้ว".into(), 4900, 5600)];
    let mut parts = vec![(1000, 4000), (4000, 9000)];
    editorkrab::export::at_word_gaps(&mut parts, &words);
    assert_eq!(parts, vec![(1000, 4750), (4750, 9000)]);
    // no pause near: the change stays where it was
    let mut parts = vec![(1000, 4000), (4000, 9000)];
    editorkrab::export::at_word_gaps(&mut parts, &[("ก".into(), 0, 9000)]);
    assert_eq!(parts, vec![(1000, 4000), (4000, 9000)]);
    // a dead-air cut between parts is not filled in
    let mut parts = vec![(1000, 3900), (4900, 9000)];
    editorkrab::export::at_word_gaps(&mut parts, &words);
    assert_eq!(parts, vec![(1000, 3900), (4900, 9000)]);
}

#[test]
fn a_card_too_long_ends_at_its_widest_pause_and_a_short_tail_joins_the_card_before() {
    use editorkrab::export::card_groups;
    let text = |g: &Vec<Vec<(&str, u64, u64)>>| g.iter().map(|c| c.iter().map(|w| w.0).collect::<String>()).collect::<Vec<_>>();
    // "มีไหม คำถามอะไรที่ตอบไม่ได้": room for 4 words; the 300 ms pause after "มีไหม" is where the card ends,
    // not between "ตอบไม่" and "ได้"
    let words = [("มีไหม", 0, 400), ("คำถาม", 700, 1100), ("อะไร", 1120, 1400), ("ที่", 1420, 1500), ("ตอบไม่", 1520, 1900), ("ได้", 1910, 2100)];
    let groups = card_groups(&words, |g| g.len() <= 4);
    assert_eq!(text(&groups)[0], "มีไหม");
    // a lone short last word left by the 3 s limit joins the card before
    let words = [("ฉันถาม", 0, 1500), ("คำถาม", 1520, 3000), ("แก", 3050, 3200)];
    assert_eq!(text(&card_groups(&words, |_| true)), ["ฉันถามคำถามแก"]);
}
