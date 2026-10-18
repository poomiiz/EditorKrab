"""Export a ClipKit project to MP4 without opening CapCut: the kept pieces of the raw clip joined in timeline
order, on the project's own canvas, with its subtitles drawn in (text, size, colour, outline, place, tilt).

Round 1 covers what ClipKit itself makes: the main video track and the main text track. Extra tracks
(b-roll, music, effects added by hand in CapCut) are reported, not silently dropped.
"""
from __future__ import annotations

import atomic
import json
import cue_data
import subtitle_plan
import motion_plan
import math
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import capcut_edit
import video_edit
from insert_geometry import RAMP_IN, RAMP_FROM, consumption
from video_edit import FFMPEG, VideoEditError, probe

US = 1_000_000


def visible_spans(a: float, b: float, motions: list[dict], *, role: str | None = None,
                  cue_id: str | None = None) -> list[tuple[float, float]]:
    """Subtract only the motion's declared cue/role; old MOVs retain their legacy policy."""
    spans = [(a, b)] if b > a else []
    for motion in motions:
        if not motion_plan.hides(motion, role=role, cue_id=cue_id):
            continue
        start, end = motion["start"], motion["start"] + motion["duration"]
        spans = [(x, y) for lo, hi in spans for x, y in
                 ((lo, min(hi, start)), (max(lo, end), hi)) if y > x]
    return spans


def check_motions(motions: list[dict], duration: float) -> None:
    if not isinstance(motions, list):
        raise VideoEditError("motion list must be an array")
    for motion in motions:
        if not isinstance(motion, dict) or any(isinstance(motion.get(k), bool) or not isinstance(motion.get(k), (int, float))
                                             or not math.isfinite(motion[k]) for k in ("start", "duration")):
            raise VideoEditError("motion start and duration must be finite seconds")
        motion_plan.ownership(motion)
    previous = []
    for motion in motions:
        a, b = motion["start"], motion["start"] + motion["duration"]
        if a < 0 or b <= a or b > duration + 0.05:
            raise VideoEditError("motion is outside the timeline; adjust it before export")
        for other in previous:
            if other["start"] < b and a < other["start"] + other["duration"]:
                if any(not isinstance(m.get("recipe"), dict) or m["recipe"].get("version") != 1 for m in (motion, other)):
                    raise VideoEditError("overlapping motion needs verified recipes; recreate legacy media")
                motion_plan.check_overlap(motion, other)
        if not isinstance(motion.get("file"), str) or not Path(motion["file"]).is_file():
            raise VideoEditError("motion file is missing")
        info = probe(motion["file"])
        if motion["duration"] > info["duration"] + 0.05:
            raise VideoEditError("motion duration exceeds its media; adjust it before export")
        previous.append(motion)
CAPCUT_PX = 5.2  # pixels per CapCut font-size unit per 1080 px of the canvas short side (same number the editor preview uses)
FONTS = Path(__file__).resolve().parents[1] / "fonts"
DEFAULT_FONT = FONTS / "Kanit-Bold.ttf"  # Google Fonts, OFL: shipped with ClipKit so Thai always shapes
# TikTok / Reels / Shorts draw their buttons on the right and the caption + sound bar at the bottom:
# subtitles stay inside this box (fractions of the frame) so the app never covers them
SAFE_W, SAFE_TOP, SAFE_BOTTOM = 0.78, 0.11, 0.77
# where each app's own bar, buttons and caption cover a 9:16 video, as fractions of the frame: side margin, top bar,
# bottom of the free area, and the lower-right button column (width, from y down; drawn in the editor, not enforced).
# "general" is ClipKit's box for ordinary posts; the app boxes follow each app's ad spec overlay for 1080x1920
# (adkit.so/tools/safe-zones, 2026-09): stricter, since ads add their own buttons at the bottom.
SAFE_ZONES = {
    "general": {"label": "โพสต์ทั่วไป (ทุกแอป)", "side": 0.11, "top": SAFE_TOP, "bottom": SAFE_BOTTOM, "col": None},
    "tiktok": {"label": "TikTok (ตามคู่มือโฆษณา)", "side": 0.111, "top": 0.125, "bottom": 0.656, "col": [0.389, 0.4375]},
    "reels": {"label": "Instagram Reels (ตามคู่มือโฆษณา)", "side": 0.06, "top": 0.14, "bottom": 0.65, "col": [0.27, 0.6]},
    "shorts": {"label": "YouTube Shorts (เทมเพลตทางการ)", "side": 0.178, "top": 0.15, "bottom": 0.65, "col": None},
    "all": {"label": "ทุกแอปพร้อมกัน (เข้มสุด)", "side": 0.178, "top": 0.15, "bottom": 0.65, "col": [0.389, 0.4375]},
}
LINE_H = 1.2  # line height in font sizes; CapCut's line_spacing is this minus 1.18 (its default 0.02 looks like 1.2)


def safe_box(style: dict) -> dict:
    """The safe box picked for this clip (clipkit_style.json safe_zone), with w = the width text may use."""
    box = SAFE_ZONES.get(style.get("safe_zone") or "general", SAFE_ZONES["general"])
    return {**box, "w": 1 - 2 * box["side"]}
LOUDNESS = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"  # every export at the level the platforms play (-14 LUFS)
# a few ms of fade on both sides of every jump cut: a cut mid-waveform clicks
EDGE_FADE = 0.008


def _video_encoder(preview: bool = False) -> list[str]:
    """Explicit final H.264 encoder; FFmpeg errors are reported without switching hardware."""
    encoder = video_edit.kitconfig._load().get("render_encoder", "libx264")
    if encoder == "libx264":
        options = ["-preset", "ultrafast" if preview else "veryfast", "-crf", "28" if preview else "20"]
    elif encoder == "h264_nvenc":
        options = ["-preset", "p1" if preview else "p4", "-rc", "vbr", "-cq", "28" if preview else "20", "-b:v", "0"]
    elif encoder == "h264_videotoolbox":
        options = ["-allow_sw", "0", "-b:v", "3M" if preview else "12M"]
    else:
        raise VideoEditError("render_encoder must be libx264, h264_nvenc or h264_videotoolbox")
    return ["-c:v", encoder, *options, "-pix_fmt", "yuv420p"]


def edge_fades(dur: float) -> str:
    """afade pair for one kept piece of audio (no click where two pieces meet)."""
    return f"afade=t=in:d={EDGE_FADE},afade=t=out:st={max(0.0, dur - EDGE_FADE):.3f}:d={EDGE_FADE}"


def normalize_loudness(mp4: Path) -> None:
    """Re-level the sound of a finished MP4 in place (picture copied, not re-encoded)."""
    tmp = mp4.with_name(mp4.stem + ".loud.mp4")
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(mp4), "-map", "0:v", "-map", "0:a", "-c:v", "copy",
                        "-af", LOUDNESS, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    if r.returncode or not tmp.is_file():
        raise VideoEditError("loudness pass failed: " + r.stderr.strip()[-400:])
    tmp.replace(mp4)


def _font(path: str | None) -> tuple[str, Path]:
    """Configured fonts must exist; use bundled Kanit only when no font is configured."""
    from fontTools.ttLib import TTFont
    f = Path(path) if path and Path(path).is_file() else DEFAULT_FONT
    if not f.is_file():
        raise VideoEditError(f"font file missing: {f}")
    return TTFont(str(f), fontNumber=0)["name"].getDebugName(1), f


# where a Thai line may break: a new thought may start a line, a closing particle never does
OPENERS = {"แต่", "และ", "ก็", "คือ", "เพราะ", "ซึ่ง", "แล้วก็", "หรือ", "ถ้า", "เลยทำให้", "ดังนั้น", "ส่วน",
           "ว่า", "ที่", "เพื่อ", "โดย", "จน", "ทำให้"}
TAILS = {"ครับ", "ค่ะ", "คะ", "นะ", "จ้า", "จ้ะ", "เลย", "ด้วย", "กัน", "ไหม", "มั้ย", "เหรอ", "หรอ", "แหละ", "น่ะ", "ล่ะ",
         "สิ", "ซะ", "บาท", "คน", "ครั้ง", "เท่า", "เปอร์เซ็นต์", "ล้าน", "แสน", "หมื่น", "พัน", "ร้อย", "ปี", "เดือน", "วัน",
         "ชั่วโมง", "นาที"}  # particles and units: never the first word of a line
PREFIXES = {"การ", "ความ", "ผู้", "นัก", "น่า"}  # bind to the next word: never the last word of a line


def unbreak(text: str) -> str:
    """A subtitle on one line again: a break between Thai letters joins them (Thai has no spaces), others become one."""
    return " ".join(re.sub(r"(?<=[\u0e00-\u0e7f])\s*\n\s*(?=[\u0e00-\u0e7f])", "", text).split())


def _key_word(tok: str) -> bool:
    """A number, an English term or a lesson word: worth starting a line with."""
    return bool(re.search(r"\d|[A-Za-z]{2,}", tok)) or tok in video_edit._EMPHASIS_WORDS


def _best_break(words: list[str], width, max_w: float) -> int:
    """Index in words (word_tokenize output) to break a line at, 0 if none: among breaks that fit, one where the
    thought breaks (a space between phrases, punctuation, before a joining or key word), never ending line one on a
    joining word or prefix (การ/ความ) or starting line two with a particle or unit (ครับ/บาท), never leaving a scrap
    line under 40% of the other; then the most balanced. The base ranking was checked on 398 wrapped lines from
    20 real projects."""
    def rank(i: int) -> tuple:
        a, b = "".join(words[:i]).strip(), "".join(words[i:]).strip()
        wa, wb = width(a), width(b)
        tail = next(w.strip() for w in reversed(words[:i]) if w.strip())
        head = next(w.strip() for w in words[i:] if w.strip())
        good = words[i - 1].isspace() or head in OPENERS or _key_word(head) or a[-1:] in ",.!?…"
        bad = tail in OPENERS or tail in PREFIXES or head in TAILS  # worse than a short line
        return max(wa, wb) > max_w, bad, min(wa, wb) < 0.4 * max(wa, wb), not good, max(wa, wb)
    cuts = [i for i in range(1, len(words)) if "".join(words[:i]).strip() and "".join(words[i:]).strip()]
    return min(cuts, key=rank) if cuts else 0


def split_sub(text: str, spoken: list | None, t0: float, t1: float, font_file: Path, px: float, max_w: float,
              mode: str = "lines") -> list[tuple[float, float, str, list, float]]:
    """One subtitle that may be too wide -> [(start, end, text, spoken words, size factor)]. It stays as is when it
    fits one line; else it breaks at the best Thai word break into two lines (mode "lines") or into a new subtitle
    from that point, timed by the spoken words (when two lines still overflow, or mode "split"). Mode "shrink" keeps
    one line down to 3/4 size, then breaks like "lines". A piece too short to stand alone (< 0.6 s) stays two
    lines; the factor shrinks what still overflows."""
    from PIL import ImageFont
    from pythainlp.tokenize import word_tokenize
    font = ImageFont.truetype(str(font_file), 100)
    width = lambda t: font.getlength(t) * px / 100  # noqa: E731
    flat = unbreak(text)
    fit = lambda lines: min(1.0, max_w / max(width(t) for t in lines))  # noqa: E731
    if "\n" in text.strip() and mode != "split" and fit(text.strip().split("\n")) == 1.0:
        return [(t0, t1, text.strip(), spoken or [], 1.0)]  # line breaks someone chose, and they fit
    words = word_tokenize(flat, keep_whitespace=True)
    if mode == "shrink" and fit([flat]) >= 0.75:
        return [(t0, t1, flat, spoken or [], fit([flat]))]
    mode = "lines" if mode == "shrink" else mode  # shrinking more than a quarter is unreadable: break it instead
    i = 0 if width(flat) <= max_w else _best_break(words, width, max_w)
    if not i:
        return [(t0, t1, flat, spoken or [], fit([flat]))]
    left, right = "".join(words[:i]).strip(), "".join(words[i:]).strip()
    at = _clock(spoken, t1 - t0)(len("".join(words[:i])) / len(flat))
    if (mode == "lines" and fit([left, right]) == 1.0) or at < 0.6 or t1 - t0 - at < 0.6:
        return [(t0, t1, left + "\n" + right, spoken or [], fit([left, right]))]
    early = [w for w in spoken or [] if w[0] < at]
    late = [[round(a - at, 2), round(b - at, 2), w] for a, b, w in spoken or [] if a >= at]
    return (split_sub(left, early, t0, t0 + at, font_file, px, max_w, mode)
            + split_sub(right, late, t0 + at, t1, font_file, px, max_w, mode))


def carry_joiners(rows: list[dict]) -> int:
    """Subtitles that run straight on (< 0.3 s apart) read as one sentence: a joining word left at the end of one
    moves to the start of the next, a closing particle at the start of the next moves back. rows: start, end,
    text, spoken (word times from the row's start); changed in place. Returns how many moved."""
    from pythainlp.tokenize import word_tokenize
    moved = 0
    for a, b in zip(rows, rows[1:]):
        if b["start"] - a["end"] >= 0.3:
            continue
        wa = word_tokenize(unbreak(a["text"]), keep_whitespace=True)
        wb = word_tokenize(unbreak(b["text"]), keep_whitespace=True)
        joined, n = wa + wb, len(wa)
        if sum(1 for w in wa if w.strip()) > 1 and wa[-1].strip() in OPENERS:
            at = _clock(a["spoken"], a["end"] - a["start"])(len("".join(wa[:-1])) / len("".join(wa)))
            if at < 0.6:
                continue
            t = round(a["start"] + at, 2)  # the joining word and its time go to the next line
            b["spoken"] = ([[round(x - at, 2), round(y - at, 2), w] for x, y, w in a["spoken"] or [] if x >= at]
                           + [[round(x + b["start"] - t, 2), round(y + b["start"] - t, 2), w]
                              for x, y, w in b["spoken"] or []])
            a["spoken"] = [w for w in a["spoken"] or [] if w[0] < at]
            a["end"], b["start"], cut = t, t, n - 1
        elif sum(1 for w in wb if w.strip()) > 1 and wb[0].strip() in TAILS:
            cut = n + 1  # a particle is said fast: only its text moves back, the times stay
        else:
            continue
        a["text"], b["text"] = "".join(joined[:cut]).strip(), "".join(joined[cut:]).strip()
        moved += 1
    return moved


def _fit(text: str, font_file: Path, size: float, max_w: float, one: bool = False) -> tuple[list[str], float]:
    """Keep a subtitle inside the frame: one line when it fits, else two lines split where the thought breaks
    (_best_break); only if no break fits, a smaller size."""
    from PIL import ImageFont
    from pythainlp.tokenize import word_tokenize
    font = ImageFont.truetype(str(font_file), 100)
    width = lambda t: font.getlength(t) * size / 100  # noqa: E731
    if one:  # white + colour pair: one line each so the screen never holds more than 2; smaller when long
        lines = [unbreak(text)]
    elif "\n" in text:  # the editor already chose the line breaks
        lines = text.split("\n")
    elif width(text) <= max_w:
        return [text], size
    else:
        words = word_tokenize(text, keep_whitespace=True)
        i = _best_break(words, width, max_w)
        lines = ["".join(words[:i]).strip(), "".join(words[i:]).strip()] if i else [text]
    widest = max(width(t) for t in lines)
    return lines, size if widest <= max_w else size * max_w / widest


def one_line(text: str, font_file: Path, px: float, max_w: float, a: float, b: float, one: bool = False,
             fixed: bool = False) -> list[tuple[str, float, float, float]]:
    """A subtitle that never takes two lines and never leaves the frame: text too wide for one line becomes
    pieces shown one after another over a..b (cut where the thought breaks, _best_break), each in time with its
    share of the letters. Only a single word wider than the frame is drawn smaller. [(piece, start, end, px)]"""
    from PIL import ImageFont
    from pythainlp.tokenize import word_tokenize
    font = ImageFont.truetype(str(font_file), 100)
    width = lambda t: font.getlength(t) * px / 100  # noqa: E731
    if fixed:
        whole = unbreak(text)
        if width(whole) > max_w:
            raise VideoEditError(f"ข้อความยาวเกินขนาดคงที่ · แบ่งตามความหมายหรือย่อ show: {whole}")
        return [(whole, a, b, px)] if whole and b > a else []
    if one:
        whole = unbreak(text)
        return [(whole, a, b, min(px, px * max_w / width(whole)) if width(whole) else px)] if whole and b > a else []

    def cut(words: list[str]) -> list[str]:
        whole = "".join(words).strip()
        if width(whole) <= max_w or len([w for w in words if w.strip()]) < 2:
            return [whole] if whole else []
        i = _best_break(words, width, max_w) or len(words) // 2
        return cut(words[:i]) + cut(words[i:])

    pieces = cut(word_tokenize(unbreak(text), keep_whitespace=True))
    total = sum(len(x.replace(" ", "")) for x in pieces) or 1
    out, t = [], a
    for x in pieces:
        end = t + (b - a) * len(x.replace(" ", "")) / total
        out.append((x, t, end, min(px, px * max_w / width(x)) if width(x) else px))
        t = end
    return out


def _json(path: Path, empty: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else empty


def _clock(spoken: list | None, length: float, text: str | None = None):
    """Look up recorded word times; align corrected spelling before lookup instead of stretching the clock."""
    if not spoken:
        return lambda f: f * length
    marks, n, acc = [], sum(len(w) for _, _, w in spoken) or 1, 0
    for a, _, w in spoken:
        marks.append((acc / n, a))
        acc += len(w)
    marks.append((1.0, spoken[-1][1]))
    clock = lambda f: next((t for p, t in reversed(marks) if p <= f), 0.0)
    heard = "".join(w for _, _, w in spoken)
    wanted = "".join(text.split()) if text is not None else heard
    if wanted == heard:
        return clock
    import difflib
    edits = difflib.SequenceMatcher(a=heard, b=wanted, autojunk=False).get_opcodes()

    def corrected(f):
        position = round(f * len(wanted))
        for op, a, b, c, d in edits:
            if c <= position < d:
                return clock((a + position - c if op == "equal" else a) / n)
        return clock(1.0)
    return corrected


# the sentence-pair look: each spoken phrase becomes a normal line plus a bigger emphasis line, optionally with a
# small translated caption at the bottom. How they look is a preset each user makes (presets/<name>.json, by chat
# or from one of their own CapCut projects: scripts/preset_from_capcut.py); "default" ships with ClipKit.
PRESETS = Path(__file__).resolve().parents[1] / "presets"


def _rgb(hexcolor: str) -> list[float]:
    h = hexcolor.lstrip("#")
    if len(h) != 6:
        raise VideoEditError(f"preset colour must be #rrggbb: {hexcolor}")
    return [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]


def preset_names() -> list[str]:
    return sorted(p.stem for p in PRESETS.glob("*.json"))


def pair_look(style: dict, W: int | None = None, H: int | None = None) -> dict[str, Any]:
    """The project's preset as (size, y, fill, outline colour, outline width) per role: lead (normal text),
    punch (emphasis), caption (or None)."""
    name = style.get("preset") or "default"
    f = PRESETS / f"{name}.json"
    if not f.is_file():
        raise VideoEditError(f"preset '{name}' not found in {PRESETS} (have: {', '.join(preset_names())})")
    try:
        p = json.loads(f.read_text(encoding="utf-8"))
        def part(role: dict) -> tuple:
            values = (float(role["size"]), float(role["y"]), _rgb(role["color"]), _rgb(role["outline"]), float(role["outline_width"]))
            if not all(math.isfinite(v) for v in (values[0], values[1], values[4], *values[2], *values[3])) or values[0] <= 0 or values[4] < 0:
                raise ValueError("role size/position/outline must be finite, with positive size")
            return values
        second = p.get("second")
        if not isinstance(p.get("fixed_size", False), bool) or p.get("font") is not None and not isinstance(p["font"], str):
            raise ValueError("fixed_size must be boolean and font must be a path")
        if second and not all(math.isfinite(float(second.get(k, default))) for k, default in (("x", .3), ("rotation", -6))):
            raise ValueError("second position/rotation must be finite")
        look = {"lead": part(p["normal"]), "punch": part(p["emphasis"]), "fixed_size": bool(p.get("fixed_size")),
                "_style": dict(style), "_canvas": (W, H),
                "font": p.get("font"),
                "caption": part(p["caption"]) if p.get("caption") else None,
                "caption_lang": (p.get("caption") or {}).get("language", "en"),
                "caption_shadow": bool((p.get("caption") or {}).get("shadow")),
                # the second person's short reactions: off the centre line and tilted, for movement on screen
                "second": part(second) if second else None,
                "second_at": (float(second.get("x", 0.3)), float(second.get("rotation", -6))) if second else None}
        if W and H:
            ratio_scale = min(W, H * 1080 / 1920) / min(W, H)
            for role in ("lead", "punch", "caption", "second"):
                if look[role]:
                    values = look[role]
                    look[role] = (values[0] * ratio_scale, *values[1:])
            box = safe_box(style)
            scale = CAPCUT_PX * min(W, H) / 1080
            heights = [look[role][0] * scale * LINE_H * (1.3 if role == "punch" else 1)
                       + 2 * (look[role][0] * scale * look[role][4] * .6 + 4) for role in ("lead", "punch")]
            gap = max(8, min(W, H) * 0.012)
            if sum(heights) + gap > H * (box["bottom"] - box["top"]):
                raise VideoEditError("ขนาดตัวปกติและคำเน้นใหญ่เกินพื้นที่ · ลดขนาดใน preset ก่อนส่งออก")
            normal = H / 2 - look["lead"][1] * H / 2
            punch = H / 2 - look["punch"][1] * H / 2
            if not look["fixed_size"]:
                punch = max(punch, normal + sum(heights) / 2 + gap)
            centers = {"lead": normal, "punch": punch}
            if look["caption"]:
                caption_h = look["caption"][0] * scale * LINE_H + 2 * (look["caption"][0] * scale * look["caption"][4] * .6 + 7)
                centers["caption"] = H / 2 - look["caption"][1] * H / 2
                if not look["fixed_size"]:
                    centers["caption"] = max(centers["caption"], punch + (heights[1] + caption_h) / 2 + gap)
            role_heights = dict(zip(("lead", "punch"), heights))
            if look["caption"]:
                role_heights["caption"] = caption_h
            bottom = max(center + role_heights[role] / 2 for role, center in centers.items())
            shift = max(0, bottom - H * box["bottom"])
            for role, center in centers.items():
                values = look[role]
                look[role] = (values[0], 1 - 2 * (center - shift) / H, *values[2:])
        return look
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VideoEditError(f"preset {f.name} is broken: {exc}") from exc


RED = [0.93, 0.11, 0.11]


def _shown(lead: str, punch: str, opts: dict | None, prev_white: str) -> tuple[str, str, list | None]:
    """What a phrase puts on screen, from the agent's choice in clipkit_punch.json (4th item, optional), the
    options measured from real hand-edited clips: "pair" white lead + coloured punch (default), "white" white
    only, "color" coloured only, "red" punch in red for the strongest point, "hold" keeps the previous white line
    while the coloured line changes (lists), "skip" no subtitle (filler). "show": [white, colour] = shorter
    rewritten words on screen (timing still comes from the spoken words). "color": "#rrggbb" = this line's own
    emphasis colour. "second": a short line from the second person (pair_look "second"). Returns (white, colour, colour rgb)."""
    o = opts or {}
    w, c = o.get("show") or (lead, punch)
    look = o.get("look", "pair")
    rgb = RED if look == "red" else (_rgb(o["color"]) if o.get("color") else None)  # "color": "#rrggbb" = this line's own
    if look == "skip":
        return "", "", None
    if look == "white":
        return " ".join(x for x in (w, c) if x), "", None
    if look == "color":
        return "", " ".join(x for x in (w, c) if x), rgb
    return (prev_white if look == "hold" else w), c, rgb


def upper_row(look: dict, W: int, H: int) -> float:
    """y of the first of two stacked emphasis beats: just above the second, never on it, and no lower than the
    normal text's row."""
    size, y = look["punch"][:2]
    scale = CAPCUT_PX * min(W, H) / 1080
    height = size * scale * LINE_H * 1.3 + 2 * (size * scale * look["punch"][4] * .6 + 4)
    return max(look["lead"][1], y + 2 * (height + max(8, min(W, H) * .012)) / H)


def stacks(looks: list[str | None]) -> dict[int, int]:
    """Emphasis-only phrases in a row come in two beats, one row each: {first: second}. The first goes on the upper
    row and stays until the second is done; then the next pair starts. A held white line is not stacked: there the
    emphasis replaces the one before."""
    out, n = {}, 0
    while n + 1 < len(looks):
        if looks[n] == "color" and looks[n + 1] == "color":
            out[n] = n + 1
            n += 2
        else:
            n += 1
    return out


# the clip title over the first seconds, measured from hand-edited clips: one or two lines, each line white,
# orange or red (any mix: red/white, white/orange, orange/white ...), coloured text outlined white, white outlined black
HOOK_COLORS = {"white": ([1, 1, 1], [0, 0, 0], 0.05), "orange": ([1, 0.49, 0], [1, 1, 1], 0.06), "red": (RED, [1, 1, 1], 0.06)}
HOOK_DUR = 4.0
# skin smoothing like CapCut's "skin" slider (0.6 = a typical hand-edited setting): an edge-keeping (bilateral) blur
# mixed over the footage at that strength
SKIN_FILTER = "split[sk1][sk2];[sk2]bilateral=sigmaS=8:sigmaR=0.08[sk3];[sk1][sk3]blend=all_opacity={skin:.2f}"
SKIN_DEFAULT = 0.6


def _hook(folder: Path) -> list[dict[str, Any]]:
    """clipkit_hook.json = [{"text": ..., "color": "white|orange|red"}] (1-2 lines, written by the agent) as
    lines to draw: text, size, y, fill, stroke, width. No file = no title; a broken file raises."""
    raw = _json(folder / "clipkit_hook.json", None)
    if raw is None or raw == []:
        return []
    if not isinstance(raw, list) or not 1 <= len(raw) <= 2 or any(
            not isinstance(x, dict) or not str(x.get("text", "")).strip() or x.get("color") not in HOOK_COLORS for x in raw):
        raise VideoEditError('clipkit_hook.json must be 1-2 lines of {"text": ..., "color": "white|orange|red"}')
    # one centre line at y -0.37: a single title sits on it; with two, the first sits above it and the second
    # (bigger) hangs below it
    place = [(36, "mid")] if len(raw) == 1 else [(30, "up"), (36, "down")]
    return [{"text": x["text"].strip(), "size": s, "y": -0.37, "grow": g, "fill": HOOK_COLORS[x["color"]][0],
             "stroke": HOOK_COLORS[x["color"]][1], "width": HOOK_COLORS[x["color"]][2], "start": 0.0, "end": HOOK_DUR}
            for x, (s, g) in zip(raw, place)]


ZOOM, ZOOM_GAP = 1.08, 2.5


def progress_bar(vin: str, length: float, W: int, H: int) -> tuple[str, str]:
    """A bar along the bottom edge that fills from left to right over the clip (keeps people watching to the end):
    a white strip slid in from the left, as overlay works out x on every frame (drawbox does not).
    Returns (filter, output label)."""
    if length <= 0:
        raise VideoEditError("progress bar needs a clip longer than 0 s")
    h = max(4, round(H * 0.008))
    return (f";color=c=white:s={W}x{h}:d={length:.3f}[pbar];"
            f"{vin}[pbar]overlay=x='-w+w*t/{length:.3f}':y=H-h:eof_action=pass[pb]"), "[pb]"


EMOJI_DIR = Path(__file__).resolve().parents[1] / "emoji"  # Twemoji pictures (CC-BY 4.0), see THIRD_PARTY_NOTICES
EMOJI = {  # picture -> words that call it up: whole words after Thai word splitting, English in any case
    "1f4b0": ("เงิน", "รวย", "ล้าน", "บาท", "money", "rich"),
    "1f525": ("ไฟ", "ร้อน", "ปัง", "hot", "fire"),
    "2764": ("รัก", "love"),
    "1f602": ("ขำ", "ตลก", "ฮา", "funny", "lol"),
    "1f631": ("ตกใจ", "ช็อก", "shock", "wow"),
    "1f4a1": ("ไอเดีย", "เคล็ดลับ", "idea", "tip"),
    "23f0": ("เวลา", "time"),
    "2705": ("สำเร็จ", "ถูกต้อง", "success"),
    "274c": ("ผิดพลาด", "ล้มเหลว", "ห้าม", "mistake", "wrong"),
    "1f4c8": ("กำไร", "เพิ่มขึ้น", "growth", "profit"),
    "1f4c9": ("ขาดทุน", "ลดลง", "loss"),
    "1f680": ("พุ่ง", "launch", "fast"),
    "1f3af": ("เป้าหมาย", "goal", "target"),
    "1f35c": ("อาหาร", "อร่อย", "food"),
    "2708": ("เที่ยว", "เดินทาง", "travel", "trip"),
    "1f3e0": ("บ้าน", "home", "house"),
    "1f4aa": ("สู้", "ออกกำลังกาย", "strong", "workout"),
    "1f914": ("ทำไม", "สงสัย", "why"),
    "26a0": ("ระวัง", "อันตราย", "warning", "careful"),
    "1f389": ("ฉลอง", "ยินดี", "party"),
    "1f4f1": ("มือถือ", "แอป", "phone", "app"),
    "1f9e0": ("สมอง", "brain"),
    "2b50": ("ดีที่สุด", "best"),
}
WORD_EMOJI = {w: code for code, words in EMOJI.items() for w in words}
EMOJI_GAP = 2.5  # seconds between two emoji at least: one now and then reads as a reaction, every line as noise


def _emoji_for(word: str) -> str | None:
    """The emoji a word calls up: a keyword itself, or a Thai compound of a keyword and another real word
    ("ได้เงิน", "ไฟไหม้"), not a word that only starts with the letters ("ไฟล์", "รักษา")."""
    from pythainlp.corpus import thai_words
    if word in WORD_EMOJI:
        return WORD_EMOJI[word]
    for kw, code in WORD_EMOJI.items():
        rest = word[len(kw):] if word.startswith(kw) else word[:-len(kw)] if word.endswith(kw) else ""
        if rest and rest in thai_words():
            return code
    return None


def emoji_beats(phrases: list[tuple[float, float, str]], skip_until: float = 0) -> list[dict[str, Any]]:
    """An emoji that pops up as a phrase starts when one of its words is a keyword (EMOJI): the first match in a
    phrase, none in the clip title's seconds, and never closer than EMOJI_GAP to the one before."""
    from pythainlp.tokenize import word_tokenize
    beats: list[dict[str, Any]] = []
    for a, b, line in sorted(phrases):
        if a < skip_until or (beats and a - beats[-1]["at"] < EMOJI_GAP):
            continue
        code = next((c for w in word_tokenize(unbreak(line), keep_whitespace=False) if (c := _emoji_for(w.lower()))), None)
        if code:
            f = EMOJI_DIR / f"{code}.png"
            if not f.is_file():
                raise VideoEditError(f"emoji picture missing: {f}")
            beats.append({"at": round(a, 2), "dur": round(min(1.2, max(0.4, b - a)), 2), "file": str(f)})
    return beats


def emoji_box(W: int, H: int) -> tuple[int, int, int]:
    """(size, x, y) of an emoji: centred, in the upper third, clear of the subtitles lower down."""
    size = round(min(W, H) * 0.16)
    return size, (W - size) // 2, round(H * 0.3)


def emoji_overlay(vin: str, n: int, k: int, e: dict[str, Any], W: int, H: int) -> tuple[str, str]:
    """Input n (the emoji picture, looped) over vin for its beat: pops up from a little lower, fades out at the end.
    Returns (filter, output label)."""
    es, ex, ey = emoji_box(W, H)
    a, d = e["at"], e["dur"]
    return (f";[{n}:v]scale={es}:{es},format=rgba,trim=0:{d:.3f},setpts=PTS-STARTPTS+{a:.3f}/TB,"
            f"fade=t=in:st={a:.3f}:d=0.1:alpha=1,fade=t=out:st={a + d - 0.15:.3f}:d=0.15:alpha=1[em{k}];"
            f"{vin}[em{k}]overlay=x={ex}:y='{ey}+{es * 0.4:.0f}*max(0,1-(t-{a:.3f})/0.15)':eof_action=pass:"
            f"enable='between(t,{a:.3f},{a + d:.3f})'[eo{k}]"), f"[eo{k}]"


def speed_ramp(file: str, dur: float, cache: Path, *, source_start: float = 0, source_sha256: str,
               source_duration: float) -> str:
    """A muted copy of a b-roll clip, dur seconds long, that starts at RAMP_FROM x speed and slows evenly to 1x
    over RAMP_IN s (the vlog speed ramp). Made once per file and length, kept in cache."""
    import hashlib
    import insert_plan
    import os
    needed = consumption(dur, ramp=True)
    if source_start < 0 or source_start + needed > source_duration + 1e-6:
        raise VideoEditError("insert source is too short for this speed ramp; shorten it or choose another source range")
    if insert_plan.file_digest(Path(file)) != source_sha256:
        raise VideoEditError("insert source changed; register the current file before exporting")
    recipe = ["ramp-v2", source_sha256, source_start, dur, RAMP_IN, RAMP_FROM, 30, "libx264"]
    key = hashlib.sha256(json.dumps(recipe, separators=(",", ":")).encode()).hexdigest()
    dst = cache / f"ramp_{key}.mp4"
    manifest = dst.with_suffix(".json")
    if dst.is_file() and manifest.is_file():
        try:
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            if saved.get("recipe") == recipe and saved.get("sha256") == insert_plan.file_digest(dst):
                return str(dst)
        except (ValueError, OSError, AttributeError):
            pass  # A corrupt/incomplete managed cache entry is rebuilt, never exposed as a finished asset.
    R, S = RAMP_IN, RAMP_FROM
    used = (S + 1) * R / 2  # source seconds the ramp plays through
    # output time for source time T: the inverse of T = S*o - (S-1)*o^2/(2R) during the ramp, then 1:1
    pts = f"if(lt(T,{used:.4f}),({S}-sqrt({S * S}-{2 * (S - 1) / R:.4f}*T))*{R / (S - 1):.4f},T-{used - R:.4f})/TB"
    cache.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ramp-", suffix=".mp4", dir=cache)
    os.close(fd)
    tmp = Path(temporary)
    try:
        r = subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{source_start:.9f}", "-t", f"{needed:.9f}", "-i", file, "-an",
                            "-vf", f"setpts=PTS-STARTPTS,setpts='{pts}',fps=30,trim=0:{dur:.9f}",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(tmp)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        if r.returncode or not tmp.stat().st_size:
            raise VideoEditError("speed ramp failed: " + r.stderr.strip()[-300:])
        if insert_plan.file_digest(Path(file)) != source_sha256:
            raise VideoEditError("insert source changed while preparing the speed ramp")
        length = probe(str(tmp))["duration"]
        if abs(length - dur) > 1 / 30 + .01:
            raise VideoEditError("speed ramp duration does not match the planned insert")
        digest = insert_plan.file_digest(tmp)
        tmp.replace(dst)
        atomic.write_text(manifest, json.dumps({"recipe": recipe, "sha256": digest}, allow_nan=False))
    finally:
        tmp.unlink(missing_ok=True)
    return str(dst)


def insert_playback(path: str | Path, rows: list[dict], *, materialize: bool = False) -> list[dict]:
    """Every export uses the same source offset, derived ramp and canvas rectangle."""
    import insert_plan
    out = []
    for row in rows:
        item = dict(row)
        if insert_plan.file_digest(Path(item["file"])) != item["source_sha256"]:
            raise VideoEditError("insert source changed; register the current file before exporting")
        item["play"], item["media_start"] = item["file"], item["source_start"]
        if item.get("ramp") and item["kind"] == "video":
            if item["source_start"] + consumption(item["dur"], ramp=True) > item["source_duration"] + 1e-6:
                raise VideoEditError("insert source is too short for this speed ramp")
            item["derived_recipe"] = "ramp-v2"
            if materialize:
                item["play"] = speed_ramp(item["file"], item["dur"], Path(path) / "clipkit_ramp",
                    source_start=item["source_start"], source_sha256=item["source_sha256"], source_duration=item["source_duration"])
                item["media_start"] = 0.0
        out.append(item)
    return out


def zoom_spans(segs: list) -> list[tuple[float, float]]:
    """Zoom cut: the punch-in changes only on a real cut (a removed pause), so the jump looks meant; it flips
    100% <-> 108% at a cut only when the current framing has held for at least ZOOM_GAP s (no flicker on
    breath cuts close together). Returns the zoomed-in spans in timeline seconds."""
    starts = sorted(s["target_timerange"]["start"] / US for s in segs)
    end = max((s["target_timerange"]["start"] + s["target_timerange"]["duration"]) / US for s in segs)
    out, zoomed, since = [], False, 0.0
    for t in starts[1:] + [end]:
        if t - since >= ZOOM_GAP or t == end:
            if zoomed:
                out.append((round(since, 3), round(t, 3)))
            zoomed, since = not zoomed, t
    return out


def pair_events(phrases: list[dict], look: dict, W: int, H: int, fam: str, font_file: Path,
                *, captions: dict | None = None, held: dict | None = None, layout_errors: list | None = None) -> list[dict]:
    """Measure one immutable role plan. Adapters serialize these coordinates without fitting again."""
    from PIL import ImageFont
    context = held if held is not None else {"white": ""}
    box, scale = context.get("box") or safe_box({}), CAPCUT_PX * min(W, H) / 1080
    font = ImageFont.truetype(str(font_file), 100)
    metrics = font_cell_metrics(font_file)
    pairs = stacks([(p.get("opts") or {}).get("look") for p in phrases])
    out = []

    def line(role: str, words: str, part: tuple, a: float, b: float, cue_id: str | None,
             *, x: float = 0, rotation: float = 0, effect: str = "none") -> None:
        if not words.strip() or b <= a:
            return
        size, y, fill, stroke, width = part
        px = size * scale
        outline = px * width * .6 + 2
        shadow = 5 if role == "caption" and look.get("caption_shadow") else 2
        angle = abs(math.radians(rotation))
        # Reserve stroke and shadow as well as the rotated line. A fixed preset never shrinks.
        peak = 1.3 if effect == "pop" else 1.0
        projection = abs(math.cos(angle)) + abs(math.sin(angle))
        room = W * (box["w"] - abs(x)) / peak - 2 * (outline + shadow) * projection
        measured = font.getlength(unbreak(words)) / 100 * px
        height = px * LINE_H
        rotated_width = measured * abs(math.cos(angle)) + height * abs(math.sin(angle))
        if rotated_width > room + .01:
            if look.get("fixed_size") and layout_errors is None:
                raise VideoEditError(f"cue {cue_id or '(unbound)'} · {role}: ข้อความล้นขนาดคงที่ · ย่อ show หรือแบ่งตามขอบคำ")
            if not look.get("fixed_size"):
                px *= max(0, room) / max(rotated_width, 1)
                if px <= 0:
                    raise VideoEditError(f"cue {cue_id or '(unbound)'} · {role}: ไม่มีพื้นที่วางข้อความ")
                context.setdefault("shrunk", []).append({"at": a, "text": words})
                outline, measured, height = px * width * .6 + 2, font.getlength(unbreak(words)) / 100 * px, px * LINE_H
        bw = (measured * abs(math.cos(angle)) + height * abs(math.sin(angle)) + 2 * (outline + shadow) * projection) * peak
        bh = (height * abs(math.cos(angle)) + measured * abs(math.sin(angle)) + 2 * (outline + shadow) * projection) * peak
        cx = W / 2 + x * W / 2
        # Safe translation is role based, using the preset size, never a phrase's fitted size.
        static_height = (size * scale * LINE_H + 2 * (size * scale * width * .6 + 2 + shadow)) * peak
        cy = min(max(H / 2 - y * H / 2, H * box["top"] + static_height / 2), H * box["bottom"] - static_height / 2)
        if layout_errors is None and (cx - bw / 2 < W * (1 - box["w"]) / 2 - .05 or cx + bw / 2 > W * (1 + box["w"]) / 2 + .05
                or cy - bh / 2 < H * box["top"] - .05 or cy + bh / 2 > H * box["bottom"] + .05):
            raise VideoEditError(f"cue {cue_id or '(unbound)'} · {role}: ข้อความเกินพื้นที่ปลอดภัย")
        event = {"role": role, "cue_id": cue_id, "text": unbreak(words), "start": a, "end": b,
                 "size_px": px, "font_family": fam, "font_file": str(font_file), "center_x_px": cx,
                 "center_y_px": cy, "rotation": rotation, "fill": list(fill), "stroke": list(stroke),
                 "outline_px": outline, "shadow": shadow, "fixed_size": bool(look.get("fixed_size")),
                 "effect": effect, "peak_scale": peak, "bbox": [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2]}
        event.update(metrics)
        show_start = max(a, context.get("until", 0)) if role != "caption" else a
        for va, vb in visible_spans(show_start, b, context.get("motions", []), role=role, cue_id=cue_id):
            out.append(dict(event, start=va, end=vb, effect=effect if va == a else "none", effect_start=a))

    for n, phrase in enumerate(phrases):
        a, b, cue = phrase["start"], phrase["end"], phrase.get("cue_id")
        opts = phrase.get("opts") or {}
        white, colour, rgb = _shown(phrase["lead"], phrase["punch"], opts, context.get("white", ""))
        context["white"] = white or context.get("white", "")
        if white:
            line("normal", white, look["lead"], a, b, cue, effect="none" if opts.get("look") == "hold" else "fade")
        if colour:
            part = look["punch"] if rgb is None else look["punch"][:2] + (rgb,) + look["punch"][3:]
            end = b
            if n in pairs:
                part, end = part[:1] + (upper_row(look, W, H),) + part[2:], phrases[pairs[n]]["end"]
            # A color-only phrase includes the spoken lead, so it starts at that phrase's onset.
            line("emphasis", colour, part, phrase["hit"] if white else a, end, cue, effect="pop")
        if opts.get("second"):
            if not look.get("second"):
                raise VideoEditError(f"cue {cue}: preset ไม่มีตำแหน่ง second · เลือก preset ที่รองรับก่อน")
            x, rotation = look["second_at"]
            line("second", opts["second"], look["second"], a, b, cue, x=x, rotation=rotation, effect="fade")
    if look.get("caption"):
        groups = {}
        for p in phrases:
            key = cue_data.phrase_key(p)
            if key not in groups:
                groups[key] = [p["start"], p["end"], p["line"], p.get("cue_id")]
            groups[key][1] = max(groups[key][1], p["end"])
        for key, (a, b, source, cue) in groups.items():
            words = source if look.get("caption_lang") == "th" else (captions or {}).get(key)
            if isinstance(words, str) and words.strip():  # Missing/invalid translations stay absent in repair previews.
                line("caption", words, look["caption"], a, b, cue)
    return check_pair_events(out, W, H, box=box, layout_errors=layout_errors)


def _quads_intersect(first, second):
    for quad in (first, second):
        for a,b in ((quad[0],quad[1]), (quad[1],quad[2])):
            dx,dy = b[0]-a[0],b[1]-a[1]
            length = math.hypot(dx,dy)
            axis = (-dy/length,dx/length)
            one = [p[0]*axis[0]+p[1]*axis[1] for p in first]
            two = [p[0]*axis[0]+p[1]*axis[1] for p in second]
            if min(max(one),max(two)) - max(min(one),min(two)) <= .05:
                return False
    return True


def check_pair_events(events: list[dict], W: int, H: int, *, box: dict | None = None, layout_errors: list | None = None) -> list[dict]:
    """Read-only guard for a measured plan, including public motion requests before allocating a render."""
    from PIL import ImageFont
    box = box or safe_box({})
    measured, fonts = [], {}
    for event in events:
        if event["font_file"] not in fonts:
            fonts[event["font_file"]] = ImageFont.truetype(event["font_file"], 100)
        font = fonts[event["font_file"]]
        width = font.getlength(event["text"]) / 100 * event["size_px"]
        height = event["size_px"] * LINE_H
        angle, peak = math.radians(event["rotation"]), 1.3 if event["effect"] == "pop" else 1
        padding = 2 * (event["outline_px"] + event["shadow"])
        half_w, half_h = (width+padding)*peak/2, (height+padding)*peak/2
        x, y = event["center_x_px"], event["center_y_px"]
        if not all(math.isfinite(v) for v in (half_w,half_h,x,y)) or min(half_w,half_h) <= 0:
            raise VideoEditError("subtitle cell needs finite positive dimensions and a finite center")
        c,s = math.cos(angle), math.sin(angle)
        quad = [[x+dx*c-dy*s,y+dx*s+dy*c] for dx,dy in ((-half_w,-half_h),(half_w,-half_h),(half_w,half_h),(-half_w,half_h))]
        bounds = [min(p[0] for p in quad),min(p[1] for p in quad),max(p[0] for p in quad),max(p[1] for p in quad)]
        if (bounds[0] < W * (1 - box["w"]) / 2 - .05 or bounds[2] > W * (1 + box["w"]) / 2 + .05
                or bounds[1] < H * box["top"] - .05 or bounds[3] > H * box["bottom"] + .05):
            message = f"cue {event.get('cue_id')} · {event['role']}: ข้อความหรือ motion เกินพื้นที่ปลอดภัย · ย่อ show หรือปรับ preset"
            if layout_errors is None:
                raise VideoEditError(message)
            layout_errors.append({"code": "subtitle_bounds", "severity": "error", "cue_id": event.get("cue_id"),
                                  "role": event["role"], "start": event["start"], "end": event["end"], "bbox": bounds, "message": message})
        measured.append(dict(event, bbox=bounds, quad=quad))
    active = []
    for first in sorted(measured, key=lambda event: event["start"]):
        active = [event for event in active if event["end"] > first["start"] + 1e-6]
        for second in active:
            if (max(first["start"], second["start"]) < min(first["end"], second["end"]) - 1e-6
                    and _quads_intersect(first["quad"],second["quad"])):
                message = f"cue {first['cue_id']} · {first['role']}/{second['role']}: ข้อความทับกัน · ปรับตำแหน่งใน preset"
                if layout_errors is None:
                    raise VideoEditError(message)
                layout_errors.append({"code": "subtitle_overlap", "severity": "error", "cue_id": first["cue_id"],
                    "other_cue_id": second["cue_id"], "role": first["role"], "other_role": second["role"],
                    "start": max(first["start"], second["start"]), "end": min(first["end"], second["end"]),
                    "bbox": [first["bbox"], second["bbox"]], "message": message})
        active.append(first)
    return measured


def _resolved_pair_cues(rows: list[dict], words: dict, picks: dict, *, duration: float | None = None) -> list[dict]:
    ordered = sorted(rows, key=lambda row: row["start"])
    groups, seen = [], set()
    for n, row in enumerate(ordered):
        cue = row.get("id")
        key = cue_data.key(cue)
        if cue in seen:
            raise VideoEditError(f"cue {cue}: cue ID ซ้ำ")
        seen.add(cue)
        # First check against the original source cue. Extending the visual hold cannot legitimize bad word clocks.
        resolved = subtitle_plan.resolve(row["text"], picks.get(key), words.get(key), cue_id=cue,
                                         start=row["start"], end=row["end"])
        if n + 1 < len(ordered) and ordered[n + 1]["start"] < row["end"] - subtitle_plan.EPSILON:
            raise VideoEditError(f"cue {cue}: ช่วงซับทับกัน · แก้ช่วงก่อนส่งออก")
        groups.append(resolved)
    # Native rows can include pre-speech silence. The replacement appears at its real
    # first word, so keep the preceding text until that resolved onset, not row.start.
    for n, resolved in enumerate(groups):
        resolved[-1]["end"] = (groups[n + 1][0]["start"] if n + 1 < len(groups)
                               else max(ordered[n]["end"], duration or ordered[n]["end"]))
    return [phrase for group in groups for phrase in group]


def validate_pair_cues(rows: list[dict], words: dict, picks: dict, *, style: dict, W: int, H: int,
                       captions: dict | None = None, duration: float | None = None, layout_errors: list | None = None) -> list[dict]:
    """Read-only preflight shared by manual/Browser AI and every export, with the complete cue context."""
    look = pair_look(style, W, H)
    family, font = _font(look.get("font"))
    phrases = _resolved_pair_cues(rows, words, picks, duration=duration)
    return pair_events(phrases, look, W, H, family, font, captions=captions, held={"white": "", "box": safe_box(style)}, layout_errors=layout_errors)


def plain_events(draft: dict, rows: list[dict], words: dict, *, style: dict, duration: float) -> list[dict]:
    """Measure native still/pop text once; preserve its font, rotation, color and real cue clock."""
    from PIL import ImageFont
    W, H = draft["canvas_config"]["width"], draft["canvas_config"]["height"]
    box = safe_box(style)
    segments = {s["id"]: s for s in (capcut_edit._text_track(draft) or {}).get("segments", [])}
    index = capcut_edit._index(draft)
    phrases = _resolved_pair_cues(rows, words, {}, duration=duration)
    out = []
    for phrase in phrases:
        segment = segments[phrase["cue_id"]]
        material = index[segment["material_id"]][1]
        styles = json.loads(material["content"]).get("styles") or [{}]
        if len(styles) != 1:
            raise VideoEditError("plain subtitle has multiple styled ranges; split or normalize them before export")
        native = styles[0]
        requested_font = (native.get("font") or {}).get("path") or material.get("font_path")
        if requested_font and not Path(requested_font).is_file():
            raise VideoEditError("plain subtitle font file is missing; select an available font before export")
        family, file = _font(requested_font)
        font = ImageFont.truetype(str(file), 100)
        clip = segment["clip"]
        scale = clip["scale"]
        if (any(isinstance(scale.get(k), bool) or not isinstance(scale.get(k), (int,float))
                or not math.isfinite(scale[k]) or scale[k] <= 0 for k in ("x","y"))
                or abs(scale["x"] - scale["y"]) > 1e-6):
            raise VideoEditError("plain subtitle needs equal positive X/Y scale; normalize it before export")
        size = (material.get("font_size") or native.get("size") or 15) * CAPCUT_PX * min(W,H) / 1080 * clip["scale"]["x"]
        rotation = clip.get("rotation", 0)
        if not math.isfinite(size) or size <= 0 or not math.isfinite(rotation):
            raise VideoEditError("plain subtitle size/rotation must be finite and positive")
        cx, requested_y = W / 2 + clip["transform"].get("x",0) * W / 2, H / 2 - clip["transform"]["y"] * H / 2
        available = 2 * min(cx - W * (1-box["w"])/2, W * (1+box["w"])/2 - cx)
        if not math.isfinite(available) or not math.isfinite(requested_y) or available <= 0:
            raise VideoEditError("plain subtitle has no safe horizontal placement")
        peak = 1.3 if style.get("anim") == "pop" else 1
        strokes = native.get("strokes") or []
        rate = strokes[0].get("width",0) * .6 if strokes else 0
        if not math.isfinite(rate) or rate < 0:
            raise VideoEditError("plain subtitle outline must be finite and nonnegative")
        lines, px = _fit(phrase["line"], file, size, available / peak)
        angle = abs(math.radians(rotation))
        widest = max(font.getlength(line) for line in lines) / 100 * px
        pitch = (px * LINE_H + 2 * px * rate) * peak
        block_w, block_h = (widest + 2 * px * rate) * peak, len(lines) * pitch
        bw = block_w * abs(math.cos(angle)) + block_h * abs(math.sin(angle))
        bh = block_h * abs(math.cos(angle)) + block_w * abs(math.sin(angle))
        factor = min(1, available / max(bw,1), H * (box["bottom"] - box["top"]) / max(bh,1))
        px, pitch, bh = px * factor, pitch * factor, bh * factor
        cy = min(max(requested_y, H*box["top"] + bh/2), H*box["bottom"] - bh/2)
        fill = ((native.get("fill") or {}).get("content") or {}).get("solid",{}).get("color",[1,1,1])
        stroke = strokes[0]["content"]["solid"]["color"] if strokes else [0,0,0]
        for n, line in enumerate(lines):
            if not line.strip():
                continue
            out.append({"role":"normal","cue_id":phrase["cue_id"],"text":line,"start":phrase["start"],"end":phrase["end"],
                "size_px":px,"font_family":family,"font_file":str(file),
                "center_x_px":cx-math.sin(math.radians(rotation))*(n-(len(lines)-1)/2)*pitch,
                "center_y_px":cy+math.cos(math.radians(rotation))*(n-(len(lines)-1)/2)*pitch,"rotation":rotation,"fill":list(fill),"stroke":list(stroke),
                "outline_px":px*rate,"outline_extra_px":0,"shadow":0,"fixed_size":False,
                "effect":"pop" if peak>1 else "none","effect_start":phrase["start"],"peak_scale":peak})
    return check_pair_events(out,W,H,box=box)


def pair_events_from_timeline(timeline: dict, *, layout_errors: list | None = None) -> list[dict]:
    if "plain_events" in timeline:
        out = []
        for event in timeline["plain_events"]:
            for a,b in visible_spans(max(event["start"], HOOK_DUR if timeline.get("hook") else 0), event["end"],
                                     timeline.get("motions", []), role=event["role"], cue_id=event["cue_id"]):
                out.append(dict(event,start=a,end=b,effect=event["effect"] if a==event["start"] else "none"))
        return out
    if "subtitle_events" in timeline:
        return timeline["subtitle_events"]
    # Older timeline callers may already supply resolved phrases. They use the same planner once.
    look = pair_look(timeline["style"], timeline["width"], timeline["height"])
    family, font = _font(look.get("font"))
    return pair_events(timeline["phrases"], look, timeline["width"], timeline["height"], family, font,
                       captions=timeline.get("caption_en"), held={"white": "", "box": safe_box(timeline["style"]),
                       "until": HOOK_DUR if timeline.get("hook") else 0, "motions": timeline.get("motions", [])}, layout_errors=layout_errors)


def font_cell_metrics(font_file: Path) -> dict:
    """ASS uses the OS/2 Windows cell; CSS/Pillow font size uses em units.

    See libass/ass_font.c:set_font_metrics. This is a font-unit conversion, never a text fit.
    """
    from fontTools.ttLib import TTFont
    with TTFont(str(font_file), fontNumber=0) as font:
        units = font["head"].unitsPerEm
        hhea, os2 = font["hhea"], font.get("OS/2")
        asc, desc = (os2.usWinAscent, os2.usWinDescent) if os2 and os2.usWinAscent + os2.usWinDescent else (hhea.ascent, -hhea.descent)
        css_asc, css_desc = ((os2.sTypoAscender, -os2.sTypoDescender) if os2 and os2.fsSelection & 128
                             else (hhea.ascent, -hhea.descent))
        return {"font_cell_scale": (asc + desc) / units,
                "font_cell_center_delta": ((css_asc - css_desc) - (asc - desc)) / (2 * units)}


def _ass_text(text: str) -> str:
    """Keep displayed source text literal: ASS braces and backslash commands are not user formatting.

    libass escapes braces but not backslashes. An invisible word joiner prevents a literal
    backslash from binding to N/n/h or to an escaped brace, without changing visible glyphs.
    """
    return unbreak(text).replace("\\", "\\\u2060").replace("{", "\\{").replace("}", "\\}")


def subtitle_ass_header(width: int, height: int, family: str) -> str:
    # libass defaults kerning off; Pillow/Raqm and browser shaping enable it.
    return (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nKerning: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BorderStyle, Outline, "
            f"Shadow, Alignment, Encoding\nStyle: S,{family},60,&H00FFFFFF,&H00000000,1,0,0,5,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


def subtitle_ass(event: dict) -> str:
    extra = {"fade": "\\fad(80,0)", "pop": "\\fscx130\\fscy130\\t(0,140,\\fscx100\\fscy100)"}.get(event["effect"], "")
    metrics = event if "font_cell_scale" in event else font_cell_metrics(Path(event["font_file"]))
    font_size = event["size_px"] * metrics["font_cell_scale"]
    # Preserve the common logical line center despite the two font-cell definitions.
    center_y = event["center_y_px"] + event["size_px"] * metrics["font_cell_center_delta"]
    return (f"Dialogue: 0,{_ts(event['start'])},{_ts(event['end'])},S,,0,0,0,,{{\\an5"
            f"\\pos({event['center_x_px']:.3f},{center_y:.3f})\\frz{-event['rotation']:g}"
            f"\\fn{event['font_family']}\\fs{font_size:.3f}\\1c{_ass_color(event['fill'])}"
            f"\\3c{_ass_color(event['stroke'])}\\bord{event['outline_px']:.3f}\\shad{event['shadow']}{extra}}}{_ass_text(event['text'])}")


def _pair(text: str, spoken: list | None, t0: float, t1: float, W: int, H: int, fam: str, font_file: Path,
          look: dict, picked: list | None = None, marks: list | None = None, held: dict | None = None) -> list[str]:
    context = held if held is not None else {"white": ""}
    if look.get("_canvas") != (W, H) and "_style" in look:
        look = pair_look(look["_style"], W, H)
    phrases = subtitle_plan.resolve(unbreak(text), picked, spoken, cue_id=context.get("cue_id"), start=t0, end=t1)
    events = pair_events(phrases, look, W, H, fam, font_file, captions=context.get("en"), held=context)
    if marks is not None:
        for p in phrases:
            marks.append(("phrase", p["start"], p["end"], p["lead"], p["punch"], p["line"], p["k"], p["query"], p["opts"],
                          p["hit"], p["cue_id"], p["clock_status"]))
            if p["query"]:
                marks.append(("broll", p["start"], p["end"], p["query"]))
        marks.extend(("pop", event["start"]) for event in events if event["role"] == "emphasis" and event["effect"] == "pop")
    return [subtitle_ass(event) for event in events]


def _karaoke(lines: list[str], spoken: list | None, length: float, base: str, hl: str) -> list[tuple[float, float, str]]:
    """The whole line shows; the word being said turns the highlight colour, then back.
    Returned as back-to-back pieces (start, end, text in seconds from the line start), one per word, each with
    plain colour switches: libass chains two \\t colour changes on one word wrongly (unsaid words lit up).
    Word times come from the speech model (relative to the line start); the shown words can differ from the
    heard ones (fixed typos, English terms), so they are matched by position in the text, not by spelling."""
    from pythainlp.tokenize import word_tokenize
    total = sum(len(t) for t in lines) or 1
    at = _clock(spoken, length)
    toks, pos = [], 0  # (line no, text, start, end)
    for li, line in enumerate(lines):
        for tok in word_tokenize(line, keep_whitespace=True):
            a = at(pos / total)
            pos += len(tok)
            toks.append((li, tok, a, max(a + 0.08, at(pos / total))))

    def text(lit: int) -> str:
        parts = []
        for k, (li, tok, _, _) in enumerate(toks):
            if k and li != toks[k - 1][0]:
                parts.append("\\N")
            parts.append(f"{{\\1c{hl}}}{tok}{{\\1c{base}}}" if k == lit and tok.strip() else tok)
        return "".join(parts)

    words = [k for k, t in enumerate(toks) if t[1].strip()]
    if not words:
        return [(0.0, length, text(-1))]
    out = [(0.0, toks[words[0]][2], text(-1))]
    for n, k in enumerate(words):
        end = toks[words[n + 1]][2] if n + 1 < len(words) else toks[k][3]
        out.append((toks[k][2], end, text(k)))
    out.append((toks[words[-1]][3], length, text(-1)))
    return [(a, min(b, length), t) for a, b, t in out if min(b, length) - a > 0.005]


def _safe_y(cy: float, block: float, H: int, held: dict) -> float:
    """Centre y of a text block of height ~block moved inside the clip's safe box (held["box"]); counts each move."""
    half, box = block * 0.6, held.get("box") or safe_box({})
    safe = min(max(cy, H * box["top"] + half), H * box["bottom"] - half)
    if safe != cy:
        held["safe_moved"] = held.get("safe_moved", 0) + 1
    return safe


def _ass_color(rgb: list[float]) -> str:
    r, g, b = (max(0, min(255, round(c * 255))) for c in rgb[:3])
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _ts(t: float) -> str:
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


SFX = FONTS.parent / "sfx_builtin"  # made here with ffmpeg on first use: no licence to worry about
SFX_RECIPE = {
    "pop": "aevalsrc=0.6*sin(2*PI*(500+1400*t)*t)*exp(-28*t):d=0.14:s=44100",
    "whoosh": "anoisesrc=d=0.5:c=pink:a=0.6:r=44100,bandpass=f=1600:w=1400,afade=t=in:d=0.22,afade=t=out:st=0.22:d=0.28",
}


SFX_SUFFIXES = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")


def _own_sfx(kind: str) -> Path | None:
    """This machine's own sound for kind from the sfx folder in Settings: <sfx>/<kind>.<ext>, else the first file in
    <sfx>/<kind>/. Those files stay on this machine (many downloaded sounds may not be passed on), so none ship."""
    try:
        folder = Path(video_edit.kitconfig.need("sfx"))
    except RuntimeError:
        return None
    named = [f for f in sorted(folder.glob(f"{kind}.*")) if f.suffix.lower() in SFX_SUFFIXES]
    inside = [f for f in sorted((folder / kind).glob("*")) if f.suffix.lower() in SFX_SUFFIXES] if (folder / kind).is_dir() else []
    return (named or inside or [None])[0]


def sfx_len(kind: str, file: Path | None = None) -> float:
    """Seconds the sound for kind plays, at most 2."""
    return min(2.0, capcut_edit._music_meta(file if file is not None else _sfx(kind))["duration"] + 0.05)  # its 0.1 s rounding never cuts the tail


def _sfx(kind: str) -> Path:
    own = _own_sfx(kind)
    if own:
        return own
    f = SFX / f"{kind}.wav"
    if not f.is_file():
        SFX.mkdir(exist_ok=True)
        r = subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", SFX_RECIPE[kind], str(f)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode or not f.is_file():
            raise VideoEditError(f"could not make sound effect {kind}: {r.stderr[-300:]}")
    return f


def _music(style: dict[str, Any]) -> str | None:
    """The bed picked in the editor; with none picked, the first track of the music library (same pick every
    export), or no music when there is no library. style['music'] = '' turns music off."""
    if "music" in style:
        if style["music"] and not Path(style["music"]).is_file():
            raise VideoEditError(f"music file missing: {style['music']}")
        return style["music"] or None
    tracks = sorted(p for d in capcut_edit.music_dirs() for p in Path(d).rglob("*")
                    if p.suffix.lower() in capcut_edit.AUDIO_SUFFIXES)
    return str(tracks[0]) if tracks else None


def validate_output(file: Path, width: int, height: int, duration: float, *, preview: bool = False,
                    decode_log: Path | None = None) -> dict[str, Any]:
    """Probe and decode staged bytes; callers publish only after validation and input CAS succeed."""
    info = video_edit.probe(str(file))
    values = [info.get(key) for key in ("duration", "width", "height")]
    try:
        valid = all(not isinstance(value, bool) and isinstance(value, (int, float))
                    and math.isfinite(value) and value > 0 for value in values)
    except OverflowError:
        valid = False
    if not valid or not all(isinstance(info.get(key), str) and info[key] for key in ("video_codec", "audio_codec")):
        raise VideoEditError("ไฟล์ส่งออกไม่มีภาพ เสียง หรือความยาวที่ถูกต้อง")
    wrong_size = (info["width"], info["height"]) != (width, height)
    if preview:
        wrong_size = abs(info["width"] / info["height"] - width / height) > .005
    if wrong_size or abs(info["duration"] - duration) > .1:
        raise VideoEditError("ขนาดหรือความยาวไฟล์ส่งออกไม่ตรงกับโปรเจกต์")
    decoded = subprocess.run([FFMPEG, "-v", "error", "-xerror", "-i", str(file),
                              "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    if decode_log is not None:
        atomic.write_text(decode_log, (decoded.stdout or "") + "\n" + (decoded.stderr or ""))
    if decoded.returncode:
        raise VideoEditError("ตรวจไฟล์ส่งออกไม่ผ่าน: " + decoded.stderr[-800:])
    return info


def render_draft(path: str, out_dir: str, preview: bool = False, dry: bool = False, *, final: bool = False, repair: bool = False) -> dict[str, Any]:
    """Dry plans remain repairable; final=True applies delivery checks to a dry adapter plan."""
    encoder = _video_encoder(preview)
    folder, out = Path(path), Path(out_dir)
    target = out / f"{folder.name}{' - ตัวอย่าง' if preview else ''}.mp4"
    import web_ai_identity
    check_export = web_ai_identity.export_check(path, target) if not dry else None
    draft = capcut_edit._load(folder)
    import draft_quality
    quality = draft_quality.check(path)
    index = capcut_edit._index(draft)
    W, H = draft["canvas_config"]["width"], draft["canvas_config"]["height"]
    video = capcut_edit._video_track(draft)
    text = capcut_edit._text_track(draft)
    skipped = sum(1 for t in draft["tracks"] if t is not video and t is not text and t["segments"])

    segs = sorted(video["segments"], key=lambda s: s["target_timerange"]["start"])
    if not segs:
        raise VideoEditError("project has no video on its timeline")
    mats = {m["id"]: m for m in draft["materials"].get("videos", [])}
    src = mats[segs[0]["material_id"]]["path"]
    if any(mats[s["material_id"]]["path"] != src for s in segs):
        names = sorted({Path(mats[s["material_id"]]["path"]).name for s in segs})
        raise VideoEditError("โปรเจกต์นี้แทร็กหลักมีหลายไฟล์ (" + ", ".join(names[:3]) + ") แบบที่ตัดมือใน CapCut: "
                             "หน้าแก้คลิปของ EditorKrab เปิดได้เฉพาะคลิปที่ตัดจากไฟล์ดิบไฟล์เดียว ให้กดปุ่ม CapCut เพื่อแก้ต่อ "
                             "หรือสร้างโปรเจกต์ใหม่จากไฟล์ดิบ")
    if not Path(src).is_file():
        raise VideoEditError(f"raw file not found: {src}")
    info = probe(src)

    # footage placed the way CapCut shows it: fit inside the canvas, then the clip's own scale and offset
    clip = segs[0]["clip"]
    fit = min(W / info["width"], H / info["height"]) * clip["scale"]["x"]
    vw, vh = round(info["width"] * fit / 2) * 2, round(info["height"] * fit / 2) * 2
    ox = round((W - vw) / 2 + clip["transform"]["x"] * W / 2)
    oy = round((H - vh) / 2 - clip["transform"]["y"] * H / 2)

    parts, labels = [], []
    seek = min(s["source_timerange"]["start"] for s in segs) / US  # jump to the story, not decode from 0:00
    for i, s in enumerate(segs):
        a = s["source_timerange"]["start"] / US - seek
        b = a + s["source_timerange"]["duration"] / US
        parts.append(f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS[v{i}];"
                     f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,{edge_fades(b - a)}[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(segs)}:v=1:a=1[vc][ac];" + \
        f"[vc]scale={vw}:{vh}[vs];color=black:s={W}x{H}:r={info.get("fps") or 30}[bg];[bg][vs]overlay={ox}:{oy}:shortest=1[vo]"

    # subtitles: one ASS event per line, positioned and tilted like the CapCut segment
    events, font_files, family = [], set(), None
    olist = folder / "clipkit_overlays.json"
    overlays = json.loads(olist.read_text(encoding="utf-8")) if olist.is_file() else []
    check_motions(overlays, sum(s["target_timerange"]["duration"] for s in segs) / US)
    overlays = [dict(motion, review_required=True, legacy_full_card=True) if "ownership" not in motion else motion
                for motion in overlays]
    # subtitle look chosen in the editor: none (still), pop (bounce in), karaoke (the spoken word lights up)
    style = _json(folder / "clipkit_style.json", {})
    # colour: the editor's sliders (or the auto pick) on the footage only, never on text or b-roll
    skin = float(style.get("skin", 0))
    if skin > 0:  # smoother skin: an edge-keeping blur mixed in (eyes, hair and text edges stay sharp)
        graph = graph.replace("[vs];color=", f",{SKIN_FILTER.format(skin=skin)}[vs];color=", 1)  # after the size-down
    lab = "[vc]"
    c = style.get("color") or {}
    if c:
        w = float(c.get("warmth", 0))
        r, g, b = (float(c.get(k, 1)) for k in ("r", "g", "b"))  # white balance gains from the auto pick
        grade = (f"colorchannelmixer=rr={r:.3f}:gg={g:.3f}:bb={b:.3f},"
                 f"eq=brightness={float(c.get('brightness', 0)):.3f}:contrast={float(c.get('contrast', 1)):.3f}"
                 f":saturation={float(c.get('saturation', 1)):.3f},colorbalance=rm={w:.3f}:bm={-w:.3f}")
        if vw * vh < info["width"] * info["height"]:  # grade after the size-down: 4K to 1080 is 4x fewer pixels
            graph = graph.replace(f"{lab}scale={vw}:{vh}", f"{lab}scale={vw}:{vh},{grade}", 1)
        else:
            graph = graph.replace(f"{lab}scale=", f"{lab}{grade},scale=", 1)
    anim, highlight = style.get("anim", "none"), style.get("highlight", [1, 0.83, 0])
    if anim.startswith("pair-"):  # projects made before presets: that look is now the with-caption preset
        anim, style["preset"] = "pair", style.get("preset") or "with-caption"
    cue_rows = capcut_edit.read_draft(path)["subtitles"]
    said = cue_data.load(folder, "clipkit_words.json", cue_rows)
    punches = cue_data.load(folder, "clipkit_punch.json", cue_rows)
    marks: list = []  # ("pop", t) at each punch, ("broll", a, b, query) where the agent asked for b-roll
    shown = 0
    estimated = 0  # lines with no word times (typed by hand, or English): highlight paced by letters instead
    hook = _hook(folder)
    held = {"white": "", "until": HOOK_DUR if hook else 0, "en": cue_data.load(folder, "clipkit_caption.json", cue_rows),
            "box": safe_box(style), "motions": overlays}
    text_segments = sorted((text or {}).get("segments", []), key=lambda s: s["target_timerange"]["start"])
    layout_errors = [] if repair and dry and not final else None
    quality["layout_errors"] = layout_errors if layout_errors is not None else []
    subtitle_events, plain = [], None
    if anim in ("none", "pop"):
        plain = plain_events(draft, cue_rows, said, style=style, duration=sum(seg["target_timerange"]["duration"] for seg in segs) / US)
        subtitle_events = pair_events_from_timeline({"plain_events":plain,"hook":hook,"motions":overlays})
        events.extend(subtitle_ass(event) for event in subtitle_events)
        for phrase in _resolved_pair_cues(cue_rows, said, {}, duration=sum(seg["target_timerange"]["duration"] for seg in segs) / US):
            marks.append(("phrase",phrase["start"],phrase["end"],phrase["lead"],"",phrase["line"],0,"",None,phrase["hit"],phrase["cue_id"],phrase["clock_status"]))
        shown, estimated = len(cue_rows), sum(not said.get(cue_data.key(row["id"])) for row in cue_rows)
    if anim == "pair":
        pair = pair_look(style, W, H)
        fam, fdir = _font(pair.get("font"))
        family = family or fam
        font_files.add(fdir)
        rows = []
        for segment in text_segments:
            material = index.get(segment["material_id"], (None, None))[1]
            if material:
                start = segment["target_timerange"]["start"] / US
                rows.append({"id": segment["id"], "text": json.loads(material["content"]).get("text", ""),
                             "start": start, "end": start + segment["target_timerange"]["duration"] / US})
        phrases = _resolved_pair_cues(rows, said, punches, duration=sum(seg["target_timerange"]["duration"] for seg in segs) / US)
        quality["caption_errors"] = draft_quality.caption_errors(phrases, pair, held["en"])
        if quality["caption_errors"] and (final or not dry and not preview):
            gaps = ", ".join(str(issue["cue_id"]) for issue in quality["caption_errors"])
            raise VideoEditError(f"required {pair['caption_lang']} caption missing for cue(s): {gaps}")
        subtitle_events = pair_events(phrases, pair, W, H, fam, fdir, captions=held["en"], held=held, layout_errors=layout_errors)
        events.extend(subtitle_ass(event) for event in subtitle_events)
        for p in phrases:
            marks.append(("phrase", p["start"], p["end"], p["lead"], p["punch"], p["line"], p["k"], p["query"], p["opts"], p["hit"], p["cue_id"], p["clock_status"]))
            if p["query"]:
                marks.append(("broll", p["start"], p["end"], p["query"]))
        marks.extend(("pop", event["start"]) for event in subtitle_events if event["role"] == "emphasis" and event["effect"] == "pop")
        shown, estimated = len(rows), sum(not said.get(cue_data.key(row["id"])) for row in rows)
    for line_number, s in enumerate(text_segments):
        if anim == "pair" or plain is not None:
            continue
        m = index.get(s["material_id"], (None, None))[1]
        if not m:
            continue
        body = json.loads(m["content"])
        st = (body.get("styles") or [{}])[0]
        fam, fdir = _font((st.get("font") or {}).get("path") or m.get("font_path"))
        family = family or fam
        font_files.add(fdir)
        size = (m.get("font_size") or st.get("size") or 15) * CAPCUT_PX * (min(W, H) / 1080) * s["clip"]["scale"]["x"]
        fill = ((st.get("fill") or {}).get("content") or {}).get("solid", {}).get("color", [1, 1, 1])
        strokes = st.get("strokes") or []
        outline = size * strokes[0].get("width", 0) * 0.6 if strokes else 0  # measured by eye against CapCut
        ocol = strokes[0]["content"]["solid"]["color"] if strokes else [0, 0, 0]
        x = W / 2 + s["clip"]["transform"].get("x", 0) * W / 2
        y = H / 2 - s["clip"]["transform"]["y"] * H / 2
        t0 = s["target_timerange"]["start"] / US
        t1 = t0 + s["target_timerange"]["duration"] / US
        marks.append(("phrase", round(t0, 2), round(t1, 2), body.get("text", ""), "", body.get("text", ""), 0, "", None, t0, s["id"]))
        lines, fitted = _fit(body.get("text", ""), fdir, size, W * held["box"]["w"])
        if fitted < size:
            held.setdefault("shrunk", []).append({"at": round(t0, 2), "text": body.get("text", "")})
        size = fitted
        y = _safe_y(y, len(lines) * size, H, held)
        if anim == "karaoke":
            spoken = said.get(cue_data.key(s["id"]))
            estimated += not bool(spoken)
            pieces = [(t0 + a, t0 + b, w) for a, b, w in
                      _karaoke(lines, spoken, t1 - t0, _ass_color(fill), _ass_color(highlight))]
        else:
            pieces = [(t0, t1, "\\N".join(lines))]
        shown += 1
        pop = "\\fscx70\\fscy70\\t(0,120,\\fscx108\\fscy108)\\t(120,200,\\fscx100\\fscy100)" if anim == "pop" else ""
        for pa, pb, words in pieces:
            for a, b in visible_spans(max(pa, held["until"]), pb, overlays, role="normal", cue_id=s["id"]):
                events.append(f"Dialogue: 0,{_ts(a)},{_ts(b)},S,,0,0,0,,{{\\an5\\pos({x:.0f},{y:.0f})\\fn{fam}"
                              f"\\fs{size:.0f}\\frz{-s['clip'].get('rotation', 0):.1f}\\1c{_ass_color(fill)}"
                              f"\\3c{_ass_color(ocol)}\\bord{outline:.1f}{pop}}}{words}")
    if hook:
        hfam, hfile = _font(None)
        family = family or hfam
        font_files.add(hfile)
        kk = min(W, H) / 1080 * CAPCUT_PX
        for n, h in enumerate(hook):
            lines, px = _fit(h["text"], hfile, h["size"] * kk, W * held["box"]["w"], True)
            an = {"up": 2, "down": 8}.get(h["grow"], 5)
            for ha, hb in visible_spans(h["start"], h["end"], overlays, role="title"):
                events.append(f"Dialogue: 1,{_ts(ha)},{_ts(hb)},S,,0,0,0,,{{\\an{an}\\pos({W / 2:.0f},{H / 2 - h['y'] * H / 2 + {2: -4, 8: 4}.get(an, 0):.0f})"
                          f"\\fn{hfam}\\fs{px:.0f}\\1c{_ass_color(h['fill'])}\\3c{_ass_color(h['stroke'])}"
                          f"\\bord{px * h['width'] * 0.6 + 2:.1f}\\shad2\\fad(0,150)\\fscx120\\fscy120"
                          f"\\t({n * 150},{n * 150 + 160},\\fscx100\\fscy100)}}" + lines[0])

    emoji = emoji_beats([(m[1], m[2], f"{m[3]} {m[4]}") for m in marks if m[0] == "phrase"],
                        HOOK_DUR if hook else 0) if style.get("emoji") else []

    resolved_phrases = [{"start": m[1], "end": m[2], "lead": m[3], "punch": m[4], "line": m[5], "k": m[6],
                         "query": m[7], "opts": m[8] if len(m) > 8 else None,
                         "hit": m[9] if len(m) > 9 else m[1], "cue_id": m[10] if len(m) > 10 else None,
                         "clock_status": m[11] if len(m) > 11 else "cue"} for m in marks if m[0] == "phrase"]
    recipe_errors = motion_plan.verify_timeline(folder, {"duration": sum(s["target_timerange"]["duration"] for s in segs) / US,
        "width": W, "height": H, "look": anim, "style": style, "hook": hook, "caption_en": held["en"],
        "phrases": resolved_phrases, "motions": overlays, **({"plain_events":plain} if plain is not None else {})}, strict=not dry)

    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        base = "[vo]"
        if style.get("zoomcut"):
            zin = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in zoom_spans(segs)) or "0"
            # a zoomed copy laid over the plain one only during those spans (zoompan dropped frames: 60 s -> 25 s)
            zw, zh = round(W * ZOOM / 2) * 2, round(H * ZOOM / 2) * 2
            graph += (f";[vo]split[zp][zq];[zq]scale={zw}:{zh},crop={W}:{H}[zz];"
                      f"[zp][zz]overlay=0:0:enable='{zin}'[vz]")
            base = "[vz]"
        ins: list[str] = []  # extra inputs; input 0 is the raw clip

        def add(*args: str) -> int:
            ins.extend(args)
            return sum(1 for x in ins if x == "-i")
        # Only explicit placements enter the picture. Query maps are candidates for review,
        # never permission to choose a first hit or invent an insertion duration.
        placed = _json(folder / "clipkit_placed.json", None)
        plan = [] if placed is None else placed.get("broll", [])
        simple = video_edit.kitconfig.simple()  # simple mode: no b-roll, music or sound effects
        if simple:
            plan = []
        import insert_plan
        plan = insert_plan.normalize(path, plan, allow_legacy=True)
        plan = insert_playback(path, plan, materialize=not dry)
        for k, it in enumerate(plan):
            # a still picture is one frame: loop it so it stays up for the whole span
            n = (add("-loop", "1", "-i", it["play"]) if it["kind"] == "image"
                 else add("-ss", f"{it['media_start']:.9f}", "-i", it["play"]))
            a, e = float(it["start"]), float(it["start"]) + float(it["dur"])
            frame = it["frame"]
            graph += (f";[{n}:v]scale={frame['width']}:{frame['height']},crop={W}:{H}:{-frame['left']}:{-frame['top']},"
                      f"trim=0:{e - a:.9f},setpts=PTS-STARTPTS+{a:.9f}/TB[br{k}];"
                      f"{base}[br{k}]overlay=0:0:eof_action=pass:repeatlast=0:enable='gte(t,{a:.9f})*lt(t,{e:.9f})'[bo{k}]")
            base = f"[bo{k}]"
        for k, e in enumerate(emoji):
            piece, base = emoji_overlay(base, add("-loop", "1", "-i", e["file"]), k, e, W, H)
            graph += piece
        if events:
            fam0 = family or _font(None)[0]
            atomic.write_text(Path(tmp) / "subs.ass", subtitle_ass_header(W, H, fam0) + "\n".join(events) + "\n", encoding="utf-8")
            # relative names + cwd=tmp: no Windows drive-letter escaping inside the filter string;
            # libass reads one fonts folder, so the project's font files and Kanit are copied into it
            import shutil
            fd = Path(tmp) / "fonts"
            fd.mkdir()
            for f in font_files | {DEFAULT_FONT}:
                shutil.copy2(f, fd / f.name)
            graph += f";{base}ass=subs.ass:fontsdir=fonts[vf]"
            vout = "[vf]"
        else:
            vout = base
        # motion clips (transparent MOV) on top, each from its own moment; scaled to the canvas height
        for k, o in enumerate(overlays):
            if not Path(o["file"]).is_file():
                raise VideoEditError(f"motion file missing: {o['file']}")
            n = add("-i", o["file"])
            s0 = float(o["start"])
            graph += (f";[{n}:v]scale=-2:{H},setpts=PTS-STARTPTS+{s0:.3f}/TB[m{k}];"
                      f"{vout}[m{k}]overlay=(W-w)/2:0:eof_action=pass:enable='between(t,{s0:.3f},{s0 + float(o['duration']):.3f})'[o{k}]")
            vout = f"[o{k}]"
        if style.get("progress"):
            bar, vout = progress_bar(vout, sum(s["target_timerange"]["duration"] for s in segs) / US, W, H)
            graph += bar
        if preview:  # quick look for review: half size, fast encode
            graph += f";{vout}scale=trunc(iw/4)*2:-2[pv]"
            vout = "[pv]"

        # sound: a quiet music bed, a pop on each punch, a whoosh as b-roll comes in
        length = sum(s["target_timerange"]["duration"] for s in segs) / US
        mus = (placed or {}).get("music")  # the editor's pick: {"file": path or "", "volume": 0-1}
        mix, music_file = ["[voice]"], (mus["file"] or None) if mus is not None else _music(style)
        if music_file and not Path(music_file).is_file():
            raise VideoEditError(f"music file missing: {music_file}")
        music_vol = mus.get("volume", 0.12) if mus is not None else style.get("music_volume", 0.12)
        fx = (placed or {}).get("sfx", {"on": style.get("sfx", True), "volume": style.get("sfx_volume", 1.0)})
        if simple:
            music_file, fx = None, {"on": False, "volume": 0}
        if dry:  # what the export would contain, for the timeline editor; nothing is encoded
            length = sum(s["target_timerange"]["duration"] for s in segs) / US
            return {"duration": length, "width": W, "height": H, "look": anim, "style": style,
                    "hook": hook, "subtitle_events": subtitle_events, **({"plain_events":plain} if plain is not None else {}), "caption_en": held["en"], "zoom": zoom_spans(segs) if style.get("zoomcut") else [],
                    "progress": bool(style.get("progress")), "emoji": emoji,
                    "phrases": resolved_phrases, "motion_recipe_errors": recipe_errors,
                    "broll": plan, "motions": overlays, "quality": quality, "music": {"file": music_file or "", "volume": music_vol},
                    "sfx": fx, "edited": placed is not None, "shrunk": held.get("shrunk", []), "source": src, "src_w": info["width"], "src_h": info["height"],
                    "fit": {"w": vw, "h": vh, "x": ox, "y": oy},
                    "pops": [m[1] for m in marks if m[0] == "pop"],
                    "segments": [{"media_start": s["source_timerange"]["start"] / US, "dur": s["source_timerange"]["duration"] / US,
                                  "start": s["target_timerange"]["start"] / US} for s in segs]}
        sfx = [("pop", m[1]) for m in marks if m[0] == "pop"] + [("whoosh", float(it["start"]) - 0.15) for it in plan]
        sounds = {kind: _sfx(kind) for kind in sorted({kind for kind, _ in sfx})} if fx["on"] else {}
        audio_files = ([music_file] if music_file else []) + list(sounds.values())
        check_export.bind_media({str(Path(file).resolve()): file for file in audio_files})
        graph += ";[ac]asplit=2[voice][key]"
        if music_file:
            n = add("-stream_loop", "-1", "-i", music_file)
            # one low steady level under the voice (no pumping up and down)
            graph += (f";[{n}:a]atrim=0:{length:.3f},volume={music_vol},"
                      f"afade=t=out:st={max(0, length - 1.5):.3f}:d=1.5[mus]")
            mix.append("[mus]")
        graph += ";[key]anullsink"
        if fx["on"]:
            for k, (kind, t) in enumerate(sfx):
                n = add("-i", str(sounds[kind]))
                ms = max(0, round(t * 1000))
                graph += f";[{n}:a]adelay={ms}|{ms},volume={(0.5 if kind == 'pop' else 0.35) * fx['volume']:.3f}[s{k}]"
                mix.append(f"[s{k}]")
        graph += f";{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first,{LOUDNESS}[aout]"
        encoded = Path(tmp) / "export.mp4"
        cmd = [FFMPEG, "-y", "-ss", f"{seek:.3f}", "-i", src, *ins, "-filter_complex", graph, "-map", vout, "-map", "[aout]",
               *encoder, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(encoded)]
        started = time.monotonic()
        r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
        encode_seconds = round(time.monotonic() - started, 3)
        if r.returncode != 0 or not encoded.is_file() or not encoded.stat().st_size:
            raise VideoEditError(f"export failed ({encoder[1]}): " + r.stderr.strip()[-800:])
        check_export()
        output_info = validate_output(encoded, W, H, length, preview=preview)
        import delivery_manifest
        evidence = delivery_manifest.prepare(path, encoded, target, "mp4", check_export(),
            options={"backend": "ffmpeg", "encoder": encoder, "preview": preview},
            checks={"encode": "passed", "full_av_decode": "passed", "dimensions": "passed", "duration": "passed", "audio": "passed", "probe": output_info})
        check_export()
        encoded.replace(target)
    return {"file": str(target), "delivery_manifest": str(evidence), "subtitles": shown, "motions": len(overlays), "sub_anim": anim,
            "encoder": encoder[1], "encode_seconds": encode_seconds,
            "broll": [it["query"] for it in plan], "broll_items": plan, "music": Path(music_file).name if music_file else None,
            "music_file": music_file or "", "music_volume": music_vol, "sfx_on": fx["on"], "sfx_volume": fx["volume"],
            "sfx": len(sfx) if fx["on"] else 0, "preview": preview,
            "karaoke_estimated": estimated, "pieces": len(segs), "safe_zone_moved": held.get("safe_moved", 0),
            "shrunk": held.get("shrunk", []), "skipped_tracks": skipped,
            "duration": output_info["duration"]}


def text_px(m: dict, s: dict, W: int, H: int) -> tuple[Path, float]:
    """(font file, size in pixels) of a CapCut text material m shown by segment s on a W x H canvas."""
    st = (json.loads(m["content"]).get("styles") or [{}])[0]
    _, fdir = _font((st.get("font") or {}).get("path") or m.get("font_path"))
    return fdir, (m.get("font_size") or st.get("size") or 15) * CAPCUT_PX * (min(W, H) / 1080) * s["clip"]["scale"]["x"]


def safe_zone(path: str) -> list[str]:
    """Text outside the clip's safe box (safe_box) in a CapCut project or its subtitle preset: '<time> s "<text>":
    <where>' per line. The MP4 export moves such lines itself (_safe_y); CapCut projects and presets are only reported."""
    from PIL import ImageFont
    folder = Path(path)
    style = _json(folder / "clipkit_style.json", {})
    box, lh = safe_box(style), float(style.get("line_h", LINE_H))
    draft = capcut_edit._load(folder)
    W, H = draft["canvas_config"]["width"], draft["canvas_config"]["height"]
    index = capcut_edit._index(draft)
    problems = []
    for track in draft["tracks"]:
        if track.get("type") != "text":
            continue
        for s in track["segments"]:
            m = index.get(s["material_id"], (None, None))[1]
            if not m:
                continue
            text = json.loads(m["content"]).get("text", "")
            fdir, size = text_px(m, s, W, H)
            lines, size = _fit(text, fdir, size, W * box["w"])
            font = ImageFont.truetype(str(fdir), max(1, round(size)))
            half_w = max(font.getlength(t) for t in lines) / 2
            half_h = size * lh * len(lines) / 2
            x = W / 2 + s["clip"]["transform"].get("x", 0) * W / 2
            y = H / 2 - s["clip"]["transform"]["y"] * H / 2
            where = []
            if y - half_h < box["top"] * H:
                where.append("top bar")
            if y + half_h > box["bottom"] * H:
                where.append("caption area at the bottom")
            if abs(x - W / 2) + half_w > box["w"] * W / 2:
                where.append("button column / frame edge")
            if where:
                problems.append(f'{s["target_timerange"]["start"] / US:.1f} s "{text[:30]}": ' + ", ".join(where))
    if style.get("anim", "none").startswith("pair"):  # the pair look draws lines from the preset, not the draft
        look = pair_look(style, W, H)
        for role in ("lead", "punch", "caption"):
            if look[role]:
                size, y = look[role][0] * CAPCUT_PX * (min(W, H) / 1080), H / 2 - look[role][1] * H / 2
                if y - size * 0.6 < box["top"] * H or y + size * 0.6 > box["bottom"] * H:
                    problems.append(f'preset {style.get("preset") or "default"} {role} line (y {look[role][1]}): '
                                    "under the top bar or the caption area")
    return problems
