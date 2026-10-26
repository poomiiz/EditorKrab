---
name: make-clip
description: One-request runner for a whole short clip - "ทำคลิปนี้", "ตัดคลิปจากไฟล์นี้", "ทำคลิปสั้นไม่เกิน 2 นาที", "make a clip from this footage", "ทำเป็นโปรเจกต์ CapCut ด้วย", and the commands the ClipKit app asks the person to paste ("ClipKit: แบ่งเรื่อง ...", "ClipKit: เลือกคำเน้น ..."). Runs scripts/run_clip.py end to end (transcribe, stories, cuts, subtitles in the person's preset, title, b-roll, music, MP4 and/or CapCut project) and does its two thinking steps. Use this as the entry point; it uses clip-qc.
---

# Make a clip (entry point)

> **Retired flow.** This file describes the old Python ClipKit. Its scripts (`scripts/run_clip.py`,
> `scripts/web_ai.py` and the rest) are no longer in the repository, so do not run them or quote their rules.
> EditorKrab is now the Rust app: work in the app, or, while it is open, through its MCP tools at
> `http://127.0.0.1:8871/mcp` (`import_videos`, `transcribe`, `get_project`, `get_transcript`, `set_clips`,
> `export_clips`). Story rules are `prompts/story_split.md`, which matches the app's step 2. Never report a
> story count before the footage is transcribed.

Run from the ClipKit folder. Stop and report `Blocked` with the exact error if any command fails - never skip a step or guess a value.

## New machine (once)
1. `python scripts/setup_workspace.py D:\ClipKit` (any folder) - makes `config.json` with every folder.
2. `python scripts/doctor.py` - every line OK (WARN on "preview font" is fine). FAIL = tell the person which line
   and stop. Python packages: `scripts/setup.ps1`. Node.js LTS from nodejs.org. Speech model: `scripts/fetch_model.py`.
3. Optional: Pixabay key (pixabay.com/api/docs) as `"pixabay_key"` in `config.json` to search stock candidates for review.

## One command: "ตัดคลิปนี้" / "ทำคลิปสั้นจากไฟล์นี้" (no ClipKit window needed)
```
python scripts/run_clip.py "<video>" [--max <approximate-seconds>] [--preset default] [--all] [--capcut] [--export] [--insert]
```
Before selecting stories, read the person's duration choice: approximate minutes or “ตามเนื้อหา” (`max_minutes: null`).
Reuse the packet's choice and do not ask an extra question after the person attaches it to browser AI.
`--max` expresses that target in seconds; story export accepts `--max-minutes`, the packet uses `max_minutes`
and the preference is saved in `<video>.story-preferences.json`. A complete thought may be shorter or slightly longer: do not force a cut at
the number, pad with filler, or assume every clip must be 1-3 minutes. Ask only what is missing: the video path,
duration choice when preparing the packet (natural content is a valid choice), and which subtitle preset (`default` when they have no preference;
list: the `presets/` folder; none they like: make one, see **Subtitle presets**). "ทำเป็นโปรเจกต์ CapCut" = add
`--capcut`; "ขอไฟล์ MP4" = add `--export`. `--look` (pair, karaoke, pop, none) and `--shape` (portrait default)
rarely change.
It transcribes, splits into stories, makes one project per story (face crop, subtitles, silences trimmed,
subtitle look, zoom cut), prepares stock candidates, makes the cover and the HyperFrames page, and writes
`<video> - ClipKit.html` with one Pixabay search link per b-roll spot. Rerunning skips every finished step.
1. Run it. Output is one JSON line per step.
2. `{"WAIT": ...}` (exit 2) = a thinking step is yours: do the pasted command(s) below (`แบ่งเรื่อง`, then
   `เลือกคำเน้น` for each project), choose complete stories near the duration preference, then run the same command again.
3. Pilot rule: without `--all` only story 1 is built. Send the person the HTML file and wait for their OK
   before `--all`.
4. Inserts: the person selects their own images/videos or downloads stock candidates into a folder. Run
   `--insert-sheet "<folder>"` to create one complete browser/local AI prompt and contact sheets using the shared
   registered asset catalog. `--insert-from "<folder>"` without a plan does the same and exits `WAIT` (2); filenames,
   link numbers and download order never choose placements. Review candidates and import the explicit reply with
   `--insert-from "<folder>" --insert-plan "<answer.json>"`. `--insert` only verifies/reuses accepted placements.
   Original files remain in place. Existing music/SFX remain intact. Never log in to a stock site for them.
5. `--capcut` also builds "<project> · CapCut": the same clip laid out in CapCut (subtitle roles on their own
   tracks, title, English caption, b-roll, clicks, music, zoom, skin) for a person to keep editing there.
6. Before showing the person any project, do the `ClipKit: ตรวจคำผิด` steps below on it. This is not optional and
   applies whichever speech model is set (large-v3 or turbo); ClipKit's own clean-up (spacing, English terms,
   learned fixes) already runs inside every transcription.
7. `--export` renders the MP4s; measure the actual run rather than promising an unmeasured speed. Review frames
   and audio before saying done, then record the time until the person approves the pilot.

## Commands pasted from the ClipKit app
The app does the mechanical work; the thinking steps are yours. It shows the person a line to paste here.
The same two steps (แบ่งเรื่อง, เลือกคำเน้น) use one answer contract for browser AI and optional local agents:
`scripts/web_ai.py` exports a self-contained packet and imports its JSON answer (`prompts/web_ai/schema.md`).
Always import the answer through this validator; do not write the resulting sidecars or `draft_content.json` directly.
Manual editor controls remain available without AI; they edit the same project artifacts under the shared writer lock.

**Current stories/clip answer contract is v2.** Copy `clipkit_web_ai: 2`, `step`, `video`/`project`, and the exact
`packet_id`, `revision`, `source_id` from the exported packet. All three identifiers are opaque: never generate
them, transplant them into an old answer or infer identity from a basename. Preserve each line's `n` and
`cue_id`, including cut/group-continuation entries. Insert matching uses the same v2 `step: "clip"` envelope
with `operation: "inserts"`, bound to its own exported packet. Legacy unbound v1 insert plans require fresh export.

Each uploaded packet names only the local answer **basename**. Browser AI returns JSON without another prompt;
an optional local agent saves that answer beside the packet. Exporting again keeps pending answers as
`.answer.json.before-export-<timestamp>`; successful local imports retain `.answer.json.imported-<packet_id>`.
Old v1 packets/answers remain readable history but cannot mutate stories or clips; export a new v2 packet.

Every new/unreceipted answer needs the latest active packet and unchanged source/dependency revision, including
transcript, brief, target, cue/word clock, native timelines, style and preset/font/template dependencies. After
an edit or a new decision, export again. A committed packet plus the same canonical JSON answer returns its
durable receipt even after later exports or edits: it does not cut twice or restore an earlier project state.
The same packet with different content is rejected. CLI import, browser paste and local polling share this rule.

Import validates/prepares all metadata and unique private motion assets in staging before applying under the
writer lock. Ordinary failure rolls back; an interrupted application without a receipt recovers before the next
edit/import. A durable receipt commits the successful result and must never be rolled back on retry. Recovery
that finds unexpected external/native edits preserves both versions and stops for review; do not delete the
recovery state, reseal a draft or bypass the importer to continue. Close CapCut before modifying its project.
Successful JSON/import checks still require human viewing/listening and native CapCut acceptance before calling
a clip done. Obtain approval of one pilot before building a batch.

### Browser/local AI insert matching

Run `python scripts/run_clip.py "<video>" [--all] --insert-sheet "<asset folder>"` after story selection.
Attach every returned `upload` file to the chosen browser AI; the packet already contains all instructions.
The CLI reply is `{ "clipkit_web_ai": 2, "step": "clip", "operation": "inserts", "projects": [...] }`.
Keep one individually bound envelope for every requested project, with its exact identifiers and `place: []`
when no image helps. The local agent can save the same JSON next to the packet and run the import command.

Each placement selects a registered `asset_id`, a copied cue/whole-word anchor, a content-supported `reason`,
`source_start`, `dur`, `focus_x` and `focus_y`. Use only actual anchors listed in the packet. Do not generate
paths, invent word times, reuse a previous packet's asset list, select the first search hit without review,
or claim a stock license is verified from an AI answer. Keep provenance/status supplied by the catalog.
The source crop/offset and cue clock are validated by the same planner used in the manual editor.

Projects commit separately. If a later project fails, the CLI reports completed receipts; fix the blocker and
retry the same answer. A committed project is returned from its receipt rather than applied again. Invalid
answers are preflighted before the first apply; concurrent changes can still stop a later project. Preserve
the recovery state and both versions if an external edit prevents recovery.

### `ClipKit: แบ่งเรื่อง "<video path>"`
1. Run `python scripts/web_ai.py export stories "<video path>"` and read the complete packet
   `<video path>.web-ai-stories.md`. It contains source rows (`cue_id`, `start`, `end`, `text`) and full-word
   `words` clocks (`n`, `end_n`, `start`, `end`, `text`, seconds), the
   person's brief and their saved duration choice. Missing transcript = report the error and ask them to
   transcribe first. Choose and shape stories to match the brief while following the rules below.
2. (Retired flow; the current rules are `prompts/story_split.md`: cover the whole file, one point each, about
   1-2 minutes, never ending mid-sentence.) Read the packet's duration preference before selecting ranges.
   Use source `start_anchor`/`end_anchor` (`cue_id`, `word` copied from `words[].n`) to choose whole-word edges
   inside ASR rows. Word numbers may skip because partial Thai pieces form one complete word. With no word
   clock, omit `word` and use the row edge. Numeric times, if included, must match the actual anchors. Retain
   `reason` and `duration_reason`; duration deviation requests review and never authorizes truncation or approval.
3. Write its complete v2 answer schema with the copied identity fields as UTF-8 to the named answer file beside
   the packet, `<video path>.web-ai-stories.answer.json` (not directly to `.stories.json`).
4. Run `python scripts/web_ai.py import stories "<video path>" "<video path>.web-ai-stories.answer.json"`
   when working from the CLI. The active app AI box can poll/import the same answer; a duplicate returns its
   receipt. The list and `.story-selection.json` binding commit together; never write either directly. Missing
   or stale bindings require explicit validation/save, including legacy local lists. Reply with the list
   (number, title, actual length versus target and review reason) after validation succeeds. For a changed answer, export
   another packet rather than reusing its identifiers.

### `ClipKit: แปลซับเป็นอังกฤษ "<CapCut project folder>"`
1. Read `<folder>/clipkit_subs_th.json` (list of `{start, end, text}`).
2. Translate every line into short natural English for a vertical short; keep brand names and technical terms.
3. Write `<folder>/clipkit_subs_en.json` as a JSON list of strings, exactly one per Thai line, same order.
4. Tell the person to press "ซับขาว ภาษาอังกฤษ" again.

### `ClipKit: ตรวจคำผิด "<CapCut project folder>"`
1. Read `<folder>/clipkit_lines.json` (subtitle lines in order), project `clipkit_corrections.json`, and retained
   machine `corrections.json` beside ClipKit if present. Project rules override machine rules.
   (`{wrong: {"right": ..., "n": times}}`, this person's earlier fixes).
2. Find words the speech model misheard: wrong spellings, sound-alikes that make no sense in the sentence
   ("คัป" -> "ครับ", "กอ" -> "ก็"), names and brands spelled two ways in one clip. Keep the speaker's own words
   and slang; this is not a rewrite. Check each line at three levels (letter, word, whole line) for:
   - Thai spelling by the Royal Institute dictionary (อนุญาติ -> อนุญาต, ไกล้ -> ใกล้), missing or extra
     การันต์ / ไม้ยมก / ไม้ไต่คู้ / ไม้หันอากาศ, a word split in the middle ("ประ เทศ"), ฯ ๆ used wrongly.
   - Loanwords spelled one way through the whole clip; English terms written as English with a capital first
     letter and a space either side (`video_edit.ENGLISH_TERMS`); brand casing (YouTube, iPhone, LinkedIn).
   - English lines: spelling, grammar, capitals, one of US or UK spelling throughout.
   - Names, titles, numbers, dates and money the same everywhere they appear; Thai or Arabic digits, not both.
   A line that is correct but could be written another accepted way is a suggestion, not a fix: list it,
   do not change it. Never guess a word you cannot tell from the line and its neighbours; mark it "ไม่แน่ใจ".
   Also find lines that read incomplete: a sentence cut off, a word plainly missing ("เราต้อง ให้ลูกค้า"). For
   each, run `python scripts/relisten.py "<folder>" "<line as it is>"` to hear that stretch again, and complete
   the line from what it prints. Never invent words the speaker did not say; if the re-listen does not have
   them either, leave the line and list it for the person.
3. Close-CapCut check: ask the person to close CapCut if it is open. Then for each line to change run
   `python scripts/fix_text.py "<folder>" "<line as it is>" "<fixed line>"` (the old version is kept and the
   fix is learned only in this project for its next transcription). Use `--cue-id` for repeated lines.
   Only an explicit request to share a correction across projects permits `--learn-global`.
4. Reply with a short table: line number, line, wrong -> right, level (ร้ายแรง = meaning, name or number changes;
   ปานกลาง = spelling or grammar; เล็กน้อย = spacing, punctuation, consistency), then the suggestions and
   "ไม่แน่ใจ" lines, and incomplete lines the re-listen could not fill. Say "ไม่พบคำผิด" when there is nothing to
   fix. Whole stretches with no subtitle at all are the editor's "เติมซับ" button, not this.

### `ClipKit: เลือกคำเน้น "<CapCut project folder>"`
For the sentence-pair subtitles (normal lead line + bigger emphasis punch line, in the project's preset).
First run `python scripts/web_ai.py export clip "<folder>"` and read `<folder>/clipkit_web_ai.md`.
The rules below describe choices for the packet's `lines`, `hook`, `caption`, and `motion` fields.
Save one complete answer to `<folder>/clipkit_web_ai.answer.json`; import it with
`python scripts/web_ai.py import clip "<folder>" "<folder>/clipkit_web_ai.answer.json"` for CLI work,
or let the active app AI box import it. Include the packet's exact v2 identity fields; a duplicate paste/poll/CLI
import returns its receipt, while changing the answer requires a fresh export. The packet displays only
`clipkit_web_ai.answer.json`; save it beside that packet, never invent an absolute path from an uploaded file.
1. Read `<folder>/clipkit_lines.json` and the packet's original numbered ASR lines in context. Transport lines
   may end mid-thought: group consecutive lines with `through: <last n>` on the head and `{n, cue_id}` continuations,
   then cover the group's complete source text with semantic items. Never group across a cut; caption/motion
   refer to heads. The shared importer preserves actual word times for browser AI and local agents; manual
   consecutive-line merge uses the same helper.
2. Read the packet's actual `words` clock. Split each line by meaning and spoken thought boundaries, in order;
   never split by character count, a fixed number of seconds, or a fixed emphasis delay. Each phrase = `[lead, punch]`: the punch is the word or short phrase that
   carries the point (a feeling, a number, a result, a brand: "ถูกกดดัน", "20 ปี", "ไม่อิ่ม"), not just the
   last word. Lead + punch together must be that phrase's exact text (punch may come first: then lead is "").
   Every character of the line must be used once, in order (spaces may be dropped).
   The normal lead stays visible while the punch appears at its real spoken-word start; both end together at
   the next phrase. Never invent word times. Missing word times require a word-level transcription before
   emphasis; unselected content can remain plain without claiming audio synchronization.
   Not every phrase deserves a coloured punch. Filler and connective talk ("อืมๆ", "เออจริงๆ", "แบบว่า",
   "อะไรอย่างนี้", repeated words) and phrases with no point get white only: `[text, ""]`. Colour only the
   phrases that carry the message. Cover every key idea, action, example, comparison/list item and result;
   one idea may need several spoken emphasis beats. Do not cap the proportion or count of punches, or leave
   an important point plain to meet a quota. Review the whole clip for missed emphasis without colouring every word.
   Fit the preset's normal/emphasis roles to the chosen canvas and safe area. Choose another genuine thought
   boundary when necessary; do not make letter-timed fragments just to fit the screen.
   A learned `fixed_size` preset keeps each role's size and center throughout the clip, including sentence-pair
   motion. If a phrase overflows, shorten its `show` without changing meaning or split at a real thought/word
   boundary. Do not shrink individual phrases; keep source lead/punch and word timing intact.
   Display options - a 4th item `{"look": ..., "show": [white, colour]}` per phrase ("white" = the preset's
   normal text, "colour" = its emphasis text), measured from real hand-edited clips:
   - `pair` (default): white lead + coloured punch.
   - `white`: white only - setups and questions ("จะแนะนำยังไง", "ธุรกิจเขาเป็นยังไง").
   - `color`: coloured only - a short line that is all point.
   - `red`: the colour line turns red - the one or two strongest points or the clip's question.
   - `hold`: the previous white line stays while the coloured line changes - lists ("บางคนบอก" ->
     "ทำแบรนด์ดิ้ง" / "ลงระบบ CRM" / "จ้างเซลส์มาขาย").
   - `skip`: no subtitle - filler, repeats, false starts, the other person's "อืมๆ".
   - Two `color` phrases in a row stack in two beats, one row each: the first stays on the upper row until the
     second is done. Use `hold` instead when a white line has to stay; then the colour lines replace each other.
   - `color`: `"#rrggbb"` - this line's own emphasis colour (default the preset's).
   - `second`: an actual short reaction spoken by the second person, shown off centre and tilted only when
     the preset has a `second` role (e.g. `4-levels`); never invent a reaction.
   - `show`: an optional shorter display wording that preserves the speaker's meaning. Lead + punch still
     contain the exact spoken words for timing. It does not authorize invented claims or letter-based splits.
   Example item: `["บางคนบอกต้องลง", "ระบบ Crm", {"look": "hold", "show": ["", "ลงระบบ CRM"]}]`;
   with a b-roll search: `[lead, punch, "office team computer", {...}]`.
3. B-roll: when a relevant image would clarify a product, food, place, action or visible reaction,
   add a third item: a short English stock-footage search, e.g.
   `["เดี๋ยวมากิน", "กาแฟต่อ", "coffee cup cafe"]`. Concrete nouns, 2-4 words, no brand names.
   Skip unsupported matches; do not add images to meet a count or frequency. A search query prepares
   candidates only; the explicit insert plan selects the asset, spoken anchor and usable source interval.
4. Fill the answer's `lines` with numbered entries and their items; the importer writes `clipkit_punch.json`. Each item is
   `[lead, punch]`, `[lead, punch, query]`, `[lead, punch, options]` or `[lead, punch, query, options]`
   covering every line.
5. Clip title is optional: use `hook: []` unless requested or useful without obscuring important speech.
   If used, choose 1-2 short lines supported by the source, with `white`, `orange` or `red` colours. The title
   owns the first 4 seconds and suppresses subtitles there; check that this does not hide needed content.
6. Only when the packet has a non-null `caption_language`: fill `caption` as `{"1": "<translation>", ...}`
   for every retained line, in that language (otherwise use null) -
   short and natural ("If I want to start my own business from scratch, let's say..."). Brand names stay.
7. Tell the person the clip is ready to review in ClipKit (แท็บ ส่งออก > ดูตัวอย่าง).

### `ClipKit: สร้างท่าใหม่ ใช้ทำ "<role>" อารมณ์ "<mood>"` and `ClipKit: ทำเทมเพลตจาก HyperFrames "<name>"`
Owner only: do this only when `config.json` has `"creator": true`; otherwise say it is done on the owner's machine.
1. Read `motion/library/README.md` and `motion/library/hyperframes.md`. A new move must not repeat an existing template in `motion/*/` or a registry item with the same role and mood; for the HyperFrames command, start from that item (`npx hyperframes@0.8.101 add <name>` in a scratch project) and rebuild it as our template.
2. Make `motion/<kebab-name>/index.html`: 1080x1920 transparent, Thai-safe (grapheme splitting with `Intl.Segmenter`, never per code point), lines shrink to fit the frame, `speed` and `font` params, and a `<script type="application/json" id="clipkit-motion">` block with `role`, `mood`, `label`, `fields`, `length` (copy the shape from `motion/hook-title`).
3. Render one MOV through `POST /api/kit/motion` with Thai words, pull 3 frames with ffmpeg and look at them (pilot first). Fix anything cut off or broken before going on.
4. Commit and push to ClipKit; teammates get it from the update banner and it appears on the motion page and in the library.

## Cut log (always)
Every finished clip is logged by `run_clip.py` (`<output>/logs/runs.jsonl`). Its output has a `check` per clip (score + problems
against the rules): fix every problem it lists (re-pick emphasis / title) and run again before showing the person. When the person complains about a
result ("ซับยาวไป", "ซับสั้นเกิน", "เรียงเนื้อหาไม่ดี", "หัวคลิปไม่ตรง"...), BEFORE you change anything run
`python scripts/cutlog.py feedback "<project folder>" "<what they said, their words>"`, then fix, then run it again
with `--fix "<what you changed>"`. Asked to send results back: `clipkit report` (or `python scripts/cutlog.py report`)
makes a small zip with no video - tell them it contains the clip's spoken words.
Every change ClipKit writes to a project (subtitles, style, cuts, hook...) is also recorded with its before and after
in the project's own `clipkit_changes.jsonl` (stays on this machine); `editdata.py extract` loads it into `edits.sqlite`
(table `changes`) for the clips in approved.json, so the owner's own style can be learned from it.
Each edit also keeps a copy of the project in `clipkit_history/` (an autosave history):
`python scripts/draft_history.py "<project folder>"` lists them, adding a version name puts that one back.
This is ClipKit - Autosave. The app also runs its draft bot (`scripts/draft_watch.py`, on every machine unless
config.json `"autosave": false`): every project CapCut itself saves goes into the same history and log, and a
save CapCut could not open is reported there. `python scripts/autosave_profile.py` turns the log into the
person's own editing style (`<output_dir>/autosave/profile.md`, profile.json) and `presets/my-style.json` (the subtitle
look of the project they edited last); the app remakes them once a day, run_clip.py uses my-style when no `--preset`
is given, and none of it leaves their machine.

**ClipKit itself goes wrong** (an error, CapCut will not open a project, a crash): report it through the log,
never as a loose file on the Desktop - `python scripts/cutlog.py feedback "<project folder>" "BUG: <what happened,
exact error text>" --fix "<what you tried>"`. It lands in the team sheet with the version. Do not hand-edit a
project's draft_content.json / Timelines files or ClipKit's code to work around it: a hand fix hides the bug and
is lost on the next update. Tell the person it is reported.
ClipKit also seals every project it writes: after a hand edit it stops with "ถูกแก้นอก ClipKit". Then make the
project again from the raw file. `scripts/accept_edit.py` is only for a change the person made in CapCut themselves,
never to get past your own edit.

## Subtitle presets (each person makes their own)
A preset is `presets/<name>.json`: how the normal text and the emphasis text look, plus an optional small
translated caption. Never name one after a client unless the person asks; ask them for a name.
- **By chat** ("ตัวปกติขาวขอบดำ ตัวเน้นเหลืองใหญ่กว่า"): copy `presets/default.json` and change it.
  Per role: `size` (CapCut text size, 15-40 normal), `y` (-1 bottom .. 1 top; normal above emphasis, about 0.12
  apart), `color` / `outline` as `#rrggbb`, `outline_width` (0-0.15). `caption`: `null`, or the same fields plus
  `language` (`"en"` translation from clipkit_caption.json, default; `"th"` the whole spoken Thai line) and
  `shadow` (true = dark shadow). `second` (optional): the same fields plus `x` (-1 left .. 1 right) and
  `rotation` (degrees, minus = tilted left). `presets/4-levels.json` is all four levels.
- **From the person's own CapCut project**: `python scripts/preset_from_capcut.py "<CapCut project folder>" --name <name>`
  reads the text styles they used most and writes the preset; show them what it wrote.
- Use it: `run_clip.py ... --preset <name>`, or the preset list in the ClipKit editor.

## QC and hand-over
Run `clip-qc` on the pilot clip. Tell the person what to open (the HTML page, the MP4, or the "· CapCut"
project), its length and what to check. Wait for their verdict before `--all`.

## Log the clip (the moment it is done)
```
python scripts/log_clip.py add --project "<client>" --item "<clip name>" --agent <claude|codex> \
  --started-at <when you started, ISO time> --minutes <measured agent minutes> \
  --metadata '{"human_fix_min": N, "source_range": "s-e", "final_len_s": N, "draft": "<draft name>"}'
```
- Always written to `<output>/logs/clips_log.jsonl`. On the office machine it also goes straight to the central work log.
- Team machine: the file prints "hand clips_log.jsonl to the office" - send that file; the office runs `python scripts/log_clip.py import <file>` (already-imported rows are skipped).
- A problem row needs `--problem`, `--cause`, `--fix`.

## Report format
End with one of `Changed` / `Verified` / `Blocked`, plain language, the draft name, and the next action for the person.
