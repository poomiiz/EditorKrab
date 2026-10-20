"""Transcribe one audio chunk the v0.29.1 way for the Rust app and write whisper.cpp-shaped full JSON.

faster-whisper large-v3 on CUDA (int8_float16), 15 s windows with 1 s overlap decoded 8 at a time, no
previous-text conditioning, words merged by time (video_edit.transcribe). Without a usable NVIDIA GPU it exits
non-zero so the Rust app reruns the chunk on its bundled whisper.cpp CPU build.

Usage: python asr_runner.py --wav AUDIO.wav --language th|en|auto --out OUT.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

LARGE_V3_VOCAB = 51866
# a faster-whisper large-v3 folder, or the hub name it downloads under
MODEL = os.environ.get("EDITORKRAB_FW_MODEL", "large-v3")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True)
    ap.add_argument("--language", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import video_edit

    if not video_edit._has_cuda():
        print("asr_runner: no CUDA device", file=sys.stderr)
        return 3
    with wave.open(args.wav) as w:
        duration = w.getnframes() / w.getframerate()
    language = args.language
    if language == "auto":
        model, engine = video_edit._get_model(MODEL)
        language = video_edit._detect_language(model, Path(args.wav), model_size=MODEL)
    # the Rust app's GPU check reads this line from stderr
    print("whisper_backend_init_gpu: using CUDA (faster-whisper large-v3 int8_float16)", file=sys.stderr, flush=True)
    phrases = video_edit.transcribe(args.wav, 0.0, duration, language=language, model_size=MODEL)

    segments, previous = [], 0
    for phrase in phrases:
        start = max(previous, round(phrase["start"] * 1000))
        end = round(phrase["end"] * 1000)
        text = phrase["text"].replace("\n", " ").strip()
        if end <= start or not text:
            continue
        tokens = []
        for a, b, word in phrase.get("words", []):
            a_ms = min(max(start, round(a * 1000)), end)
            tokens.append({"id": 0, "text": word, "p": 1.0,
                           "offsets": {"from": a_ms, "to": min(end, max(a_ms, round(b * 1000)))}})
        segments.append({"offsets": {"from": start, "to": end}, "text": text, "tokens": tokens})
        previous = end
    out = {
        "model": {"type": "large-v3", "multilingual": True, "vocab": LARGE_V3_VOCAB},
        "params": {"language": args.language, "translate": False},
        "result": {"language": language},
        "transcription": segments,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
