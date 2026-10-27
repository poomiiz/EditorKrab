//! Step 6: turns the saved draft (AI plan + subtitle and headline styles) into one 9:16 MP4 per story.
//! The plan only names transcript segments; times, text and source files come from finished jobs.
use crate::jobs::{Job, Task};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
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
    let c = source_id(document, path).map(|id| &document["crops"][id]).unwrap_or(&Value::Null);
    let at = |v: &Value| v.as_f64().filter(|v| v.is_finite()).unwrap_or(0.5).clamp(0.0, 1.0);
    (at(&c["x"]), at(&c["y"]))
}
/// The crop at `ms` of the video as (x, y, zoom, centred). Without the camera following the speaker it is the
/// video's own window: x and y place it across the slack, zoom 1. Following (`follow_speaker`), it is the window
/// of the person talking: one they dragged (`crops[id].speakers[n]`, x/y across the slack) or, when step 3 found
/// their face (`voice[id].faces[n]`), centred on that face (x/y are then the face centre, `centred` true); zoom
/// 1-3, FOLLOW_ZOOM when they set none, so a clip that fits the frame exactly can still move in on them.
pub fn crop_at(document: &Value, path: &Path, ms: u64) -> (f64, f64, f64, bool) {
    let (x, y) = crop(document, path);
    let base = (x, y, 1.0, false);
    if document["follow_speaker"] != true {
        return base;
    }
    let Some(id) = source_id(document, path) else { return base };
    let Some(who) = speaker_at(document, id, ms) else { return base };
    let c = &document["crops"][id]["speakers"][who.to_string()];
    // an entry of exactly x 0.5, y 0.5 was written by the first zoom picker (people were voices then): unset
    let c = if c["x"].as_f64() == Some(0.5) && c["y"].as_f64() == Some(0.5) && !c["zoom"].is_number() { &Value::Null } else { c };
    let at = |v: &Value, d: f64| v.as_f64().filter(|v| v.is_finite()).unwrap_or(d).clamp(0.0, 1.0);
    let zoom = c["zoom"].as_f64().filter(|v| v.is_finite()).unwrap_or(FOLLOW_ZOOM).clamp(1.0, 3.0);
    if c["x"].is_number() || c["y"].is_number() {
        return (at(&c["x"], x), at(&c["y"], y), zoom, false);
    }
    let face = &document["voice"][id]["faces"][who as usize];
    if seen(document, id) && face["cx"].is_number() {
        return (at(&face["cx"], 0.5), at(&face["cy"], 0.5), zoom, true);
    }
    (x, y, zoom, false)
}
/// Whether step 3 saw who talks (faces and mouth movement); the camera then follows people on screen, not voices.
fn seen(document: &Value, id: &str) -> bool {
    document["voice"][id]["seen"].is_array() && document["voice"][id]["faces"].as_array().is_some_and(|s| !s.is_empty())
}
/// The turns the camera follows: who is seen talking (`voice[id].seen`, by person on screen) when step 3 found
/// faces, else who is heard (`voice[id].turns`, by diarization).
fn follow_turns(document: &Value, id: &str) -> Vec<(u64, u64, i64)> {
    let visual=seen(document,id);
    let (list,who)=if visual {("seen","person")} else {("turns","speaker")};
    let mut input:Vec<_>=document["voice"][id][list].as_array().into_iter().flatten()
        .filter_map(|t|Some((t["start_ms"].as_u64()?,t["end_ms"].as_u64()?,t[who].as_i64()?)))
        .filter(|(a,b,_)|b>a).collect();
    input.sort_by_key(|t|t.0);
    let mut turns:Vec<(u64,u64,i64)>=vec![];
    for (a,b,person) in input {
        if let Some(last)=turns.last_mut().filter(|last|last.2==person&&a.saturating_sub(last.1)<=1200) {last.1=last.1.max(b);}
        else {turns.push((a,b,person));}
    }
    let speech=document["voice"][id]["speech"].as_array();
    let spans:Vec<(u64,u64)>=speech.into_iter().flatten().filter_map(|p|Some((p[0].as_u64()?,p[1].as_u64()?))).collect();
    turns.into_iter().filter(|(a,b,_)|b-a>=if visual {2500} else {FOLLOW_MIN_MS})
        .filter(|(a,b,_)|!visual||spans.is_empty()||spans.iter().any(|&(x,y)|b.min(&y).saturating_sub(*a.max(&x))>=600))
        .map(|(a,b,who)|(snap(a,spans.iter().map(|s|s.0)),snap(b,spans.iter().map(|s|s.1)),who))
        .filter(|(a,b,_)|b>a).collect()
}
/// How far a camera cut moves to the nearest pause edge, ms.
pub const CUT_SNAP_MS: u64 = 1000;
/// A camera cut lands where speech starts or stops (`edges`), not on the 1 s grid mouth motion is
/// measured in (24 Oct: cuts in the middle of words); with no edge within CUT_SNAP_MS it stays.
fn snap(at: u64, edges: impl Iterator<Item = u64>) -> u64 {
    edges.filter(|e| e.abs_diff(at) <= CUT_SNAP_MS).min_by_key(|e| e.abs_diff(at)).unwrap_or(at)
}
/// The draft's source id for the video at `path`.
pub fn source_id<'a>(document: &'a Value, path: &Path) -> Option<&'a str> {
    document["files"]
        .as_array()?
        .iter()
        .find(|f| f["path"].as_str().map(Path::new) == Some(path))
        .and_then(|f| f["source_id"].as_str())
}
/// Shortest shot a camera change may leave (24 Oct: 8 of 20 shots were under 1 s; Nina 16's median is 2.2 s).
pub const MIN_SHOT_MS: u64 = 1000;
/// A gap this short between two pieces of the same video is played, not cut: a 60-100 ms jump only shows
/// as a stutter (Nina keeps breaths of 0.3-0.5 s).
pub const JOIN_GAP_MS: u64 = 250;
/// How much closer the camera goes on a speaker who has no zoom of their own (20%, P'Ohm 23 Oct).
pub const FOLLOW_ZOOM: f64 = 1.2;
/// Shortest turn the camera cuts to; a shorter one (a "yes" from the other side) keeps the shot.
pub const FOLLOW_MIN_MS: u64 = 1000;
/// Who is talking at `ms` in a video, by the turns step 3 stored (`follow_turns`); between turns, whoever spoke
/// last, held until the next turn, and before the first turn whoever speaks first. P'Ohm 27 Oct: the camera
/// stays on the last known talker; dropping to the whole picture after 1.2 s left 31 of 46 cuts wide on C4926.
/// Turns under FOLLOW_MIN_MS do not count.
pub fn speaker_at(document: &Value, id: &str, ms: u64) -> Option<i32> {
    let turns: Vec<(u64, u64, i64)> = follow_turns(document, id)
        .into_iter()
        .filter(|(a, b, _)| b >= &(a + FOLLOW_MIN_MS))
        .collect();
    let inside = turns.iter().filter(|(a, b, _)| *a <= ms && ms < *b).min_by_key(|(a, _, _)| *a);
    let before = turns.iter().filter(|(_, b, _)| *b <= ms).max_by_key(|(_, b, _)| *b);
    let first = turns.iter().filter(|(a, _, _)| *a > ms).min_by_key(|(a, _, _)| *a);
    inside.or(before).or(first).map(|t| t.2 as i32)
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

/// The whole-project subtitle shift in ms (+ later, - earlier), kept within ±5 s.
pub fn subtitle_offset(document: &Value) -> i64 {
    document["subtitle_offset_ms"].as_i64().unwrap_or(0).clamp(-5000, 5000)
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
        let Task::Transcribe { path, context, .. } = &job.task else {
            continue;
        };
        // the speech VAD heard in this video (step 4 "ตัดช่วงเงียบ"), to fit word clocks to
        let spans = |key: &str| -> Vec<(u64, u64)> {
            document["voice"][context.source.id.as_str()][key]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|p| Some((p[0].as_u64()?, p[1].as_u64()?)))
                .collect()
        };
        let (speech, hush) = (spans("speech"), spans("hush"));
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
            let words: Vec<(String, u64, u64)> = s["words"]
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
                    .collect();
            // re-timed word clocks ("จัดเวลาคำใหม่", one [start, end] per word in order), then the
            // whole-project subtitle shift; both live in the draft, the transcript stays as it was
            let mut words = words;
            if let Some(times) = document["word_times"][id].as_array().filter(|t| t.len() == words.len()) {
                for (w, t) in words.iter_mut().zip(times) {
                    if let (Some(a), Some(b)) = (t[0].as_u64(), t[1].as_u64()) {
                        if b >= a {
                            (w.1, w.2) = (a, b);
                        }
                    }
                }
            }
            let mut clocks: Vec<(u64, u64)> = words.iter().map(|w| (w.1, w.2)).collect();
            crate::voice::fit_words_hushed(&mut clocks, &speech, &hush);
            for (w, c) in words.iter_mut().zip(clocks) {
                (w.1, w.2) = c;
            }
            let offset = subtitle_offset(document);
            for w in &mut words {
                (w.1, w.2) = ((w.1 as i64 + offset).max(0) as u64, (w.2 as i64 + offset).max(0) as u64);
            }
            // a corrected line keeps the clocks of the words it was heard as, one per phrase it is written in
            if let Some((_, fixed)) = edited {
                words = realign(&words, fixed);
            }
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

/// Every line's words as the export times them (re-timed, fitted to the speech heard, shifted), by line id.
pub fn line_words(document: &Value, jobs: &[Job]) -> HashMap<String, Vec<(String, u64, u64)>> {
    spoken(document, jobs).into_iter().map(|(id, s)| (id, s.words)).collect()
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

/// Dead air of the video at `path`, as step 3 stored it (`quiet` by source id: speech and speakers, voice.rs).
fn quiet_of(document: &Value, path: &std::path::Path) -> Vec<(u64, u64)> {
    let id = document["files"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|f| f["path"].as_str().is_some_and(|p| std::path::Path::new(p) == path))
        .and_then(|f| f["source_id"].as_str());
    // cutting is off when the shortest pause is set to 0; a cut the person clicked back stays in (air_keep)
    if document["dead_min_ms"].as_u64() == Some(0) {
        return Vec::new();
    }
    let Some(id) = id else { return Vec::new() };
    let kept: Vec<u64> = document["air_keep"][id].as_array().into_iter().flatten().filter_map(Value::as_u64).collect();
    document["quiet"][id]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|s| Some((s[0].as_u64()?, s[1].as_u64()?)))
        .filter(|(start, _)| !kept.contains(start))
        .collect()
}
/// Dead air: a quiet stretch a line starts or ends in is cut, leaving PAD_MS of it; an edge the person dragged
/// in step 3 stays where they put it. Matches `unquiet` in stories.js.
/// Dead air kept before speech starts again, ms: VAD hears a word late (24 Oct review: a cut at 46.66 s
/// took the first 50 ms of a word whose sound starts at 46.61 s), so more is kept on that side than after speech.
pub const LEAD_PAD_MS: u64 = 250;
pub fn unquiet(quiet: &[(u64, u64)], trim: &Value, start: u64, end: u64) -> (u64, u64) {
    const PAD_MS: u64 = crate::pauses::WORD_PAD_MS;
    const SHORTEST: u64 = 200;
    let (mut a, mut b) = (start, end);
    if !trim["start_ms"].is_number() {
        for &(s, e) in quiet {
            if s <= a + PAD_MS && e > a {
                a = a.max(e.saturating_sub(LEAD_PAD_MS));
            }
        }
    }
    if !trim["end_ms"].is_number() {
        for &(s, e) in quiet.iter().rev() {
            if s > a && s < b && e + PAD_MS >= b {
                b = b.min(s + PAD_MS);
            }
        }
    }
    if b >= a + SHORTEST { (a, b) } else { (start, end) }
}

/// A stretch of video split where the speaker (by `speaker_at`) changes, for the camera to follow.
pub fn speaker_cuts(document: &Value, id: &str, start: u64, end: u64) -> Vec<(u64, u64)> {
    let mut edges: Vec<u64> = follow_turns(document, id)
        .into_iter()
        .flat_map(|(a, b, _)| [a, b, b.saturating_add(1201)])
        .filter(|&t| t >= start + MIN_SHOT_MS && t + MIN_SHOT_MS <= end)
        .collect();
    edges.sort();
    edges.dedup();
    let mut parts = Vec::new();
    let mut at = start;
    for t in edges {
        if t >= at + MIN_SHOT_MS && speaker_at(document, id, t) != speaker_at(document, id, at) {
            parts.push((at, t));
            at = t;
        }
    }
    parts.push((at, end));
    parts
}

/// Words said while finding the next one; at a line's start they are cut with any word said twice in a row.
/// Matches FILLERS in stories.js.
pub const FILLERS: [&str; 12] = ["เออ", "เอ่อ", "เอิ่ม", "อืม", "อือ", "อ่า", "อ้า", "อะ", "อ่ะ", "คือ", "แบบ", "แบบว่า"];
/// Sound kept before the first word left after fillers are cut, ms.
pub const FILLER_PAD_MS: u64 = 100;
/// Where a line's first real word starts when it opens with fillers or a stammer ("เออ คือ คือ มีโมเม้นต์"
/// -> "มีโมเม้นต์"); None when it opens with a real word, or when it is nothing but fillers (a listener's
/// "อืม" is kept: Nina keeps the listener).
pub fn past_fillers(words: &[(String, u64, u64)], start: u64, end: u64) -> Option<u64> {
    let said: Vec<&(String, u64, u64)> = words.iter().filter(|w| (w.1 + w.2) / 2 >= start && w.1 < end && !w.0.trim().is_empty()).collect();
    let mut i = 0;
    while i < said.len() {
        let word = said[i].0.trim();
        let repeated = said.get(i + 1).is_some_and(|next| next.0.trim() == word);
        if !FILLERS.contains(&word) && !repeated {
            break;
        }
        i += 1;
    }
    // the cut lands before the real word but never inside the filler said just before it
    (i > 0 && i < said.len()).then(|| said[i].1.saturating_sub(FILLER_PAD_MS).max(said[i - 1].2.min(said[i].1)) + FILLER_PAD_MS)
}
/// The orange cards as drawn: each stays until the white card it sits under goes, so both go together (Nina
/// 16: 26 of 33 orange cards end with their white card; ours ended 0.13 s after their last word, 4 of 12).
pub fn emphasis_cues(story: &Story, document: &Value) -> Vec<(u64, u64, String)> {
    let mut emphasis = emphasis_lines(story, document);
    let white = speech_cues(story, document);
    for i in 0..emphasis.len() {
        let next = emphasis.get(i + 1).map_or(u64::MAX, |e| e.0);
        if let Some(w) = white.iter().find(|w| w.0 <= emphasis[i].0 && emphasis[i].0 < w.1) {
            emphasis[i].1 = emphasis[i].1.max(w.1).min(next);
        }
    }
    emphasis
}
/// How far back a line's start moves to where its sound begins, ms.
pub const ONSET_REACH_MS: u64 = 300;
/// A line that starts while its sound is already on (inside a stored loud stretch) starts where that sound
/// began, up to ONSET_REACH_MS earlier (24 Oct review: "ตอบ" at 26.9 s lost its first sound). Matches
/// `onset` in stories.js.
pub fn onset(document: &Value, path: &Path, start: u64) -> u64 {
    let Some(id) = source_id(document, path) else { return start };
    document["voice"][id]["loud"].as_array().into_iter().flatten()
        .filter_map(|p| Some((p[0].as_u64()?, p[1].as_u64()?)))
        .find(|&(a, b)| a < start && start < b)
        .map_or(start, |(a, _)| a.max(start.saturating_sub(ONSET_REACH_MS)))
}
/// How far a camera change moves to land between two words, ms.
pub const GAP_REACH_MS: u64 = 1000;
/// A camera change inside a line moves to the widest pause between two words near it, so the picture never
/// changes in the middle of a word or phrase (24 Oct review: "สมัย สัก | ปี ที่แล้ว" switched mid-phrase,
/// loud on both sides). Each part keeps at least MIN_SHOT_MS; with no pause near, the change stays.
pub fn at_word_gaps(parts: &mut [(u64, u64)], words: &[(String, u64, u64)]) {
    for i in 1..parts.len() {
        // only a camera change (the parts touch); a dead-air cut between two parts stays cut
        if parts[i - 1].1 != parts[i].0 {
            continue;
        }
        let (lo, hi, t) = (parts[i - 1].0 + MIN_SHOT_MS, parts[i].1.saturating_sub(MIN_SHOT_MS), parts[i].0);
        let best = words
            .windows(2)
            .filter(|w| w[1].1 > w[0].2)
            .map(|w| (w[1].1 - w[0].2, (w[0].2 + w[1].1) / 2))
            .filter(|&(_, mid)| mid.abs_diff(t) <= GAP_REACH_MS && mid >= lo && mid <= hi)
            .max_by_key(|&(size, mid)| (size, std::cmp::Reverse(mid.abs_diff(t))));
        if let Some((_, mid)) = best {
            parts[i - 1].1 = mid;
            parts[i].0 = mid;
        }
    }
}
/// The parts of a line left once the dead air inside it is cut: each quiet stretch well inside the line goes,
/// leaving PAD_MS of it on both sides, when at least 0.2 s goes. Matches `inner` in stories.js.
pub fn inner_cuts(quiet: &[(u64, u64)], start: u64, end: u64) -> Vec<(u64, u64)> {
    const PAD_MS: u64 = crate::pauses::WORD_PAD_MS;
    let mut parts = Vec::new();
    let mut at = start;
    for &(s, e) in quiet {
        let (a, b) = (s + PAD_MS, e.saturating_sub(LEAD_PAD_MS));
        if a > at && b < end && b >= a + 200 {
            parts.push((at, a));
            at = b;
        }
    }
    parts.push((at, end));
    parts.retain(|(a, b)| b > a);
    parts
}

/// Match the browser's quietTimingKey; a changed clock must be analysed before export.
fn quiet_key(document: &Value, source: &str, spoken: &HashMap<String,Spoken>) -> String {
    let min=document["dead_min_ms"].as_u64().unwrap_or(1000);let min=if min==0 {0} else {min.max(300)};
    let mut keep:Vec<i64>=if let Some(values)=document["speakers_keep"][source].as_array() {values.iter().filter_map(Value::as_i64).collect()}
        else {document["voice"][source]["turns"].as_array().into_iter().flatten().filter_map(|v|v["speaker"].as_i64()).collect()};
    keep.sort_unstable();keep.dedup();
    let path=document["files"].as_array().into_iter().flatten().find(|v|v["source_id"]==source).and_then(|v|v["path"].as_str());
    let mut segments:Vec<_>=spoken.iter().filter(|(_,v)|path.is_some_and(|path|v.path==Path::new(path))).collect();
    segments.sort_by(|a,b|(a.1.start_ms,a.0).cmp(&(b.1.start_ms,b.0)));
    let words:Vec<_>=segments.iter().flat_map(|(_,v)|v.words.iter().map(|(_,a,b)|[*a,*b])).collect();
    serde_json::json!([min,7,keep,words]).to_string()
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
    if document["dead_min_ms"].as_u64()!=Some(0) {
        for file in document["files"].as_array().into_iter().flatten() {
            if let Some(source)=file["source_id"].as_str() {
                if let Some(saved)=document["quiet_key"][source].as_str() {
                    if saved!=quiet_key(document,source,&spoken) {return Err("pause_analysis_stale".into());}
                }
            }
        }
    }
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
            let quiet = quiet_of(document, &s.path);
            let (start_ms, end_ms) = unquiet(&quiet, &story["trims"][id], start_ms, end_ms);
            // filler and stammer at the line's start go (P'Ohm 24 Oct: "เออ คือ คือ มีโมเม้นต์" -> "มีโมเม้นต์"),
            // then the start goes back to where the first kept word's sound begins
            let cut = match story["trims"][id]["start_ms"].is_number() {
                true => None,
                false => past_fillers(&s.words, start_ms, end_ms).map(|at| at.saturating_sub(FILLER_PAD_MS).max(start_ms)),
            };
            // (never back into the fillers just cut: "คือ" runs straight into "มี")
            let start_ms = match (cut, story["trims"][id]["start_ms"].is_number()) {
                (_, true) => start_ms,
                // back to where the first kept word's sound begins, never into the last filler said
                (Some(cut), _) => {
                    let said = s.words.iter().filter(|w| w.1 < cut).map(|w| w.2.min(cut)).max().unwrap_or(0);
                    onset(document, &s.path, cut).max(said)
                }
                (None, _) => onset(document, &s.path, start_ms),
            };
            // a word counts as said in the span when it starts or centres there: the engine stretches a word
            // over the pause after it ("การ" 32.84-37.04 s), which the dead-air cut then removes
            let inside = |from: u64, to: u64, a: u64, b: u64| ((from + to) / 2 >= a && (from + to) / 2 < b) || (from >= a && from < b);
            // dead air inside the line is cut too: the line plays as several pieces with the pause taken out
            let mut parts = inner_cuts(&quiet, start_ms, end_ms);
            // the camera follows the speaker: a part is cut again where the person talking changes
            if document["follow_speaker"] == true {
                if let Some(sid) = source_id(document, &s.path) {
                    parts = parts.into_iter().flat_map(|(a, b)| speaker_cuts(document, sid, a, b)).collect();
                    at_word_gaps(&mut parts, &s.words);
                }
            }
            let split = parts.len() > 1 && !s.words.is_empty();
            for (a, b) in parts {
                let words: Vec<(String, u64, u64)> = s.words.iter().filter(|(_, from, to)| inside(*from, *to, a, b)).cloned().collect();
                let mut text = String::new();
                for (w, _, _) in &words {
                    join(&mut text, w.trim());
                }
                pieces.push(Piece {
                    source,
                    start_ms: a,
                    end_ms: b,
                    // a split line's parts carry their own words; a whole line keeps its (possibly edited) text
                    text: if split { text } else { s.text.clone() },
                    segment: id.to_owned(),
                    timed: !s.words.is_empty(),
                    words,
                });
            }
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
        // a short gap to the next piece of the same video is played instead of jumped
        for i in 1..pieces.len() {
            let (end, next) = (pieces[i - 1].end_ms, pieces[i].start_ms);
            if pieces[i - 1].source == pieces[i].source && next > end && next - end <= JOIN_GAP_MS {
                pieces[i - 1].end_ms = next;
            }
        }
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
    // letter case from step 4 (TT / tt / Tt); Thai has no case and passes through
    let text = match style["case"].as_str() {
        Some("upper") => text.to_uppercase(),
        Some("lower") => text.to_lowercase(),
        Some("title") => text
            .split(' ')
            .map(|w| {
                let mut c = w.chars();
                c.next().map_or(String::new(), |f| f.to_uppercase().collect::<String>() + &c.as_str().to_lowercase())
            })
            .collect::<Vec<_>>()
            .join(" "),
        _ => text.to_owned(),
    };
    let style = crate::text_layout::fitted_in(style, &text, 1080.0 * frame.1 as f64 / frame.0 as f64);
    event_raw(frame, layer, start, end, &style, &escape(&text))
}
/// `text` is already escaped ASS (it may hold override tags such as karaoke).
fn event_raw(frame: (u32, u32), layer: u8, start: u64, end: u64, style: &Value, text: &str) -> String {
    let x = style["x"].as_f64().unwrap_or(50.0) / 100.0 * frame.0 as f64;
    let y = style["y"].as_f64().unwrap_or(50.0) / 100.0 * frame.1 as f64;
    let scale = frame.0 as f64 / 1080.0;
    let size = (style["size"].as_f64().unwrap_or(64.0) * scale).round() as u64;
    // left / right alignment anchors that side of the text at the position, as the preview does
    let an = match style["align"].as_str() {
        Some("left") => 4,
        Some("right") => 6,
        _ => 5,
    };
    let reveal = if style["enter"] == "wipe" {
        let w = crate::text_layout::width(text, style) * scale;
        let left = match style["align"].as_str() { Some("left") => x, Some("right") => x - w, _ => x - w / 2.0 };
        format!("\\clip({:.0},0,{:.0},{})\\t(0,300,\\clip({:.0},0,{:.0},{}))", left - 8.0, left - 8.0, frame.1, left - 8.0, left + w + 8.0, frame.1)
    } else { String::new() };
    let line = |layer: u8, extra: &str| {
        format!(
            "Dialogue: {layer},{},{},{},,0,0,0,,{{\\an{an}\\pos({x:.0},{y:.0})\\fn{}\\fs{}\\1c{}{}{extra}}}{}\n",
            clock(start),
            clock(end),
            if style["box_on"].as_bool() == Some(true) { "Box" } else { "Text" },
            font(style["font"].as_str().unwrap_or("system")),
            size,
            colour(style["color"].as_str().unwrap_or("#ffffff")),
            format!("{}{}", effects(style, scale, frame), reveal),
            text
        )
    };
    let blur = (style["shadow_blur"].as_f64().unwrap_or(0.0) * scale * 10.0).round() / 10.0;
    let shadowed = style["box_on"].as_bool() != Some(true) && style["shadow"].as_f64().unwrap_or(0.0) > 0.0;
    // glow: a wide, soft outline of the glow colour under the text (no fill, no shadow)
    let mut out = String::new();
    if style["glow_on"].as_bool() == Some(true) && style["box_on"].as_bool() != Some(true) {
        let range = (style["glow_range"].as_f64().unwrap_or(20.0) * scale * 10.0).round() / 10.0;
        let strength = style["glow_strength"].as_f64().unwrap_or(60.0).clamp(0.0, 100.0);
        out += &line(
            layer * 2,
            &format!(
                "\\1a&HFF&\\shad0\\bord{range}\\3c{}\\3a&H{:02X}&\\blur{}",
                colour(style["glow_color"].as_str().unwrap_or("#ffffff")),
                (255.0 * (1.0 - strength / 100.0)).round() as u8,
                (range / 2.0).max(1.0)
            ),
        );
    }
    if shadowed && blur > 0.0 {
        out + &line(layer * 2, &format!("\\1a&HFF&\\3a&HFF&\\blur{blur}")) + &line(layer * 2 + 1, "\\shad0")
    } else {
        out + &line(layer * 2 + 1, "")
    }
}

/// Outline, shadow, box and entrance as ASS override tags; sizes are reference px on a 1080-wide frame.
fn effects(style: &Value, scale: f64, _frame: (u32, u32)) -> String {
    let px = |key: &str, default: f64| (style[key].as_f64().unwrap_or(default) * scale * 10.0).round() / 10.0;
    let mut tags = String::new();
    if style["box_on"].as_bool() == Some(true) {
        // in the Box style the outline is the box: its colour, and its width is the padding round the text
        tags += &format!("\\bord{}\\shad0\\3c{}", px("outline", 12.0).max(4.0), colour(style["box_color"].as_str().unwrap_or("#000000")));
        // its own room across and up/down, when set (libass draws \xbord/\ybord as the box padding)
        if style["box_pad_x"].is_u64() || style["box_pad_y"].is_u64() {
            let pad = |key: &str| style[key].as_f64().map_or(px("outline", 12.0).max(4.0), |v| (v * scale * 10.0).round() / 10.0);
            tags += &format!("\\xbord{}\\ybord{}", pad("box_pad_x"), pad("box_pad_y"));
        }
        if let Some(opacity) = style["box_opacity"].as_f64() {
            tags += &format!("\\3a&H{:02X}&", (255.0 * (1.0 - opacity.clamp(0.0, 100.0) / 100.0)).round() as u8);
        }
    } else {
        tags += &format!(
            "\\bord{}\\shad{}\\3c{}\\4c{}",
            px("outline", 0.0),
            px("shadow", 0.0),
            colour(style["outline_color"].as_str().unwrap_or("#000000")),
            colour(style["shadow_color"].as_str().unwrap_or("#000000"))
        );
        // shadow opacity in percent; ASS alpha counts the other way (00 solid, FF clear)
        if let Some(opacity) = style["shadow_opacity"].as_f64() {
            tags += &format!("\\4a&H{:02X}&", (255.0 * (1.0 - opacity.clamp(0.0, 100.0) / 100.0)).round() as u8);
        }
        // a shadow cast at an angle: the distance is the old diagonal offset, so 45° matches \shad
        if let Some(angle) = style["shadow_angle"].as_f64() {
            let d = px("shadow", 0.0) * std::f64::consts::SQRT_2;
            let a = angle.to_radians();
            tags += &format!("\\xshad{:.1}\\yshad{:.1}", d * a.cos(), d * a.sin());
        }
    }
    // weight, slant, underline and letter spacing
    if style["bold"].as_bool() == Some(false) {
        tags += "\\b0";
    }
    if style["italic"].as_bool() == Some(true) {
        tags += "\\i1";
    }
    if style["underline"].as_bool() == Some(true) {
        tags += "\\u1";
    }
    if let Some(spacing) = style["spacing"].as_f64() {
        tags += &format!("\\fsp{}", (spacing * scale * 10.0).round() / 10.0);
    }
    match style["enter"].as_str() {
        Some("fade") => tags += "\\fad(250,0)",
        Some("pop") => tags += "\\fad(120,0)\\fscx85\\fscy85\\t(0,300,\\fscx100\\fscy100)",
        // a clip that opens left to right across the frame, so the text is revealed as it passes
        // "words" is drawn as one event per word (see karaoke)
        Some("wipe") => {},
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
    let gap = line.chars().last().is_some_and(|c| !thai(c) && !c.is_whitespace())
        && word.chars().next().is_some_and(|c| !thai(c));
    if gap {
        line.push(' ');
    }
    line.push_str(word);
}
/// A corrected line on the clocks of the words it was heard as. The fixed text's spaces mark where a card may
/// end (its phrases); a space between two Latin words or numbers ("Royalty Fee 3%") stays inside one phrase.
/// Every heard letter is said at its share of its word's time; the fixed letters are matched to them in order
/// (longest common subsequence) and take their clocks, and a run of changed letters ("Royalty Fee" for
/// "โลยีตี้ฟรี") is spread over the time between its matched neighbours. Each phrase is one word from its
/// first letter to its last; all but the last keep a trailing space so a card of two phrases reads with it.
/// Before 27 Oct a corrected line lost its clocks and showed as one card of the whole line.
pub fn realign(heard: &[(String, u64, u64)], fixed: &str) -> Vec<(String, u64, u64)> {
    let mut old: Vec<(char, u64, u64)> = Vec::new();
    for (text, from, to) in heard {
        let letters: Vec<char> = text.chars().filter(|c| !c.is_whitespace()).collect();
        let (count, span) = (letters.len().max(1) as u64, to.saturating_sub(*from));
        old.extend(letters.into_iter().enumerate().map(|(k, c)| (c, from + span * k as u64 / count, from + span * (k as u64 + 1) / count)));
    }
    let latin = |c: Option<char>| c.is_some_and(|c| c.is_ascii_alphanumeric() || c == '%');
    let mut phrases: Vec<String> = Vec::new();
    for t in fixed.split_whitespace() {
        match phrases.last_mut() {
            Some(last) if latin(last.chars().last()) && latin(t.chars().next()) => { last.push(' '); last.push_str(t); }
            _ => phrases.push(t.to_owned()),
        }
    }
    let new: Vec<(char, usize)> = phrases.iter().enumerate().flat_map(|(n, p)| p.chars().filter(|c| !c.is_whitespace()).map(move |c| (c, n))).collect();
    let (n, m) = (old.len(), new.len());
    if n == 0 || m == 0 || n * m > 4_000_000 {
        return Vec::new();
    }
    let mut dp = vec![0u16; (n + 1) * (m + 1)];
    for i in (0..n).rev() {
        for j in (0..m).rev() {
            dp[i * (m + 1) + j] = if old[i].0 == new[j].0 { dp[(i + 1) * (m + 1) + j + 1] + 1 } else { dp[(i + 1) * (m + 1) + j].max(dp[i * (m + 1) + j + 1]) };
        }
    }
    let mut clock: Vec<Option<(u64, u64)>> = vec![None; m];
    let (mut i, mut j) = (0, 0);
    while i < n && j < m {
        if old[i].0 == new[j].0 { clock[j] = Some((old[i].1, old[i].2)); i += 1; j += 1; }
        else if dp[(i + 1) * (m + 1) + j] >= dp[i * (m + 1) + j + 1] { i += 1; } else { j += 1; }
    }
    if clock.iter().all(Option::is_none) {
        return Vec::new();
    }
    let mut j = 0;
    while j < m {
        if clock[j].is_some() { j += 1; continue; }
        let end = (j..m).find(|&k| clock[k].is_some()).unwrap_or(m);
        let left = j.checked_sub(1).and_then(|k| clock[k]).map_or(old[0].1, |c| c.1);
        let right = clock.get(end).copied().flatten().map_or(old[n - 1].2, |c| c.0).max(left);
        let run = (end - j) as u64;
        for k in 0..run {
            clock[j + k as usize] = Some((left + (right - left) * k / run, left + (right - left) * (k + 1) / run));
        }
        j = end;
    }
    let count = phrases.len();
    phrases.into_iter().enumerate().filter_map(|(k, p)| {
        let at: Vec<(u64, u64)> = new.iter().zip(&clock).filter(|((_, ph), _)| *ph == k).filter_map(|(_, c)| *c).collect();
        let (a, b) = (at.first()?.0, at.last()?.1);
        Some((if k + 1 < count { p + " " } else { p }, a, b.max(a)))
    }).collect()
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


/// Word-clocked one-line phrases. Unaligned corrections retain the original cue's clock.
/// A pause this long starts a new card; a breath shorter than it stays inside one (Nina 16 cards: median
/// 17 letters, 1.4 s; 400 ms made 13 letters, 1.1 s). Matches captionRows in text-style.js.
pub const PHRASE_PAUSE_MS: u64 = 650;
/// A card stays on until the next one starts when the gap is no longer than this (Nina's cards follow on).
pub const CARD_HOLD_MS: u64 = 1000;
/// Longest stretch of speech one card holds (Nina 16 timeline transcribed 24 Oct: white cards median 1.9 s,
/// p75 3.0 s, 16 letters; 2.2 s made 1.3 s / 14 letters). Matches captionRows' maxMs in text-style.js.
pub const CARD_MAX_MS: u64 = 3000;
pub fn layout_lines(piece: &Piece, style: &Value) -> Vec<(u64, u64, String)> {
    let length = piece.end_ms - piece.start_ms;
    if piece.words.is_empty() {
        return if piece.timed || piece.text.trim().is_empty() { vec![] } else { vec![(0, length, piece.text.clone())] };
    }
    let local = |v: u64| v.clamp(piece.start_ms, piece.end_ms) - piece.start_ms;
    // the words once each: the engine can give one word twice at the same clock
    let mut words: Vec<(&str, u64, u64)> = Vec::new();
    for (word, from, to) in &piece.words {
        // a corrected line's phrase keeps its trailing space (realign) so two phrases on one card stay apart
        let word = word.trim_start();
        if word.trim().is_empty() { continue; }
        match words.last_mut() {
            Some(last) if last.0 == word && last.1 == *from => last.2 = last.2.max(*to),
            _ => words.push((word, *from, *to)),
        }
    }
    let joined = |g: &[(&str, u64, u64)]| { let mut t = String::new(); for w in g { join(&mut t, w.0); } t.trim_end().to_owned() };
    // a style may hold longer sentences per card (card_ms: the ckfastwork reference reads whole clauses, 27 Oct)
    let most = style["card_ms"].as_u64().filter(|n| (1000..=8000).contains(n)).unwrap_or(CARD_MAX_MS);
    let groups = card_groups_within(&words, most, |g| crate::text_layout::fits(&joined(g), style));
    let mut rows = Vec::new();
    for (i, g) in groups.iter().enumerate() {
        let (start, end) = (g[0].1, g[g.len() - 1].2);
        let next = groups.get(i + 1).map_or(u64::MAX, |n| n[0].1);
        rows.push((local(start), local(end.saturating_add(180).min(next)).max(local(start) + 1).min(length), joined(g)));
    }
    rows.retain(|(a,b,_)| b > a);
    rows
}
/// A card whose words are said in less than this joins the next card, ms. Matches captionRows in text-style.js.
pub const FLASH_MS: u64 = 120;
/// A pause this long between two words is a place a card may end inside a phrase that runs too long, ms.
pub const PHRASE_GAP_MS: u64 = 120;
/// Words grouped into cards the way Nina 16 reads (its timeline transcribed 24 Oct): a card ends at a pause of
/// PHRASE_PAUSE_MS, or when it would hold more than CARD_MAX_MS of speech or not fit (`fits`); then it ends at
/// the widest pause inside it rather than before the word that did not fit ("ตอบไม่ | ได้" became "ตอบไม่ได้"),
/// and a last word or two of five letters or fewer joins the card before ("แก", "ได้" stood alone).
/// Matches captionRows in text-style.js.
pub fn card_groups<'a>(words: &[(&'a str, u64, u64)], fits: impl Fn(&[(&'a str, u64, u64)]) -> bool) -> Vec<Vec<(&'a str, u64, u64)>> {
    card_groups_within(words, CARD_MAX_MS, fits)
}
/// card_groups with a card holding at most `most` ms of speech.
pub fn card_groups_within<'a>(words: &[(&'a str, u64, u64)], most: u64, fits: impl Fn(&[(&'a str, u64, u64)]) -> bool) -> Vec<Vec<(&'a str, u64, u64)>> {
    let mut groups: Vec<Vec<(&str, u64, u64)>> = Vec::new();
    let mut cur: Vec<(&str, u64, u64)> = Vec::new();
    for &w in words {
        if let Some(last) = cur.last() {
            let pause = w.1.saturating_sub(last.2) >= PHRASE_PAUSE_MS;
            let mut with = cur.clone();
            with.push(w);
            let over = w.2.saturating_sub(cur[0].1) > most || !fits(&with);
            if pause {
                groups.push(std::mem::take(&mut cur));
            } else if over {
                // the widest pause inside the card, leaving at least one word on each side
                let split = (1..cur.len()).map(|k| (cur[k].1.saturating_sub(cur[k - 1].2), k)).filter(|&(gap, _)| gap >= PHRASE_GAP_MS).max();
                match split {
                    Some((_, k)) if { let mut rest = cur[k..].to_vec(); rest.push(w); fits(&rest) && w.2.saturating_sub(rest[0].1) <= most } => {
                        let rest = cur.split_off(k);
                        groups.push(std::mem::replace(&mut cur, rest));
                    }
                    _ => groups.push(std::mem::take(&mut cur)),
                }
            }
        }
        cur.push(w);
    }
    if !cur.is_empty() {
        groups.push(cur);
    }
    // a card said in no time (its words fall in cut dead air, so their clocks close up: "บาท" on C4926 s38,
    // 27 Oct) would flash for a frame and sit on a track of its own in CapCut: it joins the card after it
    let mut k = 0;
    while k + 1 < groups.len() {
        if groups[k][groups[k].len() - 1].2.saturating_sub(groups[k][0].1) < FLASH_MS {
            let mut both = groups.remove(k);
            both.extend(groups[k].iter().copied());
            groups[k] = both;
        } else {
            k += 1;
        }
    }
    // a short tail joins the card before when they fit together
    if groups.len() > 1 {
        let tail = &groups[groups.len() - 1];
        let letters: usize = tail.iter().map(|w| w.0.chars().count()).sum();
        let prev = &groups[groups.len() - 2];
        let mut both = prev.clone();
        both.extend(tail.iter().copied());
        if letters <= 5 && tail[0].1.saturating_sub(prev[prev.len() - 1].2) < PHRASE_PAUSE_MS && fits(&both) {
            groups.pop();
            *groups.last_mut().unwrap() = both;
        }
    }
    groups
}

fn speech_person(story: &Story, document: &Value, p: &Piece) -> Option<i64> {
    let id = source_id(document, &story.sources[p.source])?;
    document["voice"][id]["turns"].as_array()?.iter()
        .filter_map(|t| {
            let overlap = t["end_ms"].as_u64()?.min(p.end_ms).saturating_sub(t["start_ms"].as_u64()?.max(p.start_ms));
            (overlap > 0).then_some((overlap, t["speaker"].as_i64()?))
        }).max_by_key(|(overlap,_)| *overlap).map(|(_, who)| who)
}

fn speech_pieces(story: &Story, document: &Value) -> Vec<Piece> {
    let mut out = Vec::new();
    let mut batch: Vec<Piece> = Vec::new();
    for p in &story.pieces {
        if batch.last().is_some_and(|last| speech_person(story, document, last) != speech_person(story, document, p)) {
            out.extend(join_short_lines(&batch)); batch.clear();
        }
        batch.push(p.clone());
    }
    out.extend(join_short_lines(&batch)); out
}

/// What step 4/5 shows on screen, as ASS: headline items for the whole clip, speech lines in time.
/// Returns the subtitle file and the text roles that were asked for but have nothing to draw yet.
pub fn subtitles(story: &Story, document: &Value) -> (String, Vec<String>) {
    subtitles_in(story, document, "normal")
}
/// A role's blend mode; headlines always draw normally.
fn blend_of(style: &Value) -> &str {
    style["blend"].as_str().filter(|b| crate::styles::BLENDS.contains(b)).unwrap_or("normal")
}
/// The non-normal blend modes the speech and emphasis layers use, each needing its own subtitle file.
pub fn blend_modes(document: &Value) -> Vec<String> {
    let mut modes: Vec<String> = ["speech", "emphasis"]
        .iter()
        .map(|role| blend_of(&document["text_styles"][role]).to_owned())
        .filter(|m| m != "normal")
        .collect();
    modes.sort();
    modes.dedup();
    modes
}
/// The subtitle file of the roles drawn with `mode`.
pub fn subtitles_in(story: &Story, document: &Value, mode: &str) -> (String, Vec<String>) {
    let (width, height) = frame(document);
    let mut ass = format!(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n\
[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n\
Style: Text,Leelawadee UI,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,1,5,60,60,0,1\n\
Style: Box,Leelawadee UI,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,12,0,5,60,60,0,1\n\n\
[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    );
    let total = story.duration_ms();
    let mut skipped = vec!["emphasis".to_owned(), "english".to_owned()];
    skipped.retain(|role| document["text_roles"].as_array().is_some_and(|roles| roles.iter().any(|r|r.as_str()==Some(role.as_str()))));
    let style = speech_style(document);
    if blend_of(&style) == mode {
        for (start,end,text) in speech_cues(story, document) {
            ass.push_str(&event((width,height),0,start,end,&style,&text));
        }
    }
    let emphasis = emphasis_cues(story, document);
    if !emphasis.is_empty() {
        skipped.retain(|role| role != "emphasis");
        let style = match &document["text_styles"]["emphasis"] {
            s @ Value::Object(_) => s.clone(),
            _ => default_style("emphasis"),
        };
        if blend_of(&style) == mode {
            for (start, end, text) in &emphasis {
                ass.push_str(&event((width, height), 0, *start, *end, &style, text));
            }
        }
    }
    let translated = subtitle_lines(story, document);
    if !translated.is_empty() {
        skipped.retain(|role| role != "english");
        let style = match &document["text_styles"]["english"] {
            s @ Value::Object(_) => s.clone(),
            _ => default_style("english"),
        };
        if blend_of(&style) == mode {
            for (start, end, text) in &translated {
                ass.push_str(&event((width, height), 0, *start, *end, &style, text));
            }
        }
    }
    for item in headlines(document).into_iter().filter(|_| mode == "normal") {
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

/// One piece's speech as display lines whose words appear as they are said: one event per word, each
/// showing the line up to that word, so words not yet said take no space on screen (karaoke tags would
/// still draw their shadow). Pieces without timed words fall back to whole lines.
pub fn karaoke(piece: &Piece, limit: usize) -> Vec<(u64, u64, String)> {
    karaoke_from(piece, lines(piece, limit))
}
fn karaoke_from(piece: &Piece, plain: Vec<(u64,u64,String)>) -> Vec<(u64,u64,String)> {
    if piece.words.is_empty() {
        return plain;
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
        // the words of this display line, as lines() grouped them
        let mut built = String::new();
        let mut shown: Vec<(u64, String)> = Vec::new();
        while next < words.len() {
            let mut joined = built.clone();
            join(&mut joined, words[next].1);
            if !built.is_empty() && !text.starts_with(joined.as_str()) {
                break;
            }
            shown.push((words[next].0.clamp(start, end), joined.clone()));
            built = joined;
            next += 1;
            if built == text {
                break;
            }
        }
        if shown.is_empty() {
            out.push((start, end, text));
            continue;
        }
        for (i, (at, so_far)) in shown.iter().enumerate() {
            let until = shown.get(i + 1).map(|w| w.0).unwrap_or(end);
            if until > *at {
                out.push((*at, until, so_far.clone()));
            }
        }
    }
    out
}

/// Short readable emphasis beat, followed by a small tail; matches stories.js.
pub const EMPHASIS_HOLD_MS: u64 = 450;
pub const EMPHASIS_TAIL_MS: u64 = 180;
pub const EMPHASIS_GAP_MS: u64 = 160;

/// Explicit word_ids form one phrase. Legacy adjacent fragments can be coalesced without borrowing letters.
fn emphasis_groups(p: &Piece, document: &Value) -> Vec<Vec<(u64,String,u64)>> {
    let explicit=|word: &(u64,String,u64)|document["emphasis"][&p.segment].as_array().into_iter().flatten()
        .any(|v|v["word_ids"].is_array()&&v["text"].as_str()==Some(word.1.as_str()));
    let mut groups:Vec<Vec<(u64,String,u64)>>=vec![];
    for word in picks(p,document) {
        let merge=groups.last().is_some_and(|g| {
            let last=g.last().unwrap();let mut text=String::new();for w in g {join(&mut text,&w.1);}join(&mut text,&word.1);
            !explicit(last)&&!explicit(&word)&&word.0>=last.2&&word.0-last.2<=EMPHASIS_GAP_MS
                &&locate(p,&text,g[0].0).is_some_and(|(a,_)|a.abs_diff(g[0].0)<=1)
        });
        if merge {groups.last_mut().unwrap().push(word);} else {groups.push(vec![word]);}
    }
    groups
}

/// Emphasis windows on the final playback clock. Camera/dead-air splits retain a phrase's identity.
pub fn emphasis_lines(story: &Story, document: &Value) -> Vec<(u64,u64,String)> {
    if !document["text_roles"].as_array().is_some_and(|r|r.iter().any(|v|v=="emphasis")) {return vec![];}
    let mut events:HashMap<(usize,String,u64,String),(u64,u64,String)>=HashMap::new();let mut at=0;
    for p in &story.pieces {
        let same:Vec<_>=story.pieces.iter().filter(|other|other.source==p.source&&other.segment==p.segment).collect();
        let mut full=p.clone();full.start_ms=same.iter().map(|v|v.start_ms).min().unwrap();full.end_ms=same.iter().map(|v|v.end_ms).max().unwrap();
        full.words=same.iter().flat_map(|v|v.words.iter().cloned()).collect();full.words.sort_by_key(|v|(v.1,v.2));full.words.dedup();
        for group in emphasis_groups(&full,document) {
            let start=group[0].0;let end=group.last().unwrap().2;
            if start>=p.end_ms||end<=p.start_ms {continue;}
            let mut text=String::new();for word in group {join(&mut text,&word.1);}
            let a=at+start.max(p.start_ms)-p.start_ms;let b=at+end.min(p.end_ms)-p.start_ms;
            let key=(p.source,p.segment.clone(),start,text.clone());
            events.entry(key).and_modify(|v|v.1=v.1.max(b)).or_insert((a,b,text));
        }
        at+=p.end_ms-p.start_ms;
    }
    let mut out:Vec<_>=events.into_values().collect();out.sort_by(|a,b|(a.0,&a.2).cmp(&(b.0,&b.2)));
    for i in 0..out.len() {
        let next=out.get(i+1).map_or(at,|e|e.0);
        out[i].1=out[i].1.saturating_add(EMPHASIS_TAIL_MS).max(out[i].0+EMPHASIS_HOLD_MS).min(next).min(at);
    }
    out.retain(|(a,b,_)|b>a);out
}

/// A piece's emphasis picks as (start, text, end), in time order. A pick is {text, start_ms, end_ms}
/// (step 4) or a bare start time of a timed word (older drafts).
fn picks(p: &Piece, document: &Value) -> Vec<(u64, String, u64)> {
    let mut words: Vec<(u64, String, u64)> = document["emphasis"][&p.segment]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|v| match v.as_u64() {
            Some(at) => p.words.iter().find(|w| w.1 == at).map(|w| (w.1, w.0.trim().to_owned(), w.2)),
            None => {
                let at = v["start_ms"].as_u64()?;
                Some((at, v["text"].as_str()?.trim().to_owned(), v["end_ms"].as_u64().unwrap_or(at)))
            }
        })
        // a pick keeps its letters but takes its clock from the piece's words as they are now
        // (re-timed or shifted since it was picked)
        .map(|(at, text, end)| locate(p, &text, at).map_or((at, text.clone(), end), |(a, b)| (a, text, b)))
        .filter(|(at, text, _)| !text.is_empty() && *at < p.end_ms && *at + 1 > p.start_ms)
        .collect();
    words.sort();
    words
}
/// Where `text` is said in the piece: the run of letters matching it closest to `near`, timed by the
/// letters' share of their words.
fn locate(p: &Piece, text: &str, near: u64) -> Option<(u64, u64)> {
    let mut letters: Vec<(char, u64, u64)> = Vec::new();
    for (word, from, to) in &p.words {
        let word = word.trim();
        let count = word.chars().count().max(1) as u64;
        for (k, c) in word.chars().enumerate() {
            let span = to.saturating_sub(*from);
            letters.push((c, from + span * k as u64 / count, from + span * (k as u64 + 1) / count));
        }
    }
    let want: Vec<char> = text.chars().filter(|c| !c.is_whitespace()).collect();
    if want.is_empty() || want.len() > letters.len() {
        return None;
    }
    (0..=letters.len() - want.len())
        .filter(|&i| letters[i..i + want.len()].iter().zip(&want).all(|(l, w)| l.0 == *w))
        .min_by_key(|&i| letters[i].1.abs_diff(near))
        .map(|i| (letters[i].1, letters[i + want.len() - 1].2))
}
/// The piece with its emphasised words taken out of the speech, so a word is not on screen twice
/// (P'Ohm 21 Oct 2026). Each pick is cut where its letters are said closest to its time; a timed
/// word left empty is dropped. Matches the step 4 preview (`speechText` in stories.js).
fn without_picks(p: &Piece, document: &Value) -> Piece {
    let picked = picks(p, document);
    if picked.is_empty() || p.words.is_empty() {
        return p.clone();
    }
    // every letter with its timed word and the moment it is said, shared by length within the word
    let mut letters: Vec<(usize, char, u64)> = Vec::new();
    for (n, (text, from, to)) in p.words.iter().enumerate() {
        let text = text.trim();
        let count = text.chars().count().max(1) as u64;
        for (k, c) in text.chars().enumerate() {
            letters.push((n, c, from + (to.saturating_sub(*from)) * k as u64 / count));
        }
    }
    let mut keep = vec![true; letters.len()];
    for (start, text, _) in &picked {
        let want: Vec<char> = text.chars().filter(|c| !c.is_whitespace()).collect();
        if want.is_empty() || want.len() > letters.len() {
            continue;
        }
        let best = (0..=letters.len() - want.len())
            .filter(|&i| letters[i..i + want.len()].iter().zip(&want).all(|(l, w)| l.1 == *w))
            .min_by_key(|&i| letters[i].2.abs_diff(*start));
        if let Some(i) = best {
            keep[i..i + want.len()].fill(false);
        }
    }
    let words: Vec<(String, u64, u64)> = p
        .words
        .iter()
        .enumerate()
        .map(|(n, (word, from, to))| {
            let mut text: String = letters.iter().zip(&keep).filter(|(l, k)| l.0 == n && **k).map(|(l, _)| l.1).collect();
            // a phrase's trailing space (realign) stays with what is left of it
            if !text.is_empty() && word.ends_with(' ') { text.push(' '); }
            (text, *from, *to)
        })
        .filter(|(text, _, _)| !text.is_empty())
        .collect();
    let mut text = String::new();
    for (w, _, _) in &words {
        join(&mut text, w);
    }
    Piece { words, text, ..p.clone() }
}
/// The story whose speech leaves out the emphasised words, when emphasis is on screen.
fn speech_story(story: &Story, document: &Value) -> Story {
    let ticked = document["text_roles"].as_array().is_some_and(|r| r.iter().any(|v| v == "emphasis"));
    if !ticked {
        return story.clone();
    }
    Story { pieces: story.pieces.iter().map(|p| without_picks(p, document)).collect(), ..story.clone() }
}

/// Defaults shared with the style panel; no entrance animation until explicitly chosen.
fn default_style(role: &str) -> Value {
    match role {
        // Nina orange line: (1, 0.5, 0), size 24 under a white 21 (about 1.14x), just below the white line
        "emphasis" => serde_json::json!({"font":"family:DB Heavent","size":143,"color":"#ff8000","x":50,"y":81,"outline":4,"shadow":1}),
        "english" => serde_json::json!({"font":"system","size":47,"color":"#ececf1","x":50,"y":90,"outline":2}),
        _ => serde_json::json!({"font":"family:DB Heavent","size":125,"color":"#ececf1","x":50,"y":75,"outline":4,"shadow":1}),
    }
}
/// Step 4's speech style; a draft that never opened its style panel uses that panel's defaults.
pub fn speech_style(document: &Value) -> Value { text_style(document,"speech") }
pub fn text_style(document: &Value, role: &str) -> Value {
    match &document["text_styles"][role] {
        style @ Value::Object(_) => style.clone(),
        _ => default_style(role),
    }
}
/// The same final speech cues feed video and native CapCut output. Emphasised words are removed once.
pub fn speech_cues(story: &Story, document: &Value) -> Vec<(u64,u64,String)> {
    if !document["text_roles"].as_array().is_some_and(|roles|roles.iter().any(|r|r=="speech")) {return vec![];}
    let spoken=speech_story(story,document);
    let style=speech_style(document);
    let mut cues=vec![]; let mut at=0;
    // a line's pieces (split where dead air is cut) are laid out together on the played clock, so a card runs
    // across a cut instead of leaving "ฉันน่ะ | คิด | ว่า..." as cards of one word (24 Oct, against Nina 16)
    let pieces=speech_pieces(&spoken,document);
    let mut i=0;
    while i<pieces.len() {
        let mut j=i+1;
        while j<pieces.len() && pieces[j].segment==pieces[i].segment && pieces[j].source==pieces[i].source && pieces[j].start_ms>=pieces[j-1].end_ms { j+=1; }
        let run=&pieces[i..j];
        let mut words:Vec<(String,u64,u64)>=Vec::new(); let mut off=0; let mut heard:Option<(&str,u64,u64)>=None;
        for p in run {
            for (w,a,b) in &p.words {
                if *b<p.start_ms || *a>p.end_ms { continue; }
                let (from,to)=(off+(*a).clamp(p.start_ms,p.end_ms)-p.start_ms, off+(*b).clamp(p.start_ms,p.end_ms)-p.start_ms);
                // a word (or a corrected phrase) said across a dead-air cut is in both pieces: draw it once
                if heard==Some((w.as_str(),*a,*b)) { if let Some(last)=words.last_mut() { last.2=to; } continue; }
                heard=Some((w.as_str(),*a,*b));
                words.push((w.clone(), from, to));
            }
            off+=p.end_ms-p.start_ms;
        }
        let mut text=String::new(); for p in run { join(&mut text,p.text.trim()); }
        let piece=Piece{source:run[0].source,start_ms:0,end_ms:off,text:if run.len()==1 {run[0].text.clone()} else {text},words,timed:run[0].timed,segment:run[0].segment.clone()};
        let rows=layout_lines(&piece,&style);
        let rows=if style["enter"].as_str()==Some("words") {karaoke_from(&piece,rows)} else {rows};
        cues.extend(rows.into_iter().map(|(a,b,text)|(at+a,at+b,text)));
        at+=off; i=j;
    }
    // A zero-length ASR token can repeat the next word at the same clock. Draw that event once.
    let mut unique: Vec<(u64,u64,String)>=Vec::new();
    for (a,b,text) in cues {
        if let Some(last)=unique.last_mut().filter(|last|last.0==a&&last.2==text) {last.1=last.1.max(b);}
        else {unique.push((a,b,text));}
    }
    // a card said in no time at the end of one run of pieces ("บาท" before a dead-air cut, C4926 s38) joins the
    // card that starts with it, the same as inside a run (card_groups)
    let mut k = 0;
    while k + 1 < unique.len() {
        if unique[k].1.saturating_sub(unique[k].0) < FLASH_MS && unique[k + 1].0.saturating_sub(unique[k].1) < FLASH_MS {
            let (a, _, text) = unique.remove(k);
            unique[k] = (a, unique[k].1, format!("{} {}", text.trim_end(), unique[k].2));
        } else {
            k += 1;
        }
    }
    for i in 1..unique.len() {
        let next = unique[i].0;
        if next > unique[i - 1].1 && next - unique[i - 1].1 <= CARD_HOLD_MS {
            unique[i - 1].1 = next;
        }
    }
    hold_for_emphasis(&mut unique, &emphasis_lines(story, document));
    unique
}
/// How long before an emphasis starts its setup line may have ended and still be held for it, ms.
pub const STACK_REACH_MS: u64 = 1500;
/// Nina's stacked cards (Nina 16-19: 132 of 132 orange cards sit under a white one): the white setup line
/// stays on until the orange punch line said after it ends, and both go together; never into the next line.
pub fn hold_for_emphasis(cues: &mut [(u64,u64,String)], emphasis: &[(u64,u64,String)]) {
    for &(a,b,_) in emphasis {
        let Some(i)=cues.iter().rposition(|c|c.0<=a&&a<=c.1+STACK_REACH_MS) else {continue};
        let next=cues.get(i+1).map_or(u64::MAX,|c|c.0);
        cues[i].1=cues[i].1.max(b.min(next));
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
        let style = speech_style(document);
        let mut at = 0;
        for p in &speech_pieces(story, document) {
            for (start, end, text) in layout_lines(p, &style) {
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
/// Letters in a line short enough to join the line it runs into (the preview uses the same: SHORT in stories.js).
pub const SHORT_LINE: usize = 4;
/// Back-channel words ("ใช่", "อืม") stay a card of their own, as in a Nina clip; only a cut-off word joins the next
/// line. Matches BACKCHANNEL in stories.js.
pub const BACKCHANNEL: [&str; 16] = ["ใช่", "อืม", "อือ", "เออ", "อ๋อ", "อ้อ", "เฮ้ย", "ค่ะ", "ครับ", "จ้ะ", "จ้า", "โห", "ว้าว", "หา", "ฮะ", "อ่าว"];
/// A piece of one short word ("การ" said before a pause, P'Ohm 23 Oct) joins the piece it runs straight into,
/// same video and no gap, so it is not on screen alone; the joined line is split again if it is too long.
pub fn join_short_lines(pieces: &[Piece]) -> Vec<Piece> {
    let mut out: Vec<Piece> = Vec::new();
    for p in pieces {
        match out.last_mut() {
            // the next piece of the same video; the dead air between them is cut, so the joined piece is
            // as long as the two together and the second one's words move up by the gap
            // pieces of one line (its inner dead air cut out) read as that one line
            Some(last) if last.source == p.source && p.start_ms >= last.end_ms && last.segment == p.segment => {
                let gap = p.start_ms - last.end_ms;
                if last.segment != p.segment {
                    last.text = format!("{}{}", last.text.trim_end(), p.text.trim_start());
                    last.segment = p.segment.clone();
                } else if last.text != p.text {
                    // the parts' own words (emphasis taken out): the line is all of them in order
                    let mut text = last.text.clone();
                    join(&mut text, p.text.trim());
                    last.text = text;
                }
                last.words.extend(p.words.iter().map(|(w, from, to)| (w.clone(), from - gap, to - gap)));
                // A camera boundary may include the same timed word on both sides; do not speak it twice.
                // Distinct repetitions have different clocks and remain separate.
                let mut seen=std::collections::HashSet::new();last.words.retain(|word|seen.insert(word.clone()));
                last.end_ms += p.end_ms - p.start_ms;
                last.timed &= p.timed;
            }
            _ => out.push(p.clone()),
        }
    }
    out
}
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct TranslationRange {
    pub start_ms: u64,
    pub end_ms: u64,
    pub output_start_ms: u64,
    pub output_end_ms: u64,
}
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct TranslationCue {
    pub id: String,
    pub story_id: String,
    pub segment_id: String,
    pub occurrence: u32,
    pub source_id: String,
    pub start_ms: u64,
    pub end_ms: u64,
    pub output_start_ms: u64,
    pub output_end_ms: u64,
    pub text: String,
    pub timing: &'static str,
    pub ranges: Vec<TranslationRange>,
}
#[derive(Clone, Debug, Serialize)]
pub struct TranslationManifest {
    pub schema_version: u8,
    pub input_revision: String,
    pub cues: Vec<TranslationCue>,
    pub legacy_count: usize,
    pub timing_verified: bool,
    pub timing_warning_count: usize,
}
fn cue_digest(value: &impl Serialize) -> String {
    let bytes=serde_json::to_vec(value).expect("serializable caption anchors");
    format!("{:x}",Sha256::digest(bytes))[..16].to_owned()
}
/// Full Thai phrases, including highlighted words. The final retained source pieces supply
/// all clocks; a camera boundary never creates a new word or an arbitrary translation page.
pub fn translation_cues(story: &Story, document: &Value) -> Vec<TranslationCue> {
    struct Run { source:usize, segment:String, occurrence:u32, source_end:u64, at:u64, end:u64, text:String, timed:bool,
        words:Vec<(String,u64,u64)>, identities:HashMap<(String,u64,u64),usize> }
    let mut runs:Vec<Run>=vec![];
    let mut occurrences:HashMap<(usize,String),u32>=HashMap::new();
    let mut slices:Vec<(usize,u64,&Piece)>=vec![];
    let mut at=0;
    for p in &story.pieces {
        let length=p.end_ms-p.start_ms;
        slices.push((p.source,at,p));
        if !runs.last().is_some_and(|r|r.source==p.source&&r.segment==p.segment&&p.start_ms>=r.source_end) {
            let occurrence=occurrences.entry((p.source,p.segment.clone())).or_default();*occurrence+=1;
            runs.push(Run{source:p.source,segment:p.segment.clone(),occurrence:*occurrence,source_end:p.end_ms,at,end:at+length,text:p.text.clone(),timed:p.timed,
                words:vec![],identities:HashMap::new()});
        }
        let run=runs.last_mut().unwrap();run.end=at+length;run.source_end=p.end_ms;
        if run.text!=p.text && !p.text.trim().is_empty() {join(&mut run.text,p.text.trim());}
        for (word,a,b) in &p.words {
            if *a>p.end_ms || *b<p.start_ms {continue;}
            let from=at+(*a).clamp(p.start_ms,p.end_ms)-p.start_ms;
            let to=at+(*b).clamp(p.start_ms,p.end_ms)-p.start_ms;
            let key=(word.clone(),*a,*b);
            if let Some(&index)=run.identities.get(&key) {run.words[index].1=run.words[index].1.min(from);run.words[index].2=run.words[index].2.max(to);}
            else {run.identities.insert(key,run.words.len());run.words.push((word.clone(),from,to));}
        }
        at+=length;
    }
    let style=speech_style(document);
    let mut cues=vec![];
    for run in runs {
        let piece=Piece{source:run.source,start_ms:run.at,end_ms:run.end,text:run.text,words:run.words,timed:run.timed,segment:run.segment.clone()};
        for (a,b,text) in layout_lines(&piece,&style) {
            let (start,end)=(run.at+a,run.at+b);
            let mut ranges:Vec<TranslationRange>=vec![];
            for (source,from,p) in &slices {
                if *source!=run.source || p.segment!=run.segment {continue;}
                let left=start.max(*from);let right=end.min(from+p.end_ms-p.start_ms);
                if right<=left {continue;}
                let source_start=p.start_ms+left-from;let source_end=p.start_ms+right-from;
                if let Some(last)=ranges.last_mut().filter(|r|r.end_ms==source_start&&r.output_end_ms==left) {last.end_ms=source_end;last.output_end_ms=right;}
                else {ranges.push(TranslationRange{start_ms:source_start,end_ms:source_end,output_start_ms:left,output_end_ms:right});}
            }
            let (Some(first),Some(last))=(ranges.first(),ranges.last()) else {continue;};
            let source=source_id(document,&story.sources[run.source]).unwrap_or("").to_owned();
            let id=format!("e_{}",cue_digest(&(&story.id,&source,run.source,&run.segment,run.occurrence,first.start_ms,last.end_ms,&text)));
            cues.push(TranslationCue{id,story_id:story.id.clone(),segment_id:run.segment.clone(),occurrence:run.occurrence,source_id:source,
                start_ms:first.start_ms,end_ms:last.end_ms,output_start_ms:start,output_end_ms:end,text,
                timing:if piece.words.is_empty(){"segment"}else{"words"},ranges});
        }
    }
    cues
}
/// Saved-draft anchors used by the AI round, browser, native CapCut and video export.
pub fn translation_manifest(document: &Value, jobs: &[Job]) -> Result<TranslationManifest,String> {
    let stories=plan(document,jobs)?;
    let selected:std::collections::HashSet<_>=stories.iter().flat_map(|story|story.pieces.iter().map(|p|p.segment.as_str())).collect();
    let timing_warning_count=spoken(document,jobs).iter().filter(|(id,_)|selected.contains(id.as_str())).map(|(_,s)|{
        if s.words.is_empty()&&!s.text.trim().is_empty(){return 1;}
        s.words.iter().enumerate().filter(|(i,(_,a,b))|b<=a||b.saturating_sub(*a)>=1000||*a<s.start_ms||*b>s.end_ms||(*i>0&&*a<s.words[*i-1].2)).count()
    }).sum();
    let cues:Vec<_>=stories.iter().flat_map(|story|translation_cues(story,document)).collect();
    let input_revision=cue_digest(&(1,&cues));
    Ok(TranslationManifest{schema_version:1,input_revision,cues,legacy_count:document["subtitles"].as_object().map_or(0,|v|v.len()),timing_verified:false,timing_warning_count})
}

/// The step 4 subtitle layer (lines translated by a chat AI, by segment id), each line on screen for its
/// pieces in output time; empty when the layer is not ticked or nothing was translated.
pub fn subtitle_lines(story: &Story, document: &Value) -> Vec<(u64, u64, String)> {
    let ticked = document["text_roles"]
        .as_array()
        .is_some_and(|r| r.iter().any(|v| v == "english"));
    let mut out = Vec::new();
    if !ticked {
        return out;
    }
    if document["english_cues"].is_object() {
        let cues=translation_cues(story,document);
        let texts=&document["english_cues"]["translations"];
        if cues.iter().any(|cue|!texts[&cue.id].as_str().is_some_and(|s|!s.trim().is_empty())) {return vec![];}
        return cues.into_iter().map(|cue|(cue.output_start_ms,cue.output_end_ms,texts[&cue.id].as_str().unwrap().trim().to_owned())).collect();
    }
    let mut at = 0;
    for p in &story.pieces {
        let length = p.end_ms - p.start_ms;
        if let Some(text) = document["subtitles"][p.segment.as_str()].as_str().map(str::trim).filter(|t| !t.is_empty()) {
            match out.last_mut() {
                // one line split into neighbouring pieces stays one subtitle
                Some((_, end, last)) if *end == at && last == text => *end = at + length,
                _ => out.push((at, at + length, text.to_owned())),
            }
        }
        at += length;
    }
    // Preserve legacy translations on their known segment spans. Splitting their English
    // words by elapsed-time percentages would invent speech alignment.
    out
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
    ffmpeg_args_on(story, document, output, &[])
}
/// The same, where `gpu[n]` says source `n` is decoded and shrunk on the graphics card. That is for 4K phone footage,
/// where the CPU spends most of the export decoding and scaling. The picture is cut to the frame after it has
/// been shrunk (the same window, found from the shrunk size), then goes on exactly as on the CPU path.
pub fn ffmpeg_args_on(story: &Story, document: &Value, output: &Path, gpu: &[bool]) -> Vec<String> {
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
    for (n, source) in story.sources.iter().enumerate() {
        if gpu.get(n).copied().unwrap_or(false) {
            args.extend(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"].map(String::from));
        }
        args.push("-i".into());
        args.push(source.display().to_string());
    }
    let mut graph = String::new();
    for (n, p) in story.pieces.iter().enumerate() {
        let (a, b) = (p.start_ms as f64 / 1000.0, p.end_ms as f64 / 1000.0);
        // the largest window of the clip's shape, slid to where step 3 put it, then scaled to the frame
        let (cx, cy, zoom, centred) = crop_at(document, &story.sources[p.source], p.start_ms);
        // zooming in on a speaker is a smaller window; no zoom keeps the plain one
        let zoom = if zoom > 1.0001 { format!("/{zoom:.4}") } else { String::new() };
        // a window on a face is centred on it, held inside the picture
        let (x, y) = if centred {
            (format!("max(0,min(iw-ow,iw*{cx:.4}-ow/2))"), format!("max(0,min(ih-oh,ih*{cy:.4}-oh/2))"))
        } else {
            (format!("(iw-ow)*{cx:.4}"), format!("(ih-oh)*{cy:.4}"))
        };
        let crop_w = format!("min(iw,ih*{shape:.6}){zoom}");
        let crop_h = format!("min(ih,iw/{shape:.6}){zoom}");
        let video = if gpu.get(p.source).copied().unwrap_or(false) {
            format!(
                "fps=30,scale_cuda=w='trunc(iw*{width}/({crop_w})/2)*2':h='trunc(ih*{height}/({crop_h})/2)*2':format=nv12,\
hwdownload,format=nv12,crop=w={width}:h={height}:x='{x}':y='{y}',setsar=1,format=yuv420p"
            )
        } else {
            format!("crop=w='{crop_w}':h='{crop_h}':x='{x}':y='{y}',scale={width}:{height},setsar=1,fps=30,format=yuv420p")
        };
        graph.push_str(&format!(
            "[{s}:v]trim=start={a:.3}:end={b:.3},setpts=PTS-STARTPTS,{video}[v{n}];\
[{s}:a]atrim=start={a:.3}:end={b:.3},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,afade=t=in:d=0.008,\
afade=t=out:st={fade:.3}:d=0.008[a{n}];",
            s = p.source,
            fade = ((b - a) - 0.008).max(0.0),
        ));
    }
    for n in 0..story.pieces.len() {
        graph.push_str(&format!("[v{n}][a{n}]"));
    }
    graph.push_str(&format!("concat=n={}:v=1:a=1[cv][ca];[cv]ass=subs.ass", story.pieces.len()));
    for (n, mode) in blend_modes(document).iter().enumerate() {
        let (paper, how) = match mode.as_str() {
            "multiply" => ("white", "multiply"),
            "screen" => ("black", "screen"),
            _ => ("black", "addition"),
        };
        graph.push_str(&format!(
            "[m{n}];[m{n}]split[p{n}][q{n}];[q{n}]drawbox=x=0:y=0:w=iw:h=ih:c={paper}:t=fill,ass=subs_{mode}.ass,format=gbrp[l{n}];\
[p{n}]format=gbrp[g{n}];[g{n}][l{n}]blend=all_mode={how},format=yuv420p"
        ));
    }
    graph.push_str("[vout]");
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
