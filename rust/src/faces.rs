//! Where each speaker's face is, so the camera can move in on whoever is talking. Faces come from SeetaFace
//! (rustface, pure Rust, a 1.2 MB model) on small grey frames; which face belongs to which speaker comes from
//! mouth movement: whoever's lower face moves most while speech is heard is the one talking (`visual_turns`).
use serde::Serialize;
use std::path::Path;

/// Frames are read this wide (height follows the video); faces and movement are measured on them.
pub const WIDTH: u32 = 270;
/// Frames read per second for mouth movement; faces are looked for in every FACE_EVERY-th one.
pub const FPS: u32 = 4;
const FACE_EVERY: usize = 8;

#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct Face {
    /// centre and size, as fractions of the frame
    pub cx: f64,
    pub cy: f64,
    pub size: f64,
}

/// Grey frames of a whole video at FPS, WIDTH wide, through ffmpeg: (frames, width, height).
pub async fn frames(ffmpeg: &Path, source: &Path) -> Result<(Vec<Vec<u8>>, u32, u32), String> {
    let probe = |w: u32, h: u32| (w, h);
    let mut command = tokio::process::Command::new(ffmpeg);
    command
        .args(["-nostdin", "-v", "error", "-i"])
        .arg(source)
        .args(["-map", "0:v:0", "-an", "-vf"])
        .arg(format!("fps={FPS},scale={WIDTH}:-2"))
        .args(["-pix_fmt", "gray", "-f", "rawvideo", "-"])
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW, matching media.rs.
    let output = command.output().await.map_err(|_| "face_frames_start_failed")?;
    if !output.status.success() {
        let reason = String::from_utf8_lossy(&output.stderr);
        return Err(format!("face_frames_failed: {}", reason.trim().chars().take(300).collect::<String>()));
    }
    // the height is whatever keeps the shape at WIDTH wide; find it from one frame's worth of bytes
    let height = height_of(ffmpeg, source).await?;
    let (w, h) = probe(WIDTH, height);
    let size = (w * h) as usize;
    if size == 0 {
        return Err("face_frames_empty".into());
    }
    Ok((output.stdout.chunks_exact(size).map(<[u8]>::to_vec).collect(), w, h))
}

async fn height_of(ffmpeg: &Path, source: &Path) -> Result<u32, String> {
    let mut command = tokio::process::Command::new(ffmpeg);
    command
        .args(["-nostdin", "-v", "error", "-i"])
        .arg(source)
        .args(["-map", "0:v:0", "-frames:v", "1", "-vf"])
        .arg(format!("scale={WIDTH}:-2"))
        .args(["-pix_fmt", "gray", "-f", "rawvideo", "-"])
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    let output = command.output().await.map_err(|_| "face_frames_start_failed")?;
    let n = output.stdout.len() as u32;
    if n == 0 || n % WIDTH != 0 {
        return Err("face_frame_size_unknown".into());
    }
    Ok(n / WIDTH)
}

/// Faces in one grey frame.
pub fn detect(detector: &mut dyn rustface::Detector, frame: &[u8], w: u32, h: u32) -> Vec<Face> {
    let image = rustface::ImageData::new(frame, w, h);
    detector
        .detect(&image)
        .into_iter()
        .map(|f| {
            let b = f.bbox();
            Face {
                cx: (b.x() as f64 + b.width() as f64 / 2.0) / w as f64,
                cy: (b.y() as f64 + b.height() as f64 / 2.0) / h as f64,
                size: b.width() as f64 / w as f64,
            }
        })
        .collect()
}

/// The people on screen: faces found every FACE_EVERY frames, grouped by where they are. Someone found in at least
/// two looks counts (the frontal model loses a face turned to the side, so the side-on interviewer is found less).
pub fn people(model: &Path, frames: &[Vec<u8>], w: u32, h: u32) -> Result<Vec<Face>, String> {
    let mut detector = rustface::create_detector(&model.to_string_lossy()).map_err(|e| format!("face_model: {e}"))?;
    detector.set_min_face_size(20);
    detector.set_score_thresh(2.0);
    detector.set_pyramid_scale_factor(0.8);
    detector.set_slide_window_step(4, 4);
    let mut found: Vec<(Face, u32)> = Vec::new();
    for frame in frames.iter().step_by(FACE_EVERY) {
        for f in detect(detector.as_mut(), frame, w, h) {
            match found.iter_mut().find(|(p, _)| (p.cx - f.cx).abs() < 0.15 && (p.cy - f.cy).abs() < 0.15) {
                Some((p, n)) => {
                    let k = *n as f64;
                    *p = Face { cx: (p.cx * k + f.cx) / (k + 1.0), cy: (p.cy * k + f.cy) / (k + 1.0), size: (p.size * k + f.size) / (k + 1.0) };
                    *n += 1;
                }
                None => found.push((f, 1)),
            }
        }
    }
    found.retain(|(_, n)| *n >= 2);
    found.sort_by(|a, b| a.0.cx.total_cmp(&b.0.cx));
    Ok(found.into_iter().map(|(f, _)| f).collect())
}

/// How much each person's mouth (the lower half of their face box) changes from frame to frame.
pub fn mouth_motion(people: &[Face], frames: &[Vec<u8>], w: u32, h: u32) -> Vec<Vec<f64>> {
    people
        .iter()
        .map(|p| {
            let side = (p.size * w as f64) as i64;
            let (x0, y0) = ((p.cx * w as f64) as i64 - side / 2, (p.cy * h as f64) as i64);
            let (x0, x1) = (x0.max(0) as usize, ((x0 + side).min(w as i64)).max(0) as usize);
            let (y0, y1) = (y0.max(0) as usize, ((y0 + side / 2).min(h as i64)).max(0) as usize);
            let mut out = vec![0.0];
            for pair in frames.windows(2) {
                let mut sum = 0u64;
                for y in y0..y1 {
                    for x in x0..x1 {
                        let i = y * w as usize + x;
                        sum += (pair[0][i] as i64 - pair[1][i] as i64).unsigned_abs();
                    }
                }
                out.push(sum as f64 / ((x1 - x0) * (y1 - y0)).max(1) as f64);
            }
            out
        })
        .collect()
}

/// Who is talking when, by sight: during speech (`speech`, ms), the person whose mouth moves most against their
/// own usual movement, in 1 s steps, joined into turns. Diarization could not tell two women apart on IMG_6779
/// (one voice for 70 of 73 s at every threshold, 23 Oct), so the camera follows faces, not voices.
pub fn visual_turns(motion: &[Vec<f64>], speech: &[(u64, u64)]) -> Vec<(u64, u64, usize)> {
    if motion.is_empty() {
        return Vec::new();
    }
    let usual: Vec<f64> = motion.iter().map(|m| { let mut v = m.clone(); v.sort_by(f64::total_cmp); v[v.len() / 2].max(1e-6) }).collect();
    let step = FPS as usize;
    let frames = motion[0].len();
    let mut out: Vec<(u64, u64, usize)> = Vec::new();
    for s in (0..frames).step_by(step) {
        let (a, b) = (s as u64 * 1000 / FPS as u64, ((s + step).min(frames)) as u64 * 1000 / FPS as u64);
        if !speech.iter().any(|&(x, y)| x < b && a < y) {
            continue;
        }
        let who = (0..motion.len())
            .max_by(|&i, &j| {
                let score = |k: usize| motion[k][s..(s + step).min(frames)].iter().sum::<f64>() / usual[k];
                score(i).total_cmp(&score(j))
            })
            .unwrap_or(0);
        match out.last_mut() {
            Some(last) if last.2 == who && last.1 >= a => last.1 = b,
            _ => out.push((a, b, who)),
        }
    }
    out
}

pub const MODEL_URL: &str = "https://github.com/atomashpolskiy/rustface/raw/master/model/seeta_fd_frontal_v1.0.bin";
pub const MODEL_SIZE: u64 = 1_209_904;
pub const MODEL_SHA256: &str = "c4619d066ed35e84d9a8e842860b0dff567aba0cbb139881075538761db3ff5d";

#[cfg(test)]
mod tests {
    use super::*;
    /// Real-clip check, run by hand: EDITORKRAB_FACE_MODEL=<seeta .bin> EDITORKRAB_FACE_VIDEO=<video> EDITORKRAB_FFMPEG=<ffmpeg>
    #[tokio::test]
    #[ignore]
    async fn real_clip() {
        let ffmpeg = std::env::var("EDITORKRAB_FFMPEG").unwrap();
        let video = std::env::var("EDITORKRAB_FACE_VIDEO").unwrap();
        let model = std::env::var("EDITORKRAB_FACE_MODEL").unwrap();
        let t = std::time::Instant::now();
        let (frames, w, h) = frames(Path::new(&ffmpeg), Path::new(&video)).await.unwrap();
        println!("{} frames {w}x{h} in {} ms", frames.len(), t.elapsed().as_millis());
        let mut detector = rustface::create_detector(&model).unwrap();
        detector.set_min_face_size(20);
        detector.set_score_thresh(2.0);
        for (n, f) in frames.iter().enumerate().step_by(40) {
            println!("frame {n}: {:?}", detect(detector.as_mut(), f, w, h));
        }
        let t = std::time::Instant::now();
        let people = people(Path::new(&model), &frames, w, h).unwrap();
        println!("people {people:?} in {} ms", t.elapsed().as_millis());
        let motion = mouth_motion(&people, &frames, w, h);
        let turns = visual_turns(&motion, &[(0, 90000)]);
        for t in &turns { println!("seen talking {:>6} {:>6} person {}", t.0, t.1, t.2); }
    }
}
