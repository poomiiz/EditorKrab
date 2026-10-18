//! Native probing only. No Python subprocess or shell interpolation.
use crate::{
    drafts::Store,
    jobs::{Job, Task},
};
use serde_json::Value;
use std::{
    path::Path,
    process::Stdio,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{io::AsyncReadExt, process::Command, sync::watch};

pub fn validate_probe(bytes: &[u8]) -> Result<Value, String> {
    let value: Value = serde_json::from_slice(bytes).map_err(|_| "invalid_probe_output")?;
    let duration = value["format"]["duration"]
        .as_str()
        .and_then(|s| s.parse::<f64>().ok())
        .filter(|v| v.is_finite() && *v > 0.0)
        .ok_or("media_duration_missing")?;
    let streams = value["streams"].as_array().ok_or("media_streams_missing")?;
    if !streams
        .iter()
        .any(|s| matches!(s["codec_type"].as_str(), Some("video" | "audio")))
    {
        return Err("media_streams_missing".into());
    }
    Ok(serde_json::json!({"duration_s":duration,"streams":streams}))
}

pub async fn probe(
    job: &Job,
    executable: &Path,
    store: Arc<Mutex<Store>>,
    mut stopping: watch::Receiver<bool>,
) -> Result<Value, String> {
    let Task::Probe { path, source } = &job.task else {
        return Err("invalid_probe_task".into());
    };
    let path = Path::new(path)
        .canonicalize()
        .map_err(|_| "source_file_missing")?;
    if !path.is_file() {
        return Err("source_not_a_file".into());
    }
    let verify = || -> Result<(), String> {
        if let Some(expected) = source {
            let metadata = path.metadata().map_err(|_| "source_file_missing")?;
            if metadata.len() != expected.size
                || crate::sources::stamp(&metadata).map_err(|_| "source_metadata_unavailable")?
                    != expected.modified_ns
            {
                return Err("source_changed".into());
            }
        }
        Ok(())
    };
    verify()?;
    let mut command = Command::new(executable);
    command.args(["-v","error","-show_entries",
        "format=duration:stream=index,codec_type,width,height,avg_frame_rate,sample_rate,channels,color_space,color_transfer,color_primaries",
        "-of","json","-i"]).arg(&path).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW, matching the existing application.
    let mut child = command.spawn().map_err(|_| "ffprobe_start_failed")?;
    let output = child.stdout.take().ok_or("ffprobe_stdout_missing")?;
    let errors = child.stderr.take().ok_or("ffprobe_stderr_missing")?;
    let out = tokio::spawn(async move {
        let mut bytes = Vec::new();
        output
            .take(1_048_577)
            .read_to_end(&mut bytes)
            .await
            .map(|_| bytes)
    });
    let err = tokio::spawn(async move {
        let mut bytes = Vec::new();
        errors
            .take(65_537)
            .read_to_end(&mut bytes)
            .await
            .map(|_| bytes)
    });
    let deadline = tokio::time::sleep(Duration::from_secs(60));
    tokio::pin!(deadline);
    let mut tick = tokio::time::interval(Duration::from_secs(1));
    let completion: Result<std::process::ExitStatus, String> = loop {
        tokio::select! {
            status = child.wait() => break status.map_err(|_|"ffprobe_wait_failed".into()),
            _ = &mut deadline => break Err("ffprobe_timeout".into()),
            _ = stopping.changed() => break Err("application_stopping".into()),
            _ = tick.tick() => {
                let id=job.id.clone();let store=store.clone();
                let cancellation = tokio::task::spawn_blocking(move || {
                    let database=store.lock().map_err(|_|"job_store_unavailable")?;
                    let cancelled=database.job(&id).map_err(|_|"job_read_failed")?.cancel_requested;
                    database.heartbeat_job(&id).map_err(|_|"job_heartbeat_failed")?;
                    Ok::<_,&str>(cancelled)
                }).await.map_err(|_|"job_check_failed")?;
                match cancellation {
                    Ok(true) => break Err("cancelled".into()),
                    Ok(false) => {},
                    Err(error) => break Err(error.into()),
                }
            }
        }
    };
    if completion.is_err() {
        let _ = child.kill().await;
        let _ = child.wait().await;
    }
    let stdout = out
        .await
        .map_err(|_| "ffprobe_output_task_failed")?
        .map_err(|_| "ffprobe_output_read_failed")?;
    let _stderr = err
        .await
        .map_err(|_| "ffprobe_error_task_failed")?
        .map_err(|_| "ffprobe_error_read_failed")?;
    let status = completion?;
    if !status.success() {
        return Err("ffprobe_rejected_media".into());
    }
    if stdout.len() > 1_048_576 {
        return Err("ffprobe_output_too_large".into());
    }
    verify()?;
    validate_probe(&stdout)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_no_duration_no_streams_and_nonfinite_duration() {
        assert!(
            validate_probe(br#"{"format":{"duration":"NaN"},"streams":[{"codec_type":"video"}]}"#)
                .is_err()
        );
        assert!(validate_probe(br#"{"format":{"duration":"2"},"streams":[]}"#).is_err());
        assert!(validate_probe(b"broken").is_err());
        assert_eq!(
            validate_probe(br#"{"format":{"duration":"2.5"},"streams":[{"codec_type":"audio"}]}"#)
                .unwrap()["duration_s"],
            2.5
        );
    }
}
