//! Step 6 "แก้ต่อใน CapCut": each story as a native CapCut project, so a person can keep editing it.
//! The footage is cut exactly like the MP4 export and the subtitles and headline items use the step
//! 4/5 font, size, colour and position. Projects are written into the folder CapCut lists projects
//! from, under a new name, so nothing that already exists there is replaced.
use crate::export::{self, Story};
use serde_json::{Value, json};
use std::collections::HashMap;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

/// An empty CapCut project plus one footage and one text block, taken from a hand-made project
/// with every name, path and machine id removed.
const TEMPLATE: &str = include_str!("../data/capcut_template.json");
/// Pixels per CapCut font-size unit on a 1080-wide canvas, as measured for the old app.
const PX_PER_UNIT: f64 = 5.2;
/// What the export falls back to (see `export::font`), so both outputs look the same.
pub const FALLBACK_FONT: &str = "Leelawadee UI";

#[derive(Clone, Debug, PartialEq)]
pub struct Media {
    pub duration_ms: u64,
    pub width: u64,
    pub height: u64,
    pub has_audio: bool,
}

pub struct Draft {
    pub content: Value,
    pub meta: Value,
    /// Step 4/5 fonts this machine does not have; CapCut shows the fallback font instead.
    pub missing_fonts: Vec<String>,
    pub missing_roles: Vec<String>,
}

fn id() -> String {
    uuid::Uuid::new_v4().to_string().to_uppercase()
}
fn us(ms: u64) -> u64 {
    ms * 1000
}
fn posix(path: &Path) -> String {
    path.display().to_string().replace('\\', "/")
}
fn push(materials: &mut Value, kind: &str, value: Value) {
    match materials.get_mut(kind).and_then(Value::as_array_mut) {
        Some(list) => list.push(value),
        None => materials[kind] = json!([value]),
    }
}
/// A new segment for `material`, with fresh copies of the block's helper materials.
fn segment(materials: &mut Value, block: &Value, kind: &str, mut material: Value) -> Value {
    let material_id = id();
    material["id"] = json!(material_id);
    push(materials, kind, material);
    let mut seg = block["seg"].clone();
    seg["id"] = json!(id());
    seg["material_id"] = json!(material_id);
    let mut refs = Vec::new();
    for helper in block["refs"].as_array().into_iter().flatten() {
        let (Some(kind), mut helper) = (helper[0].as_str(), helper[1].clone()) else {
            continue;
        };
        let helper_id = id();
        helper["id"] = json!(helper_id);
        push(materials, kind, helper);
        refs.push(json!(helper_id));
    }
    seg["extra_material_refs"] = Value::Array(refs);
    seg
}
/// `#rrggbb` as CapCut's 0-1 RGB list; anything else is white, like the export.
fn rgb(value: &str) -> Value {
    let hex = value.trim_start_matches('#');
    let channel = |i: usize| {
        hex.get(i..i + 2)
            .and_then(|c| u8::from_str_radix(c, 16).ok())
            .map_or(1.0, |c| f64::from(c) / 255.0)
    };
    if hex.len() != 6 {
        return json!([1.0, 1.0, 1.0]);
    }
    json!([channel(0), channel(2), channel(4)])
}

/// One text on screen from `start_ms` for `duration_ms`, styled like the step 4/5 preview.
fn text(
    materials: &mut Value,
    block: &Value,
    words: &str,
    start_ms: u64,
    duration_ms: u64,
    style: &Value,
    font: &Path,
    frame: (u32, u32),
) -> Value {
    let fitted = crate::text_layout::fitted_in(style, words, frame.1 as f64 * 1080.0 / frame.0 as f64);
    let style = &fitted;
    let family = export::font(style["font"].as_str().unwrap_or("system"));
    let size = (style["size"].as_f64().unwrap_or(64.0) / PX_PER_UNIT * 100.0).round() / 100.0;
    let colour = style["color"].as_str().unwrap_or("#ffffff");
    let mut material = block["mat"].clone();
    let mut body: Value = serde_json::from_str(material["content"].as_str().unwrap_or(""))
        .expect("capcut_template.json text content is JSON");
    let mut look = body["styles"][0].clone();
    look["range"] = json!([0, words.encode_utf16().count()]);
    look["size"] = json!(size);
    look["fill"]["content"]["solid"]["color"] = rgb(colour);
    look["font"] = json!({"path": posix(font), "id": ""});
    let outline=style["outline"].as_f64().unwrap_or(0.0)/100.0;
    let border=style["outline_color"].as_str().unwrap_or("#000000");
    look["strokes"]=if outline>0.0 {json!([{"content":{"render_type":"solid","solid":{"color":rgb(border)}},"width":outline,"mode":0}])} else {json!([])};
    material["border_width"]=json!(outline);
    material["border_color"]=json!(border);
    material["has_shadow"]=json!(style["shadow"].as_f64().unwrap_or(0.0)>0.0);
    material["shadow_color"]=json!(style["shadow_color"].as_str().unwrap_or("#000000"));
    material["shadow_alpha"]=json!(style["shadow_opacity"].as_f64().unwrap_or(60.0)/100.0);
    material["shadow_distance"]=json!(style["shadow"].as_f64().unwrap_or(0.0));
    material["italic_degree"]=json!(if style["italic"]==true {10} else {0});
    material["underline"]=json!(style["underline"]==true);
    body["text"] = json!(words);
    body["styles"] = json!([look]);
    material["content"] = json!(body.to_string());
    material["name"] = json!(words.chars().take(20).collect::<String>());
    material["font_path"] = json!(posix(font));
    material["font_name"] = json!(family);
    material["font_title"] = json!(family);
    material["font_size"] = json!(size);
    material["text_size"] = json!(size.round());
    material["text_color"] = json!(colour.to_lowercase());
    // the export draws each subtitle on one line; leave CapCut room so it does not wrap them
    material["line_max_width"] = json!(0.9);
    let mut seg = segment(materials, block, "texts", material);
    seg["target_timerange"] = json!({"start": us(start_ms), "duration": us(duration_ms).max(1)});
    // CapCut places a text by its centre, from -1 (left/bottom) to 1 (right/top)
    let x = style["x"].as_f64().unwrap_or(50.0);
    let y = style["y"].as_f64().unwrap_or(50.0);
    seg["clip"]["transform"] = json!({"x": x / 50.0 - 1.0, "y": 1.0 - y / 50.0});
    seg["clip"]["scale"] = json!({"x": 1.0, "y": 1.0});
    seg
}

fn track(kind: &str, segments: Vec<Value>) -> Value {
    json!({"type": kind, "attribute": 0, "flag": 0, "is_default_name": true, "id": id(), "segments": segments})
}
/// Texts that overlap in time cannot share a CapCut track, so each one goes on the first free lane.
fn text_tracks(mut texts: Vec<Value>, render_index: u64, name: &str) -> Vec<Value> {
    let start = |s: &Value| s["target_timerange"]["start"].as_u64().unwrap_or(0);
    let end = |s: &Value| start(s) + s["target_timerange"]["duration"].as_u64().unwrap_or(0);
    texts.sort_by_key(start);
    let mut lanes: Vec<Vec<Value>> = Vec::new();
    for mut seg in texts {
        seg["render_index"] = json!(render_index);
        match lanes
            .iter_mut()
            .find(|lane| lane.last().is_some_and(|last| end(last) <= start(&seg)))
        {
            Some(lane) => lane.push(seg),
            None => lanes.push(vec![seg]),
        }
    }
    lanes.into_iter().map(|lane| {let mut t=track("text",lane);t["name"]=json!(name);t["is_default_name"]=json!(false);t}).collect()
}

/// CapCut scale 1 contains the entire source in the canvas. Its translation is in half-canvas
/// units, positive Y upwards (also used by native text positioning above). Project the exact
/// source window used by the shared FFmpeg crop into those native coordinates.
fn video_transform(document: &Value, source: &Path, media: &Media, ms: u64, frame: (u32,u32)) -> (f64,f64,f64) {
    let (iw,ih)=(media.width as f64,media.height as f64);
    let (width,height)=(frame.0 as f64,frame.1 as f64);
    let (cx,cy,zoom,centred)=export::crop_at(document,source,ms);
    let shape=width/height;
    let (ow,oh)=(iw.min(ih*shape)/zoom,ih.min(iw/shape)/zoom);
    let (left,top)=if centred {((iw*cx-ow/2.0).clamp(0.0,iw-ow),(ih*cy-oh/2.0).clamp(0.0,ih-oh))}
        else {((iw-ow)*cx,(ih-oh)*cy)};
    let fit=(width/iw).min(height/ih);
    let scale=(width/ow)/fit;
    (scale,(iw-ow-2.0*left)/ow,(2.0*top+oh-ih)/oh)
}

/// Shot sizes, one per cut in turn, when the camera follows the person talking in a vertical picture in a
/// vertical frame: medium on them, close on them, medium again, then wide. CapCut scale 1 is the whole picture;
/// the numbers are the ones in P'Ohm's own cut of C4926 ("Punthai เปิดพันธุ์ไทย ต้องจ่ายอะไรบ้าง", 26 Oct 2026).
pub const SHOTS: [f64; 4] = [1.9, 2.67, 1.9, 1.3];

/// The cut's (scale, x, y) from `SHOTS` when it applies (following on, a face found for the person talking,
/// vertical picture and frame); the face sits in the middle of the frame, the wide shot is centred. A crop or
/// zoom the person set, or nobody known to be talking, keeps the window `video_transform` gives.
fn shot(document: &Value, source: &Path, media: &Media, ms: u64, frame: (u32,u32), cut: usize) -> Option<(f64,f64,f64)> {
    if document["follow_speaker"]!=true || media.width>=media.height || frame.0>=frame.1 {return None;}
    let (cx,cy,zoom,centred)=export::crop_at(document,source,ms);
    // a zoom set for this person in step 3 is theirs to keep
    if !centred || zoom!=export::FOLLOW_ZOOM {return None;}
    let wide=cut%SHOTS.len()==SHOTS.len()-1;
    let scale=SHOTS[cut%SHOTS.len()];
    let (cx,cy)=if wide {(0.5,0.5)} else {(cx,cy)};
    let (iw,ih)=(media.width as f64,media.height as f64);
    let (width,height)=(frame.0 as f64,frame.1 as f64);
    let fit=(width/iw).min(height/ih);
    // translation is in half-frame units, Y upwards: move the picture so the face lands in the middle, but
    // never so far that an edge of the picture shows inside the frame
    let (sw,sh)=(iw*fit*scale/width,ih*fit*scale/height);
    let room=|half:f64|(half-1.0).max(0.0);
    Some((scale,((0.5-cx)*2.0*sw).clamp(-room(sw),room(sw)),((cy-0.5)*2.0*sh).clamp(-room(sh),room(sh))))
}

/// The CapCut project for one story. `media` describes each of `story.sources`; `fonts` finds the
/// file of an installed font family. `folder` is where the project will live inside `root`.
pub fn build(
    story: &Story,
    document: &Value,
    media: &[Media],
    fonts: &dyn Fn(&str) -> Option<PathBuf>,
    root: &Path,
    folder: &Path,
    now_us: u64,
) -> Result<Draft, String> {
    let frame=export::frame(document);
    let template: Value = serde_json::from_str(TEMPLATE).expect("capcut_template.json is JSON");
    let mut content = template["draft"].clone();
    let mut materials = content["materials"].clone();
    let mut missing_fonts: Vec<String> = Vec::new();
    let mut font_file = |style: &Value| -> PathBuf {
        let family = export::font(style["font"].as_str().unwrap_or("system"));
        if let Some(file) = fonts(family) {
            return file;
        }
        if !missing_fonts.iter().any(|f| f == family) {
            missing_fonts.push(family.to_owned());
        }
        fonts(FALLBACK_FONT).unwrap_or_default()
    };

    let mut footage = Vec::new();
    let mut at = 0;
    for piece in &story.pieces {
        let source = &story.sources[piece.source];
        let info = media.get(piece.source).ok_or("capcut_media_missing")?;
        if info.width==0 || info.height==0 {return Err("capcut_media_dimensions_invalid".to_owned());}
        let spans=if document["follow_speaker"]==true {
            export::source_id(document,source).map(|id|export::speaker_cuts(document,id,piece.start_ms,piece.end_ms))
                .unwrap_or_else(||vec![(piece.start_ms,piece.end_ms)])
        } else {vec![(piece.start_ms,piece.end_ms)]};
        for (start,end) in spans {
            let length = end-start;
            let name = source
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default();
            let mut material = template["video"]["mat"].clone();
            material["path"] = json!(posix(source));
            material["material_name"] = json!(name);
            material["name"] = json!(name);
            material["type"] = json!("video");
            material["duration"] = json!(us(info.duration_ms));
            material["width"] = json!(info.width);
            material["height"] = json!(info.height);
            material["has_audio"] = json!(info.has_audio);
            material["local_material_id"] = json!(id());
            material["unique_id"] = json!(uuid::Uuid::new_v4().simple().to_string());
            let mut seg = segment(&mut materials, &template["video"], "videos", material);
            seg["source_timerange"] = json!({"start": us(start), "duration": us(length)});
            seg["target_timerange"] = json!({"start": us(at), "duration": us(length)});
            seg["volume"] = json!(1.0);
            seg["last_nonzero_volume"] = json!(1.0);
            seg["render_index"] = json!(0);
            seg["track_render_index"] = json!(0);
            let (scale,x,y)=shot(document,source,info,start,frame,footage.len())
                .unwrap_or_else(||video_transform(document,source,info,start,frame));
            seg["clip"]["scale"] = json!({"x": scale, "y": scale});
            seg["clip"]["transform"] = json!({"x": x, "y": y});
            footage.push(seg);
            at += length;
        }
    }
    let total = at;

    let block = &template["text"];
    let lines = export::speech_cues(story, document);
    let speech_style = export::speech_style(document);
    let speech_font = if lines.is_empty() {
        PathBuf::new()
    } else {
        font_file(&speech_style)
    };
    let speech = lines
        .iter()
        .map(|(start, end, words)| {
            text(
                &mut materials,
                block,
                words,
                *start,
                end - start,
                &speech_style,
                &speech_font,
                frame,
            )
        })
        .collect();
    let mut extra = Vec::new();
    let mut missing_roles = Vec::new();
    for (role,name,index,cues) in [
        ("emphasis","คำเน้น",14002,export::emphasis_cues(story,document)),
        ("english","อังกฤษ",14003,export::subtitle_lines(story,document)),
    ] {
        if cues.is_empty() {
            if document["text_roles"].as_array().is_some_and(|roles|roles.iter().any(|r|r==role)) {missing_roles.push(role.to_owned());}
            continue;
        }
        let style=export::text_style(document,role);
        let font=font_file(&style);
        let segments=cues.iter().map(|(a,b,words)|text(&mut materials,block,words,*a,b-a,&style,&font,frame)).collect();
        extra.extend(text_tracks(segments,index,name));
    }
    let mut titles = Vec::new();
    for item in export::headlines(document) {
        let font = font_file(&item);
        let words = item["text"].as_str().unwrap_or("").trim();
        titles.push(text(&mut materials, block, words, 0, total, &item, &font,frame));
    }

    let mut tracks = vec![track("video", footage)];
    tracks.extend(text_tracks(speech, 14001,"คำพูด"));
    tracks.extend(extra);
    tracks.extend(text_tracks(titles, 14004,"หัวคลิป"));
    let name = folder
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let draft_id = id();
    content["id"] = json!(draft_id);
    content["name"] = json!(name);
    content["duration"] = json!(us(total));
    content["canvas_config"] =
        json!({"ratio": "original", "width": frame.0, "height": frame.1, "background": null});
    content["tracks"] = Value::Array(tracks);
    content["materials"] = materials;

    let mut meta = template["meta"].clone();
    meta["draft_fold_path"] = json!(posix(folder));
    meta["draft_name"] = json!(name);
    meta["draft_root_path"] = json!(posix(root));
    meta["draft_id"] = json!(id());
    meta["tm_draft_create"] = json!(now_us);
    meta["tm_draft_modified"] = json!(now_us);
    meta["tm_duration"] = json!(us(total));
    Ok(Draft {
        content,
        meta,
        missing_fonts,
        missing_roles,
    })
}

/// `root/<title> · CapCut`, or `… (2)` and so on: a project folder that does not exist yet.
pub fn unused_folder(root: &Path, title: &str) -> PathBuf {
    let first = root.join(format!("{title} · CapCut"));
    if !first.exists() {
        return first;
    }
    (2..)
        .map(|n| root.join(format!("{title} · CapCut ({n})")))
        .find(|p| !p.exists())
        .expect("unbounded")
}
/// Writes the project next to `folder` first and renames it into place, so CapCut never lists a
/// half-written project, and an existing folder is never written into.
pub fn write(folder: &Path, draft: &Draft) -> Result<(), String> {
    let root = folder.parent().ok_or("capcut_folder_invalid")?;
    let staging = root.join(format!(".editorkrab-{}", uuid::Uuid::new_v4().simple()));
    let result = (|| {
        std::fs::create_dir(&staging)?;
        std::fs::write(
            staging.join("draft_content.json"),
            serde_json::to_vec(&draft.content)?,
        )?;
        std::fs::write(
            staging.join("draft_meta_info.json"),
            serde_json::to_vec(&draft.meta)?,
        )?;
        if folder.exists() {
            return Err(std::io::Error::from(std::io::ErrorKind::AlreadyExists));
        }
        std::fs::rename(&staging, folder)
    })();
    if let Err(e) = result {
        let _ = std::fs::remove_dir_all(&staging);
        return Err(format!("capcut_write:{e}"));
    }
    Ok(())
}

/// The folder CapCut lists projects from: the one picked in CapCut's own settings, else its default.
pub fn drafts_root() -> Option<PathBuf> {
    if let Ok(path) = std::env::var("EDITORKRAB_CAPCUT_DRAFTS") {
        return Some(path.into());
    }
    let data = if cfg!(windows) {
        PathBuf::from(std::env::var("LOCALAPPDATA").ok()?).join("CapCut/User Data")
    } else {
        PathBuf::from(std::env::var("HOME").ok()?).join("Movies/CapCut/User Data")
    };
    let setting = std::fs::read_to_string(data.join("Config/globalSetting")).unwrap_or_default();
    let custom = setting
        .lines()
        .find_map(|l| l.strip_prefix("currentCustomDraftPath="))
        .map(|v| PathBuf::from(v.trim().replace("\\\\", "\\")))
        .filter(|p| p.is_dir());
    custom.or_else(|| Some(data.join("Projects/com.lveditor.draft")).filter(|p| p.is_dir()))
}

fn be16(b: &[u8], at: usize) -> Option<u16> {
    Some(u16::from_be_bytes(b.get(at..at + 2)?.try_into().ok()?))
}
fn be32(b: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_be_bytes(b.get(at..at + 4)?.try_into().ok()?))
}
fn read_at(file: &mut File, at: u64, length: usize) -> Option<Vec<u8>> {
    let mut buf = vec![0; length];
    file.seek(SeekFrom::Start(at)).ok()?;
    file.read_exact(&mut buf).ok()?;
    Some(buf)
}
/// Family names and style name of every face in a TrueType/OpenType file or collection.
pub fn font_names(path: &Path) -> Vec<(Vec<String>, String)> {
    let Ok(mut file) = File::open(path) else {
        return Vec::new();
    };
    let Some(head) = read_at(&mut file, 0, 12) else {
        return Vec::new();
    };
    let faces: Vec<u64> = if &head[..4] == b"ttcf" {
        let count = be32(&head, 8).unwrap_or(0).min(64) as usize;
        read_at(&mut file, 12, count * 4)
            .map(|b| {
                (0..count)
                    .filter_map(|i| be32(&b, i * 4))
                    .map(u64::from)
                    .collect()
            })
            .unwrap_or_default()
    } else {
        vec![0]
    };
    let mut out = Vec::new();
    for face in faces {
        let Some(dir) = read_at(&mut file, face, 12) else {
            continue;
        };
        let tables = be16(&dir, 4).unwrap_or(0) as usize;
        let Some(records) = read_at(&mut file, face + 12, tables * 16) else {
            continue;
        };
        let Some((offset, length)) = (0..tables).find_map(|i| {
            (&records[i * 16..i * 16 + 4] == b"name")
                .then(|| Some((be32(&records, i * 16 + 8)?, be32(&records, i * 16 + 12)?)))
                .flatten()
        }) else {
            continue;
        };
        let Some(table) = read_at(&mut file, offset.into(), (length as usize).min(1 << 20)) else {
            continue;
        };
        let (count, strings) = (
            be16(&table, 2).unwrap_or(0) as usize,
            be16(&table, 4).unwrap_or(0) as usize,
        );
        // name id -> (English Windows name?, text)
        let mut names: HashMap<u16, (bool, String)> = HashMap::new();
        for i in 0..count {
            let r = 6 + i * 12;
            let field = |n: usize| be16(&table, r + n);
            let (Some(platform), Some(language), Some(name), Some(len), Some(at)) =
                (field(0), field(4), field(6), field(8), field(10))
            else {
                break;
            };
            if ![1, 2, 16, 17].contains(&name) {
                continue;
            }
            let start = strings + at as usize;
            let Some(raw) = table.get(start..start + len as usize) else {
                continue;
            };
            let value = match platform {
                0 | 3 => String::from_utf16_lossy(
                    &raw.as_chunks::<2>()
                        .0
                        .iter()
                        .map(|c| u16::from_be_bytes(*c))
                        .collect::<Vec<_>>(),
                ),
                1 => raw.iter().map(|&b| b as char).collect(),
                _ => continue,
            };
            let english = platform == 3 && language == 0x409;
            if !names.get(&name).is_some_and(|(e, _)| *e) && !value.trim().is_empty() {
                names.insert(name, (english, value.trim().to_owned()));
            }
        }
        let get = |id: u16| names.get(&id).map(|(_, v)| v.clone());
        let families: Vec<String> = [get(1), get(16)].into_iter().flatten().collect();
        if !families.is_empty() {
            out.push((families, get(17).or_else(|| get(2)).unwrap_or_default()));
        }
    }
    out
}
/// Installed font families (lower case) and the file to use for each, preferring the regular style.
pub fn installed_fonts(folders: &[PathBuf]) -> HashMap<String, PathBuf> {
    let mut found: HashMap<String, (bool, PathBuf)> = HashMap::new();
    for folder in folders {
        let Ok(entries) = std::fs::read_dir(folder) else {
            continue;
        };
        for path in entries.filter_map(|e| e.ok()).map(|e| e.path()) {
            let font = path.extension().is_some_and(|e| {
                ["ttf", "otf", "ttc", "otc"].contains(&e.to_string_lossy().to_lowercase().as_str())
            });
            if !font {
                continue;
            }
            for (families, style) in font_names(&path) {
                let regular = style.eq_ignore_ascii_case("regular");
                for family in families {
                    let key = family.to_lowercase();
                    if !found.get(&key).is_some_and(|(r, _)| *r || !regular) {
                        found.insert(key, (regular, path.clone()));
                    }
                }
            }
        }
    }
    found.into_iter().map(|(k, (_, p))| (k, p)).collect()
}
/// Where this machine keeps fonts, system-wide and per user.
pub fn font_folders() -> Vec<PathBuf> {
    let env = |k: &str| std::env::var(k).ok().map(PathBuf::from);
    if cfg!(windows) {
        [
            env("WINDIR").map(|w| w.join("Fonts")),
            env("LOCALAPPDATA").map(|l| l.join("Microsoft/Windows/Fonts")),
        ]
        .into_iter()
        .flatten()
        .collect()
    } else if cfg!(target_os = "macos") {
        [
            Some(PathBuf::from("/System/Library/Fonts")),
            Some(PathBuf::from("/Library/Fonts")),
            env("HOME").map(|h| h.join("Library/Fonts")),
        ]
        .into_iter()
        .flatten()
        .collect()
    } else {
        [
            Some(PathBuf::from("/usr/share/fonts")),
            env("HOME").map(|h| h.join(".fonts")),
        ]
        .into_iter()
        .flatten()
        .collect()
    }
}
