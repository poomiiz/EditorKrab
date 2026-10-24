//! Read-only: the cut and caption rhythm the export would make for a project, in the numbers the
//! NinaStyleBook measures Nina 16 by. `cargo run --example nina_compare -- <dump.json>` (dump_project.py).
fn median(mut v: Vec<f64>) -> f64 {
    if v.is_empty() {
        return 0.0;
    }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    v[v.len() / 2]
}
fn main() {
    let path = std::env::args().nth(1).expect("dump.json");
    let state: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let document = &state["document"];
    let jobs: Vec<editorkrab::jobs::Job> = serde_json::from_value(state["jobs"].clone()).unwrap();
    for story in editorkrab::export::plan(document, &jobs).unwrap() {
        let total = story.duration_ms();
        let speech = editorkrab::export::speech_cues(&story, document);
        let emphasis = editorkrab::export::emphasis_cues(&story, document);
        let chars: Vec<f64> = speech.iter().map(|c| c.2.chars().filter(|c| !c.is_whitespace()).count() as f64).collect();
        let dur: Vec<f64> = speech.iter().map(|c| (c.1 - c.0) as f64 / 1000.0).collect();
        // shots: a new piece, or a camera change inside one
        let mut cuts = vec![0u64];
        let mut at = 0;
        let mut last: Option<(f64, f64, f64)> = None;
        let mut prev: Option<(usize, u64)> = None;
        let (mut jumps, mut cams) = (0, 0);
        for p in &story.pieces {
            let path = std::path::Path::new(&story.sources[p.source]);
            // a piece that goes on where the last one stopped is not a cut
            if at > 0 && prev.is_none_or(|(s, e)| s != p.source || p.start_ms.abs_diff(e) > 40) {
                cuts.push(at);
                jumps += 1;
            }
            prev = Some((p.source, p.end_ms));
            // the export draws one window per piece (plan() already splits pieces where the camera changes)
            let (x, y, z, _) = editorkrab::export::crop_at(document, path, p.start_ms);
            let now = ((x * 100.0).round(), (y * 100.0).round(), (z * 100.0).round());
            if last.is_some_and(|l| l != now) && at > 0 && cuts.last() != Some(&at) {
                cuts.push(at);
                cams += 1;
            }
            last = Some(now);
            at += p.end_ms - p.start_ms;
        }
        cuts.sort();
        cuts.dedup();
        cuts.push(total);
        let shots: Vec<f64> = cuts.windows(2).map(|w| (w[1] - w[0]) as f64 / 1000.0).filter(|d| *d > 0.0).collect();
        let stacked = emphasis.iter().filter(|e| speech.iter().any(|s| s.0 <= e.0 && e.0 <= s.1 + 1500 && s.1 >= e.1)).count();
        println!("story {:?}: {:.1} s, {} pieces", story.title, total as f64 / 1000.0, story.pieces.len());
        let short = shots.iter().filter(|d| **d < 1.0).count();
        if std::env::var("SHOW").is_ok() {
            for w in cuts.windows(2) { println!("    shot {:6.2}-{:6.2} = {:.2}", w[0] as f64/1000.0, w[1] as f64/1000.0, (w[1]-w[0]) as f64/1000.0); }
            let mut at = 0; for p in &story.pieces { println!("    piece {:6.2} src {:6.2}-{:6.2} {}", at as f64/1000.0, p.start_ms as f64/1000.0, p.end_ms as f64/1000.0, p.segment.rsplit(':').next().unwrap_or("")); at += p.end_ms-p.start_ms; }
        }
        println!("  shots: {} ({} jump cuts, {} camera changes, {} under 1 s) · median {:.2} s (Nina 16: about 2.2 s)", shots.len(), jumps, cams, short, median(shots));
        println!("  white cards: {} · median {} letters (Nina 16: 17) · median {:.2} s (Nina 16: 1.40 s)", speech.len(), median(chars), median(dur));
        println!("  orange cards: {} = {:.0}% of white (Nina 16: 33 of 48 Thai = 69%; Nina 16-19: 40-50% of sentences) · under a white line until it ends: {}",
            emphasis.len(), if speech.is_empty() { 0.0 } else { 100.0 * emphasis.len() as f64 / speech.len() as f64 }, stacked);
    }
}
