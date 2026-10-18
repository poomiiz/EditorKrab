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

## Run 4: release build, 8 whisper threads

Thread benchmark (large-v3, 30 s of English speech, whisper-cli directly): 2 threads 59 s, 8 threads 28 s, 14 threads 25 s. Run 4 used the app at 8 threads on a 20-thread CPU (the cap in `asr_threads`).

| Clip | Transcribe run 3 (debug, 2 threads) | Transcribe run 4 (release, 8 threads) | Rate |
|---|---|---|---|
| English 68.6 s | 332.6 s | 94.1 s | WER 6.74% (same) |
| Thai 45.1 s | 543.1 s | 128.4 s | CER 2.05% (same) |

- Steps 2-6 all completed for both clips (packet 2.4 s / 3.2 s, plan paste and steps 4-6 about 1.5 s each). Step 6 shows "not enabled yet, works up to step 5": video export is not built in this app yet.
- Run 3's step 2 timeout was the debug build re-hashing the 3.1 GB model on every start request (by design, to catch a swapped model); the release build does it in about 2 s.
- Step 2 errors are written to the step 1 error line, so a failure on step 2 is not visible on screen (not fixed yet).
- English repeated clause and the per-load 404 are unchanged.

Raw scores: score-en-run4.json, score-th-run4.json.
