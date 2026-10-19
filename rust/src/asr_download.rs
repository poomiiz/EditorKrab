//! Downloads the speech model (and, on Windows, the whisper.cpp runtime) when this machine has none.
//! curl does the transfer (`-C -` resumes a `.part`), every file is checked against a pinned SHA256,
//! and only a verified file is renamed into place.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use tokio::sync::watch;

pub const MODEL_URL: &str =
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin";
pub const MODEL_SIZE: u64 = 3_095_033_483;
pub const MODEL_SHA256: &str = "64d182b440b98d5203c4f9bd541544d84c605196c4f7b845dfa11fb23594d1e2";
pub const RUNTIME_URL: &str =
    "https://github.com/ggml-org/whisper.cpp/releases/download/v1.9.2/whisper-bin-x64.zip";
pub const RUNTIME_SIZE: u64 = 8_194_445;
pub const RUNTIME_SHA256: &str = "49dcc16de826f20bd53d44f947a1ae49dfa81f86cad67a64d80820cb192d674a";

/// One file to fetch. A zip is unpacked into `dest` (a folder); anything else is renamed to `dest`.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct Item {
    pub url: String,
    pub size: u64,
    pub sha256: String,
    pub dest: PathBuf,
    #[serde(default)]
    pub unzip: bool,
}
impl Item {
    pub fn part(&self) -> PathBuf {
        let mut name = self.dest.as_os_str().to_os_string();
        name.push(if self.unzip { ".zip.part" } else { ".part" });
        PathBuf::from(name)
    }
}

/// What the app still lacks, in download order; empty when nothing is missing.
pub fn plan(data: &Path, need_runtime: bool, need_model: bool) -> Vec<Item> {
    let mut items = Vec::new();
    if need_runtime {
        items.push(Item {
            url: RUNTIME_URL.into(),
            size: RUNTIME_SIZE,
            sha256: RUNTIME_SHA256.into(),
            dest: data.join("whisper"),
            unzip: true,
        });
    }
    if need_model {
        items.push(Item {
            url: MODEL_URL.into(),
            size: MODEL_SIZE,
            sha256: MODEL_SHA256.into(),
            dest: data.join("models").join("ggml-large-v3.bin"),
            unzip: false,
        });
    }
    items
}

/// Bytes still to download, counting what earlier attempts already left in `.part` files.
pub fn remaining(items: &[Item]) -> u64 {
    items
        .iter()
        .map(|i| {
            let have = std::fs::metadata(i.part()).map(|m| m.len()).unwrap_or(0);
            i.size.saturating_sub(have.min(i.size))
        })
        .sum()
}

/// A plain refusal the UI can show as "drive X needs N more GB".
pub fn check_space(free: u64, items: &[Item], drive: &str) -> Result<(), String> {
    // a zip needs room for itself and its unpacked files
    let needed = remaining(items)
        + items
            .iter()
            .filter(|i| i.unzip)
            .map(|i| i.size * 3)
            .sum::<u64>();
    if free < needed {
        return Err(format!("disk_space:{drive}:{}", needed - free));
    }
    Ok(())
}

pub fn sha256(path: &Path) -> Result<String, String> {
    let mut file = std::fs::File::open(path).map_err(|e| e.to_string())?;
    let mut hash = Sha256::new();
    let mut buffer = vec![0; 1 << 20];
    loop {
        let n = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

/// Downloads (or resumes) one item, then verifies and installs it. A wrong checksum deletes the
/// `.part` so the next attempt starts clean instead of resuming bad bytes.
pub async fn fetch(
    item: &Item,
    curl: &Path,
    mut cancel: watch::Receiver<bool>,
) -> Result<(), String> {
    let part = item.part();
    if let Some(folder) = part.parent() {
        tokio::fs::create_dir_all(folder)
            .await
            .map_err(|e| e.to_string())?;
    }
    // A dropped connection (curl 56 at 79% of 3.1 GB, seen 2026-10-19) resumes from the .part
    // instead of failing; only a run of failures with no new bytes gives up.
    let mut stalled = 0;
    loop {
        let have = tokio::fs::metadata(&part)
            .await
            .map(|m| m.len())
            .unwrap_or(0);
        if have >= item.size {
            break;
        }
        match curl_once(item, curl, &part, &mut cancel).await {
            Ok(()) => break,
            Err(e) if e == "cancelled" || e.starts_with("curl_missing") => return Err(e),
            Err(e) => {
                let now = tokio::fs::metadata(&part)
                    .await
                    .map(|m| m.len())
                    .unwrap_or(0);
                stalled = if now > have { 0 } else { stalled + 1 };
                if stalled >= RETRIES {
                    return Err(e);
                }
                tokio::select! {
                    _ = tokio::time::sleep(std::time::Duration::from_secs(2)) => {}
                    _ = async { drop(cancel.wait_for(|stop| *stop).await) } => return Err("cancelled".into()),
                }
            }
        }
    }
    let check = part.clone();
    let actual = tokio::task::spawn_blocking(move || sha256(&check))
        .await
        .map_err(|e| e.to_string())??;
    if actual != item.sha256 {
        let _ = tokio::fs::remove_file(&part).await;
        return Err("checksum_mismatch".into());
    }
    install(item, &part).await
}

/// Attempts in a row that add no bytes before a download is reported as failed.
pub const RETRIES: u32 = 3;

async fn curl_once(
    item: &Item,
    curl: &Path,
    part: &Path,
    cancel: &mut watch::Receiver<bool>,
) -> Result<(), String> {
    {
        let mut command = tokio::process::Command::new(curl);
        command
            .args(["--fail", "--location", "--silent", "--show-error"])
            .args(["--retry", "2", "--connect-timeout", "20"])
            .args(["--continue-at", "-", "--output"])
            .arg(&part)
            .arg(&item.url)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(windows)]
        command.creation_flags(0x0800_0000);
        let mut child = command.spawn().map_err(|e| format!("curl_missing:{e}"))?;
        let mut stderr = child.stderr.take();
        let status = tokio::select! {
            status = child.wait() => status.map_err(|e| e.to_string())?,
            _ = async { drop(cancel.wait_for(|stop| *stop).await) } => {
                let _ = child.kill().await;
                return Err("cancelled".into());
            }
        };
        if !status.success() {
            let mut message = String::new();
            if let Some(err) = stderr.as_mut() {
                use tokio::io::AsyncReadExt;
                let _ = err.read_to_string(&mut message).await;
            }
            return Err(format!("network:{}", message.trim()));
        }
    }
    Ok(())
}

/// Puts a verified download in place: a zip is unpacked into `dest`, anything else renamed to it.
async fn install(item: &Item, part: &Path) -> Result<(), String> {
    if item.unzip {
        tokio::fs::create_dir_all(&item.dest)
            .await
            .map_err(|e| e.to_string())?;
        // Windows 10+ System32 tar is bsdtar, which reads zip (a GNU tar earlier on PATH cannot);
        // the release keeps its files under Release/
        let tar = if cfg!(windows) {
            PathBuf::from(std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into()))
                .join("System32")
                .join("tar.exe")
        } else {
            PathBuf::from("tar")
        };
        let mut unpack = tokio::process::Command::new(tar);
        #[cfg(windows)]
        unpack.creation_flags(0x0800_0000);
        let out = unpack
            .arg("-xf")
            .arg(&part)
            .arg("--strip-components=1")
            .arg("-C")
            .arg(&item.dest)
            .output()
            .await
            .map_err(|e| e.to_string())?;
        if !out.status.success() {
            return Err("unzip_failed".into());
        }
        tokio::fs::remove_file(&part)
            .await
            .map_err(|e| e.to_string())?;
    } else {
        tokio::fs::rename(&part, &item.dest)
            .await
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// Free bytes on the drive holding `path` (asks the OS; std has no portable call).
pub fn free_space(path: &Path) -> Result<u64, String> {
    let mut probe = path.to_path_buf();
    while !probe.exists() {
        probe = probe.parent().ok_or("no_existing_parent")?.to_path_buf();
    }
    let out = if cfg!(windows) {
        let mut command = std::process::Command::new("powershell");
        let quoted = probe.to_string_lossy().replace('\'', "''");
        #[cfg(windows)]
        std::os::windows::process::CommandExt::creation_flags(&mut command, 0x0800_0000);
        command.args(["-NoProfile", "-NonInteractive", "-Command"]);
        command.arg(format!(
            "([IO.DriveInfo]::new('{quoted}')).AvailableFreeSpace"
        ));
        command.output()
    } else {
        std::process::Command::new("df")
            .args(["-Pk"])
            .arg(&probe)
            .output()
    }
    .map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&out.stdout);
    let value = if cfg!(windows) {
        text.trim().parse::<u64>().ok()
    } else {
        text.lines()
            .nth(1)
            .and_then(|l| l.split_whitespace().nth(3))
            .and_then(|k| k.parse::<u64>().ok())
            .map(|k| k * 1024)
    };
    value.ok_or_else(|| "free_space_unknown".into())
}

/// The drive letter (Windows) or mount-ish root shown in the "not enough space" message.
pub fn drive(path: &Path) -> String {
    path.components()
        .next()
        .map(|c| {
            c.as_os_str()
                .to_string_lossy()
                .trim_end_matches(['\\', ':'])
                .to_owned()
        })
        .unwrap_or_default()
}
