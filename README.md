# EditorKrab

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo-dark.png">
  <img src="docs/brand/logo.png" alt="EditorKrab" width="460">
</picture>

EditorKrab is a desktop app that turns talking-head footage into short clips with subtitles. It runs on your own computer: videos, transcripts and projects stay on your machine.

This is an early access project for **Windows 10/11 only**. Mac and Linux are not supported: the desktop app has not been tested on them. All five steps work. Keyword highlights and English subtitles are in (one extra AI paste in step 4). Music and sound effects are not in the app yet. See [Known limits](#known-limits) before you rely on it.

[คู่มือภาษาไทย](docs/README.th.md) · [Report a bug](CONTRIBUTING.md) · [Security](SECURITY.md)

## How it works

The app has one window and five steps, with one main button per step.

1. **Add video:** pick a file or a whole folder. Press **Transcribe** to turn speech into text, then listen and fix any wrong words. Thai, English and other languages are detected automatically.
2. **Summarise:** download one file and attach it in any AI chat (ChatGPT, Gemini or Claude), or press **คัดลอกให้ AI** and paste the same content straight into the chat. Then paste the AI's whole reply back into the app. The AI picks the stories to keep.
3. **Edit:** adjust the cuts and the words of each story. Pauses are cut for you and you can put any cut back.
4. **Check subtitles:** pick a subtitle look, then drag a subtitle to move it and drag a corner to resize it. One more trip to the AI chat adds keyword highlights and English subtitles.
5. **Get the clip:** check the list, then press **Create clip**. The app makes one vertical 9:16 MP4 per story, with the subtitles burned in, and saves them in `Videos\EditorKrab`. It never overwrites an existing file. Each file is checked for size, sound and length before it is saved. You can also export each story as a CapCut project (CapCut must be installed).

The work saves itself as you go. **Undo**, **Redo**, **Open** and **Download backup** are in the top bar.

## Use from an AI agent (MCP)

While the app is open, an agent such as Claude Code, Claude Desktop or Codex can run the same steps through the [Model Context Protocol](https://modelcontextprotocol.io). The tools are `import_videos`, `transcribe`, `get_project`, `get_transcript`, `set_clips` and `export_clips`. Each answer includes an `open_in_app` link, so you can open the project the agent is working on and adjust it by hand.

- **HTTP:** point the agent at `http://127.0.0.1:8871/mcp`.
- **stdio:** for agents that only start a command, run the app's exe with `--mcp`. It relays to the open app, and says so when the app is not running. For example, in Claude Code:

```powershell
claude mcp add editorkrab -- "$env:LOCALAPPDATA\Programs\EditorKrab\EditorKrab.exe" --mcp
```

The agent reads the transcript text and file names to choose clips, so that text goes to whichever AI service the agent uses.

## Install

1. Download `EditorKrab-windows-x64.zip` from [Releases](https://github.com/poomiiz/EditorKrab/releases).
2. Unzip it and double-click **Install EditorKrab**. The installer copies the app to `%LOCALAPPDATA%\Programs\EditorKrab` and adds Desktop and Start Menu shortcuts.
3. Open **EditorKrab**. It opens in its own window using Microsoft Edge, which comes with Windows. Google Chrome also works.

The download includes ffmpeg. The speech engine ([whisper.cpp](https://github.com/ggml-org/whisper.cpp)) and the Whisper large-v3 model are not included. The first time you transcribe, the app shows one button: **ดาวน์โหลดตัวถอดเสียง (3.1 GB)**. On a computer with an NVIDIA graphics card it also offers a GPU engine (670 MB).
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
| Transcribe the English clip, 8 CPU threads (release build) | 94.1 s (was 332.6 s with 2 threads) | [flow-run3, run 4](docs/test-logs/rust-20261018/flow-run3.md) |
| Transcribe the Thai clip, 8 CPU threads (release build) | 128.4 s (was 543.1 s with 2 threads) | [flow-run3, run 4](docs/test-logs/rust-20261018/flow-run3.md) |
| Transcribe a 15.8-minute Thai phone video on the graphics card (GeForce RTX 3070 Ti Laptop, GPU engine the app downloads, first video after launch) | 351.5 s and 358.9 s in two runs | [transcribe-and-export](docs/test-logs/rust-20261025/transcribe-and-export.md) |
| Export 173 s of 4K phone video as 9:16 clips with subtitles (same PC) | 76.7 s, one run | [transcribe-and-export](docs/test-logs/rust-20261025/transcribe-and-export.md) |

The test speech was generated from the scripts in that folder; it is not customer footage. The first two rows and the two CPU rows were measured on one 20-thread desktop CPU, with whisper running on the CPU only; the app uses up to 8 threads because more threads gave little extra speed. The last two rows were measured on a laptop with a Core i9-12900H and a GeForce RTX 3070 Ti, transcribing on the graphics card. Time saved compared with manual editing has not been measured by us. The editor who built the tool reports going from 2 to 4-5 short clips a day with the earlier prototype (ClipKit); that is their own report, not a controlled test, and not yet repeated with this app.

## Known limits

- **Windows only.** There is no Mac or Linux build.
- **Fast only with an NVIDIA card.** With one, the app offers a one-time 670 MB download so it can transcribe on the graphics card, and it also decodes 4K phone video there when exporting. Without one, transcription runs on the CPU and is slower (a 75-second Thai clip took 221 s on our test CPU).
- **One model.** The app uses Whisper large-v3 and nothing else.
- **Language choice.** The menu offers only Automatic, Thai and English. Other languages rely on automatic detection, which got Urdu (detected as Hindi) and Maltese (detected as Arabic) wrong in our test.
- **Ten languages fail to transcribe:** Hindi, Bengali, Tamil, Telugu, Kannada, Marathi, Gujarati, Punjabi, Swahili and Armenian. Whisper repeats one sentence with zero-length timings, and the app rejects the chunk. A retry with different settings was tried and removed because it made Thai worse, so transcription stays a single pass.
- **Poor accuracy** in Malayalam, Nepali, Azerbaijani and Hebrew (more than 50% wrong in our test).

### 60-language test

We ran the real app through every step of the flow it had then (six steps; the title step has since been removed) on [Google FLEURS](https://huggingface.co/datasets/google/fleurs) read speech (CC BY 4.0), about 75 seconds per language. Thai, Chinese, Japanese and Korean are scored by character error rate, the rest by word error rate.

| Result | Languages |
|---|---|
| Every step and the export passed | 50 of 60 |
| Error rate 15% or lower | 24 (best: Chinese 1.2%, German 3.7%, Polish 3.9%, Finnish 5.5%, Slovak 5.8%) |
| Error rate 15–40% | 17 |
| Error rate above 40% | 8 |
| Transcription failed | 10 (listed above) |

About 75 seconds of audio per language is a small sample, so do not compare languages that are a few points apart. These runs used a test build with GPU transcription to save time. On the three languages run both ways, the CPU gave the same error rate for English and Japanese, and 10.6% versus 6.8% for Thai.

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

## Contributing and license

[Contributing](CONTRIBUTING.md) explains bug reports and pull requests. [Security](SECURITY.md) explains private vulnerability reporting. [Sponsor the project](https://github.com/sponsors/poomiiz) to support development.

[MIT license](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md) for ffmpeg, whisper.cpp and other components.
