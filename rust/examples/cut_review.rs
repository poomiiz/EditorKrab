//! Read-only: every piece, caption card and emphasis the export would make for a project, as JSON for
//! scripts/cut_review.py to check against the audio. `cargo run --example cut_review -- <dump.json> <out.json>`
fn main() {
    let mut args = std::env::args().skip(1);
    let (input, output) = (args.next().expect("dump.json"), args.next().expect("out.json"));
    let state: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(input).unwrap()).unwrap();
    let document = &state["document"];
    let jobs: Vec<editorkrab::jobs::Job> = serde_json::from_value(state["jobs"].clone()).unwrap();
    // every transcribed line with its words, so a cut can be checked against whole lines
    let lines: Vec<serde_json::Value> = editorkrab::export::transcript(document, &jobs)
        .into_iter()
        .flat_map(|(path, lines)| lines.into_iter().map(move |(id, a, b, text)| serde_json::json!({"path": path, "id": id, "start_ms": a, "end_ms": b, "text": text})))
        .collect();
    let stories: Vec<serde_json::Value> = editorkrab::export::plan(document, &jobs)
        .unwrap()
        .iter()
        .map(|story| {
            serde_json::json!({
                "title": story.title,
                "sources": story.sources,
                "pieces": story.pieces.iter().map(|p| serde_json::json!({
                    "source": p.source, "start_ms": p.start_ms, "end_ms": p.end_ms, "segment": p.segment, "text": p.text,
                    "words": p.words, "crop": editorkrab::export::crop_at(document, std::path::Path::new(&story.sources[p.source]), p.start_ms)})).collect::<Vec<_>>(),
                "cards": editorkrab::export::speech_cues(story, document),
                "emphasis": editorkrab::export::emphasis_lines(story, document),
            })
        })
        .collect();
    std::fs::write(output, serde_json::to_vec_pretty(&serde_json::json!({"stories": stories, "lines": lines})).unwrap()).unwrap();
}
