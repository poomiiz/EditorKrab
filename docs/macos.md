# Mac setup for the older Python workflow

> **Not for the desktop app.** The EditorKrab desktop app described in the [README](../README.md) supports Windows only. This page covers the older Python tools, kept for existing users. It is unmaintained and has not been tested on real Mac hardware.

The Mac setup path prepares a local Python environment and starts the older Python app in your browser. Transcription uses the CPU. Apple GPU acceleration is not implemented. Installation checks or CI results do not prove that a CapCut draft opens correctly on a Mac.

## Before you start

You need Git, Python 3.12, ffmpeg and Node.js, which provides `npx`. Install Homebrew if you want setup to install missing tools for you. Install CapCut desktop if you want to continue editing a CapCut draft.

The setup script checks the prerequisites and uses Homebrew to install missing tools. It selects an ffmpeg build that can render subtitles and H.264 video. It does not install Homebrew itself. If you prefer to install the tools first:

```bash
brew install python@3.12 ffmpeg-full node@22
```

Keep footage and projects in a workspace folder you control. Allow enough space for the speech model, source media and exported videos.

## Install and open

```bash
git clone https://github.com/poomiiz/EditorKrab.git
cd EditorKrab
bash scripts/setup.sh "$HOME/EditorKrab Workspace"
./start.command
```

Run these from the repository folder. Setup creates `.venv` and your local `config.json`. The launcher starts the local server and opens your browser. The app asks you to sign in with Google before opening the editing pages; sign-in still needs testing with real accounts.

Machine paths come from `config.json`. Use your own paths for footage, fonts, drafts and exports. Do not copy Windows drive paths into the Mac configuration.

For HyperFrames, use the app's preview/export actions. When working from a motion project folder in Terminal, run preview or render through `scripts/hf.py` with your EditorKrab Python environment. This applies the same process guard as the app.

## Check the installation

```bash
.venv/bin/python scripts/doctor.py
.venv/bin/python scripts/doctor.py --asr
```

Fresh setup selects the medium Whisper model and CPU; an existing model setting is kept. Setup downloads the configured model and runs a speech check by default. Keep its model and device settings in any benchmark record. A CPU run with a different model cannot establish the speed of a Windows CUDA run.

Start with one short:

```bash
.venv/bin/python scripts/run_clip.py "<video>" --max 60 --capcut
```

If the script prints `WAIT`, complete the requested AI step and run the command again. For a benchmark, supply a unique `--trial-id` and reuse it for retries within that editing session.

## CapCut and fonts

Choose the actual drafts location for your installed CapCut version in Settings. Draft formats and folder locations can differ between versions. Close CapCut and back up `draft_content.json` before editing an existing draft through a tool.

Use fonts installed on the Mac and permitted by their license. Windows font names and paths may not exist on your machine.

Review a generated draft in the Mac version of CapCut before treating it as compatible. If CapCut reports an unsupported or damaged project, keep the original footage and report the CapCut version and error without sharing private project files publicly.

## Update

Use Update in the app, or:

```bash
.venv/bin/python scripts/update.py
```

The updater follows published release tags and preserves tracked edits and local commits before moving to the release. It updates this repository clone. The new code may require restarting the app.

## What still needs checking on a Mac

- Clean installation and launch on the intended Intel or Apple Silicon device.
- A transcription run, an exported MP4 and a human review of the picture, sound and subtitles.
- A generated draft opened and edited in the installed Mac CapCut version.
- File dialogs, fonts, Trash and local process cleanup on that device.
- Google sign-in and any team synchronization on the operator's actual services.

Record the Mac model, macOS version, CapCut version and EditorKrab revision when reporting results. Until those checks are complete, describe this setup as experimental.
