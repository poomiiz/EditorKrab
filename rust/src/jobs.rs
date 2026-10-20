//! Durable batches. Only a claimed job can finish; a request retry never creates another batch.
use crate::drafts::{Error, Store};
use rusqlite::{OptionalExtension, Transaction, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashSet;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Task {
    Probe {
        path: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        source: Option<crate::sources::Identity>,
    },
    Transcribe {
        path: String,
        context: crate::transcription::Context,
        settings: crate::native_asr::Settings,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Item {
    pub key: String,
    pub task: Task,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BatchRequest {
    pub request_id: String,
    pub revision: i64,
    pub items: Vec<Item>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Job {
    pub id: String,
    pub batch_id: String,
    pub project_id: String,
    pub position: i64,
    pub status: String,
    pub step: String,
    pub attempt: i64,
    pub cancel_requested: bool,
    pub task: Task,
    pub result: Option<Value>,
    pub error: Option<String>,
    pub created_at: i64,
    pub started_at: Option<i64>,
    pub finished_at: Option<i64>,
    pub heartbeat_at: Option<i64>,
    pub revision: i64,
}

#[derive(Debug, Serialize)]
pub struct Event {
    pub sequence: i64,
    pub job: Job,
}

pub fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|v| v.is_ascii_alphanumeric() || v == b'-' || v == b'_')
}

pub(crate) fn read_job(row: &rusqlite::Row<'_>) -> rusqlite::Result<Job> {
    fn decode<T: serde::de::DeserializeOwned>(
        row: &rusqlite::Row<'_>,
        index: usize,
    ) -> rusqlite::Result<T> {
        serde_json::from_str(&row.get::<_, String>(index)?).map_err(|error| {
            rusqlite::Error::FromSqlConversionFailure(
                index,
                rusqlite::types::Type::Text,
                Box::new(error),
            )
        })
    }
    let result: Option<String> = row.get(9)?;
    let result = result
        .map(|s| {
            serde_json::from_str(&s).map_err(|e| {
                rusqlite::Error::FromSqlConversionFailure(
                    9,
                    rusqlite::types::Type::Text,
                    Box::new(e),
                )
            })
        })
        .transpose()?;
    Ok(Job {
        id: row.get(0)?,
        batch_id: row.get(1)?,
        project_id: row.get(2)?,
        position: row.get(3)?,
        status: row.get(4)?,
        step: row.get(5)?,
        attempt: row.get(6)?,
        cancel_requested: row.get(7)?,
        task: decode(row, 8)?,
        result,
        error: row.get(10)?,
        created_at: row.get(11)?,
        started_at: row.get(12)?,
        finished_at: row.get(13)?,
        heartbeat_at: row.get(14)?,
        revision: row.get(15)?,
    })
}

pub(crate) const COLUMNS: &str = "id,batch_id,project_id,position,status,step,attempt,cancel_requested,task,result,error,created_at,started_at,finished_at,heartbeat_at,(SELECT json_extract(request,'$.revision') FROM job_batches WHERE id=jobs.batch_id)";

pub(crate) fn event(tx: &Transaction<'_>, id: &str) -> Result<(), Error> {
    let job = tx.query_row(
        &format!("SELECT {COLUMNS} FROM jobs WHERE id=?1"),
        [id],
        read_job,
    )?;
    tx.execute(
        "INSERT INTO job_events(project_id,job_id,snapshot) VALUES(?1,?2,?3)",
        params![job.project_id, id, serde_json::to_string(&job)?],
    )?;
    Ok(())
}

pub(crate) fn insert_batch(
    tx: &Transaction<'_>,
    project: &str,
    request: &BatchRequest,
) -> Result<(), Error> {
    let id = format!("{project}.{}", request.request_id);
    tx.execute(
        "INSERT INTO job_batches(id,project_id,request_id,request) VALUES(?1,?2,?3,?4)",
        params![
            id,
            project,
            request.request_id,
            serde_json::to_string(request)?
        ],
    )?;
    for (index, item) in request.items.iter().enumerate() {
        let job_id = format!("{id}.{}", item.key);
        tx.execute("INSERT INTO jobs(id,batch_id,project_id,position,status,step,task) VALUES(?1,?2,?3,?4,'queued','queued',?5)",
            params![job_id,id,project,index as i64,serde_json::to_string(&item.task)?])?;
        event(tx, &job_id)?;
    }
    Ok(())
}

/// Draft and desired source jobs commit together, even if the HTTP acknowledgement is lost.
pub(crate) fn source_probes(
    tx: &Transaction<'_>,
    project: &str,
    revision: i64,
    document: &Value,
) -> Result<(), Error> {
    let Some(files) = document.get("files").and_then(Value::as_array) else {
        return Ok(());
    };
    let mut ids = HashSet::new();
    for file in files {
        let Some(source_id) = file.get("source_id") else {
            continue;
        };
        let source_id = source_id
            .as_str()
            .filter(|s| identifier(s))
            .ok_or(Error::InvalidInput)?;
        if !ids.insert(source_id) {
            return Err(Error::InvalidInput);
        }
        let path = file["path"]
            .as_str()
            .filter(|s| !s.trim().is_empty() && !s.contains('\0'))
            .ok_or(Error::InvalidInput)?;
        let identity = crate::sources::Identity {
            id: source_id.into(),
            size: file["size"].as_u64().ok_or(Error::InvalidInput)?,
            modified_ns: file["modified_ns"]
                .as_str()
                .filter(|s| {
                    let digits = s.strip_prefix('-').unwrap_or(s);
                    !digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit())
                })
                .ok_or(Error::InvalidInput)?
                .to_owned(),
        };
        let task = Task::Probe {
            path: path.into(),
            source: Some(identity),
        };
        let request_id = format!("source-{source_id}");
        let id = format!("{project}.{request_id}");
        let old: Option<String> = tx
            .query_row("SELECT request FROM job_batches WHERE id=?1", [&id], |r| {
                r.get(0)
            })
            .optional()?;
        if let Some(old) = old {
            let prior: BatchRequest = serde_json::from_str(&old)?;
            if prior.items.len() != 1 || prior.items[0].task != task {
                return Err(Error::OperationConflict);
            }
        } else {
            insert_batch(
                tx,
                project,
                &BatchRequest {
                    request_id,
                    revision,
                    items: vec![Item {
                        key: "probe".into(),
                        task,
                    }],
                },
            )?;
        }
    }
    Ok(())
}

pub(crate) fn validate_transcription_source(
    tx: &Transaction<'_>,
    project: &str,
    document: &Value,
    path: &str,
    context: &crate::transcription::Context,
) -> Result<u64, Error> {
    let file = document["files"]
        .as_array()
        .and_then(|files| {
            files
                .iter()
                .find(|f| f["source_id"].as_str() == Some(&context.source.id))
        })
        .ok_or(Error::InvalidInput)?;
    if file["path"].as_str() != Some(path)
        || file["size"].as_u64() != Some(context.source.size)
        || file["modified_ns"].as_str() != Some(&context.source.modified_ns)
        || document["language"].as_str() != Some(&context.language)
    {
        return Err(Error::InvalidInput);
    }
    let mut statement=tx.prepare("SELECT task,result FROM jobs WHERE project_id=?1 AND status='succeeded' AND json_extract(task,'$.kind')='probe' ORDER BY rowid DESC")?;
    let rows = statement.query_map([project], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    for row in rows {
        let (task, result) = row?;
        if let Task::Probe {
            path: probed,
            source: Some(identity),
        } = serde_json::from_str::<Task>(&task)?
        {
            if probed != path || identity != context.source {
                continue;
            }
            let result: Value = serde_json::from_str(&result)?;
            let duration = result["duration_s"]
                .as_f64()
                .filter(|v| v.is_finite() && *v > 0.0)
                .ok_or(Error::InvalidInput)?;
            let audio = result["streams"]
                .as_array()
                .is_some_and(|streams| streams.iter().any(|v| v["codec_type"] == "audio"));
            return if audio && context.end_ms as f64 <= (duration * 1000.0).ceil() {
                Ok((duration * 1000.0).ceil() as u64)
            } else {
                Err(Error::InvalidInput)
            };
        }
    }
    Err(Error::InvalidInput)
}

impl Store {
    pub fn initialize_jobs(&self) -> Result<(), Error> {
        self.0.execute_batch("CREATE TABLE IF NOT EXISTS job_batches(
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES drafts(project_id),
            request_id TEXT NOT NULL, request TEXT NOT NULL, UNIQUE(project_id,request_id));
            CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES job_batches(id),
                project_id TEXT NOT NULL, position INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
                step TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0, task TEXT NOT NULL,
                result TEXT, error TEXT, created_at INTEGER NOT NULL DEFAULT(unixepoch()),
                started_at INTEGER,finished_at INTEGER,heartbeat_at INTEGER);
            CREATE INDEX IF NOT EXISTS jobs_project ON jobs(project_id);
            CREATE TABLE IF NOT EXISTS job_events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT NOT NULL,
                job_id TEXT NOT NULL REFERENCES jobs(id),snapshot TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS job_events_project ON job_events(project_id,sequence);
            CREATE TABLE IF NOT EXISTS transcription_requests(
                project_id TEXT NOT NULL REFERENCES drafts(project_id), request_id TEXT NOT NULL,
                request TEXT NOT NULL, job_ids TEXT NOT NULL, PRIMARY KEY(project_id,request_id));
            CREATE TABLE IF NOT EXISTS transcription_cache(
                key TEXT PRIMARY KEY, raw BLOB NOT NULL, sha256 TEXT NOT NULL);")?;
        Ok(())
    }

    pub fn create_batch(
        &mut self,
        project: &str,
        request: &BatchRequest,
    ) -> Result<Vec<Job>, Error> {
        if !identifier(project)
            || !identifier(&request.request_id)
            || request.revision < 1
            || request.items.is_empty()
            || request.items.len() > 100
            || request.items.iter().any(|item| {
                !identifier(&item.key)
                    || match &item.task {
                        Task::Probe { path, .. } => path.trim().is_empty() || path.contains('\0'),
                        Task::Transcribe {
                            path,
                            context,
                            settings,
                        } => {
                            path.trim().is_empty()
                                || path.contains('\0')
                                || context.validate().is_err()
                                || context.run_id != request.request_id
                                || !settings.valid()
                        }
                    }
            })
            || request
                .items
                .iter()
                .map(|i| &i.key)
                .collect::<HashSet<_>>()
                .len()
                != request.items.len()
        {
            return Err(Error::InvalidInput);
        }
        let text = serde_json::to_string(request)?;
        let id = format!("{project}.{}", request.request_id);
        let tx = self
            .0
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let old: Option<String> = tx
            .query_row("SELECT request FROM job_batches WHERE id=?1", [&id], |r| {
                r.get(0)
            })
            .optional()?;
        if let Some(old) = old {
            if old != text {
                return Err(Error::OperationConflict);
            }
        } else {
            let revision: Option<i64> = tx
                .query_row(
                    "SELECT revision FROM drafts WHERE project_id=?1",
                    [project],
                    |r| r.get(0),
                )
                .optional()?;
            let actual = revision.ok_or(Error::NotFound)?;
            if actual != request.revision {
                return Err(Error::Conflict {
                    expected: request.revision,
                    actual,
                });
            }
            let document: String = tx.query_row(
                "SELECT document FROM drafts WHERE project_id=?1",
                [project],
                |r| r.get(0),
            )?;
            let document: Value = serde_json::from_str(&document)?;
            let mut chunks = HashSet::new();
            for item in &request.items {
                if let Task::Transcribe { path, context, .. } = &item.task {
                    if !chunks.insert((context.source.id.clone(), context.start_ms, context.end_ms))
                    {
                        return Err(Error::InvalidInput);
                    }
                    validate_transcription_source(&tx, project, &document, path, context)?;
                }
            }
            insert_batch(&tx, project, request)?;
        }
        tx.commit()?;
        self.list_jobs(project)
            .map(|jobs| jobs.into_iter().filter(|j| j.batch_id == id).collect())
    }

    pub fn list_jobs(&self, project: &str) -> Result<Vec<Job>, Error> {
        Ok(self
            .0
            .prepare(&format!(
                "SELECT {COLUMNS} FROM jobs WHERE project_id=?1 ORDER BY rowid"
            ))?
            .query_map([project], read_job)?
            .collect::<Result<Vec<_>, _>>()?)
    }

    pub fn job(&self, id: &str) -> Result<Job, Error> {
        self.0
            .query_row(
                &format!("SELECT {COLUMNS} FROM jobs WHERE id=?1"),
                [id],
                read_job,
            )
            .optional()?
            .ok_or(Error::NotFound)
    }

    pub fn claim_job(&mut self) -> Result<Option<Job>, Error> {
        let tx = self
            .0
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let id: Option<String> = tx
            .query_row(
                "SELECT id FROM jobs WHERE status='queued'
            AND NOT EXISTS(SELECT 1 FROM jobs WHERE status='running') ORDER BY rowid LIMIT 1",
                [],
                |r| r.get(0),
            )
            .optional()?;
        let Some(id) = id else {
            return Ok(None);
        };
        tx.execute(
            "UPDATE jobs SET status='running',step='starting',attempt=attempt+1,
            started_at=unixepoch(),finished_at=NULL,heartbeat_at=unixepoch() WHERE id=?1",
            [&id],
        )?;
        event(&tx, &id)?;
        tx.commit()?;
        self.job(&id).map(Some)
    }

    pub fn job_step(&mut self, id: &str, step: &str) -> Result<(), Error> {
        if !["verifying", "extracting", "transcribing"].contains(&step) {
            return Err(Error::InvalidInput);
        }
        let tx = self
            .0
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let changed=tx.execute("UPDATE jobs SET step=?2,heartbeat_at=unixepoch() WHERE id=?1 AND status='running' AND step<>?2",params![id,step])?;
        if changed > 0 {
            event(&tx, id)?;
        }
        tx.commit()?;
        Ok(())
    }

    pub fn heartbeat_job(&self, id: &str) -> Result<(), Error> {
        if self.0.execute(
            "UPDATE jobs SET heartbeat_at=unixepoch() WHERE id=?1 AND status='running'",
            [id],
        )? != 1
        {
            return Err(Error::JobState);
        }
        Ok(())
    }

    pub fn finish_job(&mut self, id: &str, result: Result<Value, String>) -> Result<Job, Error> {
        self.finish_job_cached(id, result, None)
    }

    pub fn finish_job_cached(
        &mut self,
        id: &str,
        result: Result<Value, String>,
        cache: Option<&crate::chunk_cache::Candidate>,
    ) -> Result<Job, Error> {
        let tx = self
            .0
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let (status, cancel): (String, bool) = tx
            .query_row(
                "SELECT status,cancel_requested FROM jobs WHERE id=?1",
                [id],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?
            .ok_or(Error::NotFound)?;
        if status != "running" {
            return Err(Error::JobState);
        }
        let (status, value, error) = if cancel {
            ("cancelled", None, None)
        } else {
            match result {
                Ok(value) => {
                    if let Some(cache) = cache {
                        let task: String =
                            tx.query_row("SELECT task FROM jobs WHERE id=?1", [id], |r| r.get(0))?;
                        let Task::Transcribe {
                            context, settings, ..
                        } = serde_json::from_str(&task)?
                        else {
                            return Err(Error::InvalidInput);
                        };
                        crate::transcription::parse(&cache.raw, &context)
                            .map_err(|_| Error::TranscriptionCacheCorrupt)?;
                        let source: crate::native_asr::Artifact =
                            serde_json::from_value(value["source"].clone())
                                .map_err(|_| Error::TranscriptionCacheCorrupt)?;
                        if crate::native_asr::cache_key(&source, &context, &settings)
                            .map_err(|_| Error::TranscriptionCacheCorrupt)?
                            != cache.key
                        {
                            return Err(Error::TranscriptionCacheCorrupt);
                        }
                        crate::chunk_cache::save(&tx, cache)?;
                    }
                    ("succeeded", Some(serde_json::to_string(&value)?), None)
                }
                Err(error) => ("failed", None, Some(error)),
            }
        };
        tx.execute("UPDATE jobs SET status=?2,step=?2,result=?3,error=?4,finished_at=unixepoch(),heartbeat_at=unixepoch() WHERE id=?1",
            params![id,status,value,error])?;
        event(&tx, id)?;
        tx.commit()?;
        self.job(id)
    }

    pub fn cancel_job(&mut self, id: &str) -> Result<Job, Error> {
        let tx = self
            .0
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let changed = tx.execute(
            "UPDATE jobs SET cancel_requested=1,
            status=CASE status WHEN 'queued' THEN 'cancelled' ELSE status END,
            step=CASE status WHEN 'queued' THEN 'cancelled' ELSE 'cancelling' END,
            finished_at=CASE status WHEN 'queued' THEN unixepoch() ELSE finished_at END
            WHERE id=?1 AND status IN ('queued','running') AND cancel_requested=0",
            [id],
        )?;
        if changed > 0 {
            event(&tx, id)?;
        }
        tx.commit()?;
        self.job(id)
    }

    pub fn retry_job(&mut self, id: &str) -> Result<Job, Error> {
        let tx = self
            .0
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let old: String = tx
            .query_row("SELECT status FROM jobs WHERE id=?1", [id], |r| r.get(0))
            .optional()?
            .ok_or(Error::NotFound)?;
        if old == "succeeded" || old == "running" {
            return Err(Error::JobState);
        }
        if old != "queued" {
            tx.execute("UPDATE jobs SET status='queued',step='queued',cancel_requested=0,result=NULL,error=NULL,
                started_at=NULL,finished_at=NULL,heartbeat_at=NULL WHERE id=?1",[id])?;
            event(&tx, id)?;
        }
        tx.commit()?;
        self.job(id)
    }

    /// Call once after acquiring the process-wide state-file lock, never on ordinary DB reads.
    pub fn recover_jobs(&mut self) -> Result<usize, Error> {
        let tx = self
            .0
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let ids = tx
            .prepare("SELECT id FROM jobs WHERE status='running'")?
            .query_map([], |r| r.get::<_, String>(0))?
            .collect::<Result<Vec<_>, _>>()?;
        for id in &ids {
            tx.execute("UPDATE jobs SET status=CASE cancel_requested WHEN 1 THEN 'cancelled' ELSE 'queued' END,
                step='recovered',error=NULL,started_at=NULL,heartbeat_at=NULL,
                finished_at=CASE cancel_requested WHEN 1 THEN unixepoch() ELSE NULL END WHERE id=?1",[id])?;
            event(&tx, id)?;
        }
        tx.commit()?;
        Ok(ids.len())
    }

    pub fn job_events(&self, project: &str, after: i64) -> Result<Vec<Event>, Error> {
        let raw = self.0.prepare("SELECT sequence,snapshot FROM job_events WHERE project_id=?1 AND sequence>?2 ORDER BY sequence LIMIT 100")?
            .query_map(params![project,after],|r|Ok((r.get::<_,i64>(0)?,r.get::<_,String>(1)?)))?.collect::<Result<Vec<_>,_>>()?;
        raw.into_iter()
            .map(|(sequence, text)| {
                Ok(Event {
                    sequence,
                    job: serde_json::from_str(&text)?,
                })
            })
            .collect()
    }

    pub fn latest_job_event(&self, project: &str) -> Result<i64, Error> {
        Ok(self.0.query_row(
            "SELECT COALESCE(MAX(sequence),0) FROM job_events WHERE project_id=?1",
            [project],
            |r| r.get(0),
        )?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn store() -> Store {
        let mut s = Store::open(std::path::Path::new(":memory:")).unwrap();
        s.initialize_jobs().unwrap();
        s.save("project", "initial", 0, &json!({})).unwrap();
        s
    }
    fn batch() -> BatchRequest {
        BatchRequest {
            request_id: "batch".into(),
            revision: 1,
            items: (0..5)
                .map(|n| Item {
                    key: format!("clip{n}"),
                    task: Task::Probe {
                        path: format!("{n}.mp4"),
                        source: None,
                    },
                })
                .collect(),
        }
    }
    #[test]
    fn batch_retry_failure_cancel_and_replay_preserve_every_item() {
        let mut s = store();
        let req = batch();
        let first = s.create_batch("project", &req).unwrap();
        assert_eq!(first.len(), 5);
        assert_eq!(s.create_batch("project", &req).unwrap().len(), 5);
        assert_eq!(s.job_events("project", 0).unwrap().len(), 5);
        let one = s.claim_job().unwrap().unwrap();
        assert!(s.claim_job().unwrap().is_none());
        s.finish_job(&one.id, Err("broken source".into())).unwrap();
        let two = s.claim_job().unwrap().unwrap();
        assert_ne!(one.id, two.id);
        s.cancel_job(&two.id).unwrap();
        assert_eq!(
            s.finish_job(&two.id, Ok(json!({"output":"ignored"})))
                .unwrap()
                .status,
            "cancelled"
        );
        s.retry_job(&one.id).unwrap();
        s.retry_job(&one.id).unwrap();
        assert_eq!(s.claim_job().unwrap().unwrap().id, one.id);
        s.finish_job(&one.id, Ok(json!({"verified":true}))).unwrap();
        assert!(matches!(s.retry_job(&one.id), Err(Error::JobState)));
        assert_eq!(s.list_jobs("project").unwrap().len(), 5);
        let events = s.job_events("project", 0).unwrap();
        assert!(
            events
                .windows(2)
                .all(|pair| pair[0].sequence < pair[1].sequence)
        );
        assert!(
            s.job_events("project", events.last().unwrap().sequence)
                .unwrap()
                .is_empty()
        );
    }
    #[test]
    fn changed_batch_is_rejected_and_restart_does_not_redo_success() {
        let mut s = store();
        let mut req = batch();
        s.create_batch("project", &req).unwrap();
        req.items.pop();
        assert!(matches!(
            s.create_batch("project", &req),
            Err(Error::OperationConflict)
        ));
        let first = s.claim_job().unwrap().unwrap();
        s.finish_job(&first.id, Ok(json!({}))).unwrap();
        let running = s.claim_job().unwrap().unwrap();
        assert_eq!(s.recover_jobs().unwrap(), 1);
        let recovered = s.claim_job().unwrap().unwrap();
        assert_eq!(running.id, recovered.id);
        assert_eq!(recovered.attempt, 2);
        assert_eq!(s.job(&first.id).unwrap().status, "succeeded");
    }
}
