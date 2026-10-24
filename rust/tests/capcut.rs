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

#[test]
fn caption_layers_keep_word_clocks_and_translations_in_separate_named_tracks() {
    let story = Story {id:"layers".into(),title:"".into(),sources:vec!["a.mp4".into()],pieces:vec![Piece {
        source:0,start_ms:0,end_ms:4200,segment:"a".into(),timed:true,text:"กินคอนเทนต์กัน".into(),
        // "กัน" after a 2 s pause: its own card (a card holds up to CARD_MAX_MS of speech)
        words:vec![("กิน".into(),100,400),("คอนเทนต์".into(),900,1400),("กัน".into(),3400,3800)],
    }]};
    let doc=json!({"text_roles":["speech","emphasis","english"],
        "emphasis":{"a":[{"text":"คอนเทนต์","start_ms":900,"end_ms":1400}]},
        "subtitles":{"a":"Sometimes I make something and post it right away with a phone."},
        "text_styles":{"english":{"font":"system","size":125,"x":50,"y":90,"color":"#ffffff"}}});
    let draft=capcut::build(&story,&doc,&media(),&fonts,Path::new("root"),Path::new("root/layers"),1).unwrap();
    let texts=draft.content["materials"]["texts"].as_array().unwrap();
    let contents=|seg:&Value| -> String {
        let mat=texts.iter().find(|m|m["id"]==seg["material_id"]).unwrap();
        let body:Value=serde_json::from_str(mat["content"].as_str().unwrap()).unwrap();body["text"].as_str().unwrap().into()
    };
    let track=|name:&str|draft.content["tracks"].as_array().unwrap().iter().find(|t|t["name"]==name).unwrap();
    let speech=track("คำพูด")["segments"].as_array().unwrap();
    assert_eq!(speech.iter().map(contents).collect::<Vec<_>>(),["กิน","กัน"]);
    // the white line stays on until the orange "คอนเทนต์" said after it ends (1.4 s + 180 ms tail), as in Nina
    assert_eq!(speech[0]["target_timerange"],json!({"start":100000,"duration":1480000}));
    let emphasis=track("คำเน้น")["segments"].as_array().unwrap();
    assert_eq!(emphasis.len(),1);assert_eq!(contents(&emphasis[0]),"คอนเทนต์");
    assert_eq!(emphasis[0]["target_timerange"]["start"],900000);
    let english=track("อังกฤษ")["segments"].as_array().unwrap();
    let expected=editorkrab::export::subtitle_lines(&story,&doc);
    assert_eq!(english.len(),1,"legacy translation keeps its known whole-segment span instead of invented page clocks");
    for (seg,(start,end,text)) in english.iter().zip(expected.iter()) {
        assert_eq!(contents(seg),*text);assert_eq!(seg["target_timerange"],json!({"start":start*1000,"duration":(end-start)*1000}));
    }
    assert_eq!(english.len(),expected.len());

    let cues=editorkrab::export::translation_cues(&story,&doc);
    let mut next=doc.clone();
    let translations:serde_json::Map<String,Value>=cues.iter().enumerate().map(|(i,c)|(c.id.clone(),json!(format!("English {}",i+1)))).collect();
    next["english_cues"]=json!({"input_revision":"0123456789abcdef","translations":translations});
    let made=capcut::build(&story,&next,&media(),&fonts,Path::new("root"),Path::new("root/final-cues"),1).unwrap();
    let english=made.content["tracks"].as_array().unwrap().iter().find(|t|t["name"]=="อังกฤษ").unwrap()["segments"].as_array().unwrap();
    assert_eq!(english.len(),cues.len());assert!(english.len()>1,"new translations use final speech-cue anchors");
    for (seg,cue) in english.iter().zip(cues.iter()) {
        assert_eq!(seg["target_timerange"],json!({"start":cue.output_start_ms*1000,"duration":(cue.output_end_ms-cue.output_start_ms)*1000}));
    }
}

#[test]
fn unticked_layers_stay_absent_and_missing_english_is_reported() {
    let mut doc=document();doc["text_roles"]=json!(["speech","english"]);
    let made=capcut::build(&story(),&doc,&media(),&fonts,Path::new("root"),Path::new("root/missing"),1).unwrap();
    assert_eq!(made.missing_roles,["english"]);
    assert!(made.content["tracks"].as_array().unwrap().iter().all(|t|t["name"]!="อังกฤษ"&&t["name"]!="คำเน้น"));
    doc["text_roles"]=json!([]);doc["headline_items"]=json!([]);
    let made=capcut::build(&story(),&doc,&media(),&fonts,Path::new("root"),Path::new("root/none"),1).unwrap();
    assert!(made.missing_roles.is_empty());
    assert_eq!(made.content["tracks"].as_array().unwrap().len(),1);
    assert!(made.content["materials"]["texts"].as_array().unwrap().is_empty());
}

#[test]
fn native_camera_follows_shared_face_crops_and_preserves_continuous_audio_ranges() {
    let story = Story { id:"camera".into(), title:"".into(), sources:vec!["a.mp4".into()],
        pieces:vec![piece(0,1000,11000,"")] };
    let doc=json!({"files":[{"path":"a.mp4","source_id":"src"}],"text_roles":[],"follow_speaker":true,
        "crops":{"src":{"speakers":{"0":{"zoom":1.5}}}},
        "voice":{"src":{"faces":[{"cx":0.62,"cy":0.48},{"cx":0.4,"cy":0.5}],
            "seen":[{"start_ms":0,"end_ms":4000,"person":0},{"start_ms":4000,"end_ms":8000,"person":1}]}}});
    let media=vec![Media{duration_ms:12000,width:2160,height:3840,has_audio:true}];
    let draft=capcut::build(&story,&doc,&media,&fonts,Path::new("root"),Path::new("root/camera"),1).unwrap();
    let video=tracks(&draft,"video");let segments=video[0]["segments"].as_array().unwrap();
    assert_eq!(segments.len(),3,"speaker changes and unknown-gap expiry must also reach native video");
    // the wide shot after the unknown gap is at least MIN_SHOT_MS long here, so it stays
    let expected=[(1000,4000,1.5,-0.36,-0.06),(4000,9201,1.2,0.2,0.0),(9201,11000,1.0,0.0,0.0)];
    let mut target=0;
    for (seg,(a,b,scale,x,y)) in segments.iter().zip(expected) {
        assert_eq!(seg["source_timerange"],json!({"start":a*1000,"duration":(b-a)*1000}));
        assert_eq!(seg["target_timerange"],json!({"start":target*1000,"duration":(b-a)*1000}));
        for (actual,want) in [(seg["clip"]["scale"]["x"].as_f64().unwrap(),scale),
            (seg["clip"]["scale"]["y"].as_f64().unwrap(),scale),
            (seg["clip"]["transform"]["x"].as_f64().unwrap(),x),
            (seg["clip"]["transform"]["y"].as_f64().unwrap(),y)] {assert!((actual-want).abs()<1e-8,"{actual} != {want}");}
        assert_eq!(seg["volume"],1.0);target+=b-a;
    }
    assert_eq!(draft.content["duration"],target*1000);
}

#[test]
fn native_crop_fills_the_selected_canvas_and_honours_manual_slack_position() {
    let story=Story{id:"crop".into(),title:"".into(),sources:vec!["a.mp4".into()],pieces:vec![piece(0,0,1000,"")]};
    let doc=json!({"files":[{"path":"a.mp4","source_id":"src"}],"text_roles":[],"crops":{"src":{"x":1,"y":0.5}}});
    let media=vec![Media{duration_ms:1000,width:1920,height:1080,has_audio:true}];
    let draft=capcut::build(&story,&doc,&media,&fonts,Path::new("root"),Path::new("root/crop"),1).unwrap();
    let video=tracks(&draft,"video");let clip=&video[0]["segments"][0]["clip"];
    let fill=(1920.0/1080.0)/(1080.0/1920.0);
    assert!((clip["scale"]["x"].as_f64().unwrap()-fill).abs()<1e-8);
    assert!((clip["transform"]["x"].as_f64().unwrap()-(1.0-fill)).abs()<1e-8);
    let mut landscape=doc;landscape["output"]=json!({"aspect":"16:9"});
    let draft=capcut::build(&story,&landscape,&media,&fonts,Path::new("root"),Path::new("root/landscape"),1).unwrap();
    assert_eq!(draft.content["canvas_config"]["width"],1920);
    assert_eq!(draft.content["canvas_config"]["height"],1080);
    assert_eq!(tracks(&draft,"video")[0]["segments"][0]["clip"]["scale"],json!({"x":1.0,"y":1.0}));
}

#[test]
fn the_camera_never_cuts_to_a_shot_under_a_second() {
    // the unknown-gap wide shot would be 0.8 s at the end of the piece: the close-up holds instead
    let story = Story { id:"short".into(), title:"".into(), sources:vec!["a.mp4".into()], pieces:vec![piece(0,1000,10000,"")] };
    let doc=json!({"files":[{"path":"a.mp4","source_id":"src"}],"text_roles":[],"follow_speaker":true,
        "voice":{"src":{"faces":[{"cx":0.62,"cy":0.48},{"cx":0.4,"cy":0.5}],
            "seen":[{"start_ms":0,"end_ms":4000,"person":0},{"start_ms":4000,"end_ms":8000,"person":1}]}}});
    let media=vec![Media{duration_ms:12000,width:2160,height:3840,has_audio:true}];
    let draft=capcut::build(&story,&doc,&media,&fonts,Path::new("root"),Path::new("root/short"),1).unwrap();
    let segments=tracks(&draft,"video")[0]["segments"].as_array().unwrap().clone();
    let ranges:Vec<_>=segments.iter().map(|s|s["source_timerange"]["duration"].as_u64().unwrap()/1000).collect();
    assert_eq!(ranges,[3000,6000]);
}
