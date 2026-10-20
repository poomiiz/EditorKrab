# Documentation

Start with the [README](../README.md) for the Windows desktop app, or the [Thai guide](README.th.md) for the same steps in Thai.

Most pages below describe the **older Python workflow** (`app/`, `scripts/`, `skills/`), not the desktop app. They are kept for existing users.

## Set up and update

| Guide | What it covers |
|---|---|
| [Mac setup](macos.md) | Older Python workflow only; the desktop app is Windows only |
| [Mac and Update checks](test-logs/macos-support.md) | Results and the CapCut checks still needed |
| [Team setup](team-setup.md) (Thai) | Account configuration, ownership, MP4 review and external synchronization |
| [Setup settings](../config.example.json) | The settings copied into your private config.json |
| [Machine check](../scripts/doctor.py) | Python packages, ffmpeg, Node.js and optional speech-model checks |

Check [Releases](https://github.com/poomiiz/EditorKrab/releases) for the current desktop app download.

## Make and review a clip

| Guide | What it covers |
|---|---|
| [Make-clip entry point](../skills/make-clip/SKILL.md) | The agent workflow, starting with one pilot |
| [Clip workflow](../skills/clip-workflow/SKILL.md) | Steps from source footage to finished video |
| [Quality checklist](../skills/clip-qc/SKILL.md) | Checks before delivery |
| [Subtitle presets](../presets/) | Styles you can choose or adapt |
| [Editing techniques](../reference/tricks.md) | When to use the available effects |

A generated draft still needs an editor to review the subtitles, cut, framing and sound. Watch the MP4 after export.

## Measure time

The [benchmark protocol](benchmark.md) (Thai) and [CSV sheet](benchmark-sheet.csv) define a pilot with eight sources and two editors. Each source is tested in both modes by different editors. Each session produces one short, and both modes must pass the same quality checks.

Measure two things: the time the editor actively works, and the elapsed time from starting the task to a reviewed MP4. The log shows how long each step takes; it cannot replace the elapsed timer. Failed sessions remain in the results. No real saving percentage is available until the human trial is complete.

[Benchmark instrumentation evidence](test-logs/benchmark-instrumentation-pr98.md) records software checks. It is separate from the human editing trial.

[Desktop media and motion evidence](test-logs/desktop-test-20261014.md) records four real-source attempts, motion MOV exports, filename overflow checks, checkpoint fixes, and the failed editorial pilot.

[Reporting and Drive privacy checks](test-logs/privacy-20261014.md) cover manual GitHub reports, restricted uploads, browser-AI download/import, and mobile layout verification.

## Share or contribute

| Resource | Purpose |
|---|---|
| [Launch kit](launch.md) | Release status, existing assets and launch checks |
| [Promotion drafts](promotion.md) | English/Thai copy and a 30-second demo plan; nothing has been posted |
| [Contributing](../CONTRIBUTING.md) | Bug reports, fixes and presets |
| [Security policy](../SECURITY.md) | Private vulnerability reporting |
| [License](../LICENSE) / [third-party notices](../THIRD_PARTY_NOTICES.md) | Usage and attribution terms |
