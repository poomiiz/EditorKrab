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
    assert!(ass.contains("Dialogue: 0,0:00:00.00,0:00:01.00,Text,,0,0,0,,{\\an5\\pos(540,1325)\\fnLeelawadee UI\\fs64\\1c&H0B9EF5&\\bord4\\shad1\\3c&H000000&}ตอนจบ"));
    assert!(ass.contains("Dialogue: 0,0:00:01.00,0:00:03.50,Text"));
    assert!(ass.contains("Dialogue: 1,0:00:00.00,0:00:03.50,Text,,0,0,0,,{\\an5\\pos(540,307)\\fnKanit\\fs80\\1c&HFFFFFF&\\bord4\\shad1\\3c&H000000&}หัวข้อ (สำคัญ)"));
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
    assert!(plain.contains("\\bord4\\shad1\\3c&H000000&"));
    doc["text_styles"]["speech"]["outline"] = json!(8);
    doc["text_styles"]["speech"]["outline_color"] = json!("#ff0000");
    doc["text_styles"]["speech"]["enter"] = json!("pop");
    let (ass, _) = subtitles(story, &doc);
    assert!(ass.contains("\\bord8\\shad1\\3c&H0000FF&\\fscx70\\fscy70\\t(0,180,\\fscx100\\fscy100)"), "{ass}");
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
    assert_eq!(lines.len(), 1);
    // 0.2 s before the first word, then each word held until the next one starts
    assert_eq!(lines[0].2, r"{\k20}{\ko80}หนึ่ง{\ko100}สอง{\ko100}สาม");
    let story = editorkrab::export::Story { id: "s".into(), title: String::new(), sources: vec!["a.mp4".into()], pieces: vec![piece] };
    let doc = json!({"text_roles": ["speech", "emphasis"], "emphasis": {"a1": [2000]}});
    assert_eq!(editorkrab::export::emphasis_lines(&story, &doc), vec![(1000, 3000, "สอง".to_owned())]);
    let (ass, skipped) = subtitles(&story, &doc);
    assert!(ass.contains(r"{\k20}{\ko80}หนึ่ง"), "{ass}");
    assert!(ass.contains("0:00:01.00,0:00:03.00,Text") && ass.contains("}สอง\n"), "{ass}");
    assert!(skipped.is_empty(), "emphasis is drawn now: {skipped:?}");
}
