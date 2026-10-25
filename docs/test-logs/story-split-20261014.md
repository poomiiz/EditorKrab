> **History.** Written for the Python-era build, kept as a record. The Python app was removed on 25 Oct 2026; EditorKrab is now the Rust app only, so commands here that use Python no longer exist. / เอกสารเก่าของรุ่น Python เก็บไว้เป็นบันทึก แอป Python ถูกลบเมื่อ 25 ต.ค. 2026

# Full-footage story selection verification — 2026-10-14

Task: `bc9986e3-6221-44cb-bda9-67dbf2613360`  
Product revision tested: `fb8715c`  
Result: **positive import/readback PASS; invalid-input safeguards FAIL; editorial acceptance pending**.

## Scope and provenance

Reviewed all 1,214 transcript rows of a 54:30.25 Thai narrative. Reused the existing large-v3 transcript; this was not a new transcription run. A separate private trial contains the copied media, exported packet, authored answer and imported selections. Existing editing projects were not changed.

Codex read the full transcript and authored the answer using the exported Browser AI packet contract. No external ChatGPT/Claude/Gemini browser service was exercised. This verifies the shared answer/import path, not provider performance or fully automatic semantic selection. Supernatural/health statements are the speaker's narrative claims, not verified facts.

## Editorial result

**One main narrative, eight candidate highlights, four selected for preview, four held for missing context or an unresolved ending.** These are not eight independent complete stories. Selection is a transcript-based editorial assessment and awaits listening/viewing approval.

The approximate target was three minutes. Retained selections range from 2:30 to 3:20; they were not cut mechanically at 180 seconds. Greetings, promotions, outro and unsupported factual commentary were excluded. The requirement is to select useful highlights, not cover every second.

| Candidate | Source seconds | Length | Transcript review |
|---|---:|---:|---|
| [Private work description removed] | 166.04–366.14 | 3:20 | Selected for preview: help leads to employment |
| [Private work description removed] | 455.44–630.25 | 2:55 | Held: opening refers to a previously introduced woman |
| [Private work description removed] | 759.13–931.95 | 2:53 | Held: symptom episode remains unresolved |
| [Private work description removed] | 1090.39–1258.79 | 2:48 | Selected for preview: call, visit, claimed improvement and warning |
| [Private work description removed] | 1260.01–1475.21 | 3:35 | Held: depends on prior warning and ends with unresolved threat |
| [Private work description removed] | 1580.89–1785.31 | 3:24 | Held: ritual/witness setup depends on earlier content |
| [Private work description removed] | 1790.06–1940.20 | 2:30 | Selected for preview: ritual episode has a stated outcome |
| [Private work description removed] | 2224.60–2400.66 | 2:56 | Selected for preview: return and revelation |

Only the four selected ranges were imported as the trial's story list. They have exact ASR row boundaries, valid source bounds, chronological order and no overlap. Row-boundary accuracy does not establish audible sentence boundaries or standalone comprehension.

## Checks actually run

- `python state/verify_story_split.py` — private trial driver; source paths and transcript deliberately excluded from Git.
- Driver invoked the real `scripts/web_ai.py import stories <private-video> <private-answer>` CLI. Imported sidecar matched all four authored selections.
- `video_edit.stories_from_agent(..., split_long=False)` retained their exact ranges/count without applying a rigid split.
- FastAPI `TestClient` mounted the actual video router and called `GET /api/video/story-plan` with an initially empty, isolated in-memory job store. It rehydrated from disk using the existing transcription cache and returned `done`, target `3`, and the same four stories. A second GET returned the same list. The production job store was not modified by this test.
- Import plus API readback took **14.348 seconds**. This excludes transcript reading/selection, initial ASR, rendering and human review; it is not an end-to-end speed benchmark.

This was a router-level integration test. The authenticated desktop/browser UI, app restart and local-agent polling were not exercised in this round.

## Invalid-answer safeguards: six failures

An isolated synthetic transcript had speech rows `[0,2]` and `[3,5]`. Each case went through `web_ai_json.parse` and `import_stories`; all should be rejected under the selection rules. No real project was used.

| Case | Payload ranges | Actual |
|---|---|---|
| Out of order | `[3,5]`, `[0,2]` | **Accepted — FAIL** |
| Duplicate | `[0,2]`, `[0,2]` | **Accepted — FAIL** |
| Overlap | `[0,5]`, `[3,5]` | **Accepted — FAIL** |
| Beyond source | `[0,50]` | **Accepted — FAIL** |
| Mid-phrase boundary | `[0.2,1.8]` | **Accepted — FAIL**: half-second tolerance accepts a partial speech row |
| Non-finite end | `[0,Infinity]` | **Accepted — FAIL**: Python JSON parser accepts the nonstandard token |
| Reversed range | `[2,0]` | Rejected — PASS |
| No speech | `[10,12]` | Rejected — PASS |

Root: `app/web_ai_json.py::import_stories` checks increasing endpoints and at least one speech row within a half-second tolerance, but does not enforce finite/source-bounded times, chronology, non-overlap or exact boundaries. `app/video_edit.py::stories_from_agent` also lacks cross-story checks and may snap imported endpoints. Prompts request those constraints; the importer does not guarantee them.

## Remaining acceptance

Repair the shared importer safeguards and verify all callers before trusting arbitrary AI answers. Preview/listen to the selected openings and endings, then get one pilot approved before a batch. This round neither rendered a new video nor checked silence-cut retiming, subtitle fit, emphasis sync, motion collisions or native CapCut playback. The earlier rejected pilot remains rejected; this report does not supersede it.

Machine-readable sanitized results: [story-split-20261014.json](story-split-20261014.json). Full source, transcript, prompt packet, answer and private paths remain local.
