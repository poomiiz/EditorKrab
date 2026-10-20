//! GPU transcription sidecar for EditorKrab: CTranslate2 large-v3 (int8_float16 on CUDA), kept loaded between requests.
//! Decoding copies v0.29.1 (app/video_edit.py:413-534 at 020c899), which ran faster-whisper 1.2.1's
//! BatchedInferencePipeline: 15 s windows running 1 s past their end, the even windows then the odd ones, 8 per
//! batch, beam 5, no timestamp tokens, no previous-text prompt, no temperature fallback, no VAD, word times from
//! the cross-attention alignment, seams cut between whole words, loops dropped, lines grouped by pauses.
//! Output is whisper.cpp-shaped full JSON so the app's parser reads it.
//!
//! Usage: ek-asr-ct2 <model_dir>
//! Prints `{"ready":true,"load_ms":N}` once the model is loaded (or `{"ready":false,"error":"..."}` and exits),
//! then answers one JSON line per request line on stdin until stdin closes:
//!   in:  {"wav":"<16 kHz mono s16>","language":"<as asked: code or auto>","decode":"<code>","out":"<json path>"}
//!   out: {"ok":true,"transcribe_ms":N,"words":N,...} or {"ok":false,"error":"..."}
use ct2rs::sys::{StorageView, Whisper, WhisperOptions};
use ct2rs::tokenizers::hf::Tokenizer;
use ct2rs::{ComputeType, Config, Device};
use rustfft::{num_complex::Complex, Fft, FftPlanner};
use serde_json::{json, Value};
use std::io::{BufRead, Write};
use std::sync::Arc;
use std::time::Instant;

const RATE: usize = 16_000;
const N_FFT: usize = 400;
const HOP: usize = 160;
/// Mel frames in one 30 s encoder input.
const FRAMES: usize = 3000;
const WINDOW: f32 = 15.0;
const OVERLAP: f32 = 1.0;
const BATCH: usize = 8;
/// The one setting that differs from v0.29.1, which decoded without timestamp tokens (faster-whisper's batched
/// default): without them the model often stops after a window's first sentence. 20 Oct 2026, 5 Thai FLEURS
/// clips: CER 18.2% with stretches of up to 18 s missing; with them 11.0% and nothing missing, while an
/// 11-minute clip takes 49 s instead of 41 s.
const TIMESTAMP_TOKENS: bool = true;
/// faster-whisper's split_to_word_tokens: these languages get one word per decodable token piece.
const UNSPACED: [&str; 6] = ["zh", "ja", "th", "lo", "my", "yue"];
/// v0.29.1 NO_SPACES: any piece may start a word.
const NO_SPACES: [&str; 7] = ["ja", "zh", "yue", "lo", "km", "my", "bo"];
const PREPENDED: &str = "\"'“¿([{-";
const APPENDED: &str = "\"'.。,，!！?？:：”)]}、";
const SENTENCE_END: &str = ".。!！?？";

/// (start s, end s, text as decoded with its leading space, probability)
type Word = (f32, f32, String, f32);

struct Model {
    whisper: Whisper,
    tok: Tokenizer,
    fft: Arc<dyn Fft<f32>>,
    filters: Vec<f32>,
    n_mels: usize,
    options: WhisperOptions,
    eot: usize,
    sot: usize,
    transcribe: usize,
}

#[derive(Default)]
struct Spent {
    features_ms: u128,
    decode_ms: u128,
    align_ms: u128,
}

fn id(tok: &Tokenizer, token: &str) -> Result<usize, String> {
    tok.token_to_id(token)
        .map(|v| v as usize)
        .ok_or_else(|| format!("tokenizer: no {token}"))
}

fn encode(tok: &Tokenizer, text: &str) -> Vec<usize> {
    tok.encode(text, false)
        .map(|e| e.get_ids().iter().map(|&v| v as usize).collect())
        .unwrap_or_default()
}

fn decode(tok: &Tokenizer, ids: &[usize]) -> String {
    let ids: Vec<u32> = ids.iter().map(|&v| v as u32).collect();
    tok.decode(&ids, false).unwrap_or_default()
}

/// faster-whisper get_suppressed_tokens(tokenizer, [-1]): non-speech symbols plus the task and start tokens.
fn suppressed(tok: &Tokenizer) -> Result<Vec<i32>, String> {
    let mut symbols: Vec<String> = "\"#()*+/:;<=>@[\\]^_`{|}~「」『』"
        .chars()
        .map(String::from)
        .collect();
    symbols.extend(
        "<< >> <<< >>> -- --- -( -[ (' (\" (( )) ((( ))) [[ ]] {{ }} ♪♪ ♪♪♪"
            .split(' ')
            .map(String::from),
    );
    let misc: Vec<String> = "♩♪♫♬♭♮♯".chars().map(String::from).collect();
    let mut out: Vec<usize> = Vec::new();
    for s in [" -", " '"] {
        out.extend(encode(tok, s).first());
    }
    for symbol in symbols.iter().chain(misc.iter()) {
        for tokens in [encode(tok, symbol), encode(tok, &format!(" {symbol}"))] {
            if tokens.len() == 1 || misc.contains(symbol) {
                out.extend(tokens.first());
            }
        }
    }
    for special in [
        "<|transcribe|>",
        "<|translate|>",
        "<|startoftranscript|>",
        "<|startofprev|>",
        "<|startoflm|>",
        "<|nospeech|>",
    ] {
        out.push(id(tok, special)?);
    }
    out.sort_unstable();
    out.dedup();
    Ok(out.into_iter().map(|v| v as i32).collect())
}

/// faster-whisper FeatureExtractor.get_mel_filters (Slaney scale, area-normalised), n_mels x 201.
fn mel_filters(n_mels: usize) -> Vec<f32> {
    let bins = N_FFT / 2 + 1;
    let (f_sp, min_log_hz) = (200.0 / 3.0, 1000.0);
    let min_log_mel = min_log_hz / f_sp;
    let logstep = 6.4f64.ln() / 27.0;
    let freqs: Vec<f64> = (0..n_mels + 2)
        .map(|i| {
            let m = 45.245640471924965 * i as f64 / (n_mels + 1) as f64;
            if m >= min_log_mel {
                min_log_hz * (logstep * (m - min_log_mel)).exp()
            } else {
                f_sp * m
            }
        })
        .collect();
    let mut w = vec![0f32; n_mels * bins];
    for m in 0..n_mels {
        let enorm = 2.0 / (freqs[m + 2] - freqs[m]);
        for k in 0..bins {
            let f = k as f64 * RATE as f64 / N_FFT as f64;
            let lower = (f - freqs[m]) / (freqs[m + 1] - freqs[m]);
            let upper = (freqs[m + 2] - f) / (freqs[m + 2] - freqs[m + 1]);
            w[m * bins + k] = (lower.min(upper).max(0.0) * enorm) as f32;
        }
    }
    w
}

/// faster-whisper FeatureExtractor()(clip)[..., :-1] then pad_or_trim to 3000 frames: n_mels x 3000, row-major.
/// The clip is padded with 160 zeros, reflect-padded by 200 at both ends, Hann-windowed, and its log-mel is
/// floored 8 below its own loudest value; frames past the clip are 0, as faster-whisper pads them.
fn features(clip: &[f32], fft: &dyn Fft<f32>, filters: &[f32], n_mels: usize) -> Vec<f32> {
    let bins = N_FFT / 2 + 1;
    let mut x = clip.to_vec();
    x.extend(std::iter::repeat(0.0).take(HOP));
    let (n, pad) = (x.len(), N_FFT / 2);
    let mut p = Vec::with_capacity(n + 2 * pad);
    p.extend((1..=pad).rev().map(|i| x[i]));
    p.extend_from_slice(&x);
    p.extend((0..pad).map(|i| x[n - 2 - i]));
    let stft_frames = 1 + (p.len() - N_FFT) / HOP;
    let used = stft_frames - 1; // magnitudes drop the last STFT frame
    let window: Vec<f32> = (0..N_FFT)
        .map(|i| (0.5 - 0.5 * (2.0 * std::f64::consts::PI * i as f64 / N_FFT as f64).cos()) as f32)
        .collect();
    let mut log = vec![0f32; n_mels * used];
    let mut buf = vec![Complex::new(0f32, 0f32); N_FFT];
    let mut power = vec![0f32; bins];
    let mut loudest = f32::MIN;
    for f in 0..used {
        for i in 0..N_FFT {
            buf[i] = Complex::new(p[f * HOP + i] * window[i], 0.0);
        }
        fft.process(&mut buf);
        for k in 0..bins {
            power[k] = buf[k].norm_sqr();
        }
        for m in 0..n_mels {
            let row = &filters[m * bins..(m + 1) * bins];
            let v: f32 = row.iter().zip(&power).map(|(a, b)| a * b).sum();
            let l = v.max(1e-10).log10();
            loudest = loudest.max(l);
            log[m * used + f] = l;
        }
    }
    let keep = (used - 1).min(FRAMES); // [..., :-1], then trimmed to 3000
    let mut out = vec![0f32; n_mels * FRAMES];
    for m in 0..n_mels {
        for f in 0..keep {
            out[m * FRAMES + f] = (log[m * used + f].max(loudest - 8.0) + 4.0) / 4.0;
        }
    }
    out
}

/// faster-whisper split_tokens_on_unicode: the shortest token runs that decode without a broken character.
fn unicode_pieces(tok: &Tokenizer, tokens: &[usize]) -> Vec<(String, Vec<usize>)> {
    let full: Vec<char> = decode(tok, tokens).chars().collect();
    let mut out = Vec::new();
    let (mut current, mut offset) = (Vec::new(), 0usize);
    for &t in tokens {
        current.push(t);
        let d = decode(tok, &current);
        let broken = d.chars().position(|c| c == '\u{fffd}');
        if broken.is_none_or(|i| full.get(offset + i) == Some(&'\u{fffd}')) {
            offset += d.chars().count();
            out.push((d, std::mem::take(&mut current)));
        }
    }
    out
}

/// faster-whisper split_to_word_tokens.
fn word_tokens(
    tok: &Tokenizer,
    tokens: &[usize],
    language: &str,
    eot: usize,
) -> Vec<(String, Vec<usize>)> {
    let pieces = unicode_pieces(tok, tokens);
    if UNSPACED.contains(&language) {
        return pieces;
    }
    let mut out: Vec<(String, Vec<usize>)> = Vec::new();
    for (text, ids) in pieces {
        let trimmed = text.trim();
        let punctuation = !trimmed.is_empty() && trimmed.chars().all(|c| c.is_ascii_punctuation());
        if ids[0] >= eot || text.starts_with(' ') || punctuation || out.is_empty() {
            out.push((text, ids));
        } else {
            let last = out.last_mut().unwrap();
            last.0.push_str(&text);
            last.1.extend(ids);
        }
    }
    out
}

/// faster-whisper merge_punctuations: opening marks join the next word, closing marks the previous one.
fn merge_punctuations(words: &mut [Word]) {
    if words.len() < 2 {
        return;
    }
    let (mut i, mut j) = (words.len() as isize - 2, words.len() - 1);
    while i >= 0 {
        let p = i as usize;
        if words[p].2.starts_with(' ') && PREPENDED.contains(words[p].2.trim()) {
            let joined = format!("{}{}", words[p].2, words[j].2);
            words[j].2 = joined;
            words[p].2.clear();
        } else {
            j = p;
        }
        i -= 1;
    }
    let (mut i, mut j) = (0usize, 1usize);
    while j < words.len() {
        if !words[i].2.ends_with(' ') && APPENDED.contains(words[j].2.as_str()) {
            let joined = format!("{}{}", words[i].2, words[j].2);
            words[i].2 = joined;
            words[j].2.clear();
        } else {
            i = j;
        }
        j += 1;
    }
}

/// One window's words, as faster-whisper find_alignment + add_word_timestamps give them (absolute seconds).
fn timed_words(
    model: &Model,
    tokens: &[usize],
    alignment: &ct2rs::sys::WhisperAlignmentResult,
    language: &str,
    offset: f32,
    duration: f32,
    last_speech: &mut f32,
) -> Vec<Word> {
    let split = word_tokens(
        &model.tok,
        &[tokens, &[model.eot]].concat(),
        language,
        model.eot,
    );
    if split.len() <= 1 {
        return Vec::new();
    }
    let mut bounds = vec![0usize];
    for (_, ids) in &split[..split.len() - 1] {
        bounds.push(bounds.last().unwrap() + ids.len());
    }
    let mut jumps: Vec<f32> = Vec::new();
    let mut previous = None;
    for a in &alignment.alignments {
        if previous != Some(a.token_x) {
            jumps.push(a.frame_x as f32 / 50.0);
        }
        previous = Some(a.token_x);
    }
    let at = |i: usize| jumps.get(i).or(jumps.last()).copied().unwrap_or(0.0);
    let probs = &alignment.text_token_probs;
    let mut words: Vec<Word> = (0..bounds.len() - 1)
        .map(|w| {
            let (a, b) = (bounds[w], bounds[w + 1]);
            let p = &probs[a.min(probs.len())..b.min(probs.len())];
            let mean = if p.is_empty() {
                0.0
            } else {
                p.iter().sum::<f32>() / p.len() as f32
            };
            (at(a), at(b), split[w].0.clone(), mean)
        })
        .collect();
    // sentence ends are kept within twice the median word length (at most 1.4 s)
    let mut lengths: Vec<f32> = words
        .iter()
        .map(|w| w.1 - w.0)
        .filter(|&d| d != 0.0)
        .collect();
    lengths.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let median = if lengths.is_empty() {
        0.0
    } else if lengths.len() % 2 == 1 {
        lengths[lengths.len() / 2]
    } else {
        (lengths[lengths.len() / 2 - 1] + lengths[lengths.len() / 2]) / 2.0
    }
    .min(0.7);
    let longest = median * 2.0;
    if !lengths.is_empty() {
        for i in 1..words.len() {
            if words[i].1 - words[i].0 > longest {
                if SENTENCE_END.contains(words[i].2.as_str()) && !words[i].2.is_empty() {
                    words[i].1 = words[i].0 + longest;
                } else if SENTENCE_END.contains(words[i - 1].2.as_str())
                    && !words[i - 1].2.is_empty()
                {
                    words[i].0 = words[i].1 - longest;
                }
            }
        }
    }
    merge_punctuations(&mut words);
    let seek = (offset * 100.0) as i64 as f32 / 100.0;
    let round = |v: f32| (v * 100.0).round() / 100.0;
    let mut words: Vec<Word> = words
        .into_iter()
        .filter(|w| !w.2.is_empty())
        .map(|w| (round(seek + w.0), round(seek + w.1), w.2, w.3))
        .collect();
    if words.is_empty() {
        return words;
    }
    // the first word after a pause, and the first and last word of the window, are kept to a speaking length
    if words[0].1 - *last_speech > median * 4.0
        && (words[0].1 - words[0].0 > longest
            || (words.len() > 1 && words[1].1 - words[0].0 > longest * 2.0))
    {
        if words.len() > 1 && words[1].1 - words[1].0 > longest {
            let boundary = (words[1].1 / 2.0).max(words[1].1 - longest);
            words[0].1 = boundary;
            words[1].0 = boundary;
        }
        words[0].0 = (words[0].1 - longest).max(0.0);
    }
    let (start, end) = (offset, offset + duration);
    if start < words[0].1 && start - 0.5 > words[0].0 {
        words[0].0 = (words[0].1 - median).min(start).max(0.0);
    }
    let last = words.len() - 1;
    if end > words[last].0 && end + 0.5 < words[last].1 {
        words[last].1 = (words[last].0 + median).max(end);
        *last_speech = end;
    } else {
        *last_speech = words[last].1;
    }
    words
}

/// v0.29.1 _word_starts: which word pieces begin a whole word. Thai pieces can end mid-syllable ("นั่|งสมาธิ"),
/// so for Thai, Japanese and Chinese a piece counts only where a dictionary word segmenter also puts a boundary
/// (v0.29.1: pythainlp newmm for Thai, BudouX for Japanese / Chinese; here ICU4X's dictionary segmenter), never on
/// a Thai vowel or tone mark, never after a Thai leading vowel. Elsewhere a word starts after a space.
fn word_starts(words: &[Word], language: &str) -> Vec<bool> {
    if !matches!(language, "th" | "ja" | "zh") {
        return (0..words.len())
            .map(|i| {
                i == 0
                    || NO_SPACES.contains(&language)
                    || words[i].2.starts_with(char::is_whitespace)
            })
            .collect();
    }
    let text: String = words.iter().map(|w| w.2.as_str()).collect();
    let bounds: std::collections::HashSet<usize> = icu_segmenter::WordSegmenter::new_dictionary()
        .segment_str(&text)
        .collect();
    let mark =
        |c: char| matches!(c, '\u{0e31}' | '\u{0e34}'..='\u{0e3a}' | '\u{0e47}'..='\u{0e4e}');
    let mut at = 0;
    words
        .iter()
        .enumerate()
        .map(|(i, w)| {
            let start = i == 0
                || (bounds.contains(&at)
                    && !w.2.trim_start().chars().next().is_some_and(mark)
                    && !words[i - 1]
                        .2
                        .trim_end()
                        .chars()
                        .last()
                        .is_some_and(|c| "เแโใไ".contains(c)));
            at += w.2.len();
            start
        })
        .collect()
}

/// v0.29.1 _drop_loops: a run of the same 4-40 letters said more than twice in a row keeps its first two.
fn drop_loops(words: Vec<Word>) -> Vec<Word> {
    const MOST: usize = 2;
    let text: Vec<char> = words.iter().flat_map(|w| w.2.chars()).collect();
    let mut starts = Vec::with_capacity(words.len());
    let mut at = 0;
    for w in &words {
        starts.push(at);
        at += w.2.chars().count();
    }
    let (mut keep, mut skip) = (0usize, 0usize);
    let mut out = Vec::new();
    for (i, w) in words.into_iter().enumerate() {
        let off = starts[i];
        if off >= skip {
            for size in 4..=40 {
                if off + size > text.len() {
                    break;
                }
                let unit = &text[off..off + size];
                let mut n = 1;
                while off + (n + 1) * size <= text.len()
                    && &text[off + n * size..off + (n + 1) * size] == unit
                {
                    n += 1;
                }
                if n > MOST && unit.iter().any(|c| !c.is_whitespace()) {
                    keep = off + MOST * size;
                    skip = off + n * size;
                    break;
                }
            }
        }
        if !(keep <= off && off < skip) {
            out.push(w);
        }
    }
    out
}

fn transcribe(
    model: &Model,
    audio: &[f32],
    language: &str,
    spent: &mut Spent,
) -> Result<Vec<Word>, String> {
    let total = audio.len() as f32 / RATE as f32;
    let starts: Vec<f32> = (0..=(total / WINDOW) as usize)
        .map(|i| i as f32 * WINDOW)
        .filter(|t| total - t >= 0.5)
        .collect();
    let lang = format!("<|{language}|>");
    let mut prompt = vec!["<|startoftranscript|>", &lang, "<|transcribe|>"];
    if !TIMESTAMP_TOKENS {
        prompt.push("<|notimestamps|>");
    }
    let start_seq = [
        model.sot,
        id(&model.tok, &format!("<|{language}|>"))?,
        model.transcribe,
    ];
    // (window start, its words) for every window
    let mut windows: Vec<(f32, Vec<Word>)> = Vec::new();
    // faster-whisper rejects overlapping clips in one call, so v0.29.1 ran the even windows, then the odd ones
    let halves: [Vec<f32>; 2] = [
        starts.iter().copied().step_by(2).collect(),
        starts.iter().copied().skip(1).step_by(2).collect(),
    ];
    for half in halves {
        let mut last_speech = 0.0f32;
        for group in half.chunks(BATCH) {
            let clock = Instant::now();
            let clips: Vec<(f32, f32)> = group
                .iter()
                .map(|&t| (t, (t + WINDOW + OVERLAP).min(total) - t))
                .collect();
            let mut mel = vec![0f32; group.len() * model.n_mels * FRAMES];
            std::thread::scope(|s| {
                for (slot, &(t, d)) in mel.chunks_mut(model.n_mels * FRAMES).zip(&clips) {
                    let from = (t * RATE as f32) as usize;
                    let to = (from + (d * RATE as f32) as usize).min(audio.len());
                    let clip = &audio[from..to];
                    s.spawn(move || {
                        slot.copy_from_slice(&features(
                            clip,
                            model.fft.as_ref(),
                            &model.filters,
                            model.n_mels,
                        ))
                    });
                }
            });
            spent.features_ms += clock.elapsed().as_millis();
            let clock = Instant::now();
            let view = StorageView::new(
                &[group.len(), model.n_mels, FRAMES],
                &mut mel,
                Default::default(),
            )
            .map_err(|e| format!("features: {e}"))?;
            let encoded = model
                .whisper
                .encode(&view, false)
                .map_err(|e| format!("encode: {e}"))?;
            let results = model
                .whisper
                .generate(
                    &encoded,
                    &vec![prompt.to_vec(); group.len()],
                    &model.options,
                )
                .map_err(|e| format!("decode: {e}"))?;
            spent.decode_ms += clock.elapsed().as_millis();
            let clock = Instant::now();
            let tokens: Vec<Vec<usize>> = results
                .iter()
                .map(|r| {
                    r.sequences_ids.first().map_or(Vec::new(), |s| {
                        s.iter().copied().filter(|&t| t < model.eot).collect()
                    })
                })
                .collect();
            let frames: Vec<usize> = clips
                .iter()
                .map(|&(_, d)| d.ceil() as usize * 100)
                .collect();
            let aligned = model
                .whisper
                .align(&encoded, &start_seq, &tokens, &frames, 7)
                .map_err(|e| format!("align: {e}"))?;
            for (i, &(t, d)) in clips.iter().enumerate() {
                let words = if tokens[i].is_empty() {
                    Vec::new()
                } else {
                    timed_words(
                        model,
                        &tokens[i],
                        &aligned[i],
                        language,
                        t,
                        d,
                        &mut last_speech,
                    )
                };
                windows.push((t, words));
            }
            spent.align_ms += clock.elapsed().as_millis();
        }
    }
    // v0.29.1 _batched: windows in time order; start after the last whole word kept, stop at the whole word on the seam
    windows.sort_by(|a, b| {
        a.1.first()
            .map_or(a.0, |w| w.0)
            .partial_cmp(&b.1.first().map_or(b.0, |w| w.0))
            .unwrap()
    });
    let mut out: Vec<Word> = Vec::new();
    for (_, words) in windows {
        let piece: Vec<Word> = words
            .into_iter()
            .filter(|w| !w.2.trim().is_empty())
            .collect();
        if piece.is_empty() {
            continue;
        }
        let whole = word_starts(&piece, language);
        let hi = (piece[0].0 / WINDOW).floor() * WINDOW + WINDOW + OVERLAP / 2.0;
        let after = |w: &Word| out.last().is_none_or(|l: &Word| w.0 >= l.1 - 0.1);
        let begin = (0..piece.len())
            .find(|&i| whole[i] && after(&piece[i]))
            .unwrap_or(piece.len());
        let stop = (begin + 1..piece.len())
            .find(|&i| whole[i] && piece[i].0 >= hi)
            .unwrap_or(piece.len());
        out.extend(piece[begin..stop].iter().cloned());
    }
    Ok(drop_loops(out))
}

/// v0.29.1 _lines: break at a pause of 0.3 s, then split any line over 3.5 s at its widest pause; never inside a
/// word, never leaving a line under 1 s. Returns the index each line starts at.
fn line_starts(words: &[Word], language: &str) -> Vec<usize> {
    const GAP: f32 = 0.3;
    const SHORTEST: f32 = 1.0;
    const LONGEST: f32 = 3.5;
    let mut ok = word_starts(words, language);
    ok[0] = false;
    let last_end = words[words.len() - 1].1;
    let mut cuts = Vec::new();
    let mut last = 0;
    for i in 1..words.len() {
        if ok[i]
            && words[i].0 - words[i - 1].1 >= GAP
            && words[i - 1].1 - words[last].0 >= SHORTEST
            && last_end - words[i].0 >= SHORTEST
        {
            cuts.push(i);
            last = i;
        }
    }
    fn split(words: &[Word], ok: &[bool], lo: usize, hi: usize) -> Vec<usize> {
        if words[hi - 1].1 - words[lo].0 <= LONGEST {
            return Vec::new();
        }
        let inner: Vec<usize> = (lo + 1..hi)
            .filter(|&i| {
                ok[i]
                    && words[i - 1].1 - words[lo].0 >= SHORTEST
                    && words[hi - 1].1 - words[i].0 >= SHORTEST
            })
            .collect();
        if inner.is_empty() {
            return Vec::new();
        }
        let mid = (words[lo].0 + words[hi - 1].1) / 2.0;
        let key = |i: usize| {
            (
                ((words[i].0 - words[i - 1].1) * 100.0).round() as i64,
                -(words[i].0 - mid).abs(),
            )
        };
        let i = inner.iter().copied().fold(
            inner[0],
            |best, i| if key(i) > key(best) { i } else { best },
        );
        let mut out = split(words, ok, lo, i);
        out.push(i);
        out.extend(split(words, ok, i, hi));
        out
    }
    let mut edges = vec![0];
    edges.extend(cuts);
    edges.push(words.len());
    let mut starts = vec![0];
    for pair in edges.windows(2) {
        starts.extend(split(words, &ok, pair[0], pair[1]));
        starts.push(pair[1]);
    }
    starts.dedup();
    starts
}

/// One whisper.cpp-style segment per subtitle line, clocks kept inside the audio and in order.
fn document(words: &[Word], asked: &str, decoded: &str, length_ms: i64) -> Value {
    let ms = |s: f32| ((s.max(0.0) * 1000.0).round() as i64).min(length_ms);
    let mut segs: Vec<Value> = Vec::new();
    let mut previous = 0i64;
    if !words.is_empty() {
        for pair in line_starts(words, decoded).windows(2) {
            let group = &words[pair[0]..pair[1]];
            if group.is_empty() {
                continue;
            }
            let from = ms(group[0].0).max(previous);
            let to = ms(group[group.len() - 1].1).max(from + 1).min(length_ms);
            if from >= to {
                continue;
            }
            let tokens: Vec<Value> = group
                .iter()
                .map(|w| {
                    let f = ms(w.0).clamp(from, to);
                    json!({"id": 0, "text": w.2, "p": w.3.clamp(0.0, 1.0), "offsets": {"from": f, "to": ms(w.1).clamp(f, to)}})
                })
                .collect();
            let text: String = group.iter().map(|w| w.2.as_str()).collect();
            segs.push(
                json!({"offsets": {"from": from, "to": to}, "text": text.trim(), "tokens": tokens}),
            );
            previous = to;
        }
    }
    json!({
        "model": {"type": "large-v3", "multilingual": true, "vocab": 51866},
        "params": {"language": asked, "translate": false},
        "result": {"language": decoded},
        "transcription": segs,
    })
}

fn read_wav(path: &str) -> Result<Vec<f32>, String> {
    let mut r = hound::WavReader::open(path).map_err(|e| format!("wav_open: {e}"))?;
    let s = r.spec();
    if s.sample_rate != RATE as u32 || s.channels != 1 || s.bits_per_sample != 16 {
        return Err("wav_format: need 16 kHz mono s16".into());
    }
    r.samples::<i16>()
        .map(|v| {
            v.map(|v| v as f32 / 32768.0)
                .map_err(|e| format!("wav_read: {e}"))
        })
        .collect()
}

fn handle(model: &Model, line: &str) -> Result<Value, String> {
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
    let mut spent = Spent::default();
    let found = transcribe(model, &audio, decoded, &mut spent)?;
    let length_ms = (audio.len() as i64 * 1000) / RATE as i64;
    let doc = document(&found, asked, decoded, length_ms);
    std::fs::write(out, serde_json::to_vec(&doc).map_err(|e| e.to_string())?)
        .map_err(|e| format!("write: {e}"))?;
    Ok(
        json!({"ok": true, "transcribe_ms": started.elapsed().as_millis() as u64, "words": found.len(),
              "features_ms": spent.features_ms as u64, "decode_ms": spent.decode_ms as u64, "align_ms": spent.align_ms as u64}),
    )
}

fn load(dir: &str) -> Result<Model, String> {
    let config = Config {
        device: Device::CUDA,
        compute_type: ComputeType::INT8_FLOAT16,
        ..Default::default()
    };
    let whisper = Whisper::new(dir, config).map_err(|e| format!("load: {e}"))?;
    let tok = Tokenizer::new(dir).map_err(|e| format!("tokenizer: {e}"))?;
    let n_mels = whisper.n_mels();
    // faster-whisper 1.2.1 generate_segment_batched with v0.29.1's arguments
    let options = WhisperOptions {
        beam_size: 5,
        patience: 1.0,
        length_penalty: 1.0,
        repetition_penalty: 1.0,
        no_repeat_ngram_size: 0,
        max_length: 448,
        sampling_temperature: 0.0,
        return_scores: true,
        return_no_speech_prob: true,
        suppress_blank: true,
        suppress_tokens: suppressed(&tok)?,
        ..Default::default()
    };
    Ok(Model {
        eot: id(&tok, "<|endoftext|>")?,
        sot: id(&tok, "<|startoftranscript|>")?,
        transcribe: id(&tok, "<|transcribe|>")?,
        fft: FftPlanner::new().plan_fft_forward(N_FFT),
        filters: mel_filters(n_mels),
        whisper,
        tok,
        n_mels,
        options,
    })
}

fn main() {
    let say = |v: Value| {
        let mut out = std::io::stdout().lock();
        let _ = writeln!(out, "{v}");
        let _ = out.flush();
    };
    let Some(dir) = std::env::args().nth(1) else {
        say(json!({"ready": false, "error": "usage: ek-asr-ct2 <model_dir>"}));
        std::process::exit(2);
    };
    let started = Instant::now();
    let model = match load(&dir) {
        Ok(m) => m,
        Err(e) => {
            say(json!({"ready": false, "error": e}));
            std::process::exit(1);
        }
    };
    say(json!({"ready": true, "load_ms": started.elapsed().as_millis() as u64}));
    for line in std::io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        say(handle(&model, &line).unwrap_or_else(|e| json!({"ok": false, "error": e})));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn w(a: f32, b: f32, t: &str) -> Word {
        (a, b, t.to_string(), 0.9)
    }
    #[test]
    fn loops_keep_their_first_two() {
        let words: Vec<Word> = (0..6)
            .map(|i| w(i as f32, i as f32 + 0.5, "แค่มีสต๊อค"))
            .collect();
        assert_eq!(drop_loops(words).len(), 2);
        let words = vec![w(0.0, 1.0, " hello"), w(1.0, 2.0, " world")];
        assert_eq!(drop_loops(words).len(), 2);
    }
    #[test]
    fn thai_words_start_on_dictionary_boundaries_never_mid_word() {
        // "นั่|งสมาธิ" is one word split mid-syllable by the recogniser; "ครับ" after a space is a word
        let words = vec![
            w(0.0, 0.3, "นั่"),
            w(0.3, 0.9, "งสมาธิ"),
            w(0.9, 1.2, " ครับ"),
            w(1.2, 1.5, "\u{0e48}"),
        ];
        assert_eq!(word_starts(&words, "th"), vec![true, false, true, false]);
    }
    #[test]
    fn lines_break_at_pauses_and_stay_in_order_inside_the_audio() {
        let words = vec![
            w(0.0, 0.5, "สวัสดี"),
            w(0.5, 1.2, "ครับ"),
            w(2.0, 2.4, " ค่ะ"),
            w(2.4, 9.0, " จบ"),
        ];
        let doc = document(&words, "auto", "th", 3000);
        let segs = doc["transcription"].as_array().unwrap();
        assert_eq!(doc["params"]["language"], "auto");
        assert_eq!(doc["result"]["language"], "th");
        assert_eq!(segs[0]["text"], "สวัสดีครับ");
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
    #[test]
    fn mel_filters_match_faster_whisper() {
        // faster-whisper FeatureExtractor.get_mel_filters(16000, 400, 128)[0, :3] = [0, 0.01237399, 0]
        let f = mel_filters(128);
        assert!((f[1] - 0.01237399).abs() < 1e-7, "{}", f[1]);
        assert_eq!((f[0], f[2]), (0.0, 0.0));
    }
}
