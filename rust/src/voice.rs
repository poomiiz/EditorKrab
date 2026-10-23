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
}
impl Models {
    pub fn at(data: &Path) -> Self {
        let dir = data.join("voice");
        Self {
            vad: dir.join("silero_vad.onnx"),
            segmentation: dir.join("pyannote-segmentation-3.0.onnx"),
            embedding: dir.join("wespeaker_en_voxceleb_resnet34_LM.onnx"),
        }
    }
    pub fn ready(&self) -> bool {
        [&self.vad, &self.segmentation, &self.embedding].iter().all(|p| p.is_file())
    }
}

/// What still has to be downloaded, pinned by size and SHA256 like the speech model.
pub fn plan(data: &Path) -> Vec<Item> {
    let models = Models::at(data);
    [
        (VAD_URL, VAD_SIZE, VAD_SHA256, models.vad),
        (SEGMENTATION_URL, SEGMENTATION_SIZE, SEGMENTATION_SHA256, models.segmentation),
        (EMBEDDING_URL, EMBEDDING_SIZE, EMBEDDING_SHA256, models.embedding),
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
        clustering: FastClusteringConfig { num_clusters: -1, threshold: 0.5, ..Default::default() },
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

/// How long after a transcribed word starts it is kept from being cut, however long the engine stretched it.
pub const WORD_HOLD_MS: u64 = 600;

/// Dead air: stretches of at least `min_ms` with no speech from the people kept (`keep`, the main speaker unless
/// the person picks others): VAD speech where diarization hears one of them (all VAD speech when `keep` is empty). Laughs, a café and other people's voices are
/// dead air. Diarization misses soft speech (23 Oct, IMG_6779: no turn at 8.8-14.2 s where a line is said), so
/// WORD_HOLD_MS after every transcribed word start (`words`, ms) is never cut either.
pub fn dead_air(speech: &[(u64, u64)], turns: &[Turn], keep: &[i32], words: &[u64], total_ms: u64, min_ms: u64) -> Vec<(u64, u64)> {
    let mut talk: Vec<(u64, u64)> = Vec::new();
    for &(a, b) in speech {
        match keep.is_empty() {
            true => talk.push((a, b)),
            false => {
                for t in turns.iter().filter(|t| keep.contains(&t.speaker)) {
                    let (x, y) = (a.max(t.start_ms), b.min(t.end_ms));
                    if y > x {
                        talk.push((x, y));
                    }
                }
            }
        }
    }
    talk.extend(words.iter().map(|&w| (w, (w + WORD_HOLD_MS).min(total_ms.max(w)))));
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
        assert_eq!(dead_air(&speech, &turns, &[0], &[], 12500, 400), vec![(3000, 3500), (4000, 9000), (12000, 12500)]);
        // keeping both people: the laugh is speech too
        assert_eq!(dead_air(&speech, &turns, &[0, 1], &[], 12500, 400), vec![(3000, 3500), (5500, 9000), (12000, 12500)]);
        // nobody known: any speech counts
        assert_eq!(dead_air(&speech, &turns, &[], &[], 12500, 400), vec![(3000, 3500), (6000, 9000), (12000, 12500)]);
        // a transcribed word said in a gap keeps WORD_HOLD_MS after it (soft speech diarization missed)
        assert_eq!(dead_air(&speech, &turns, &[0], &[7000], 12500, 400), vec![(3000, 3500), (4000, 7000), (7600, 9000), (12000, 12500)]);
    }
}
