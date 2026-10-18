# Four selected story excerpts — review, 2026-10-14

Task: `23187ac4-d1ac-4ebb-91a7-6e51078e41f0`  
Product revision: `88a4212` (no product changes during this review).

## What was inspected

All four selected source ranges were extracted into private MP4 review files with accurate input seeking and video/audio re-encoding. These are source excerpts, not finished EditorKrab subtitle/motion exports. Each file was fully decoded by FFmpeg without errors. Opening, midpoint and ending frames were visually inspected: 12 frames total. The source is 256×144 landscape artwork with a small speaker inset; the large Thai artwork lettering is baked into the original video.

EditorKrab's actual `video_edit.transcribe(..., language='th')` re-read each complete source range plus eight seconds of context on either side, using the configured large-v3 model. A separate pass re-read the first/last 15 seconds of each extracted MP4. Fresh ASR and the original full transcript were inspected for content and opening/ending continuity.

**This is visual sampling, complete decode and machine-assisted audio/content review. It is not direct human listening or human approval.** Word timings from repeat ASR vary, especially across silence; they cannot certify that a syllable is intact at a cut.

## Per-clip assessment

| Clip | Range in source | Actual MP4 | Assessment | Required action before release |
|---|---|---:|---|---|
| [Private work description removed] | 166.04–366.14 | 3:20.12 | Best standalone candidate: introduces wife, friend's need, donor, request for work and employment outcome. Opening/ending text is recovered from the actual excerpt. | Listen to the boundaries and confirm the wife's name before generating corrected subtitles. About 30 seconds of wife/office setup could be tightened, but do not remove the relationship introduction blindly. |
| [Private work description removed] | 1090.39–1258.79 | 2:48.40 | Needs context. A call and temporary improvement are present, but the woman's agitation and warning to avoid "that person" depend on an earlier episode. | Re-select an opening that establishes the condition and relevant woman; reassess the ending as temporary relief, not resolution of the main narrative. |
| [Private work description removed] | 1790.06–1940.20 | 2:30.16 | The doll episode has a stated outcome, but introduces "what happened to me" and later references the previously described surrounding footsteps without including that incident. | Re-select setup from the source or explicitly treat as a continuation. Recheck the tail: context ASR recovers an additional final word after the saved endpoint; this is a boundary warning, not proof of an audible cutoff. |
| [Private work description removed] | 2224.60–2400.66 | 2:56.08 | Reveal is present, but opening starts after an unexplained year-long recovery. The spoken section also names the wrong woman and immediately corrects itself around source 2256.78–2263.34. | Establish the disappearance/relevant relationship in the selected material, or re-select a coherent opening. Remove the actual self-correction in the editing plan rather than silently changing who is speaking about whom in subtitles. |

These are editorial assessments, not automatic quality-gate results. Clip 1 is the strongest pilot candidate; clips 2–4 need re-selection or clearer context before being called standalone. None is release-approved. No new captions, emphasis, motion or CapCut drafts were generated or modified.

## Technical evidence and limits

- All four private MP4s have video and audio streams and decode end-to-end.
- Fresh complete-context ASR took 42.34, 12.84, 15.20 and 11.66 seconds respectively (82.04 seconds total, including first model load). These are transcription-call timings, not total editing or review time.
- Source-selected durations are 200.10, 168.40, 150.14 and 176.06 seconds; encoded MP4 durations are 200.12, 168.40, 150.16 and 176.08 seconds. At 25 fps, the maximum difference is half a frame; this does not establish speech alignment.
- Native CapCut opening, finished subtitle timing/fit, emphasis, silence-cut remapping, loudness compliance and motion overlap were not exercised. Original baked-in artwork text is not an EditorKrab-generated subtitle.
- The six importer safeguard failures in [the preceding test](story-split-20261014.md) remain unresolved.

Review media, full fresh transcripts and source context remain in the private trial folder. Sanitized measurements and boundary observations are in [story-review-four-20261014.json](story-review-four-20261014.json).
