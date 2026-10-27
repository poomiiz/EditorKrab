//! The `/mcp` endpoint: each tool makes the same store calls and requests the step-by-step page makes,
//! so an agent and the person can work on one project and see each other's changes.
use super::*;
use editorkrab::mcp::{self, Message};
use editorkrab::transcription_jobs::StartRequest;

type Outcome = Result<Value, Value>;

fn refuse(code: &str) -> Value {
    json!({ "error": code })
}
fn api(error: ApiError) -> Value {
    error.1.0
}

pub(crate) async fn endpoint(
    State(runtime): State<Runtime>,
    headers: HeaderMap,
    body: Bytes,
) -> axum::response::Response {
    let Ok(body) = serde_json::from_slice::<Value>(&body) else {
        return Json(mcp::error(&Value::Null, -32700, "parse_error")).into_response();
    };
    let (id, method, params) = match mcp::parse(&body) {
        Message::Notification => return StatusCode::ACCEPTED.into_response(),
        Message::Invalid(id) => {
            return Json(mcp::error(&id, -32600, "invalid_request")).into_response();
        }
        Message::Request { id, method, params } => (id, method, params),
    };
    let reply = match method.as_str() {
        "initialize" => mcp::result(&id, mcp::initialize(&params)),
        "ping" => mcp::result(&id, json!({})),
        "tools/list" => mcp::result(&id, json!({ "tools": mcp::tools() })),
        "tools/call" => {
            let name = params["name"].as_str().unwrap_or("");
            let args = params
                .get("arguments")
                .cloned()
                .unwrap_or_else(|| json!({}));
            // local_only already accepted this host, so it is the address the person can open too
            let host = headers
                .get("host")
                .and_then(|v| v.to_str().ok())
                .unwrap_or("127.0.0.1:8871");
            match call(runtime, name, args, host).await {
                Some(Ok(value)) => mcp::result(&id, mcp::tool_result(&value, false)),
                Some(Err(value)) => mcp::result(&id, mcp::tool_result(&value, true)),
                None => mcp::error(&id, -32602, "unknown_tool"),
            }
        }
        _ => mcp::error(&id, -32601, "method_not_found"),
    };
    Json(reply).into_response()
}

async fn call(runtime: Runtime, name: &str, args: Value, host: &str) -> Option<Outcome> {
    let link = |project: &str| format!("http://{host}/?project={project}");
    Some(match name {
        "import_videos" => import(runtime, args, link).await,
        "transcribe" => transcribe(runtime, args).await,
        "get_project" => project_state(runtime, args, link).await,
        "get_transcript" => transcript(runtime, args).await,
        "set_clips" => set_clips(runtime, args).await,
        "export_clips" => export(runtime, args).await,
        _ => return None,
    })
}

fn project_arg(args: &Value) -> Result<String, Value> {
    args["project"]
        .as_str()
        .filter(|p| editorkrab::jobs::identifier(p))
        .map(str::to_owned)
        .ok_or_else(|| refuse("project_required"))
}

async fn load(
    runtime: &Runtime,
    project: &str,
) -> Result<(editorkrab::drafts::Draft, Vec<Job>), Value> {
    let id = project.to_owned();
    database(runtime.clone(), move |store| {
        Ok((
            store.read(&id)?.ok_or(Error::NotFound)?,
            store.list_jobs(&id)?,
        ))
    })
    .await
    .map_err(api)
}

async fn store(
    runtime: &Runtime,
    project: &str,
    revision: i64,
    document: Value,
) -> Result<i64, Value> {
    let id = project.to_owned();
    let operation = uuid::Uuid::new_v4().to_string();
    database(runtime.clone(), move |store| {
        store.save(&id, &operation, revision, &document)
    })
    .await
    .map_err(api)
}

async fn import(runtime: Runtime, args: Value, link: impl Fn(&str) -> String) -> Outcome {
    preload_asr(&runtime);
    let paths: Vec<PathBuf> = args["paths"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|p| p.as_str().map(PathBuf::from))
        .collect();
    if paths.is_empty() || paths.iter().any(|p| !p.is_absolute()) {
        return Err(refuse("absolute_paths_required"));
    }
    let project = match args.get("project") {
        Some(_) => project_arg(&args)?,
        None => uuid::Uuid::new_v4().to_string(),
    };
    let selection = tokio::task::spawn_blocking(move || editorkrab::sources::collect(paths))
        .await
        .map_err(|_| refuse("storage_worker_unavailable"))?
        .map_err(|e| api(source_failure(e)))?;
    if selection.files.is_empty() {
        return Err(json!({"error":"no_supported_files","skipped":selection.skipped}));
    }
    let id = project.clone();
    let current = database(runtime.clone(), move |store| store.read(&id))
        .await
        .map_err(api)?;
    let (revision, mut document) = match current {
        Some(draft) => (draft.revision, draft.document),
        // the page's own empty project
        None => (
            0,
            json!({"flow_version":2,"headline_items":[],"step":1,"title":"","language":"auto","files":[],"plan":"","style":"bold16"}),
        ),
    };
    if let Some(title) = args["title"].as_str() {
        if title.chars().count() > 120 {
            return Err(refuse("title_too_long"));
        }
        document["title"] = json!(title);
    }
    if let Some(language) = args["language"].as_str() {
        if !["auto", "th", "en"].contains(&language) {
            return Err(refuse("language_unsupported"));
        }
        document["language"] = json!(language);
    }
    let files = document["files"]
        .as_array_mut()
        .ok_or_else(|| refuse("draft_invalid"))?;
    // same rule as the native picker on the page: one entry per path, refreshed when the file changed
    for source in &selection.files {
        let entry = json!(source);
        match files
            .iter()
            .position(|f| f["path"].as_str() == Some(&source.path))
        {
            Some(at)
                if files[at]["size"] == entry["size"]
                    && files[at]["modified_ns"] == entry["modified_ns"] => {}
            Some(at) => files[at] = entry,
            None => files.push(entry),
        }
    }
    if files.len() > 1000 {
        return Err(refuse("source_limit_exceeded"));
    }
    let listed: Vec<Value> = files
        .iter()
        .map(|f| json!({"source_id":f["source_id"],"name":f["name"]}))
        .collect();
    let revision = store(&runtime, &project, revision, document).await?;
    Ok(json!({
        "project": project,
        "revision": revision,
        "files": listed,
        "skipped": selection.skipped,
        "open_in_app": link(&project),
        "next": "transcribe"
    }))
}

fn probing(jobs: &[Job], path: &str) -> bool {
    jobs.iter().any(|j| {
        matches!(&j.task, Task::Probe { path: p, .. } if p == path)
            && matches!(j.status.as_str(), "queued" | "running")
    })
}

async fn transcribe(runtime: Runtime, args: Value) -> Outcome {
    let project = project_arg(&args)?;
    let asr = runtime.asr().ok_or_else(|| {
        json!({"error":"transcription_not_configured","hint":"Open the app at step 1 and download the speech engine once."})
    })?;
    let wanted: Option<Vec<&str>> = args["source_ids"]
        .as_array()
        .map(|ids| ids.iter().filter_map(Value::as_str).collect());
    // a just-imported file is still being measured; transcription needs its length
    let mut waited = 0;
    let (draft, _) = loop {
        let (draft, jobs) = load(&runtime, &project).await?;
        let busy = draft.document["files"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|f| f["path"].as_str())
            .any(|path| probing(&jobs, path));
        if !busy || waited >= 120 {
            break (draft, jobs);
        }
        waited += 1;
        tokio::time::sleep(Duration::from_millis(250)).await;
    };
    let mut started = Vec::new();
    for file in draft.document["files"].as_array().into_iter().flatten() {
        let Some(source_id) = file["source_id"].as_str() else {
            continue;
        };
        if wanted.as_ref().is_some_and(|ids| !ids.contains(&source_id)) {
            continue;
        }
        let request = StartRequest {
            request_id: uuid::Uuid::new_v4().to_string(),
            revision: draft.revision,
            source_id: source_id.to_owned(),
            settings: asr.1.clone(),
        };
        let outcome =
            start_transcription(State(runtime.clone()), Path(project.clone()), Json(request)).await;
        started.push(match outcome {
            Ok(Json(jobs)) => json!({"source_id":source_id,"chunks":jobs.len()}),
            Err(error) => json!({"source_id":source_id,"error":api(error)["error"]}),
        });
    }
    if started.is_empty() {
        return Err(refuse("no_matching_files"));
    }
    let failed = started.iter().all(|s| s.get("error").is_some());
    let answer = json!({"project":project,"started":started,"next":"poll get_project until transcription.state is done"});
    if failed { Err(answer) } else { Ok(answer) }
}

/// Progress of the newest transcription run of one file.
fn transcription_state(jobs: &[Job], source_id: &str) -> Value {
    let runs: Vec<&Job> = jobs
        .iter()
        .filter(|j| matches!(&j.task, Task::Transcribe { context, .. } if context.source.id == source_id))
        .collect();
    let Some(latest) = runs.iter().max_by_key(|j| (j.created_at, j.position)) else {
        return json!({"state":"not_started"});
    };
    let Task::Transcribe {
        context: newest, ..
    } = &latest.task
    else {
        unreachable!()
    };
    let run: Vec<&&Job> = runs
        .iter()
        .filter(|j| matches!(&j.task, Task::Transcribe { context, .. } if context.run_id == newest.run_id))
        .collect();
    let count = |s: &[&str]| {
        run.iter()
            .filter(|j| s.contains(&j.status.as_str()))
            .count()
    };
    let (done, failed) = (count(&["succeeded"]), count(&["failed", "cancelled"]));
    let state = if done == run.len() {
        "done"
    } else if done + failed == run.len() {
        "failed"
    } else {
        "running"
    };
    json!({"state":state,"chunks":run.len(),"done":done,"failed":failed})
}

async fn project_state(runtime: Runtime, args: Value, link: impl Fn(&str) -> String) -> Outcome {
    let project = project_arg(&args)?;
    let (draft, jobs) = load(&runtime, &project).await?;
    let files: Vec<Value> = draft.document["files"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|f| {
            let path = f["path"].as_str().unwrap_or("");
            let probe = jobs
                .iter()
                .filter(|j| matches!(&j.task, Task::Probe { path: p, .. } if p == path))
                .max_by_key(|j| j.created_at);
            json!({
                "source_id": f["source_id"],
                "name": f["name"],
                "duration_s": probe.and_then(|j| j.result.as_ref()).map(|r| r["duration_s"].clone()),
                "probe_error": probe.and_then(|j| j.error.clone()),
                "transcription": f["source_id"].as_str().map(|id| transcription_state(&jobs, id)),
            })
        })
        .collect();
    let clips = editorkrab::export::plan(&draft.document, &jobs)
        .map(|stories| {
            stories
                .iter()
                .map(|s| json!({"title":s.title,"duration_s":s.duration_ms() as f64 / 1000.0}))
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let Json(export) = export_status(State(runtime.clone()), Path(project.clone())).await;
    Ok(json!({
        "project": project,
        "title": draft.document["title"],
        "language": draft.document["language"],
        "revision": draft.revision,
        "transcription_ready": runtime.asr().is_some(),
        "files": files,
        "clips": clips,
        "export": export,
        "open_in_app": link(&project),
    }))
}

async fn transcript(runtime: Runtime, args: Value) -> Outcome {
    let project = project_arg(&args)?;
    let (draft, jobs) = load(&runtime, &project).await?;
    let lines = editorkrab::export::transcript(&draft.document, &jobs);
    let sources: Vec<Value> = draft.document["files"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|f| {
            let segments = lines
                .iter()
                .find(|(path, _)| Some(path.as_path()) == f["path"].as_str().map(std::path::Path::new))
                .map(|(_, l)| {
                    l.iter()
                        .map(|(id, start_ms, end_ms, text)| json!({"id":id,"start_ms":start_ms,"end_ms":end_ms,"text":text}))
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            let state = f["source_id"].as_str().map(|id| transcription_state(&jobs, id));
            json!({"source_id":f["source_id"],"name":f["name"],"transcription":state,"segments":segments})
        })
        .collect();
    if sources
        .iter()
        .all(|s| s["segments"].as_array().is_none_or(Vec::is_empty))
    {
        return Err(
            json!({"error":"transcript_not_ready","next":"transcribe, then poll get_project"}),
        );
    }
    Ok(json!({
        "project": project,
        "language": draft.document["language"],
        "sources": sources,
        "instructions": "Group segments into stories that each make sense alone. Use only these segment_ids, keep each story's ending complete, \
    take each story's segments from one file only, never invent times, and do not treat different files as synced cameras.     Mark who says each segment in speakers: \"host\" for the one asking or hosting, \"guest\" for the one answering (\"guest\" for all when one person talks). Then call set_clips."
    }))
}

async fn set_clips(runtime: Runtime, args: Value) -> Outcome {
    let project = project_arg(&args)?;
    let clips = args["clips"]
        .as_array()
        .filter(|c| !c.is_empty())
        .ok_or_else(|| refuse("clips_required"))?;
    let stories: Vec<Value> = clips
        .iter()
        .enumerate()
        .map(|(n, c)| json!({"id":format!("story-{}", n + 1),"title":c["title"],"segment_ids":c["segment_ids"]}))
        .collect();
    // who says each line, as step 2 asks the browser AI: the CapCut camera frames that person
    let speakers = match &args["speakers"] {
        Value::Null => json!({}),
        Value::Object(m) if m.values().all(|r| r == "host" || r == "guest") => args["speakers"].clone(),
        _ => return Err(refuse("speakers_must_be_host_or_guest")),
    };
    let (draft, jobs) = load(&runtime, &project).await?;
    let mut document = draft.document;
    // the agent skips step 4, so speech subtitles are on unless the person already chose otherwise there
    if document.get("text_roles").is_none() {
        document["text_roles"] = json!(["speech"]);
    }
    // stored exactly as a reply pasted at step 3, so the page shows and edits it the same way
    document["plan"] = json!(
        serde_json::to_string_pretty(
            &json!({"schema_version":1,"project_id":project,"stories":stories,"speakers":speakers})
        )
        .unwrap_or_default()
    );
    let planned = editorkrab::export::plan(&document, &jobs).map_err(|e| refuse(&e))?;
    let revision = store(&runtime, &project, draft.revision, document).await?;
    Ok(json!({
        "project": project,
        "revision": revision,
        "clips": planned.iter().map(|s| json!({"title":s.title,"duration_s":s.duration_ms() as f64 / 1000.0})).collect::<Vec<_>>(),
        "next": "export_clips"
    }))
}

async fn export(runtime: Runtime, args: Value) -> Outcome {
    let project = project_arg(&args)?;
    match start_export(State(runtime), Path(project.clone())).await {
        Ok(Json(status)) => Ok(
            json!({"project":project,"export":status,"next":"poll get_project until export.state is done"}),
        ),
        Err(error) => Err(api(error)),
    }
}
