//! Native selection returns exact paths and file stamps. A dropped file is only matched when
//! exactly one file on disk has its name, byte size and modified millisecond; otherwise the
//! person picks it.
use serde::{Deserialize, Serialize};
use std::{
    collections::{HashSet, VecDeque},
    fs,
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};

pub const EXTENSIONS: &[&str] = &[
    "mp4", "mov", "m4v", "mkv", "avi", "webm", "mts", "m2ts", "wav", "mp3", "m4a", "aac", "flac",
    "ogg", "opus",
];

#[derive(Debug, thiserror::Error)]
pub enum SourceError {
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error("source_limit_exceeded")]
    Limit,
    #[error("source_path_not_unicode")]
    Path,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Identity {
    pub id: String,
    pub size: u64,
    pub modified_ns: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Source {
    pub source_id: String,
    pub name: String,
    pub path: String,
    pub size: u64,
    pub modified_ns: String,
}

#[derive(Debug, Serialize)]
pub struct Selection {
    pub files: Vec<Source>,
    pub skipped: usize,
    pub cancelled: bool,
}

pub fn stamp(metadata: &fs::Metadata) -> Result<String, std::io::Error> {
    let time = metadata.modified()?;
    Ok(match time.duration_since(std::time::UNIX_EPOCH) {
        Ok(d) => d.as_nanos().to_string(),
        Err(e) => format!("-{}", e.duration().as_nanos()),
    })
}

fn supported(path: &Path) -> bool {
    path.extension()
        .and_then(|s| s.to_str())
        .is_some_and(|s| EXTENSIONS.contains(&s.to_ascii_lowercase().as_str()))
}

pub fn collect(paths: Vec<PathBuf>) -> Result<Selection, SourceError> {
    let mut files = Vec::new();
    let mut skipped = 0;
    let mut seen = HashSet::new();
    let mut pending = VecDeque::new();
    for path in paths {
        let canonical = path.canonicalize()?;
        let base = if canonical.is_dir() {
            canonical.parent().unwrap_or(&canonical).to_owned()
        } else {
            canonical.parent().unwrap().to_owned()
        };
        pending.push_back((canonical, base));
    }
    let mut directories = HashSet::new();
    while let Some((path, base)) = pending.pop_front() {
        if path.is_dir() {
            if !directories.insert(path.clone()) {
                continue;
            }
            if directories.len() > 10_000 {
                return Err(SourceError::Limit);
            }
            let mut entries = fs::read_dir(&path)?.collect::<Result<Vec<_>, _>>()?;
            entries.sort_by_key(|e| e.file_name());
            for entry in entries {
                if entry.file_type()?.is_symlink() {
                    skipped += 1;
                    continue;
                }
                pending.push_back((entry.path(), base.clone()));
            }
        } else if supported(&path) {
            let canonical = path.canonicalize()?;
            if !seen.insert(canonical.clone()) {
                continue;
            }
            let metadata = canonical.metadata()?;
            if !metadata.is_file() {
                skipped += 1;
                continue;
            }
            if files.len() >= 1000 {
                return Err(SourceError::Limit);
            }
            let name = path
                .strip_prefix(&base)
                .unwrap_or(&path)
                .to_str()
                .ok_or(SourceError::Path)?
                .to_owned();
            files.push(Source {
                source_id: uuid::Uuid::new_v4().to_string(),
                name,
                path: canonical.to_str().ok_or(SourceError::Path)?.to_owned(),
                size: metadata.len(),
                modified_ns: stamp(&metadata)?,
            });
        } else {
            skipped += 1;
        }
    }
    Ok(Selection {
        files,
        skipped,
        cancelled: false,
    })
}

/// A dropped file as the browser describes it: its name, byte size and last-modified time
/// in milliseconds. The browser never says where the file is.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Dropped {
    pub name: String,
    pub size: u64,
    pub last_modified: i64,
}

fn modified_ms(metadata: &fs::Metadata) -> Option<i64> {
    let time = metadata.modified().ok()?;
    let d = time.duration_since(std::time::UNIX_EPOCH).ok()?;
    i64::try_from(d.as_millis()).ok()
}

/// Finds each dropped file under `roots` by exact name, byte size and modified millisecond.
/// A file is only taken when exactly one file on disk matches all three; anything else stays
/// None and the page asks the person to pick it. The walk stops after `budget` folders.
pub fn locate(dropped: &[Dropped], roots: &[PathBuf], depth: usize, budget: usize) -> Vec<Option<Source>> {
    let mut found: Vec<Vec<PathBuf>> = vec![Vec::new(); dropped.len()];
    let mut seen = HashSet::new();
    let mut pending: VecDeque<(PathBuf, usize)> = roots.iter().map(|r| (r.clone(), 0)).collect();
    let mut visited = 0;
    while let Some((dir, level)) = pending.pop_front() {
        if visited >= budget || !seen.insert(dir.clone()) {
            continue;
        }
        visited += 1;
        let Ok(entries) = fs::read_dir(&dir) else { continue };
        for entry in entries.flatten() {
            let Ok(kind) = entry.file_type() else { continue };
            if kind.is_symlink() {
                continue;
            }
            if kind.is_dir() {
                let hidden = entry.file_name().to_str().is_none_or(|n| {
                    n.starts_with('.') || n.starts_with('$') || matches!(n, "Windows" | "Program Files" | "Program Files (x86)" | "ProgramData" | "AppData" | "node_modules")
                });
                if level < depth && !hidden {
                    pending.push_back((entry.path(), level + 1));
                }
                continue;
            }
            let name = entry.file_name();
            for (i, want) in dropped.iter().enumerate() {
                if name.to_str() != Some(want.name.as_str()) {
                    continue;
                }
                let Ok(metadata) = entry.metadata() else { continue };
                if metadata.len() == want.size && modified_ms(&metadata) == Some(want.last_modified) {
                    let path = entry.path().canonicalize().unwrap_or_else(|_| entry.path());
                    if !found[i].contains(&path) {
                        found[i].push(path);
                    }
                }
            }
        }
    }
    found
        .into_iter()
        .zip(dropped)
        .map(|(paths, want)| {
            let [path] = paths.as_slice() else { return None };
            let metadata = path.metadata().ok()?;
            Some(Source {
                source_id: uuid::Uuid::new_v4().to_string(),
                name: want.name.clone(),
                path: path.to_str()?.to_owned(),
                size: metadata.len(),
                modified_ns: stamp(&metadata).ok()?,
            })
        })
        .collect()
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PickKind {
    Files,
    Folder,
}

pub struct PickerLease(pub Arc<AtomicBool>);
impl Drop for PickerLease {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}
pub struct PickCommand {
    pub kind: PickKind,
    pub answer: tokio::sync::oneshot::Sender<Vec<PathBuf>>,
    pub lease: PickerLease,
}

/// The executable's main OS thread dispatches dialogs, required by non-windowed macOS.
pub fn dispatch(command: PickCommand) {
    if command.answer.is_closed() {
        return;
    }
    let dialog = rfd::FileDialog::new()
        .set_title("EditorKrab · เลือกฟุต")
        .add_filter("Video / Audio", EXTENSIONS);
    let paths = match command.kind {
        PickKind::Files => dialog.pick_files(),
        PickKind::Folder => dialog.pick_folder().map(|p| vec![p]),
    };
    let _ = command.answer.send(paths.unwrap_or_default());
    drop(command.lease);
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn a_dropped_file_is_found_only_when_name_size_and_time_all_match_once() {
        let root = std::env::temp_dir().join(format!("editorkrab-locate-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(root.join("a/b")).unwrap();
        fs::create_dir_all(root.join("c")).unwrap();
        fs::write(root.join("a/b/คลิป.mov"), b"video").unwrap();
        fs::write(root.join("c/same.mp4"), b"x").unwrap();
        fs::write(root.join("a/same.mp4"), b"x").unwrap();
        let ms = |p: &str| modified_ms(&root.join(p).metadata().unwrap()).unwrap();
        let wanted = [
            Dropped { name: "คลิป.mov".into(), size: 5, last_modified: ms("a/b/คลิป.mov") },
            Dropped { name: "คลิป.mov".into(), size: 6, last_modified: ms("a/b/คลิป.mov") },
            Dropped { name: "same.mp4".into(), size: 1, last_modified: ms("c/same.mp4") },
        ];
        let found = locate(&wanted, &[root.clone()], 4, 100);
        assert!(found[0].as_ref().unwrap().path.ends_with("คลิป.mov"));
        assert!(found[1].is_none(), "a different size is a different file");
        let twins = ms("c/same.mp4") == ms("a/same.mp4");
        assert_eq!(found[2].is_none(), twins, "two matching files means asking, not guessing");
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn nested_selection_keeps_exact_paths_and_does_not_duplicate_inputs() {
        let root =
            std::env::temp_dir().join(format!("editorkrab-sources-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(root.join("ชุดสอง")).unwrap();
        fs::write(root.join("เสียง.wav"), b"audio").unwrap();
        fs::write(root.join("ชุดสอง/ภาพ.MP4"), b"video").unwrap();
        fs::write(root.join("notes.txt"), b"ignore").unwrap();
        let selected = collect(vec![root.clone(), root.join("เสียง.wav")]).unwrap();
        assert_eq!(selected.files.len(), 2);
        assert_eq!(selected.skipped, 1);
        assert!(
            selected
                .files
                .iter()
                .all(|f| Path::new(&f.path).is_absolute())
        );
        let source = selected
            .files
            .iter()
            .find(|f| f.name.ends_with("เสียง.wav"))
            .unwrap();
        assert_eq!(source.size, 5);
        assert_eq!(
            source.modified_ns,
            stamp(&Path::new(&source.path).metadata().unwrap()).unwrap()
        );
        fs::remove_dir_all(root).unwrap();
    }
}
