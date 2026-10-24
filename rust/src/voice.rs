//! Who speaks when, for cutting dead air: Silero VAD tells speech from other sound (laughs, a café), and
//! sherpa-onnx's speaker diarization (pyannote segmentation 3.0 + a speaker embedding model) tells the people
//! apart. Both run on CPU from small ONNX files that are downloaded on first use (`plan`).
use crate::asr_download::Item;
use serde::Serialize;
use sherpa_onnx::{
    FastClusteringConfig, OfflineSpeakerDiarization, OfflineSpeakerDiarizationConfig,
    OfflineSpeakerSegmentationModelConfig, OfflineSpeakerSegmentationPyannoteModelConfig,
    SileroVadModelConfig, SpeakerEmbeddingExtractorConfig, VadModelConfig, VoiceActivityDetector,
};
use std::path::{Path, PathBuf};

pub const RATE: u32 = 16_000;

/// The three model files, kept in `<data>/voice/`.
pub struct Models {
    pub vad: PathBuf,
    pub segmentation: PathBuf,
    pub embedding: PathBuf,
    /// SeetaFace frontal face model (faces.rs)
    pub face: PathBuf,
}
impl Models {
    pub fn at(data: &Path) -> Self {
        let dir = data.join("voice");
        Self {
            vad: dir.join("silero_vad.onnx"),
            segmentation: dir.join("pyannote-segmentation-3.0.onnx"),
            embedding: dir.join("wespeaker_en_voxceleb_resnet34_LM.onnx"),
            face: dir.join("seeta_fd_frontal_v1.0.bin"),
        }
    }
    pub fn ready(&self) -> bool {
        [&self.vad, &self.segmentation, &self.embedding, &self.face].iter().all(|p| p.is_file())
    }
}

/// What still has to be downloaded, pinned by size and SHA256 like the speech model.
pub fn plan(data: &Path) -> Vec<Item> {
    let models = Models::at(data);
    [
        (VAD_URL, VAD_SIZE, VAD_SHA256, models.vad),
        (SEGMENTATION_URL, SEGMENTATION_SIZE, SEGMENTATION_SHA256, models.segmentation),
        (EMBEDDING_URL, EMBEDDING_SIZE, EMBEDDING_SHA256, models.embedding),
        (crate::faces::MODEL_URL, crate::faces::MODEL_SIZE, crate::faces::MODEL_SHA256, models.face),
    ]
    .into_iter()
    .filter(|(_, _, _, dest)| !dest.is_file())
    .map(|(url, size, sha256, dest)| Item { url: url.into(), size, sha256: sha256.into(), dest, unzip: false })
    .collect()
}

/// A whole source's first audio stream as 16 kHz mono samples, read through ffmpeg.
pub async fn samples(ffmpeg: &Path, source: &Path) -> Result<Vec<f32>, String> {
    let mut command = tokio::process::Command::new(ffmpeg);
    command
        .args(["-nostdin", "-v", "error", "-i"])
        .arg(source)
        .args(["-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"])
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW, matching media.rs.
    let output = command.output().await.map_err(|_| "voice_audio_start_failed")?;
    if !output.status.success() {
        let reason = String::from_utf8_lossy(&output.stderr);
        return Err(format!("voice_audio_failed: {}", reason.trim().chars().take(300).collect::<String>()));
    }
    Ok(output.stdout.chunks_exact(2).map(|b| i16::from_le_bytes([b[0], b[1]]) as f32 / 32768.0).collect())
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, serde::Deserialize)]
pub struct Turn {
    pub start_ms: u64,
    pub end_ms: u64,
    pub speaker: i32,
}

/// Stretches with speech in them, by Silero VAD.
pub fn speech(model: &Path, samples: &[f32]) -> Result<Vec<(u64, u64)>, String> {
    let config = VadModelConfig {
        silero_vad: SileroVadModelConfig {
            model: Some(model.to_string_lossy().into_owned()),
            threshold: 0.5,
            min_silence_duration: 0.25,
            min_speech_duration: 0.15,
            window_size: 512,
            max_speech_duration: 30.0,
        },
        ten_vad: Default::default(),
        sample_rate: RATE as i32,
        num_threads: 2,
        provider: Some("cpu".into()),
        debug: false,
    };
    let vad = VoiceActivityDetector::create(&config, 60.0).ok_or("vad_load_failed")?;
    let mut out = Vec::new();
    let ms = |n: i32| n.max(0) as u64 * 1000 / RATE as u64;
    let take = |vad: &VoiceActivityDetector, out: &mut Vec<(u64, u64)>| {
        while !vad.is_empty() {
            if let Some(s) = vad.front() {
                out.push((ms(s.start()), ms(s.start() + s.n())));
            }
            vad.pop();
        }
    };
    for chunk in samples.chunks(512) {
        vad.accept_waveform(chunk);
        take(&vad, &mut out);
    }
    vad.flush();
    take(&vad, &mut out);
    Ok(out)
}

/// Who speaks when, by speaker diarization; speakers are numbered from 0.
pub fn speakers(models: &Models, samples: &[f32]) -> Result<Vec<Turn>, String> {
    speakers_at(models, samples, CLUSTER_THRESHOLD)
}
/// How alike two voices must be to count as one person (sherpa-onnx fast clustering; lower splits more).
pub const CLUSTER_THRESHOLD: f32 = 0.5;
pub fn speakers_at(models: &Models, samples: &[f32], threshold: f32) -> Result<Vec<Turn>, String> {
    let config = OfflineSpeakerDiarizationConfig {
        segmentation: OfflineSpeakerSegmentationModelConfig {
            pyannote: OfflineSpeakerSegmentationPyannoteModelConfig {
                model: Some(models.segmentation.to_string_lossy().into_owned()),
                ..Default::default()
            },
            num_threads: 2,
            ..Default::default()
        },
        embedding: SpeakerEmbeddingExtractorConfig {
            model: Some(models.embedding.to_string_lossy().into_owned()),
            num_threads: 2,
            ..Default::default()
        },
        // the number of people is not known; voices closer than this threshold are one person
        clustering: FastClusteringConfig { num_clusters: -1, threshold, ..Default::default() },
        ..Default::default()
    };
    let diarization = OfflineSpeakerDiarization::create(&config).ok_or("diarization_load_failed")?;
    let result = diarization.process(samples).ok_or("diarization_failed")?;
    Ok(result
        .sort_by_start_time()
        .into_iter()
        .map(|s| Turn {
            start_ms: (s.start.max(0.0) * 1000.0) as u64,
            end_ms: (s.end.max(0.0) * 1000.0) as u64,
            speaker: s.speaker,
        })
        .collect())
}

/// The clip's main speaker: whoever says the most transcribed words, or talks longest when no words are given.
pub fn main_speaker(turns: &[Turn], words: &[u64]) -> Option<i32> {
    let mut score: std::collections::BTreeMap<i32, u64> = Default::default();
    for t in turns {
        let said = words.iter().filter(|&&w| w >= t.start_ms && w < t.end_ms).count() as u64;
        *score.entry(t.speaker).or_default() += if words.is_empty() { t.end_ms - t.start_ms } else { said };
    }
    score.into_iter().max_by_key(|&(_, v)| v).map(|(s, _)| s)
}

/// A word with a positive clock protects its actual span. Only zero/reversed clocks use WORD_HOLD_MS;
/// an end stretched past WORD_MAX_MS is not trusted.
pub const WORD_HOLD_MS: u64 = 600;
pub const WORD_MAX_MS: u64 = 2500;
/// Gaps between words and breaths are shorter than this; a smaller shortest pause would cut into speech
/// (23 Oct: 0.1 s chopped a two-person talk into pieces). Nina clips keep 0.3-0.5 s breaths.
pub const MIN_PAUSE_MS: u64 = 300;

/// Dead air: stretches of at least `min_ms` with no speech from the people kept (`keep`, the main speaker unless
/// the person picks others): VAD speech where diarization hears one of them (all VAD speech when `keep` is empty). Laughs, a café and other people's voices are
/// dead air. Diarization misses soft speech (23 Oct, IMG_6779: no turn at 8.8-14.2 s where a line is said), so
/// every transcribed word (`words`, [start, end] ms) is never cut either. Only the talk of people the person
/// dropped is taken out of the speech; speech diarization gave to nobody stays.
pub fn dead_air(speech: &[(u64, u64)], turns: &[Turn], keep: &[i32], words: &[(u64, u64)], total_ms: u64, min_ms: u64) -> Vec<(u64, u64)> {
    dead_air_heard(speech, turns, keep, words, &[], total_ms, min_ms)
}
/// `dead_air` that also keeps every `loud` stretch (see `loud`) not said by a dropped person: a sound VAD does
/// not call speech is not silence (24 Oct review: 1.1 s of sound at 33.5-35.4 s, no VAD speech, was cut).
pub fn dead_air_heard(speech: &[(u64, u64)], turns: &[Turn], keep: &[i32], words: &[(u64, u64)], loud: &[(u64, u64)], total_ms: u64, min_ms: u64) -> Vec<(u64, u64)> {
    dead_air_hushed(speech, turns, keep, words, loud, &[], total_ms, min_ms)
}
#[allow(clippy::too_many_arguments)]
pub fn dead_air_hushed(speech: &[(u64, u64)], turns: &[Turn], keep: &[i32], words: &[(u64, u64)], loud: &[(u64, u64)], hush: &[(u64, u64)], total_ms: u64, min_ms: u64) -> Vec<(u64, u64)> {
    let min_ms = min_ms.max(MIN_PAUSE_MS);
    let dropped: Vec<&Turn> = match keep.is_empty() {
        true => Vec::new(),
        false => turns.iter().filter(|t| !keep.contains(&t.speaker)).collect(),
    };
    let speech: Vec<(u64, u64)> = speech.iter().copied().chain(loud.iter().copied()).collect();
    let speech = &speech[..];
    let words: Vec<(u64, u64)> = {
        let mut w = words.to_vec();
        fit_words_hushed(&mut w, speech, hush);
        w
    };
    let words = &words[..];
    let mut talk: Vec<(u64, u64)> = Vec::new();
    for &(a, b) in speech {
        // the speech left after the dropped people's turns are taken out of it
        let mut at = a;
        let mut cuts: Vec<(u64, u64)> = dropped.iter().map(|t| (t.start_ms.max(a), t.end_ms.min(b))).filter(|(x, y)| y > x).collect();
        cuts.sort();
        for (x, y) in cuts {
            if x > at {
                talk.push((at, x));
            }
            at = at.max(y);
        }
        if b > at {
            talk.push((at, b));
        }
        // and a kept person talking over a dropped one is still talk
        for t in turns.iter().filter(|t| keep.contains(&t.speaker)) {
            let (x, y) = (a.max(t.start_ms), b.min(t.end_ms));
            if y > x {
                talk.push((x, y));
            }
        }
    }
    talk.extend(words.iter().filter_map(|&(w, e)| {
        if w >= total_ms { return None; }
        let end = if e > w { e.min(w.saturating_add(WORD_MAX_MS)) }
            else { w.saturating_add(WORD_HOLD_MS) };
        Some((w, end.min(total_ms)))
    }));
    talk.sort();
    let mut out = Vec::new();
    let mut at = 0u64;
    for (a, b) in talk {
        if a > at && a - at >= min_ms {
            out.push((at, a));
        }
        at = at.max(b);
    }
    if total_ms > at && total_ms - at >= min_ms {
        out.push((at, total_ms));
    }
    out
}

/// How far a word may run past the speech it starts in (quiet word endings VAD trims), ms.
pub const WORD_SLACK_MS: u64 = 150;
/// Length given to a word the engine timed at zero, ms (never past the next word or its speech).
pub const WORD_LEAST_MS: u64 = 200;

/// Word clocks fitted to the speech VAD hears (`speech`, sorted [start, end] ms). The engine stretches a word
/// over the pause after it, starts a word said after a pause at the pause, and times some words at zero
/// (24 Oct, IMG_6779: 11 words of 221 at 0 s, one 2.7 s long across a 1.7 s pause). Such clocks move subtitles,
/// emphasis and cuts, so: a word starting in silence starts where the next speech it reaches begins; a word
/// running more than WORD_SLACK_MS past its speech ends there; a zero-length word gets WORD_LEAST_MS.
/// A word with no speech under it at all is left alone (VAD misses soft speech).
pub fn fit_words(words: &mut [(u64, u64)], speech: &[(u64, u64)]) {
    fit_words_hushed(words, speech, &[]);
}
/// How far after a hushed run a word timed inside it may be moved to the speech that follows, ms.
pub const WORD_SHIFT_MS: u64 = 1000;
/// `fit_words`, and a word timed wholly inside a hushed run (`hush`, see `hush`) is moved to the speech that
/// starts within WORD_SHIFT_MS after the run: nothing is said where it was put (24 Oct: "คำ" sat in 1.8 s of
/// silence at 12.4-14.2 s and kept it from being cut). A word in a gap that is not hushed may be soft speech
/// VAD missed, and stays.
pub fn fit_words_hushed(words: &mut [(u64, u64)], speech: &[(u64, u64)], hush: &[(u64, u64)]) {
    if speech.is_empty() {
        return;
    }
    for w in words.iter_mut() {
        let (s, e) = (w.0, w.1.max(w.0));
        if speech.iter().any(|&(a, b)| a < e.max(s + 1) && b > s) {
            continue;
        }
        let Some(&(_, end)) = hush.iter().find(|&&(a, b)| a <= s && e <= b) else { continue };
        if let Some(&(a, _)) = speech.iter().find(|&&(a, _)| a >= end && a <= end + WORD_SHIFT_MS) {
            *w = (a, a);
        }
    }
    let span_at = |t: u64| speech.iter().copied().find(|&(a, b)| a <= t + WORD_SLACK_MS && t < b);
    let starts: Vec<u64> = words.iter().map(|w| w.0).collect();
    for (i, w) in words.iter_mut().enumerate() {
        let (mut s, mut e) = (w.0, w.1.max(w.0));
        let span = match span_at(s) {
            Some(span) => Some(span),
            // starts in a pause: move to the first speech it reaches
            None => match speech.iter().copied().find(|&(a, _)| a > s && a < e) {
                Some(span) => {
                    s = span.0;
                    Some(span)
                }
                None => None,
            },
        };
        let Some((_, b)) = span else { continue };
        if e > b + WORD_SLACK_MS {
            e = b.max(s);
        }
        if e <= s {
            let next = starts.get(i + 1).copied().filter(|&n| n > s).unwrap_or(u64::MAX);
            e = (s + WORD_LEAST_MS).min(next).min(b.max(s + 1));
        }
        *w = (s, e.max(s));
    }
}

/// Level above which 20 ms of audio is sound, dBFS; speech in these clips sits around -25..-10.
pub const LOUD_DB: f32 = -38.0;
/// Level under which audio is hushed, dBFS (24 Oct, IMG_6779: background -53..-47, speech median -27).
pub const HUSH_DB: f32 = -44.0;
/// Shortest hushed run that counts, ms: shorter dips are the gaps inside words.
pub const HUSH_MIN_MS: u64 = 350;
/// Runs of at least HUSH_MIN_MS where the audio stays under HUSH_DB. VAD keeps talking for a while after a
/// voice stops and joins short pauses into one stretch of speech (24 Oct: 1.8 s of quiet at 12.4-14.2 s sat
/// inside "speech"); these runs are taken out of it.
pub fn hush(samples: &[f32]) -> Vec<(u64, u64)> {
    let n = RATE as usize / 50;
    let mut out: Vec<(u64, u64)> = Vec::new();
    for (k, chunk) in samples.chunks(n).enumerate() {
        let rms = (chunk.iter().map(|x| x * x).sum::<f32>() / chunk.len().max(1) as f32).sqrt();
        if 20.0 * rms.max(1e-5).log10() >= HUSH_DB {
            continue;
        }
        let (a, b) = (k as u64 * 20, k as u64 * 20 + 20);
        match out.last_mut() {
            Some(last) if a == last.1 => last.1 = b,
            _ => out.push((a, b)),
        }
    }
    out.retain(|(a, b)| b - a >= HUSH_MIN_MS);
    out
}
/// VAD speech with the hushed runs taken out: what is really heard.
pub fn heard(speech: &[(u64, u64)], hush: &[(u64, u64)]) -> Vec<(u64, u64)> {
    let mut out = Vec::new();
    for &(a, b) in speech {
        let mut at = a;
        for &(x, y) in hush.iter().filter(|(x, y)| *y > a && *x < b) {
            if x > at {
                out.push((at, x));
            }
            at = at.max(y);
        }
        if b > at {
            out.push((at, b));
        }
    }
    out
}
/// Stretches of at least 200 ms where the audio is above LOUD_DB (gaps up to 100 ms bridged), ms.
pub fn loud(samples: &[f32]) -> Vec<(u64, u64)> {
    let n = RATE as usize / 50;
    let mut out: Vec<(u64, u64)> = Vec::new();
    for (k, chunk) in samples.chunks(n).enumerate() {
        let rms = (chunk.iter().map(|x| x * x).sum::<f32>() / chunk.len().max(1) as f32).sqrt();
        if 20.0 * rms.max(1e-5).log10() <= LOUD_DB {
            continue;
        }
        let (a, b) = (k as u64 * 20, k as u64 * 20 + 20);
        match out.last_mut() {
            Some(last) if a <= last.1 + 100 => last.1 = b,
            _ => out.push((a, b)),
        }
    }
    out.retain(|(a, b)| b - a >= 200);
    out
}

pub const VAD_URL: &str = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx";
pub const SEGMENTATION_URL: &str =
    "https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/main/model.onnx";
pub const EMBEDDING_URL: &str = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx";
pub const VAD_SIZE: u64 = 643_854;
pub const VAD_SHA256: &str = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6";
pub const SEGMENTATION_SIZE: u64 = 5_992_913;
pub const SEGMENTATION_SHA256: &str = "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079";
pub const EMBEDDING_SIZE: u64 = 26_530_550;
pub const EMBEDDING_SHA256: &str = "e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012";

#[cfg(test)]
mod tests {
    use super::*;
    /// Real-clip check, run by hand: EDITORKRAB_VOICE_MODELS=<folder> EDITORKRAB_VOICE_WAV=<16 kHz mono s16 wav>
    #[test]
    #[ignore]
    fn real_clip() {
        let models = Models::at(std::path::Path::new(&std::env::var("EDITORKRAB_VOICE_MODELS").unwrap()).parent().unwrap());
        let bytes = std::fs::read(std::env::var("EDITORKRAB_VOICE_WAV").unwrap()).unwrap();
        let samples: Vec<f32> = bytes[44..].chunks_exact(2).map(|b| i16::from_le_bytes([b[0], b[1]]) as f32 / 32768.0).collect();
        let t = std::time::Instant::now();
        let speech = speech(&models.vad, &samples).unwrap();
        let vad_ms = t.elapsed().as_millis();
        for threshold in [0.3, 0.4, 0.6, 0.7] {
            let turns = speakers_at(&models, &samples, threshold).unwrap();
            let mut talk = std::collections::BTreeMap::<i32, u64>::new();
            for t in &turns { *talk.entry(t.speaker).or_default() += t.end_ms - t.start_ms; }
            println!("threshold {threshold}: {talk:?}");
        }
        let t = std::time::Instant::now();
        let turns = speakers(&models, &samples).unwrap();
        println!("vad {vad_ms} ms, diarization {} ms", t.elapsed().as_millis());
        let main = main_speaker(&turns, &[]);
        println!("speakers: {:?}", turns.iter().map(|t| t.speaker).collect::<std::collections::BTreeSet<_>>());
        for t in &turns { println!("turn {:>6} {:>6} speaker {}", t.start_ms, t.end_ms, t.speaker); }
        let total = samples.len() as u64 * 1000 / RATE as u64;
        for (a, b) in dead_air(&speech, &turns, &main.into_iter().collect::<Vec<_>>(), &[], total, 500) { println!("dead {a:>6} {b:>6}"); }
    }
    #[test]
    fn dead_air_is_where_the_main_speaker_is_quiet_even_if_others_talk() {
        let speech = [(0, 3000), (3500, 6000), (9000, 12000)];
        // speaker 1 laughs at 4.0-5.5 s inside the second stretch of speech
        let turns = [
            Turn { start_ms: 0, end_ms: 3100, speaker: 0 },
            Turn { start_ms: 3500, end_ms: 4000, speaker: 0 },
            Turn { start_ms: 4000, end_ms: 5500, speaker: 1 },
            Turn { start_ms: 9000, end_ms: 12000, speaker: 0 },
        ];
        let main = main_speaker(&turns, &[100, 900, 2000, 3600, 9500, 11000, 4500]);
        assert_eq!(main, Some(0));
        // speech after the laugh that diarization gave to nobody (5.5-6.0 s) stays
        assert_eq!(dead_air(&speech, &turns, &[0], &[], 12500, 400), vec![(3000, 3500), (4000, 5500), (6000, 9000), (12000, 12500)]);
        // keeping both people: the laugh is speech too
        assert_eq!(dead_air(&speech, &turns, &[0, 1], &[], 12500, 400), vec![(3000, 3500), (6000, 9000), (12000, 12500)]);
        // nobody known: any speech counts
        assert_eq!(dead_air(&speech, &turns, &[], &[], 12500, 400), vec![(3000, 3500), (6000, 9000), (12000, 12500)]);
        // a short word uses its actual end; soft speech missed by diarization still stays
        assert_eq!(dead_air(&speech, &turns, &[0], &[(7000, 7100)], 12500, 400), vec![(3000, 3500), (4000, 5500), (6000, 7000), (7100, 9000), (12000, 12500)]);
        assert_eq!(dead_air(&speech, &turns, &[0], &[(7000, 8000)], 12500, 400), vec![(3000, 3500), (4000, 5500), (6000, 7000), (8000, 9000), (12000, 12500)]);
        // a word starting in a pause is fitted to start with the speech it reaches, so the pause before it goes
        assert_eq!(dead_air(&speech, &turns, &[], &[(6000, 12000)], 13500, 400), vec![(3000, 3500), (6000, 9000), (12000, 13500)]);
    }
    #[test]
    fn positive_word_clocks_do_not_create_a_six_hundred_ms_tail() {
        assert_eq!(dead_air(&[], &[], &[], &[(1000, 1100)], 2000, 300), vec![(0, 1000), (1100, 2000)]);
        // A real end longer than the old blanket hold is preserved too.
        assert_eq!(dead_air(&[], &[], &[], &[(1000, 1800)], 2500, 300), vec![(0, 1000), (1800, 2500)]);
    }
    #[test]
    fn missing_word_end_is_protected_conservatively_within_the_source() {
        for end in [1000, 900] {
            assert_eq!(dead_air(&[], &[], &[], &[(1000, end)], 2500, 300), vec![(0, 1000), (1600, 2500)]);
        }
        // Unknown clocks close to the source end cannot extend the clip.
        assert_eq!(dead_air(&[], &[], &[], &[(1800, 1800)], 2000, 300), vec![(0, 1800)]);
        assert_eq!(dead_air(&[], &[], &[], &[(2500, 2500)], 2000, 300), vec![(0, 2000)]);
    }
    #[test]
    fn word_clocks_are_fitted_to_the_speech_heard() {
        let speech = [(1000, 2000), (3000, 5000)];
        let mut words = [
            (1000, 1400),
            (1400, 2800), // stretched over the pause: ends where speech ends
            (2200, 3400), // starts in the pause: starts with the next speech
            (3400, 3400), // timed at zero: gets WORD_LEAST_MS
            (3600, 5100), // within the slack: kept
            (6000, 6500), // no speech heard: left alone
        ];
        fit_words(&mut words, &speech);
        assert_eq!(words, [(1000, 1400), (1400, 2000), (3000, 3400), (3400, 3600), (3600, 5100), (6000, 6500)]);
        // no speech known: nothing changes
        let mut same = [(0, 0)];
        fit_words(&mut same, &[]);
        assert_eq!(same, [(0, 0)]);
    }
    #[test]
    fn a_hushed_run_inside_vad_speech_is_not_heard() {
        // VAD calls 10-20 s speech; the audio is hushed at 12.4-14.2 s: that run is not heard
        assert_eq!(heard(&[(10_000, 20_000)], &[(12_400, 14_200)]), vec![(10_000, 12_400), (14_200, 20_000)]);
        // and a word stretched over it ends where the hush starts, so the hush is dead air
        let speech = heard(&[(10_000, 20_000)], &[(12_400, 14_200)]);
        assert_eq!(dead_air(&speech, &[], &[], &[(12_000, 13_900)], 20_000, 300), vec![(0, 10_000), (12_400, 14_200)]);
        // 400 ms of near-silence counts; a 200 ms dip inside a word does not
        let mut samples = vec![0.3f32; 16_000];
        samples.extend(vec![0.0; 16_000 * 4 / 10]);
        samples.extend(vec![0.3; 16_000]);
        samples.extend(vec![0.0; 16_000 * 2 / 10]);
        samples.extend(vec![0.3; 16_000]);
        assert_eq!(hush(&samples), vec![(1000, 1400)]);
    }
    #[test]
    fn a_word_timed_inside_silence_moves_to_the_speech_after_it() {
        let speech = [(10_000, 12_400), (14_200, 16_000)];
        // "คำ" at 12.6-13.2 s sits in the hush 12.4-14.2 s: it is said with the speech at 14.2 s
        let mut words = [(12_600, 13_200), (20_000, 20_300)];
        fit_words_hushed(&mut words, &speech, &[(12_400, 14_200)]);
        assert_eq!(words[0].0, 14_200);
        // a word in a gap that is not hushed (soft speech) stays
        assert_eq!(words[1], (20_000, 20_300));
        assert_eq!(dead_air_hushed(&speech, &[], &[], &[(12_600, 13_200)], &[], &[(12_400, 14_200)], 16_000, 300), vec![(0, 10_000), (12_400, 14_200)]);
    }
    #[test]
    fn a_sound_vad_does_not_call_speech_is_not_dead_air() {
        // 1.1 s of sound in a 2.6 s gap with no VAD speech stays; the quiet either side of it still goes
        let speech = [(0, 33_000), (35_700, 40_000)];
        assert_eq!(dead_air_heard(&speech, &[], &[], &[], &[(33_500, 34_600)], 40_000, 300), vec![(33_000, 33_500), (34_600, 35_700)]);
        // 20 ms windows: 300 ms of tone, a 60 ms dip bridged, then quiet
        let mut samples = vec![0.5f32; 16_000 * 3 / 10];
        samples.extend(vec![0.0; 960]);
        samples.extend(vec![0.5; 16_000 / 10]);
        samples.extend(vec![0.0; 16_000]);
        assert_eq!(loud(&samples), vec![(0, 460)]);
    }
    #[test]
    fn a_shortest_pause_under_a_breath_is_raised() {
        // gaps of 200 ms between words are never cut, whatever is asked
        let speech = [(0, 1000), (1200, 2000), (2600, 3000)];
        assert_eq!(dead_air(&speech, &[], &[], &[], 3000, 100), vec![(2000, 2600)]);
    }
}
