> **History.** Written for the Python-era build, kept as a record. The Python app was removed on 25 Oct 2026; EditorKrab is now the Rust app only, so commands here that use Python no longer exist. / เอกสารเก่าของรุ่น Python เก็บไว้เป็นบันทึก แอป Python ถูกลบเมื่อ 25 ต.ค. 2026

# Mac and Update checks

Checked on 13 October 2026. This records software evidence; it does not certify a CapCut project on a Mac.

## Published version 0.29.0

- [Release](https://github.com/poomiiz/EditorKrab/releases/tag/v0.29.0): commit `aee73656f608ee78d0ef2049529f220f4c50a970`.
- [PR checks](https://github.com/poomiiz/EditorKrab/actions/runs/38377507826) and [main checks](https://github.com/poomiiz/EditorKrab/actions/runs/38378233631): passed at that commit.
- Intel (`macos-15-intel`) and Apple Silicon (`macos-15`) runners installed the tools, configured a temporary workspace, ran CPU transcription on a generated tone, and passed the Python/page checks. The tone checks that the speech engine runs; it cannot establish speech accuracy.
- Native Mac server checks covered start, readiness, reopening without duplication, login-page/CSP transport, restart and graceful shutdown on an isolated port.
- The nine original security fixes required when v0.29.0 was published existed and were ancestors of the release commit. The [inventory](../release-inventory.json) also lists the later Mac preview guard required by the next patch.
- A disposable Windows clone updated from v0.28.1 to this exact commit using the old updater. Tracked edits were stashed, a local commit remained on a backup branch, dependencies were updated, and a second Update reported the newest version. The benchmark returned `UNKNOWN` with no human trials.

## Patch work

Version 0.29.1 fixes a missing-font case in CapCut titles as well as subtitles. Six regression checks pass locally, including the public templates, installed fonts, quoted text and missing bundled fonts. The old implementation failed four of those cases. A fresh Windows CI run exposed an import that required personal config; the test now uses its own temporary config from the start.

Mac preview and render use a private, checksum-checked HyperFrames 0.8.101 cache with its global orphan-Chrome cleanup disabled, including the render worker. The original CLI defaults and Node version check are retained. Preview runs in the foreground with the normal media proxy; EditorKrab stops only its recorded preview tree. This patches a pinned internal module, so a HyperFrames version change requires reviewing the launcher. Platform regressions pass locally, with the native AppleScript compiler skipped on Windows.

The promo MP4 and GIF now describe an editable draft, human review and export. They are labelled a workflow illustration, with Mac setup experimental. The voice-over script matches the captions. Rendered scenes were reviewed; the video is silent and is not an editing-speed demonstration.

The [PR #100 checks](https://github.com/poomiiz/EditorKrab/pull/100/checks) exercise real font discovery, a Thai ASS subtitle frame, an isolated preview's HTTP output, process cleanup and a generated motion MP4 on both Mac architectures. They upload a JSON report, PNG frames and the sample MP4. These checks cover generated media and native processes, not CapCut editing or speech quality.

After publication, run the `tests` workflow manually with `expected_release=v0.29.1`. It updates a disposable clone from v0.29.0 using its actual updater and verifies the published commit, version/files, stashed tracked edits, backed-up local commits, retained private config and repeat Update. Packages are unchanged in this patch. The [Release notes](https://github.com/poomiiz/EditorKrab/releases/tag/v0.29.1) must link the finished release/CI/Update results; check those before claiming delivery.

## Still needed

The owner has no Mac available for an interactive trial. These checks remain open:

- Select and cancel files/folders in the native dialogs; move a disposable item to Trash and confirm its recovery.
- Use real speech, inspect Thai subtitles and sound, then watch the exported MP4.
- Open a generated project in Mac CapCut, edit it, save it, reopen it and export it. Record the Mac model, macOS and CapCut versions.
- Try Google sign-in and any team workflow with the operator's actual services.

Follow [the Mac guide](../macos.md). A passing CI run cannot close the CapCut or human-review items. Real editing-time savings still need the [paired benchmark](../benchmark.md).
