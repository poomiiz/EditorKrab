# Third-party notices

## HyperFrames (HeyGen) — Apache License 2.0

- Source: https://github.com/heygen-com/hyperframes (npm package `hyperframes`, version 0.8.101)
- Licence: Apache License 2.0 — https://www.apache.org/licenses/LICENSE-2.0
- Used by EditorKrab:
  - `app/static/hyperframes-player.js` — the `<hyperframes-player>` web component, copied unchanged from
    `hyperframes/dist/hyperframes-player.global.js`.
  - `app/hyperframe.runtime.iife.js` — the HyperFrames runtime, copied unchanged from `hyperframes/dist/`.
  - The `hyperframes` command line (installed from npm at run time) renders the editor's compositions to MP4.
- EditorKrab is not affiliated with HeyGen; "HyperFrames" and "HeyGen" are their names, not EditorKrab's.

## Kanit (Cadson Demak) — SIL Open Font License 1.1

- `fonts/Kanit-Bold.ttf`, licence text in `fonts/OFL-Kanit.txt`.

## Twemoji (Twitter / jdecked) — CC-BY 4.0

- `emoji/*.png` — 23 emoji pictures rasterised to 160 px from the npm package `@twemoji/svg` 15.0.0
  (https://github.com/jdecked/twemoji). Graphics licensed under CC-BY 4.0
  (https://creativecommons.org/licenses/by/4.0/). Copyright Twitter, Inc and other contributors.

## FFmpeg (Gyan.dev essentials build 7.1) — GPL 3.0

- The Windows download (`scripts/package-windows-app.ps1`) bundles `tools/ffmpeg/ffmpeg.exe` and `ffprobe.exe`
  unchanged from https://github.com/GyanD/codexffmpeg/releases/tag/7.1, with that build's `LICENSE` beside them.
  EditorKrab runs them as separate programs. Source: https://ffmpeg.org/download.html

## whisper.cpp and the Whisper large-v3 model — MIT

- Not bundled. On first use the app downloads `whisper-bin-x64.zip` (v1.9.2) from
  https://github.com/ggml-org/whisper.cpp and `ggml-large-v3.bin` from https://huggingface.co/ggerganov/whisper.cpp.
