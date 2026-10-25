> **History.** Written for the Python-era build, kept as a record. The Python app was removed on 25 Oct 2026; EditorKrab is now the Rust app only, so commands here that use Python no longer exist. / เอกสารเก่าของรุ่น Python เก็บไว้เป็นบันทึก แอป Python ถูกลบเมื่อ 25 ต.ค. 2026

# Real onsite AI pilot — 2026-10-14

Task: `4f8c1a50-3bb6-4d03-9f37-6eafd088d5ec`  
Status: **MP4 and native draft built; human/native playback acceptance pending**.

## Actual deliverables

Used one privately selected onsite dialogue source. The 99.44-second selected interval became a **71.28-second editing timeline / 71.30-second MP4**, 1080×1920. The footage was actually cut: it now contains two kept source pieces, rather than merely a JSON story list. The shared clip-answer importer removed eleven transport lines and merged retained ASR rows into semantic groups. There are 30 display beats, seven chosen emphasis beats and one rendered EditorKrab sentence-pair motion at the opening. No stock or synthetic voice was needed.

The source, project, full answer/transcript, frame samples and native draft remain private. The MP4 was presented inline for the user to watch. Native CapCut was closed when the draft was built; it has not been opened and played in native CapCut in this round. An editable JSON draft is not proof of native acceptance.

## Shared-function fixes, not manual CapCut repair

- `video_edit.slice_transcript` permits semantic edges inside an ASR transport row using existing word clocks and refuses cuts inside a Thai whole word. Importer and persisted selection reader now share validation for finite/source-bounded times, chronology and overlap. Story consumers retain selected endpoints rather than dropping the crossing row. Source-transcript reuse follows the same word-edge logic.
- Automatic silence trimming subtracts protected timed-word spans, including padding, from proposed quiet cuts. Existing shared retiming of text, word clocks and placed media remains tested. This particular pilot's initial silence pass found no safe removal; the actual shortening came from editorial line cuts. Do not claim silence removal was demonstrated on this footage.
- Corrected-spelling emphasis lookup aligns the corrected text against the original recorded word clock before onset lookup. A regression proves that replacing a long recognition token with `AI` does not move a later keyword to time zero. Replacement words still inherit a recorded token onset; this is not a new forced-alignment measurement or human listening proof.
- Real export exposed a missing `_split2` call in `auto_cover`. Reused the existing font-measured title fitter instead. Failed attempt retained in timing logs.
- Real rendered motion exceeded the safe box. Adjusted the existing sentence-pair template's positions and measured text width. Before/after frames show visible alpha≥8 bounds improve from `[86,1251,973,1492]` to `[122,1211,955,1446]` on 1080×1920. Very faint shadow pixels extend outside that visible-alpha box; no claim that every nonzero alpha pixel is contained.
- Native CapCut text fitting left no room for rounding/outline. Reserved 24 pixels and rounded fitted native size downward. The rebuilt native draft has **zero safe-zone findings**, with a focused regression on the long real phrase.

## Checks and observed limits

`doctor.py` passed Python/packages/FFmpeg/Node/config/GPU/disk/model/font checks. Focused suites passed: story timing 4, story target 6, trim timing 9, subtitle timing 12, semantic merge 6, font/native sizing 7, automatic cover 1 and motion handoff 12 (**57 tests**), plus all smoke checks. Reproductions failed before their responsible fixes, then passed. The old smoke fixture accepting a story past the transcript end was changed to require rejection.

The original, unprepared home-source transcript now preserves its 58.36-second endpoint using the new shared slicer; the earlier private manual transport split is no longer needed to demonstrate that function.

The actual MP4 decoded end-to-end. Visually inspected 17 exported frames, including before/after selected emphasis onsets: the normal lead remains while the emphasis appears. Observed no overlap between those two rows or with the speaker's face in these samples. This is sampled inspection, not per-frame exhaustive certification. New exported audio was re-transcribed completely; first and final content remain present, but recognition errors persist and direct human listening remains required.

Measured delivered loudness: **−13.99 LUFS**, true peak **−1.30 dBTP**. These values are measured from the delivered file, not just export settings.

Playwright loaded the real generated composition from a temporary loopback-only server and sought its GSAP timeline. Captured 375×844 and 1440×1000 views. The **raw fixed-resolution composition is not a responsive page** and is cropped at those viewport sizes; these captures are not a mobile/editor UI pass. The rendered portrait MP4 and actual exported-frame samples are the visual evidence. No authenticated EditorKrab UI acceptance was performed.

## Timing and acceptance

Three pipeline attempts totaled **478.227722 seconds**: 51.574022 seconds reaching the judgement WAIT, 23.768505 seconds failing on the cover bug, and 402.885195 seconds completing the pipeline. The successful export call itself took 238.0 seconds, including its preparation/normalization. Do not add nested step durations to attempt totals.

An independent float16/unbatched ASR comparison took 107.047 seconds, and exported-audio ASR took 22.49 seconds. Motion renders, engineering fixes, tests and review are outside pipeline attempt totals but inside the measured wall-clock trial, which started at 14:29:54 UTC. The JSON records elapsed wall time when evidence was captured. Earlier footage inventory/transcription is recorded separately in preceding reports and is not silently counted as new transcription in this pilot.

**No time-saving claim yet:** there is no measured manual-edit baseline or human-approval endpoint. The trial includes engineering/debugging, not just normal editing. Human review time and total time until approval remain open. The user has been asked to watch the actual MP4 before any batch is released.

Machine-readable measurements: [onsite-ai-pilot-20261014.json](onsite-ai-pilot-20261014.json). Local exact paths are in ignored `state/onsite-ai-pilot-context.json`; customer media and names are excluded from this public evidence.

## Human review: emphasis revision

The viewer rejected the pilot because emphasis was too sparse. The initial choice had 7 emphasis beats across 30 subtitle phrases. Removed the half-or-fewer guidance from both browser and local-agent instructions; selection must cover meaningful ideas, examples, lists and outcomes without a count quota. Prepared 22 proposed emphasis beats privately with exact source-text coverage. These have not been applied to the open native draft or rendered; playback approval remains pending. The existing 12 subtitle timing regressions pass.

Revision v2 was subsequently built after the viewer saved and closed CapCut. Original projects and the latest native JSON were preserved; a separate revision contains 22 emphasis beats using existing word clocks. Native safe-zone check returned no findings or build warnings. Regenerated sentence-pair motion uses the revised choices. Full MP4 decoding passed; eight sampled frames were visually inspected with no visible clipping or overlap. Build/export call took 276.8 s (including composition preparation; inner render/loudness call 161.3 s, do not add these). User playback acceptance is still pending; this is not a time-savings benchmark.

## Selected reference: stable subtitle geometry

The viewer selected a private hand-edited portrait project as the reference. All 23 normal segments use effective size 22.7 at y=-0.366; all 23 emphasis segments use effective size 22.136 at y=-0.484759, both centered. All 23 emphasis ends match their normal partner within 0.04 s; all 22 normal transitions are contiguous within 0.04 s. The actual v2 generated page varies normal font size 72-118 px and emphasis 96-167 px because phrases shrink independently. The sentence-pair template also uses independent typography. Fixed preset extraction to multiply by uniform text scale and preserve position precision; three focused tests pass. This extraction fix does not yet fix varying render typography or establish viewer acceptance.

## Revision 3: fixed reference typography

Learned presets now retain a fixed-size flag and the local font, including save/compose in the style library. Browser packets disclose layout sizes/centers without a local font path. Shared subtitle fitting rejects overflow instead of shrinking fixed text; FFmpeg, HyperFrames, native CapCut and sentence-pair motion consume the same preset. Three overlong display strings were shortened privately without changing spoken source strings or word times. Legacy presets retain their existing fitting behavior. Local font paths are omitted from GitHub style-share payloads.

Actual revision: 22 emphasis beats, normal 118 px/emphasis 115 px across the generated page; native text roles each have one size and center (22.7/22.13). The complete pair is translated upward consistently for the safe box, preserving reference spacing. Native safe-zone check has no findings. Full MP4 decode passed; four revised frames plus four prior frames were inspected. Build/export took 187.31 s, including the nested 154.1 s render/loudness call. Human playback/native review remains pending, and there is no manual baseline for a time-saving claim.
