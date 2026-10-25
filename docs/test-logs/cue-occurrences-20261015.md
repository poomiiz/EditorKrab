> **History.** Written for the Python-era build, kept as a record. The Python app was removed on 25 Oct 2026; EditorKrab is now the Rust app only, so commands here that use Python no longer exist. / เอกสารเก่าของรุ่น Python เก็บไว้เป็นบันทึก แอป Python ถูกลบเมื่อ 25 ต.ค. 2026

# EK-02 subtitle occurrence identity

Task: `69d83965-a7cf-46e4-8b93-e540b25f7414`

## Problem and shared change

Two occurrences of the same subtitle previously shared a text-keyed word clock, emphasis and caption. Cutting the first could overwrite the second's clock. HF/native caption grouping also spanned all repeated wording as one interval.

Sidecars now use `cue:<subtitle-segment-id>` keys. Actual word clocks remain relative to their own occurrence. Existing unique text keys are read without mutation and backed up to `.legacy.bak` on first migrated write. Ambiguous repeated legacy text raises an actionable error instead of inventing separate timings. Old app versions do not understand the new keys; preserve project history/backups when rolling back application code.

The shared change reaches transcription and project creation, subtitle replacement/merge/split/language restyling, silence/editorial cuts and undo, corrected text/time/relisten, browser packet/import, manual APIs/editor selection, FFmpeg dry timing, HyperFrames captions and native CapCut captions. Merge/split source IDs are recorded in local `clipkit_cues.json`. Direct CLI text fixes and relistening accept `--cue-id` for repeated wording.

Transcription prepares IDs and word clocks in the returned rows; it does not overwrite the current project's word clocks until subtitles are applied. Appending a timed subtitle preserves existing clocks. Editing a shared native text material clones it for the selected occurrence.

This is a compatible extension of the numbered Browser AI answer contract: exported rows include `cue_id`; supplied IDs must match the current numbered row, including continuations. Full packet fingerprinting, idempotency and recoverable multi-file transactions remain EK-03 work.

## Regression evidence

- Repeated text at 2–5 and 6–9 seconds, keyword at relative time 2: cutting 3–3.5 produces keyword offsets 1.5 and 2 respectively; the second occurrence retains its own data. Cut undo restores the original clock map.
- Different emphasis/captions for equal wording survive browser import and export. The packet exposes distinct IDs and word onsets.
- Native and HyperFrames captions remain separate intervals (2–6 and 6–8), rather than a shared 2–8 span.
- Manual text/punch/caption editing changes only the selected cue. A text-only request with repeated wording is refused. Shared native materials do not leak text edits across occurrences.
- After cutting the first occurrence, motion on the second keeps cue ID `1` and moves from 6–8 to 2–4.
- Legacy migration, ambiguous legacy refusal, invalid word times, duplicate IDs, appended clocks, transcription preview preservation and merge/split provenance have focused tests.

Focused suites: `cue_data_test.py` (4), `cue_occurrence_test.py` (10), `trim_timing_test.py` (10), `subtitle_merge_test.py` (6), `subtitle_timing_test.py` (14). The full local sweep passed all 42 Python test files, including the pre-existing uncommitted installer/library tests; this is a working-tree result, not a claim that all 42 are published in CI. Individual outputs are saved in ignored `state/ek02-test-logs/`; the added final motion regression was run separately after that sweep.

## Playwright with the real editor and API routes

Used an isolated loopback server with the repository's actual editor and video API router, generated blue footage and synthetic timing fixtures. This is not Google authentication, external browser-AI-provider, audio-sync or editorial acceptance.

At 1440×1000, selected the second equal-text timeline item and changed its emphasis and caption through the visible form. Readback confirmed `cue:0` retained `first caption`, while `cue:1` became `second changed`. Preview composition was built by the real HF path. Screenshots at desktop and 375×844 were captured and inspected; private fixture proof/readback is in `state/ek02-ui-evidence/`.

**375px acceptance failed:** existing editor layout clips controls and leaves the edit panel outside the viewport. This data-identity change does not resolve the responsive UI work. The isolated harness also lacks the shell's version/update endpoints (404 console messages), and the player reports its existing iframe-sandbox warning. These are recorded, not treated as a clean whole-app pass.

The added-line raw-colour diff check found no new raw colours. No customer draft, transcript or media was modified in this pass. Native CapCut playback and representative real-footage acceptance remain pending under the overall plan.

CI follow-up: the new cue fixtures initially depended on private machine configuration. Commit `d97c554`
isolates that configuration; [CI run 38698122737](https://github.com/poomiiz/EditorKrab/actions/runs/38698122737)
passed on that exact commit. Subsequent packet identity and recovery work is recorded in
[the EK-03 checkpoint](ai-import-20261015.md).
