//! Pinned whisper.cpp full JSON. Tokens are subwords; words are placed on the token clock, not verified alignment.
use crate::sources::Identity;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Context {
    pub run_id: String,
    pub source: Identity,
    pub start_ms: u64,
    pub end_ms: u64,
    pub language: String,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Offsets {
    pub from: i64,
    pub to: i64,
}
#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct Token {
    pub id: String,
    pub engine_id: u32,
    pub text: String,
    pub probability: f64,
    pub raw_offsets: Option<Offsets>,
    pub start_ms: Option<u64>,
    pub end_ms: Option<u64>,
    pub timing: String,
}
#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct Word {
    pub text: String,
    pub start_ms: u64,
    pub end_ms: u64,
}
#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct Segment {
    pub id: String,
    pub text: String,
    pub start_ms: u64,
    pub end_ms: u64,
    pub tokens: Vec<Token>,
    pub speaker_id: Option<String>,
    /// Dictionary words of `text`, timed by interpolating the engine token clock.
    #[serde(default)]
    pub words: Vec<Word>,
}
#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct Transcript {
    pub schema_version: u32,
    pub engine: String,
    pub output_format: String,
    pub model_type: String,
    pub context: Context,
    pub detected_language: String,
    pub alignment: String,
    pub segments: Vec<Segment>,
}
#[derive(Deserialize)]
struct EngineModel {
    #[serde(rename = "type")]
    kind: String,
    multilingual: bool,
    vocab: u32,
}
#[derive(Deserialize)]
struct Parameters {
    language: String,
    translate: bool,
}
#[derive(Deserialize)]
struct Detection {
    language: String,
}
#[derive(Deserialize)]
struct EngineToken {
    id: u32,
    text: String,
    p: f64,
    offsets: Option<Offsets>,
}
#[derive(Deserialize)]
struct EngineSegment {
    offsets: Offsets,
    text: String,
    tokens: Vec<EngineToken>,
}
#[derive(Deserialize)]
struct Output {
    model: EngineModel,
    params: Parameters,
    result: Detection,
    transcription: Vec<EngineSegment>,
}

fn identifier(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 128
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}
pub fn language(s: &str) -> bool {
    (s.len() == 2 || s.len() == 3) && s.bytes().all(|b| b.is_ascii_lowercase())
}
impl Context {
    pub fn validate(&self) -> Result<(), String> {
        let stamp = self
            .source
            .modified_ns
            .strip_prefix('-')
            .unwrap_or(&self.source.modified_ns);
        if !identifier(&self.run_id)
            || !identifier(&self.source.id)
            || stamp.is_empty()
            || !stamp.bytes().all(|b| b.is_ascii_digit())
            || self.end_ms <= self.start_ms
            || self.end_ms > i64::MAX as u64
            || self.end_ms - self.start_ms > 300_000
            || (self.language != "auto" && !language(&self.language))
        {
            return Err("invalid_transcription_context".into());
        }
        Ok(())
    }
}
pub const MAX_JSON: u64 = 16 * 1024 * 1024;
pub fn parse(bytes: &[u8], context: &Context) -> Result<Transcript, String> {
    context.validate()?;
    if bytes.len() as u64 > MAX_JSON {
        return Err("transcript_output_too_large".into());
    }
    // whisper.cpp writes subword token bytes as-is; a Thai character split across tokens is not UTF-8 on its own.
    let text = String::from_utf8_lossy(bytes);
    let raw: Output = serde_json::from_str(&text).map_err(|_| "invalid_transcript_output")?;
    if raw.params.translate
        || raw.params.language != context.language
        || !language(&raw.result.language)
        || (context.language != "auto" && raw.result.language != context.language)
        || raw.model.kind.is_empty()
        || raw.model.kind.len() > 64
        || !(50_257..=60_000).contains(&raw.model.vocab)
        || raw.transcription.len() > 10_000
    {
        return Err("transcript_settings_mismatch".into());
    }
    // whisper.cpp v1.9.2 vocab.token_eot starts at 50256, incremented for multilingual.
    let eot = 50_256 + u32::from(raw.model.multilingual);
    let length = (context.end_ms - context.start_ms) as i64;
    let mut segments = Vec::with_capacity(raw.transcription.len());
    let mut previous_end = 0;
    let mut token_count = 0;
    for (index, segment) in raw.transcription.into_iter().enumerate() {
        let clock = &segment.offsets;
        if clock.from < previous_end
            || clock.to <= clock.from
            || clock.to > length
            || segment.text.trim().is_empty()
        {
            return Err("invalid_segment_clock_or_text".into());
        }
        previous_end = clock.to;
        let id = format!(
            "{}:{}:{}:s{index}",
            context.source.id, context.run_id, context.start_ms
        );
        let mut tokens = Vec::new();
        for (number, token) in segment.tokens.into_iter().enumerate() {
            token_count += 1;
            if token_count > 200_000
                || token.id >= raw.model.vocab
                || !token.p.is_finite()
                || !(0.0..=1.0).contains(&token.p)
            {
                return Err("invalid_transcript_token".into());
            }
            if token.id >= eot {
                continue;
            }
            let (start_ms, end_ms, timing) = match &token.offsets {
                None => (None, None, "unavailable"),
                Some(t)
                    if t.from < -1 || t.to < -1 || (t.from >= 0 && t.to >= 0 && t.to < t.from) =>
                {
                    return Err("invalid_token_clock".into());
                }
                Some(t) if t.from < 0 || t.to < 0 => (None, None, "unavailable"),
                Some(t) => {
                    let timing = if t.from < clock.from || t.to > clock.to {
                        "outside_segment"
                    } else if t.from == t.to {
                        "zero_duration"
                    } else {
                        "engine_token"
                    };
                    // Keep raw engine values for review; out-of-segment clocks cannot drive captions.
                    if timing == "outside_segment" {
                        (None, None, timing)
                    } else {
                        (
                            Some(context.start_ms + t.from as u64),
                            Some(context.start_ms + t.to as u64),
                            timing,
                        )
                    }
                }
            };
            tokens.push(Token {
                id: format!("{id}:t{number}"),
                engine_id: token.id,
                text: token.text,
                probability: token.p,
                raw_offsets: token.offsets,
                start_ms,
                end_ms,
                timing: timing.into(),
            });
        }
        let start_ms = context.start_ms + clock.from as u64;
        let end_ms = context.start_ms + clock.to as u64;
        let words = place_words(&segment.text, &tokens, start_ms, end_ms)?;
        segments.push(Segment {
            id,
            text: segment.text,
            start_ms,
            end_ms,
            tokens,
            speaker_id: None,
            words,
        });
    }
    Ok(Transcript {
        schema_version: 1,
        engine: "whisper.cpp".into(),
        output_format: "whisper.cpp-full-json-v1.9.2".into(),
        model_type: raw.model.kind,
        context: context.clone(),
        detected_language: raw.result.language,
        alignment: "unverified".into(),
        segments,
    })
}

/// Times each dictionary word by its position in the segment, mapped through the timed tokens.
fn place_words(
    text: &str,
    tokens: &[Token],
    start_ms: u64,
    end_ms: u64,
) -> Result<Vec<Word>, String> {
    let lengths: Vec<usize> = tokens.iter().map(|t| t.text.chars().count()).collect();
    let total: usize = lengths.iter().sum();
    // Anchors map a 0..1 position in the text to a clock; segment edges always anchor.
    let mut anchors = vec![(0.0, start_ms as f64)];
    let mut seen = 0;
    for (token, length) in tokens.iter().zip(&lengths) {
        if let (Some(a), Some(b), true) = (token.start_ms, token.end_ms, total > 0) {
            anchors.push((seen as f64 / total as f64, a as f64));
            anchors.push(((seen + length) as f64 / total as f64, b as f64));
        }
        seen += length;
    }
    anchors.push((1.0, end_ms as f64));
    anchors.sort_by(|x, y| x.0.total_cmp(&y.0));
    let mut latest = start_ms as f64;
    for anchor in &mut anchors {
        anchor.1 = anchor.1.clamp(latest, end_ms as f64);
        latest = anchor.1;
    }
    let clock = |r: f64| {
        let next = anchors
            .iter()
            .position(|a| a.0 >= r)
            .unwrap_or(anchors.len() - 1);
        if next == 0 {
            return anchors[0].1;
        }
        let (a, b) = (anchors[next - 1], anchors[next]);
        if b.0 <= a.0 {
            b.1
        } else {
            a.1 + (b.1 - a.1) * (r - a.0) / (b.0 - a.0)
        }
    };
    let pieces = crate::words::split(text)?;
    let count = text.chars().count().max(1) as f64;
    let mut at = 0;
    let mut words = Vec::new();
    for piece in pieces {
        let length = piece.chars().count();
        if !piece.trim().is_empty() {
            words.push(Word {
                text: piece.trim().to_string(),
                start_ms: clock(at as f64 / count).round() as u64,
                end_ms: clock((at + length) as f64 / count).round() as u64,
            });
        }
        at += length;
    }
    Ok(words)
}
