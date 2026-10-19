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
use std::{convert::Infallible, path::PathBuf, time::Duration};
use tokio::sync::watch;

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
    /// where new project folders go unless the person picks another parent
    projects_root: PathBuf,
    // swapped in place when the in-app download finishes, so no restart is needed
    asr: Arc<std::sync::RwLock<Option<Asr>>>,
    setup: Arc<AsrSetup>,
    download: Arc<Mutex<Download>>,
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
    let id = project.clone();
    let folder = match database(runtime.clone(), move |store| store.project(&id)).await? {
        Some(p) => PathBuf::from(p.folder).join(editorkrab::projects::EXPORTS),
        None => export_dir().ok_or_else(|| export_error("output_folder_unknown"))?,
    };
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
                &editorkrab::export::ffmpeg_args(story, &partial),
                signal.clone(),
                progress,
            )
            .await?;
            verify_export(&ffprobe, &partial, story.duration_ms()).await?;
            let name = editorkrab::export::file_name(
                &[title.trim(), story.title.as_str()]
                    .into_iter()
                    .filter(|t| !t.is_empty())
                    .collect::<Vec<_>>()
                    .join(" - "),
                &format!("EditorKrab {}", n + 1),
            );
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
/// A clip is published only when ffprobe reads one video and one audio stream of the planned length.
async fn verify_export(
    ffprobe: &std::path::Path,
    file: &std::path::Path,
    expected_ms: u64,
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
        v["width"] == editorkrab::export::WIDTH && v["height"] == editorkrab::export::HEIGHT
    });
    let duration_ms = info["format"]["duration"]
        .as_str()
        .and_then(|d| d.parse::<f64>().ok())
        .map(|d| (d * 1000.0) as u64)
        .unwrap_or(0);
    if !size_ok || !has_audio || duration_ms.abs_diff(expected_ms) > 500 {
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
    let assets = runtime.assets.clone();
    database(runtime, move |store| {
        let root = upload_root(store, &assets, &body.project)?;
        store.begin_upload(&root, &body)
    })
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
        let root = upload_root(store, &assets, &store.upload_project(&id)?)?;
        store.upload_chunk(&root, &id, query.offset, &body)
    })
    .await
    .map(Json)
}
async fn finish_upload(
    State(runtime): State<Runtime>,
    Path(id): Path<String>,
) -> Result<Json<editorkrab::sources::Source>, ApiError> {
    let assets = runtime.assets.clone();
    database(runtime, move |store| {
        let root = upload_root(store, &assets, &store.upload_project(&id)?)?;
        store.finish_upload(&root, &id)
    })
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

/// Shows one native dialog at a time; an empty list means the person cancelled.
async fn pick(runtime: &Runtime, kind: PickKind) -> Result<Vec<PathBuf>, ApiError> {
    if runtime
        .picker_busy
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return Err((StatusCode::CONFLICT, Json(json!({"error":"picker_busy"}))));
    }
    let lease = PickerLease(runtime.picker_busy.clone());
    let (answer, receive) = tokio::sync::oneshot::channel();
    runtime
        .picker
        .send(PickCommand {
            kind,
            answer,
            lease,
        })
        .map_err(|_| failure(Error::Unavailable))?;
    receive.await.map_err(|_| failure(Error::Unavailable))
}

async fn pick_sources(
    State(runtime): State<Runtime>,
    Json(body): Json<PickBody>,
) -> Result<Json<Selection>, ApiError> {
    let paths = pick(&runtime, body.kind).await?;
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

/// Copies of dropped videos go inside their project; old drafts without a folder keep the app folder.
fn upload_root(store: &Store, assets: &std::path::Path, project: &str) -> Result<PathBuf, Error> {
    Ok(match store.project(project)? {
        Some(p) => PathBuf::from(p.folder).join(editorkrab::projects::MEDIA),
        None => assets.to_path_buf(),
    })
}

fn project_json(store: &Store, p: &editorkrab::projects::Project) -> Value {
    let title = store
        .read(&p.id)
        .ok()
        .flatten()
        .and_then(|d| d.document["title"].as_str().map(str::to_owned))
        .unwrap_or_default();
    let exists = std::path::Path::new(&p.folder)
        .join(editorkrab::projects::FILE)
        .is_file();
    json!({"id":p.id,"name":p.name,"folder":p.folder,"opened_at":p.opened_at,"title":title,"exists":exists})
}

async fn list_projects(State(runtime): State<Runtime>) -> Result<Json<Value>, ApiError> {
    let root = runtime.projects_root.clone();
    database(runtime, move |store| {
        let projects: Vec<Value> = store
            .projects()?
            .iter()
            .map(|p| project_json(store, p))
            .collect();
        Ok(json!({"projects":projects,"legacy":store.legacy_drafts()?,"default_parent":root}))
    })
    .await
    .map(Json)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NewProject {
    name: String,
    parent: Option<String>,
    /// an old draft that is getting its folder
    id: Option<String>,
}
async fn create_project(
    State(runtime): State<Runtime>,
    Json(body): Json<NewProject>,
) -> Result<Json<Value>, ApiError> {
    let parent = body
        .parent
        .map(PathBuf::from)
        .unwrap_or_else(|| runtime.projects_root.clone());
    if !parent.is_absolute() {
        return Err(failure(Error::InvalidInput));
    }
    database(runtime, move |store| {
        let document = json!({"flow_version":2,"headline_items":[],"step":1,"title":body.name.trim(),
            "language":"auto","files":[],"plan":"","style":"bold16"});
        let project = store.create_project(&parent, &body.name, body.id.as_deref(), &document)?;
        Ok(project_json(store, &project))
    })
    .await
    .map(Json)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct OpenProject {
    folder: Option<String>,
}
async fn open_project(
    State(runtime): State<Runtime>,
    Json(body): Json<OpenProject>,
) -> Result<Json<Value>, ApiError> {
    let folder = match body.folder {
        Some(folder) => PathBuf::from(folder),
        None => match pick(&runtime, PickKind::ProjectFolder).await?.pop() {
            Some(folder) => folder,
            None => return Ok(Json(json!({"cancelled":true}))),
        },
    };
    if !folder.join(editorkrab::projects::FILE).is_file() {
        return Err(export_error("not_a_project"));
    }
    database(runtime, move |store| {
        let project = store.open_project(&folder)?;
        Ok(project_json(store, &project))
    })
    .await
    .map(Json)
}

async fn pick_parent(State(runtime): State<Runtime>) -> Result<Json<Value>, ApiError> {
    Ok(Json(
        match pick(&runtime, PickKind::ProjectFolder).await?.pop() {
            Some(folder) => json!({"folder":folder}),
            None => json!({"cancelled":true}),
        },
    ))
}

fn trash(path: PathBuf) -> Result<(), ApiError> {
    trash::delete(&path).map_err(|e| export_error(format!("trash_failed:{e}")))
}

/// The whole project folder goes to the Recycle Bin; the original videos it links stay where they are.
async fn trash_project(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
) -> Result<StatusCode, ApiError> {
    let id = project.clone();
    let found = database(runtime.clone(), move |store| {
        store.project(&id)?.ok_or(Error::NotFound)
    })
    .await?;
    let folder = PathBuf::from(&found.folder);
    if folder.exists() {
        tokio::task::spawn_blocking(move || trash(folder))
            .await
            .map_err(|_| failure(Error::Unavailable))??;
    }
    database(runtime, move |store| store.forget(&project)).await?;
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProjectFile {
    path: String,
}
/// Removing footage never touches an original video: only a copy kept inside the project goes to the bin.
async fn trash_project_file(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
    Json(body): Json<ProjectFile>,
) -> Result<Json<Value>, ApiError> {
    let found = database(runtime, move |store| {
        store.project(&project)?.ok_or(Error::NotFound)
    })
    .await?;
    let path = PathBuf::from(&body.path);
    if !editorkrab::projects::inside(std::path::Path::new(&found.folder), &path) {
        return Ok(Json(json!({"trashed":false})));
    }
    tokio::task::spawn_blocking(move || trash(path))
        .await
        .map_err(|_| failure(Error::Unavailable))??;
    Ok(Json(json!({"trashed":true})))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CheckBody {
    files: Vec<editorkrab::sources::Linked>,
}
async fn check_sources(Json(body): Json<CheckBody>) -> Result<Json<Value>, ApiError> {
    if body.files.len() > 1000 {
        return Err(failure(Error::InvalidInput));
    }
    let states = tokio::task::spawn_blocking(move || editorkrab::sources::check(&body.files))
        .await
        .map_err(|_| failure(Error::Unavailable))?;
    Ok(Json(json!({ "states": states })))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RelinkBody {
    /// the first one is the video being relinked; the rest are looked for beside it
    missing: Vec<editorkrab::sources::Linked>,
}
async fn relink_source(
    State(runtime): State<Runtime>,
    Json(body): Json<RelinkBody>,
) -> Result<Json<Value>, ApiError> {
    if body.missing.is_empty() || body.missing.len() > 1000 {
        return Err(failure(Error::InvalidInput));
    }
    let Some(path) = pick(&runtime, PickKind::Relink).await?.pop() else {
        return Ok(Json(json!({"cancelled":true})));
    };
    tokio::task::spawn_blocking(move || {
        let picked = editorkrab::sources::picked(&path).map_err(source_failure)?;
        let others: Vec<Value> = editorkrab::sources::relink_siblings(
            std::path::Path::new(&picked.path),
            &body.missing[1..],
        )
        .into_iter()
        .filter(|(_, s)| s.path != picked.path)
        .map(|(old, source)| json!({"path":old,"source":source}))
        .collect();
        Ok(Json(json!({"picked":picked,"others":others})))
    })
    .await
    .map_err(|_| failure(Error::Unavailable))?
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LocateBody {
    files: Vec<editorkrab::sources::Dropped>,
}
/// Where a dropped video probably already lives: this project's videos' folders first, then the usual user folders.
async fn locate_sources(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
    Json(body): Json<LocateBody>,
) -> Result<Json<Value>, ApiError> {
    if body.files.len() > 1000 {
        return Err(failure(Error::InvalidInput));
    }
    let mut roots = database(runtime, move |store| {
        let mut roots: Vec<PathBuf> = store
            .read(&project)?
            .and_then(|d| d.document["files"].as_array().cloned())
            .unwrap_or_default()
            .iter()
            .filter_map(|f| f["path"].as_str())
            .filter_map(|p| std::path::Path::new(p).parent().map(|p| p.to_path_buf()))
            .collect();
        if let Some(p) = store.project(&project)? {
            roots.push(p.folder.into());
        }
        Ok(roots)
    })
    .await?;
    if let Some(home) = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }) {
        let home = PathBuf::from(home);
        for folder in [
            "Desktop",
            "Downloads",
            "Videos",
            "Movies",
            "Documents",
            "Pictures",
            "OneDrive",
        ] {
            roots.push(home.join(folder));
        }
    }
    let found =
        tokio::task::spawn_blocking(move || editorkrab::sources::locate(&roots, &body.files))
            .await
            .map_err(|_| failure(Error::Unavailable))?;
    Ok(Json(json!({ "files": found })))
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

async fn save(
    State(runtime): State<Runtime>,
    Path(project): Path<String>,
    Json(body): Json<Save>,
) -> Result<Json<Value>, ApiError> {
    let id = project.clone();
    let revision = database(runtime, move |store| {
        let revision = store.save(
            &id,
            &body.operation_id,
            body.expected_revision,
            &body.document,
        )?;
        // a retry of the same operation rewrites the file, so a failed write is never final
        store.mirror(&id)?;
        Ok(revision)
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
    database(runtime, move |store| {
        let draft = store.copy_draft(&project, &body)?;
        // a copy of a project gets its own folder beside the original
        if store.project(&project)?.is_none()
            && let Some(source) = body
                .source_project
                .as_deref()
                .map(|id| store.project(id))
                .transpose()?
                .flatten()
        {
            let folder = std::path::Path::new(&source.folder);
            let parent = folder.parent().ok_or(Error::InvalidInput)?;
            let made =
                editorkrab::projects::create_folder(parent, &format!("{} สำเนา", source.name))?;
            let name = made
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("")
                .to_owned();
            store.register(&project, &name, &made)?;
            store.mirror(&project)?;
        }
        Ok(draft)
    })
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
        store.start_transcription_checked(&project, &body, Some(&source))
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
    let store = runtime.store.clone();
    let path = tokio::task::spawn_blocking(move || -> Result<PathBuf, ApiError> {
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
    .map_err(|_| failure(Error::Unavailable))??;
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
    }
}
type BoxError = Box<dyn std::error::Error + Send + Sync>;
async fn configure_asr(
    whisper: PathBuf,
    model: PathBuf,
    ffmpeg: PathBuf,
    state_path: &std::path::Path,
) -> Result<Asr, BoxError> {
    let config = AsrConfig {
        whisper,
        model,
        ffmpeg,
        workspace: PathBuf::from(format!("{}.asr-work", state_path.display())),
        timeout: Duration::from_secs(3600),
        threads: asr_threads(),
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
    tokio::spawn(run_download(runtime.clone(), items, signal));
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
) {
    let ticker = {
        let (runtime, items) = (runtime.clone(), items.clone());
        tokio::spawn(async move {
            let mut last = (std::time::Instant::now(), received(&items));
            loop {
                tokio::time::sleep(Duration::from_millis(500)).await;
                let now = (std::time::Instant::now(), received(&items));
                let rate = now.1.saturating_sub(last.1) as f64
                    / now.0.duration_since(last.0).as_secs_f64().max(0.001);
                let mut status = runtime.download.lock().unwrap_or_else(|e| e.into_inner());
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
    if outcome.is_ok() {
        let found = find_asr_tools(&runtime.setup.data);
        outcome = match (found.engine, found.model, found.ffmpeg) {
            (Some(engine), Some(model), Some(ffmpeg)) => {
                match configure_asr(engine, model, ffmpeg, &runtime.setup.state_path).await {
                    Ok(asr) => {
                        *runtime.asr.write().unwrap_or_else(|e| e.into_inner()) = Some(asr);
                        Ok(())
                    }
                    Err(e) => Err(format!("invalid_tools:{e}")),
                }
            }
            _ => Err("invalid_tools".into()),
        };
    }
    ticker.abort();
    let mut status = runtime.download.lock().unwrap_or_else(|e| e.into_inner());
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
            } => Some(configure_asr(engine, model, ffmpeg, &state_path).await?),
            _ => None,
        },
        (Ok(engine), Ok(model), Ok(ffmpeg)) => {
            Some(configure_asr(engine.into(), model.into(), ffmpeg.into(), &state_path).await?)
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
        exports: Arc::default(),
        picker,
        picker_busy: Arc::new(AtomicBool::new(false)),
        projects_root: std::env::var("EDITORKRAB_PROJECTS_DIR")
            .map(PathBuf::from)
            .ok()
            .or_else(export_dir)
            .ok_or("no folder for projects")?,
        assets: PathBuf::from(format!(
            "{}.assets",
            state_path.to_str().ok_or("state path is not Unicode")?
        )),
    };
    let ffprobe = ffprobe_path(&runtime.setup);
    let app = Router::new()
        .route(
            "/",
            get(|| async { Html(include_str!("../../design/rust-flow/index.html")) }),
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
        .route(
            "/projects.js",
            get(|| async {
                (
                    [("content-type", "application/javascript; charset=utf-8")],
                    include_str!("../../design/rust-flow/projects.js"),
                )
            }),
        )
        .route("/api/v1/projects", get(list_projects).post(create_project))
        .route("/api/v1/projects/open", post(open_project))
        .route("/api/v1/projects/parent", post(pick_parent))
        .route("/api/v1/projects/{project}/trash", post(trash_project))
        .route(
            "/api/v1/projects/{project}/trash-file",
            post(trash_project_file),
        )
        .route("/api/v1/sources/check", post(check_sources))
        .route("/api/v1/sources/relink", post(relink_source))
        .route(
            "/api/v1/projects/{project}/sources/locate",
            post(locate_sources),
        )
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
        .route(
            "/api/v1/projects/{project}/transcription",
            post(start_transcription),
        )
        .route(
            "/api/v1/projects/{project}/sources/{source_id}/audio",
            get(source_audio),
        )
        .route(
            "/api/v1/projects/{project}/sources/{source_id}/pauses",
            get(source_pauses),
        )
        .route("/api/v1/spelling/learn", post(spelling_learn))
        .route("/api/v1/spelling/fix", post(spelling_fix))
        .route("/api/v1/text-qc", post(text_qc))
        .route("/api/v1/sources/pick", post(pick_sources))
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
