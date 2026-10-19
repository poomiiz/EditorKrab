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

fn source(path: &Path, name: String) -> Result<Source, SourceError> {
    let metadata = path.metadata()?;
    Ok(Source {
        source_id: uuid::Uuid::new_v4().to_string(),
        name,
        path: path.to_str().ok_or(SourceError::Path)?.to_owned(),
        size: metadata.len(),
        modified_ns: stamp(&metadata)?,
    })
}

fn file_name(path: &str) -> &str {
    path.rsplit(['/', '\\']).next().unwrap_or(path)
}

fn same_name(a: &str, b: &str) -> bool {
    if cfg!(any(windows, target_os = "macos")) {
        a.to_lowercase() == b.to_lowercase()
    } else {
        a == b
    }
}

/// A linked video as the project last saw it.
#[derive(Debug, Clone, Deserialize)]
pub struct Linked {
    pub path: String,
    pub size: u64,
    pub modified_ns: String,
}

/// `ok`, `missing` (nothing at the path) or `changed` (a different file is there now).
pub fn check(files: &[Linked]) -> Vec<&'static str> {
    files
        .iter()
        .map(|file| match Path::new(&file.path).metadata() {
            Ok(m) if m.is_file() => {
                if m.len() == file.size && stamp(&m).is_ok_and(|s| s == file.modified_ns) {
                    "ok"
                } else {
                    "changed"
                }
            }
            _ => "missing",
        })
        .collect()
}

/// After the person points at one moved video, the others that went missing with it are looked
/// for in the same new folder: same file name and same size.
pub fn relink_siblings(picked: &Path, missing: &[Linked]) -> Vec<(String, Source)> {
    let Some(folder) = picked.parent() else {
        return vec![];
    };
    let Ok(entries) = fs::read_dir(folder) else {
        return vec![];
    };
    let entries: Vec<PathBuf> = entries.filter_map(|e| e.ok().map(|e| e.path())).collect();
    missing
        .iter()
        .filter_map(|old| {
            let found = entries.iter().find(|p| {
                p.file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(|n| same_name(n, file_name(&old.path)))
                    && p.metadata()
                        .is_ok_and(|m| m.is_file() && m.len() == old.size)
            })?;
            let name = found.file_name()?.to_str()?.to_owned();
            Some((old.path.clone(), source(found, name).ok()?))
        })
        .collect()
}

pub fn picked(path: &Path) -> Result<Source, SourceError> {
    let canonical = path.canonicalize()?;
    let name = canonical
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or(SourceError::Path)?
        .to_owned();
    source(&canonical, name)
}

/// What a browser drop tells us about a file: no path, only its name, size and modified time.
#[derive(Debug, Clone, Deserialize)]
pub struct Dropped {
    pub name: String,
    pub size: u64,
    /// milliseconds since 1970, as the browser reports it
    pub last_modified: u64,
}

/// Finds where dropped videos already live so the project can link them instead of copying.
/// A drop is linked only when exactly one file matches its name, size and modified time.
pub fn locate(roots: &[PathBuf], dropped: &[Dropped]) -> Vec<Option<Source>> {
    const DEPTH: usize = 4;
    const ENTRIES: usize = 60_000;
    let mut matches: Vec<HashSet<PathBuf>> = vec![HashSet::new(); dropped.len()];
    let mut seen = HashSet::new();
    let mut visited = 0;
    let mut pending: VecDeque<(PathBuf, usize)> = roots.iter().map(|r| (r.clone(), 0)).collect();
    while let Some((folder, depth)) = pending.pop_front() {
        let Ok(canonical) = folder.canonicalize() else {
            continue;
        };
        if !seen.insert(canonical.clone()) {
            continue;
        }
        let Ok(entries) = fs::read_dir(&canonical) else {
            continue;
        };
        for entry in entries.flatten() {
            visited += 1;
            if visited > ENTRIES {
                break;
            }
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            let name = entry.file_name();
            let Some(name) = name.to_str() else {
                continue;
            };
            if kind.is_dir() {
                if depth < DEPTH
                    && !name.starts_with('.')
                    && !["node_modules", "AppData"].contains(&name)
                {
                    pending.push_back((entry.path(), depth + 1));
                }
                continue;
            }
            if !kind.is_file() {
                continue;
            }
            for (index, want) in dropped.iter().enumerate() {
                if !same_name(name, file_name(&want.name)) {
                    continue;
                }
                let Ok(metadata) = entry.metadata() else {
                    continue;
                };
                let modified_ms = metadata
                    .modified()
                    .ok()
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| d.as_millis() as u64);
                // FAT drives keep times to 2 seconds
                if metadata.len() == want.size
                    && modified_ms.is_some_and(|ms| ms.abs_diff(want.last_modified) <= 2000)
                {
                    matches[index].insert(entry.path());
                }
            }
        }
    }
    matches
        .into_iter()
        .zip(dropped)
        .map(|(found, want)| {
            if found.len() != 1 {
                return None;
            }
            let path = found.into_iter().next()?.canonicalize().ok()?;
            source(&path, file_name(&want.name).to_owned()).ok()
        })
        .collect()
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PickKind {
    Files,
    Folder,
    /// one replacement for a video that went missing
    Relink,
    /// a project folder, or the folder new projects go in
    ProjectFolder,
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
        PickKind::Relink => dialog
            .set_title("EditorKrab · หาวิดีโอที่ย้ายไป")
            .pick_file()
            .map(|p| vec![p]),
        PickKind::ProjectFolder => rfd::FileDialog::new()
            .set_title("EditorKrab · เลือกโฟลเดอร์โปรเจกต์")
            .pick_folder()
            .map(|p| vec![p]),
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

    #[test]
    fn moved_videos_are_reported_missing_and_relinked_from_their_new_folder() {
        let root = std::env::temp_dir().join(format!("editorkrab-relink-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(root.join("old")).unwrap();
        fs::create_dir_all(root.join("new")).unwrap();
        fs::write(root.join("old/a.mp4"), b"aaaa").unwrap();
        fs::write(root.join("old/b.mp4"), b"bbbbbb").unwrap();
        let picked_before = collect(vec![root.join("old")]).unwrap().files;
        let linked: Vec<Linked> = picked_before
            .iter()
            .map(|f| Linked {
                path: f.path.clone(),
                size: f.size,
                modified_ns: f.modified_ns.clone(),
            })
            .collect();
        assert_eq!(check(&linked), ["ok", "ok"]);
        for name in ["a.mp4", "b.mp4"] {
            fs::rename(root.join("old").join(name), root.join("new").join(name)).unwrap();
        }
        assert_eq!(check(&linked), ["missing", "missing"]);
        let picked = picked(&root.join("new/a.mp4")).unwrap();
        assert_eq!(picked.size, 4);
        let others = relink_siblings(Path::new(&picked.path), &linked[1..]);
        assert_eq!(others.len(), 1);
        assert_eq!(others[0].0, linked[1].path);
        assert!(others[0].1.path.ends_with("b.mp4"));
        // a same-named file of another size is not the moved video
        fs::write(root.join("new/b.mp4"), b"different").unwrap();
        assert!(relink_siblings(Path::new(&picked.path), &linked[1..]).is_empty());
        assert_eq!(check(&linked[..1])[0], "missing");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn dropped_video_is_linked_only_when_one_file_matches() {
        let root = std::env::temp_dir().join(format!("editorkrab-locate-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(root.join("ถ่าย/วันแรก")).unwrap();
        fs::write(root.join("ถ่าย/วันแรก/clip.mp4"), b"video").unwrap();
        fs::write(root.join("twin.mp4"), b"same").unwrap();
        fs::create_dir_all(root.join("other")).unwrap();
        fs::copy(root.join("twin.mp4"), root.join("other/twin.mp4")).unwrap();
        let ms = |p: &Path| {
            p.metadata()
                .unwrap()
                .modified()
                .unwrap()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis() as u64
        };
        let drop = |name: &str, size, last_modified| Dropped {
            name: name.into(),
            size,
            last_modified,
        };
        let clip = ms(&root.join("ถ่าย/วันแรก/clip.mp4"));
        let twin = ms(&root.join("twin.mp4"));
        let other_twin = ms(&root.join("other/twin.mp4"));
        let found = locate(
            std::slice::from_ref(&root),
            &[
                drop("clip.mp4", 5, clip),
                drop("clip.mp4", 6, clip),
                drop("missing.mp4", 1, 0),
            ],
        );
        assert!(found[0].as_ref().unwrap().path.ends_with("clip.mp4"));
        assert!(found[1].is_none() && found[2].is_none());
        // two copies with the same stamp are ambiguous, so neither is guessed
        if twin.abs_diff(other_twin) <= 2000 {
            assert!(locate(std::slice::from_ref(&root), &[drop("twin.mp4", 4, twin)])[0].is_none());
        }
        fs::remove_dir_all(root).unwrap();
    }
}
