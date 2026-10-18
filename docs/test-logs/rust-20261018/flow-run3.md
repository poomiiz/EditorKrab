# Rust app real flow, run 3 (2026-10-18)

Build: claude/rust-app-3 (debug exe), whisper.cpp v1.9.2 CPU (`-ng -t 2`), ggml-large-v3.
Clips: generated test speech read from script-en.txt (68.6 s) and script-th.txt (45.1 s); no customer footage.

| Clip | Transcribe (UI, s) | Engine (s) | Metric | Errors / units | Rate |
|---|---|---|---|---|---|
| English | 332.6 | 281.6 | WER | 13 / 193 | 6.74% |
| Thai | 543.1 | 486.3 | CER | 9 / 438 | 2.05% |

- 31-45 s window (English): every word present; not reproduced. Clips under 300 s are one whisper call (chunks are 300 s, no overlap).
- English output repeats one clause ("then the editor splits each line so it fits on a phone screen") — whisper repetition, cause not yet traced.
- Step 2 packet download in the scripted run timed out waiting for a download event (button lookup in the test script; not yet traced). Steps 3-6 not run in this pass.
- One 404 console error per page load (resource not yet identified).
- Speed: `-t 2` on a 20-thread CPU; transcription runs about 5x slower than real time.

Raw scores: score-en.json, score-th.json.
