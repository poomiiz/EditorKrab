//! Copy transcript provenance with its draft, in one idempotent transaction.
use crate::{
    drafts::{Draft, Error, Store},
    jobs::{self, BatchRequest, Item, Job, Task},
};
use rusqlite::{OptionalExtension, Transaction, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeSet, HashMap, HashSet};

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CopyRequest {
    pub operation_id: String,
    pub expected_revision: i64,
    pub document: Value,
    pub source_project: Option<String>,
}

fn selected(document: &Value, job: &Job) -> bool {
    let Task::Transcribe { path, context, .. } = &job.task else {
        return false;
    };
    document["language"].as_str() == Some(&context.language)
        && document["files"].as_array().is_some_and(|files| {
            files.iter().any(|file| {
                file["path"].as_str() == Some(path)
                    && file["source_id"].as_str() == Some(&context.source.id)
                    && file["size"].as_u64() == Some(context.source.size)
                    && file["modified_ns"].as_str() == Some(&context.source.modified_ns)
            })
        })
}

fn clone_job(
    tx: &Transaction<'_>,
    target: &str,
    revision: i64,
    job: &Job,
) -> Result<String, Error> {
    if job.project_id == target {
        return Ok(job.id.clone());
    }
    let request_id = format!("copy-{:x}", Sha256::digest(job.id.as_bytes()));
    let batch_id = format!("{target}.{request_id}");
    let id = format!("{batch_id}.transcript");
    let old = tx
        .query_row(
            &format!("SELECT {} FROM jobs WHERE id=?1", jobs::COLUMNS),
            [&id],
            jobs::read_job,
        )
        .optional()?;
    if let Some(old) = old {
        if old.status != "succeeded"
            || old.project_id != target
            || old.task != job.task
            || old.result != job.result
        {
            return Err(Error::OperationConflict);
        }
        return Ok(id);
    }
    let batch = BatchRequest {
        request_id: request_id.clone(),
        revision,
        items: vec![Item {
            key: "transcript".into(),
            task: job.task.clone(),
        }],
    };
    tx.execute(
        "INSERT INTO job_batches(id,project_id,request_id,request) VALUES(?1,?2,?3,?4)",
        params![batch_id, target, request_id, serde_json::to_string(&batch)?],
    )?;
    tx.execute("INSERT INTO jobs(id,batch_id,project_id,position,status,step,attempt,cancel_requested,task,result,created_at,started_at,finished_at,heartbeat_at) VALUES(?1,?2,?3,?4,'succeeded','succeeded',?5,0,?6,?7,unixepoch(),?8,?9,?10)",params![id,batch_id,target,job.position,job.attempt,serde_json::to_string(&job.task)?,job.result.as_ref().map(serde_json::to_string).transpose()?,job.started_at,job.finished_at,job.heartbeat_at])?;
    jobs::event(tx, &id)?;
    Ok(id)
}

impl Store {
    pub fn copy_draft(&mut self, target: &str, request: &CopyRequest) -> Result<Draft, Error> {
        if !jobs::identifier(target)
            || !jobs::identifier(&request.operation_id)
            || request.expected_revision < 0
            || !crate::styles::valid(&request.document)
            || request
                .source_project
                .as_deref()
                .is_some_and(|p| !jobs::identifier(p))
        {
            return Err(Error::InvalidInput);
        }
        let text = serde_json::to_string(request)?;
        let tx = self
            .0
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let prior:Option<(String,i64,String)>=tx.query_row("SELECT request,revision,document FROM draft_copies WHERE project_id=?1 AND operation_id=?2",params![target,request.operation_id],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?;
        if let Some((prior, revision, document)) = prior {
            return if prior == text {
                Ok(Draft {
                    project_id: target.into(),
                    revision,
                    document: serde_json::from_str(&document)?,
                })
            } else {
                Err(Error::OperationConflict)
            };
        }
        let occupied: bool = tx.query_row(
            "SELECT EXISTS(SELECT 1 FROM draft_operations WHERE project_id=?1 AND operation_id=?2)",
            params![target, request.operation_id],
            |r| r.get(0),
        )?;
        if occupied {
            return Err(Error::OperationConflict);
        }
        let actual: i64 = tx
            .query_row(
                "SELECT revision FROM drafts WHERE project_id=?1",
                [target],
                |r| r.get(0),
            )
            .optional()?
            .unwrap_or(0);
        if actual != request.expected_revision {
            return Err(Error::Conflict {
                expected: request.expected_revision,
                actual,
            });
        }
        let revision = actual.checked_add(1).ok_or(Error::InvalidInput)?;
        let mut origins = BTreeSet::new();
        if let Some(origin) = &request.source_project {
            let exists: bool = tx.query_row(
                "SELECT EXISTS(SELECT 1 FROM drafts WHERE project_id=?1)",
                [origin],
                |r| r.get(0),
            )?;
            if exists {
                origins.insert(origin.clone());
            }
        }
        let mut historical = HashSet::new();
        for edit in crate::transcript_edits::edits(&request.document)? {
            let job = crate::transcript_edits::referenced_job(&tx, &edit, None)?;
            origins.insert(job.project_id);
            historical.insert(job.id);
        }
        let mut jobs = Vec::new();
        let mut known = HashSet::new();
        for origin in origins {
            let completed=tx.prepare(&format!("SELECT {} FROM jobs WHERE project_id=?1 AND status='succeeded' AND json_extract(task,'$.kind')='transcribe' ORDER BY rowid",jobs::COLUMNS))?.query_map([origin],jobs::read_job)?.collect::<Result<Vec<_>,_>>()?;
            for job in completed {
                if (historical.contains(&job.id) || selected(&request.document, &job))
                    && known.insert(job.id.clone())
                {
                    jobs.push(job);
                }
            }
        }
        let mut document = request.document.clone();
        // The provisional row and every copied job roll back if any later binding/probe fails.
        tx.execute("INSERT INTO drafts(project_id,revision,document) VALUES(?1,?2,?3) ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision,document=excluded.document",params![target,revision,serde_json::to_string(&document)?])?;
        let mut mapping = HashMap::new();
        for job in jobs {
            mapping.insert(job.id.clone(), clone_job(&tx, target, revision, &job)?);
        }
        if let Some(edits) = document
            .get_mut("transcript_edits")
            .and_then(Value::as_array_mut)
        {
            for edit in edits {
                let old = edit["job_id"].as_str().ok_or(Error::InvalidInput)?;
                edit["job_id"] =
                    Value::String(mapping.get(old).ok_or(Error::InvalidInput)?.clone());
            }
        }
        crate::transcript_edits::validate_saved(&tx, target, &document)?;
        let saved = serde_json::to_string(&document)?;
        tx.execute(
            "UPDATE drafts SET document=?2 WHERE project_id=?1",
            params![target, saved],
        )?;
        tx.execute("INSERT INTO draft_operations(project_id,operation_id,base_revision,revision,document) VALUES(?1,?2,?3,?4,?5)",params![target,request.operation_id,request.expected_revision,revision,saved])?;
        tx.execute("INSERT INTO draft_copies(project_id,operation_id,request,revision,document) VALUES(?1,?2,?3,?4,?5)",params![target,request.operation_id,text,revision,saved])?;
        jobs::source_probes(&tx, target, revision, &document)?;
        tx.commit()?;
        Ok(Draft {
            project_id: target.into(),
            revision,
            document,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn settings() -> crate::native_asr::Settings {
        let artifact = crate::native_asr::Artifact {
            path: std::env::temp_dir().join("artifact"),
            size: 1,
            modified_ns: "1".into(),
            sha256: "a".repeat(64),
        };
        crate::native_asr::Settings {
            engine: artifact.clone(),
            model: artifact.clone(),
            ffmpeg: artifact,
            threads: 1,
        }
    }
    fn fixture() -> (Store, Value, Vec<Job>) {
        let mut s = Store::open(std::path::Path::new(":memory:")).unwrap();
        let active = json!({"source_id":"active","path":"active.wav","size":10,"modified_ns":"1"});
        let gone = json!({"source_id":"gone","path":"gone.wav","size":10,"modified_ns":"1"});
        s.save(
            "origin",
            "initial",
            0,
            &json!({"files":[active.clone(),gone],"language":"auto"}),
        )
        .unwrap();
        while let Some(job) = s.claim_job().unwrap() {
            s.finish_job(
                &job.id,
                Ok(json!({"duration_s":600.0,"streams":[{"codec_type":"audio"}]})),
            )
            .unwrap();
        }
        for source_id in ["active", "gone"] {
            s.start_transcription(
                "origin",
                &crate::transcription_jobs::StartRequest {
                    request_id: format!("run-{source_id}"),
                    revision: 1,
                    source_id: source_id.into(),
                    settings: settings(),
                },
            )
            .unwrap();
        }
        while let Some(job) = s.claim_job().unwrap() {
            let Task::Transcribe { context, .. } = &job.task else {
                panic!()
            };
            let result = json!({"source":{"sha256":"a".repeat(64)},"transcript":{"context":context,"segments":[{"id":format!("{}:{}:{}:s0",context.source.id,context.run_id,context.start_ms),"text":format!("original {} {}",context.source.id,context.start_ms),"start_ms":context.start_ms,"end_ms":context.start_ms+1000}]}});
            s.finish_job(&job.id, Ok(result)).unwrap();
        }
        let jobs = s
            .list_jobs("origin")
            .unwrap()
            .into_iter()
            .filter(|j| matches!(j.task, Task::Transcribe { .. }))
            .collect::<Vec<_>>();
        let historical = &jobs[2];
        let segment = &historical.result.as_ref().unwrap()["transcript"]["segments"][0];
        let document = json!({"files":[active],"language":"auto","transcript_edits":[{"job_id":historical.id,"segment_id":segment["id"],"source_sha256":"a".repeat(64),"original_text":segment["text"],"text":"corrected"}]});
        s.save("origin", "remove-source", 1, &document).unwrap();
        (s, document, jobs)
    }
    #[test]
    fn copy_preserves_full_current_transcript_and_removed_source_correction_atomically() {
        let (mut s, document, original) = fixture();
        let request = CopyRequest {
            operation_id: "copy".into(),
            expected_revision: 0,
            document: document.clone(),
            source_project: Some("origin".into()),
        };
        let copied = s.copy_draft("fork", &request).unwrap();
        assert_eq!(copied.revision, 1);
        assert_ne!(
            copied.document["transcript_edits"][0]["job_id"],
            document["transcript_edits"][0]["job_id"]
        );
        let jobs = s.list_jobs("fork").unwrap();
        let completed = jobs
            .iter()
            .filter(|j| matches!(j.task, Task::Transcribe { .. }))
            .collect::<Vec<_>>();
        assert_eq!(completed.len(), 3);
        for original in original.iter().take(3) {
            let clone = completed.iter().find(|j| j.task == original.task).unwrap();
            assert_eq!(clone.status, "succeeded");
            assert_eq!(clone.result, original.result);
        }
        s.save("fork", "ordinary-save", 1, &copied.document)
            .unwrap();
        let replay = s.copy_draft("fork", &request).unwrap();
        assert_eq!(replay, copied);
        assert_eq!(s.read("fork").unwrap().unwrap().revision, 2);
        assert_eq!(s.list_jobs("fork").unwrap().len(), jobs.len());
        let mut changed = request.clone();
        changed.document["note"] = json!("changed");
        assert!(matches!(
            s.copy_draft("fork", &changed),
            Err(Error::OperationConflict)
        ));
        let inferred = s
            .copy_draft(
                "legacy-import",
                &CopyRequest {
                    source_project: None,
                    ..request
                },
            )
            .unwrap();
        assert_eq!(
            s.list_jobs("legacy-import")
                .unwrap()
                .iter()
                .filter(|j| matches!(j.task, Task::Transcribe { .. }))
                .count(),
            3
        );
        assert_eq!(
            inferred.document["transcript_edits"][0]["text"],
            json!("corrected")
        );
    }
    #[test]
    fn forged_bindings_and_failed_cas_leave_no_partial_draft_or_clone() {
        let (mut s, document, jobs) = fixture();
        for (index, (field, value)) in [
            ("original_text", json!("invented")),
            ("source_sha256", json!("b".repeat(64))),
            ("job_id", json!(jobs[0].id)),
            ("job_id", json!("missing")),
        ]
        .into_iter()
        .enumerate()
        {
            let mut bad = document.clone();
            bad["transcript_edits"][0][field] = value;
            let target = format!("bad-{index}");
            assert!(matches!(
                s.copy_draft(
                    &target,
                    &CopyRequest {
                        operation_id: "copy".into(),
                        expected_revision: 0,
                        document: bad,
                        source_project: Some("origin".into())
                    }
                ),
                Err(Error::InvalidInput)
            ));
            assert!(s.read(&target).unwrap().is_none());
            assert!(s.list_jobs(&target).unwrap().is_empty());
        }
        assert!(matches!(
            s.copy_draft(
                "bad-revision",
                &CopyRequest {
                    operation_id: "copy".into(),
                    expected_revision: 1,
                    document,
                    source_project: Some("origin".into())
                }
            ),
            Err(Error::Conflict { .. })
        ));
        assert!(s.read("bad-revision").unwrap().is_none());
    }
    #[test]
    fn explicit_origin_preserves_transcript_without_corrections() {
        let (mut s, mut document, _) = fixture();
        document.as_object_mut().unwrap().remove("transcript_edits");
        let copied = s
            .copy_draft(
                "plain-fork",
                &CopyRequest {
                    operation_id: "copy".into(),
                    expected_revision: 0,
                    document,
                    source_project: Some("origin".into()),
                },
            )
            .unwrap();
        assert!(copied.document.get("transcript_edits").is_none());
        assert_eq!(
            s.list_jobs("plain-fork")
                .unwrap()
                .iter()
                .filter(|j| matches!(j.task, Task::Transcribe { .. }))
                .count(),
            2
        );
    }
    #[test]
    fn invalid_source_list_rolls_back_provisional_draft_clones_events_and_ack() {
        let (mut s, mut document, _) = fixture();
        let duplicate = document["files"][0].clone();
        document["files"].as_array_mut().unwrap().push(duplicate);
        assert!(matches!(
            s.copy_draft(
                "rolled-back",
                &CopyRequest {
                    operation_id: "copy".into(),
                    expected_revision: 0,
                    document,
                    source_project: Some("origin".into())
                }
            ),
            Err(Error::InvalidInput)
        ));
        assert!(s.read("rolled-back").unwrap().is_none());
        assert!(s.list_jobs("rolled-back").unwrap().is_empty());
        assert_eq!(s.latest_job_event("rolled-back").unwrap(), 0);
        assert_eq!(
            s.0.query_row(
                "SELECT count(*) FROM draft_copies WHERE project_id='rolled-back'",
                [],
                |r| r.get::<_, i64>(0)
            )
            .unwrap(),
            0
        );
    }
    #[test]
    fn absent_backup_origin_restores_plain_draft_but_cannot_fabricate_correction_provenance() {
        let (mut s, document, _) = fixture();
        let mut plain = document.clone();
        plain.as_object_mut().unwrap().remove("transcript_edits");
        let request = CopyRequest {
            operation_id: "copy".into(),
            expected_revision: 0,
            document: plain.clone(),
            source_project: Some("missing-origin".into()),
        };
        let restored = s.copy_draft("plain-restored", &request).unwrap();
        assert_eq!(restored.document, plain);
        let jobs = s.list_jobs("plain-restored").unwrap();
        assert_eq!(jobs.len(), 1);
        assert!(matches!(jobs[0].task, Task::Probe { .. }));
        let mut unknown = document;
        unknown["transcript_edits"][0]["job_id"] = serde_json::json!("missing-job");
        assert!(matches!(
            s.copy_draft(
                "unknown-correction",
                &CopyRequest {
                    document: unknown,
                    ..request
                }
            ),
            Err(Error::InvalidInput)
        ));
        assert!(s.read("unknown-correction").unwrap().is_none());
        assert!(s.list_jobs("unknown-correction").unwrap().is_empty());
    }
}
