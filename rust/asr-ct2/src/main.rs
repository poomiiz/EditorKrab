//! GPU transcription sidecar for EditorKrab: CTranslate2 large-v3 (int8_float16 on CUDA), kept loaded between requests.
//! Decoding follows v0.29.1: 15 s windows with 1 s overlap, each zero-padded to one 30 s chunk, decoded together in
//! batches of 8 with beam 5, words merged by time. Output is whisper.cpp-shaped full JSON so the app's parser reads it.
//!
//! Usage: ek-asr-ct2 <model_dir>
//! Prints `{"ready":true,"load_ms":N}` once the model is loaded (or `{"ready":false,"error":"..."}` and exits),
//! then answers one JSON line per request line on stdin until stdin closes:
//!   in:  {"wav":"<16 kHz mono s16>","language":"<as asked: code or auto>","decode":"<code>","out":"<json path>"}
//!   out: {"ok":true,"transcribe_ms":N,"words":N} or {"ok":false,"error":"..."}
use ct2rs::{ComputeType, Config, Device, Whisper, WhisperOptions};
use serde_json::{json, Value};
use std::io::{BufRead, Write};
use std::time::Instant;

const WINDOW: f32 = 15.0;
const OVERLAP: f32 = 1.0;
const BATCH: usize = 8;

type Word = (f32, f32, String, f32);

fn read_wav(path: &str) -> Result<Vec<f32>, String> {
    let mut r = hound::WavReader::open(path).map_err(|e| format!("wav_open: {e}"))?;
    let s = r.spec();
    if s.sample_rate != 16_000 || s.channels != 1 || s.bits_per_sample != 16 {
        return Err("wav_format: need 16 kHz mono s16".into());
    }
    r.samples::<i16>()
        .map(|v| {
            v.map(|v| v as f32 / 32768.0)
                .map_err(|e| format!("wav_read: {e}"))
        })
        .collect()
}

fn words(whisper: &Whisper, audio: &[f32], language: &str) -> Result<Vec<Word>, String> {
    let rate = whisper.sampling_rate() as f32;
    let chunk = whisper.n_samples();
    let total = audio.len() as f32 / rate;
    let starts: Vec<f32> = (0..=(total / WINDOW) as usize)
        .map(|i| i as f32 * WINDOW)
        .filter(|t| total - t >= 0.5)
        .collect();
    let options = WhisperOptions {
        beam_size: 5,
        ..Default::default()
    };
    let mut out: Vec<Word> = Vec::new();
    for group in starts.chunks(BATCH) {
        // each window becomes its own 30 s chunk; the rest of the chunk is silence
        let mut buf = vec![0f32; chunk * group.len()];
        for (i, &t) in group.iter().enumerate() {
            let from = (t * rate) as usize;
            let to = (((t + WINDOW + OVERLAP) * rate) as usize).min(audio.len());
            buf[i * chunk..i * chunk + (to - from)].copy_from_slice(&audio[from..to]);
        }
        let segments = whisper
            .generate_segments(&buf, Some(language), &options)
            .map_err(|e| format!("decode: {e}"))?;
        for (i, seg) in segments.iter().enumerate() {
            let t = group[i];
            let shift = t - (i * chunk) as f32 / rate;
            let piece: Vec<Word> = seg
                .words
                .iter()
                .flatten()
                .map(|w| {
                    (
                        w.start + shift,
                        w.end + shift,
                        w.word.clone(),
                        w.probability,
                    )
                })
                .filter(|w| !w.2.trim().is_empty() && w.0 < t + WINDOW + OVERLAP)
                .collect();
            // start after the last word kept, stop at the first word past the seam
            let hi = t + WINDOW + OVERLAP / 2.0;
            let last_end = out.last().map(|w| w.1);
            let begin = piece
                .iter()
                .position(|w| last_end.is_none_or(|e| w.0 >= e - 0.1))
                .unwrap_or(piece.len());
            let stop = (begin + 1..piece.len())
                .find(|&j| piece[j].0 >= hi)
                .unwrap_or(piece.len());
            out.extend(piece[begin..stop].iter().cloned());
        }
    }
    unsqueeze(&mut out);
    Ok(out)
}

/// Characters per second above which a word's clock cannot be real speech.
const SQUEEZED: f32 = 25.0;
/// Speaking rate used to give a squeezed word back its length.
const SPOKEN: f32 = 12.0;

/// ct2rs groups tokens into words only at spaces, so in Thai (no spaces) one "word" can be a whole
/// sentence, and it then caps every word at twice the median length (at most 1.4 s; ct2rs 0.10.1
/// whisper.rs:532). A 15 s sentence ends up on a 1.4 s clock and leaves a false silence after it.
/// Such a word is stretched at a speaking pace, never past the next word's start.
fn unsqueeze(words: &mut [Word]) {
    for i in 0..words.len() {
        let chars = words[i].2.trim().chars().count() as f32;
        let (start, end) = (words[i].0, words[i].1);
        if chars / (end - start).max(0.02) <= SQUEEZED {
            continue;
        }
        let limit = words.get(i + 1).map_or(f32::MAX, |next| next.0);
        words[i].1 = (start + chars / SPOKEN).min(limit).max(end);
    }
}

/// One whisper.cpp-style segment per pause of 0.3 s or more, clocks kept inside the audio and in order.
fn document(words: &[Word], asked: &str, decoded: &str, length_ms: i64) -> Value {
    let ms = |s: f32| ((s.max(0.0) * 1000.0).round() as i64).min(length_ms);
    let sep = if matches!(decoded, "th" | "ja" | "zh" | "lo" | "my" | "km" | "yue") {
        ""
    } else {
        " "
    };
    let mut segs: Vec<Value> = Vec::new();
    let mut previous = 0i64;
    let mut group: Vec<&Word> = Vec::new();
    let mut flush = |group: &mut Vec<&Word>, segs: &mut Vec<Value>| {
        if group.is_empty() {
            return;
        }
        let from = ms(group[0].0).max(previous);
        let to = ms(group[group.len() - 1].1).max(from + 1).min(length_ms);
        if from < to {
            let parts: Vec<&str> = group.iter().map(|w| w.2.trim()).collect();
            let tokens: Vec<Value> = parts
                .iter()
                .zip(group.iter())
                .enumerate()
                .map(|(n, (text, w))| {
                    let f = ms(w.0).clamp(from, to);
                    let text = if n > 0 && !sep.is_empty() { format!(" {text}") } else { text.to_string() };
                    json!({"id": 0, "text": text, "p": w.3.clamp(0.0, 1.0), "offsets": {"from": f, "to": ms(w.1).clamp(f, to)}})
                })
                .collect();
            segs.push(json!({"offsets": {"from": from, "to": to}, "text": parts.join(sep), "tokens": tokens}));
            previous = to;
        }
        group.clear();
    };
    for w in words {
        if group.last().is_some_and(|p| w.0 - p.1 >= 0.3) {
            flush(&mut group, &mut segs);
        }
        group.push(w);
    }
    flush(&mut group, &mut segs);
    json!({
        "model": {"type": "large-v3", "multilingual": true, "vocab": 51866},
        "params": {"language": asked, "translate": false},
        "result": {"language": decoded},
        "transcription": segs,
    })
}

fn handle(whisper: &Whisper, line: &str) -> Result<Value, String> {
    let req: Value = serde_json::from_str(line).map_err(|_| "bad_request")?;
    let field = |k: &str| req[k].as_str().ok_or_else(|| format!("bad_request: {k}"));
    let (wav, asked, decoded, out) = (
        field("wav")?,
        field("language")?,
        field("decode")?,
        field("out")?,
    );
    let started = Instant::now();
    let audio = read_wav(wav)?;
    let found = words(whisper, &audio, decoded)?;
    let length_ms = (audio.len() as i64 * 1000) / whisper.sampling_rate() as i64;
    let doc = document(&found, asked, decoded, length_ms);
    std::fs::write(out, serde_json::to_vec(&doc).map_err(|e| e.to_string())?)
        .map_err(|e| format!("write: {e}"))?;
    Ok(
        json!({"ok": true, "transcribe_ms": started.elapsed().as_millis() as u64, "words": found.len()}),
    )
}

fn main() {
    let say = |v: Value| {
        let mut out = std::io::stdout().lock();
        let _ = writeln!(out, "{v}");
        let _ = out.flush();
    };
    let Some(model) = std::env::args().nth(1) else {
        say(json!({"ready": false, "error": "usage: ek-asr-ct2 <model_dir>"}));
        std::process::exit(2);
    };
    let started = Instant::now();
    let config = Config {
        device: Device::CUDA,
        compute_type: ComputeType::INT8_FLOAT16,
        ..Default::default()
    };
    let whisper = match Whisper::new(&model, config) {
        Ok(w) => w,
        Err(e) => {
            say(json!({"ready": false, "error": format!("load: {e}")}));
            std::process::exit(1);
        }
    };
    say(json!({"ready": true, "load_ms": started.elapsed().as_millis() as u64}));
    for line in std::io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        say(handle(&whisper, &line).unwrap_or_else(|e| json!({"ok": false, "error": e})));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn squeezed_sentences_get_their_length_back_but_never_overlap() {
        let sentence = "มันไม่สามารถที่พ่อแม่เองเนี่ยก็ต้องคอยมอนิเตอร์นะครับว่าถึงเวลาต้องไปอย่างนี้";
        let mut w = vec![
            (405.9, 407.3, sentence.to_string(), 0.9),
            (420.3, 420.8, "อันนี้".to_string(), 0.9),
            (421.0, 421.3, "hi".to_string(), 0.9),
            (421.3, 421.4, "longerwordthanthat".to_string(), 0.9),
            (421.5, 422.0, "next".to_string(), 0.9),
        ];
        unsqueeze(&mut w);
        assert_eq!(w[0].1, 405.9 + sentence.chars().count() as f32 / SPOKEN);
        assert_eq!(w[1].1, 420.8); // a real pace is left alone
        assert_eq!(w[2].1, 421.3);
        assert_eq!(w[3].1, 421.5); // stretched only up to the next word
    }
    #[test]
    fn segments_stay_in_order_inside_the_audio() {
        let w = |a: f32, b: f32, t: &str| (a, b, t.to_string(), 0.9);
        let doc = document(
            &[
                w(0.0, 0.5, "สวัสดี"),
                w(0.5, 1.0, "ครับ"),
                w(2.0, 2.4, "ค่ะ"),
                w(2.3, 9.0, "จบ"),
            ],
            "auto",
            "th",
            3000,
        );
        let segs = doc["transcription"].as_array().unwrap();
        assert_eq!(doc["params"]["language"], "auto");
        assert_eq!(doc["result"]["language"], "th");
        assert_eq!(segs[0]["text"], "สวัสดีครับ");
        assert_eq!(segs[1]["offsets"]["to"], 3000);
        let mut previous = 0;
        for s in segs {
            let (from, to) = (
                s["offsets"]["from"].as_i64().unwrap(),
                s["offsets"]["to"].as_i64().unwrap(),
            );
            assert!(from >= previous && to > from && to <= 3000);
            previous = to;
        }
    }
}
