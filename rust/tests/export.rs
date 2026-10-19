use editorkrab::export::{ffmpeg_args, file_name, plan, subtitles, unused_path};
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
        job("j1", "succeeded", "C:/clips/a.mp4", json!([
            {"id": "a0", "text": " สวัสดีครับ", "start_ms": 0, "end_ms": 1500},
            {"id": "a1", "text": " วันนี้", "start_ms": 1500, "end_ms": 4000},
            {"id": "a2", "text": " ตอนจบ", "start_ms": 9000, "end_ms": 10000}
        ])),
        job("j2", "succeeded", "C:/clips/b.mp4", json!([{"id": "b0", "text": "second file", "start_ms": 200, "end_ms": 1200}])),
        job("j3", "failed", "C:/clips/c.mp4", json!([{"id": "c0", "text": "x", "start_ms": 0, "end_ms": 1}])),
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
fn unknown_or_failed_segments_and_bad_plans_are_errors() {
    let bad = |p: Value| plan(&document(p), &jobs()).unwrap_err();
    assert_eq!(bad(json!({"stories": [{"segment_ids": ["a0", "c0"]}]})), "segment_unknown:c0");
    assert_eq!(bad(json!({"stories": []})), "plan_has_no_stories");
    assert_eq!(bad(json!({"stories": [{"segment_ids": []}]})), "story_empty:1");
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
    assert!(ass.contains("Dialogue: 0,0:00:00.00,0:00:01.00,Text,,0,0,0,,{\\an5\\pos(540,1325)\\fnLeelawadee UI\\fs64\\1c&H0B9EF5&}ตอนจบ"));
    assert!(ass.contains("Dialogue: 0,0:00:01.00,0:00:03.50,Text"));
    assert!(ass.contains("Dialogue: 1,0:00:00.00,0:00:03.50,Text,,0,0,0,,{\\an5\\pos(540,307)\\fnKanit\\fs80\\1c&HFFFFFF&}หัวข้อ (สำคัญ)"));
    assert_eq!(skipped, ["english"]);
    let mut quiet = doc.clone();
    quiet["text_roles"] = json!([]);
    assert!(!subtitles(story, &quiet).0.contains("Dialogue: 0,"));
}

#[test]
fn ffmpeg_cuts_each_piece_from_its_own_input_and_burns_subs() {
    let doc = document(json!({"stories": [{"segment_ids": ["b0", "a1"]}]}));
    let story = &plan(&doc, &jobs()).unwrap()[0];
    let args = ffmpeg_args(story, Path::new("out.mp4"));
    let graph = &args[args.iter().position(|a| a == "-filter_complex").unwrap() + 1];
    assert!(graph.starts_with("[0:v]trim=start=0.200:end=1.200"));
    assert!(graph.contains("[1:v]trim=start=1.500:end=4.000"));
    assert!(graph.ends_with("[v0][a0][v1][a1]concat=n=2:v=1:a=1[cv][ca];[cv]ass=subs.ass[vout]"));
    assert_eq!(args.last().unwrap(), "out.mp4");
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
