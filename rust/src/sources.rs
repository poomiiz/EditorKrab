//! Native selection returns exact paths and file stamps; no filename-based guessing.
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
