//! One bounded native chunk. Queue/checkpoint ownership lives outside this adapter.
use crate::{
    sources,
    transcription::{self, Context, Transcript},
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::Read;
use std::{
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};
use tokio::{io::AsyncReadExt, process::Command, sync::watch};

#[derive(Clone, Debug)]
pub struct Config {
    pub ffmpeg: PathBuf,
    pub whisper: PathBuf,
    pub model: PathBuf,
    pub workspace: PathBuf,
    pub timeout: Duration,
    pub threads: u16,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Artifact {
    pub path: PathBuf,
    pub size: u64,
    pub modified_ns: String,
    pub sha256: String,
}
#[derive(Debug, Serialize)]
pub struct ResultChunk {
    pub transcript: Transcript,
    pub source: Artifact,
    pub ffmpeg: Artifact,
    pub threads: u16,
    pub engine: Artifact,
    pub model: Artifact,
    pub elapsed_ms: u128,
    pub cache_hit: bool,
    #[serde(skip)]
    pub cache_candidate: Option<crate::chunk_cache::Candidate>,
}
impl Artifact {
    pub fn read(path: &Path) -> Result<Self, String> {
        Self::read_cancellable(path, None)
    }
    fn read_cancellable(
        path: &Path,
        cancel: Option<&watch::Receiver<bool>>,
    ) -> Result<Self, String> {
        let path = path
            .canonicalize()
            .map_err(|_| "transcription_dependency_missing")?;
        let metadata = path
            .metadata()
            .map_err(|_| "transcription_dependency_unreadable")?;
        if !metadata.is_file() {
            return Err("transcription_dependency_not_file".into());
        }
        let stamp = sources::stamp(&metadata).map_err(|_| "transcription_dependency_unreadable")?;
        let mut file =
            std::fs::File::open(&path).map_err(|_| "transcription_dependency_unreadable")?;
        let mut hash = Sha256::new();
        let mut buffer = [0u8; 65536];
        loop {
            if cancel.is_some_and(|c| *c.borrow()) {
                return Err("cancelled".into());
            }
            let size = file
                .read(&mut buffer)
                .map_err(|_| "transcription_dependency_unreadable")?;
            if size == 0 {
                break;
            }
            hash.update(&buffer[..size]);
        }
        let after = file
            .metadata()
            .map_err(|_| "transcription_dependency_unreadable")?;
        if after.len() != metadata.len()
            || sources::stamp(&after).map_err(|_| "transcription_dependency_unreadable")? != stamp
        {
            return Err("transcription_dependency_changed".into());
        }
        Ok(Self {
            path,
            size: metadata.len(),
            modified_ns: stamp,
            sha256: format!("{:x}", hash.finalize()),
        })
    }
    fn verify(&self, cancel: &watch::Receiver<bool>) -> Result<(), String> {
        let current = Self::read_cancellable(&self.path, Some(cancel))?;
        if current.path != self.path
            || current.size != self.size
            || current.modified_ns != self.modified_ns
            || current.sha256 != self.sha256
        {
            return Err("transcription_dependency_changed".into());
        }
        Ok(())
    }
}
struct Scratch(PathBuf);
impl Scratch {
    fn create(parent: &Path) -> Result<Self, String> {
        std::fs::create_dir_all(parent).map_err(|_| "transcription_workspace_unavailable")?;
        let root = parent
            .canonicalize()
            .map_err(|_| "transcription_workspace_unavailable")?;
        let path = root.join(format!("asr-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&path).map_err(|_| "transcription_workspace_unavailable")?;
        Ok(Self(path))
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        if self.0.canonicalize().ok().as_ref() == Some(&self.0) {
            if let Err(error) = std::fs::remove_dir_all(&self.0) {
                eprintln!("transcription_workspace_cleanup_failed: {error}");
            }
        }
    }
}

async fn command(
    mut command: Command,
    mut cancel: watch::Receiver<bool>,
    timeout: Duration,
) -> Result<(), String> {
    if *cancel.borrow() {
        return Err("cancelled".into());
    }
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    let mut child = command
        .spawn()
        .map_err(|_| "transcription_process_start_failed")?;
    let mut errors = child.stderr.take().ok_or("transcription_stderr_missing")?;
    // Drain the entire pipe while retaining no unbounded native log in memory.
    let mut drain = tokio::spawn(async move {
        let mut buffer = [0u8; 8192];
        loop {
            if errors.read(&mut buffer).await? == 0 {
                return Ok::<_, std::io::Error>(());
            }
        }
    });
    let result = tokio::select! {
        result=child.wait()=>result.map_err(|_|"transcription_process_wait_failed".to_string()),
        _=cancel.wait_for(|requested| *requested)=>Err("cancelled".into()),
        _=tokio::time::sleep(timeout)=>Err("transcription_process_timeout".into()),
    };
    if result.is_err() {
        let _ = child.kill().await;
        let _ = child.wait().await;
    }
    // A child may have inherited the pipe; do not hang on log collection after timeout/cancel.
    let drained = tokio::time::timeout(Duration::from_secs(2), &mut drain).await;
    if drained.is_err() {
        drain.abort();
        let _ = drain.await;
    }
    let status = result?;
    if !status.success() {
        return Err("transcription_process_failed".into());
    }
    match drained {
        Ok(Ok(Ok(()))) => Ok(()),
        _ => Err("transcription_log_read_failed".into()),
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Settings {
    pub engine: Artifact,
    pub model: Artifact,
    pub ffmpeg: Artifact,
    pub threads: u16,
}
impl Settings {
    pub fn valid(&self) -> bool {
        (1..=64).contains(&self.threads)
            && [&self.engine, &self.model, &self.ffmpeg].iter().all(|v| {
                v.size > 0
                    && v.path.is_absolute()
                    && v.sha256.len() == 64
                    && v.sha256.bytes().all(|b| b.is_ascii_hexdigit())
                    && !v
                        .modified_ns
                        .strip_prefix('-')
                        .unwrap_or(&v.modified_ns)
                        .is_empty()
                    && v.modified_ns
                        .strip_prefix('-')
                        .unwrap_or(&v.modified_ns)
                        .bytes()
                        .all(|b| b.is_ascii_digit())
            })
    }

    pub fn read(config: &Config) -> Result<Self, String> {
        Ok(Self {
            engine: Artifact::read(&config.whisper)?,
            model: Artifact::read(&config.model)?,
            ffmpeg: Artifact::read(&config.ffmpeg)?,
            threads: config.threads,
        })
    }
}
pub async fn run(
    config: &Config,
    source_path: &Path,
    context: &Context,
    cancel: watch::Receiver<bool>,
) -> Result<ResultChunk, String> {
    run_checked(config, source_path, context, cancel, None).await
}
pub async fn run_checked(
    config: &Config,
    source_path: &Path,
    context: &Context,
    cancel: watch::Receiver<bool>,
    expected: Option<&Settings>,
) -> Result<ResultChunk, String> {
    run_progress(config, source_path, context, cancel, expected, None).await
}
pub async fn run_progress(
    config: &Config,
    source_path: &Path,
    context: &Context,
    cancel: watch::Receiver<bool>,
    expected: Option<&Settings>,
    progress: Option<watch::Sender<&'static str>>,
) -> Result<ResultChunk, String> {
    run_cached(
        config,
        source_path,
        context,
        cancel,
        expected,
        progress,
        None,
    )
    .await
}

pub async fn run_cached(
    config: &Config,
    source_path: &Path,
    context: &Context,
    cancel: watch::Receiver<bool>,
    expected: Option<&Settings>,
    progress: Option<watch::Sender<&'static str>>,
    cache: Option<(
        std::sync::Arc<std::sync::Mutex<crate::drafts::Store>>,
        String,
    )>,
) -> Result<ResultChunk, String> {
    let started = std::time::Instant::now();
    context.validate()?;
    if config.threads == 0
        || config.threads > 64
        || config.timeout.is_zero()
        || config.timeout > Duration::from_secs(3600)
    {
        return Err("invalid_transcription_configuration".into());
    }
    if *cancel.borrow() {
        return Err("cancelled".into());
    }
    let path = source_path.to_path_buf();
    let cfg = config.clone();
    let signal = cancel.clone();
    let (source, engine, model, ffmpeg) = tokio::task::spawn_blocking(move || {
        Ok::<_, String>((
            Artifact::read_cancellable(&path, Some(&signal))?,
            Artifact::read_cancellable(&cfg.whisper, Some(&signal))?,
            Artifact::read_cancellable(&cfg.model, Some(&signal))?,
            Artifact::read_cancellable(&cfg.ffmpeg, Some(&signal))?,
        ))
    })
    .await
    .map_err(|_| "transcription_identity_task_failed")??;
    if source.size != context.source.size || source.modified_ns != context.source.modified_ns {
        return Err("source_changed".into());
    }
    if expected.is_some_and(|v| {
        v != &Settings {
            engine: engine.clone(),
            model: model.clone(),
            ffmpeg: ffmpeg.clone(),
            threads: config.threads,
        }
    }) {
        return Err("transcription_settings_changed".into());
    }
    let key = cache_key(
        &source,
        context,
        &Settings {
            engine: engine.clone(),
            model: model.clone(),
            ffmpeg: ffmpeg.clone(),
            threads: config.threads,
        },
    )?;
    if let Some((store, job_id)) = cache {
        let lookup_key = key.clone();
        let source_sha256 = source.sha256.clone();
        let raw = tokio::task::spawn_blocking(move || {
            store
                .lock()
                .map_err(|_| "job_store_unavailable".to_string())?
                .transcription_cache(&job_id, &lookup_key, Some(&source_sha256))
                .map_err(|e| match e {
                    crate::drafts::Error::TranscriptionCacheCorrupt => {
                        "transcription_cache_corrupt".to_string()
                    }
                    crate::drafts::Error::SourceChanged => "source_changed".to_string(),
                    _ => "job_store_unavailable".to_string(),
                })
        })
        .await
        .map_err(|_| "job_store_unavailable")??;
        if let Some(raw) = raw {
            let transcript =
                transcription::parse(&raw, context).map_err(|_| "transcription_cache_corrupt")?;
            verify_all(&source, &engine, &model, &ffmpeg, cancel.clone()).await?;
            if *cancel.borrow() {
                return Err("cancelled".into());
            }
            return Ok(ResultChunk {
                transcript,
                source,
                ffmpeg,
                threads: config.threads,
                engine,
                model,
                elapsed_ms: started.elapsed().as_millis(),
                cache_hit: true,
                cache_candidate: None,
            });
        }
    }
    let scratch = Scratch::create(&config.workspace)?;
    let audio = scratch.0.join("audio.wav");
    let prefix = scratch.0.join("transcript");
    let mut extract = Command::new(&ffmpeg.path);
    extract
        .args(["-nostdin", "-v", "error", "-ss"])
        .arg(format!("{:.3}", context.start_ms as f64 / 1000.0))
        .arg("-i")
        .arg(&source.path)
        .arg("-t")
        .arg(format!(
            "{:.3}",
            (context.end_ms - context.start_ms) as f64 / 1000.0
        ))
        .args([
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-n",
        ])
        .arg(&audio);
    if let Some(p) = &progress {
        let _ = p.send("extracting");
    }
    command(extract, cancel.clone(), config.timeout).await?;
    let mut infer = Command::new(&engine.path);
    infer
        .arg("-m")
        .arg(&model.path)
        .arg("-f")
        .arg(&audio)
        .arg("-l")
        .arg(&context.language)
        .args(DECODE_ARGS)
        .args(["-ojf", "-ng", "-t"])
        .arg(config.threads.to_string())
        .arg("-of")
        .arg(&prefix);
    if let Some(p) = &progress {
        let _ = p.send("transcribing");
    }
    command(infer, cancel.clone(), config.timeout).await?;
    if *cancel.borrow() {
        return Err("cancelled".into());
    }
    verify_all(&source, &engine, &model, &ffmpeg, cancel.clone()).await?;
    let output = prefix.with_extension("json");
    let mut file = tokio::fs::File::open(&output)
        .await
        .map_err(|_| "transcription_output_missing")?;
    let mut bytes = Vec::new();
    (&mut file)
        .take(transcription::MAX_JSON + 1)
        .read_to_end(&mut bytes)
        .await
        .map_err(|_| "transcription_output_unreadable")?;
    let transcript = transcription::parse(&bytes, context)?;
    if *cancel.borrow() {
        return Err("cancelled".into());
    }
    Ok(ResultChunk {
        transcript,
        source,
        ffmpeg,
        threads: config.threads,
        engine,
        model,
        elapsed_ms: started.elapsed().as_millis(),
        cache_hit: false,
        cache_candidate: Some(crate::chunk_cache::Candidate::new(key, bytes)),
    })
}

/// Decoding options passed to whisper-cli; part of the cache key, so changing them re-transcribes.
/// `-sns` suppresses non-speech tokens: without it, large-v3 looped one Chinese sentence 8 times
/// at the end of a 75 s FLEURS clip (CER 64% -> 11%) while Thai and English stayed level.
/// `-mc 0` also broke the loop but cost Thai 2 points of CER.
pub const DECODE_ARGS: [&str; 1] = ["-sns"];

pub fn cache_key(
    source: &Artifact,
    context: &Context,
    settings: &Settings,
) -> Result<String, String> {
    let bytes=serde_json::to_vec(&serde_json::json!({"source":source,"identity":context.source,"start_ms":context.start_ms,"end_ms":context.end_ms,"language":context.language,"settings":settings,"decode":DECODE_ARGS,"output_format":"whisper.cpp-full-json-v1.9.2"})).map_err(|_|"transcription_cache_key_invalid")?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}
async fn verify_all(
    source: &Artifact,
    engine: &Artifact,
    model: &Artifact,
    ffmpeg: &Artifact,
    cancel: watch::Receiver<bool>,
) -> Result<(), String> {
    let checked = (
        source.clone(),
        engine.clone(),
        model.clone(),
        ffmpeg.clone(),
    );
    tokio::task::spawn_blocking(move || {
        checked.0.verify(&cancel).map_err(|error| {
            if error == "cancelled" {
                error
            } else {
                "source_changed".into()
            }
        })?;
        checked.1.verify(&cancel)?;
        checked.2.verify(&cancel)?;
        checked.3.verify(&cancel)
    })
    .await
    .map_err(|_| "transcription_identity_task_failed")?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cache_identity_excludes_run_and_includes_source_settings_language_and_range() {
        let source = Artifact {
            path: std::env::temp_dir().join("source.wav"),
            size: 10,
            modified_ns: "1".into(),
            sha256: "a".repeat(64),
        };
        let settings = Settings {
            engine: source.clone(),
            model: source.clone(),
            ffmpeg: source.clone(),
            threads: 1,
        };
        let mut context = Context {
            run_id: "first".into(),
            source: crate::sources::Identity {
                id: "source".into(),
                size: 10,
                modified_ns: "1".into(),
            },
            start_ms: 0,
            end_ms: 1000,
            language: "auto".into(),
        };
        let key = cache_key(&source, &context, &settings).unwrap();
        context.run_id = "second".into();
        assert_eq!(cache_key(&source, &context, &settings).unwrap(), key);
        context.language = "th".into();
        assert_ne!(cache_key(&source, &context, &settings).unwrap(), key);
        context.language = "auto".into();
        context.end_ms = 999;
        assert_ne!(cache_key(&source, &context, &settings).unwrap(), key);
        context.end_ms = 1000;
        context.source.id = "other".into();
        assert_ne!(cache_key(&source, &context, &settings).unwrap(), key);
        context.source.id = "source".into();
        let mut changed = settings.clone();
        changed.threads = 2;
        assert_ne!(cache_key(&source, &context, &changed).unwrap(), key);
        changed = settings.clone();
        changed.model.sha256 = "b".repeat(64);
        assert_ne!(cache_key(&source, &context, &changed).unwrap(), key);
        let mut changed_source = source.clone();
        changed_source.sha256 = "b".repeat(64);
        assert_ne!(
            cache_key(&changed_source, &context, &settings).unwrap(),
            key
        );
        // transcripts cached before decode options joined the key (which could hold a
        // repetition loop) must not be reused
        assert_eq!(DECODE_ARGS, ["-sns"]);
        let before=serde_json::to_vec(&serde_json::json!({"source":source,"identity":context.source,"start_ms":context.start_ms,"end_ms":context.end_ms,"language":context.language,"settings":settings,"output_format":"whisper.cpp-full-json-v1.9.2"})).unwrap();
        assert_ne!(format!("{:x}", Sha256::digest(before)), key);
    }
}
