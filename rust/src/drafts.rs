//! Acknowledged revisions survive restart; retries cannot overwrite a newer edit.
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::Path;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error("upload bytes conflict")]
    UploadConflict,
    #[error("upload incomplete")]
    UploadIncomplete,
    #[error("upload file does not match its durable checkpoint")]
    UploadCorrupt,
    #[error(transparent)]
    Database(#[from] rusqlite::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    #[error("draft revision changed: expected {expected}, actual {actual}")]
    Conflict { expected: i64, actual: i64 },
    #[error("operation id reused with different content")]
    OperationConflict,
    #[error("project and operation ids must be nonempty and revision nonnegative")]
    InvalidInput,
    #[error("job not found")]
    NotFound,
    #[error("invalid job transition")]
    JobState,
    #[error("storage worker unavailable")]
    Unavailable,
    #[error("transcription source exceeds 100 five-minute chunks")]
    TranscriptionLimit,
    #[error("transcription cache integrity check failed")]
    TranscriptionCacheCorrupt,
    #[error("selected source content changed")]
    SourceChanged,
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
pub struct Draft {
    pub project_id: String,
    pub revision: i64,
    pub document: Value,
}

pub struct Store(pub(crate) Connection);

impl Store {
    pub fn open(path: &Path) -> Result<Self, Error> {
        let connection = Connection::open(path)?;
        connection.busy_timeout(std::time::Duration::from_secs(5))?;
        connection.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=FULL;
             PRAGMA foreign_keys=ON;
             CREATE TABLE IF NOT EXISTS drafts (
                 project_id TEXT PRIMARY KEY,
                 revision INTEGER NOT NULL,
                 document TEXT NOT NULL
             );
             CREATE TABLE IF NOT EXISTS draft_operations (
                 project_id TEXT NOT NULL,
                 operation_id TEXT NOT NULL,
                 base_revision INTEGER NOT NULL,
                 revision INTEGER NOT NULL,
                 document TEXT NOT NULL,
                 PRIMARY KEY (project_id, operation_id),
                 UNIQUE (project_id, revision),
                 FOREIGN KEY (project_id) REFERENCES drafts(project_id)
             );
             CREATE TABLE IF NOT EXISTS draft_copies (
                 project_id TEXT NOT NULL REFERENCES drafts(project_id), operation_id TEXT NOT NULL,
                 request TEXT NOT NULL, revision INTEGER NOT NULL, document TEXT NOT NULL,
                 PRIMARY KEY(project_id,operation_id)
             );
             CREATE TABLE IF NOT EXISTS hidden_projects (
                 project_id TEXT PRIMARY KEY REFERENCES drafts(project_id)
             );",
        )?;
        let store = Self(connection);
        store.initialize_jobs()?;
        store.initialize_uploads()?;
        Ok(store)
    }

    pub fn read(&self, project: &str) -> Result<Option<Draft>, Error> {
        let row: Option<(i64, String)> = self
            .0
            .query_row(
                "SELECT revision, document FROM drafts WHERE project_id=?1",
                [project],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        row.map(|(revision, text)| {
            Ok(Draft {
                project_id: project.to_owned(),
                revision,
                document: serde_json::from_str(&text)?,
            })
        })
        .transpose()
    }

    /// Every saved project, most recently saved first, with what the project screen shows.
    pub fn list(&self) -> Result<Vec<Value>, Error> {
        let mut statement = self.0.prepare(
            "SELECT d.project_id, d.document FROM drafts d
             WHERE d.project_id NOT IN (SELECT project_id FROM hidden_projects)
             ORDER BY (SELECT MAX(o.rowid) FROM draft_operations o WHERE o.project_id=d.project_id) DESC",
        )?;
        let rows = statement.query_map([], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
        })?;
        let mut projects = Vec::new();
        for row in rows {
            let (project, text) = row?;
            let document: Value = serde_json::from_str(&text)?;
            let files = document["files"].as_array().map(Vec::as_slice).unwrap_or(&[]);
            projects.push(serde_json::json!({
                "project_id": project,
                "title": document["title"].as_str().unwrap_or(""),
                "step": document["step"].as_i64().unwrap_or(1),
                "videos": files.len(),
                "cover": files.iter().find_map(|f| f["source_id"].as_str()),
            }));
        }
        Ok(projects)
    }

    /// Folders holding the videos every project already uses, where a dropped video most likely sits.
    pub fn source_folders(&self) -> Result<Vec<String>, Error> {
        let mut statement = self.0.prepare("SELECT document FROM drafts")?;
        let rows = statement.query_map([], |r| r.get::<_, String>(0))?;
        let mut folders = std::collections::BTreeSet::new();
        for row in rows {
            let document: Value = serde_json::from_str(&row?)?;
            for file in document["files"].as_array().into_iter().flatten() {
                if let Some(parent) = file["path"].as_str().and_then(|p| Path::new(p).parent()) {
                    folders.insert(parent.to_string_lossy().into_owned());
                }
            }
        }
        Ok(folders.into_iter().collect())
    }

    /// Deleting a project on the project screen only hides it: its draft, videos and
    /// transcripts stay, so "undo" brings it back whole.
    pub fn hide(&self, project: &str, hidden: bool) -> Result<(), Error> {
        if self.read(project)?.is_none() {
            return Err(Error::NotFound);
        }
        let sql = if hidden {
            "INSERT OR IGNORE INTO hidden_projects(project_id) VALUES (?1)"
        } else {
            "DELETE FROM hidden_projects WHERE project_id=?1"
        };
        self.0.execute(sql, [project])?;
        Ok(())
    }

    pub fn save(
        &mut self,
        project: &str,
        operation: &str,
        expected: i64,
        document: &Value,
    ) -> Result<i64, Error> {
        if project.trim().is_empty()
            || operation.trim().is_empty()
            || expected < 0
            || !crate::styles::valid(document)
        {
            return Err(Error::InvalidInput);
        }
        let text = serde_json::to_string(document)?;
        let tx = self
            .0
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let previous: Option<(i64, i64, String)> = tx
            .query_row(
                "SELECT base_revision, revision, document FROM draft_operations
             WHERE project_id=?1 AND operation_id=?2",
                params![project, operation],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .optional()?;
        if let Some((base, revision, saved)) = previous {
            return if base == expected && saved == text {
                Ok(revision)
            } else {
                Err(Error::OperationConflict)
            };
        }
        let actual: i64 = tx
            .query_row(
                "SELECT revision FROM drafts WHERE project_id=?1",
                [project],
                |r| r.get(0),
            )
            .optional()?
            .unwrap_or(0);
        if actual != expected {
            return Err(Error::Conflict { expected, actual });
        }
        crate::transcript_edits::validate_saved(&tx, project, document)?;
        let revision = actual.checked_add(1).ok_or(Error::InvalidInput)?;
        tx.execute(
            "INSERT INTO drafts(project_id, revision, document) VALUES (?1,?2,?3)
             ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision,
             document=excluded.document",
            params![project, revision, text],
        )?;
        tx.execute(
            "INSERT INTO draft_operations(project_id,operation_id,base_revision,revision,document)
             VALUES (?1,?2,?3,?4,?5)",
            params![project, operation, expected, revision, text],
        )?;
        crate::jobs::source_probes(&tx, project, revision, document)?;
        tx.commit()?;
        Ok(revision)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn invalid_source_imports_leave_draft_and_jobs_unchanged() {
        let mut store = Store::open(Path::new(":memory:")).unwrap();
        let source =
            json!({"source_id":"source1","path":"media.wav","size":12,"modified_ns":"123"});
        store
            .save("project", "initial", 0, &json!({"files":[]}))
            .unwrap();
        let mut nul = source.clone();
        nul["path"] = json!("media\0.wav");
        let mut empty_stamp = source.clone();
        empty_stamp["modified_ns"] = json!("");
        for (index, files) in [
            json!([nul]),
            json!([source.clone(), source]),
            json!([empty_stamp]),
        ]
        .iter()
        .enumerate()
        {
            assert!(matches!(
                store.save(
                    "project",
                    &format!("bad{index}"),
                    1,
                    &json!({"files":files})
                ),
                Err(Error::InvalidInput)
            ));
            assert_eq!(store.read("project").unwrap().unwrap().revision, 1);
            assert!(store.list_jobs("project").unwrap().is_empty());
        }
        store
            .save(
                "project",
                "next",
                1,
                &json!({"files":[],"title":"still editable"}),
            )
            .unwrap();
    }

    #[test]
    fn selected_sources_and_probe_jobs_commit_together_and_do_not_duplicate() {
        let mut store = Store::open(Path::new(":memory:")).unwrap();
        let first = json!({"files":[{"source_id":"source1","path":"media.wav","size":12,"modified_ns":"123"}]});
        assert_eq!(store.save("project", "pick", 0, &first).unwrap(), 1);
        assert_eq!(store.list_jobs("project").unwrap().len(), 1);
        let mut second = first.clone();
        second["title"] = json!("edited");
        store.save("project", "edit", 1, &second).unwrap();
        assert_eq!(store.save("project", "pick", 0, &first).unwrap(), 1);
        let jobs = store.list_jobs("project").unwrap();
        assert_eq!(jobs.len(), 1);
        assert_eq!(jobs[0].revision, 1);
        let mut invalid = second.clone();
        invalid["files"][0]["path"] = json!("another.wav");
        assert!(matches!(
            store.save("project", "bad", 2, &invalid),
            Err(Error::OperationConflict)
        ));
        let restored = store.read("project").unwrap().unwrap();
        assert_eq!(restored.revision, 2);
        assert_eq!(restored.document, second);
    }

    #[test]
    fn retry_does_not_revert_newer_content_and_stale_tab_cannot_overwrite() {
        let mut store = Store::open(Path::new(":memory:")).unwrap();
        assert_eq!(
            store.save("งาน", "first", 0, &json!({"text":"ก"})).unwrap(),
            1
        );
        assert_eq!(
            store
                .save("งาน", "second", 1, &json!({"text":"ข"}))
                .unwrap(),
            2
        );
        assert_eq!(
            store.save("งาน", "first", 0, &json!({"text":"ก"})).unwrap(),
            1
        );
        assert_eq!(
            store.read("งาน").unwrap().unwrap().document,
            json!({"text":"ข"})
        );
        assert!(matches!(
            store.save("งาน", "stale", 1, &json!({})),
            Err(Error::Conflict { actual: 2, .. })
        ));
        assert!(matches!(
            store.save("งาน", "first", 0, &json!({})),
            Err(Error::OperationConflict)
        ));
    }

    #[test]
    fn acknowledged_draft_survives_reopen_and_concurrent_connection() {
        let path = std::env::temp_dir().join(format!(
            "editorkrab-store-{}-{}.sqlite",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        {
            let mut first = Store::open(&path).unwrap();
            let mut second = Store::open(&path).unwrap();
            first
                .save(
                    "project",
                    "a",
                    0,
                    &json!({"step":3,"json_input":"{unfinished"}),
                )
                .unwrap();
            assert!(matches!(
                second.save("project", "b", 0, &json!({})),
                Err(Error::Conflict { actual: 1, .. })
            ));
        }
        {
            let store = Store::open(&path).unwrap();
            let restored = store.read("project").unwrap().unwrap();
            assert_eq!(restored.revision, 1);
            assert_eq!(restored.document["json_input"], "{unfinished");
        }
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn projects_list_newest_saved_first_with_video_count_and_cover() {
        let mut store = Store::open(Path::new(":memory:")).unwrap();
        let video = |id: &str| json!({"name":"a.mov","size":1,"source_id":id,"path":"C:/a.mov","modified_ns":"1"});
        store
            .save("old", "1", 0, &json!({"title":"เก่า","step":3,"files":[]}))
            .unwrap();
        store
            .save("new", "2", 0, &json!({"title":"ใหม่","step":1,"files":[{"name":"wait.mov","size":1}]}))
            .unwrap();
        store
            .save("old", "3", 1, &json!({"title":"เก่า","step":4,"files":[video("s1"),video("s2")]}))
            .unwrap();
        let projects = store.list().unwrap();
        assert_eq!(projects[0]["project_id"], "old");
        assert_eq!(projects[0]["videos"], 2);
        assert_eq!(projects[0]["cover"], "s1");
        assert_eq!(projects[0]["step"], 4);
        assert_eq!(projects[1]["title"], "ใหม่");
        assert_eq!(projects[1]["cover"], Value::Null);
        store.hide("old", true).unwrap();
        assert_eq!(store.list().unwrap().len(), 1);
        assert!(store.read("old").unwrap().is_some(), "a deleted project keeps its draft");
        store.hide("old", false).unwrap();
        assert_eq!(store.list().unwrap().len(), 2);
        assert!(matches!(store.hide("missing", true), Err(Error::NotFound)));
    }
}
