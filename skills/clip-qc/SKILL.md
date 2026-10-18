---
name: clip-qc
description: Pre-delivery checklist for a finished short clip (CapCut draft or MP4). Use before saying a clip is done, before sending it for review, and on the first clip of every batch.
---

# Clip QC

Check with your eyes and ears on the real output, not on the plan. Grab frames with `ffmpeg -ss <t> -i out.mp4 -frames:v 1 f.png` at a card, an insert and the ending; listen to the first 10 s and the last 10 s.

1. **Story** - the approximate duration was chosen before story selection; one idea, meaningful opening and a complete ending. Shorter or slightly longer is acceptable to finish the thought. An invented hook or ending is never required; `hook: []` is valid.
2. **Duplicates** - no point already made in an earlier clip of the series.
3. **Subtitles** - verify against the actual audio: normal/emphasis splits follow meaning; the punch appears at its actual word start while the normal lead stays, then both end together at the next phrase. No letter-count or fixed-delay timing. Missing word times block emphasis claims. Names and brands are correct; investigate gaps > 1 s during speech rather than filling silence. Run `ClipKit: ตรวจคำผิด` whichever speech model made the subtitles.
4. **Thai text** - no broken vowels or tone marks; text fits the chosen canvas/preset. Resolve overflow at a real thought boundary or with fit settings, never by inventing letter-timed fragments.
5. **Cuts** - no cut inside a word or syllable; no flash frames.
6. **Inserts** - each on its keyword; framed on the subject; no watermark; no banding.
7. **Audio** - voice clear; music audible but under the voice; SFX not louder than the voice; no click at a cut. ClipKit levels every MP4 to -14 LUFS (`render.LOUDNESS`); a CapCut project is not levelled, so switch on CapCut's loudness normalisation on export.
8. **Colour** - footage not washed out or over-saturated (HDR/Dolby Vision sources need checking on the export).
9. **Format** - exported aspect ratio matches the person's selection (portrait, landscape, square or source); duration follows the chosen approximate target and complete thought. File plays from start to end. No text under platform controls or outside its safe box: review `render.safe_zone` findings and actual frames at that ratio.
10. **Branding** - client logo/overlay present per the style skill.
11. **Pace** - CapCut project: `python scripts/capcut/pace.py check "<project>" <client>` (or `run_clip.py --capcut --client <client>`) has no problems: cards and inserts not held longer than the person holds them for that client, enough cards per minute, no long stretch without a card. `low_data: true` = the client has under 5 approved minutes; treat its limits as a guide.

Report `time - problem - fix` and separate structural/render checks from actual listening, native CapCut opening
and the person's approval. Record measured run/review time; leave unobserved checks pending. A batch is released
only after clip 1 passes and the person has watched it.

After the person approves a clip, add it to `<edit_data>/approved.json` with `"ai_version": "clipkit_ai_version.json"` (ClipKit saves that copy on every CapCut build), then run `editdata.py extract`, `pace.py baseline` and `pace.py learn`; a change the person made in 3+ clips is a rule idea to add here.

Add a line here every time a reviewer sends a clip back for a reason not on this list.
