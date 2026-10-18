# Onsite source-audio story selection — 2026-10-14

Task: `a4f7f5ff-1413-4262-919e-e74d0eea5c9e`  
Product revision: `5bfb2bc`  
User confirmed: split stories from audio in the designated folder, rather than insert these images into earlier interviews.

## Coverage

Screened audio from all **61 source files**, reading all 474 baseline ASR rows. Reused the four complete baseline dialogue transcriptions from the preceding inspection and ran EditorKrab's real `video_edit.transcribe` on all remaining 57 complete sources (Thai, configured large-v3, normal 15-second window). New calls took 151.45 seconds including model load; this is not total review/editing time. The unsuitable 30-second retries were not used.

This is full ASR coverage and textual editorial screening, not direct listening to all source audio. Short repeated greeting outputs on scenic sources are not accepted as proof of useful speech. Ambient chatter, setup, countdowns and incomplete answers were excluded from selection. A behind-the-scenes source contains an interrupted AI question/answer followed by a request to stop; it was not promoted to a standalone story.

## Imported selection plan

| Source ID | Selected topic | Source start–end | Length | Current status |
|---|---|---:|---:|---|
| 01 | Teach AI to answer in your own voice | 36.02–135.46 | 1:39.44 | Imported plan; coherent setup, method and stated practical outcome in ASR |
| 04 | Health considerations when designing a home | 0.68–58.36 | 0:57.68 | Imported plan; includes the complete question/answer and excludes the next-question cue |

The saved approximate target remains three minutes. Shorter complete thoughts are allowed; neither selection was padded. The two selections have different takeaways. Their titles/summaries describe the speaker's statements and are not verification of AI or health claims.

Created private byte-identical copies of the two selected media files, verified against source SHA-256, and prepared separate trial sidecars. Original footage and its neighboring files were not changed. Exported the shared Browser AI packet and imported a Codex-authored answer through the real `scripts/web_ai.py import stories` CLI for each file. The stored selections and `video_edit.stories_from_agent(..., split_long=False)` returned the exact planned ranges and one selection per source. No external browser AI provider was tested.

**Two imported selections are not two finished or approved video exports.** Source media has not yet been cut into new subtitle/motion projects in this task.

## Four sources held rather than silently promoted

| Source ID | Observed topic | Reason held |
|---|---|---|
| 02 | Home data and AI assistance | Unreliable technical terms; overlaps the other AI source unless the takeaway is narrowed |
| 03 | Solar/EV integration | Incorrect wording and missing ASR coverage; cannot verify a complete ending from the current transcript |
| 48 | AI/camera-related building security discussion | Fragmentary background dialogue with long missing spans; topic itself needs audio confirmation |
| 50 | Starting as a contractor and adding material sales | Newly found in behind-the-scenes audio, but distant/incorrect transcription requires correction before final boundaries |

Source 56 is a separate interrupted take about choosing AI tools; it was excluded, not counted as a completed story. No final approved story count is claimed for the folder.

## Reproduced consumer timing defect

On source 04, the original final ASR transport row combines a closing polite particle with the next-topic cue. The selected end was 58.36 seconds, but the existing story consumer returned **57.76** because it only includes whole ASR rows within a half-second tolerance. Reproduced through the actual importer and consumer in an isolated temporary fixture.

For this private selection trial only, split that transport row at the existing pause: its recorded closing-word pieces end at 58.36, while the next-topic pieces begin at 60.10. Assertions confirm all text and all original word clocks were preserved in order. No words or synthetic timings were added. The original ASR file was preserved. With this prepared transcript, the real consumer returned 58.36 exactly.

This is **manual preparation in a test artifact**, not a product fix. Unprepared AI answers can still snap incorrectly at semantic endpoints inside ASR rows. The previously recorded invalid-input safeguard failures remain unresolved. Both problems must be fixed in the shared functions before claiming reliable automatic operation.

## Remaining acceptance and privacy

Human listening, term correction, audible boundary confirmation, finished subtitle/emphasis/motion export, native CapCut playback and user approval remain pending. Only story selection/import was performed; no CapCut drafts or finished batch were generated.

Media, source mappings, transcripts, packet/answer files and a Thai review sheet remain in the private trial referenced by `state/work-footage-root.txt`. Customer names/paths and source content are excluded from GitHub. Sanitized measurements: [onsite-story-selection-20261014.json](onsite-story-selection-20261014.json).
