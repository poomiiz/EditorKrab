---
name: clip-workflow
description: Editing rules behind make-clip - choose duration before self-contained stories, place cuts and Thai emphasis from meaning and real speech timing, then review B-roll, audio and output. Read it for judgement; use make-clip (scripts/run_clip.py) to run the workflow.
---

# Clip workflow (any client)

Order matters. Each step has an output file you can show; never skip to the next step without it.

## 0. Setup check
Run `python scripts/doctor.py` once per machine. It must print all green before any work. Paths come from `config.json` (see `config.example.json`) - never type a drive path into a script or skill.

## 1. Set the target and transcribe once
Before selecting stories, read the person's approximate duration or “ตามเนื้อหา” (`max_minutes: null`) and output ratio. Reuse any choice
already given; save approximate minutes as `max_minutes` in `<video>.story-preferences.json` through the app/export
flow. The target guides selection, with shorter or slightly longer accepted to finish a thought; it is not a
mandatory 60-130 second bracket. Null follows complete natural thoughts; ask no extra question after attaching
the exported packet. CLI `--max` expresses a numeric target in seconds.
The transcript is made by `scripts/run_clip.py` (`<video>.transcript.json`, Thai lines + times).
- Thai speech-to-text is a draft. Names, brands and English words come out wrong; fix them from the client's word list (`clients/<client>/words.txt`) before you write cards.

## 2. Pick the stories (one idea per clip)
Read the transcript with that duration target and list candidate clips as `[start, end, title, promise]`.
A clip needs one understandable idea with enough context and a complete ending. Use only beats actually in the
source; a twist, lesson, tease or on-screen hook is not compulsory (`hook: []` is valid).
- Start where the point becomes understandable; skip unrelated warm-up without inventing a promise.
- Ask "what does the viewer take away?" No answer = merge or drop.
- No point may repeat across the series. Neighbouring ranges overlap at the edges - check.
- Never end mid-sentence. Stay near the chosen duration without padding or cutting off the thought.
Show the list to the person before cutting.

## 3. Cut
- Place cuts at real pauses and completed words. Keep or remove fillers, stutters and reactions according to the meaning, not a fixed cut interval.
- The shared answer contract removes complete lines and preserves source order. Keep that order and the
  speaker's meaning; do not promise rearrangement that this import path cannot represent.
- Reuse the existing word clock mapped through actual cuts; verify the edited timeline against audio. If word
  times are missing or no longer map correctly, obtain word-level transcription before emphasis. Never replace
  missing timings with proportional letter counts or claim synchronization from a structural check.

## 4. Cards and subtitles
- Pace and tricks change per beat: label each beat (hook, setup, proof, twist, reaction, laugh, list, number...) and pick the trick from `reference/tricks.md` whose trigger matches. Same trick everywhere = flat clip.
- Split normal/emphasis text from real content and thought boundaries; normal text stays while the punch
  appears at its actual spoken-word start, and both end at the next phrase. Plain content needs no invented
  emphasis. Fit the preset to the selected canvas rather than chopping by character count or elapsed seconds.
- Cover every important idea, action, example, comparison/list item and result with meaningful emphasis.
  Review the whole clip for omissions; do not cap emphasis by a fraction of phrases or colour filler to meet a quota.
- Every spoken stretch has a card; no gap longer than about 1 s while someone talks.
- Keep words spoken in English in English.
- Colours, sizes and positions come from the project's subtitle preset (`presets/<name>.json`).

## 5. B-roll inserts
`scripts/run_clip.py` fills free Pixabay b-roll and lists one search link per spot; the person downloads anything else by hand and it goes in with `--insert-from`. Place each insert on the exact spoken keyword it shows.

## 6. Audio
Music under the voice (volume from the style skill). SFX only where the style skill says.

## 7. QC, pilot first
Run the `clip-qc` checklist on the **first** clip and have the person watch it in CapCut before you build the rest of the batch. Report problems with time stamps.

## 8. Log every clip when it is done
One row per clip in the central work log, the moment the clip is finished (never later from memory):
```
python scripts/log_clip.py add --agent <claude|codex> --project "<client>" --item "<clip name>"   --started-at <ISO time> --minutes <agent minutes>   --metadata '{"human_fix_min": N, "source_range": "a-b s", "final_len_s": N, "title": "...", "fixed_by": "agent|person", "rollback": "draft_content.before_*.json"}'
```
- `--started-at`, `--minutes` and `human_fix_min` are measured, never estimated. Unknown = leave the field out.
- A row about a problem also needs `--problem`, `--cause`, `--fix`.
- Also update the client's `EDIT_LOG.md` (copy from `templates/EDIT_LOG.md` on the first clip): state table + per-clip history.
- Always appended to `<output>/logs/clips_log.jsonl`; on the office machine it also writes the central work log. Team machine: hand that file to the office, where `python scripts/log_clip.py import <file>` loads new rows (already-imported rows are skipped).

## Time budget
Record transcription, preparation, export and review time through human approval separately. Use measured
results for estimates; an assumed clips-per-day target or an encode time alone does not prove editing savings.
