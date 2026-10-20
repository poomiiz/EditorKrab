//! Keeps the CTranslate2 GPU sidecar (`ek-asr-ct2`, see rust/asr-ct2) loaded between chunks.
//! The model loads once (in the background as soon as a video is imported), is reused by every chunk,
//! and is released after 15 idle minutes. Any failure kills the sidecar; the caller falls back to whisper.cpp.
use crate::native_asr::{Artifact, Ct2Identity};
use std::{
    path::{Path, PathBuf},
    process::Stdio,
    sync::{Arc, Weak},
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader, Lines},
    process::{Child, ChildStdin, ChildStdout, Command},
    sync::{Mutex, watch},
};

pub const IDLE: Duration = Duration::from_secs(15 * 60);
const LOAD_TIMEOUT: Duration = Duration::from_secs(300);

struct Running {
    child: Child,
    stdin: ChildStdin,
    stdout: Lines<BufReader<ChildStdout>>,
    used: Instant,
}
pub struct Ct2 {
    pub exe: PathBuf,
    pub model: PathBuf,
    running: Mutex<Option<Running>>,
    identity: std::sync::Mutex<Option<Ct2Identity>>,
}
impl std::fmt::Debug for Ct2 {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Ct2")
            .field("exe", &self.exe)
            .field("model", &self.model)
            .finish()
    }
}

async fn line(stdout: &mut Lines<BufReader<ChildStdout>>) -> Result<serde_json::Value, String> {
    let text = stdout
        .next_line()
        .await
        .map_err(|_| "ct2_read_failed")?
        .ok_or("ct2_exited")?;
    serde_json::from_str(&text).map_err(|_| "ct2_bad_reply".into())
}

impl Ct2 {
    /// Starts the idle reaper; it stops when the last handle is dropped.
    pub fn new(exe: PathBuf, model: PathBuf) -> Arc<Self> {
        let ct2 = Arc::new(Self {
            exe,
            model,
            running: Mutex::new(None),
            identity: std::sync::Mutex::new(None),
        });
        let weak: Weak<Self> = Arc::downgrade(&ct2);
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(60)).await;
                let Some(ct2) = weak.upgrade() else { break };
                ct2.release_if_idle(IDLE).await;
            }
        });
        ct2
    }

    /// Sidecar and model identity. model.bin is ~3 GB, so it is hashed once and again only when
    /// its size or modification stamp changes.
    pub fn identity(&self) -> Result<Ct2Identity, String> {
        let mut cached = self.identity.lock().unwrap_or_else(|e| e.into_inner());
        let unchanged = |a: &Artifact| {
            a.path
                .metadata()
                .ok()
                .and_then(|m| Some((m.len(), crate::sources::stamp(&m).ok()?)))
                == Some((a.size, a.modified_ns.clone()))
        };
        if let Some(id) = cached
            .as_ref()
            .filter(|id| unchanged(&id.engine) && unchanged(&id.model))
        {
            return Ok(id.clone());
        }
        let id = Ct2Identity {
            engine: Artifact::read(&self.exe)?,
            model: Artifact::read(&self.model.join("model.bin"))?,
        };
        *cached = Some(id.clone());
        Ok(id)
    }

    async fn start(&self) -> Result<Running, String> {
        let mut command = Command::new(&self.exe);
        command
            .arg(&self.model)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true);
        // the CUDA and cuDNN DLLs ship beside the sidecar
        if let Some(dir) = self.exe.parent() {
            command.current_dir(dir);
        }
        #[cfg(windows)]
        command.creation_flags(0x08000000);
        let mut child = command.spawn().map_err(|_| "ct2_start_failed")?;
        let stdin = child.stdin.take().ok_or("ct2_start_failed")?;
        let mut stdout = BufReader::new(child.stdout.take().ok_or("ct2_start_failed")?).lines();
        let ready = tokio::time::timeout(LOAD_TIMEOUT, line(&mut stdout))
            .await
            .map_err(|_| "ct2_load_timeout")??;
        if ready["ready"] != true {
            return Err(format!(
                "ct2_load_failed:{}",
                ready["error"].as_str().unwrap_or("")
            ));
        }
        Ok(Running {
            child,
            stdin,
            stdout,
            used: Instant::now(),
        })
    }

    /// Loads the model unless it is already loaded; used right after an import.
    pub async fn preload(&self) -> Result<(), String> {
        let mut running = self.running.lock().await;
        if running.is_none() {
            *running = Some(self.start().await?);
        }
        Ok(())
    }

    pub async fn release_if_idle(&self, idle: Duration) {
        // a sidecar busy with a request is in use, so a held lock means "not idle"
        let Ok(mut running) = self.running.try_lock() else {
            return;
        };
        if let Some(mut r) = running.take_if(|r| r.used.elapsed() >= idle) {
            let _ = r.child.kill().await;
        }
    }

    /// Transcribes one extracted 16 kHz WAV into whisper.cpp-shaped JSON at `out`.
    /// `asked` is the job's language (may be "auto"); `decode` is the language to decode with.
    pub async fn transcribe(
        &self,
        wav: &Path,
        asked: &str,
        decode: &str,
        out: &Path,
        cancel: &watch::Receiver<bool>,
        timeout: Duration,
    ) -> Result<(), String> {
        let mut running = self.running.lock().await;
        if running.is_none() {
            *running = Some(self.start().await?);
        }
        let r = running.as_mut().ok_or("ct2_start_failed")?;
        let request =
            serde_json::json!({"wav": wav, "language": asked, "decode": decode, "out": out});
        let mut cancel = cancel.clone();
        let result = tokio::select! {
            result = async {
                r.stdin.write_all(format!("{request}\n").as_bytes()).await.map_err(|_| "ct2_write_failed")?;
                r.stdin.flush().await.map_err(|_| "ct2_write_failed")?;
                line(&mut r.stdout).await
            } => result,
            _ = cancel.wait_for(|requested| *requested) => Err("cancelled".into()),
            _ = tokio::time::sleep(timeout) => Err("ct2_timeout".into()),
        };
        match result {
            Ok(reply) if reply["ok"] == true => {
                r.used = Instant::now();
                Ok(())
            }
            other => {
                // a half-answered sidecar cannot be trusted with the next request
                if let Some(mut r) = running.take() {
                    let _ = r.child.kill().await;
                }
                match other {
                    Ok(reply) => Err(format!(
                        "ct2_failed:{}",
                        reply["error"].as_str().unwrap_or("")
                    )),
                    Err(e) => Err(e),
                }
            }
        }
    }
}
