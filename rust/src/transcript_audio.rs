//! Seekable source preview reads only an acknowledged source, without transcoding it.
use crate::{drafts::Store, sources};
use axum::{
    Json,
    body::{Body, Bytes},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
};
use serde_json::json;
use std::{
    io,
    path::PathBuf,
    sync::{Arc, Mutex},
};
use tokio::io::{AsyncReadExt, AsyncSeekExt};

#[derive(Debug, thiserror::Error)]
pub enum AudioError {
    #[error("invalid_source_request")]
    InvalidInput,
    #[error("source_not_found")]
    NotFound,
    #[error("source_changed")]
    Changed,
    #[error("source_preview_unsupported")]
    Unsupported,
    #[error("source_read_failed")]
    Read,
    #[error("source_preview_unavailable")]
    Unavailable,
    #[error("source_range_unsatisfiable")]
    Range(u64),
}

impl IntoResponse for AudioError {
    fn into_response(self) -> Response {
        let status = match self {
            Self::InvalidInput => StatusCode::BAD_REQUEST,
            Self::NotFound => StatusCode::NOT_FOUND,
            Self::Changed => StatusCode::CONFLICT,
            Self::Unsupported => StatusCode::UNSUPPORTED_MEDIA_TYPE,
            Self::Read => StatusCode::UNPROCESSABLE_ENTITY,
            Self::Unavailable => StatusCode::SERVICE_UNAVAILABLE,
            Self::Range(_) => StatusCode::RANGE_NOT_SATISFIABLE,
        };
        let mut response = (status, Json(json!({"error":self.to_string()}))).into_response();
        response
            .headers_mut()
            .insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
        if let Self::Range(size) = self {
            response.headers_mut().insert(
                header::CONTENT_RANGE,
                format!("bytes */{size}").parse().unwrap(),
            );
            response
                .headers_mut()
                .insert(header::ACCEPT_RANGES, "bytes".parse().unwrap());
        }
        response
    }
}

#[derive(Debug, PartialEq)]
struct ByteRange {
    start: u64,
    end: u64,
}

fn byte_range(headers: &HeaderMap, size: u64) -> Result<Option<ByteRange>, AudioError> {
    let mut values = headers.get_all(header::RANGE).iter();
    let Some(value) = values.next() else {
        return Ok(None);
    };
    if values.next().is_some() || value.as_bytes().len() > 128 {
        return Err(AudioError::Range(size));
    }
    let value = value.to_str().map_err(|_| AudioError::Range(size))?;
    // No validator is advertised, so If-Range cannot be matched safely.
    if headers.contains_key(header::IF_RANGE) {
        return Ok(None);
    }
    let Some((unit, range)) = value.split_once('=') else {
        return Err(AudioError::Range(size));
    };
    // Ignore unsupported range units and multipart requests; never allocate a multipart response.
    if !unit.eq_ignore_ascii_case("bytes") || range.contains(',') {
        return Ok(None);
    }
    let Some((start, end)) = range.split_once('-') else {
        return Err(AudioError::Range(size));
    };
    let number = |s: &str| {
        if !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()) {
            s.parse::<u64>().ok()
        } else {
            None
        }
    };
    if size == 0 {
        return Err(AudioError::Range(size));
    }
    let (start, end) = if start.is_empty() {
        let suffix = number(end)
            .filter(|&n| n > 0)
            .ok_or(AudioError::Range(size))?;
        (size.saturating_sub(suffix), size - 1)
    } else {
        let start = number(start)
            .filter(|&n| n < size)
            .ok_or(AudioError::Range(size))?;
        let end = if end.is_empty() {
            size - 1
        } else {
            number(end).ok_or(AudioError::Range(size))?.min(size - 1)
        };
        if end < start {
            return Err(AudioError::Range(size));
        }
        (start, end)
    };
    Ok(Some(ByteRange { start, end }))
}

fn content_type(path: &std::path::Path) -> Result<&'static str, AudioError> {
    match path
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "wav" => Ok("audio/wav"),
        "mp3" => Ok("audio/mpeg"),
        "m4a" => Ok("audio/mp4"),
        "aac" => Ok("audio/aac"),
        "flac" => Ok("audio/flac"),
        "ogg" | "opus" => Ok("audio/ogg"),
        "mp4" | "m4v" => Ok("video/mp4"),
        "mov" => Ok("video/quicktime"),
        "webm" => Ok("video/webm"),
        _ => Err(AudioError::Unsupported),
    }
}

fn read_error(error: io::Error) -> AudioError {
    if error.kind() == io::ErrorKind::NotFound {
        AudioError::NotFound
    } else {
        AudioError::Read
    }
}

/// The draft's acknowledged source for `source_id`, opened and checked against its recorded size and stamp.
async fn acknowledged(
    store: Arc<Mutex<Store>>,
    project: String,
    source_id: String,
) -> Result<(PathBuf, tokio::fs::File, u64, String), AudioError> {
    if !crate::jobs::identifier(&project) || !crate::jobs::identifier(&source_id) {
        return Err(AudioError::InvalidInput);
    }
    let (path, size, modified_ns) = tokio::task::spawn_blocking(move || {
        let store = store.lock().map_err(|_| AudioError::Unavailable)?;
        let draft = store
            .read(&project)
            .map_err(|_| AudioError::Unavailable)?
            .ok_or(AudioError::NotFound)?;
        let source = draft.document["files"]
            .as_array()
            .and_then(|files| {
                files
                    .iter()
                    .find(|f| f["source_id"].as_str() == Some(&source_id))
            })
            .ok_or(AudioError::NotFound)?;
        let path = PathBuf::from(source["path"].as_str().ok_or(AudioError::InvalidInput)?);
        if !path.is_absolute() {
            return Err(AudioError::InvalidInput);
        }
        let size = source["size"].as_u64().ok_or(AudioError::InvalidInput)?;
        let stamp = source["modified_ns"]
            .as_str()
            .ok_or(AudioError::InvalidInput)?
            .to_owned();
        Ok((path, size, stamp))
    })
    .await
    .map_err(|_| AudioError::Unavailable)??;
    let file = tokio::fs::File::open(&path).await.map_err(read_error)?;
    // Check the opened handle, rather than metadata from a separate path lookup.
    let metadata = file.metadata().await.map_err(read_error)?;
    if !metadata.is_file()
        || metadata.len() != size
        || sources::stamp(&metadata).map_err(read_error)? != modified_ns
    {
        return Err(AudioError::Changed);
    }
    Ok((path, file, size, modified_ns))
}

pub async fn serve(
    store: Arc<Mutex<Store>>,
    project: String,
    source_id: String,
    headers: HeaderMap,
) -> Result<Response, AudioError> {
    let (path, file, size, _) = acknowledged(store, project, source_id).await?;
    stream(file, size, content_type(&path)?, &headers).await
}

/// A 360p H.264 copy of the source, made once per source version with ffmpeg and kept in `cache`.
/// Phone footage is often 4K HEVC HDR, which many browsers cannot play; this copy plays everywhere.
pub async fn serve_preview(
    store: Arc<Mutex<Store>>,
    project: String,
    source_id: String,
    headers: HeaderMap,
    ffmpeg: PathBuf,
    cache: PathBuf,
) -> Result<Response, AudioError> {
    let (path, _, size, stamp) = acknowledged(store, project, source_id.clone()).await?;
    let target = cache.join(format!("{source_id}-{size}-{stamp}.mp4"));
    // one encode at a time: a second request for the same video waits for the first instead of encoding twice
    static ENCODING: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());
    if !target.is_file() {
        let _one = ENCODING.lock().await;
        if !target.is_file() {
            tokio::fs::create_dir_all(&cache)
                .await
                .map_err(|_| AudioError::Unavailable)?;
            let partial = target.with_extension("part.mp4");
            // NVIDIA decode + scale + encode is about 3x faster on 4K HEVC (60 s of a phone clip: 11 s vs 30 s);
            // without an NVIDIA GPU, or if that fails, the CPU makes the same 360p copy
            let gpu: &[&str] = &["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i"];
            let gpu_out: &[&str] = &[
                "-vf",
                "scale_cuda=-2:360:format=yuv420p,hwdownload,format=yuv420p",
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p1",
                "-cq",
                "32",
            ];
            let cpu_out: &[&str] = &[
                "-vf",
                "scale=-2:360,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "30",
            ];
            let mut ok = false;
            for (input, output) in [(gpu, gpu_out), (&["-i"][..], cpu_out)] {
                let mut command = tokio::process::Command::new(&ffmpeg);
                command
                    .args(["-hide_banner", "-nostdin", "-v", "error", "-y"])
                    .args(input)
                    .arg(&path)
                    .args(output)
                    .args([
                        "-c:a",
                        "aac",
                        "-b:a",
                        "64k",
                        "-ac",
                        "1",
                        "-movflags",
                        "+faststart",
                    ])
                    .arg(&partial)
                    .kill_on_drop(true);
                #[cfg(windows)]
                command.creation_flags(0x0800_0000);
                ok = command.status().await.map(|s| s.success()).unwrap_or(false);
                if ok {
                    break;
                }
            }
            if !ok || tokio::fs::rename(&partial, &target).await.is_err() {
                let _ = tokio::fs::remove_file(&partial).await;
                return Err(AudioError::Read);
            }
            forget_old_previews(&cache, &target).await;
        }
    }
    let file = tokio::fs::File::open(&target).await.map_err(read_error)?;
    let size = file.metadata().await.map_err(read_error)?.len();
    stream(file, size, "video/mp4", &headers).await
}

/// Previews nobody opened for 14 days are deleted when a new one is made; they are only copies.
async fn forget_old_previews(cache: &std::path::Path, keep: &std::path::Path) {
    let Ok(mut entries) = tokio::fs::read_dir(cache).await else {
        return;
    };
    let old = std::time::SystemTime::now() - std::time::Duration::from_secs(14 * 24 * 3600);
    while let Ok(Some(entry)) = entries.next_entry().await {
        let path = entry.path();
        let stale = entry
            .metadata()
            .await
            .ok()
            .and_then(|m| m.accessed().or_else(|_| m.modified()).ok())
            .is_some_and(|t| t < old);
        if stale && path != keep && path.extension().is_some_and(|e| e == "mp4") {
            let _ = tokio::fs::remove_file(path).await;
        }
    }
}

async fn stream(
    mut file: tokio::fs::File,
    size: u64,
    mime: &'static str,
    headers: &HeaderMap,
) -> Result<Response, AudioError> {
    let range = byte_range(headers, size)?;
    let (start, length, status) = range.as_ref().map_or((0, size, StatusCode::OK), |r| {
        (r.start, r.end - r.start + 1, StatusCode::PARTIAL_CONTENT)
    });
    file.seek(io::SeekFrom::Start(start))
        .await
        .map_err(read_error)?;
    let stream = async_stream::stream! {
        let mut buffer = vec![0;64*1024];
        let mut remaining = length;
        while remaining>0 {
            let limit = remaining.min(buffer.len() as u64) as usize;
            match file.read(&mut buffer[..limit]).await {
                Ok(0) => {
                    yield Err::<Bytes,io::Error>(io::Error::new(io::ErrorKind::UnexpectedEof,"source ended during preview"));
                    break;
                },
                Ok(count) => {
                    remaining-=count as u64;
                    yield Ok(Bytes::copy_from_slice(&buffer[..count]));
                },
                Err(error) => { yield Err(error); break; }
            }
        }
    };
    let mut response = Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, mime)
        .header(header::CONTENT_LENGTH, length)
        .header(header::ACCEPT_RANGES, "bytes")
        .header(header::CACHE_CONTROL, "no-store")
        .header("x-content-type-options", "nosniff")
        .header("cross-origin-resource-policy", "same-origin");
    if let Some(range) = range {
        response = response.header(
            header::CONTENT_RANGE,
            format!("bytes {}-{}/{size}", range.start, range.end),
        );
    }
    response
        .body(Body::from_stream(stream))
        .map_err(|_| AudioError::Unavailable)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs, path::Path};

    fn headers(value: &str) -> HeaderMap {
        let mut result = HeaderMap::new();
        result.insert(header::RANGE, value.parse().unwrap());
        result
    }

    #[test]
    fn byte_ranges_clamp_support_suffix_and_reject_unsatisfiable_or_abusive_inputs() {
        for (value, start, end) in [
            ("bytes=0-0", 0, 0),
            ("bytes=2-5", 2, 5),
            ("bytes=8-999", 8, 9),
            ("bytes=5-", 5, 9),
            ("bytes=-3", 7, 9),
            ("bytes=-50", 0, 9),
            ("BYTES=1-2", 1, 2),
        ] {
            assert_eq!(
                byte_range(&headers(value), 10).unwrap(),
                Some(ByteRange { start, end })
            );
        }
        for value in [
            "bytes=10-",
            "bytes=7-2",
            "bytes=-0",
            "bytes=-",
            "bytes=foo-2",
            "bytes=+1-2",
            "bytes=1-2-3",
            "bytes=18446744073709551616-",
            "bytes=1-18446744073709551616",
            "bytes=1 -2",
            "bytes",
        ] {
            assert!(
                matches!(byte_range(&headers(value), 10), Err(AudioError::Range(10))),
                "{value}"
            );
        }
        assert!(matches!(
            byte_range(&headers("bytes=0-0"), 0),
            Err(AudioError::Range(0))
        ));
        assert!(matches!(
            byte_range(&headers(&format!("bytes={}-", "0".repeat(129))), 10),
            Err(AudioError::Range(10))
        ));
        let mut duplicate = headers("bytes=0-1");
        duplicate.append(header::RANGE, "bytes=5-6".parse().unwrap());
        assert!(matches!(
            byte_range(&duplicate, 10),
            Err(AudioError::Range(10))
        ));
    }

    #[test]
    fn unsupported_units_multipart_and_unmatched_if_range_are_ignored() {
        assert_eq!(byte_range(&HeaderMap::new(), 0).unwrap(), None);
        assert_eq!(byte_range(&headers("frames=0-1"), 10).unwrap(), None);
        assert_eq!(byte_range(&headers("bytes=0-1,5-6"), 10).unwrap(), None);
        let mut conditional = headers("bytes=0-1");
        conditional.insert(header::IF_RANGE, "\"unknown\"".parse().unwrap());
        assert_eq!(byte_range(&conditional, 10).unwrap(), None);
    }

    struct Fixture {
        root: PathBuf,
        path: PathBuf,
        store: Arc<Mutex<Store>>,
    }
    impl Fixture {
        fn new(extension: &str, bytes: &[u8]) -> Self {
            let root =
                std::env::temp_dir().join(format!("editorkrab-audio-{}", uuid::Uuid::new_v4()));
            fs::create_dir(&root).unwrap();
            let path = root.join(format!("เสียง.{extension}"));
            fs::write(&path, bytes).unwrap();
            let metadata = path.metadata().unwrap();
            let mut store = Store::open(Path::new(":memory:")).unwrap();
            store.save("project","select",0,&json!({"files":[{"source_id":"selected","name":"เสียง","path":path.to_str().unwrap(),"size":metadata.len(),"modified_ns":sources::stamp(&metadata).unwrap()}]})).unwrap();
            Self {
                root,
                path,
                store: Arc::new(Mutex::new(store)),
            }
        }
        async fn request(&self, headers: HeaderMap) -> Result<Response, AudioError> {
            serve(
                self.store.clone(),
                "project".into(),
                "selected".into(),
                headers,
            )
            .await
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.root).unwrap();
        }
    }

    #[tokio::test]
    async fn full_and_partial_preview_leave_source_and_draft_unchanged() {
        let bytes = (0..200_000).map(|i| (i % 251) as u8).collect::<Vec<_>>();
        let fixture = Fixture::new("wav", &bytes);
        let original_stamp = sources::stamp(&fixture.path.metadata().unwrap()).unwrap();
        let full = fixture.request(HeaderMap::new()).await.unwrap();
        assert_eq!(full.status(), StatusCode::OK);
        assert_eq!(full.headers()[header::CONTENT_LENGTH], "200000");
        assert_eq!(full.headers()[header::CONTENT_TYPE], "audio/wav");
        assert_eq!(full.headers()[header::ACCEPT_RANGES], "bytes");
        assert_eq!(full.headers()["x-content-type-options"], "nosniff");
        assert_eq!(
            full.headers()["cross-origin-resource-policy"],
            "same-origin"
        );
        assert_eq!(
            axum::body::to_bytes(full.into_body(), 200_000)
                .await
                .unwrap()
                .as_ref(),
            bytes.as_slice()
        );
        let partial = fixture.request(headers("bytes=65530-65545")).await.unwrap();
        assert_eq!(partial.status(), StatusCode::PARTIAL_CONTENT);
        assert_eq!(partial.headers()[header::CONTENT_LENGTH], "16");
        assert_eq!(
            partial.headers()[header::CONTENT_RANGE],
            "bytes 65530-65545/200000"
        );
        assert_eq!(
            axum::body::to_bytes(partial.into_body(), 16)
                .await
                .unwrap()
                .as_ref(),
            &bytes[65530..65546]
        );
        assert_eq!(
            fixture
                .store
                .lock()
                .unwrap()
                .read("project")
                .unwrap()
                .unwrap()
                .revision,
            1
        );
        assert_eq!(fs::read(&fixture.path).unwrap(), bytes);
        assert_eq!(
            sources::stamp(&fixture.path.metadata().unwrap()).unwrap(),
            original_stamp
        );
    }

    #[tokio::test]
    async fn rejected_ranges_report_current_size_and_empty_files_work() {
        let fixture = Fixture::new("wav", &[]);
        let full = fixture.request(HeaderMap::new()).await.unwrap();
        assert_eq!(full.status(), StatusCode::OK);
        assert_eq!(full.headers()[header::CONTENT_LENGTH], "0");
        assert!(
            axum::body::to_bytes(full.into_body(), 0)
                .await
                .unwrap()
                .is_empty()
        );
        let response = fixture
            .request(headers("bytes=0-"))
            .await
            .unwrap_err()
            .into_response();
        assert_eq!(response.status(), StatusCode::RANGE_NOT_SATISFIABLE);
        assert_eq!(response.headers()[header::CONTENT_RANGE], "bytes */0");
    }

    #[tokio::test]
    async fn source_resolution_rejects_unselected_missing_changed_and_unsupported_files() {
        let fixture = Fixture::new("wav", b"same-length");
        for (project, source) in [("project", "unknown"), ("unknown", "selected")] {
            assert!(matches!(
                serve(
                    fixture.store.clone(),
                    project.into(),
                    source.into(),
                    HeaderMap::new()
                )
                .await,
                Err(AudioError::NotFound)
            ));
        }
        assert!(matches!(
            serve(
                fixture.store.clone(),
                "project".into(),
                "../selected".into(),
                HeaderMap::new()
            )
            .await,
            Err(AudioError::InvalidInput)
        ));
        let original = fixture.path.metadata().unwrap().modified().unwrap();
        fs::OpenOptions::new()
            .write(true)
            .open(&fixture.path)
            .unwrap()
            .set_modified(original + std::time::Duration::from_secs(1))
            .unwrap();
        assert!(matches!(
            fixture.request(HeaderMap::new()).await,
            Err(AudioError::Changed)
        ));
        fs::write(&fixture.path, b"changed size").unwrap();
        assert!(matches!(
            fixture.request(HeaderMap::new()).await,
            Err(AudioError::Changed)
        ));
        fs::remove_file(&fixture.path).unwrap();
        assert!(matches!(
            fixture.request(HeaderMap::new()).await,
            Err(AudioError::NotFound)
        ));
        let unsupported = Fixture::new("avi", b"video");
        assert!(matches!(
            unsupported.request(HeaderMap::new()).await,
            Err(AudioError::Unsupported)
        ));
        assert_eq!(
            fixture
                .store
                .lock()
                .unwrap()
                .read("project")
                .unwrap()
                .unwrap()
                .revision,
            1
        );
    }
}
