> **History.** Written for the Python-era build, kept as a record. The Python app was removed on 25 Oct 2026; EditorKrab is now the Rust app only, so commands here that use Python no longer exist. / เอกสารเก่าของรุ่น Python เก็บไว้เป็นบันทึก แอป Python ถูกลบเมื่อ 25 ต.ค. 2026

# Benchmark instrumentation verification — 13 October 2026

Scope: measurement reliability for the short-clip pilot in PR #98. No human editing trial or real time-saving percentage is claimed here.

## Changes

- `--trial-id` associates retries with one human session; each invocation has a unique `run_id` and a hash of the resolved source path. Basenames alone never join machine timing to a sheet row.
- Schema 2 records invocation start/end, exit status and nested diagnostic steps. Pipeline minutes sum complete invocation totals, including waiting/error attempts before a successful retry. Nested steps are not added again.
- Export, focus detection, draft/subtitle operations, inserts, pace/safe-zone checks and the summary page are timed. Import/startup time and external AI waiting are captured by the human's elapsed timer, rather than presented as automatically measured pipeline time.
- Reports use observed `elapsed_min` for both modes. Complete A/B pairs, opposite mode order, unique editor/source sessions and equal 30–60 second outputs are required.
- Failed QC rows remain visible and suppress the group's saving percentages. Legacy timing records are reported and ignored because their trials cannot be identified.

## Local verification

| Check | Result |
|---|---|
| `python scripts/benchmark_test.py` | 14 regression tests passed |
| `python scripts/smoke_test.py` | All checks passed, including the benchmark regressions |
| `python scripts/bench.py docs/benchmark-sheet.csv` | `UNKNOWN: no human trials recorded yet.` |
| `git diff --check` | Passed |

Regression cases cover same-named sources in different directories, historical trial exclusion, retry cost, nested steps, failed attempts, incomplete/duplicate/out-of-order events, malformed exit codes, the observed elapsed-time formula, QC failure retention, invalid numeric/CSV input, unequal/out-of-scope outputs, repeated source exposure, zero assisted human time and an empty sheet. A stubbed CLI invocation verifies WAIT then retry without opening media or CapCut.

GitHub CI evidence is separate from these local results; see the checks on PR #98 at its current revision.

The first CI run exposed a test assertion that compared a Windows 8.3 temporary path with its expanded spelling. The assertion now checks actual file identity with `samefile()`. Pipeline source resolution was already correct.

Standalone CLI adapter checks also use a temporary `CLIP_KIT_CONFIG` fixture so a fresh Windows runner does not need a personal configuration. All media operations remain stubbed.

## Remaining acceptance

Choose eight source files (A/B at each of 1/5/20/60 minutes), then run sixteen sessions with two editors. Keep the agreed brief, hardware, software revision, permitted CapCut tools and cache conditions in the human test log. Real savings, live CapCut compatibility and long-form/batch performance remain unmeasured.
