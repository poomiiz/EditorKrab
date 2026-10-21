//! Step 6: turns the saved draft (AI plan + subtitle and headline styles) into one 9:16 MP4 per story.
//! The plan only names transcript segments; times, text and source files come from finished jobs.
use crate::jobs::{Job, Task};
use serde::Serialize;
use serde_json::Value;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

pub const WIDTH: u32 = 1080;
pub const HEIGHT: u32 = 1920;

/// The finished clip's size for the shape picked in step 3: 9:16 unless 1:1 or 16:9 was chosen.
pub fn frame(document: &Value) -> (u32, u32) {
    match document["output"]["aspect"].as_str() {
        Some("1:1") => (1080, 1080),
        Some("16:9") => (1920, 1080),
        _ => (WIDTH, HEIGHT),
    }
}

/// Where a video's crop window sits, 0..1 across the slack left and right (x) and above and below (y);
/// the window is the largest one of the clip's shape. Keyed by the draft's source_id, centred when unset.
pub fn crop(document: &Value, path: &Path) -> (f64, f64) {
    let id = document["files"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|f| f["path"].as_str().map(Path::new) == Some(path))
        .and_then(|f| f["source_id"].as_str());
    let c = id.map(|id| &document["crops"][id]).unwrap_or(&Value::Null);
    let at = |v: &Value| v.as_f64().filter(|v| v.is_finite()).unwrap_or(0.5).clamp(0.0, 1.0);
    (at(&c["x"]), at(&c["y"]))
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Piece {
    /// Index into `Story::sources`.
    pub source: usize,
    pub start_ms: u64,
    pub end_ms: u64,
    pub text: String,
    /// Timed dictionary words (source time); empty when the engine gave none.
    pub words: Vec<(String, u64, u64)>,
    /// The line had timed words before trimming: none left means nothing is said, not "spread the text".
    #[serde(default)]
    pub timed: bool,
    /// The transcript line (segment id) this piece plays.
    #[serde(default)]
    pub segment: String,
}
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Story {
    pub id: String,
    pub title: String,
    pub sources: Vec<PathBuf>,
    pub pieces: Vec<Piece>,
}
impl Story {
    pub fn duration_ms(&self) -> u64 {
        self.pieces.iter().map(|p| p.end_ms - p.start_ms).sum()
    }
}

struct Spoken {
    path: PathBuf,
    start_ms: u64,
    end_ms: u64,
    text: String,
    words: Vec<(String, u64, u64)>,
}

/// Every segment of every finished transcription in the project, with hand corrections applied.
fn spoken(document: &Value, jobs: &[Job]) -> HashMap<String, Spoken> {
    // same rule as the step 1 editor: a correction applies only to the exact text it was made on
    let edits: HashMap<(&str, &str), (&str, &str)> = document["transcript_edits"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|e| {
            Some((
                (e["job_id"].as_str()?, e["segment_id"].as_str()?),
                (e["original_text"].as_str()?, e["text"].as_str()?),
            ))
        })
        .collect();
    let mut found = HashMap::new();
    for job in jobs.iter().filter(|j| j.status == "succeeded") {
        let Task::Transcribe { path, .. } = &job.task else {
            continue;
        };
        let segments = job
            .result
            .as_ref()
            .and_then(|r| r["transcript"]["segments"].as_array());
        for s in segments.into_iter().flatten() {
            let (Some(id), Some(start_ms), Some(end_ms), Some(text)) = (
                s["id"].as_str(),
                s["start_ms"].as_u64(),
                s["end_ms"].as_u64(),
                s["text"].as_str(),
            ) else {
                continue;
            };
            let edited = edits
                .get(&(job.id.as_str(), id))
                .filter(|(original, _)| *original == text);
            // a corrected line no longer matches its timed words, so it is split by length instead
            let words = match edited {
                Some(_) => Vec::new(),
                None => s["words"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|w| {
                        Some((
                            w["text"].as_str()?.to_owned(),
                            w["start_ms"].as_u64()?,
                            w["end_ms"].as_u64()?,
                        ))
                    })
                    .collect(),
            };
            let text = edited.map_or(text, |(_, fixed)| fixed);
            found.insert(
                id.to_owned(),
                Spoken {
                    path: PathBuf::from(path),
                    start_ms,
                    end_ms,
                    text: text.trim().to_owned(),
                    words,
                },
            );
        }
    }
    found
}

/// The segments a plan may name, grouped per source file in time order: `(id, start_ms, end_ms, text)`.
pub type Line = (String, u64, u64, String);
pub fn transcript(document: &Value, jobs: &[Job]) -> Vec<(PathBuf, Vec<Line>)> {
    let mut files: Vec<(PathBuf, Vec<Line>)> = Vec::new();
    for (id, s) in spoken(document, jobs) {
        if s.end_ms <= s.start_ms {
            continue;
        }
        let at = match files.iter().position(|(path, _)| *path == s.path) {
            Some(at) => at,
            None => {
                files.push((s.path.clone(), Vec::new()));
                files.len() - 1
            }
        };
        files[at].1.push((id, s.start_ms, s.end_ms, s.text));
    }
    for (_, lines) in &mut files {
        lines.sort_by(|a, b| (a.1, a.2, &a.0).cmp(&(b.1, b.2, &b.0)));
    }
    files
}

/// A line's trimmed span: the same limits as the step 3 editor, so a bad value cannot reach ffmpeg.
fn trim(t: &Value, start: u64, end: u64) -> (u64, u64) {
    const REACH: u64 = 2000;
    const SHORTEST: u64 = 200;
    let round = |v: u64| (v + 50) / 100 * 100;
    let from = t["start_ms"].as_u64().map(round).unwrap_or(start);
    let from = from.clamp(start.saturating_sub(REACH), end.saturating_sub(SHORTEST).max(start.saturating_sub(REACH)));
    let to = t["end_ms"].as_u64().map(round).unwrap_or(end);
    (from, to.clamp(from + SHORTEST, (end + REACH).max(from + SHORTEST)))
}

/// The stories to render, in plan order. Any unknown segment is an error, never a silent skip.
pub fn plan(document: &Value, jobs: &[Job]) -> Result<Vec<Story>, String> {
    let raw = document["plan"].as_str().unwrap_or("").trim();
    if raw.is_empty() {
        return Err("plan_missing".into());
    }
    let reply: Value = serde_json::from_str(raw).map_err(|_| "plan_invalid".to_string())?;
    // a bare list is a plan whose {"stories": ...} wrapper the AI dropped; step 2 reads it the same way
    let stories = reply["stories"]
        .as_array()
        .or_else(|| reply.as_array())
        .filter(|s| !s.is_empty())
        .ok_or("plan_has_no_stories")?;
    let spoken = spoken(document, jobs);
    let mut out = Vec::new();
    for (n, story) in stories.iter().enumerate() {
        let ids = story["segment_ids"]
            .as_array()
            .filter(|s| !s.is_empty())
            .ok_or_else(|| format!("story_empty:{}", n + 1))?;
        let mut sources: Vec<PathBuf> = Vec::new();
        let mut pieces = Vec::new();
        for id in ids {
            let id = id.as_str().ok_or("plan_invalid")?;
            let s = spoken
                .get(id)
                .ok_or_else(|| format!("segment_unknown:{id}"))?;
            if s.end_ms <= s.start_ms {
                continue;
            }
            let source = match sources.iter().position(|p| p == &s.path) {
                Some(i) => i,
                None => {
                    sources.push(s.path.clone());
                    sources.len() - 1
                }
            };
            // step 3 can move a line's in/out points (up to 2 s past it); only words inside the new span are shown
            let (start_ms, end_ms) = trim(&story["trims"][id], s.start_ms, s.end_ms);
            pieces.push(Piece {
                source,
                start_ms,
                end_ms,
                text: s.text.clone(),
                segment: id.to_owned(),
                timed: !s.words.is_empty(),
                words: s
                    .words
                    .iter()
                    .filter(|(_, from, to)| (from + to) / 2 >= start_ms && (from + to) / 2 < end_ms)
                    .cloned()
                    .collect(),
            });
        }
        // a trim that runs into the next kept line of the same video would play that stretch twice
        for i in 1..pieces.len() {
            let (prev, cur) = (&pieces[i - 1], &pieces[i]);
            if prev.source == cur.source && cur.start_ms >= prev.start_ms && cur.start_ms < prev.end_ms {
                let end = prev.end_ms;
                let cur = &mut pieces[i];
                cur.start_ms = end.min(cur.end_ms.saturating_sub(1));
                cur.words.retain(|(_, from, to)| (from + to) / 2 >= end);
            }
        }
        // a line the trim before it swallowed (nearly) whole is gone, not a 1 ms clip
        pieces.retain(|p| p.end_ms >= p.start_ms + 200);
        if pieces.is_empty() {
            return Err(format!("story_empty:{}", n + 1));
        }
        out.push(Story {
            id: story["id"]
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| format!("story-{}", n + 1)),
            title: story["title"].as_str().unwrap_or("").trim().to_owned(),
            sources,
            pieces,
        });
    }
    Ok(out)
}

/// The family a step 4/5 font choice draws with.
pub fn font(value: &str) -> &str {
    match value {
        "tahoma" => "Tahoma",
        // the UI's system stack falls back to Leelawadee UI for Thai, which also covers Latin
        "system" | "thai" => "Leelawadee UI",
        other => other.strip_prefix("family:").unwrap_or("Leelawadee UI"),
    }
}
/// `#rrggbb` to ASS `&HBBGGRR&`.
fn colour(value: &str) -> String {
    let hex = value.trim_start_matches('#');
    if hex.len() != 6 || !hex.bytes().all(|b| b.is_ascii_hexdigit()) {
        return "&HFFFFFF&".into();
    }
    format!("&H{}{}{}&", &hex[4..6], &hex[2..4], &hex[0..2]).to_uppercase()
}
fn clock(ms: u64) -> String {
    let cs = ms / 10;
    format!(
        "{}:{:02}:{:02}.{:02}",
        cs / 360_000,
        cs / 6000 % 60,
        cs / 100 % 60,
        cs % 100
    )
}
fn escape(text: &str) -> String {
    text.replace('\\', "\u{29F5}")
        .replace('{', "(")
        .replace('}', ")")
        .replace(['\r', '\n'], " ")
}
/// One positioned text event; size and position use the same 1080-wide frame as the step 4/5 preview.
fn event(frame: (u32, u32), layer: u8, start: u64, end: u64, style: &Value, text: &str) -> String {
    event_raw(frame, layer, start, end, style, &escape(text))
}
/// `text` is already escaped ASS (it may hold override tags such as karaoke).
fn event_raw(frame: (u32, u32), layer: u8, start: u64, end: u64, style: &Value, text: &str) -> String {
    let x = style["x"].as_f64().unwrap_or(50.0) / 100.0 * frame.0 as f64;
    let y = style["y"].as_f64().unwrap_or(50.0) / 100.0 * frame.1 as f64;
    let scale = frame.0 as f64 / 1080.0;
    let size = (style["size"].as_f64().unwrap_or(64.0) * scale).round() as u64;
    format!(
        "Dialogue: {layer},{},{},{},,0,0,0,,{{\\an5\\pos({x:.0},{y:.0})\\fn{}\\fs{}\\1c{}{}}}{}\n",
        clock(start),
        clock(end),
        if style["box_on"].as_bool() == Some(true) { "Box" } else { "Text" },
        font(style["font"].as_str().unwrap_or("system")),
        size,
        colour(style["color"].as_str().unwrap_or("#ffffff")),
        effects(style, scale, frame),
        text
    )
}

/// Outline, shadow, box and entrance as ASS override tags; sizes are reference px on a 1080-wide frame.
fn effects(style: &Value, scale: f64, frame: (u32, u32)) -> String {
    let px = |key: &str, default: f64| (style[key].as_f64().unwrap_or(default) * scale * 10.0).round() / 10.0;
    let mut tags = String::new();
    if style["box_on"].as_bool() == Some(true) {
        // in the Box style the outline is the box: its colour, and its width is the padding round the text
        tags += &format!("\\bord{}\\shad0\\3c{}", px("outline", 12.0).max(4.0), colour(style["box_color"].as_str().unwrap_or("#000000")));
    } else {
        tags += &format!("\\bord{}\\shad{}\\3c{}", px("outline", 4.0), px("shadow", 1.0), colour(style["outline_color"].as_str().unwrap_or("#000000")));
    }
    match style["enter"].as_str() {
        Some("fade") => tags += "\\fad(250,0)",
        Some("pop") => tags += "\\fscx70\\fscy70\\t(0,180,\\fscx100\\fscy100)",
        // a clip that opens left to right across the frame, so the text is revealed as it passes
        // "words" is drawn by karaoke tags in the text itself
        Some("wipe") => tags += &format!("\\clip(0,0,0,{h})\\t(0,400,\\clip(0,0,{w},{h}))", w = frame.0, h = frame.1),
        _ => {}
    }
    tags
}

fn thai(c: char) -> bool {
    ('\u{0E00}'..='\u{0E7F}').contains(&c)
}
/// Rough glyph count for line fitting: Thai vowel and tone marks take no width of their own.
fn units(text: &str) -> usize {
    text.chars()
        .filter(|&c| !matches!(c, '\u{0E31}' | '\u{0E34}'..='\u{0E3A}' | '\u{0E47}'..='\u{0E4E}'))
        .count()
}
fn join(line: &mut String, word: &str) {
    let gap = line.chars().last().is_some_and(|c| !thai(c))
        && word.chars().next().is_some_and(|c| !thai(c));
    if gap {
        line.push(' ');
    }
    line.push_str(word);
}
/// One piece's speech as single on-screen lines that fit the frame, timed from 0 at the piece start.
/// Thai has no spaces for libass to wrap at, so lines are broken here on dictionary words.
pub fn lines(piece: &Piece, limit: usize) -> Vec<(u64, u64, String)> {
    let length = piece.end_ms - piece.start_ms;
    let local = |ms: u64| ms.clamp(piece.start_ms, piece.end_ms) - piece.start_ms;
    let mut out: Vec<(u64, u64, String)> = Vec::new();
    if piece.words.is_empty() && piece.timed {
        return out;
    }
    if piece.words.is_empty() {
        let chars: Vec<char> = piece.text.chars().collect();
        let count = units(&piece.text).div_ceil(limit).max(1);
        let per = chars.len().div_ceil(count).max(1);
        for (n, part) in chars.chunks(per).enumerate() {
            let text = part.iter().collect::<String>().trim().to_owned();
            if !text.is_empty() {
                let (n, count) = (n as u64, count as u64);
                out.push((length * n / count, length * (n + 1) / count, text));
            }
        }
        return out;
    }
    let mut line = String::new();
    let mut start = 0;
    for (word, from, _) in &piece.words {
        let word = word.trim();
        if word.is_empty() {
            continue;
        }
        if !line.is_empty() && units(&line) + 1 + units(word) > limit {
            out.push((start, local(*from), std::mem::take(&mut line)));
        }
        if line.is_empty() {
            start = if out.is_empty() { 0 } else { local(*from) };
        }
        join(&mut line, word);
    }
    if !line.is_empty() {
        out.push((start, length, line));
    }
    out
}

/// What step 4/5 shows on screen, as ASS: headline items for the whole clip, speech lines in time.
/// Returns the subtitle file and the text roles that were asked for but have nothing to draw yet.
pub fn subtitles(story: &Story, document: &Value) -> (String, Vec<String>) {
    let (width, height) = frame(document);
    let mut ass = format!(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n\
[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n\
Style: Text,Leelawadee UI,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,1,5,60,60,0,1\n\
Style: Box,Leelawadee UI,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,12,0,5,60,60,0,1\n\n\
[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    );
    let total = story.duration_ms();
    let (speech, mut skipped) = speech_lines(story, document);
    let style = speech_style(document);
    if style["enter"].as_str().unwrap_or("words") == "words" && !speech.is_empty() {
        let size = style["size"].as_f64().unwrap_or(64.0);
        let limit = (0.86 * WIDTH as f64 / (size * 0.55)).max(4.0) as usize;
        let mut at = 0;
        for p in &story.pieces {
            for (start, end, text) in karaoke(p, limit) {
                ass.push_str(&event_raw((width, height), 0, at + start, at + end, &style, &text));
            }
            at += p.end_ms - p.start_ms;
        }
    } else {
        for (start, end, text) in &speech {
            ass.push_str(&event((width, height), 0, *start, *end, &style, text));
        }
    }
    let emphasis = emphasis_lines(story, document);
    if !emphasis.is_empty() {
        skipped.retain(|role| role != "emphasis");
        let style = match &document["text_styles"]["emphasis"] {
            s @ Value::Object(_) => s.clone(),
            _ => serde_json::json!({"font":"system","size":84,"color":"#f59e0b","x":50,"y":55}),
        };
        for (start, end, text) in &emphasis {
            ass.push_str(&event((width, height), 0, *start, *end, &style, text));
        }
    }
    for item in headlines(document) {
        ass.push_str(&event(
            (width, height),
            1,
            0,
            total,
            &item,
            item["text"].as_str().unwrap_or(""),
        ));
    }
    (ass, skipped)
}

/// One piece's speech as display lines whose words appear as they are said: `\ko` karaoke keeps each word
/// (and its outline) hidden until its start. Pieces without timed words fall back to whole lines.
pub fn karaoke(piece: &Piece, limit: usize) -> Vec<(u64, u64, String)> {
    let plain = lines(piece, limit);
    if piece.words.is_empty() {
        return plain.into_iter().map(|(a, b, t)| (a, b, escape(&t))).collect();
    }
    let local = |ms: u64| ms.clamp(piece.start_ms, piece.end_ms) - piece.start_ms;
    let words: Vec<(u64, &str)> = piece
        .words
        .iter()
        .map(|(w, from, _)| (local(*from), w.trim()))
        .filter(|(_, w)| !w.is_empty())
        .collect();
    let mut out = Vec::new();
    let mut next = 0;
    for (start, end, text) in plain {
        // the words of this display line, in order, as lines() grouped them
        let mut line = String::new();
        let mut cursor = start;
        let mut built = String::new();
        while next < words.len() && (built.is_empty() || {
            let mut probe = built.clone();
            join(&mut probe, words[next].1);
            text.starts_with(probe.as_str())
        }) {
            let (at, word) = words[next];
            let mut joined = built.clone();
            join(&mut joined, word);
            let spoken = &joined[built.len()..];
            if at > cursor {
                line += &format!("{{\\k{}}}", (at - cursor) / 10);
                cursor = at;
            }
            let until = words.get(next + 1).map(|w| w.0).unwrap_or(end).clamp(cursor, end.max(cursor));
            line += &format!("{{\\ko{}}}{}", (until - cursor) / 10, escape(spoken));
            cursor = until;
            built = joined;
            next += 1;
            if built == text {
                break;
            }
        }
        if line.is_empty() {
            line = escape(&text);
        }
        out.push((start, end, line));
    }
    out
}

/// Emphasis words picked in step 3 (by each word's start time on its transcript line), on screen in output
/// time from the first picked word to the end of its piece.
pub fn emphasis_lines(story: &Story, document: &Value) -> Vec<(u64, u64, String)> {
    let ticked = document["text_roles"]
        .as_array()
        .is_some_and(|r| r.iter().any(|v| v == "emphasis"));
    let mut out = Vec::new();
    if !ticked {
        return out;
    }
    let mut at = 0;
    for p in &story.pieces {
        let picked: Vec<u64> = document["emphasis"][&p.segment]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_u64)
            .collect();
        let words: Vec<&(String, u64, u64)> = p.words.iter().filter(|w| picked.contains(&w.1)).collect();
        if let Some(first) = words.first() {
            let mut text = String::new();
            for w in &words {
                if !text.is_empty() {
                    text.push(' ');
                }
                text.push_str(w.0.trim());
            }
            let start = first.1.clamp(p.start_ms, p.end_ms) - p.start_ms;
            out.push((at + start, at + p.end_ms - p.start_ms, text));
        }
        at += p.end_ms - p.start_ms;
    }
    out
}

/// Step 4's speech style; a draft that never opened its style panel uses that panel's defaults.
pub fn speech_style(document: &Value) -> Value {
    match &document["text_styles"]["speech"] {
        style @ Value::Object(_) => style.clone(),
        _ => serde_json::json!({"font":"system","size":64,"color":"#ffffff","x":50,"y":69}),
    }
}
/// Speech as one-line subtitles in output time (empty when speech is not ticked), and the
/// ticked text roles that have nothing to draw yet.
pub fn speech_lines(story: &Story, document: &Value) -> (Vec<(u64, u64, String)>, Vec<String>) {
    let roles: Vec<&str> = document["text_roles"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    let mut out = Vec::new();
    if roles.contains(&"speech") {
        let size = speech_style(document)["size"].as_f64().unwrap_or(64.0);
        let limit = (0.86 * WIDTH as f64 / (size * 0.55)).max(4.0) as usize;
        let mut at = 0;
        for p in &story.pieces {
            for (start, end, text) in lines(p, limit) {
                out.push((at + start, at + end, text));
            }
            at += p.end_ms - p.start_ms;
        }
    }
    let skipped = ["emphasis", "english"]
        .into_iter()
        .filter(|role| roles.contains(role))
        .map(str::to_owned)
        .collect();
    (out, skipped)
}
/// Step 5's headline items that have text; each stays on screen for the whole clip.
pub fn headlines(document: &Value) -> Vec<Value> {
    document["headline_items"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|item| item["text"].as_str().is_some_and(|t| !t.trim().is_empty()))
        .cloned()
        .collect()
}

/// ffmpeg arguments, run with the work folder as the current directory (holds `subs.ass`).
pub fn ffmpeg_args(story: &Story, document: &Value, output: &Path) -> Vec<String> {
    let (width, height) = frame(document);
    let shape = width as f64 / height as f64;
    let mut args: Vec<String> = [
        "-hide_banner",
        "-nostdin",
        "-y",
        "-progress",
        "pipe:1",
        "-nostats",
    ]
    .map(String::from)
    .into();
    for source in &story.sources {
        args.push("-i".into());
        args.push(source.display().to_string());
    }
    let mut graph = String::new();
    for (n, p) in story.pieces.iter().enumerate() {
        let (a, b) = (p.start_ms as f64 / 1000.0, p.end_ms as f64 / 1000.0);
        // the largest window of the clip's shape, slid to where step 3 put it, then scaled to the frame
        let (cx, cy) = crop(document, &story.sources[p.source]);
        graph.push_str(&format!(
            "[{s}:v]trim=start={a:.3}:end={b:.3},setpts=PTS-STARTPTS,\
crop=w='min(iw,ih*{shape:.6})':h='min(ih,iw/{shape:.6})':x='(iw-ow)*{cx:.4}':y='(ih-oh)*{cy:.4}',\
scale={width}:{height},setsar=1,fps=30,format=yuv420p[v{n}];\
[{s}:a]atrim=start={a:.3}:end={b:.3},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,afade=t=in:d=0.008,\
afade=t=out:st={fade:.3}:d=0.008[a{n}];",
            s = p.source,
            fade = ((b - a) - 0.008).max(0.0),
        ));
    }
    for n in 0..story.pieces.len() {
        graph.push_str(&format!("[v{n}][a{n}]"));
    }
    graph.push_str(&format!(
        "concat=n={}:v=1:a=1[cv][ca];[cv]ass=subs.ass[vout]",
        story.pieces.len()
    ));
    args.extend(
        [
            "-filter_complex",
            &graph,
            "-map",
            "[vout]",
            "-map",
            "[ca]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
        ]
        .map(String::from),
    );
    args.push(output.display().to_string());
    args
}

/// What the files for story `n` (0-based) are called: project title, then story title.
pub fn story_name(project_title: &str, story: &Story, n: usize) -> String {
    file_name(
        &[project_title.trim(), story.title.as_str()]
            .into_iter()
            .filter(|t| !t.is_empty())
            .collect::<Vec<_>>()
            .join(" - "),
        &format!("EditorKrab {}", n + 1),
    )
}
/// A file name Windows accepts, from the story or project title.
pub fn file_name(title: &str, fallback: &str) -> String {
    let clean: String = title
        .chars()
        .map(|c| {
            if r#"<>:"/\|?*"#.contains(c) || c.is_control() {
                ' '
            } else {
                c
            }
        })
        .collect();
    let clean = clean.split_whitespace().collect::<Vec<_>>().join(" ");
    let clean: String = clean.chars().take(80).collect();
    let clean = clean.trim_end_matches(['.', ' ']).to_owned();
    if clean.is_empty() {
        fallback.to_owned()
    } else {
        clean
    }
}
/// `folder/name.mp4`, or `name (2).mp4` and so on: an export never replaces an existing file.
pub fn unused_path(folder: &Path, name: &str) -> PathBuf {
    let first = folder.join(format!("{name}.mp4"));
    if !first.exists() {
        return first;
    }
    (2..)
        .map(|n| folder.join(format!("{name} ({n}).mp4")))
        .find(|p| !p.exists())
        .expect("unbounded")
}
