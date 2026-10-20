# ek-asr-ct2: GPU transcription sidecar

A small program the app keeps running while it transcribes on an NVIDIA GPU. It loads Whisper large-v3 once
through CTranslate2 (the engine v0.29.1 used through faster-whisper) and answers one request per chunk, decoding
15-second windows eight at a time. The app starts it when a video is imported, stops it after 15 idle minutes,
and falls back to whisper.cpp (GPU, then CPU) whenever it is missing or fails.

## What the app looks for

| | Default location | Override |
|---|---|---|
| Sidecar | `tools/asr-ct2/ek-asr-ct2.exe` beside the app, or `asr-ct2/` in the data folder | `EDITORKRAB_CT2` |
| CUDA DLLs | beside the sidecar: `cudart64_12.dll`, `cublas64_12.dll`, `cublasLt64_12.dll`, `cudnn*64_9.dll` | |
| Model | `models/ct2-large-v3/` (CTranslate2 conversion of large-v3: `model.bin`, `config.json`, `tokenizer.json`, `preprocessor_config.json`, `vocabulary.json`) | `EDITORKRAB_CT2_MODEL` |

"CPU only" in Settings turns the sidecar off.

## Build (Windows)

Needs the MSVC build tools, CMake, the CUDA 12.4 toolkit and cuDNN 9. It is built apart from the app (its own
`[workspace]`), so the app's CI does not need CUDA.

```bat
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
set CUDA_PATH=C:/path/to/cuda-12.4
set CUDA_TOOLKIT_ROOT_DIR=%CUDA_PATH%
set CUDA_ARCH_LIST=8.6
rem cc/cmake drop /O2 from CXXFLAGS; without it CTranslate2 is built unoptimized and runs ~15x slower
set CL=/O2
set CXXFLAGS=/DNDEBUG
set RUSTFLAGS=-C target-feature=+crt-static
cargo build --release
```

Set `CUDA_ARCH_LIST` to the cards you ship for (8.6 is RTX 30xx). The CUDA path must use forward slashes.

## Protocol

`ek-asr-ct2 <model_dir>` prints `{"ready":true,"load_ms":N}` (or `{"ready":false,"error":"..."}` and exits),
then reads one JSON request per line on stdin and answers one line each, until stdin closes:

```
in:  {"wav":"chunk.wav","language":"auto","decode":"th","out":"chunk.json"}
out: {"ok":true,"transcribe_ms":8123,"words":412}
```

`language` is what the job asked for (the app detects the language with whisper.cpp first when it is `auto`);
`decode` is the language to decode with. The output file is whisper.cpp-shaped full JSON.
