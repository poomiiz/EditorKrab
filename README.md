# EditorKrab

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo-dark.png">
  <img src="docs/brand/logo.png" alt="EditorKrab" width="460">
</picture>

EditorKrab helps turn talking-head footage into short videos. It prepares a draft with story cuts, subtitles and a cover, so you can review and finish the edit in CapCut or the local editor.

This is an early access project. Windows uses the existing installer. Mac support is experimental, and CapCut projects still need testing on real Macs. These instructions cover version 0.29.1. Check [Releases](https://github.com/poomiiz/EditorKrab/releases) for availability.

[คู่มือภาษาไทย](docs/README.th.md) · [Documentation](docs/index.md) · [Report a bug](CONTRIBUTING.md)

## What it does

- Transcribes speech and prepares editable subtitles, with Thai layouts and support for other spoken languages.
- Cuts stories from longer footage and removes pauses.
- Adds subtitle styles, emphasis, covers and optional stock footage.
- Creates CapCut drafts or a HyperFrames preview, then exports an MP4.
- Records how long each step takes and includes a guide for comparing editing time. Real time savings have not been measured yet.

AI story selection and emphasis can require help from Claude Code, Codex or a web AI. If the script prints `WAIT`, complete that step and run the same command again. Review the draft before export and watch the exported video before delivery.

## Install

You need Git and a workspace folder for your footage, models, drafts and exports. Install CapCut desktop separately if you want to finish your draft there. Machine paths are stored in the local `config.json`.

### Windows

Windows 10/11. The setup script installs Python, ffmpeg, Node.js and the Python packages through the existing Windows setup flow.

```powershell
git clone https://github.com/poomiiz/EditorKrab.git
cd EditorKrab
.\setup.cmd
```

Enter a workspace folder when prompted. Open EditorKrab from its desktop shortcut after setup, or run `powershell -NoProfile -ExecutionPolicy Bypass -File app\start.ps1`. An NVIDIA GPU can run transcription with CUDA; setup uses CPU when no NVIDIA GPU is detected.

### Mac — experimental

Install Homebrew first, or provide Python 3.12, ffmpeg and Node.js yourself. Setup checks the tools, installs missing ones through Homebrew and prepares a local Python environment.

```bash
git clone https://github.com/poomiiz/EditorKrab.git
cd EditorKrab
bash scripts/setup.sh "$HOME/EditorKrab Workspace"
./start.command
```

Fresh Mac setups use the medium Whisper model on the CPU. Apple GPU acceleration is not implemented. See the [Mac guide](docs/macos.md) for prerequisites, settings and the checks still needed on real devices.

### AI agent tools

These integrations are optional ways to guide the workflow. The plugin name remains `clip-kit` for existing users.

Claude Code:

```text
claude plugin marketplace add poomiiz/EditorKrab
claude plugin install clip-kit@clip-kit
```

Codex:

```text
npx skills add poomiiz/EditorKrab --agent codex -g
```

Start an editing request with [make-clip](skills/make-clip/SKILL.md).

## Update

Use **Update** in the app, or run `python scripts/update.py` from the Windows clone. On Mac, use `.venv/bin/python scripts/update.py`.

Updates follow the newest published `vX.Y.Z` tag. A merge to the main branch becomes available through Update after a release tag is published. The updater saves tracked local edits in a Git stash and local commits on a backup branch before moving to the release.

Agent integrations have their own update step: `claude plugin marketplace update clip-kit`, or run the Codex install command again.

## Make your first clip

1. Select a source video and set the spoken language, shape and subtitle preset.
2. Prepare one story as a pilot. Finish any AI steps requested by the workflow.
3. Review the cut, subtitles, framing, sound and cover. Continue editing in CapCut if needed.
4. Export an MP4 and watch it through. Use the [quality checklist](skills/clip-qc/SKILL.md) before making more clips.

For the CLI pilot:

```text
python scripts/run_clip.py "<video>" --max 60 --capcut
```

On Mac, replace `python` with `.venv/bin/python`. Close CapCut and back up `draft_content.json` before a tool changes an existing draft.

Free stock footage can come from Pixabay after you add an API key in Settings. You can also insert files downloaded from a library you have permission to use.

## Measure editing time

The benchmark compares manual CapCut editing with an EditorKrab draft followed by human editing. Both methods must deliver the same quality.

The initial pilot uses eight source videos: an A/B pair at each of 1, 5, 20 and 60 minutes. Two editors complete sixteen sessions, producing one 30–60 second short per session. Each editor sees a source only once. Record active human time and observed elapsed time separately; keep failed sessions in the sheet.

Use the [benchmark protocol](docs/benchmark.md) and [result sheet](docs/benchmark-sheet.csv). Retries within the same editing session share a `--trial-id` and stay in the timing log. This measures one short per source, so it does not establish long-form or batch performance.

## Files, privacy and teams

Footage and project folders are stored on your machine. Keep client media, private projects, credentials and personal `config.json` files out of Git.

Some optional steps send information to other services: web AI receives the material you export or upload, and team accounts store metadata in Supabase. Drive upload does not grant public link access; add chosen reviewers in Google Drive. Existing public links are not revoked automatically. Report a problem opens GitHub for you to review and submit; automatic error reporting is disabled, including for old configurations. No logs or transcripts are attached automatically.

The app uses Google sign-in before opening the editing pages. Sign-in, team permissions and synchronization still need testing with real accounts and devices. If you operate your own service, configure Supabase and Google OAuth before distributing it. Team synchronization uses Drive for desktop or Syncthing for footage and exports; private project folders stay with their owner. See [team setup](docs/team-setup.md) (Thai).

## Documentation and contributions

Start with the [documentation index](docs/index.md). [Contributing](CONTRIBUTING.md) explains bug reports and pull requests; [Security](SECURITY.md) explains private vulnerability reporting.

[Sponsor the project](https://github.com/sponsors/poomiiz) to support development.

EditorKrab was previously called ClipKit. Existing plugin IDs and file-format names remain compatible.

[MIT license](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md) for separate licenses.
