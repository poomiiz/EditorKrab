//! Only synced bytes advance the durable offset. Replays verify bytes instead of guessing by name.
use crate::{
    drafts::{Error, Store},
    sources::{EXTENSIONS, Source, stamp},
};
use rusqlite::{OptionalExtension, params};
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
};

pub const CHUNK_BYTES: usize = 4 * 1024 * 1024;
// shortcut: retain managed copies for undo/recovery; add explicit cleanup with project deletion.
const MAX_SAFE_INTEGER: u64 = 9_007_199_254_740_991;

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Begin {
    pub id: String,
    pub project: String,
    pub name: String,
    pub size: u64,
    pub last_modified: u64,
}

#[derive(Debug, Serialize)]
pub struct Progress {
    pub offset: u64,
    pub size: u64,
}

fn extension(name: &str) -> Result<String, Error> {
    let ext = name.rsplit('.').next().unwrap_or("").to_ascii_lowercase();
    if EXTENSIONS.contains(&ext.as_str()) {
        Ok(ext)
    } else {
        Err(Error::InvalidInput)
    }
}

fn paths(root: &Path, id: &str, name: &str) -> Result<(PathBuf, PathBuf), Error> {
    let safe_id = uuid::Uuid::parse_str(id)
        .map_err(|_| Error::InvalidInput)?
        .to_string();
    if safe_id != id {
        return Err(Error::InvalidInput);
    }
    // Neither the client filename nor a client directory can become a destination path.
    Ok((
        root.join(format!("{safe_id}.part")),
        root.join(format!("{safe_id}.{}", extension(name)?)),
    ))
}

fn sync_directory(root: &Path) -> Result<(), Error> {
    #[cfg(unix)]
    fs::File::open(root)?.sync_all()?;
    #[cfg(not(unix))]
    let _ = root;
    Ok(())
}

impl Store {
    pub fn initialize_uploads(&self) -> Result<(), Error> {
        self.0.execute_batch(
            "CREATE TABLE IF NOT EXISTS uploads(
            id TEXT PRIMARY KEY, request TEXT NOT NULL, received INTEGER NOT NULL DEFAULT 0,
            source TEXT, created_at INTEGER NOT NULL DEFAULT(unixepoch()));",
        )?;
        Ok(())
    }

    fn upload(&self, id: &str) -> Result<(Begin, u64, Option<String>), Error> {
        let (request, received, source): (String, u64, Option<String>) = self
            .0
            .query_row(
                "SELECT request,received,source FROM uploads WHERE id=?1",
                [id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .optional()?
            .ok_or(Error::NotFound)?;
        Ok((serde_json::from_str(&request)?, received, source))
    }

    /// The project the upload was first started for; its copy lives in that project's folder.
    pub fn upload_project(&self, id: &str) -> Result<String, Error> {
        Ok(self.upload(id)?.0.project)
    }

    pub fn begin_upload(&self, root: &Path, request: &Begin) -> Result<Progress, Error> {
        if request.project.trim().is_empty()
            || request.project.len() > 128
            || request.name.is_empty()
            || request.name.len() > 4096
            || request.name.contains('\0')
            || request.size > MAX_SAFE_INTEGER
            || request.last_modified > MAX_SAFE_INTEGER
        {
            return Err(Error::InvalidInput);
        }
        let (part, _) = paths(root, &request.id, &request.name)?;
        let old: Option<String> = self
            .0
            .query_row(
                "SELECT request FROM uploads WHERE id=?1",
                [&request.id],
                |r| r.get(0),
            )
            .optional()?;
        if let Some(old) = old {
            let mut prior: Begin = serde_json::from_str(&old)?;
            // Draft forks may reference the same immutable upload; project records its origin only.
            prior.project = request.project.clone();
            if prior != *request {
                return Err(Error::OperationConflict);
            }
        } else {
            fs::create_dir_all(root)?;
            // A prior uncommitted creation can be recovered only when it contains no bytes.
            match OpenOptions::new().write(true).create_new(true).open(&part) {
                Ok(file) => file.sync_all()?,
                Err(e)
                    if e.kind() == std::io::ErrorKind::AlreadyExists
                        && part.metadata()?.len() == 0 => {}
                Err(e) => return Err(e.into()),
            }
            sync_directory(root)?;
            self.0.execute(
                "INSERT INTO uploads(id,request) VALUES(?1,?2)",
                params![request.id, serde_json::to_string(request)?],
            )?;
        }
        let (_, offset, _) = self.upload(&request.id)?;
        Ok(Progress {
            offset,
            size: request.size,
        })
    }

    pub fn upload_chunk(
        &self,
        root: &Path,
        id: &str,
        offset: u64,
        bytes: &[u8],
    ) -> Result<Progress, Error> {
        if bytes.is_empty() || bytes.len() > CHUNK_BYTES {
            return Err(Error::InvalidInput);
        }
        let (request, received, source) = self.upload(id)?;
        let end = offset
            .checked_add(bytes.len() as u64)
            .ok_or(Error::InvalidInput)?;
        if end > request.size || offset > received || (offset < received && end > received) {
            return Err(Error::UploadConflict);
        }
        let (part, complete) = paths(root, id, &request.name)?;
        let finalized = source.is_some() || (!part.exists() && complete.exists());
        let mut file = OpenOptions::new()
            .read(true)
            .write(!finalized)
            .open(if finalized { complete } else { part })?;
        if file.metadata()?.len() < received {
            return Err(Error::UploadCorrupt);
        }
        file.seek(SeekFrom::Start(offset))?;
        if offset < received {
            let mut previous = vec![0; bytes.len()];
            file.read_exact(&mut previous)?;
            if previous != bytes {
                return Err(Error::UploadConflict);
            }
            return Ok(Progress {
                offset: received,
                size: request.size,
            });
        }
        if finalized {
            return Err(Error::UploadConflict);
        }
        // Discard only an unacknowledged tail left by a crash before SQLite advanced.
        file.set_len(received)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        self.0.execute(
            "UPDATE uploads SET received=?2 WHERE id=?1",
            params![id, end],
        )?;
        Ok(Progress {
            offset: end,
            size: request.size,
        })
    }

    pub fn finish_upload(&self, root: &Path, id: &str) -> Result<Source, Error> {
        let (request, received, source) = self.upload(id)?;
        if received != request.size {
            return Err(Error::UploadIncomplete);
        }
        let (part, complete) = paths(root, id, &request.name)?;
        if let Some(source) = source {
            let source: Source = serde_json::from_str(&source)?;
            let metadata = complete.metadata()?;
            if metadata.len() != source.size || stamp(&metadata)? != source.modified_ns {
                return Err(Error::UploadCorrupt);
            }
            return Ok(source);
        }
        if part.exists() {
            if part.metadata()?.len() != received || complete.exists() {
                return Err(Error::UploadCorrupt);
            }
            fs::rename(&part, &complete)?;
            sync_directory(root)?;
        }
        // A completed rename followed by a crash is recovered from this exact generated path.
        let metadata = complete.metadata()?;
        if metadata.len() != received {
            return Err(Error::UploadCorrupt);
        }
        let source = Source {
            source_id: request.id,
            name: request.name,
            size: received,
            path: complete
                .canonicalize()?
                .to_str()
                .ok_or(Error::InvalidInput)?
                .to_owned(),
            modified_ns: stamp(&metadata)?,
        };
        self.0.execute(
            "UPDATE uploads SET source=?2 WHERE id=?1",
            params![id, serde_json::to_string(&source)?],
        )?;
        Ok(source)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn invalid_destinations_and_io_failures_cannot_advance_an_upload() {
        let root =
            std::env::temp_dir().join(format!("editorkrab-upload-io-{}", uuid::Uuid::new_v4()));
        let store = Store::open(Path::new(":memory:")).unwrap();
        let request = Begin {
            id: uuid::Uuid::new_v4().to_string(),
            project: "project".into(),
            name: "file.wav".into(),
            size: 4,
            last_modified: 0,
        };
        for bad in ["../escape".to_owned(), request.id.replace('-', "")] {
            assert!(matches!(
                store.begin_upload(
                    &root,
                    &Begin {
                        id: bad,
                        ..request.clone()
                    }
                ),
                Err(Error::InvalidInput)
            ));
        }
        fs::write(&root, b"blocking file").unwrap();
        assert!(matches!(
            store.begin_upload(&root, &request),
            Err(Error::Io(_))
        ));
        assert!(matches!(store.upload(&request.id), Err(Error::NotFound)));
        fs::remove_file(&root).unwrap();
        store.begin_upload(&root, &request).unwrap();
        assert!(matches!(
            store.upload_chunk(&root, &request.id, 0, b"12345"),
            Err(Error::UploadConflict)
        ));
        assert_eq!(store.upload(&request.id).unwrap().1, 0);
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn upload_resume_checks_replays_and_recovers_unacknowledged_tail() {
        let root = std::env::temp_dir().join(format!("editorkrab-upload-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        let state = root.join("state.sqlite");
        let request = Begin {
            id: uuid::Uuid::new_v4().to_string(),
            project: "project".into(),
            name: "ชุดหนึ่ง/ทดสอบ.wav".into(),
            size: 8,
            last_modified: 123,
        };
        {
            let store = Store::open(&state).unwrap();
            assert_eq!(store.begin_upload(&root, &request).unwrap().offset, 0);
            store.upload_chunk(&root, &request.id, 0, b"abcd").unwrap();
            assert!(matches!(
                store.finish_upload(&root, &request.id),
                Err(Error::UploadIncomplete)
            ));
            let (part, _) = paths(&root, &request.id, &request.name).unwrap();
            OpenOptions::new()
                .append(true)
                .open(part)
                .unwrap()
                .write_all(b"unacked")
                .unwrap();
        }
        {
            let store = Store::open(&state).unwrap();
            assert_eq!(store.begin_upload(&root, &request).unwrap().offset, 4);
            assert_eq!(
                store
                    .upload_chunk(&root, &request.id, 0, b"abcd")
                    .unwrap()
                    .offset,
                4
            );
            assert!(matches!(
                store.upload_chunk(&root, &request.id, 0, b"wrong"),
                Err(Error::UploadConflict)
            ));
            store.upload_chunk(&root, &request.id, 4, b"efgh").unwrap();
            let (part, complete) = paths(&root, &request.id, &request.name).unwrap();
            fs::rename(part, &complete).unwrap(); // Crash after rename, before source commit.
            store.upload_chunk(&root, &request.id, 0, b"abcd").unwrap();
            let source = store.finish_upload(&root, &request.id).unwrap();
            assert_eq!(fs::read(&source.path).unwrap(), b"abcdefgh");
            assert_eq!(
                store.finish_upload(&root, &request.id).unwrap().source_id,
                source.source_id
            );
            store.upload_chunk(&root, &request.id, 0, b"abcd").unwrap();
            let changed = Begin {
                size: 9,
                ..request.clone()
            };
            assert!(matches!(
                store.begin_upload(&root, &changed),
                Err(Error::OperationConflict)
            ));
        }
        fs::remove_dir_all(root).unwrap();
    }
}
