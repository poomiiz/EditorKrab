use editorkrab::asr_download::{Item, check_space, fetch, plan, remaining, sha256};
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

/// A local HTTP file server that honours `Range: bytes=N-` and records the ranges it was asked for.
fn serve(body: Vec<u8>) -> (String, Arc<Mutex<Vec<String>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/model.bin", listener.local_addr().unwrap());
    let ranges = Arc::new(Mutex::new(Vec::new()));
    let seen = ranges.clone();
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let mut stream = stream.unwrap();
            let mut request = Vec::new();
            let mut byte = [0; 1];
            while !request.ends_with(b"\r\n\r\n") && stream.read(&mut byte).unwrap() == 1 {
                request.push(byte[0]);
            }
            let text = String::from_utf8_lossy(&request).to_lowercase();
            let start = text
                .lines()
                .find_map(|l| l.strip_prefix("range: bytes="))
                .map(|r| {
                    seen.lock().unwrap().push(r.to_owned());
                    r.trim_end_matches('-').parse::<usize>().unwrap()
                })
                .unwrap_or(0);
            let head = if start > 0 {
                format!(
                    "HTTP/1.1 206 Partial Content\r\nContent-Length: {}\r\nContent-Range: bytes {}-{}/{}\r\nConnection: close\r\n\r\n",
                    body.len() - start,
                    start,
                    body.len() - 1,
                    body.len()
                )
            } else {
                format!(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                )
            };
            let _ = stream.write_all(head.as_bytes());
            let _ = stream.write_all(&body[start..]);
        }
    });
    (url, ranges)
}
fn curl() -> PathBuf {
    if cfg!(windows) {
        PathBuf::from(std::env::var("SystemRoot").unwrap()).join(r"System32\curl.exe")
    } else {
        PathBuf::from("curl")
    }
}
fn body() -> Vec<u8> {
    (0..200_000u32).flat_map(|n| n.to_le_bytes()).collect()
}
fn folder(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("ek-download-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}
fn item(url: String, dir: &std::path::Path, sha: String) -> Item {
    Item {
        url,
        size: body().len() as u64,
        sha256: sha,
        dest: dir.join("models").join("ggml-test.bin"),
        unzip: false,
    }
}
fn hash(bytes: &[u8]) -> String {
    // one folder per call: tests run in parallel and `folder` empties the folder it returns
    static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let dir = folder(&format!("hash-{}", NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)));
    let path = dir.join("x");
    std::fs::write(&path, bytes).unwrap();
    sha256(&path).unwrap()
}
fn idle() -> tokio::sync::watch::Receiver<bool> {
    let (tx, rx) = tokio::sync::watch::channel(false);
    std::mem::forget(tx);
    rx
}

#[tokio::test]
async fn resumes_a_part_file_and_installs_only_after_the_checksum_matches() {
    let data = body();
    let (url, ranges) = serve(data.clone());
    let dir = folder("resume");
    let item = item(url, &dir, hash(&data));
    std::fs::create_dir_all(item.part().parent().unwrap()).unwrap();
    std::fs::write(item.part(), &data[..300_000]).unwrap();
    assert_eq!(remaining(std::slice::from_ref(&item)), item.size - 300_000);
    fetch(&item, &curl(), idle()).await.unwrap();
    assert_eq!(ranges.lock().unwrap().as_slice(), ["300000-"]);
    assert_eq!(std::fs::read(&item.dest).unwrap(), data);
    assert!(!item.part().exists());
}

#[tokio::test]
async fn a_wrong_checksum_fails_and_discards_the_part() {
    let (url, _) = serve(body());
    let dir = folder("checksum");
    let item = item(url, &dir, "0".repeat(64));
    assert_eq!(
        fetch(&item, &curl(), idle()).await.unwrap_err(),
        "checksum_mismatch"
    );
    assert!(!item.dest.exists());
    assert!(!item.part().exists());
}

#[test]
fn refuses_to_start_without_enough_free_space() {
    let dir = folder("space");
    let items = plan(&dir, false, true);
    assert_eq!(items.len(), 1);
    let error = check_space(1_000, &items, "C").unwrap_err();
    assert_eq!(error, format!("disk_space:C:{}", items[0].size - 1_000));
    assert!(check_space(items[0].size, &items, "C").is_ok());
}

#[test]
fn plans_only_what_is_missing() {
    let dir = folder("plan");
    assert!(plan(&dir, false, false).is_empty());
    let both = plan(&dir, true, true);
    assert!(both[0].unzip && both[0].dest.ends_with("whisper"));
    assert!(!both[1].unzip && both[1].dest.ends_with("ggml-large-v3.bin"));
}

#[test]
fn reads_free_space_of_the_drive_holding_a_folder() {
    let dir = folder("free");
    let free = editorkrab::asr_download::free_space(&dir.join("not-yet").join("models")).unwrap();
    assert!(free > 0);
}

/// Like `serve`, but the first `drops` responses send only `cut` bytes and close the connection,
/// the way the 3.1 GB model download died at 79% (curl 56).
fn serve_dropping(body: Vec<u8>, cut: usize, drops: usize) -> (String, Arc<Mutex<Vec<String>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/model.bin", listener.local_addr().unwrap());
    let ranges = Arc::new(Mutex::new(Vec::new()));
    let seen = ranges.clone();
    std::thread::spawn(move || {
        for (n, stream) in listener.incoming().enumerate() {
            let mut stream = stream.unwrap();
            let mut request = Vec::new();
            let mut byte = [0; 1];
            while !request.ends_with(b"\r\n\r\n") && stream.read(&mut byte).unwrap() == 1 {
                request.push(byte[0]);
            }
            let text = String::from_utf8_lossy(&request).to_lowercase();
            let start = text
                .lines()
                .find_map(|l| l.strip_prefix("range: bytes="))
                .map(|r| {
                    seen.lock().unwrap().push(r.to_owned());
                    r.trim_end_matches('-').parse::<usize>().unwrap()
                })
                .unwrap_or(0);
            let head = if start > 0 {
                format!(
                    "HTTP/1.1 206 Partial Content\r\nContent-Length: {}\r\nContent-Range: bytes {}-{}/{}\r\nConnection: close\r\n\r\n",
                    body.len() - start, start, body.len() - 1, body.len()
                )
            } else {
                format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len())
            };
            let _ = stream.write_all(head.as_bytes());
            let end = if n < drops { (start + cut).min(body.len()) } else { body.len() };
            let _ = stream.write_all(&body[start..end]);
        }
    });
    (url, ranges)
}

#[tokio::test]
async fn a_dropped_connection_resumes_by_itself() {
    let data = body();
    let (url, ranges) = serve_dropping(data.clone(), 250_000, 2);
    let dir = folder("dropped");
    let item = item(url, &dir, hash(&data));
    fetch(&item, &curl(), idle()).await.unwrap();
    assert_eq!(ranges.lock().unwrap().as_slice(), ["250000-", "500000-"]);
    assert_eq!(std::fs::read(&item.dest).unwrap(), data);
}

#[tokio::test]
async fn gives_up_when_retries_add_no_bytes() {
    let data = body();
    let (url, _) = serve_dropping(data.clone(), 0, usize::MAX);
    let dir = folder("stalled");
    let item = item(url, &dir, hash(&data));
    let error = fetch(&item, &curl(), idle()).await.unwrap_err();
    assert!(error.starts_with("network:"), "{error}");
    assert!(!item.dest.exists());
}
