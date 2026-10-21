use editorkrab::capcut::{self, Media};
use editorkrab::export::{Piece, Story};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

fn piece(source: usize, start_ms: u64, end_ms: u64, text: &str) -> Piece {
    Piece {
        timed: false,
        segment: String::new(),
        source,
        start_ms,
        end_ms,
        text: text.to_owned(),
        words: Vec::new(),
    }
}

fn story() -> Story {
    Story {
        id: "s1".to_owned(),
        title: "เรื่องแรก".to_owned(),
        sources: vec![PathBuf::from("a.mp4"), PathBuf::from("b.mp4")],
        pieces: vec![
            piece(0, 2_000, 5_000, "สวัสดีครับ"),
            piece(1, 10_000, 12_500, "ลาก่อน"),
        ],
    }
}

fn document() -> Value {
    json!({
        "title": "งาน",
        "text_roles": ["speech"],
        "text_styles": {"speech": {"font": "tahoma", "size": 52, "color": "#ff0000", "x": 50, "y": 75}},
        "headline_items": [
            {"text": "หัวคลิป", "font": "family:nofont", "size": 78, "color": "#00ff00", "x": 50, "y": 15},
            {"text": "  ", "font": "tahoma"}
        ]
    })
}

fn media() -> Vec<Media> {
    vec![
        Media {
            duration_ms: 60_000,
            width: 1920,
            height: 1080,
            has_audio: true,
        },
        Media {
            duration_ms: 30_000,
            width: 1080,
            height: 1920,
            has_audio: false,
        },
    ]
}

fn fonts(family: &str) -> Option<PathBuf> {
    (family != "nofont").then(|| PathBuf::from(format!("fonts/{family}.ttf")))
}

fn build() -> capcut::Draft {
    capcut::build(
        &story(),
        &document(),
        &media(),
        &fonts,
        Path::new("root"),
        Path::new("root/งาน · CapCut"),
        1,
    )
    .unwrap()
}

fn tracks<'a>(draft: &'a capcut::Draft, kind: &str) -> Vec<&'a Value> {
    draft.content["tracks"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|t| t["type"] == kind)
        .collect()
}

#[test]
fn footage_is_cut_like_the_export() {
    let draft = build();
    assert_eq!(draft.content["duration"], 5_500_000);
    assert_eq!(draft.content["canvas_config"]["width"], 1080);
    assert_eq!(draft.content["canvas_config"]["height"], 1920);
    let video = tracks(&draft, "video");
    assert_eq!(video.len(), 1);
    let segs = video[0]["segments"].as_array().unwrap();
    assert_eq!(segs.len(), 2);
    assert_eq!(
        segs[0]["source_timerange"],
        json!({"start": 2_000_000, "duration": 3_000_000})
    );
    assert_eq!(
        segs[0]["target_timerange"],
        json!({"start": 0, "duration": 3_000_000})
    );
    assert_eq!(
        segs[1]["source_timerange"],
        json!({"start": 10_000_000, "duration": 2_500_000})
    );
    assert_eq!(
        segs[1]["target_timerange"],
        json!({"start": 3_000_000, "duration": 2_500_000})
    );
    let videos = draft.content["materials"]["videos"].as_array().unwrap();
    for seg in segs {
        let material = videos
            .iter()
            .find(|m| m["id"] == seg["material_id"])
            .unwrap();
        assert!(material["path"].as_str().unwrap().ends_with(".mp4"));
    }
    let b = videos.iter().find(|m| m["path"] == "b.mp4").unwrap();
    assert_eq!(b["has_audio"], false);
    assert_eq!(b["duration"], 30_000_000);
}

#[test]
fn subtitles_and_headline_use_step_4_and_5_styles() {
    let draft = build();
    let texts = draft.content["materials"]["texts"].as_array().unwrap();
    let body =
        |m: &Value| -> Value { serde_json::from_str(m["content"].as_str().unwrap()).unwrap() };
    let speech = texts.iter().find(|m| body(m)["text"] == "สวัสดีครับ").unwrap();
    assert_eq!(speech["font_name"], "Tahoma");
    assert_eq!(speech["font_path"], "fonts/Tahoma.ttf");
    assert_eq!(speech["text_color"], "#ff0000");
    assert_eq!(
        body(speech)["styles"][0]["fill"]["content"]["solid"]["color"],
        json!([1.0, 0.0, 0.0])
    );
    assert_eq!(
        body(speech)["styles"][0]["range"],
        json!([0, "สวัสดีครับ".encode_utf16().count()])
    );
    let headline = texts.iter().find(|m| body(m)["text"] == "หัวคลิป").unwrap();
    // a font this machine lacks falls back and is reported
    assert_eq!(
        headline["font_path"],
        format!("fonts/{}.ttf", capcut::FALLBACK_FONT)
    );
    assert_eq!(draft.missing_fonts, vec!["nofont".to_owned()]);
    // the blank headline item is not drawn
    assert_eq!(texts.len(), 3, "{texts:#?}");

    let text_segs: Vec<&Value> = tracks(&draft, "text")
        .iter()
        .flat_map(|t| t["segments"].as_array().unwrap())
        .collect();
    let seg_of = |m: &Value| {
        text_segs
            .iter()
            .find(|s| s["material_id"] == m["id"])
            .copied()
            .unwrap()
    };
    let title = seg_of(headline);
    assert_eq!(
        title["target_timerange"],
        json!({"start": 0, "duration": 5_500_000})
    );
    // y 15% from the top is 0.7 above centre
    assert!((title["clip"]["transform"]["y"].as_f64().unwrap() - 0.7).abs() < 1e-9);
    let bye = texts.iter().find(|m| body(m)["text"] == "ลาก่อน").unwrap();
    assert_eq!(seg_of(bye)["target_timerange"]["start"], 3_000_000);
}

#[test]
fn every_reference_points_at_a_material() {
    let draft = build();
    let mut ids = std::collections::HashSet::new();
    for list in draft.content["materials"].as_object().unwrap().values() {
        for m in list.as_array().into_iter().flatten() {
            assert!(
                ids.insert(m["id"].as_str().unwrap().to_owned()),
                "duplicate id {m}"
            );
        }
    }
    for track in draft.content["tracks"].as_array().unwrap() {
        for seg in track["segments"].as_array().unwrap() {
            assert!(ids.contains(seg["material_id"].as_str().unwrap()));
            for r in seg["extra_material_refs"].as_array().unwrap() {
                assert!(ids.contains(r.as_str().unwrap()));
            }
        }
    }
    assert_eq!(draft.meta["draft_fold_path"], "root/งาน · CapCut");
    assert_eq!(draft.meta["draft_name"], "งาน · CapCut");
}

/// Writes one real project into this machine's CapCut folder so it can be opened by hand:
/// `EDITORKRAB_CAPCUT_PILOT=<video with sound> cargo test --test capcut -- --ignored`
#[test]
#[ignore]
fn pilot_into_real_capcut() {
    let video = PathBuf::from(std::env::var("EDITORKRAB_CAPCUT_PILOT").unwrap());
    let probe = std::process::Command::new("ffprobe")
        .args([
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=width,height",
            "-of",
            "json",
        ])
        .arg(&video)
        .output()
        .unwrap();
    let info: Value = serde_json::from_slice(&probe.stdout).unwrap();
    let stream = &info["streams"][0];
    let seconds: f64 = info["format"]["duration"]
        .as_str()
        .unwrap()
        .parse()
        .unwrap();
    let media = vec![Media {
        duration_ms: (seconds * 1000.0) as u64,
        width: stream["width"].as_u64().unwrap(),
        height: stream["height"].as_u64().unwrap(),
        has_audio: true,
    }];
    let story = Story {
        id: "pilot".to_owned(),
        title: "ทดสอบ".to_owned(),
        sources: vec![video],
        pieces: vec![
            piece(0, 1_000, 4_000, "ซับแรกภาษาไทย"),
            piece(0, 7_000, 10_000, "ซับที่สอง"),
        ],
    };
    let mut doc = document();
    doc["text_styles"]["speech"]["font"] = json!("tahoma");
    doc["headline_items"][0]["font"] = json!("system");
    let fonts = capcut::installed_fonts(&capcut::font_folders());
    let lookup = |f: &str| fonts.get(&f.to_lowercase()).cloned();
    let root = capcut::drafts_root().expect("CapCut drafts folder");
    let folder = capcut::unused_folder(&root, "EditorKrab pilot");
    let draft = capcut::build(&story, &doc, &media, &lookup, &root, &folder, 1).unwrap();
    assert!(draft.missing_fonts.is_empty(), "{:?}", draft.missing_fonts);
    capcut::write(&folder, &draft).unwrap();
    println!("wrote {}", folder.display());
}

#[test]
fn write_never_replaces_an_existing_project() {
    let root = std::env::temp_dir().join(format!("ek-capcut-{}", uuid::Uuid::new_v4().simple()));
    std::fs::create_dir_all(&root).unwrap();
    let first = capcut::unused_folder(&root, "งาน");
    capcut::write(&first, &build()).unwrap();
    assert!(first.join("draft_content.json").is_file());
    assert!(first.join("draft_meta_info.json").is_file());
    assert!(capcut::write(&first, &build()).is_err());
    let second = capcut::unused_folder(&root, "งาน");
    assert_ne!(first, second);
    assert!(second.to_string_lossy().ends_with("(2)"));
    // no staging folder is left behind after the failed write
    let left: Vec<_> = std::fs::read_dir(&root)
        .unwrap()
        .filter_map(|e| e.ok())
        .collect();
    assert_eq!(left.len(), 1);
    let _ = std::fs::remove_dir_all(&root);
}
