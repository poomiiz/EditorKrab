use axum::{
    Json, Router,
    body::Bytes,
    extract::{Path, Query, State},
    http::{HeaderMap, StatusCode},
    response::{
        Html, IntoResponse,
        sse::{Event, KeepAlive, Sse},
    },
    routing::{get, post},
};
use editorkrab::drafts::{Error, Store};
use editorkrab::jobs::{BatchRequest, Job, Task};
use editorkrab::native_asr::{Config as AsrConfig, Settings as AsrSettings};
use editorkrab::sources::{PickCommand, PickKind, PickerLease, Selection, SourceError};
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::{Arc, Mutex};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc,
};
use std::{collections::HashMap, convert::Infallible, path::PathBuf, time::Duration};
use tokio::sync::watch;

mod agent;

type Shared = Arc<Mutex<Store>>;
type Asr = Arc<(AsrConfig, AsrSettings)>;
type ApiError = (StatusCode, Json<Value>);

#[derive(Clone)]
struct Runtime {
    store: Shared,
    stopping: watch::Sender<bool>,
    picker: mpsc::Sender<PickCommand>,
    picker_busy: Arc<AtomicBool>,
    assets: PathBuf,
    // swapped in place when the in-app download finishes, so no restart is needed
    asr: Arc<std::sync::RwLock<Option<Asr>>>,
    setup: Arc<AsrSetup>,
    download: Arc<Mutex<Download>>,
    gpu_download: Arc<Mutex<Download>>,
    exports: Arc<Mutex<std::collections::HashMap<String, Export>>>,
}
impl Runtime {
    fn asr(&self) -> Option<Asr> {
        self.asr.read().ok().and_then(|v| v.clone())
    }
}

/// Where the app looks for, and downloads, its speech tools.
struct AsrSetup {
    state_path: PathBuf,
    data: PathBuf,
    /// test-only override of the model to download (EDITORKRAB_DOWNLOAD_MANIFEST)
    model: Option<editorkrab::asr_download::Item>,
}
#[derive(Clone, Default, serde::Serialize)]
struct Download {
    state: &'static str,
    received: u64,
    total: u64,
    bytes_per_second: u64,
    error: Option<String>,
    #[serde(skip)]
    cancel: Option<watch::Sender<bool>>,
}

fn ffprobe_path(setup: &AsrSetup) -> PathBuf {
    match std::env::var("EDITORKRAB_FFPROBE") {
        Ok(path) => PathBuf::from(path),
        Err(_) => find_tool(&tool_roots(&setup.data), "ffmpeg", "ffprobe")
            .unwrap_or_else(|| "ffprobe".into()),
    }
}

/// Step 6 progress for one project; kept in memory, so a restart shows it as never started.
#[derive(Clone, Default, serde::Serialize)]
struct Export {
    state: &'static str,
    story: usize,
    stories: usize,
    progress: f64,
    files: Vec<String>,
    /// text roles the user ticked that the export cannot draw yet
    skipped: Vec<String>,
    error: Option<String>,
    #[serde(skip)]
    cancel: Option<watch::Sender<bool>>,
}
/// Finished clips go to the user's Videos folder (uploads live inside app data, so not "next to the source").
fn export_dir() -> Option<PathBuf> {
    if let Ok(path) = std::env::var("EDITORKRAB_EXPORT_DIR") {
        return Some(path.into());
    }
    let (home, videos) = if cfg!(windows) {
        (std::env::var("USERPROFILE").ok()?, "Videos")
    } else if cfg!(target_os = "macos") {
        (std::env::var("HOME").ok()?, "Movies")
    } else {
        (std::env::var("HOME").ok()?, "Videos")
    };
    Some(PathBuf::from(home).join(videos).join("EditorKrab"))
}
fn export_error(code: impl Into<String>) -> ApiError {
    (StatusCode::CONFLICT, Json(json!({ "error": code.into() })))
}
async fn export_status(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
) -> Json<Export> {
    let mut status = runtime
        .exports
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(&project)
        .cloned()
        .unwrap_or_default();
    if status.state.is_empty() {
        status.state = "idle";
    }
    Json(status)
}
async fn start_export(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
) -> Result<Json<Export>, ApiError> {
    let asr = runtime
        .asr()
        .ok_or_else(|| export_error("transcription_not_configured"))?;
    let id = project.clone();
    let (draft, jobs) = database(runtime.clone(), move |store| {
        Ok((
            store.read(&id)?.ok_or(Error::NotFound)?,
            store.list_jobs(&id)?,
        ))
    })
    .await?;
    let stories = editorkrab::export::plan(&draft.document, &jobs).map_err(export_error)?;
    if let Some(missing) = stories
        .iter()
        .flat_map(|s| &s.sources)
        .find(|p| !p.is_file())
    {
        return Err(export_error(format!(
            "source_missing:{}",
            missing.display()
        )));
    }
    let folder = export_dir().ok_or_else(|| export_error("output_folder_unknown"))?;
    let (cancel, signal) = watch::channel(false);
    {
        let mut all = runtime.exports.lock().unwrap_or_else(|e| e.into_inner());
        if all.get(&project).is_some_and(|s| s.state == "running") {
            return Ok(Json(all[&project].clone()));
        }
        all.insert(
            project.clone(),
            Export {
                state: "running",
                stories: stories.len(),
                cancel: Some(cancel),
                ..Export::default()
            },
        );
    }
    let ffmpeg = asr.0.ffmpeg.clone();
    let ffprobe = ffprobe_path(&runtime.setup);
    let work = PathBuf::from(format!(
        "{}.export-work",
        runtime.setup.state_path.display()
    ))
    .join(uuid::Uuid::new_v4().to_string());
    tokio::spawn(run_export(
        runtime.clone(),
        project.clone(),
        draft.document,
        stories,
        folder,
        work,
        ffmpeg,
        ffprobe,
        signal,
    ));
    Ok(export_status(State(runtime), Path(project)).await)
}
async fn cancel_export(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
) -> Json<Export> {
    if let Some(cancel) = runtime
        .exports
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(&project)
        .and_then(|s| s.cancel.as_ref())
    {
        let _ = cancel.send(true);
    }
    export_status(State(runtime), Path(project)).await
}
/// Opens the folder holding the exported clips, with the first one selected.
async fn reveal_export(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
) -> Result<StatusCode, ApiError> {
    let file = runtime
        .exports
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(&project)
        .and_then(|s| s.files.first().cloned())
        .ok_or_else(|| export_error("export_not_finished"))?;
    let opened = if cfg!(windows) {
        std::process::Command::new("explorer")
            .arg(format!("/select,{file}"))
            .spawn()
    } else {
        let folder = std::path::Path::new(&file)
            .parent()
            .map(|p| p.to_path_buf());
        std::process::Command::new(if cfg!(target_os = "macos") {
            "open"
        } else {
            "xdg-open"
        })
        .arg(folder.unwrap_or_default())
        .spawn()
    };
    opened.map_err(|e| export_error(format!("reveal_failed:{e}")))?;
    Ok(StatusCode::NO_CONTENT)
}
#[allow(clippy::too_many_arguments)]
async fn run_export(
    runtime: Runtime,
    project: String,
    document: Value,
    stories: Vec<editorkrab::export::Story>,
    folder: PathBuf,
    work: PathBuf,
    ffmpeg: PathBuf,
    ffprobe: PathBuf,
    signal: watch::Receiver<bool>,
) {
    let update = |f: &(dyn Fn(&mut Export) + Sync)| {
        let mut all = runtime.exports.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(status) = all.get_mut(&project) {
            f(status);
        }
    };
    let title = document["title"].as_str().unwrap_or("").to_owned();
    let mut outcome: Result<(), String> = async {
        tokio::fs::create_dir_all(&folder)
            .await
            .map_err(|e| format!("output_folder:{e}"))?;
        for (n, story) in stories.iter().enumerate() {
            update(&|s| {
                s.story = n + 1;
                s.progress = n as f64 / stories.len() as f64;
            });
            let (ass, skipped) = editorkrab::export::subtitles(story, &document);
            update(&|s| s.skipped = skipped.clone());
            let dir = work.join(n.to_string());
            tokio::fs::create_dir_all(&dir)
                .await
                .map_err(|e| e.to_string())?;
            tokio::fs::write(dir.join("subs.ass"), ass)
                .await
                .map_err(|e| e.to_string())?;
            for mode in editorkrab::export::blend_modes(&document) {
                let (layer, _) = editorkrab::export::subtitles_in(story, &document, &mode);
                tokio::fs::write(dir.join(format!("subs_{mode}.ass")), layer)
                    .await
                    .map_err(|e| e.to_string())?;
            }
            let partial = dir.join("out.mp4");
            let total = story.duration_ms().max(1);
            let base = n as f64 / stories.len() as f64;
            let share = 1.0 / stories.len() as f64;
            let progress = |done_ms: u64| {
                let fraction = (done_ms as f64 / total as f64).min(1.0);
                update(&|s| s.progress = base + share * fraction);
            };
            run_ffmpeg(
                &ffmpeg,
                &dir,
                &editorkrab::export::ffmpeg_args(story, &document, &partial),
                signal.clone(),
                progress,
            )
            .await?;
            verify_export(&ffprobe, &partial, story.duration_ms(), story.pieces.len(), editorkrab::export::frame(&document)).await?;
            let name = editorkrab::export::story_name(&title, story, n);
            let target = editorkrab::export::unused_path(&folder, &name);
            // same drive: rename; otherwise copy, then the work folder is cleaned below
            if tokio::fs::rename(&partial, &target).await.is_err() {
                tokio::fs::copy(&partial, &target)
                    .await
                    .map_err(|e| format!("output_write:{e}"))?;
            }
            let shown = target.display().to_string();
            update(&|s| s.files.push(shown.clone()));
        }
        Ok(())
    }
    .await;
    let _ = tokio::fs::remove_dir_all(&work).await;
    if outcome.is_ok() && *signal.borrow() {
        outcome = Err("cancelled".into());
    }
    update(&|s| {
        s.cancel = None;
        match &outcome {
            Ok(()) => {
                s.state = "done";
                s.progress = 1.0;
            }
            Err(e) if e == "cancelled" => s.state = "cancelled",
            Err(e) => {
                s.state = "failed";
                s.error = Some(e.clone());
            }
        }
    });
}
async fn run_ffmpeg(
    ffmpeg: &std::path::Path,
    dir: &std::path::Path,
    args: &[String],
    mut cancel: watch::Receiver<bool>,
    progress: impl Fn(u64),
) -> Result<(), String> {
    use tokio::io::{AsyncBufReadExt, AsyncReadExt};
    let mut command = tokio::process::Command::new(ffmpeg);
    command
        .args(args)
        .current_dir(dir)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(0x0800_0000);
    let mut child = command.spawn().map_err(|e| format!("ffmpeg_start:{e}"))?;
    let mut lines = tokio::io::BufReader::new(child.stdout.take().ok_or("ffmpeg_stdout")?).lines();
    let mut stderr = child.stderr.take().ok_or("ffmpeg_stderr")?;
    let errors = tokio::spawn(async move {
        let mut text = String::new();
        let _ = stderr.read_to_string(&mut text).await;
        text
    });
    loop {
        tokio::select! {
            line = lines.next_line() => match line {
                Ok(Some(line)) => {
                    if let Some(us) = line.strip_prefix("out_time_us=").and_then(|v| v.parse::<u64>().ok()) {
                        progress(us / 1000);
                    }
                }
                _ => break,
            },
            _ = async { drop(cancel.wait_for(|stop| *stop).await) } => {
                let _ = child.kill().await;
                return Err("cancelled".into());
            }
        }
    }
    let status = child.wait().await.map_err(|e| e.to_string())?;
    if !status.success() {
        let text = errors.await.unwrap_or_default();
        let last = text
            .lines()
            .rev()
            .find(|l| !l.trim().is_empty())
            .unwrap_or("")
            .trim();
        return Err(format!("ffmpeg_failed:{last}"));
    }
    Ok(())
}
/// Step 6 "แก้ต่อใน CapCut": one CapCut project per story, written where CapCut lists projects.
async fn capcut_export(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let id = project.clone();
    let draft = database(runtime.clone(), move |store| {
        store.read(&id)?.ok_or(Error::NotFound)
    })
    .await?;
    let id = project.clone();
    let jobs = database(runtime.clone(), move |store| store.list_jobs(&id)).await?;
    let stories = editorkrab::export::plan(&draft.document, &jobs).map_err(export_error)?;
    let mut media = std::collections::HashMap::new();
    let ffprobe = ffprobe_path(&runtime.setup);
    for source in stories.iter().flat_map(|s| &s.sources) {
        if !source.is_file() {
            return Err(export_error(format!("source_missing:{}", source.display())));
        }
        if !media.contains_key(source) {
            let info = probe_media(&ffprobe, source).await.map_err(export_error)?;
            media.insert(source.clone(), info);
        }
    }
    let root = editorkrab::capcut::drafts_root().ok_or_else(|| export_error("capcut_not_found"))?;
    let document = draft.document;
    let made = tokio::task::spawn_blocking(move || -> Result<Value, String> {
        let fonts = editorkrab::capcut::installed_fonts(&editorkrab::capcut::font_folders());
        let lookup = |family: &str| fonts.get(&family.to_lowercase()).cloned();
        let now_us = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_micros() as u64);
        let title = document["title"].as_str().unwrap_or("").to_owned();
        let (mut folders, mut missing) = (Vec::new(), Vec::<String>::new());
        let mut skipped = Vec::new();
        for (n, story) in stories.iter().enumerate() {
            let folder = editorkrab::capcut::unused_folder(
                &root,
                &editorkrab::export::story_name(&title, story, n),
            );
            let info: Vec<_> = story.sources.iter().map(|s| media[s].clone()).collect();
            let made = editorkrab::capcut::build(
                story, &document, &info, &lookup, &root, &folder, now_us,
            )?;
            editorkrab::capcut::write(&folder, &made)?;
            for font in made.missing_fonts {
                if !missing.contains(&font) {
                    missing.push(font);
                }
            }
            skipped = editorkrab::export::speech_lines(story, &document).1;
            folders.push(folder.display().to_string());
        }
        Ok(json!({"folders": folders, "missing_fonts": missing, "skipped": skipped}))
    })
    .await
    .map_err(|e| export_error(format!("capcut_failed:{e}")))?
    .map_err(export_error)?;
    Ok(Json(made))
}
/// Length, picture size and whether there is sound, for a CapCut footage material.
async fn probe_media(
    ffprobe: &std::path::Path,
    file: &std::path::Path,
) -> Result<editorkrab::capcut::Media, String> {
    let mut command = tokio::process::Command::new(ffprobe);
    command
        .args([
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
        ])
        .arg(file);
    #[cfg(windows)]
    command.creation_flags(0x0800_0000);
    let out = command
        .output()
        .await
        .map_err(|e| format!("ffprobe_start:{e}"))?;
    let info: Value = serde_json::from_slice(&out.stdout).map_err(|_| "source_unreadable")?;
    let streams = info["streams"].as_array().cloned().unwrap_or_default();
    let video = streams
        .iter()
        .find(|s| s["codec_type"] == "video")
        .ok_or("source_unreadable")?;
    let duration_ms = info["format"]["duration"]
        .as_str()
        .and_then(|d| d.parse::<f64>().ok())
        .filter(|d| d.is_finite() && *d > 0.0)
        .ok_or("source_unreadable")?;
    Ok(editorkrab::capcut::Media {
        duration_ms: (duration_ms * 1000.0).round() as u64,
        width: video["width"].as_u64().ok_or("source_unreadable")?,
        height: video["height"].as_u64().ok_or("source_unreadable")?,
        has_audio: streams.iter().any(|s| s["codec_type"] == "audio"),
    })
}
/// A clip is published only when ffprobe reads one video and one audio stream of the planned length.
/// Each piece ends on a source frame and concat pads its audio to match, so every piece may run up to one frame
/// long (42 ms at 24 fps): 81 subtitle lines of a 59.94 fps clip came out 0.65 s longer than planned.
async fn verify_export(
    ffprobe: &std::path::Path,
    file: &std::path::Path,
    expected_ms: u64,
    pieces: usize,
    frame: (u32, u32),
) -> Result<(), String> {
    let mut command = tokio::process::Command::new(ffprobe);
    command
        .args([
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
        ])
        .arg(file);
    #[cfg(windows)]
    command.creation_flags(0x0800_0000);
    let out = command
        .output()
        .await
        .map_err(|e| format!("ffprobe_start:{e}"))?;
    let info: Value =
        serde_json::from_slice(&out.stdout).map_err(|_| "export_unreadable".to_string())?;
    let streams = info["streams"].as_array().cloned().unwrap_or_default();
    let video = streams.iter().find(|s| s["codec_type"] == "video");
    let has_audio = streams.iter().any(|s| s["codec_type"] == "audio");
    let size_ok = video.is_some_and(|v| {
        v["width"] == frame.0 && v["height"] == frame.1
    });
    let duration_ms = info["format"]["duration"]
        .as_str()
        .and_then(|d| d.parse::<f64>().ok())
        .map(|d| (d * 1000.0) as u64)
        .unwrap_or(0);
    if !size_ok || !has_audio || duration_ms.abs_diff(expected_ms) > 500 + 42 * pieces as u64 {
        return Err(format!(
            "export_check_failed:video={size_ok},audio={has_audio},duration_ms={duration_ms},expected_ms={expected_ms}"
        ));
    }
    Ok(())
}

async fn begin_upload(
    State(runtime): State<Runtime>,
    Json(body): Json<editorkrab::uploads::Begin>,
) -> Result<Json<editorkrab::uploads::Progress>, ApiError> {
    preload_asr(&runtime);
    let assets = runtime.assets.clone();
    database(runtime, move |store| store.begin_upload(&assets, &body))
        .await
        .map(Json)
}
#[derive(Deserialize)]
struct ChunkQuery {
    offset: u64,
}
async fn upload_chunk(
    State(runtime): State<Runtime>,
    Path(id): Path<String>,
    Query(query): Query<ChunkQuery>,
    body: Bytes,
) -> Result<Json<editorkrab::uploads::Progress>, ApiError> {
    let assets = runtime.assets.clone();
    database(runtime, move |store| {
        store.upload_chunk(&assets, &id, query.offset, &body)
    })
    .await
    .map(Json)
}
async fn finish_upload(
    State(runtime): State<Runtime>,
    Path(id): Path<String>,
) -> Result<Json<editorkrab::sources::Source>, ApiError> {
    let assets = runtime.assets.clone();
    database(runtime, move |store| store.finish_upload(&assets, &id))
        .await
        .map(Json)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PickBody {
    kind: PickKind,
}

fn source_failure(error: SourceError) -> ApiError {
    let (status, code) = match error {
        SourceError::Limit => (StatusCode::PAYLOAD_TOO_LARGE, "source_limit_exceeded"),
        SourceError::Path => (StatusCode::BAD_REQUEST, "source_path_not_unicode"),
        SourceError::Io(_) => (StatusCode::UNPROCESSABLE_ENTITY, "source_read_failed"),
    };
    (status, Json(json!({"error":code})))
}

async fn pick_sources(
    State(runtime): State<Runtime>,
    Json(body): Json<PickBody>,
) -> Result<Json<Selection>, ApiError> {
    if runtime
        .picker_busy
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return Err((StatusCode::CONFLICT, Json(json!({"error":"picker_busy"}))));
    }
    let lease = PickerLease(runtime.picker_busy.clone());
    preload_asr(&runtime);
    let (answer, receive) = tokio::sync::oneshot::channel();
    runtime
        .picker
        .send(PickCommand {
            kind: body.kind,
            answer,
            lease,
        })
        .map_err(|_| failure(Error::Unavailable))?;
    let paths = receive.await.map_err(|_| failure(Error::Unavailable))?;
    if paths.is_empty() {
        return Ok(Json(Selection {
            files: vec![],
            skipped: 0,
            cancelled: true,
        }));
    }
    tokio::task::spawn_blocking(move || editorkrab::sources::collect(paths))
        .await
        .map_err(|_| failure(Error::Unavailable))?
        .map(Json)
        .map_err(source_failure)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LocateBody {
    files: Vec<editorkrab::sources::Dropped>,
}

/// A dropped video carries no location, so look for it where videos usually are: the folders
/// of videos already used, the person's own folders, and the top of each drive.
async fn locate_sources(
    State(runtime): State<Runtime>,
    Json(body): Json<LocateBody>,
) -> Result<Json<Value>, ApiError> {
    if body.files.is_empty() || body.files.len() > 1000 {
        return Err(failure(Error::InvalidInput));
    }
    let used = database(runtime, |store| store.source_folders()).await?;
    let found = tokio::task::spawn_blocking(move || {
        let mut roots: Vec<PathBuf> = used.into_iter().map(PathBuf::from).collect();
        let near = editorkrab::sources::locate(&body.files, &roots, 1, 2_000);
        if near.iter().all(Option::is_some) {
            return near;
        }
        if let Some(home) = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")) {
            let home = PathBuf::from(home);
            for folder in ["Desktop", "Downloads", "Videos", "Movies", "Documents", "Pictures"] {
                roots.push(home.join(folder));
            }
        }
        for drive in b'C'..=b'Z' {
            let root = PathBuf::from(format!("{}:\\", drive as char));
            if root.exists() {
                roots.push(root);
            }
        }
        if cfg!(not(windows)) {
            roots.push(PathBuf::from("/Volumes"));
        }
        editorkrab::sources::locate(&body.files, &roots, 5, 40_000)
    })
    .await
    .map_err(|_| failure(Error::Unavailable))?;
    Ok(Json(json!({ "files": found })))
}

async fn database<T: Send + 'static>(
    runtime: Runtime,
    operation: impl FnOnce(&mut Store) -> Result<T, Error> + Send + 'static,
) -> Result<T, ApiError> {
    tokio::task::spawn_blocking(move || {
        let mut store = runtime.store.lock().map_err(|_| Error::Unavailable)?;
        operation(&mut store)
    })
    .await
    .map_err(|_| failure(Error::Unavailable))?
    .map_err(failure)
}

#[derive(Deserialize)]
struct Save {
    operation_id: String,
    expected_revision: i64,
    document: Value,
}

fn failure(error: Error) -> (StatusCode, Json<Value>) {
    let (status, message) = match error {
        Error::Conflict { expected, actual } => {
            return (
                StatusCode::CONFLICT,
                Json(json!({"error":"revision_conflict","expected":expected,"actual":actual})),
            );
        }
        Error::OperationConflict => (StatusCode::CONFLICT, "operation_conflict"),
        Error::UploadConflict => (StatusCode::CONFLICT, "upload_bytes_conflict"),
        Error::UploadIncomplete => (StatusCode::CONFLICT, "upload_incomplete"),
        Error::UploadCorrupt => (StatusCode::CONFLICT, "upload_checkpoint_invalid"),
        Error::Io(_) => (StatusCode::INSUFFICIENT_STORAGE, "source_storage_failed"),
        Error::InvalidInput | Error::Json(_) => (StatusCode::BAD_REQUEST, "invalid_input"),
        Error::Database(_) => (StatusCode::INTERNAL_SERVER_ERROR, "draft_storage_failed"),
        Error::NotFound => (StatusCode::NOT_FOUND, "not_found"),
        Error::JobState => (StatusCode::CONFLICT, "invalid_job_state"),
        Error::SourceChanged => (StatusCode::CONFLICT, "source_changed"),
        Error::TranscriptionLimit => (
            StatusCode::UNPROCESSABLE_ENTITY,
            "transcription_chunk_limit_exceeded",
        ),
        Error::TranscriptionCacheCorrupt => (
            StatusCode::INTERNAL_SERVER_ERROR,
            "transcription_cache_corrupt",
        ),
        Error::Unavailable => (
            StatusCode::SERVICE_UNAVAILABLE,
            "storage_worker_unavailable",
        ),
    };
    (status, Json(json!({"error":message})))
}

async fn read(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let draft = database(runtime, move |store| {
        store.read(&project)?.ok_or(Error::NotFound)
    })
    .await?;
    Ok(Json(json!(draft)))
}

async fn list_drafts(State(runtime): State<Runtime>) -> Result<Json<Value>, ApiError> {
    let projects = database(runtime, |store| store.list()).await?;
    Ok(Json(json!(projects)))
}

async fn hide_draft(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
) -> Result<Json<Value>, ApiError> {
    database(runtime, move |store| store.hide(&project, true)).await?;
    Ok(Json(json!({"hidden":true})))
}

async fn restore_draft(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
) -> Result<Json<Value>, ApiError> {
    database(runtime, move |store| store.hide(&project, false)).await?;
    Ok(Json(json!({"hidden":false})))
}

async fn save(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
    Json(body): Json<Save>,
) -> Result<Json<Value>, ApiError> {
    let id = project.clone();
    let revision = database(runtime, move |store| {
        store.save(
            &id,
            &body.operation_id,
            body.expected_revision,
            &body.document,
        )
    })
    .await?;
    Ok(Json(json!({"project_id":project,"revision":revision})))
}

async fn create_jobs(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
    Json(body): Json<BatchRequest>,
) -> Result<Json<Vec<Job>>, ApiError> {
    for item in &body.items {
        if let Task::Transcribe { settings, .. } = &item.task {
            let configured = runtime.asr().ok_or((
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({"error":"transcription_not_configured"})),
            ))?;
            if settings != &configured.1 {
                return Err((
                    StatusCode::CONFLICT,
                    Json(json!({"error":"transcription_settings_changed"})),
                ));
            }
        }
    }
    database(runtime, move |store| store.create_batch(&project, &body))
        .await
        .map(Json)
}
async fn copy_draft(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
    Json(body): Json<editorkrab::draft_copy::CopyRequest>,
) -> Result<Json<editorkrab::drafts::Draft>, ApiError> {
    database(runtime, move |store| store.copy_draft(&project, &body))
        .await
        .map(Json)
}
async fn start_transcription(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
    Json(body): Json<editorkrab::transcription_jobs::StartRequest>,
) -> Result<Json<Vec<Job>>, ApiError> {
    let configured = runtime.asr().ok_or((
        StatusCode::UNPROCESSABLE_ENTITY,
        Json(json!({"error":"transcription_not_configured"})),
    ))?;
    if body.settings != configured.1 {
        return Err((
            StatusCode::CONFLICT,
            Json(json!({"error":"transcription_settings_changed"})),
        ));
    }
    let id = project.clone();
    let preflight = body.clone();
    let path = database(runtime.clone(), move |store| {
        store.transcription_source_path(&id, &preflight)
    })
    .await?;
    let expected = body.settings.clone();
    let chunk_ms = if configured.0.ct2.is_some() {
        editorkrab::transcription_jobs::CT2_CHUNK_MS
    } else {
        editorkrab::transcription_jobs::CHUNK_MS
    };
    let source = tokio::task::spawn_blocking(move || {
        if AsrSettings::read(&configured.0)? != expected {
            return Err("transcription_settings_changed".to_string());
        }
        editorkrab::native_asr::Artifact::read(&path)
    })
    .await
    .map_err(|_| failure(Error::Unavailable))?
    .map_err(|error| (StatusCode::CONFLICT, Json(json!({"error":error}))))?;
    database(runtime, move |store| {
        store.start_transcription_checked(&project, &body, Some(&source), chunk_ms)
    })
    .await
    .map(Json)
}
/// Corrections learned from hand fixes; shared with the old ClipKit `corrections.json` when pointed at it.
fn corrections_path() -> Result<PathBuf, ApiError> {
    if let Ok(path) = std::env::var("EDITORKRAB_CORRECTIONS") {
        return Ok(PathBuf::from(path));
    }
    let state = std::env::var("EDITORKRAB_RUST_STATE").map_err(|_| failure(Error::Unavailable))?;
    Ok(PathBuf::from(state).with_file_name("corrections.json"))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LearnBody {
    old: String,
    new: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FixBody {
    lines: Vec<String>,
}
fn spelling_failure(error: String) -> ApiError {
    (
        StatusCode::UNPROCESSABLE_ENTITY,
        Json(json!({"error":error})),
    )
}
async fn spelling_learn(Json(body): Json<LearnBody>) -> Result<Json<Value>, ApiError> {
    if body.old.chars().count() > 2000 || body.new.chars().count() > 2000 {
        return Err(failure(Error::InvalidInput));
    }
    let path = corrections_path()?;
    // One learner at a time: learn() reads, merges and rewrites the whole file.
    static LEARNING: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let pairs = tokio::task::spawn_blocking(move || {
        let _turn = LEARNING
            .lock()
            .map_err(|_| "corrections_lock_poisoned".to_string())?;
        editorkrab::spelling::learn(&path, &body.old, &body.new)
    })
    .await
    .map_err(|_| failure(Error::Unavailable))?
    .map_err(spelling_failure)?;
    Ok(Json(json!({"learned":pairs})))
}
async fn spelling_fix(Json(body): Json<FixBody>) -> Result<Json<Value>, ApiError> {
    if body.lines.len() > 10_000 || body.lines.iter().any(|l| l.chars().count() > 2000) {
        return Err(failure(Error::InvalidInput));
    }
    let path = corrections_path()?;
    let lines = tokio::task::spawn_blocking(move || {
        let known = editorkrab::spelling::load(&path)?;
        body.lines
            .iter()
            .map(|line| editorkrab::spelling::fix(line, &known))
            .collect::<Result<Vec<_>, _>>()
    })
    .await
    .map_err(|_| failure(Error::Unavailable))?
    .map_err(spelling_failure)?;
    Ok(Json(json!({"lines":lines})))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TextQcBody {
    items: Vec<editorkrab::text_qc::Item>,
}
/// Proofreads the clip's on-screen texts (headline, super, subtitle) and reports timed findings.
async fn text_qc(Json(body): Json<TextQcBody>) -> Result<Json<Value>, ApiError> {
    let findings = tokio::task::spawn_blocking(move || editorkrab::text_qc::check(&body.items))
        .await
        .map_err(|_| failure(Error::Unavailable))?
        .map_err(spelling_failure)?;
    Ok(Json(json!({"findings":findings})))
}
/// A project's video on disk, checked to be the one the draft names (same size and time stamp).
async fn source_path(runtime: &Runtime, project: String, source_id: String) -> Result<PathBuf, ApiError> {
    let store = runtime.store.clone();
    tokio::task::spawn_blocking(move || -> Result<PathBuf, ApiError> {
        let store = store.lock().map_err(|_| failure(Error::Unavailable))?;
        let draft = store.read(&project).map_err(failure)?.ok_or((
            StatusCode::NOT_FOUND,
            Json(json!({"error":"source_not_found"})),
        ))?;
        let file = draft.document["files"]
            .as_array()
            .and_then(|files| {
                files
                    .iter()
                    .find(|f| f["source_id"].as_str() == Some(&source_id))
            })
            .ok_or((
                StatusCode::NOT_FOUND,
                Json(json!({"error":"source_not_found"})),
            ))?;
        let path = PathBuf::from(file["path"].as_str().ok_or(failure(Error::InvalidInput))?);
        if !path.is_absolute() {
            return Err(failure(Error::InvalidInput));
        }
        let metadata = std::fs::metadata(&path).map_err(|_| {
            (
                StatusCode::NOT_FOUND,
                Json(json!({"error":"source_not_found"})),
            )
        })?;
        let stamp = editorkrab::sources::stamp(&metadata).map_err(|e| failure(Error::Io(e)))?;
        if Some(metadata.len()) != file["size"].as_u64()
            || Some(stamp.as_str()) != file["modified_ns"].as_str()
        {
            return Err((
                StatusCode::CONFLICT,
                Json(json!({"error":"source_changed"})),
            ));
        }
        Ok(path)
    })
    .await
    .map_err(|_| failure(Error::Unavailable))?
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct VoiceBody {
    /// start times of the transcribed words in this video (ms): they name the main speaker and are never cut
    words: Vec<u64>,
    min_ms: u64,
    /// speakers whose talk is kept; the main speaker when not given
    #[serde(default)]
    keep: Option<Vec<i32>>,
}
fn voice_models(setup: &AsrSetup) -> editorkrab::voice::Models {
    tool_roots(&setup.data)
        .iter()
        .map(|root| editorkrab::voice::Models::at(root))
        .find(|m| m.ready())
        .unwrap_or_else(|| editorkrab::voice::Models::at(&setup.data))
}
/// Whether the speech and speaker models are here, and how much is left to download.
async fn voice_status(State(runtime): State<Runtime>) -> Json<Value> {
    let ready = voice_models(&runtime.setup).ready();
    let left: u64 = if ready { 0 } else { editorkrab::voice::plan(&runtime.setup.data).iter().map(|i| i.size).sum() };
    Json(json!({"ready": ready, "download_bytes": left}))
}
/// Downloads the three small voice models (about 33 MB), each checked against its pinned SHA256.
async fn voice_download(State(runtime): State<Runtime>) -> Result<Json<Value>, ApiError> {
    if !voice_models(&runtime.setup).ready() {
        let curl = if cfg!(windows) {
            PathBuf::from(std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into())).join("System32").join("curl.exe")
        } else {
            PathBuf::from("curl")
        };
        let (_keep, signal) = watch::channel(false);
        for item in editorkrab::voice::plan(&runtime.setup.data) {
            editorkrab::asr_download::fetch(&item, &curl, signal.clone())
                .await
                .map_err(|e| (StatusCode::BAD_GATEWAY, Json(json!({"error": format!("voice_download_failed: {e}")}))))?;
        }
    }
    Ok(voice_status(State(runtime)).await)
}
/// Who speaks when in one video, and its dead air: speech by Silero VAD, speakers by diarization, the main
/// speaker by the transcribed words. Diarization takes about a fifth of the video's length on CPU.
async fn source_voice(
    State(runtime): State<Runtime>,
    Path((project, source_id)): Path<(String, String)>,
    Json(body): Json<VoiceBody>,
) -> Result<Json<Value>, ApiError> {
    let models = voice_models(&runtime.setup);
    if !models.ready() {
        return Err((StatusCode::CONFLICT, Json(json!({"error":"voice_models_missing"}))));
    }
    let ffmpeg = runtime
        .asr()
        .map(|v| v.0.ffmpeg.clone())
        .or_else(|| find_asr_tools(&runtime.setup.data).ffmpeg)
        .ok_or((StatusCode::SERVICE_UNAVAILABLE, Json(json!({"error":"ffmpeg_missing"}))))?;
    let path = source_path(&runtime, project, source_id).await?;
    let samples = editorkrab::voice::samples(&ffmpeg, &path)
        .await
        .map_err(|e| (StatusCode::UNPROCESSABLE_ENTITY, Json(json!({"error":e}))))?;
    let (speech, turns) = tokio::task::spawn_blocking(move || -> Result<_, String> {
        Ok((editorkrab::voice::speech(&models.vad, &samples)?, editorkrab::voice::speakers(&models, &samples)?, samples.len()))
    })
    .await
    .map_err(|_| failure(Error::Unavailable))?
    .map(|(speech, turns, n)| ((speech, n), turns))
    .map_err(|e| (StatusCode::UNPROCESSABLE_ENTITY, Json(json!({"error":e}))))?;
    let (speech, n) = speech;
    let total_ms = n as u64 * 1000 / editorkrab::voice::RATE as u64;
    let main = editorkrab::voice::main_speaker(&turns, &body.words);
    let keep = body.keep.unwrap_or_else(|| main.into_iter().collect());
    let dead = editorkrab::voice::dead_air(&speech, &turns, &keep, &body.words, total_ms, body.min_ms);
    Ok(Json(json!({"speech": speech, "turns": turns, "main": main, "dead": dead, "total_ms": total_ms})))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DeadAirBody {
    speech: Vec<(u64, u64)>,
    turns: Vec<editorkrab::voice::Turn>,
    words: Vec<u64>,
    total_ms: u64,
    min_ms: u64,
    #[serde(default)]
    keep: Option<Vec<i32>>,
}
/// Dead air again from a video's stored speech and speakers, for a new shortest pause; no audio is read.
async fn dead_air(Json(body): Json<DeadAirBody>) -> Json<Value> {
    let main = editorkrab::voice::main_speaker(&body.turns, &body.words);
    let keep = body.keep.unwrap_or_else(|| main.into_iter().collect());
    let dead = editorkrab::voice::dead_air(&body.speech, &body.turns, &keep, &body.words, body.total_ms, body.min_ms);
    Json(json!({"main": main, "dead": dead}))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PauseQuery {
    start_ms: u64,
    end_ms: u64,
}
/// Quiet spans of one source range (the old `quiet_spans`), plus the pad kept around words.
async fn source_pauses(
    State(runtime): State<Runtime>,
    Path((project, source_id)): Path<(String, String)>,
    Query(query): Query<PauseQuery>,
) -> Result<Json<Value>, ApiError> {
    if query.end_ms <= query.start_ms || query.end_ms - query.start_ms > 3_600_000 {
        return Err(failure(Error::InvalidInput));
    }
    let ffmpeg = runtime.asr().map(|v| v.0.ffmpeg.clone()).ok_or_else(|| {
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"transcription_not_configured"})),
        )
    })?;
    let path = source_path(&runtime, project, source_id).await?;
    let spans = editorkrab::pauses::find(&ffmpeg, &path, query.start_ms, query.end_ms)
        .await
        .map_err(|e| (StatusCode::UNPROCESSABLE_ENTITY, Json(json!({"error":e}))))?;
    Ok(Json(
        json!({"pauses":spans,"word_pad_ms":editorkrab::pauses::WORD_PAD_MS}),
    ))
}
async fn source_audio(
    State(runtime): State<Runtime>,
    Path((project, source_id)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<axum::response::Response, editorkrab::transcript_audio::AudioError> {
    editorkrab::transcript_audio::serve(runtime.store.clone(), project, source_id, headers).await
}
async fn source_preview(
    State(runtime): State<Runtime>,
    Path((project, source_id)): Path<(String, String)>,
    headers: HeaderMap,
) -> Result<axum::response::Response, editorkrab::transcript_audio::AudioError> {
    let ffmpeg = runtime
        .asr()
        .map(|v| v.0.ffmpeg.clone())
        .or_else(|| find_asr_tools(&runtime.setup.data).ffmpeg)
        .ok_or(editorkrab::transcript_audio::AudioError::Unavailable)?;
    let cache = PathBuf::from(format!("{}.previews", runtime.setup.state_path.display()));
    editorkrab::transcript_audio::serve_preview(
        runtime.store.clone(),
        project,
        source_id,
        headers,
        ffmpeg,
        cache,
    )
    .await
}
async fn list_jobs(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
) -> Result<impl IntoResponse, ApiError> {
    let (jobs, cursor) = database(runtime, move |store| {
        Ok((
            store.list_jobs(&project)?,
            store.latest_job_event(&project)?,
        ))
    })
    .await?;
    Ok(([("x-job-cursor", cursor.to_string())], Json(jobs)))
}
async fn cancel_job(
    State(runtime): State<Runtime>,
    Path(id): Path<String>,
) -> Result<Json<Job>, ApiError> {
    database(runtime, move |store| store.cancel_job(&id))
        .await
        .map(Json)
}
async fn retry_job(
    State(runtime): State<Runtime>,
    Path(id): Path<String>,
) -> Result<Json<Job>, ApiError> {
    database(runtime, move |store| store.retry_job(&id))
        .await
        .map(Json)
}
/// Transcribes a finished transcription job's piece again with today's aligned word clocks and
/// returns only the timed words; the job, its segments and everything keyed to them stay as they
/// are. The step 4 "re-time words" action maps these onto the existing lines (word_times).
async fn retime_job(
    State(runtime): State<Runtime>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let job = database(runtime.clone(), move |store| store.job(&id)).await?;
    let Task::Transcribe { path, context, .. } = &job.task else {
        return Err((StatusCode::UNPROCESSABLE_ENTITY, Json(json!({"error":"not_a_transcription"}))));
    };
    let asr = runtime.asr().ok_or((
        StatusCode::UNPROCESSABLE_ENTITY,
        Json(json!({"error":"transcription_not_configured"})),
    ))?;
    let mut context = context.clone();
    context.run_id = format!("retime-{}", context.run_id);
    let (_cancel, signal) = watch::channel(false);
    // whisper.cpp with DTW only: the CT2 sidecar also starts a word said after a pause at the pause
    // (on the 18-36 s test, "อะไร" at 20.6 s while the audio is quiet from 21.1 to 22.8 s and the
    // word is said at 22.9 s; DTW put it at 23.0 s)
    let mut config = asr.0.clone();
    config.ct2 = None;
    let chunk = editorkrab::native_asr::run(&config, std::path::Path::new(path), &context, signal)
        .await
        .map_err(|error| (StatusCode::CONFLICT, Json(json!({"error":error}))))?;
    let words: Vec<Value> = chunk
        .transcript
        .segments
        .iter()
        .flat_map(|s| s.words.iter())
        .map(|w| json!({"text":w.text,"start_ms":w.start_ms,"end_ms":w.end_ms}))
        .collect();
    Ok(Json(json!({"start_ms":context.start_ms,"end_ms":context.end_ms,"words":words})))
}

#[derive(Deserialize)]
struct Cursor {
    after: Option<i64>,
}

async fn events(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
    Query(query): Query<Cursor>,
    headers: HeaderMap,
) -> Result<Sse<impl futures_core::Stream<Item = Result<Event, Infallible>>>, ApiError> {
    let mut cursor = match headers.get("last-event-id") {
        Some(value) => value
            .to_str()
            .ok()
            .and_then(|s| s.parse::<i64>().ok())
            .ok_or_else(|| failure(Error::InvalidInput))?,
        None => query.after.unwrap_or(0),
    };
    if cursor < 0 {
        return Err(failure(Error::InvalidInput));
    }
    let mut stopping = runtime.stopping.subscribe();
    let stream = async_stream::stream! {
        let mut tick=tokio::time::interval(Duration::from_millis(500));
        loop {
            if *stopping.borrow() {break;}
            tokio::select! {_=tick.tick()=>{},_=stopping.changed()=>break}
            let id=project.clone();
            let result=database(runtime.clone(),move |store| {
                let latest=store.latest_job_event(&id)?;
                if cursor>latest {return Ok((latest,Vec::new(),Some(store.list_jobs(&id)?)));}
                Ok((latest,store.job_events(&id,cursor)?,None))
            }).await;
            match result {
                Ok((latest,items,Some(snapshot))) => {
                    cursor=latest;
                    yield Ok::<_,Infallible>(Event::default().id(cursor.to_string()).event("reset").data(json!(snapshot).to_string()));
                    debug_assert!(items.is_empty());
                },
                Ok((_,items,None)) => for item in items {
                    cursor=item.sequence;
                    yield Ok(Event::default().id(cursor.to_string()).event("job").data(json!(item.job).to_string()));
                },
                Err(_) => {yield Ok(Event::default().event("storage-error").data("{\"error\":\"job_read_failed\"}"));break;},
            }
        }
    };
    Ok(Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(10))))
}

async fn transcribe_job(
    runtime: &Runtime,
    job: &Job,
    mut stopping: watch::Receiver<bool>,
) -> Result<(Value, Option<editorkrab::chunk_cache::Candidate>), String> {
    let Task::Transcribe {
        path,
        context,
        settings,
    } = &job.task
    else {
        return Err("invalid_transcription_task".into());
    };
    let asr = runtime.asr().ok_or("transcription_not_configured")?;
    let (config, _) = asr.as_ref();
    let (cancel, signal) = watch::channel(false);
    let (progress, mut stages) = watch::channel("verifying");
    let task = editorkrab::native_asr::run_cached(
        config,
        std::path::Path::new(path),
        context,
        signal,
        Some(settings),
        Some(progress),
        Some((runtime.store.clone(), job.id.clone())),
    );
    tokio::pin!(task);
    let mut tick = tokio::time::interval(Duration::from_millis(250));
    let mut stage = "verifying".to_string();
    loop {
        tokio::select! {
            biased;
            result=&mut task=>return result.and_then(|mut v|{let candidate=v.cache_candidate.take();serde_json::to_value(v).map(|value|(value,candidate)).map_err(|_|"transcription_result_invalid".into())}),
            _=async {let _=stopping.wait_for(|v|*v).await;}=>{let _=cancel.send(true);let _=task.await;return Err("application_stopping".into());},
            change=stages.changed()=>{if change.is_ok(){stage=stages.borrow_and_update().to_string();}},
            _=tick.tick()=>{
                let id=job.id.clone();let next=stage.clone();
                let checked=database(runtime.clone(),move|store|{
                    let cancelled=store.job(&id)?.cancel_requested;
                    store.job_step(&id,&next)?;store.heartbeat_job(&id)?;Ok(cancelled)
                }).await;
                match checked {
                    Ok(true)=>{let _=cancel.send(true);let _=task.await;return Err("cancelled".into());},
                    Ok(false)=>{},
                    Err(_)=>{let _=cancel.send(true);let _=task.await;return Err("job_store_unavailable".into());}
                }
            }
        }
    }
}

async fn worker(runtime: Runtime, ffprobe: PathBuf) {
    let mut stopping = runtime.stopping.subscribe();
    let mut tick = tokio::time::interval(Duration::from_millis(500));
    loop {
        if *stopping.borrow() {
            return;
        }
        tokio::select! {_=tick.tick()=>{},_=stopping.changed()=>return}
        let job = match database(runtime.clone(), |store| store.claim_job()).await {
            Ok(Some(job)) => job,
            Ok(None) => continue,
            Err(_) => {
                eprintln!("job_claim_failed");
                continue;
            }
        };
        let result = match &job.task {
            Task::Probe { .. } => {
                editorkrab::media::probe(&job, &ffprobe, runtime.store.clone(), stopping.clone())
                    .await
                    .map(|value| (value, None))
            }
            Task::Transcribe { .. } => transcribe_job(&runtime, &job, stopping.clone()).await,
        };
        loop {
            if *stopping.borrow() {
                return;
            }
            let id = job.id.clone();
            let result = result.clone();
            match database(runtime.clone(), move |store| {
                let (result, cache) = match result {
                    Ok((value, cache)) => (Ok(value), cache),
                    Err(error) => (Err(error), None),
                };
                match store.finish_job_cached(&id, result, cache.as_ref()) {
                    Err(Error::TranscriptionCacheCorrupt) => {
                        store.finish_job(&id, Err("transcription_cache_corrupt".into()))
                    }
                    other => other,
                }
            })
            .await
            {
                Ok(_) => break,
                Err(_) => eprintln!("job_completion_save_failed"),
            }
            tokio::select! {_=tokio::time::sleep(Duration::from_secs(1))=>{},_=stopping.changed()=>return}
        }
    }
}

async fn local_only(
    headers: HeaderMap,
    request: axum::extract::Request,
    next: axum::middleware::Next,
) -> axum::response::Response {
    let host = headers
        .get("host")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let allowed = host
        .strip_prefix("127.0.0.1:")
        .or_else(|| host.strip_prefix("localhost:"))
        .is_some_and(|port| port.parse::<u16>().is_ok());
    let foreign_origin = headers
        .get("origin")
        .is_some_and(|origin| origin.to_str().ok() != Some(&format!("http://{host}")));
    let foreign_fetch = headers
        .get("sec-fetch-site")
        .is_some_and(|v| v == "cross-site");
    if !allowed || foreign_origin || foreign_fetch {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({"error":"local_origin_required"})),
        )
            .into_response();
    }
    let mut response = next.run(request).await;
    response.headers_mut().insert(
        "cache-control",
        axum::http::HeaderValue::from_static("no-store"),
    );
    response
}

/// Transcription tools shipped beside the executable in `tools/`, or downloaded by the app into its
/// data folder, so a fresh install works without environment variables. The largest model wins.
struct Found {
    engine: Option<PathBuf>,
    model: Option<PathBuf>,
    ffmpeg: Option<PathBuf>,
    gpu: Option<PathBuf>,
}
fn tool_roots(data: &std::path::Path) -> Vec<PathBuf> {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.join("tools")))
        .into_iter()
        .chain([data.to_path_buf()])
        .collect()
}
fn find_tool(roots: &[PathBuf], folder: &str, name: &str) -> Option<PathBuf> {
    let exe = if cfg!(windows) { ".exe" } else { "" };
    roots
        .iter()
        .map(|r| r.join(folder).join(format!("{name}{exe}")))
        .find(|p| p.is_file())
}
fn find_asr_tools(data: &std::path::Path) -> Found {
    let roots = tool_roots(data);
    let first = |folder: &str, name: &str| find_tool(&roots, folder, name);
    let model = roots
        .iter()
        .filter_map(|r| std::fs::read_dir(r.join("models")).ok())
        .flatten()
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with("ggml-") && n.ends_with(".bin"))
        })
        .max_by_key(|p| p.metadata().map(|m| m.len()).unwrap_or(0));
    Found {
        engine: first("whisper", "whisper-cli"),
        model,
        ffmpeg: first("ffmpeg", "ffmpeg"),
        gpu: gpu_engine(data).filter(|_| !force_cpu(data)),
    }
}
/// The CUDA build of whisper-cli, whether or not it is in use; EDITORKRAB_WHISPER_GPU overrides the search.
fn gpu_engine(data: &std::path::Path) -> Option<PathBuf> {
    match std::env::var("EDITORKRAB_WHISPER_GPU") {
        Ok(path) => Some(path.into()),
        Err(_) => find_tool(&tool_roots(data), "whisper-cuda", "whisper-cli"),
    }
}
/// The CTranslate2 GPU sidecar (`asr-ct2/ek-asr-ct2`) and its model folder (`models/ct2-large-v3`);
/// EDITORKRAB_CT2 and EDITORKRAB_CT2_MODEL override the search. Off under "CPU only". One handle per
/// process, so swapping the tools keeps a model that is already loaded.
fn ct2_sidecar(data: &std::path::Path) -> Option<Arc<editorkrab::ct2::Ct2>> {
    static ONE: std::sync::OnceLock<Mutex<Option<Arc<editorkrab::ct2::Ct2>>>> =
        std::sync::OnceLock::new();
    let wanted = (!force_cpu(data)).then(|| {
        let roots = tool_roots(data);
        let exe = match std::env::var("EDITORKRAB_CT2") {
            Ok(path) => Some(PathBuf::from(path)),
            Err(_) => find_tool(&roots, "asr-ct2", "ek-asr-ct2"),
        }?;
        let model = match std::env::var("EDITORKRAB_CT2_MODEL") {
            Ok(path) => Some(PathBuf::from(path)),
            Err(_) => roots
                .iter()
                .map(|r| r.join("models").join("ct2-large-v3"))
                .find(|p| p.join("model.bin").is_file()),
        }?;
        Some((exe, model))
    });
    let mut slot = ONE
        .get_or_init(Mutex::default)
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    match wanted.flatten() {
        None => {
            *slot = None;
            None
        }
        Some((exe, model)) => {
            if let Some(current) = slot.as_ref().filter(|c| c.exe == exe && c.model == model) {
                return Some(current.clone());
            }
            let fresh = editorkrab::ct2::Ct2::new(exe, model);
            *slot = Some(fresh.clone());
            Some(fresh)
        }
    }
}
/// Starts loading the GPU model as soon as a video comes in, so the first chunk does not wait for it.
fn preload_asr(runtime: &Runtime) {
    if let Some(ct2) = runtime.asr().and_then(|a| a.0.ct2.clone()) {
        tokio::spawn(async move {
            if let Err(e) = ct2.preload().await {
                eprintln!("transcription_ct2_preload_failed: {e}");
            }
        });
    }
}
/// The user's "CPU only" switch, kept as a marker file so it survives a restart.
fn force_cpu(data: &std::path::Path) -> bool {
    data.join("force-cpu").exists()
}
/// An NVIDIA card with a working driver; only then is the GPU build offered (Windows builds only).
fn nvidia() -> bool {
    static FOUND: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *FOUND.get_or_init(|| {
        if !cfg!(windows) {
            return false;
        }
        let smi =
            PathBuf::from(std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into()))
                .join("System32")
                .join("nvidia-smi.exe");
        let mut command = std::process::Command::new(smi);
        command.arg("-L");
        #[cfg(windows)]
        std::os::windows::process::CommandExt::creation_flags(&mut command, 0x0800_0000);
        command.output().is_ok_and(|o| {
            o.status.success() && String::from_utf8_lossy(&o.stdout).contains("GPU ")
        })
    })
}
type BoxError = Box<dyn std::error::Error + Send + Sync>;
async fn configure_asr(
    whisper: PathBuf,
    model: PathBuf,
    ffmpeg: PathBuf,
    gpu: Option<PathBuf>,
    ct2: Option<Arc<editorkrab::ct2::Ct2>>,
    state_path: &std::path::Path,
) -> Result<Asr, BoxError> {
    let config = AsrConfig {
        whisper,
        model,
        ffmpeg,
        workspace: PathBuf::from(format!("{}.asr-work", state_path.display())),
        timeout: Duration::from_secs(3600),
        threads: asr_threads(),
        gpu,
        ct2,
    };
    let capture = config.clone();
    let settings = tokio::task::spawn_blocking(move || AsrSettings::read(&capture)).await??;
    if !settings.valid() {
        return Err("invalid transcription tools".into());
    }
    Ok(Arc::new((config, settings)))
}
// 2 threads left a 20-thread CPU mostly idle (docs/test-logs/rust-20261018/flow-run3.md); capped at 8 until a sweep is measured
fn asr_threads() -> u16 {
    std::thread::available_parallelism().map_or(2, |n| n.get().clamp(1, 8) as u16)
}
fn data_dir() -> PathBuf {
    if let Ok(path) = std::env::var("EDITORKRAB_DATA") {
        return path.into();
    }
    let base = std::env::var("LOCALAPPDATA")
        .map(PathBuf::from)
        .or_else(|_| std::env::var("HOME").map(|h| PathBuf::from(h).join(".local/share")))
        .unwrap_or_else(|_| PathBuf::from("."));
    base.join("EditorKrab")
}

/// What the in-app download still has to fetch; an error names what it cannot fetch.
fn download_plan(setup: &AsrSetup) -> Result<Vec<editorkrab::asr_download::Item>, String> {
    let found = find_asr_tools(&setup.data);
    if found.ffmpeg.is_none() {
        return Err("ffmpeg_missing".into());
    }
    if found.engine.is_none() && !cfg!(windows) {
        return Err("runtime_missing".into());
    }
    let mut items =
        editorkrab::asr_download::plan(&setup.data, found.engine.is_none(), found.model.is_none());
    if let Some(model) = &setup.model {
        for item in items.iter_mut().filter(|i| !i.unzip) {
            *item = model.clone();
        }
    }
    Ok(items)
}
fn received(items: &[editorkrab::asr_download::Item]) -> u64 {
    items
        .iter()
        .map(|i| match std::fs::metadata(i.part()) {
            Ok(m) => m.len().min(i.size),
            Err(_) if i.dest.exists() => i.size,
            Err(_) => 0,
        })
        .sum()
}
fn download_error(code: String) -> ApiError {
    let body = match code.strip_prefix("disk_space:") {
        Some(rest) => {
            let (drive, needed) = rest.rsplit_once(':').unwrap_or((rest, "0"));
            json!({"error":"disk_space","drive":drive,"needed_bytes":needed.parse::<u64>().unwrap_or(0)})
        }
        None => json!({ "error": code }),
    };
    (StatusCode::CONFLICT, Json(body))
}
async fn download_status(State(runtime): State<Runtime>) -> Json<Download> {
    let mut status = runtime
        .download
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clone();
    if runtime.asr().is_some() {
        status.state = "done";
    } else if status.state.is_empty() || status.state == "idle" {
        let setup = runtime.setup.clone();
        match tokio::task::spawn_blocking(move || download_plan(&setup).map(|p| (received(&p), p)))
            .await
        {
            Ok(Ok((have, plan))) => {
                status.state = "idle";
                status.total = plan.iter().map(|i| i.size).sum();
                status.received = have;
            }
            Ok(Err(code)) => {
                status.state = "failed";
                status.error = Some(code);
            }
            Err(e) => {
                status.state = "failed";
                status.error = Some(e.to_string());
            }
        }
    }
    Json(status)
}
async fn start_download(State(runtime): State<Runtime>) -> Result<Json<Download>, ApiError> {
    if runtime.asr().is_some() {
        return Ok(download_status(State(runtime)).await);
    }
    let setup = runtime.setup.clone();
    let items = tokio::task::spawn_blocking(move || -> Result<_, String> {
        let items = download_plan(&setup)?;
        let free = editorkrab::asr_download::free_space(&setup.data)?;
        let drive = editorkrab::asr_download::drive(&setup.data);
        editorkrab::asr_download::check_space(free, &items, &drive)?;
        Ok(items)
    })
    .await
    .map_err(|e| download_error(e.to_string()))?
    .map_err(download_error)?;
    let (cancel, signal) = watch::channel(false);
    {
        let mut status = runtime.download.lock().unwrap_or_else(|e| e.into_inner());
        if status.state == "running" {
            return Ok(Json(status.clone()));
        }
        *status = Download {
            state: "running",
            total: items.iter().map(|i| i.size).sum(),
            received: received(&items),
            cancel: Some(cancel),
            ..Download::default()
        };
    }
    tokio::spawn(run_download(
        runtime.clone(),
        items,
        signal,
        runtime.download.clone(),
    ));
    Ok(download_status(State(runtime)).await)
}
async fn cancel_download(State(runtime): State<Runtime>) -> Json<Download> {
    if let Some(cancel) = &runtime
        .download
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .cancel
    {
        let _ = cancel.send(true);
    }
    download_status(State(runtime)).await
}
async fn run_download(
    runtime: Runtime,
    items: Vec<editorkrab::asr_download::Item>,
    signal: watch::Receiver<bool>,
    download: Arc<Mutex<Download>>,
) {
    let ticker = {
        let (download, items) = (download.clone(), items.clone());
        tokio::spawn(async move {
            let mut last = (std::time::Instant::now(), received(&items));
            loop {
                tokio::time::sleep(Duration::from_millis(500)).await;
                let now = (std::time::Instant::now(), received(&items));
                let rate = now.1.saturating_sub(last.1) as f64
                    / now.0.duration_since(last.0).as_secs_f64().max(0.001);
                let mut status = download.lock().unwrap_or_else(|e| e.into_inner());
                status.received = now.1;
                status.bytes_per_second = rate as u64;
                last = now;
            }
        })
    };
    let curl = if cfg!(windows) {
        PathBuf::from(std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into()))
            .join("System32")
            .join("curl.exe")
    } else {
        PathBuf::from("curl")
    };
    let mut outcome = Ok(());
    for item in &items {
        if let Err(e) = editorkrab::asr_download::fetch(item, &curl, signal.clone()).await {
            outcome = Err(e);
            break;
        }
    }
    // a GPU build fetched before the model waits for the main download to switch it on
    if outcome.is_ok() && (runtime.asr().is_some() || Arc::ptr_eq(&download, &runtime.download)) {
        outcome = reload_asr(&runtime).await;
    }
    ticker.abort();
    let mut status = download.lock().unwrap_or_else(|e| e.into_inner());
    status.received = received(&items);
    status.bytes_per_second = 0;
    status.cancel = None;
    match outcome {
        Ok(()) => status.state = "done",
        Err(e) if e == "cancelled" => status.state = "cancelled",
        Err(e) => {
            status.state = "failed";
            status.error = Some(e);
        }
    }
}

/// Swaps the live tools without a restart. Already configured: only the GPU build changes, so the
/// CPU identity (and every queued job's settings) stays valid without rehashing the model.
async fn reload_asr(runtime: &Runtime) -> Result<(), String> {
    let data = &runtime.setup.data;
    let asr = match runtime.asr() {
        Some(current) => {
            let (mut config, settings) = (current.0.clone(), current.1.clone());
            config.gpu = gpu_engine(data).filter(|_| !force_cpu(data));
            config.ct2 = ct2_sidecar(data);
            Arc::new((config, settings))
        }
        None => {
            let found = find_asr_tools(data);
            let (Some(engine), Some(model), Some(ffmpeg)) =
                (found.engine, found.model, found.ffmpeg)
            else {
                return Err("invalid_tools".into());
            };
            configure_asr(
                engine,
                model,
                ffmpeg,
                found.gpu,
                ct2_sidecar(data),
                &runtime.setup.state_path,
            )
            .await
            .map_err(|e| format!("invalid_tools:{e}"))?
        }
    };
    *runtime.asr.write().unwrap_or_else(|e| e.into_inner()) = Some(asr);
    Ok(())
}

/// GPU speed-up: offered on an NVIDIA machine, used when installed unless "CPU only" is on.
async fn gpu_status(State(runtime): State<Runtime>) -> Json<Value> {
    let data = runtime.setup.data.clone();
    let item = editorkrab::asr_download::gpu_plan(&data);
    let (nvidia, installed, forced, have) = tokio::task::spawn_blocking(move || {
        let installed = gpu_engine(&data).is_some();
        let have = if installed {
            item.size
        } else {
            received(&[item])
        };
        (nvidia(), installed, force_cpu(&data), have)
    })
    .await
    .unwrap_or_default();
    let mut download = runtime
        .gpu_download
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .clone();
    if download.state.is_empty() {
        download.state = if installed { "done" } else { "idle" };
    }
    download.total = editorkrab::asr_download::GPU_RUNTIME_SIZE;
    if download.state != "running" {
        download.received = have;
    }
    let asr = runtime.asr();
    let ct2 = asr.as_ref().is_some_and(|a| a.0.ct2.is_some());
    let active = ct2 || asr.is_some_and(|a| a.0.gpu.is_some());
    Json(
        json!({"nvidia":nvidia,"installed":installed,"force_cpu":forced,"active":active,"ct2":ct2,"download":download}),
    )
}
async fn start_gpu_download(State(runtime): State<Runtime>) -> Result<Json<Value>, ApiError> {
    let data = runtime.setup.data.clone();
    let items = tokio::task::spawn_blocking(move || -> Result<_, String> {
        if !nvidia() {
            return Err("gpu_missing".into());
        }
        let items = vec![editorkrab::asr_download::gpu_plan(&data)];
        let free = editorkrab::asr_download::free_space(&data)?;
        let drive = editorkrab::asr_download::drive(&data);
        editorkrab::asr_download::check_space(free, &items, &drive)?;
        Ok(items)
    })
    .await
    .map_err(|e| download_error(e.to_string()))?
    .map_err(download_error)?;
    let (cancel, signal) = watch::channel(false);
    {
        let mut status = runtime
            .gpu_download
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if status.state != "running" {
            *status = Download {
                state: "running",
                total: editorkrab::asr_download::GPU_RUNTIME_SIZE,
                received: received(&items),
                cancel: Some(cancel),
                ..Download::default()
            };
            tokio::spawn(run_download(
                runtime.clone(),
                items,
                signal,
                runtime.gpu_download.clone(),
            ));
        }
    }
    Ok(gpu_status(State(runtime)).await)
}
async fn cancel_gpu_download(State(runtime): State<Runtime>) -> Json<Value> {
    if let Some(cancel) = &runtime
        .gpu_download
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .cancel
    {
        let _ = cancel.send(true);
    }
    gpu_status(State(runtime)).await
}
#[derive(Deserialize)]
struct GpuChoice {
    force_cpu: bool,
}
async fn set_gpu(
    State(runtime): State<Runtime>,
    Json(choice): Json<GpuChoice>,
) -> Result<Json<Value>, ApiError> {
    let marker = runtime.setup.data.join("force-cpu");
    let written = if choice.force_cpu {
        std::fs::create_dir_all(&runtime.setup.data).and_then(|_| std::fs::write(&marker, b""))
    } else {
        std::fs::remove_file(&marker).or_else(|e| match e.kind() {
            std::io::ErrorKind::NotFound => Ok(()),
            _ => Err(e),
        })
    };
    written.map_err(|e| download_error(format!("gpu_setting:{e}")))?;
    if runtime.asr().is_some() {
        reload_asr(&runtime).await.map_err(download_error)?;
    }
    Ok(gpu_status(State(runtime)).await)
}

async fn serve(
    picker: mpsc::Sender<PickCommand>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    // Separate state is required during migration; never open the live Python state implicitly.
    let input = PathBuf::from(std::env::var("EDITORKRAB_RUST_STATE")?);
    let state_path = if input.exists() {
        input.canonicalize()?
    } else {
        input
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(std::path::Path::new("."))
            .canonicalize()?
            .join(input.file_name().ok_or("state file name missing")?)
    };
    let mut lock_path = state_path.as_os_str().to_os_string();
    lock_path.push(".lock");
    let instance_lock = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(lock_path)?;
    instance_lock.try_lock()?;
    let mut store = Store::open(&state_path)?;
    store.initialize_jobs()?;
    store.recover_jobs()?;
    let (stopping, _) = watch::channel(false);
    let data = data_dir();
    let asr = match (
        std::env::var("EDITORKRAB_WHISPER"),
        std::env::var("EDITORKRAB_WHISPER_MODEL"),
        std::env::var("EDITORKRAB_FFMPEG"),
    ) {
        (Err(_), Err(_), Err(_)) => match find_asr_tools(&data) {
            Found {
                engine: Some(engine),
                model: Some(model),
                ffmpeg: Some(ffmpeg),
                gpu,
            } => Some(
                configure_asr(engine, model, ffmpeg, gpu, ct2_sidecar(&data), &state_path).await?,
            ),
            _ => None,
        },
        (Ok(engine), Ok(model), Ok(ffmpeg)) => {
            let gpu = gpu_engine(&data).filter(|_| !force_cpu(&data));
            Some(
                configure_asr(
                    engine.into(),
                    model.into(),
                    ffmpeg.into(),
                    gpu,
                    ct2_sidecar(&data),
                    &state_path,
                )
                .await?,
            )
        }
        _ => return Err(
            "configure EDITORKRAB_WHISPER, EDITORKRAB_WHISPER_MODEL and EDITORKRAB_FFMPEG together"
                .into(),
        ),
    };
    // test-only: download this model item instead of the pinned large-v3
    let model = match std::env::var("EDITORKRAB_DOWNLOAD_MANIFEST") {
        Ok(path) => Some(serde_json::from_slice(&std::fs::read(path)?)?),
        Err(_) => None,
    };
    let runtime = Runtime {
        store: Arc::new(Mutex::new(store)),
        stopping,
        asr: Arc::new(std::sync::RwLock::new(asr)),
        setup: Arc::new(AsrSetup {
            state_path: state_path.clone(),
            data,
            model,
        }),
        download: Arc::new(Mutex::new(Download::default())),
        gpu_download: Arc::new(Mutex::new(Download::default())),
        exports: Arc::default(),
        picker,
        picker_busy: Arc::new(AtomicBool::new(false)),
        assets: PathBuf::from(format!(
            "{}.assets",
            state_path.to_str().ok_or("state path is not Unicode")?
        )),
    };
    let ffprobe = ffprobe_path(&runtime.setup);
    let app = Router::new()
        .route(
            "/",
            // no project in the address: the project screen comes first
            get(|Query(query): Query<HashMap<String, String>>| async move {
                Html(if query.contains_key("project") {
                    include_str!("../../design/rust-flow/index.html")
                } else {
                    include_str!("../../design/rust-flow/home.html")
                })
            }),
        )
        .route("/api/v1/drafts", get(list_drafts))
        .route("/api/v1/drafts/{project}/hide", post(hide_draft))
        .route("/api/v1/drafts/{project}/restore", post(restore_draft))
        .route(
            "/home.js",
            get(|| async {
                (
                    [("content-type", "application/javascript; charset=utf-8")],
                    include_str!("../../design/rust-flow/home.js"),
                )
            }),
        )
        .route(
            "/flow.js",
            get(|| async {
                (
                    [("content-type", "application/javascript; charset=utf-8")],
                    include_str!("../../design/rust-flow/flow.js"),
                )
            }),
        )
        .route(
            "/autosave.js",
            get(|| async {
                (
                    [("content-type", "application/javascript; charset=utf-8")],
                    include_str!("../../design/rust-flow/autosave.js"),
                )
            }),
        )
        .route(
            "/jobs.js",
            get(|| async {
                (
                    [("content-type", "application/javascript; charset=utf-8")],
                    include_str!("../../design/rust-flow/jobs.js"),
                )
            }),
        )
        .route(
            "/uploads.js",
            get(|| async {
                (
                    [("content-type", "application/javascript; charset=utf-8")],
                    include_str!("../../design/rust-flow/uploads.js"),
                )
            }),
        )
        .route(
            "/text-style.js",
            get(|| async {
                (
                    [("content-type", "application/javascript; charset=utf-8")],
                    include_str!("../../design/rust-flow/text-style.js"),
                )
            }),
        )
        .route("/api/v1/drafts/{project}", get(read).put(save))
        .route("/api/v1/drafts/{project}/copy", post(copy_draft))
        .route(
            "/text-qc.js",
            get(|| async {
                (
                    [("content-type", "application/javascript; charset=utf-8")],
                    include_str!("../../design/rust-flow/text-qc.js"),
                )
            }),
        )
        .route(
            "/export.js",
            get(|| async {
                (
                    [("content-type", "application/javascript; charset=utf-8")],
                    include_str!("../../design/rust-flow/export.js"),
                )
            }),
        )
        .route(
            "/stories.js",
            get(|| async {
                (
                    [("content-type", "application/javascript; charset=utf-8")],
                    include_str!("../../design/rust-flow/stories.js"),
                )
            }),
        )
        .route(
            "/transcription.js",
            get(|| async {
                (
                    [("content-type", "application/javascript; charset=utf-8")],
                    include_str!("../../design/rust-flow/transcription.js"),
                )
            }),
        )
        .route(
            "/api/v1/transcription/settings",
            get(|State(runtime): State<Runtime>| async move {
                Json(runtime.asr().map(|v| v.1.clone()))
            }),
        )
        .route(
            "/api/v1/transcription/download",
            get(download_status).post(start_download),
        )
        .route(
            "/api/v1/transcription/download/cancel",
            post(cancel_download),
        )
        .route("/api/v1/transcription/gpu", get(gpu_status).put(set_gpu))
        .route(
            "/api/v1/transcription/gpu/download",
            post(start_gpu_download),
        )
        .route(
            "/api/v1/transcription/gpu/download/cancel",
            post(cancel_gpu_download),
        )
        .route(
            "/api/v1/projects/{project}/export",
            get(export_status).post(start_export),
        )
        .route(
            "/api/v1/projects/{project}/export/cancel",
            post(cancel_export),
        )
        .route(
            "/api/v1/projects/{project}/export/reveal",
            post(reveal_export),
        )
        .route("/api/v1/projects/{project}/capcut", post(capcut_export))
        .route(
            "/api/v1/projects/{project}/transcription",
            post(start_transcription),
        )
        .route(
            "/api/v1/projects/{project}/sources/{source_id}/audio",
            get(source_audio),
        )
        .route(
            "/api/v1/projects/{project}/sources/{source_id}/preview",
            get(source_preview),
        )
        .route(
            "/api/v1/projects/{project}/sources/{source_id}/pauses",
            get(source_pauses),
        )
        .route("/api/v1/spelling/learn", post(spelling_learn))
        .route("/api/v1/spelling/fix", post(spelling_fix))
        .route("/api/v1/text-qc", post(text_qc))
        .route("/api/v1/sources/pick", post(pick_sources))
        .route("/api/v1/sources/locate", post(locate_sources))
        .route("/api/v1/voice", get(voice_status))
        .route("/api/v1/voice/download", post(voice_download))
        .route("/api/v1/voice/dead-air", post(dead_air))
        .route("/api/v1/projects/{project}/sources/{source_id}/voice", post(source_voice))
        .route("/api/v1/sources/uploads", post(begin_upload))
        .route("/api/v1/sources/uploads/{id}", post(upload_chunk))
        .route("/api/v1/sources/uploads/{id}/finish", post(finish_upload))
        // the app window (Edge/Chrome --app) shows this as its title-bar and taskbar icon
        .route(
            "/favicon.ico",
            get(|| async {
                (
                    [("content-type", "image/x-icon")],
                    include_bytes!("../../app/static/editorkrab-crab.ico").as_slice(),
                )
            }),
        )
        .route(
            "/api/v1/projects/{project}/jobs",
            get(list_jobs).post(create_jobs),
        )
        .route("/api/v1/projects/{project}/events", get(events))
        .route("/api/v1/jobs/{id}/cancel", post(cancel_job))
        .route("/api/v1/jobs/{id}/retry", post(retry_job))
        .route("/api/v1/jobs/{id}/retime", post(retime_job))
        // agents (Claude, ChatGPT, ...) drive the same project through MCP; `--mcp` bridges stdio here
        .route("/mcp", post(agent::endpoint))
        .layer(axum::extract::DefaultBodyLimit::max(8 * 1024 * 1024))
        .layer(axum::middleware::from_fn(local_only))
        .with_state(runtime.clone());
    let port: u16 = std::env::var("EDITORKRAB_RUST_PORT")
        .unwrap_or_else(|_| "8871".into())
        .parse()?;
    let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, port)).await?;
    println!("EditorKrab Rust migration preview: http://127.0.0.1:{port}");
    let work = tokio::spawn(worker(runtime.clone(), ffprobe));
    axum::serve(listener, app)
        .with_graceful_shutdown(async move {
            let _ = tokio::signal::ctrl_c().await;
            let _ = runtime.stopping.send(true);
        })
        .await?;
    work.await?;
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    if std::env::args().nth(1).as_deref() == Some("--mcp") {
        let port: u16 = std::env::var("EDITORKRAB_RUST_PORT")
            .unwrap_or_else(|_| "8871".into())
            .parse()?;
        editorkrab::mcp::bridge(port)?;
        return Ok(());
    }
    let (picker, requests) = mpsc::channel();
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let server = runtime.spawn(serve(picker));
    while !server.is_finished() {
        match requests.recv_timeout(Duration::from_millis(100)) {
            Ok(command) => editorkrab::sources::dispatch(command),
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
    runtime.block_on(server)??;
    Ok(())
}
