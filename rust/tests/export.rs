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
    assert!(ass.contains("\\bord8\\shad0\\3c&H0000FF&\\4c&H000000&\\fscx70\\fscy70\\t(0,180,\\fscx100\\fscy100)"), "{ass}");
    doc["text_styles"]["speech"]["box_on"] = json!(true);
    doc["text_styles"]["speech"]["box_color"] = json!("#112233");
    doc["text_styles"]["speech"]["enter"] = json!("wipe");
    let (ass, _) = subtitles(story, &doc);
    assert!(ass.contains("Style: Box,"));
    assert!(ass.contains(",Box,,0,0,0,,"), "{ass}");
    assert!(ass.contains("\\bord8\\shad0\\3c&H332211&\\clip(0,0,0,1920)\\t(0,400,\\clip(0,0,1080,1920))"), "{ass}");
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
    assert_eq!(editorkrab::export::emphasis_lines(&story, &doc), vec![(1000, 3000, "สอง".to_owned())]);
    // a dictionary word split out of a timed phrase is stored with its own text and start
    let split = json!({"text_roles": ["emphasis"], "emphasis": {"a1": [{"text": "สาม", "start_ms": 3100, "end_ms": 3400}, 2000]}});
    // picks apart in time show one at a time, each as it is said (clocked from the words), until the next one starts
    assert_eq!(
        editorkrab::export::emphasis_lines(&story, &split),
        vec![(1000, 2000, "สอง".to_owned()), (2000, 3000, "สาม".to_owned())]
    );
    // neighbouring dictionary words read as one name: no space between Thai words, a space between Latin ones
    let name = json!({"text_roles": ["emphasis"], "emphasis": {"a1": [{"text": "มอน", "start_ms": 3000, "end_ms": 3300}, {"text": "สเตอร์", "start_ms": 3300, "end_ms": 3600}]}});
    assert_eq!(editorkrab::export::emphasis_lines(&story, &name), vec![(2000, 3000, "มอนสเตอร์".to_owned())]);
    let latin = json!({"text_roles": ["emphasis"], "emphasis": {"a1": [{"text": "Gentle", "start_ms": 3000, "end_ms": 3300}, {"text": "Monster", "start_ms": 3300, "end_ms": 3600}]}});
    assert_eq!(editorkrab::export::emphasis_lines(&story, &latin), vec![(2000, 3000, "Gentle Monster".to_owned())]);
    // by default the speech line shows whole (P'Ohm 23 Oct), still without the picked word
    let (ass, _) = subtitles(&story, &doc);
    assert!(ass.contains("}หนึ่งสาม\n") && !ass.contains("}หนึ่ง\n") && !ass.contains("}หนึ่งสอง"), "{ass}");
    // word by word only when that entrance is picked
    let doc = json!({"text_roles": ["speech", "emphasis"], "emphasis": {"a1": [2000]},
        "text_styles": {"speech": {"font":"system","size":64,"color":"#ffffff","x":50,"y":69,"enter":"words"}}});
    let (ass, skipped) = subtitles(&story, &doc);
    // the picked word is on screen as emphasis, so the speech line leaves it out
    assert!(ass.contains("0:00:00.20,0:00:02.00,Text") && ass.contains("}หนึ่ง\n") && ass.contains("}หนึ่งสาม\n"), "{ass}");
    assert!(!ass.contains("}หนึ่งสอง"), "{ass}");
    assert!(ass.contains("0:00:01.00,0:00:03.00,Text") && ass.contains("}สอง\n"), "{ass}");
    assert!(skipped.is_empty(), "emphasis is drawn now: {skipped:?}");
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
    assert!(speech.iter().any(|l| l.ends_with("}หนึ่งสอง")), "{ass}");
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
