# Match chosen inserts to the actual spoken content

Read this packet and the accompanying named contact-sheet JPEG. The person has already chosen these local
files for this task. Do not ask another question, search the web, download anything, or return local paths.
The numbered tiles follow `assets` order. A tile is one representative frame, not proof of every frame,
visual suitability or usage rights. Use `source_ref` as provenance only: `unverified` means rights still need
the person's check, even when a provider calls the item free. Omit a placement if no supplied asset helps.

Choose images that clarify the sentence. Repeated searches in different cues are separate opportunities.
Prefer the supplied `opportunity_id` and its exact `anchor`; `kind: keyword` starts at the actual emphasized
word while `kind: phrase` starts at the actual phrase. You may use a copied full-word anchor
`{"cue_id":"...","kind":"word","word":1}`: word numbers are **1-based underlying ASR indices**, so
numbers can skip when Thai ASR pieces form one whole word. Never estimate timing by letters or invent a cue.
Phrase numbers are **0-based** and copied from the opportunity. A manually chosen absolute time uses
`{"kind":"timeline","start":1.25}`. Do not move an anchor by adding a guessed numeric `start`.

Each chosen item needs `asset_id`, `anchor`, a short content-specific `reason`, `source_start`, `dur`,
`focus_x` and `focus_y`. Source offset and duration are seconds. Focus is normalized 0..1 (center .5,.5).
Use source_start 0 for an image. A video must have enough source remaining; do not request a longer span
than the asset, stretch it or invent looping. Keep placements inside the clip and do not overlap them.
Only choose IDs in this packet's `assets`; do not infer IDs from filenames. `place: []` is valid.
`insert_settings.canvas` is the actual output ratio for choosing the focus. When video speed-ramp is on,
the first 0.6 output seconds slow from 3x to 1x: durations >=0.6 consume `dur + 0.6` source seconds;
shorter durations consume `3*dur - dur*dur/0.6`. Still images do not speed-ramp.

Return one JSON object in a `json` fenced block. Copy `clipkit_web_ai`, `step`, `operation`, `project`,
`packet_id`, `revision`, `source_id` exactly from the packet. Never generate or transplant these identifiers.
Do not include subtitle `lines`, `cut`, `motion`, `hook` or `caption`. The answer changes only the insert list;
existing music, SFX, subtitles and footage remain the editor's existing decisions. This is a plan for preview
and explicit application, not a claim of editorial approval or verified licensing.

```json
{"clipkit_web_ai":2,"step":"clip","operation":"inserts","project":"COPY_FROM_PACKET","packet_id":"COPY_FROM_PACKET","revision":"COPY_FROM_PACKET","source_id":"COPY_FROM_PACKET","place":[{"asset_id":"COPY_ASSET_ID","opportunity_id":"COPY_OPPORTUNITY_ID","anchor":{"cue_id":"COPY_CUE_ID","kind":"keyword","phrase":0},"reason":"This visible subject explains the spoken point.","source_start":0,"dur":1.5,"focus_x":0.5,"focus_y":0.5}]}
```
