//! Breaths and pauses under background noise (café, street), where a fixed silence gate finds nothing.
//! Port of app/video_edit.py quiet_spans: loudness per 20 ms, smoothed over 100 ms so a word's dip is never a
//! pause, compared with this clip's own noise floor (20th percentile + 2 dB).
use serde::Serialize;
use std::{path::Path, process::Stdio};
use tokio::process::Command;

pub const RATE: u32 = 16_000;
/// Silence left before and after a word when a pause is cut (capcut_edit.py WORD_PAD).
pub const WORD_PAD_MS: u64 = 100;

#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct Span {
    pub start_ms: u64,
    pub end_ms: u64,
}

/// Quiet spans of mono 16-bit samples, relative to the first sample.
pub fn quiet_spans(samples: &[i16], rate: u32, min_ms: u64) -> Vec<Span> {
    let n = (rate as usize) / 50;
    if n == 0 || samples.len() < n * 10 {
        return Vec::new();
    }
    let raw: Vec<f64> = samples
        .chunks_exact(n)
        .map(|frame| {
            let power = frame
                .iter()
                .map(|&s| (s as f64 / 32768.0).powi(2))
                .sum::<f64>()
                / n as f64;
            20.0 * (power.sqrt() + 1e-9).log10()
        })
        .collect();
    // numpy.convolve(db, ones(5)/5, mode="same"): zero outside the edges.
    let db: Vec<f64> = (0..raw.len())
        .map(|i| {
            (i.saturating_sub(2)..(i + 3).min(raw.len()))
                .map(|k| raw[k])
                .sum::<f64>()
                / 5.0
        })
        .collect();
    let mut sorted = db.clone();
    sorted.sort_by(f64::total_cmp);
    // numpy.percentile default: linear between the closest ranks.
    let rank = (sorted.len() - 1) as f64 * 0.2;
    let (low, high) = (rank.floor() as usize, rank.ceil() as usize);
    let floor = sorted[low] + (sorted[high] - sorted[low]) * (rank - low as f64) + 2.0;
    let mut spans = Vec::new();
    let mut i = 0;
    while i < db.len() {
        if db[i] < floor {
            let j = (i..db.len()).find(|&j| db[j] >= floor).unwrap_or(db.len());
            if (j - i) as u64 * 20 >= min_ms {
                spans.push(Span {
                    start_ms: i as u64 * 20,
                    end_ms: j as u64 * 20,
                });
            }
            i = j;
        } else {
            i += 1;
        }
    }
    spans
}

/// Reads `start_ms..end_ms` of a source's first audio stream through ffmpeg and finds its pauses.
pub async fn find(
    ffmpeg: &Path,
    source: &Path,
    start_ms: u64,
    end_ms: u64,
) -> Result<Vec<Span>, String> {
    if end_ms <= start_ms {
        return Err("invalid_pause_range".into());
    }
    let mut command = Command::new(ffmpeg);
    command
        .args(["-nostdin", "-v", "error", "-ss"])
        .arg(format!("{:.3}", start_ms as f64 / 1000.0))
        .arg("-i")
        .arg(source)
        .arg("-t")
        .arg(format!("{:.3}", (end_ms - start_ms) as f64 / 1000.0))
        .args([
            "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-",
        ])
        .stdin(Stdio::null())
        .kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW, matching media.rs.
    let output = command
        .output()
        .await
        .map_err(|_| "pause_audio_start_failed")?;
    if !output.status.success() {
        let reason = String::from_utf8_lossy(&output.stderr);
        return Err(format!(
            "pause_audio_failed: {}",
            reason.trim().chars().take(300).collect::<String>()
        ));
    }
    let samples: Vec<i16> = output
        .stdout
        .chunks_exact(2)
        .map(|b| i16::from_le_bytes([b[0], b[1]]))
        .collect();
    Ok(quiet_spans(&samples, RATE, 300)
        .into_iter()
        .map(|s| Span {
            start_ms: start_ms + s.start_ms,
            end_ms: start_ms + s.end_ms,
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn matches_python_reference_on_noisy_fixture() {
        // Expected values printed by the numpy implementation in app/video_edit.py on the same samples.
        let bytes = include_bytes!("../tests/fixtures/quiet_fixture.s16");
        let samples: Vec<i16> = bytes
            .chunks_exact(2)
            .map(|b| i16::from_le_bytes([b[0], b[1]]))
            .collect();
        let spans: Vec<(u64, u64)> = quiet_spans(&samples, RATE, 300)
            .iter()
            .map(|s| (s.start_ms, s.end_ms))
            .collect();
        assert_eq!(spans, [(40, 460), (2040, 2860), (4240, 4560)]);
    }
    #[test]
    fn short_audio_has_no_pauses() {
        assert!(quiet_spans(&[0; 100], RATE, 300).is_empty());
    }
}
