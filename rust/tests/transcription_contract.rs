use editorkrab::sources::Identity;
use editorkrab::transcription::{Context, parse};
use serde_json::{Value, json};
fn context() -> Context {
    Context {
        run_id: "run-1".into(),
        source: Identity {
            id: "source-1".into(),
            size: 100,
            modified_ns: "123".into(),
        },
        start_ms: 300000,
        end_ms: 310000,
        language: "auto".into(),
    }
}
fn payload() -> Value {
    json!({"model":{"type":"tiny","multilingual":true,"vocab":51865},"params":{"language":"auto","translate":false},"result":{"language":"th"},"transcription":[{"offsets":{"from":0,"to":2000},"text":" พูด English เลย", "tokens":[{"id":50364,"text":"[_BEG_]","p":0.9,"offsets":{"from":0,"to":0}},{"id":10,"text":" พูด","p":0.8,"offsets":{"from":200,"to":200}},{"id":11,"text":" English","p":0.7,"offsets":{"from":300,"to":900}},{"id":12,"text":" เลย","p":0.6,"offsets":{"from":-1,"to":-1}}]}]})
}
#[test]
fn preserves_source_clock_text_and_unknown_alignment() {
    let result = parse(&serde_json::to_vec(&payload()).unwrap(), &context()).unwrap();
    assert_eq!(result.segments[0].start_ms, 300000);
    assert_eq!(result.segments[0].text, " พูด English เลย");
    assert_eq!(result.segments[0].tokens.len(), 3);
    assert_eq!(result.segments[0].tokens[0].timing, "zero_duration");
    assert_eq!(result.segments[0].tokens[0].start_ms, Some(300200));
    assert_eq!(result.segments[0].tokens[1].end_ms, Some(300900));
    assert_eq!(result.segments[0].tokens[2].start_ms, None);
    assert_eq!(result.segments[0].tokens[2].timing, "unavailable");
    assert_eq!(result.alignment, "unverified");
    let words = &result.segments[0].words;
    assert_eq!(
        words.iter().map(|w| w.text.as_str()).collect::<Vec<_>>(),
        ["พูด", "English", "เลย"]
    );
    assert!(words.windows(2).all(|w| w[0].end_ms <= w[1].start_ms));
    assert!(
        words
            .iter()
            .all(|w| w.start_ms >= 300000 && w.end_ms <= 302000 && w.start_ms <= w.end_ms)
    );
    assert_eq!(result.segments[0].speaker_id, None);
    assert_ne!(result.segments[0].id, result.segments[0].tokens[0].id);
    assert_eq!(
        result,
        parse(&serde_json::to_vec(&payload()).unwrap(), &context()).unwrap()
    );
    let mut other = context();
    other.run_id = "run-2".into();
    assert_ne!(
        result.segments[0].id,
        parse(&serde_json::to_vec(&payload()).unwrap(), &other)
            .unwrap()
            .segments[0]
            .id
    );
}
#[test]
fn rejects_wrong_language_translation_and_untrusted_clocks() {
    for (pointer, value) in [
        ("/params/translate", json!(true)),
        ("/params/language", json!("en")),
        ("/result/language", json!("")),
        ("/transcription/0/offsets/to", json!(10001)),
        ("/transcription/0/offsets/from", json!(-1)),
        ("/transcription/0/offsets/to", json!(0)),
        ("/transcription/0/tokens/2/p", json!(1.1)),
        ("/transcription/0/tokens/2/offsets/to", json!(200)),
        ("/transcription/0/tokens/2/id", json!(-1)),
        ("/model/multilingual", json!("true")),
    ] {
        let mut value_in = payload();
        *value_in.pointer_mut(pointer).unwrap() = value;
        assert!(
            parse(&serde_json::to_vec(&value_in).unwrap(), &context()).is_err(),
            "accepted {pointer}"
        );
    }
    let mut input = payload();
    let duplicate = input["transcription"][0].clone();
    input["transcription"]
        .as_array_mut()
        .unwrap()
        .push(duplicate);
    assert!(parse(&serde_json::to_vec(&input).unwrap(), &context()).is_err());
    assert!(parse(b"broken", &context()).is_err());
    let mut ctx = context();
    ctx.source.id = "bad/id".into();
    assert!(parse(&serde_json::to_vec(&payload()).unwrap(), &ctx).is_err());
}
#[test]
fn unknown_and_outside_token_clocks_do_not_become_caption_times() {
    let mut value = payload();
    value["transcription"][0]["tokens"][2]["offsets"] = json!({"from":100,"to":3000});
    let result = parse(&serde_json::to_vec(&value).unwrap(), &context()).unwrap();
    assert_eq!(result.segments[0].tokens[1].timing, "outside_segment");
    assert_eq!(result.segments[0].tokens[1].start_ms, None);
    assert_eq!(
        result.segments[0].tokens[1]
            .raw_offsets
            .as_ref()
            .unwrap()
            .to,
        3000
    );
    value["transcription"][0]["tokens"][2]
        .as_object_mut()
        .unwrap()
        .remove("offsets");
    assert_eq!(
        parse(&serde_json::to_vec(&value).unwrap(), &context())
            .unwrap()
            .segments[0]
            .tokens[1]
            .timing,
        "unavailable"
    );
    value["transcription"] = json!([]);
    assert!(
        parse(&serde_json::to_vec(&value).unwrap(), &context())
            .unwrap()
            .segments
            .is_empty()
    );
}
