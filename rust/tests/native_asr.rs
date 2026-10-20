//! Explicit native fixture acceptance; configuration is supplied by the invoker, never defaults to tiny.
use editorkrab::{
    native_asr::{self, Config},
    sources::{self, Identity},
    transcription::Context,
};
use std::{path::PathBuf, time::Duration};
use tokio::sync::watch;

#[tokio::test]
#[ignore = "requires configured native ffmpeg/whisper/model/source"]
async fn actual_native_chunk_cancel_timeout_and_source_guard() {
    let env = |key| PathBuf::from(std::env::var(key).expect(key));
    let source = env("EDITORKRAB_ASR_TEST_SOURCE");
    let workspace =
        std::env::temp_dir().join(format!("editorkrab-asr-test-{}", uuid::Uuid::new_v4()));
    let metadata = source.metadata().unwrap();
    let context = Context {
        run_id: "native-fixture".into(),
        source: Identity {
            id: "jfk-source".into(),
            size: metadata.len(),
            modified_ns: sources::stamp(&metadata).unwrap(),
        },
        start_ms: 0,
        end_ms: 11000,
        language: "auto".into(),
    };
    let mut config = Config {
        ffmpeg: env("EDITORKRAB_ASR_TEST_FFMPEG"),
        whisper: env("EDITORKRAB_ASR_TEST_ENGINE"),
        model: env("EDITORKRAB_ASR_TEST_MODEL"),
        workspace: workspace.clone(),
        timeout: Duration::from_secs(120),
        threads: 2,
        gpu: None,
    };
    let (cancel, receiver) = watch::channel(false);
    let running = native_asr::run(&config, &source, &context, receiver.clone());
    let false_signal = async {
        tokio::time::sleep(Duration::from_millis(25)).await;
        cancel.send(false).unwrap();
    };
    let (result, ()) = tokio::join!(running, false_signal);
    let result = result.unwrap();
    assert_eq!(result.transcript.detected_language, "en");
    assert!(
        result
            .transcript
            .segments
            .iter()
            .any(|s| s.text.contains("country"))
    );
    assert!(
        result
            .transcript
            .segments
            .iter()
            .flat_map(|s| &s.tokens)
            .any(|t| t.timing == "zero_duration")
    );
    assert_eq!(std::fs::read_dir(&workspace).unwrap().count(), 0);
    let evidence = env("EDITORKRAB_ASR_TEST_EVIDENCE");
    std::fs::write(evidence, serde_json::to_vec_pretty(&result).unwrap()).unwrap();
    let mut changed = context.clone();
    changed.source.size += 1;
    assert_eq!(
        native_asr::run(&config, &source, &changed, receiver.clone())
            .await
            .unwrap_err(),
        "source_changed"
    );
    let mut rejected = config.clone();
    rejected.whisper = config.ffmpeg.clone();
    assert_eq!(
        native_asr::run(&rejected, &source, &context, receiver.clone())
            .await
            .unwrap_err(),
        "transcription_process_failed"
    );
    assert_eq!(std::fs::read_dir(&workspace).unwrap().count(), 0);
    config.timeout = Duration::from_millis(1);
    assert_eq!(
        native_asr::run(&config, &source, &context, receiver.clone())
            .await
            .unwrap_err(),
        "transcription_process_timeout"
    );
    assert_eq!(std::fs::read_dir(&workspace).unwrap().count(), 0);
    config.timeout = Duration::from_secs(120);
    let running = native_asr::run(&config, &source, &context, receiver.clone());
    let signal = async {
        tokio::time::sleep(Duration::from_millis(25)).await;
        cancel.send(true).unwrap();
    };
    let (result, ()) = tokio::join!(running, signal);
    assert_eq!(result.unwrap_err(), "cancelled");
    assert_eq!(std::fs::read_dir(&workspace).unwrap().count(), 0);
    assert_eq!(
        native_asr::run(&config, &source, &context, receiver)
            .await
            .unwrap_err(),
        "cancelled"
    );
    std::fs::remove_dir(workspace).unwrap();
}
