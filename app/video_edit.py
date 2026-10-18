"""Video editing helpers: probe a folder, find pauses, transcribe Thai speech,
and write a CapCut draft that is ready to open.

The CapCut writer works by cloning a known-good draft folder and replacing its
timeline, so every side file CapCut expects (cover, settings, resource folders)
stays valid.
"""
from __future__ import annotations

import atomic
import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "scripts" / "capcut"))
import kitconfig  # noqa: E402

US = 1_000_000
VIDEO_SUFFIXES = {".mov", ".mp4", ".mkv", ".avi", ".m4v", ".mts", ".webm"}

FFMPEG = os.environ.get("FFMPEG_BIN") or kitconfig.FFMPEG
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
# pythonw has no console: without this each ffprobe/ffmpeg spawn opens its own console window.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
def capcut_drafts_root() -> str:
    """CapCut drafts folder from config; raises until it is set in Settings (never falls back to the cwd)."""
    return kitconfig.DRAFTS
CAPCUT_TEMPLATE_DRAFT = os.environ.get("CAPCUT_TEMPLATE_DRAFT", "")

# Measured on this machine over 41s of Thai speech (RTX 3070 Ti):
#   large-v3 cuda/float16   52s, cleanest Thai      <- default
#   large-v3 cuda/int8_f16  72s, garbled, unusable
#   medium   cpu/int8      233s, drops whole spans
# So: best model on the GPU, and only fall back to CPU if CUDA is missing.
WHISPER_MODEL = os.environ.get("VIDEO_WHISPER_MODEL", "large-v3")
# Settings picks the model (config.json whisper_model). 9 Oct 2026, 180 s of Thai, RTX 3070 Ti, 15 s windows:
#   large-v3 121 s, large-v3-turbo 26 s with slightly more misspellings
WHISPER_CPU_FALLBACK = os.environ.get("VIDEO_WHISPER_CPU_MODEL", "medium")


# languages written without spaces between words: every recogniser word piece may start a line
NO_SPACES = {"ja", "zh", "yue", "lo", "km", "my", "bo"}
PHRASE_PARSERS = {"ja": "load_default_japanese_parser", "zh": "load_default_simplified_chinese_parser",
                  "yue": "load_default_traditional_chinese_parser"}


def speech_language() -> str:
    """The language people speak in the clips: config.json "speech_language", a Whisper code ("th", "en", ...)
    or "auto" = the recogniser decides once per file. Automatic unless set."""
    return kitconfig._load().get("speech_language") or "auto"


def whisper_model() -> str:
    """The model chosen in Settings, re-read on each call so a change applies to the next transcription."""
    return kitconfig._load().get("whisper_model") or WHISPER_MODEL


_model = None


class VideoEditError(RuntimeError):
    """Raised when input is unusable — never silently fall back to a default."""


# ── probing ──────────────────────────────────────────────────────────

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic"}


def is_image(path: str) -> bool:
    """A still picture used as b-roll: shown for the whole span, not as a 1-frame video."""
    return Path(path).suffix.lower() in IMAGE_EXT


def probe(path: str) -> dict[str, Any]:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, creationflags=NO_WINDOW, text=True, encoding="utf-8", errors="replace", timeout=60)
    if out.returncode != 0 or not out.stdout.strip():
        raise VideoEditError(f"ffprobe failed for {path}: {out.stderr[-300:]}")
    data = json.loads(out.stdout)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if video is None:
        raise VideoEditError(f"no video stream in {path}")

    width, height = int(video["width"]), int(video["height"])
    # iPhone clips carry their orientation in metadata; report what will be seen.
    rotation = 0
    for entry in video.get("side_data_list", []) or []:
        if "rotation" in entry:
            rotation = abs(int(entry["rotation"])) % 360
    if rotation in (90, 270):
        width, height = height, width

    num, _, den = video.get("r_frame_rate", "0/1").partition("/")
    fps = round(float(num) / float(den), 3) if den and float(den) else 0.0
    return {
        "path": path,
        "name": Path(path).name,
        "duration": round(float(data["format"]["duration"]), 2),
        "size_mb": round(int(data["format"]["size"]) / 1048576, 1),
        "width": width,
        "height": height,
        "fps": fps,
        "vertical": height >= width,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_channels": audio.get("channels") if audio else 0,
    }


def scan_folder(folder: str) -> list[dict[str, Any]]:
    base = Path(folder)
    if not base.is_dir():
        raise VideoEditError(f"not a folder: {folder}")
    files = []
    entries = sorted((entry for entry in base.rglob("*")
                      if entry.is_file() and entry.suffix.lower() in VIDEO_SUFFIXES),
                     key=lambda entry: str(entry.relative_to(base)).lower())
    for entry in entries:
        try:
            item = probe(str(entry))
            item["folder"] = str(entry.parent.relative_to(base)) if entry.parent != base else ""
            files.append(item)
        except VideoEditError as exc:
            files.append({"path": str(entry), "name": entry.name,
                          "folder": str(entry.parent.relative_to(base)) if entry.parent != base else "",
                          "error": str(exc)})
    return files


def suggest_projects(folder: str) -> list[dict[str, Any]]:
    """Pair long Sony takes with the closest consecutive DJI recording(s).

    This is an intake plan only: audio sync still needs to be verified before
    a CapCut draft is created.
    """
    files = scan_folder(folder)
    main = [f for f in files if f.get("folder") == "กล้องหลัก"
            and re.fullmatch(r"C\d+\.MP4", f.get("name", ""), re.I)
            and f.get("duration", 0) >= 300]
    dji = [f for f in files if f.get("folder") == "กล้องเสริม"
           and f.get("name", "").upper().startswith("DJI_")
           and f.get("duration", 0) >= 300]
    used: set[int] = set()
    plans = []
    for take, camera in enumerate(main, 1):
        choices = []
        for start in range(len(dji)):
            for length in range(1, min(3, len(dji) - start) + 1):
                indexes = list(range(start, start + length))
                if any(index in used for index in indexes):
                    continue
                duration = sum(dji[index]["duration"] for index in indexes)
                choices.append((abs(duration - camera["duration"]), indexes, duration))
        if not choices:
            plans.append({"take": take, "main": camera, "secondary": [],
                          "duration": camera["duration"], "offset": None, "sync": "unpaired",
                          "camera_count": 1, "file_count": 1})
            continue
        _, indexes, duration = min(choices, key=lambda choice: choice[0])
        used.update(indexes)
        plans.append({"take": take, "main": camera, "secondary": [dji[index] for index in indexes],
                      "duration": camera["duration"], "offset": round(duration - camera["duration"], 2),
                      "sync": "needs_audio_check", "camera_count": 2,
                      "file_count": 1 + len(indexes)})
    return plans


# ── audio analysis ───────────────────────────────────────────────────

def _extract_audio(path: str, start: float, end: float, dest: Path) -> Path:
    cmd = [FFMPEG, "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
           "-i", path, "-vn", "-ac", "1", "-ar", "16000", str(dest)]
    run = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW, text=True, encoding="utf-8", errors="replace", timeout=600)
    if run.returncode != 0 or not dest.exists():
        raise VideoEditError(f"audio extract failed: {run.stderr[-300:]}")
    return dest


def detect_pauses(path: str, start: float, end: float,
                  threshold_db: int = -33, min_len: float = 0.30) -> list[dict[str, float]]:
    """Silences inside [start, end), in clip-relative seconds.

    A normalised export can sit entirely above -33dB, so nothing reads as
    silence. When that happens, open the gate step by step instead of
    reporting "no pauses" for a clip that clearly has them.
    """
    work = Path(os.environ.get("TEMP", "/tmp")) / f"ve_{uuid.uuid4().hex}.wav"
    try:
        _extract_audio(path, start, end, work)
        for gate in (threshold_db, threshold_db + 3, threshold_db + 6, threshold_db + 9):
            run = subprocess.run(
                [FFMPEG, "-v", "error", "-i", str(work), "-af",
                 f"silencedetect=n={gate}dB:d={min_len},ametadata=print:file=-",
                 "-f", "null", "-"],
                capture_output=True, creationflags=NO_WINDOW, text=True, encoding="utf-8", errors="replace", timeout=1800)
            values = [float(m.group(2)) for m in
                      re.finditer(r"silence_(start|end)=([0-9.]+)", run.stdout + run.stderr)]
            pauses = [{"start": round(a, 2), "end": round(b, 2),
                       "length": round(b - a, 2), "threshold_db": gate}
                      for a, b in zip(values[0::2], values[1::2]) if b > a]
            if pauses:
                return pauses
        return []
    finally:
        work.unlink(missing_ok=True)


def quiet_spans(path: str, start: float, end: float, min_len: float = 0.30) -> list[dict[str, float]]:
    """Breaths and pauses under background noise (café, street), where a fixed silence gate finds nothing:
    loudness per 20 ms against this clip's own noise floor (20th percentile + 2 dB). Clip-relative seconds."""
    import wave
    import numpy as np
    work = Path(os.environ.get("TEMP", "/tmp")) / f"ve_{uuid.uuid4().hex}.wav"
    try:
        _extract_audio(path, start, end, work)
        with wave.open(str(work)) as w:
            rate, ch = w.getframerate(), w.getnchannels()
            pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16)[::ch].astype(float) / 32768
    finally:
        work.unlink(missing_ok=True)
    n = int(rate * 0.02)
    if len(pcm) < n * 10:
        return []
    db = 20 * np.log10(np.sqrt((pcm[:len(pcm) // n * n].reshape(-1, n) ** 2).mean(1)) + 1e-9)
    db = np.convolve(db, np.ones(5) / 5, mode="same")  # 100 ms smoothing: no cuts inside a word's dip
    quiet = db < np.percentile(db, 20) + 2
    out, i = [], 0
    while i < len(quiet):
        if quiet[i]:
            j = i
            while j < len(quiet) and quiet[j]:
                j += 1
            if (j - i) * 0.02 >= min_len:
                out.append({"start": round(i * 0.02, 2), "end": round(j * 0.02, 2), "length": round((j - i) * 0.02, 2)})
            i = j
        else:
            i += 1
    return out


def suggest_cuts(pauses: list[dict[str, float]], keep: float = 0.25,
                 min_gain: float = 0.30) -> list[dict[str, float]]:
    """Trim every pause down to `keep` seconds, but only when that frees
    at least `min_gain` — a shorter trim is a visible jump for no benefit."""
    cuts = []
    for pause in pauses:
        gain = pause["length"] - keep
        if gain >= min_gain:
            cuts.append({"start": round(pause["start"] + keep, 2),
                         "end": round(pause["end"], 2),
                         "gain": round(gain, 2)})
    return cuts


# ── transcription ────────────────────────────────────────────────────

def _enable_cuda_libs() -> None:
    """The CUDA runtime ships inside the nvidia-* wheels, not on PATH."""
    base = Path(os.path.dirname(os.sys.executable)) / "Lib" / "site-packages" / "nvidia"
    for sub in ("cublas", "cudnn", "cuda_nvrtc", "cuda_runtime"):
        lib = base / sub / "bin"
        if lib.is_dir():
            try:
                os.add_dll_directory(str(lib))
            except (AttributeError, OSError):
                pass
            if str(lib) not in os.environ.get("PATH", "").split(os.pathsep):  # runs on every transcription
                os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")


def _has_cuda() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _model_settings(model_size: str | None = None) -> tuple[str, str, str]:
    if _has_cuda():
        return model_size or whisper_model(), "cuda", "int8_float16"
    return model_size or WHISPER_CPU_FALLBACK, "cpu", "int8"


def _get_model(model_size: str | None = None):
    """Load once, keep it warm. Returns (model, description)."""
    global _model
    from faster_whisper import WhisperModel

    wanted = _model_settings(model_size)
    if wanted[1] == "cuda":
        _enable_cuda_libs()
        # int8_float16: large-v3 in about half the VRAM of float16 with near-identical accuracy, so it fits on an
        # 8 GB card next to the local LLM that keeps its own share loaded

    if _model is None or _model[0] != wanted:
        name, device, compute = wanted
        root = kitconfig._load().get("models_dir") or None  # chosen drive; None = the default cache on C:
        try:
            _model = (wanted, WhisperModel(name, device=device, compute_type=compute, download_root=root))
        except Exception as exc:  # no quiet switch to a smaller CPU model: the person must know the GPU failed
            raise VideoEditError(f"cannot load speech model {name} on {device}: {exc}") from exc
    return _model[1], "/".join(_model[0])


# English terms written the way finished subtitles have them (05, 06):
# loanwords in English, first letter capitalised, a space either side.
# Longest spelling first, so "เทรนด์" is not caught as "เทรน" + "ด์".
ENGLISH_TERMS: list[tuple[str, str]] = sorted([
    ("แมเนจเมนต์", "Management"), ("แมเนจ", "Manage"), ("เมเนจ", "Manage"),
    ("เวิร์ค", "Work"), ("เวิร์ก", "Work"),
    ("เทรดดิ้ง", "Trading"), ("เทรนเนอร์", "Trainer"), ("เทรนด์", "Trend"), ("เทรนนิ่ง", "Training"), ("เทรน", "Train"),
    ("โซลูชั่น", "Solution"), ("โซลูชัน", "Solution"),
    ("ดีลเลอร์", "Dealer"), ("ดิลเลอร์", "Dealer"), ("โซลาร์", "Solar"), ("โซล่าร์", "Solar"), ("โซล่า", "Solar"),
    ("อีโคซิสเต็ม", "Ecosystem"), ("อีโคซิสเท็ม", "Ecosystem"),
    ("เอนจิเนียร์", "Engineer"), ("เอ็นจิเนียร์", "Engineer"),
    ("มอนิเตอร์", "Monitor"), ("เพอร์ฟอร์มานซ์", "Performance"),
    ("มาร์เก็ตติ้ง", "Marketing"), ("มาร์เก็ต", "Market"),
    ("แวร์เฮาส์", "Warehouse"), ("เวนเจอร์", "Venture"),
    ("อินสตอลเลอร์", "Installer"), ("อินสตอร์เลอร์", "Installer"), ("อินสตอเลอร์", "Installer"), ("อินสตอลเลอ", "Installer"), ("ไทม์ไลน์", "Timeline"),
    ("โปรดักส์", "Product"), ("โปรดักต์", "Product"),
    ("อะคาเดมี่", "Academy"), ("อะคาเดมี", "Academy"),
    ("คอนเน็กต์", "Connect"), ("คอนเน็ค", "Connect"), ("คอนเนค", "Connect"),
    ("เฮดจิ้ง", "Hedging"), ("ซีอีโอ", "CEO"), ("เอสเอ็มอี", "SME"),
    ("วันแมนโชว์", "One-Man Show"),
], key=lambda pair: -len(pair[0]))

_THAI = "\u0e00-\u0e7f"


def english_terms(text: str) -> str:
    """Tidy the English inside a Thai subtitle line.

    Only words in ENGLISH_TERMS are converted; anything unknown is left as the
    recogniser wrote it rather than guessed at.
    """
    for thai, english in ENGLISH_TERMS:
        text = text.replace(thai, f" {english} ")
    # an all-lowercase English word gets a capital; acronyms and names like iPhone stay
    text = re.sub(r"(?<![A-Za-z])[a-z][a-z-]*(?![A-Za-z])",
                  lambda m: m.group(0)[0].upper() + m.group(0)[1:], text)
    text = text.replace("One Man Show", "One-Man Show")
    # one space between Thai and English, never doubled, never at a line edge
    text = re.sub(f"([{_THAI}])([A-Za-z0-9])", r"\1 \2", text)
    text = re.sub(f"([A-Za-z0-9])([{_THAI}])", r"\1 \2", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return "\n".join(line.strip() for line in text.split("\n"))


def transcribe(path: str, start: float, end: float, language: str | None = None,
               model_size: str | None = None, window: float = 15.0) -> list[dict[str, Any]]:
    """Speech to timed phrases, in clip-relative seconds. language None = speech_language(); "auto" = detected
    once across samples of the source, so every window is read as the same language.

    The audio is cut into fixed 15 s windows: on a long file the recogniser sometimes returns nothing for the
    first half-minute, which silently loses subtitles, and 30 s windows lost about a quarter of the words.
    The windows are decoded together (faster-whisper's batched pipeline). 9 Oct 2026, RTX 3070 Ti, 4 Thai
    clips: 3-10x faster than one window after another, and fewer wrong letters (CER 0.18 vs 0.30, 0.23 vs
    0.29 on the two clips with word-for-word subtitles). config.json "whisper_batched": false goes back to
    one window after another.
    """
    model, _engine = _get_model(model_size)
    full = Path(os.environ.get("TEMP", "/tmp")) / f"ve_{uuid.uuid4().hex}.wav"
    try:
        _extract_audio(path, start, end, full)
        language = language or speech_language()
        if language == "auto":
            language = _detect_language(model, full, model_size=_engine.split("/")[0])
        if kitconfig._load().get("whisper_batched", True):
            phrases = _batched(model, full, language, window)
        else:
            phrases = _one_by_one(model, full, end - start, language, window)
    finally:
        full.unlink(missing_ok=True)
    _bound_phrase_times(phrases, end - start)
    import spelling
    known = spelling.load()  # this person's own fixes (spelling.learn)
    for phrase in phrases:
        if language == "th":  # both tidy a Thai line: english_terms would capitalise every English word
            phrase["text"] = english_terms(thai_spacing(phrase["text"]))
        phrase["text"] = spelling.fix(phrase["text"], known)
    return phrases


def _bound_phrase_times(phrases: list[dict[str, Any]], duration: float) -> None:
    # Whisper can align the last word beyond the audio; keep every Thai word piece inside its clip.
    for phrase in phrases:
        phrase["start"] = max(0, min(phrase["start"], duration))
        phrase["end"] = max(phrase["start"], min(phrase["end"], duration))
        phrase["words"] = [[max(0, min(a, duration)), max(0, min(max(a, b), duration)), text]
                           for a, b, text in phrase.get("words", [])]


def _detect_language(model, wav: Path, samples: int = 5, lowest: float = 0.5,
                     model_size: str | None = None) -> str:
    """The language spoken across the file: the recogniser's odds averaged over up to `samples` 30 s windows
    spread through it. Its own detection reads only the first 30 s and stops at 0.5: physicsworks.wav
    (English) came out Dutch at 0.52 and half its subtitles were Dutch. Below `lowest` it raises."""
    from faster_whisper.audio import decode_audio
    audio = decode_audio(str(wav), sampling_rate=16000)
    n = 30 * 16000
    last = max(len(audio) - n, 0)
    starts = sorted({last * i // max(samples - 1, 1) for i in range(samples)})
    odds: dict[str, float] = {}
    for at in starts:
        for lang, p in model.detect_language(audio[at:at + n])[2]:
            odds[lang] = odds.get(lang, 0.0) + p / len(starts)
    lang = max(odds, key=odds.get)
    if odds[lang] < lowest:
        if model_size == "large-v3-turbo":
            # Turbo gave Thai 0.43 on the long-video trial; retry with an installed full model.
            from faster_whisper import WhisperModel
            from faster_whisper.utils import download_model
            try:
                cached = download_model("large-v3", local_files_only=True,
                                        cache_dir=kitconfig._load().get("models_dir") or None)
            except Exception as exc:
                raise VideoEditError("turbo cannot identify the language confidently; select the spoken language "
                                     "or install large-v3 for a second check") from exc
            # Keep the transcription model on its chosen device; the second check needs no extra VRAM.
            stronger = WhisperModel(cached, device="cpu", compute_type="int8", local_files_only=True)
            return _detect_language(stronger, wav, samples, lowest, model_size="large-v3")
        raise VideoEditError(f"cannot tell the spoken language (best guess {lang}, {odds[lang]:.2f}): "
                             'set config.json "speech_language" to its code, e.g. "en"')
    return lang


def _batched(model, wav: Path, language: str, window: float, overlap: float = 1.0) -> list[dict[str, Any]]:
    """Windows run `overlap` s past their end so a word on the seam is heard whole once (without it words at
    15/30/75 s were lost on REF2 09); a seam is cut between whole words, never between word pieces."""
    from faster_whisper import BatchedInferencePipeline
    from faster_whisper.audio import decode_audio
    audio = decode_audio(str(wav), sampling_rate=16000)
    total = len(audio) / 16000
    starts = [i * window for i in range(int(total // window) + 1) if total - i * window >= 0.5]
    if not starts:
        return []
    pipeline = BatchedInferencePipeline(model)
    segments = []
    for half in (starts[0::2], starts[1::2]):  # faster-whisper 1.2.1 asserts if clips in one call overlap
        if half:
            clips = [{"start": t, "end": min(t + window + overlap, total)} for t in half]
            segments += pipeline.transcribe(
                audio, language=language, clip_timestamps=clips, batch_size=8, vad_filter=False,
                beam_size=5, condition_on_previous_text=False, word_timestamps=True)[0]
    words: list[list] = []
    for seg in sorted(segments, key=lambda seg: seg.start):  # one segment per window
        piece = [[round(w.start, 2), round(w.end, 2), w.word] for w in (seg.words or []) if w.word.strip()]
        if not piece:
            continue
        whole = _word_starts(piece, language)
        hi = int(seg.start // window) * window + window + overlap / 2
        # start after the last whole word the window before kept; stop after the whole word on this seam
        begin = next((i for i in range(len(piece)) if whole[i] and (not words or piece[i][0] >= words[-1][1] - 0.1)),
                     len(piece))
        stop = next((i for i in range(begin + 1, len(piece)) if whole[i] and piece[i][0] >= hi), len(piece))
        words += piece[begin:stop]
    words = _drop_loops(words)
    return _lines(words, language) if words else []


def _drop_loops(words: list[list], most: int = 2) -> list[list]:
    """The batched decoder has no temperature fallback, so it can get stuck saying one phrase over and over
    (IMG_6414: "แค่มีสต๊อค" 13 times in 13 s, as one-letter pieces). A run of the same 4-40 letters said more
    than `most` times in a row keeps its first `most`."""
    text = "".join(w[2] for w in words)
    starts, at = [], 0
    for w in words:
        starts.append(at)
        at += len(w[2])
    out: list[list] = []
    keep = skip = 0  # letters [keep, skip) are extra repeats
    for i, w in enumerate(words):
        off = starts[i]
        if off >= skip:
            for size in range(4, 41):
                unit = text[off:off + size]
                if len(unit) < size:
                    break
                n = 1
                while text[off + n * size:off + (n + 1) * size] == unit:
                    n += 1
                if n > most and unit.strip():
                    keep, skip = off + most * size, off + n * size
                    break
        if not keep <= off < skip:
            out.append(w)
    return out


THAI_MARKS = "\u0e31\u0e34-\u0e3a\u0e47-\u0e4e"  # vowels above/below and tone marks: never start a line
THAI_LEADING = "เแโใไ"  # vowels written before their consonant: never end a line


def _word_starts(words: list[list], language: str = "th") -> list[bool]:
    """Which recogniser word pieces begin a whole word. Thai pieces can end mid-syllable ("นั่|งสมาธิ"),
    so a piece counts only where pythainlp (BudouX for Japanese / Chinese) also puts a boundary; elsewhere a word
    starts after a space."""
    text = "".join(w[2] for w in words)
    if language == "th":
        from pythainlp.tokenize import word_tokenize
        tokens = word_tokenize(text, engine="newmm", keep_whitespace=True)
    elif language in PHRASE_PARSERS:  # Japanese / Chinese: BudouX phrases (LibriVox test: "およ|そ正午" without it)
        import budoux
        tokens = getattr(budoux, PHRASE_PARSERS[language])().parse(text)
    else:
        return [i == 0 or language in NO_SPACES or w[2][:1].isspace() for i, w in enumerate(words)]
    bounds, at = set(), 0
    for token in tokens:
        bounds.add(at)
        at += len(token)
    starts, at = [], 0
    for i, w in enumerate(words):
        starts.append(i == 0 or (at in bounds and not re.match(f"[{THAI_MARKS}]", w[2].lstrip())
                                 and words[i - 1][2].rstrip()[-1:] not in THAI_LEADING))
        at += len(w[2])
    return starts


def _lines(words: list[list], language: str = "th", gap: float = 0.3, shortest: float = 1.0, longest: float = 3.5) -> list[dict[str, Any]]:
    """Group timed words into subtitle lines: break at a pause of `gap` s, then split any line over `longest` s
    at its widest pause; never inside a Thai word, never leaving a line under `shortest` s."""
    ok = _word_starts(words, language)
    ok[0] = False
    cuts, last = [], 0
    for i in range(1, len(words)):
        if (ok[i] and words[i][0] - words[i - 1][1] >= gap and words[i - 1][1] - words[last][0] >= shortest
                and words[-1][1] - words[i][0] >= shortest):
            cuts.append(i)
            last = i

    def split(lo: int, hi: int) -> list[int]:
        length = words[hi - 1][1] - words[lo][0]
        if length <= longest:
            return []
        inner = [i for i in range(lo + 1, hi) if ok[i] and words[i - 1][1] - words[lo][0] >= shortest
                 and words[hi - 1][1] - words[i][0] >= shortest]
        if not inner:
            return []
        mid = (words[lo][0] + words[hi - 1][1]) / 2
        i = max(inner, key=lambda i: (round(words[i][0] - words[i - 1][1], 2), -abs(words[i][0] - mid)))
        return split(lo, i) + [i] + split(i, hi)

    edges = [0] + cuts + [len(words)]
    starts = [0]
    for lo, hi in zip(edges, edges[1:]):
        starts += split(lo, hi) + [hi]
    groups = [words[a:b] for a, b in zip(starts, starts[1:]) if b > a]
    return [{"start": g[0][0], "end": g[-1][1], "text": "".join(w[2] for w in g).strip(), "words": g}
            for g in groups]


def _one_by_one(model, full: Path, total: float, language: str, window: float) -> list[dict[str, Any]]:
    temp_dir = full.parent
    phrases: list[dict[str, Any]] = []
    offset = 0.0
    while offset < total - 0.5:
        span = min(window, total - offset)
        chunk = temp_dir / f"ve_{uuid.uuid4().hex}.wav"
        run = subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-ss", f"{offset:.3f}", "-t", f"{span:.3f}",
             "-i", str(full), str(chunk)],
            capture_output=True, creationflags=NO_WINDOW, text=True, encoding="utf-8", errors="replace", timeout=600)
        if run.returncode != 0:
            raise VideoEditError(f"window extract failed: {run.stderr[-200:]}")
        try:
            segments, _ = model.transcribe(
                str(chunk), language=language, vad_filter=False,
                beam_size=5, condition_on_previous_text=False, word_timestamps=True)
            for seg in segments:
                text = seg.text.strip()
                if text:
                    # word times drive the word-by-word subtitle highlight in the MP4 export
                    words = [[round(offset + w.start, 2), round(offset + w.end, 2), w.word]
                             for w in (seg.words or []) if w.word.strip()]
                    phrases.append({"start": round(offset + seg.start, 2),
                                    "end": round(offset + seg.end, 2),
                                    "text": text, "words": words})
        finally:
            chunk.unlink(missing_ok=True)
        offset += window - 1.0
    phrases.sort(key=lambda p: p["start"])
    merged: list[dict[str, Any]] = []
    for phrase in phrases:
        if merged:
            previous = merged[-1]
            same_text = phrase["text"] == previous["text"]
            inside = phrase["end"] <= previous["end"] + 0.05
            if same_text or inside:
                continue  # the window overlap said it twice
            if phrase["start"] < previous["end"]:
                phrase["start"] = previous["end"]  # trim the seam, keep the words
                if phrase["end"] - phrase["start"] < 0.3:
                    continue
        merged.append(phrase)
    return merged


THAI = "฀-๿"


def thai_spacing(text: str) -> str:
    """The model sometimes puts a space after every Thai word ("เอา งี้ เอา กิน"); Thai only spaces between
    phrases. When spaces are that dense, join Thai-to-Thai words; normal phrase spacing is left alone."""
    thai = len(re.findall(f"[{THAI}]", text))
    gaps = len(re.findall(f"(?<=[{THAI}]) (?=[{THAI}])", text))
    return re.sub(f"(?<=[{THAI}]) (?=[{THAI}])", "", text) if thai and gaps / thai > 0.12 else text


# ── CapCut draft writing ─────────────────────────────────────────────

def _template_dir() -> Path:
    if CAPCUT_TEMPLATE_DRAFT:
        path = Path(CAPCUT_TEMPLATE_DRAFT)
        if not (path / "draft_content.json").is_file():
            raise VideoEditError(f"CAPCUT_TEMPLATE_DRAFT has no draft_content.json: {path}")
        return path
    root = Path(capcut_drafts_root())
    if not root.is_dir():
        raise VideoEditError(f"CapCut drafts folder not found: {root}")

    def usable(folder: Path) -> bool:
        """A template must have a video clip and a subtitle to copy styling from,
        otherwise the new project has nothing to model itself on."""
        try:
            draft = json.loads((folder / "draft_content.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        tracks = {t["type"]: t for t in draft.get("tracks", [])}
        return bool(tracks.get("video", {}).get("segments")) and             bool(tracks.get("text", {}).get("segments"))

    # A project CapCut itself made, in its current folder layout (Timelines/project.json): a clone of a ClipKit-made
    # project or an old-layout one passes its faults on, and CapCut 9.x refuses old-layout projects ("unusual path").
    candidates = [p for p in root.iterdir()
                  if (p / "draft_content.json").is_file() and not (p / "clipkit.json").is_file()
                  and not p.name.endswith("· CapCut") and (p / "Timelines" / "project.json").is_file() and usable(p)]
    if not candidates:
        raise VideoEditError(
            f"no CapCut project in {root} can serve as a template - make one project in CapCut itself "
            f"(any clip with at least one subtitle), save it, then try again")
    return max(candidates, key=lambda p: (p / "draft_content.json").stat().st_mtime)


# CapCut's colour panel on a clip: the adjust effects (by type) and the switches that turn each part on
# (every type seen in the drafts on 10 Oct 2026; "figure" is face/body retouch and "mix_mode" blending, so they stay)
COLOUR_EFFECTS = {"saturation", "temperature", "tone", "lut", "smart_color_adjust", "color_match", "color_correct",
                  "bloom"}
COLOUR_FLAGS = ("enable_adjust", "enable_color_curves", "enable_smart_color_adjust", "enable_lut", "enable_hsl",
                "enable_hsl_curves", "enable_color_wheels", "enable_color_match_adjust", "enable_color_correct_adjust",
                "enable_adjust_mask")


def _drop_unused_materials(draft: dict[str, Any]) -> None:
    """The template project's own materials (hundreds of effects, stickers, sounds) that nothing in the new clip
    uses: keep a material only when its id appears in the tracks or in another kept material."""
    materials = draft["materials"]
    ids = {m["id"] for v in materials.values() if isinstance(v, list) for m in v if isinstance(m, dict) and "id" in m}
    rest = json.dumps({k: v for k, v in draft.items() if k != "materials"})
    keep = {i for i in ids if i in rest}
    while True:  # a kept material can point at another (a text at its animation, a clip at its mask)
        inside = json.dumps([m for v in materials.values() if isinstance(v, list) for m in v
                             if isinstance(m, dict) and m.get("id") in keep])
        more = {i for i in ids - keep if i in inside}
        if not more:
            break
        keep |= more
    for key, value in materials.items():
        if isinstance(value, list):
            materials[key] = [m for m in value if not (isinstance(m, dict) and "id" in m and m["id"] not in keep)]


def _new_id() -> str:
    return str(uuid.uuid4()).upper()


CANVAS = {"portrait": (1080, 1920), "landscape": (1920, 1080), "square": (1080, 1080)}


def crop_clip(vw: int, vh: int, W: int, H: int, focus: tuple[float, float]) -> dict[str, float]:
    """CapCut clip values that fill the canvas with the footage (no bars) and put focus (0-1 of the frame,
    e.g. the speaker's face) as near the middle as the frame allows. CapCut's scale 1 = fit inside."""
    fit, cover = min(W / vw, H / vh), max(W / vw, H / vh)
    sw, sh = vw * cover, vh * cover
    ox = max(-(sw - W) / 2, min((sw - W) / 2, (0.5 - focus[0]) * sw))
    oy = max(-(sh - H) / 2, min((sh - H) / 2, (focus[1] - 0.5) * sh))  # CapCut y points up
    return {"scale": round(cover / fit, 4), "x": round(ox / (W / 2), 4), "y": round(oy / (H / 2), 4)}


def find_focus(path: str, start: float = 0.0, end: float | None = None, frames: int = 16) -> dict[str, Any]:
    """Where the speaker is: the median centre of the biggest face over a few frames (0-1 of the frame)."""
    import statistics
    import tempfile
    import cv2
    info = probe(path)
    end = min(end or info["duration"], info["duration"])
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    xs, ys, best = [], [], (0, start)
    with tempfile.TemporaryDirectory() as tmp:
        for k in range(frames):
            t = start + (end - start) * (k + 0.5) / frames
            jpg = Path(tmp) / f"{k}.jpg"
            subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                            "-vf", "scale=640:-2", str(jpg)], capture_output=True, timeout=60)
            img = cv2.imread(str(jpg))
            if img is None:
                continue
            faces = det.detectMultiScale(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 1.1, 6, minSize=(40, 40))
            if len(faces):
                x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                best = max(best, (w * h, t))
                xs.append((x + w / 2) / img.shape[1])
                ys.append((y + h / 2) / img.shape[0])
    if not xs:
        return {"found": False, "x": 0.5, "y": 0.5, "frames": frames}
    return {"found": True, "x": round(statistics.median(xs), 3), "y": round(statistics.median(ys), 3),
            "faces": len(xs), "frames": frames, "best_at": round(best[1], 2)}


def create_capcut_draft(video_path: str, project_name: str,
                        clip_in: float, clip_out: float,
                        cuts: list[dict[str, float]] | None = None,
                        subs: list[dict[str, Any]] | None = None,
                        sub_size: int = 18, sub_y: float = -0.60,
                        sub_color: str = "#ffffff", sub_stroke: float = 0.05,
                        drafts_root: str | None = None, shape: str = "source",
                        focus: tuple[float, float] = (0.5, 0.5)) -> dict[str, Any]:
    """Clone a working draft and rewrite its timeline for this clip."""
    cuts = sorted((c for c in (cuts or [])), key=lambda c: c["start"])
    subs = sorted((s for s in (subs or [])), key=lambda s: s["start"])
    info = probe(video_path)
    if clip_out <= clip_in:
        raise VideoEditError("clip_out must be greater than clip_in")

    root = Path(drafts_root or capcut_drafts_root())
    safe_name = re.sub(r'[\\/:*?"<>|]', " ", project_name).strip() or "Untitled"
    target = root / safe_name
    if target.exists():
        target = root / f"{safe_name} {time.strftime('%H%M%S')}"
    shutil.copytree(_template_dir(), target,
                    ignore=shutil.ignore_patterns("*.bak_*", "*.bak", "draft_content.before_*",
                                                  "draft_content.user_edited_*", "*.tmp",
                                                  "clipkit*.json", ".clipkit-web-ai"))  # the template project's own raw file / motions / subs

    content_path = target / "draft_content.json"
    draft = json.loads(content_path.read_text(encoding="utf-8"))
    materials = draft["materials"]
    video_track = next(t for t in draft["tracks"] if t["type"] == "video")
    text_track = next((t for t in draft["tracks"] if t["type"] == "text"), None)
    # keep ONE video and ONE text track: the template is the client's last real project, and any other
    # track would carry that project's footage, inserts and cards into the new clip
    draft["tracks"] = [t for t in (video_track, text_track) if t is not None]

    index: dict[str, tuple[str, dict]] = {}
    for key, value in materials.items():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and "id" in item:
                    index[item["id"]] = (key, item)

    base_segment = copy.deepcopy(video_track["segments"][0])
    grade_template = [index[r] for r in base_segment["extra_material_refs"] if r in index]

    # point the source material at the new file
    source_material = copy.deepcopy(materials["videos"][0])
    source_material["id"] = _new_id()
    source_material["path"] = str(Path(video_path).as_posix())
    source_material["material_name"] = Path(video_path).name
    source_material["duration"] = int(round(info["duration"] * US))
    # What CapCut writes for a clip you imported yourself. A lower value leaves
    # the clip without its selection box in the preview — you cannot scale or
    # move it. Measured: every hand-made project on this machine uses this.
    source_material["check_flag"] = 62978047
    source_material["is_set_beauty_mode"] = False
    source_material["is_unified_beauty_mode"] = False
    source_material["width"] = info["width"] if not info["vertical"] else info["width"]
    source_material["height"] = info["height"]
    # files CapCut made from the template's footage (enhanced voice, reversed copy, proxy): left in, CapCut
    # plays the template clip's processed sound under this clip's picture
    for key in ("media_path", "intensifies_audio_path", "intensifies_path", "reverse_path",
                "reverse_intensifies_path", "cartoon_path"):
        if key in source_material:
            source_material[key] = ""
    source_material["has_audio"] = info["audio_channels"] > 0
    materials["videos"] = [source_material]
    base_segment["material_id"] = source_material["id"]
    # the template clip's playback edits (speed, reverse, voice, volume, fades, keyframes) are not this clip's:
    # a sped-up template made the sound run ahead of the picture
    base_segment.update(speed=1.0, reverse=False, intensifies_audio=False, is_tone_modify=False,
                        volume=1.0, last_nonzero_volume=1.0, keyframe_refs=[], common_keyframes=[])
    grade_template = [(k, m) for k, m in grade_template if k not in ("audio_fades", "audio_effects", "loudnesses")]
    # nor is its colour (P'Ohm 2026-10-10): auto adjust, colour match to a reference frame, temperature/saturation,
    # LUT, HSL and curves came along from the last hand-made project; a new clip starts with no colour settings
    grade_template = [(k, m) for k, m in grade_template
                      if k not in ("hsl", "color_curves") and not (k == "effects" and m.get("type") in COLOUR_EFFECTS)]
    base_segment.update({flag: False for flag in COLOUR_FLAGS if flag in base_segment})
    for k, m in grade_template:
        if k == "speeds":
            m.update(speed=1.0, mode=0, curve_speed=None)

    keeps: list[tuple[float, float]] = []
    at = clip_in
    for cut in cuts:
        start, end = clip_in + cut["start"], clip_in + cut["end"]
        if start > at:
            keeps.append((at, start))
        at = max(at, end)
    if at < clip_out:
        keeps.append((at, clip_out))

    segments = []
    timeline = 0
    for start, end in keeps:
        duration = int(round((end - start) * US))
        if duration <= 0:
            continue
        segment = copy.deepcopy(base_segment)
        segment["id"] = _new_id()
        segment["source_timerange"] = {"start": int(round(start * US)), "duration": duration}
        segment["target_timerange"] = {"start": timeline, "duration": duration}
        timeline += duration
        refs = []
        for key, item in grade_template:
            clone = copy.deepcopy(item)
            clone["id"] = _new_id()
            materials.setdefault(key, []).append(clone)
            refs.append(clone["id"])
        segment["extra_material_refs"] = refs
        segments.append(segment)
    if not segments:
        shutil.rmtree(target, ignore_errors=True)
        raise VideoEditError("every second of the clip was cut away")
    video_track["segments"] = segments

    def shifted(t: float) -> float:
        removed = 0.0
        for cut in cuts:
            if t >= cut["end"]:
                removed += cut["end"] - cut["start"]
            elif t > cut["start"]:
                removed += t - cut["start"]
        return t - removed

    if text_track is not None and text_track["segments"]:
        template_seg = copy.deepcopy(text_track["segments"][0])
        template_mat = copy.deepcopy(index[template_seg["material_id"]][1])
        materials["texts"] = []
        text_segments = []
        for sub in subs:
            text = str(sub["text"])
            mat = copy.deepcopy(template_mat)
            mat["id"] = _new_id()
            mat["name"] = "Subtitle"
            body = json.loads(mat["content"])
            body["text"] = text
            rgb = [int(sub_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            for style in body["styles"]:
                style["size"] = sub_size
                style["range"] = [0, len(text)]
                style["fill"]["content"]["solid"]["color"] = rgb
                for stroke in style.get("strokes", []):
                    stroke["width"] = sub_stroke
            mat["content"] = json.dumps(body, ensure_ascii=False)
            mat["font_size"] = float(sub_size)
            mat["text_size"] = sub_size
            mat["text_color"] = sub_color
            mat["border_width"] = sub_stroke
            materials["texts"].append(mat)

            start, end = shifted(float(sub["start"])), shifted(float(sub["end"]))
            end = min(end, timeline / US - 0.07)
            if end - start < 0.3:
                continue
            seg = copy.deepcopy(template_seg)
            seg["id"] = _new_id()
            seg["material_id"] = mat["id"]
            seg["target_timerange"] = {"start": int(round(start * US)),
                                       "duration": int(round((end - start) * US))}
            seg["clip"] = copy.deepcopy(template_seg["clip"])
            seg["clip"]["transform"] = {"x": 0.0, "y": sub_y}
            seg["render_index"] = 14001
            text_segments.append(seg)
        text_track["segments"] = text_segments

    draft["duration"] = timeline
    W, H = CANVAS.get(shape) or ((1080, 1920) if info["vertical"] else (1920, 1080))
    draft["canvas_config"] = {"ratio": "original", "width": W, "height": H, "background": None}
    if shape in CANVAS:
        clip = crop_clip(info["width"], info["height"], W, H, focus)
        for seg in segments:
            seg["clip"]["scale"] = {"x": clip["scale"], "y": clip["scale"]}
            seg["clip"]["transform"] = {"x": clip["x"], "y": clip["y"]}
    _drop_unused_materials(draft)
    atomic.write_text(content_path, json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")

    meta_path = target / "draft_meta_info.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["draft_name"] = target.name
        meta["draft_fold_path"] = str(target.as_posix())
        meta["draft_root_path"] = str(root.as_posix())
        meta["draft_id"] = _new_id()
        meta["tm_duration"] = timeline
        # the template's cover picture and media size would show in CapCut's project list: use this clip's
        meta["draft_timeline_materials_size_"] = Path(video_path).stat().st_size
        cover = target / "draft_cover.jpg"
        cut = subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{clip_in + 1:.2f}", "-i", str(video_path),
                              "-frames:v", "1", "-vf", "scale=-2:720", str(cover)], capture_output=True, text=True, timeout=60)
        if cut.returncode != 0:
            raise VideoEditError(f"could not make the project cover: {cut.stderr.strip()[-200:]}")
        meta["draft_cover"] = "draft_cover.jpg"
        meta["tm_draft_create"] = int(time.time() * 1_000_000)
        meta["tm_draft_modified"] = int(time.time() * 1_000_000)
        for group in meta.get("draft_materials", []):
            group["value"] = [value for value in (group.get("value") or []) if value.get("metetype") == "video"][:1]
            for value in group.get("value", []) or []:
                if value.get("metetype") == "video":
                    value["file_Path"] = str(Path(video_path).as_posix())
                    value["extra_info"] = Path(video_path).name
                    value["duration"] = int(round(info["duration"] * US))
                    value["width"] = info["width"]
                    value["height"] = info["height"]
                    rough = value.get("roughcut_time_range")
                    if isinstance(rough, dict):
                        rough["duration"] = int(round(info["duration"] * US))
        atomic.write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    import capcut_edit  # here: capcut_edit imports this module
    capcut_edit.fresh_ids(target)  # own timeline id + the Timelines/<id> copy CapCut 9.x opens
    # Supplied subtitles still need the existing transcript's word clock for emphasis.
    import cue_data
    heard = {}
    cue_rows = capcut_edit.read_draft(str(target))["subtitles"]
    for segment in segments:
        start = segment["source_timerange"]["start"] / US
        length = segment["source_timerange"]["duration"] / US
        for phrase in capcut_edit._from_transcript(video_path, start, start + length) or []:
            at = segment["target_timerange"]["start"] / US + phrase["start"]
            matches = [row for row in cue_rows if row["text"] == phrase["text"] and abs(row["start"] - at) <= .04]
            if len(matches) == 1:
                heard[cue_data.key(matches[0]["id"])] = [[round(a - phrase["start"], 2), round(b - phrase["start"], 2), w]
                                                       for a, b, w in phrase["words"]]
    if heard:
        cue_data.save(target, "clipkit_words.json", heard)

    return {
        "draft_path": str(target),
        "draft_name": target.name,
        "segments": len(segments),
        "subtitles": len(text_track["segments"]) if text_track is not None else 0,
        "duration": round(timeline / US, 2),
        "removed": round((clip_out - clip_in) - timeline / US, 2),
    }


# ── presets learned from the projects already edited ──────────────────

LOCAL_LLM_URL = os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:8081/v1")
LOCAL_LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "qwen3-4b-2507")


def summarize_transcript(lines: list[str], source_name: str) -> dict[str, str]:
    """Make a small review card locally; the original transcript stays in Markdown."""
    import urllib.request

    text = "\n".join(lines)
    prompt = (
        "อ่านข้อความถอดเสียงภาษาไทยนี้ แล้วตอบ JSON เท่านั้น โดยมี title, hook, summary "
        "อย่างละข้อความสั้นภาษาไทย. title คือหัวข้อคลิป, hook คือประโยคเปิดที่น่าใช้, "
        "summary สรุปสิ่งที่พูด 1-2 ประโยค. ถ้าไม่มีเนื้อหาให้บอกว่าไม่มีเสียงพูด.\n\n"
        f"ไฟล์: {source_name}\n\n{text}"
    )
    payload = json.dumps({"model": LOCAL_LLM_MODEL,
                          "messages": [{"role": "user", "content": prompt}],
                          "temperature": 0.2, "max_tokens": 400}).encode("utf-8")
    request = urllib.request.Request(f"{LOCAL_LLM_URL}/chat/completions", data=payload,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            content = json.loads(response.read().decode("utf-8"))["choices"][0]["message"]["content"]
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        match = re.search(r"\{.*\}", content, re.S)
        result = json.loads(match.group(0) if match else content)
        return {key: str(result.get(key, "")).strip() for key in ("title", "hook", "summary")}
    except Exception as exc:
        return {"title": source_name, "hook": "", "summary": f"สรุป Qwen ไม่สำเร็จ: {exc}"}


STORY_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "story_split.md"


def transcript_file(video: str) -> Path:
    return Path(video).with_suffix(Path(video).suffix + ".transcript.json")


CHUNK_S = 600


def lines_file(video: str) -> Path:
    return Path(video).with_suffix(Path(video).suffix + ".lines.json")


def transcribe_file(video: str) -> list[dict[str, Any]]:
    """Save each completed piece atomically; resume only the same source and ASR settings."""
    tf = transcript_file(video)
    part, meta = tf.with_suffix(".part.json"), tf.with_suffix(".meta.json")
    media = Path(video)
    stat = media.stat()
    source_stat = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)

    def check_source():
        try:
            current = media.stat()
            unchanged = (current.st_size, current.st_mtime_ns, current.st_ctime_ns, current.st_ino) == source_stat
        except OSError:
            unchanged = False
        if not unchanged:
            if not part.exists():
                atomic.write_text(part, json.dumps({"source": None, "end": 0.0, "language": None, "phrases": []}), encoding="utf-8")
            raise VideoEditError("ฟุตเปลี่ยนระหว่างถอดเสียง · เริ่มถอดเสียงจากฟุตล่าสุดใหม่")

    cfg = kitconfig._load()
    engine = _model_settings()
    with Path(video).open("rb") as source:
        identity = {"schema_version": 2, "sha256": hashlib.file_digest(source, "sha256").hexdigest(),
                    "model": "/".join(engine), "models_dir": cfg.get("models_dir"),
                    "language": speech_language(),
                    "batched": cfg.get("whisper_batched", True), "chunk_s": CHUNK_S}
    check_source()
    # An unbound legacy transcript cannot prove which footage/settings produced its words.
    cached = not part.exists() and meta.is_file() and json.loads(meta.read_text(encoding="utf-8")) == identity
    if tf.is_file() and cached:
        phrases = json.loads(tf.read_text(encoding="utf-8"))
    else:
        done = json.loads(part.read_text(encoding="utf-8")) if part.is_file() else None
        if done is None or done["source"] != identity:
            done = {"source": identity, "end": 0.0, "language": None, "phrases": []}
        if "backups" not in done:
            stamp = f".before-retranscribe-{time.time_ns()}-{uuid.uuid4().hex[:8]}"
            done["backups"] = []
            for original in (tf, meta, lines_file(video)):
                if original.is_file():
                    backup = original.with_name(original.name + stamp)
                    shutil.copy2(original, backup)
                    done["backups"].append(backup.name)
            # Persist even the zero-second checkpoint: interruption before the first chunk
            # must retain the originals and reuse this backup rather than make one per retry.
            check_source()
            atomic.write_text(part, json.dumps(done, ensure_ascii=False), encoding="utf-8")
        total = probe(video)["duration"]
        while done["end"] < total:
            start = done["end"]
            end = total if total - start <= CHUNK_S * 1.5 else _quiet_point(video, start + CHUNK_S)
            if not start < end <= total:
                raise VideoEditError("invalid transcription checkpoint boundary")
            if done["language"] is None:
                done["language"] = _file_language(video) if total > CHUNK_S * 1.5 else speech_language()
            piece = transcribe(video, start, end, done["language"], model_size=engine[0])
            shifted = [{**p, "start": round(p["start"] + start, 2), "end": round(p["end"] + start, 2),
                        "words": [[round(a + start, 2), round(b + start, 2), w]
                                  for a, b, w in p.get("words", [])]} for p in piece]
            done["phrases"].extend(shifted)
            done["end"] = end
            check_source()
            atomic.write_text(part, json.dumps(done, ensure_ascii=False), encoding="utf-8")
        phrases = done["phrases"]
        check_source()
        atomic.write_text(tf, json.dumps(phrases, ensure_ascii=False, indent=1), encoding="utf-8")
        check_source()
        atomic.write_text(meta, json.dumps(identity), encoding="utf-8")
        check_source()
        part.unlink(missing_ok=True)
    check_source()
    atomic.write_text(lines_file(video), json.dumps([{k: p[k] for k in ("start", "end", "text")}
                                                   for p in phrases], ensure_ascii=False, indent=1), encoding="utf-8")
    check_source()
    return phrases


def _file_language(video: str) -> str:
    language = speech_language()
    if language != "auto":
        return language
    model, engine = _get_model()
    wav = Path(os.environ.get("TEMP", "/tmp")) / f"ve_{uuid.uuid4().hex}.wav"
    try:
        _extract_audio(video, 0, probe(video)["duration"], wav)
        return _detect_language(model, wav, model_size=engine.split("/")[0])
    finally:
        wav.unlink(missing_ok=True)


def _quiet_point(video: str, near: float, reach: float = 30.0) -> float:
    # shortcut: quietest spans can include speech; review seams before editorial delivery.
    lo = max(near - reach, 0.0)
    spans = quiet_spans(video, lo, near + reach, min_len=0.2)
    if not spans:
        raise VideoEditError(f"no quiet span near {near:.0f} s to split transcription")
    span = max(spans, key=lambda s: (s["length"], -abs(lo + (s["start"] + s["end"]) / 2 - near)))
    return round(lo + (span["start"] + span["end"]) / 2, 2)


def stories_file(video: str) -> Path:
    return Path(video).with_suffix(Path(video).suffix + ".stories.json")


def brief_file(video: str) -> Path:
    """What the person wants from this video, in their own words (style, mood, which parts), typed before the split."""
    return Path(video).with_suffix(Path(video).suffix + ".brief.txt")


def read_brief(video: str) -> str:
    f = brief_file(video)
    return f.read_text(encoding="utf-8").strip() if f.is_file() else ""


def agent_command(kind: str, target: str) -> str:
    """The line the editor pastes into Claude / Codex chat; the ClipKit skill knows what to do with it."""
    return {"stories": f'ClipKit: แบ่งเรื่อง "{target}"',
            "translate": f'ClipKit: แปลซับเป็นอังกฤษ "{target}"',
            "punch": f'ClipKit: เลือกคำเน้น "{target}"',
            "spelling": f'ClipKit: ตรวจคำผิด "{target}"'}[kind]


def slice_transcript(phrases: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    """Keep complete spoken words at semantic edges inside an ASR transport row."""
    out = []
    for phrase in phrases:
        if phrase["end"] <= start or phrase["start"] >= end:
            continue
        crossing = [edge for edge in (start, end) if phrase["start"] < edge < phrase["end"]]
        if not crossing:
            out.append(copy.deepcopy(phrase))
            continue
        words = phrase.get("words", [])
        if not words:
            raise VideoEditError("ขอบเขตเรื่องอยู่กลางบรรทัดที่ไม่มีเวลารายคำ · ถอดเสียงรายคำก่อนเลือกช่วงนี้")
        language = "th" if re.search(r"[ก-๛]", phrase["text"]) else speech_language()
        starts = [n for n, whole in enumerate(_word_starts(words, language)) if whole] + [len(words)]
        if any(words[a][0] + 1e-6 < edge < words[b - 1][1] - 1e-6
               for a, b in zip(starts, starts[1:]) for edge in crossing):
            raise VideoEditError("ขอบเขตเรื่องตัดกลางคำ · เลือกจุดเริ่มหรือจบของคำเต็ม")
        kept = [w for w in words if w[0] >= start - 1e-6 and w[1] <= end + 1e-6]
        if kept:
            out.append({**copy.deepcopy(phrase), "start": max(phrase["start"], kept[0][0]),
                        "end": min(phrase["end"], kept[-1][1]),
                        "text": "".join(w[2] for w in kept).strip(), "words": copy.deepcopy(kept)})
    return out


def validate_stories(items: Any, phrases: list[dict[str, Any]], *, max_minutes: float | None = None) -> list[dict[str, Any]]:
    """One trust boundary for browser answers and persisted local/manual selections."""
    import story_selection
    return story_selection.validate(items, phrases, max_minutes=max_minutes)


def stories_from_agent(video: str, phrases: list[dict[str, Any]], *, split_long: bool = True) -> list[dict[str, Any]] | None:
    """Current, explicitly validated selection; legacy/unbound/stale files remain historical.

    split_long is retained for callers, but an accepted semantic selection is never split again.
    """
    import story_selection
    items = story_selection.read_selection(video, phrases)
    if items is None:
        return None
    out = []
    for item in items:
        start, end = item["start"], item["end"]
        part = slice_transcript(phrases, start, end)
        out.append({**item, "lines": len(part), "speech": round(sum(p["end"] - p["start"] for p in part), 1)})
    return out


STORY_MAX_S = 300     # a story above this is split (length is trimmed later, in the clip step)
STORY_AIM_S = 150


def _split_long(first: int, last: int, phrases: list[dict[str, Any]], title: str, summary: str,
                *, max_s: float | None = None, aim_s: float | None = None) -> list[dict[str, Any]]:
    """The small local model sometimes returns one story for a whole file. Any story longer than STORY_MAX_S
    is cut at the longest pause near every STORY_AIM_S, so each part still ends on a finished sentence."""
    parts, a = [], first
    while phrases[last]["end"] - phrases[a]["start"] > (max_s if max_s is not None else STORY_MAX_S):
        target = phrases[a]["start"] + (aim_s if aim_s is not None else STORY_AIM_S)
        window = [i for i in range(a + 1, last + 1) if abs(phrases[i]["start"] - target) <= 25]
        if not window:
            break
        cut = max(window, key=lambda i: (phrases[i]["start"] - phrases[i - 1]["end"],
                                         -abs(phrases[i]["start"] - target) if aim_s is not None else 0))
        parts.append((a, cut - 1))
        a = cut
    parts.append((a, last))
    out = []
    for n, (f, l) in enumerate(parts, 1):
        part = phrases[f:l + 1]
        out.append({
            "title": title + (f" (ตอน {n})" if len(parts) > 1 else ""),
            "summary": summary if n == 1 else "",
            "start": part[0]["start"], "end": part[-1]["end"],
            "speech": round(sum(p["end"] - p["start"] for p in part), 1),
            "lines": len(part),
        })
    return out


def extract_presets(drafts_root: str | None = None) -> dict[str, Any]:
    """Read every CapCut draft in the folder and report the colour grade and
    subtitle styling actually in use, most-used first."""
    from collections import Counter, defaultdict

    root = Path(drafts_root or capcut_drafts_root())
    if not root.is_dir():
        raise VideoEditError(f"CapCut drafts folder not found: {root}")

    grade: dict[str, Counter] = defaultdict(Counter)
    fonts, sizes, colors, strokes, ypos, anims = (Counter() for _ in range(6))
    curves = 0
    projects: list[dict[str, Any]] = []

    for folder in sorted(root.iterdir()):
        content = folder / "draft_content.json"
        if not content.is_file():
            continue
        try:
            draft = json.loads(content.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        materials = draft.get("materials", {})
        own_grade = {}
        for effect in materials.get("effects") or []:
            kind = effect.get("type")
            if kind:
                value = round(float(effect.get("value", 0)), 3)
                grade[kind][value] += 1
                own_grade.setdefault(kind, value)
        curves += sum(1 for c in (materials.get("color_curves") or [])
                      if any(c.get(ch) for ch in ("red", "green", "blue", "luma")))
        for text in materials.get("texts") or []:
            fonts[text.get("font_name") or ""] += 1
            sizes[round(float(text.get("font_size", 0)), 1)] += 1
            colors[(text.get("text_color") or "").lower()] += 1
            strokes[round(float(text.get("border_width", 0)), 3)] += 1
        for animation in materials.get("material_animations") or []:
            for item in animation.get("animations") or []:
                anims[item.get("name")] += 1
        for track in draft.get("tracks", []):
            if track.get("type") == "text":
                for segment in track.get("segments", []):
                    ypos[round(segment["clip"]["transform"]["y"], 2)] += 1
        projects.append({"name": folder.name, "grade": own_grade})

    def top(counter: Counter, n: int = 5) -> list[dict[str, Any]]:
        return [{"value": v, "used": c} for v, c in counter.most_common(n) if v not in ("", None)]

    return {
        "projects": len(projects),
        "color_grade": {k: top(v) for k, v in grade.items()},
        "color_curves_projects": curves,
        "subtitle": {
            "font": top(fonts), "size": top(sizes, 6), "color": top(colors),
            "stroke": top(strokes), "y": top(ypos, 6),
        },
        "animations": top(anims, 6),
        "suggested": {
            "sub_size": (sizes.most_common(1) or [(18.0, 0)])[0][0],
            "sub_color": (colors.most_common(1) or [("#ffffff", 0)])[0][0] or "#ffffff",
            "sub_stroke": (strokes.most_common(1) or [(0.05, 0)])[0][0],
            "sub_y": (ypos.most_common(1) or [(-0.60, 0)])[0][0],
        },
        "styles": _styles_by_series(root),
    }


def _styles_by_series(root: Path) -> list[dict[str, Any]]:
    """One ready-to-use style per project series (first word of the project names), since each
    client's look differs — a single global average would fit neither."""
    from collections import Counter, defaultdict

    groups: dict[str, dict[str, Counter]] = defaultdict(
        lambda: {"size": Counter(), "color": Counter(), "stroke": Counter(),
                 "y": Counter(), "font": Counter()})
    for folder in sorted(root.iterdir()):
        content = folder / "draft_content.json"
        if not content.is_file():
            continue
        try:
            draft = json.loads(content.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        series = folder.name.split()[0] if folder.name.split() else folder.name
        bucket = groups[series]
        used_ids = {seg["material_id"] for track in draft.get("tracks", [])
                    if track.get("type") == "text" for seg in track.get("segments", [])}
        for text in draft.get("materials", {}).get("texts") or []:
            if text.get("id") not in used_ids:
                continue
            bucket["size"][round(float(text.get("font_size", 0)), 1)] += 1
            bucket["color"][(text.get("text_color") or "#ffffff").lower()] += 1
            bucket["stroke"][round(float(text.get("border_width", 0)), 3)] += 1
            bucket["font"][text.get("font_name") or ""] += 1
        for track in draft.get("tracks", []):
            if track.get("type") == "text":
                for segment in track.get("segments", []):
                    bucket["y"][round(segment["clip"]["transform"]["y"], 2)] += 1

    styles = []
    for series, bucket in groups.items():
        if not bucket["size"]:
            continue
        def pick(name: str, default: Any) -> Any:
            common = bucket[name].most_common(1)
            return common[0][0] if common and common[0][0] not in ("", None) else default
        styles.append({
            "series": series,
            "font": pick("font", "DB Heavent Bd"),
            "sub_size": pick("size", 18.0),
            "sub_color": pick("color", "#ffffff"),
            "sub_stroke": pick("stroke", 0.05),
            "sub_y": pick("y", -0.60),
            "projects": sum(1 for f in root.iterdir()
                            if f.name.startswith(series) and (f / "draft_content.json").is_file()),
        })
    return sorted(styles, key=lambda s: -s["projects"])


# ── clip briefs written for an earlier job ───────────────────────────

_HOOK_RE = re.compile(r"ข้อความขึ้นจอ[^\n]*\n\s*[\"“]?(.+?)[\"”]?\s*\n", re.S)


def read_briefs(folder: str) -> list[dict[str, Any]]:
    """Read the per-clip notes of a 'For Cutting' style folder: one subfolder
    per clip, each with a รายละเอียด.txt that carries the hook line."""
    base = Path(folder)
    if not base.is_dir():
        raise VideoEditError(f"not a folder: {folder}")
    briefs = []
    for sub in sorted(base.iterdir()):
        if not sub.is_dir():
            continue
        note = next((p for p in sub.glob("*.txt")), None)
        if note is None:
            continue
        text = note.read_text(encoding="utf-8", errors="replace")
        hook = _HOOK_RE.search(text)
        title = text.splitlines()[0].strip() if text.strip() else sub.name
        videos = [p.name for p in sub.iterdir()
                  if p.suffix.lower() in VIDEO_SUFFIXES]
        briefs.append({
            "folder": str(sub),
            "name": sub.name,
            "title": title,
            "hook": hook.group(1).strip() if hook else "",
            "videos": videos,
            "notes_file": str(note),
        })
    return briefs


# ── folder browsing (so nobody has to type a path) ───────────────────

def list_drives() -> list[dict[str, Any]]:
    """Drive letters that currently exist on this machine."""
    drives = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{letter}:/")
        if root.exists():
            drives.append({"name": f"{letter}:", "path": str(root)})
    return drives


def browse(folder: str | None = None) -> dict[str, Any]:
    """One level of the filesystem: subfolders plus how many videos each holds."""
    if not folder:
        return {"path": "", "parent": None, "drives": list_drives(),
                "folders": [], "videos": 0}

    base = Path(folder)
    if not base.is_dir():
        raise VideoEditError(f"not a folder: {folder}")

    folders = []
    videos = 0
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except PermissionError as exc:
        raise VideoEditError(f"cannot open {folder}: {exc}") from exc

    for entry in entries:
        try:
            if entry.is_dir():
                if entry.name.startswith((".", "$")):
                    continue
                count = 0
                try:
                    count = sum(1 for child in entry.iterdir()
                                if child.is_file() and child.suffix.lower() in VIDEO_SUFFIXES)
                except (PermissionError, OSError):
                    count = -1        # unreadable, say so instead of pretending zero
                folders.append({"name": entry.name, "path": str(entry), "videos": count})
            elif entry.suffix.lower() in VIDEO_SUFFIXES:
                videos += 1
        except OSError:
            continue

    return {
        "path": str(base),
        "parent": str(base.parent) if base.parent != base else None,
        "drives": list_drives(),
        "folders": folders,
        "videos": videos,
    }


def grab_frame(path: str, at: float = 1.0, width: int = 360) -> bytes:
    """One JPEG frame, for previewing subtitle styling over the real picture."""
    if not Path(path).is_file():
        raise VideoEditError(f"file not found: {path}")
    run = subprocess.run(
        [FFMPEG, "-v", "error", "-ss", f"{max(at, 0):.3f}", "-i", path,
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2", "-vcodec", "mjpeg",
         "-"], capture_output=True, creationflags=NO_WINDOW, timeout=60)
    if run.returncode != 0 or not run.stdout:
        raise VideoEditError(f"cannot read a frame from {Path(path).name}")
    return run.stdout


def search_terms(lines: list[str], per_line: int = 2) -> list[dict[str, Any]]:
    """Turn Thai subtitle lines into English stock-footage search terms.

    The local model does the translating; if it is not running the caller gets
    an error rather than a list of Thai words no stock site can match.
    """
    import urllib.request

    if not lines:
        return []
    numbered = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
    prompt = (
        "For each numbered Thai subtitle line, give English stock-footage search "
        f"terms ({per_line} short phrases, 2-4 words each, comma separated) that would "
        "find B-roll matching what is being said. Business/corporate context. "
        "Answer with the same numbering, one line each, no explanations.\n\n"
        f"{numbered}")
    payload = json.dumps({
        "model": LOCAL_LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 1200,
    }).encode("utf-8")
    request = urllib.request.Request(f"{LOCAL_LLM_URL}/chat/completions", data=payload,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise VideoEditError(f"local model not reachable at {LOCAL_LLM_URL}: {exc}") from exc

    content = re.sub(r"<think>.*?</think>", "", body["choices"][0]["message"]["content"], flags=re.S)
    out: dict[int, list[str]] = {}
    for line in content.splitlines():
        match = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
        if match:
            terms = [t.strip(" .-") for t in match.group(2).split(",") if t.strip(" .-")]
            if terms:
                out[int(match.group(1))] = terms[:per_line]
    return [{"line": lines[i], "terms": out.get(i + 1, [])} for i in range(len(lines))]


# ── which subtitle lines carry the punch ─────────────────────────────

_EMPHASIS_WORDS = (
    "ที่สุด", "ต้อง", "ห้าม", "อย่า", "สำคัญ", "จำไว้", "ไม่ได้", "เปลี่ยน",
    "บทเรียน", "ผิดพลาด", "ล้มเหลว", "สำเร็จ", "โอกาส", "ความเสี่ยง", "กำไร",
    "ขาดทุน", "เงิน", "ล้าน", "แสน", "เปอร์เซ็นต์", "เท่า", "อันดับ",
)


def suggest_emphasis(lines: list[str], limit_ratio: float = 0.25) -> list[dict[str, Any]]:
    """Guess which lines deserve the emphasised style.

    A line scores for numbers, a brand or English term, a punchy short length,
    and for words that carry a lesson. The top quarter is marked — a rough first
    pass meant to be corrected by hand, not a verdict.
    """
    scored = []
    for index, raw in enumerate(lines):
        text = " ".join(str(raw).split())
        score = 0.0
        if re.search(r"\d", text):
            score += 2.0
        if re.search(r"[A-Za-z]{3,}", text):
            score += 1.0
        words = len(text)
        if words <= 22:
            score += 1.5
        elif words <= 32:
            score += 0.5
        score += sum(1.2 for word in _EMPHASIS_WORDS if word in text)
        if text.endswith("?") or "ไหม" in text or "ทำไม" in text:
            score += 1.0
        scored.append({"index": index, "text": text, "score": round(score, 2)})

    keep = max(1, int(round(len(lines) * limit_ratio)))
    threshold = sorted((s["score"] for s in scored), reverse=True)[:keep]
    cut = threshold[-1] if threshold else 0
    for item in scored:
        item["emphasis"] = bool(item["score"] >= cut and item["score"] >= 2.0)
    return scored
