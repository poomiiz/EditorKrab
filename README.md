# EditorKrab

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo-dark.png">
  <img src="docs/brand/logo.png" alt="EditorKrab" width="460">
</picture>

EditorKrab is a desktop app that turns talking-head footage into short clips with subtitles. It runs on your own computer: videos, transcripts and projects stay on your machine.

This is an early access project for Windows 10/11. The app currently works through step 5. Step 6, which produces the finished clip, is not enabled yet.

[คู่มือภาษาไทย](docs/README.th.md) · [Report a bug](CONTRIBUTING.md) · [Security](SECURITY.md)

## How it works

The app has one window and six steps, with one main button per step.

1. **Add video:** pick a file or a whole folder. Press **Transcribe** to turn speech into text, then listen and fix any wrong words. Thai, English and other languages are detected automatically.
2. **Send to AI:** download one file and attach it in any AI chat (ChatGPT, Gemini or Claude). The AI picks the parts to keep.
3. **Paste the answer:** paste the AI's whole reply back into the app.
4. **Subtitles:** choose what appears on screen. Drag to move a subtitle and drag a corner to resize it.
5. **Title:** add title text and place it on the clip.
6. **Get the clip:** not enabled yet.

The work saves itself as you go. **Undo**, **Redo**, **Open** and **Download backup** are in the top bar.

## Install

1. Download `EditorKrab-windows-x64.zip` from [Releases](https://github.com/poomiiz/EditorKrab/releases).
2. Unzip it and double-click **Install EditorKrab**. The installer copies the app to `%LOCALAPPDATA%\Programs\EditorKrab` and adds Desktop and Start Menu shortcuts.
3. Open **EditorKrab**. It opens in its own window using Microsoft Edge, which comes with Windows. Google Chrome also works.

The download includes ffmpeg. The speech engine ([whisper.cpp](https://github.com/ggml-org/whisper.cpp)) and the Whisper large-v3 model are not included. The first time you transcribe, the app shows one button: **ดาวน์โหลดตัวถอดเสียง (3.1 GB)**.
- The download shows progress, can be cancelled and resumes if the connection drops.
- Each file is checked against a pinned checksum before it is used.
- If there is not enough disk space, the app says how much more it needs.

To update, download the new zip and run **Install EditorKrab** again. It replaces only the app files, so your projects and the speech model are kept.

## Measured results

These numbers come from our own test runs. They are not guarantees.

| Test | Result | Source |
|---|---|---|
| English test speech, 68.6 s, large-v3 | 6.74% word error rate | [flow-run3](docs/test-logs/rust-20261018/flow-run3.md) |
| Thai test speech, 45.1 s, large-v3 | 2.05% character error rate | [flow-run3](docs/test-logs/rust-20261018/flow-run3.md) |
| Transcription speed, 2 CPU threads | about 5x slower than real time | [flow-run3](docs/test-logs/rust-20261018/flow-run3.md) |

The test speech was generated from the scripts in that folder; it is not customer footage. The app now uses up to 8 CPU threads, and the speed with 8 threads has not been measured yet. Time saved compared with manual editing has not been measured.

## Build from source

You need [Rust](https://rustup.rs) (stable) on Windows.

```powershell
git clone https://github.com/poomiiz/EditorKrab.git
cd EditorKrab
cargo build --manifest-path rust/Cargo.toml --release --locked
cargo test --manifest-path rust/Cargo.toml --locked
```

The page and its scripts in `design/rust-flow/` are compiled into the exe. To build the same zip as a release, run `scripts/package-windows-app.ps1 -Exe rust/target/release/editorkrab.exe -Output dist/EditorKrab-windows-x64.zip`. The **release** workflow builds it on every `v*` tag. You can also run it by hand to get the zip as a workflow artifact.

## Privacy

The app serves its page only on `127.0.0.1:8871`, so nothing is reachable from other computers. Only two things leave your machine:
- The one-time speech model download.
- The file you attach to an AI chat yourself in step 2.

Keep client media, private projects and personal `config.json` files out of Git.

## Older Python workflow

The repository still contains the earlier Python tools: `app/`, `scripts/run_clip.py`, the CapCut drafts and the agent skills in `skills/`. These tools are not the desktop app described above. See the [documentation index](docs/index.md) for them. The agent plugin ID remains `clip-kit` for existing users.

## Contributing and license

[Contributing](CONTRIBUTING.md) explains bug reports and pull requests. [Security](SECURITY.md) explains private vulnerability reporting. [Sponsor the project](https://github.com/sponsors/poomiiz) to support development.

[MIT license](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md) for ffmpeg, whisper.cpp and other components.
