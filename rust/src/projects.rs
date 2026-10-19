//! A project is a folder the person chose. Its project file mirrors every saved draft, so the
//! folder alone can be reopened (or moved to another machine); state.sqlite stays the fast cache.
use crate::drafts::{Error, Store};
use rusqlite::{OptionalExtension, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    fs,
    path::{Path, PathBuf},
};

pub const FILE: &str = "project.editorkrab";
pub const EXPORTS: &str = "exports";
/// Dropped videos are copied here only when their original location cannot be found.
pub const MEDIA: &str = "media";

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Project {
    pub id: String,
    pub name: String,
    pub folder: String,
    pub opened_at: i64,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ProjectFile {
    pub schema_version: u32,
    pub project_id: String,
    pub name: String,
    pub revision: i64,
    pub document: Value,
}

#[derive(Debug, Serialize)]
pub struct Legacy {
    pub id: String,
    pub title: String,
}

fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs() as i64)
}

/// A folder name Windows, macOS and Linux all accept; an empty result means "no usable name".
pub fn folder_name(name: &str) -> String {
    let cleaned: String = name
        .chars()
        .map(|c| match c {
            '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*' => ' ',
            c if c.is_control() => ' ',
            c => c,
        })
        .collect::<String>()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    let trimmed = cleaned.trim_end_matches('.').trim();
    let reserved = ["CON", "PRN", "AUX", "NUL"].contains(&trimmed.to_ascii_uppercase().as_str())
        || trimmed.len() == 4
            && ["COM", "LPT"].contains(&trimmed[..3].to_ascii_uppercase().as_str())
            && trimmed.as_bytes()[3].is_ascii_digit();
    if reserved {
        return String::new();
    }
    trimmed.chars().take(80).collect()
}

/// Creates `<parent>/<name>`, or `<name> (2)`… when taken; never reuses an existing folder.
pub fn create_folder(parent: &Path, name: &str) -> Result<PathBuf, Error> {
    let base = folder_name(name);
    if base.is_empty() {
        return Err(Error::InvalidInput);
    }
    fs::create_dir_all(parent)?;
    for n in 1..1000 {
        let candidate = parent.join(if n == 1 {
            base.clone()
        } else {
            format!("{base} ({n})")
        });
        match fs::create_dir(&candidate) {
            Ok(()) => return Ok(candidate.canonicalize()?),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e.into()),
        }
    }
    Err(Error::InvalidInput)
}

/// Written beside the old copy, then renamed over it, so a crash leaves one whole file.
pub fn write_file(folder: &Path, file: &ProjectFile) -> Result<(), Error> {
    let target = folder.join(FILE);
    let partial = folder.join(format!("{FILE}.saving"));
    {
        use std::io::Write;
        let mut out = fs::File::create(&partial)?;
        out.write_all(&serde_json::to_vec_pretty(file)?)?;
        out.sync_all()?;
    }
    fs::rename(&partial, &target)?;
    Ok(())
}

pub fn read_file(folder: &Path) -> Result<ProjectFile, Error> {
    let file: ProjectFile = serde_json::from_slice(&fs::read(folder.join(FILE))?)?;
    if file.schema_version != 1 || file.project_id.trim().is_empty() || file.project_id.len() > 128
    {
        return Err(Error::InvalidInput);
    }
    Ok(file)
}

/// True only when `path` is inside the project folder, so trashing it never reaches outside.
pub fn inside(folder: &Path, path: &Path) -> bool {
    match (folder.canonicalize(), path.canonicalize()) {
        (Ok(folder), Ok(path)) => path != folder && path.starts_with(folder),
        _ => false,
    }
}

impl Store {
    pub fn initialize_projects(&self) -> Result<(), Error> {
        self.0.execute_batch(
            "CREATE TABLE IF NOT EXISTS projects(
             id TEXT PRIMARY KEY, name TEXT NOT NULL, folder TEXT NOT NULL UNIQUE,
             opened_at INTEGER NOT NULL);",
        )?;
        Ok(())
    }

    pub fn project(&self, id: &str) -> Result<Option<Project>, Error> {
        Ok(self
            .0
            .query_row(
                "SELECT id,name,folder,opened_at FROM projects WHERE id=?1",
                [id],
                |r| {
                    Ok(Project {
                        id: r.get(0)?,
                        name: r.get(1)?,
                        folder: r.get(2)?,
                        opened_at: r.get(3)?,
                    })
                },
            )
            .optional()?)
    }

    pub fn projects(&self) -> Result<Vec<Project>, Error> {
        let mut statement = self.0.prepare(
            "SELECT id,name,folder,opened_at FROM projects ORDER BY opened_at DESC, rowid DESC",
        )?;
        let rows = statement.query_map([], |r| {
            Ok(Project {
                id: r.get(0)?,
                name: r.get(1)?,
                folder: r.get(2)?,
                opened_at: r.get(3)?,
            })
        })?;
        Ok(rows.collect::<Result<_, _>>()?)
    }

    /// Drafts saved before projects had folders; the start screen offers to give each one a folder.
    pub fn legacy_drafts(&self) -> Result<Vec<Legacy>, Error> {
        let mut statement = self.0.prepare(
            "SELECT project_id, COALESCE(json_extract(document,'$.title'),'') FROM drafts
             WHERE project_id NOT IN (SELECT id FROM projects) ORDER BY rowid DESC LIMIT 50",
        )?;
        let rows = statement.query_map([], |r| {
            Ok(Legacy {
                id: r.get(0)?,
                title: r.get(1)?,
            })
        })?;
        Ok(rows.collect::<Result<_, _>>()?)
    }

    pub fn register(&self, id: &str, name: &str, folder: &Path) -> Result<Project, Error> {
        let folder = folder.to_str().ok_or(Error::InvalidInput)?;
        self.0.execute(
            "DELETE FROM projects WHERE folder=?1 AND id<>?2",
            params![folder, id],
        )?;
        self.0.execute(
            "INSERT INTO projects(id,name,folder,opened_at) VALUES(?1,?2,?3,?4)
             ON CONFLICT(id) DO UPDATE SET name=excluded.name, folder=excluded.folder,
             opened_at=excluded.opened_at",
            params![id, name, folder, now()],
        )?;
        self.project(id)?.ok_or(Error::NotFound)
    }

    pub fn forget(&self, id: &str) -> Result<(), Error> {
        self.0.execute("DELETE FROM projects WHERE id=?1", [id])?;
        Ok(())
    }

    /// Mirrors the saved draft into its project folder; unregistered (old) drafts have no file.
    pub fn mirror(&self, id: &str) -> Result<(), Error> {
        let (Some(project), Some(draft)) = (self.project(id)?, self.read(id)?) else {
            return Ok(());
        };
        write_file(
            Path::new(&project.folder),
            &ProjectFile {
                schema_version: 1,
                project_id: id.to_owned(),
                name: project.name,
                revision: draft.revision,
                document: draft.document,
            },
        )
    }

    /// Gives a new or old draft its folder: the draft keeps its id, the folder gets its file.
    pub fn create_project(
        &mut self,
        parent: &Path,
        name: &str,
        id: Option<&str>,
        document: &Value,
    ) -> Result<Project, Error> {
        let id = match id {
            Some(id) if !id.trim().is_empty() && id.len() <= 128 => id.to_owned(),
            Some(_) => return Err(Error::InvalidInput),
            None => uuid::Uuid::new_v4().to_string(),
        };
        if self.project(&id)?.is_some() {
            return Err(Error::OperationConflict);
        }
        let folder = create_folder(parent, name)?;
        let display = folder
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or(name)
            .to_owned();
        if self.read(&id)?.is_none() {
            let mut document = document.clone();
            if document
                .get("title")
                .and_then(Value::as_str)
                .unwrap_or("")
                .is_empty()
            {
                document["title"] = Value::String(display.chars().take(120).collect());
            }
            self.save(&id, "project-create", 0, &document)?;
        }
        let project = self.register(&id, &display, &folder)?;
        self.mirror(&id)?;
        Ok(project)
    }

    /// Opens a project folder. A folder copied from another project gets its own id, so two
    /// folders never write into one draft.
    pub fn open_project(&mut self, folder: &Path) -> Result<Project, Error> {
        let folder = folder.canonicalize()?;
        let mut file = read_file(&folder)?;
        let name = folder
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or(Error::InvalidInput)?
            .to_owned();
        if let Some(other) = self.project(&file.project_id)?
            && Path::new(&other.folder) != folder
            && Path::new(&other.folder).join(FILE).is_file()
        {
            file.project_id = uuid::Uuid::new_v4().to_string();
        }
        if self.read(&file.project_id)?.is_none() {
            // Transcripts live in this machine's cache; edits tied to jobs it never ran are dropped.
            let id = file.project_id.clone();
            if self.save(&id, "project-open", 0, &file.document).is_err() {
                let mut document = file.document.clone();
                if let Some(map) = document.as_object_mut() {
                    map.remove("transcript_edits");
                }
                self.save(&id, "project-open-without-edits", 0, &document)?;
            }
        }
        let project = self.register(&file.project_id, &name, &folder)?;
        self.mirror(&project.id)?;
        Ok(project)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn temp() -> PathBuf {
        let root =
            std::env::temp_dir().join(format!("editorkrab-projects-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        root
    }

    #[test]
    fn names_become_safe_unique_folders() {
        let root = temp();
        assert_eq!(folder_name("  งาน: ใหม่/ตอน*1.  "), "งาน ใหม่ ตอน 1");
        assert_eq!(folder_name("con"), "");
        assert_eq!(folder_name("COM1"), "");
        assert!(create_folder(&root, " ... ").is_err());
        let first = create_folder(&root, "คลิป").unwrap();
        let second = create_folder(&root, "คลิป").unwrap();
        assert!(first.ends_with("คลิป"));
        assert!(second.ends_with("คลิป (2)"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn saved_draft_is_mirrored_and_reopens_from_the_folder_alone() {
        let root = temp();
        let mut store = Store::open(Path::new(":memory:")).unwrap();
        let project = store
            .create_project(&root, "รีวิว", None, &json!({"files":[]}))
            .unwrap();
        let folder = PathBuf::from(&project.folder);
        assert_eq!(read_file(&folder).unwrap().document["title"], "รีวิว");
        store
            .save(&project.id, "edit", 1, &json!({"files":[],"title":"แก้แล้ว"}))
            .unwrap();
        store.mirror(&project.id).unwrap();
        assert_eq!(read_file(&folder).unwrap().revision, 2);
        // another machine: empty cache, same folder
        let mut fresh = Store::open(Path::new(":memory:")).unwrap();
        let opened = fresh.open_project(&folder).unwrap();
        assert_eq!(opened.id, project.id);
        assert_eq!(
            fresh.read(&opened.id).unwrap().unwrap().document["title"],
            "แก้แล้ว"
        );
        assert!(fresh.legacy_drafts().unwrap().is_empty());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn copied_folder_gets_its_own_id_and_old_drafts_can_move_into_a_folder() {
        let root = temp();
        let mut store = Store::open(Path::new(":memory:")).unwrap();
        let project = store
            .create_project(&root, "ต้นฉบับ", None, &json!({"files":[]}))
            .unwrap();
        let copy = root.join("สำเนา");
        fs::create_dir(&copy).unwrap();
        fs::copy(Path::new(&project.folder).join(FILE), copy.join(FILE)).unwrap();
        let opened = store.open_project(&copy).unwrap();
        assert_ne!(opened.id, project.id);
        assert_eq!(read_file(&copy).unwrap().project_id, opened.id);
        assert_eq!(store.projects().unwrap().len(), 2);

        store
            .save("old-draft", "a", 0, &json!({"files":[],"title":"งานเก่า"}))
            .unwrap();
        let legacy = store.legacy_drafts().unwrap();
        assert_eq!((legacy.len(), legacy[0].title.as_str()), (1, "งานเก่า"));
        let moved = store
            .create_project(&root, "งานเก่า", Some("old-draft"), &json!({}))
            .unwrap();
        assert_eq!(moved.id, "old-draft");
        assert_eq!(
            read_file(Path::new(&moved.folder)).unwrap().document["title"],
            "งานเก่า"
        );
        assert!(store.legacy_drafts().unwrap().is_empty());
        assert!(matches!(
            store.create_project(&root, "อีก", Some("old-draft"), &json!({})),
            Err(Error::OperationConflict)
        ));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn only_paths_inside_the_project_count_as_its_files() {
        let root = temp();
        let folder = create_folder(&root, "p").unwrap();
        fs::create_dir(folder.join(MEDIA)).unwrap();
        fs::write(folder.join(MEDIA).join("a.mp4"), b"x").unwrap();
        fs::write(root.join("outside.mp4"), b"x").unwrap();
        assert!(inside(&folder, &folder.join(MEDIA).join("a.mp4")));
        assert!(!inside(
            &folder,
            &folder.join(MEDIA).join("..").join("..").join("outside.mp4")
        ));
        assert!(!inside(&folder, &folder));
        fs::remove_dir_all(root).unwrap();
    }
}
