//! Whole-source planning and durable aliases keep a repeated click on the same chunks.
use crate::{
    drafts::{Error, Store},
    jobs::{self, BatchRequest, Item, Job, Task},
    native_asr::Settings,
    transcription::Context,
};
use rusqlite::{OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StartRequest {
    pub request_id: String,
    pub revision: i64,
    pub source_id: String,
    pub settings: Settings,
}

/// whisper.cpp reads 5-minute pieces.
pub const CHUNK_MS: u64 = 300_000;
/// The CTranslate2 sidecar cuts 15 s windows and decodes the even ones, then the odd ones, 8 at a time. A
/// 4-minute piece is 16 windows, two full batches; a 5-minute one leaves its batches part-empty (11 min clip:
/// 51 s instead of the 41 s the whole file takes).
pub const CT2_CHUNK_MS: u64 = 240_000;

pub fn ranges(duration_ms: u64) -> Result<Vec<(u64, u64)>, Error> {
    ranges_every(duration_ms, CHUNK_MS)
}

pub fn ranges_every(duration_ms: u64, every: u64) -> Result<Vec<(u64, u64)>, Error> {
    if duration_ms == 0 || every == 0 {
        return Err(Error::InvalidInput);
    }
    if duration_ms > 30_000_000 {
        return Err(Error::TranscriptionLimit);
    }
    Ok((0..duration_ms)
        .step_by(every as usize)
        .map(|start| (start, (start + every).min(duration_ms)))
        .collect())
}

fn same_chunk(a: &Task, b: &Task) -> bool {
    match (a, b) {
        (
            Task::Transcribe {
                path: a,
                context: ca,
                settings: sa,
            },
            Task::Transcribe {
                path: b,
                context: cb,
                settings: sb,
            },
        ) => {
            a == b
                && sa == sb
                && ca.source == cb.source
                && ca.language == cb.language
                && ca.start_ms == cb.start_ms
                && ca.end_ms == cb.end_ms
        }
        _ => false,
    }
}

fn selected(
    document: &Value,
    file: &Value,
    request: &StartRequest,
) -> Result<(String, Context), Error> {
    let path = file["path"].as_str().ok_or(Error::InvalidInput)?.to_owned();
    let context = Context {
        run_id: request.request_id.clone(),
        source: crate::sources::Identity {
            id: request.source_id.clone(),
            size: file["size"].as_u64().ok_or(Error::InvalidInput)?,
            modified_ns: file["modified_ns"]
                .as_str()
                .ok_or(Error::InvalidInput)?
                .into(),
        },
        language: document["language"]
            .as_str()
            .ok_or(Error::InvalidInput)?
            .into(),
        start_ms: 0,
        end_ms: 1,
    };
    context.validate().map_err(|_| Error::InvalidInput)?;
    if path.trim().is_empty() || path.contains('\0') {
        return Err(Error::InvalidInput);
    }
    Ok((path, context))
}

impl Store {
    pub fn transcription_source_path(
        &mut self,
        project: &str,
        request: &StartRequest,
    ) -> Result<std::path::PathBuf, Error> {
        if !jobs::identifier(project)
            || !jobs::identifier(&request.request_id)
            || !jobs::identifier(&request.source_id)
            || request.revision < 1
            || !request.settings.valid()
        {
            return Err(Error::InvalidInput);
        }
        let tx = self.0.transaction()?;
        let (actual, document): (i64, String) = tx
            .query_row(
                "SELECT revision,document FROM drafts WHERE project_id=?1",
                [project],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?
            .ok_or(Error::NotFound)?;
        let known:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM transcription_requests WHERE project_id=?1 AND request_id=?2)",params![project,request.request_id],|r|r.get(0))?;
        if !known && actual != request.revision {
            return Err(Error::Conflict {
                expected: request.revision,
                actual,
            });
        }
        let document: Value = serde_json::from_str(&document)?;
        let file = document["files"]
            .as_array()
            .and_then(|files| {
                files
                    .iter()
                    .find(|f| f["source_id"].as_str() == Some(&request.source_id))
            })
            .ok_or(Error::InvalidInput)?;
        let (path, context) = selected(&document, file, request)?;
        ranges(jobs::validate_transcription_source(
            &tx, project, &document, &path, &context,
        )?)?;
        Ok(path.into())
    }
    pub fn start_transcription(
        &mut self,
        project: &str,
        request: &StartRequest,
    ) -> Result<Vec<Job>, Error> {
        self.start_transcription_checked(project, request, None, CHUNK_MS)
    }
    pub fn start_transcription_checked(
        &mut self,
        project: &str,
        request: &StartRequest,
        actual_source: Option<&crate::native_asr::Artifact>,
        chunk_ms: u64,
    ) -> Result<Vec<Job>, Error> {
        if !jobs::identifier(project)
            || !jobs::identifier(&request.request_id)
            || !jobs::identifier(&request.source_id)
            || request.revision < 1
            || !request.settings.valid()
        {
            return Err(Error::InvalidInput);
        }
        let tx = self
            .0
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let (actual, document): (i64, String) = tx
            .query_row(
                "SELECT revision,document FROM drafts WHERE project_id=?1",
                [project],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?
            .ok_or(Error::NotFound)?;
        let document: Value = serde_json::from_str(&document)?;
        let file = document["files"]
            .as_array()
            .and_then(|files| {
                files
                    .iter()
                    .find(|f| f["source_id"].as_str() == Some(&request.source_id))
            })
            .ok_or(Error::InvalidInput)?;
        let text = serde_json::to_string(
            &serde_json::json!({"request":request,"source":file,"language":document["language"],"source_sha256":actual_source.map(|source|&source.sha256)}),
        )?;
        let prior:Option<(String,String)>=tx.query_row("SELECT request,job_ids FROM transcription_requests WHERE project_id=?1 AND request_id=?2",params![project,request.request_id],|r|Ok((r.get(0)?,r.get(1)?))).optional()?;
        let ids: Vec<String> = if let Some((prior, ids)) = prior {
            if prior != text {
                return Err(Error::OperationConflict);
            }
            serde_json::from_str(&ids)?
        } else {
            if actual != request.revision {
                return Err(Error::Conflict {
                    expected: request.revision,
                    actual,
                });
            }
            let (path, mut context) = selected(&document, file, request)?;
            let duration =
                jobs::validate_transcription_source(&tx, project, &document, &path, &context)?;
            let planned = ranges_every(duration, chunk_ms)?;
            let existing=tx.prepare("SELECT id,task FROM jobs WHERE project_id=?1 ORDER BY CASE status WHEN 'succeeded' THEN 0 WHEN 'running' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END,rowid")?
                .query_map([project],|r|Ok((r.get::<_,String>(0)?,r.get::<_,String>(1)?)))?.collect::<Result<Vec<_>,_>>()?
                .into_iter().map(|(id,task)|Ok((id,serde_json::from_str::<Task>(&task)?))).collect::<Result<Vec<_>,Error>>()?;
            let mut ids = Vec::new();
            let mut missing = Vec::new();
            for (index, (start, end)) in planned.into_iter().enumerate() {
                context.start_ms = start;
                context.end_ms = end;
                let task = Task::Transcribe {
                    path: path.clone(),
                    context: context.clone(),
                    settings: request.settings.clone(),
                };
                if let Some((id, _)) = existing.iter().find(|(_, old)| same_chunk(old, &task)) {
                    ids.push(id.clone());
                } else {
                    let key = format!("chunk-{index}");
                    ids.push(format!("{project}.{}.{key}", request.request_id));
                    missing.push(Item { key, task });
                }
            }
            if !missing.is_empty() {
                let occupied:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM job_batches WHERE project_id=?1 AND request_id=?2)",params![project,request.request_id],|r|r.get(0))?;
                if occupied {
                    return Err(Error::OperationConflict);
                }
                jobs::insert_batch(
                    &tx,
                    project,
                    &BatchRequest {
                        request_id: request.request_id.clone(),
                        revision: request.revision,
                        items: missing,
                    },
                )?;
            }
            tx.execute("INSERT INTO transcription_requests(project_id,request_id,request,job_ids) VALUES(?1,?2,?3,?4)",params![project,request.request_id,text,serde_json::to_string(&ids)?])?;
            ids
        };
        if let Some(actual) = actual_source {
            for id in &ids {
                let (task, result): (String, Option<String>) =
                    tx.query_row("SELECT task,result FROM jobs WHERE id=?1", [id], |r| {
                        Ok((r.get(0)?, r.get(1)?))
                    })?;
                let Task::Transcribe { path, context, .. } = serde_json::from_str(&task)? else {
                    return Err(Error::InvalidInput);
                };
                if std::path::Path::new(&path)
                    .canonicalize()
                    .map_err(|_| Error::InvalidInput)?
                    != actual.path
                    || actual.size != context.source.size
                    || actual.modified_ns != context.source.modified_ns
                {
                    return Err(Error::SourceChanged);
                }
                if let Some(result) = result {
                    let result: Value = serde_json::from_str(&result)?;
                    if result["source"]["sha256"].as_str() != Some(&actual.sha256) {
                        return Err(Error::SourceChanged);
                    }
                }
            }
        }
        tx.commit()?;
        ids.iter().map(|id| self.job(id)).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn settings() -> Settings {
        let artifact = crate::native_asr::Artifact {
            path: std::env::temp_dir().join("fixture"),
            size: 1,
            modified_ns: "1".into(),
            sha256: "a".repeat(64),
        };
        Settings {
            engine: artifact.clone(),
            model: artifact.clone(),
            ffmpeg: artifact,
            threads: 1,
        }
    }
    fn source_store(duration: f64) -> Store {
        let mut s = Store::open(std::path::Path::new(":memory:")).unwrap();
        s.initialize_jobs().unwrap();
        s.save("project","initial",0,&json!({"language":"th","files":[{"source_id":"one","path":"one.wav","size":10,"modified_ns":"1"}]})).unwrap();
        let probe = s.claim_job().unwrap().unwrap();
        s.finish_job(
            &probe.id,
            Ok(json!({"duration_s":duration,"streams":[{"codec_type":"audio"}]})),
        )
        .unwrap();
        s
    }
    #[test]
    fn planning_is_contiguous_and_bounded() {
        assert_eq!(
            ranges(600001).unwrap(),
            vec![(0, 300000), (300000, 600000), (600000, 600001)]
        );
        assert_eq!(ranges(30000000).unwrap().len(), 100);
        assert!(matches!(ranges(30000001), Err(Error::TranscriptionLimit)));
        assert!(ranges(0).is_err());
        // the CT2 sidecar gets 4-minute pieces, inside the 5-minute limit every job context keeps
        assert_eq!(
            ranges_every(657_600, CT2_CHUNK_MS).unwrap(),
            vec![(0, 240_000), (240_000, 480_000), (480_000, 657_600)]
        );
    }
    #[test]
    fn requests_reuse_chunks_and_preserve_partial_completion() {
        let mut s = source_store(600.001);
        let mut request = StartRequest {
            request_id: "first".into(),
            revision: 1,
            source_id: "one".into(),
            settings: settings(),
        };
        let first = s.start_transcription("project", &request).unwrap();
        assert_eq!(first.len(), 3);
        let job = s.claim_job().unwrap().unwrap();
        s.finish_job(&job.id, Ok(json!({"retained":true}))).unwrap();
        let cancelled = s.cancel_job(&first[1].id).unwrap();
        assert_eq!(cancelled.status, "cancelled");
        request.request_id = "new".into();
        let new = s.start_transcription("project", &request).unwrap();
        assert_eq!(
            new.iter().map(|j| &j.id).collect::<Vec<_>>(),
            first.iter().map(|j| &j.id).collect::<Vec<_>>()
        );
        assert_eq!(new[0].result, Some(json!({"retained":true})));
        assert_eq!(new[1].status, "cancelled");
        request.settings.threads = 2;
        assert!(matches!(
            s.start_transcription("project", &request),
            Err(Error::OperationConflict)
        ));
        request.request_id = "wrong-revision".into();
        request.revision = 2;
        assert!(matches!(
            s.start_transcription("project", &request),
            Err(Error::Conflict { .. })
        ));
        request.revision = 1;
        request.source_id = "missing".into();
        assert!(s.start_transcription("project", &request).is_err());
        assert_eq!(s.list_jobs("project").unwrap().len(), 4);
    }
    #[test]
    fn oversized_source_never_partially_enqueues() {
        let mut s = source_store(30000.001);
        let request = StartRequest {
            request_id: "big".into(),
            revision: 1,
            source_id: "one".into(),
            settings: settings(),
        };
        assert!(matches!(
            s.start_transcription("project", &request),
            Err(Error::TranscriptionLimit)
        ));
        assert!(matches!(
            s.transcription_source_path("project", &request),
            Err(Error::TranscriptionLimit)
        ));
        assert_eq!(s.list_jobs("project").unwrap().len(), 1);
    }
    #[test]
    fn existing_verified_chunk_is_reused_while_missing_chunks_resume_after_recovery() {
        let mut s = source_store(600.001);
        let context = Context {
            run_id: "partial".into(),
            source: crate::sources::Identity {
                id: "one".into(),
                size: 10,
                modified_ns: "1".into(),
            },
            start_ms: 0,
            end_ms: 300000,
            language: "th".into(),
        };
        let prior = s
            .create_batch(
                "project",
                &BatchRequest {
                    request_id: "partial".into(),
                    revision: 1,
                    items: vec![Item {
                        key: "chunk".into(),
                        task: Task::Transcribe {
                            path: "one.wav".into(),
                            context,
                            settings: settings(),
                        },
                    }],
                },
            )
            .unwrap();
        let running = s.claim_job().unwrap().unwrap();
        s.finish_job(&running.id, Ok(json!({"retained":true})))
            .unwrap();
        let request = StartRequest {
            request_id: "whole".into(),
            revision: 1,
            source_id: "one".into(),
            settings: settings(),
        };
        let all = s.start_transcription("project", &request).unwrap();
        assert_eq!(all.len(), 3);
        assert_eq!(all[0].id, prior[0].id);
        assert_eq!(s.list_jobs("project").unwrap().len(), 4);
        let running = s.claim_job().unwrap().unwrap();
        assert_eq!(s.recover_jobs().unwrap(), 1);
        let replay = s.start_transcription("project", &request).unwrap();
        assert_eq!(replay[0].status, "succeeded");
        assert_eq!(replay[1].id, running.id);
        assert_eq!(replay[1].status, "queued");
        assert_eq!(replay[2].status, "queued");
        let mut changed = s.read("project").unwrap().unwrap().document;
        changed["language"] = json!("en");
        s.save("project", "language", 1, &changed).unwrap();
        assert!(matches!(
            s.start_transcription("project", &request),
            Err(Error::OperationConflict)
        ));
    }
}
