# Work-footage inspection — 2026-10-14

Task: `146aba82-b7d1-495c-9472-868c06f5ca83`  
Product revision: `ac7b2e0`  
Result: **inventory and dialogue inspection complete; automatic subtitle/story acceptance not passed**.

## Scope and privacy

Inspected the user-designated work-footage folder. It contains 61 MP4 sources, approximately 29.93 minutes and 17,428,390,342 bytes. No existing completed edit or project sidecar was found inside that folder. Related historical planning notes were read for orientation; their old export/approval claims were not treated as current verification.

Customer/project names, original file paths, source media, frame samples and full transcripts remain private. This report uses anonymous dialogue IDs in the order of the four camera-source filenames. No original media was modified, copied to GitHub or opened for native editing.

## Inventory and actual checks

| Source group | Files | Minutes | Audio stream present |
|---|---:|---:|---:|
| Additional camera | 4 | 8.36 | 4 |
| River views | 14 | 6.37 | 14 |
| Building views | 6 | 1.19 | 6 |
| Behind the scenes | 37 | 14.01 | 37 |

`ffprobe` inspected every file. Ten frame samples were visually inspected: all four additional-camera sources, two river views, two building views and the two longest behind-the-scenes sources. The four camera samples show dialogue; scenic samples show river/building/interior footage; behind-the-scenes samples show people and production activity. Sampling does not classify every frame or establish that all other 57 files lack useful dialogue. An audio stream alone does not establish speech.

The four camera sources are 1728×3072 portrait, with durations 203.419883, 119.611150, 117.917800 and 60.785717 seconds. The inventory also contains 3840×2160 media. Frame-rate metadata differs between sources; no new ratio conversion or subtitle-fit test was performed.

EditorKrab's actual `video_edit.transcribe` re-transcribed all four complete dialogue sources using configured large-v3 and Thai, with the normal 15-second window. Calls took 43.74, 11.54, 12.79 and 10.13 seconds: 78.20 seconds total, including initial model loading, excluding inventory, editorial review and rendering. These measurements are not an end-to-end speed or accuracy benchmark.

## Content findings

| Dialogue | Duration | Topic observed in ASR | Assessment |
|---|---:|---|---|
| [Private work description removed] | 3:23.42 | Teaching AI personal/business context and retaining human decisions | Strongest story candidate. Exclude the initial setup/countdown and incomplete interviewer transition at the end. A provisional source interval 36.02–195.98 has a coherent argument and conclusion; its audible boundaries still require confirmation. |
| [Private work description removed] | 1:59.61 | Home/device data, AI assistance and repeated business questions | Different emphasis from A, but overlaps its AI/business theme. Must compare selected material to avoid repeating the same takeaway. Several technical phrases are unreliable in ASR; do not generate final subtitles from the raw text. |
| [Private work description removed] | 1:57.92 | Solar and EV integration | Topic identified, but ASR contains nonsensical terms and missing content near 90–100 seconds. No complete final story interval is approved. Reliable transcription/audio review is required before segmentation. |
| [Private work description removed] | 1:00.79 | Designing homes with health/wellness considerations | Short standalone candidate; do not pad it to three minutes. Verify brand/standard terminology. The tail contains a transition to the next question; ASR word pieces place the closing polite particle near 57.76–58.36 and the next-topic phrase near 60.10–60.70. Those estimates are not listening proof. |

**Four provisional topic candidates are not four approved finished clips.** B may be retained separately or combined/reselected after checking repetition. C cannot pass based on the current transcript. No final story-count claim is made for all 61 sources.

## Failed verification attempt preserved

B and C were re-transcribed with a 30-second window. The installed batched pipeline warned that segments longer than 30 seconds would be truncated. B still had unreliable technical wording; C lost most of its initial question in the result and still had missing/incorrect text. This experiment is **inconclusive/unsuitable**, not an improvement. Neither output replaced the normal 15-second baseline or any original data.

This identifies a real acceptance gap: syntactically valid JSON and transcript row boundaries do not guarantee transcription coverage or a complete story. The previously recorded importer safeguard failures also remain unresolved.

## Remaining gates

Confirm the intended use of these raw sources versus the earlier interview edits; a clarification was requested while inventory and dialogue inspection continued. This pass assessed the designated raw folder. The three large parent-folder videos were not transcribed or assessed, and the other 57 sources were not fully transcribed. Direct human listening, manual correction of technical terms, final selection/import, new subtitle/emphasis/motion rendering, native CapCut opening and user approval remain pending. No finished batch was generated.

Private drivers: `state/inspect_work_footage.py` and `state/transcribe_work_footage.py`; private artifact location is recorded in `state/work-footage-root.txt`. Sanitized measurements: [work-footage-review-20261014.json](work-footage-review-20261014.json).
