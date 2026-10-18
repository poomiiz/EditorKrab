"""A ClipKit project as a HyperFrames composition: what the timeline editor shows live in <hyperframes-player>
and what `hyperframes render` turns into the MP4, so the preview and the file are drawn by the same engine.

Built from the renderer's dry run (cuts, crop, subtitle phrases, b-roll, music, effects, colour) into
<project>/clipkit_hf/index.html. Media is not copied (raw clips can be gigabytes): it is served by this app.
"""
from __future__ import annotations

import atomic
import html
import json
import cue_data
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

import render
import video_edit


class BuildBusy(video_edit.VideoEditError):
    pass


@contextmanager
def build_lock(path: str):
    """One OS-held reservation across app and CLI processes; closing releases it after a crash."""
    import hashlib
    import jobstore
    root = jobstore.STATE / "render-locks"
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(os.path.normcase(str(Path(path).resolve())).encode()).hexdigest()
    with (root / f"{key}.lock").open("a+b") as lock:
        if not lock.tell():
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BuildBusy("โปรเจกต์หรือไฟล์ส่งออกนี้กำลังสร้างอยู่ · รอให้งานเดิมเสร็จ") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _local(out: Path, path: str, name: str | None = None) -> str:
    """Content-addressed local copies never alias equal-size files with the same basename."""
    import shutil
    import tempfile
    import insert_plan
    src = Path(path)
    digest = insert_plan.file_digest(src)
    dst = out / "media" / (digest + src.suffix.lower())
    dst.parent.mkdir(exist_ok=True)
    if not dst.is_file() or insert_plan.file_digest(dst) != digest:
        fd, temporary = tempfile.mkstemp(prefix=".asset-", suffix=src.suffix, dir=dst.parent)
        os.close(fd)
        temp = Path(temporary)
        try:
            shutil.copy2(src, temp)
            if insert_plan.file_digest(temp) != digest or insert_plan.file_digest(src) != digest:
                raise render.VideoEditError("asset changed while copying; finish saving it before exporting")
            temp.replace(dst)
        finally:
            temp.unlink(missing_ok=True)
    return "media/" + quote(dst.name)


def _proxy(out: Path, src: str, segs: list[dict], skin: float = 0) -> str:
    """The kept pieces of the raw clip joined into one light H.264 file (raw files can be 4 GB HEVC a browser
    may not play, and the renderer fails on dozens of clips cut from one file), skin smoothing baked in so the
    live player shows it: made once per cut list and skin strength."""
    import hashlib
    import subprocess
    import tempfile
    import insert_plan
    source = Path(src)
    identity = insert_plan.file_digest(source)
    key = hashlib.sha256(json.dumps([identity, [(s["media_start"], s["dur"]) for s in segs], round(skin, 2), render.SKIN_FILTER, render.EDGE_FADE, 3]).encode()).hexdigest()
    dst = out / "media" / f"footage_{key}.mp4"
    dst.parent.mkdir(exist_ok=True)
    if not dst.is_file():
        a, b = min(s["media_start"] for s in segs), max(s["media_start"] + s["dur"] for s in segs)
        # size down once, before the cuts: trimming 36 pieces off 4K frames took 336 s, this order 78 s (same file)
        size = "scale='if(gt(iw,ih),min(1920,iw),-2)':'if(gt(iw,ih),-2,min(1920,ih))'"
        parts = f"[0:v]{size},split={len(segs)}" + "".join(f"[s{i}]" for i in range(len(segs))) + ";" + \
            "".join(f"[s{i}]trim={s['media_start'] - a:.3f}:{s['media_start'] - a + s['dur']:.3f},setpts=PTS-STARTPTS[v{i}];"
                        f"[0:a]atrim={s['media_start'] - a:.3f}:{s['media_start'] - a + s['dur']:.3f},asetpts=PTS-STARTPTS,{render.edge_fades(s['dur'])}[a{i}];"
                        for i, s in enumerate(segs))
        graph = parts + "".join(f"[v{i}][a{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=1[cv][ca];[cv]" + \
            (render.SKIN_FILTER.format(skin=skin) if skin > 0 else "null") + "[sv]"  # smoothing after the size-down: 4x less work
        fd, name = tempfile.mkstemp(prefix="footage_", suffix=".tmp.mp4", dir=dst.parent)
        os.close(fd)
        temp = Path(name)
        try:
            r = subprocess.run([render.FFMPEG, "-v", "error", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", src,
                                "-filter_complex", graph, "-map", "[sv]", "-map", "[ca]", "-r", "30", "-c:v", "libx264",
                                "-preset", "veryfast", "-crf", "18", "-g", "15", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                                str(temp)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
            if r.returncode or not temp.stat().st_size:
                raise render.VideoEditError("could not prepare the footage: " + r.stderr[-300:])
            temp.replace(dst)
        finally:
            temp.unlink(missing_ok=True)
    return "media/" + dst.name


def _motion(out: Path, source: str) -> str:
    """Browser-decodable alpha video; CapCut keeps the original transparent MOV."""
    import hashlib
    import subprocess
    import insert_plan
    src = Path(source)
    key = hashlib.sha256(f"alpha-v1:{insert_plan.file_digest(src)}".encode()).hexdigest()
    dst = out / "media" / f"motion_{key}.webm"
    dst.parent.mkdir(exist_ok=True)
    if not dst.is_file():
        temp = dst.with_suffix(".tmp.webm")
        result = subprocess.run([render.FFMPEG, "-v", "error", "-y", "-i", str(src), "-an", "-c:v", "libvpx-vp9",
                                 "-lossless", "1", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", str(temp)],
                                capture_output=True, text=True, timeout=600)
        if result.returncode:
            raise render.VideoEditError("could not prepare transparent motion: " + result.stderr[-300:])
        temp.replace(dst)
    return "media/" + dst.name


def _css_color(rgb: list[float]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c * 255))) for c in rgb[:3])


def build(path: str, *, repair: bool = False) -> Path:
    with build_lock(path):
        return _build(path, repair=repair)


def subtitle_html(event: dict, identifier: str, font_face: str = "ClipKit Thai") -> str:
    """Serialize the measured plan; the timed outer clip never receives animation visibility."""
    return (f'<div id="{identifier}" class="clip subtitle-event" data-role="{event["role"]}" '
            f'data-start="{event["start"]:.6f}" data-duration="{event["end"] - event["start"]:.6f}" data-track-index="3" '
            f'style="position:absolute;inset:0">'
            f'<div class="subtitle-glyph" style="left:{event["center_x_px"]:.6f}px;top:{event["center_y_px"]:.6f}px;'
            f'width:max-content;font-family:{font_face};font-size:{event["size_px"]:.6f}px;line-height:{render.LINE_H};'
            f'color:{_css_color(event["fill"])};-webkit-text-stroke:{event["outline_px"] * 2:.6f}px {_css_color(event["stroke"])};'
            f'text-shadow:0 {event["shadow"]}px {event["shadow"] * 2}px rgba(0,0,0,.7)">{html.escape(event["text"])}</div></div>')


def subtitle_animation(event: dict, identifier: str) -> str:
    selector = f'#{identifier} > .subtitle-glyph'
    base = f'tl.set("{selector}",{{xPercent:-50,yPercent:-50,rotation:{event["rotation"]:g}}},0);'
    if event["effect"] == "pop":
        return base + f'tl.fromTo("{selector}",{{scale:1.3}},{{scale:1,duration:0.14,ease:"none"}},{event["start"]:.6f});'
    if event["effect"] == "fade":
        return base + f'tl.fromTo("{selector}",{{opacity:0}},{{opacity:1,duration:0.08,ease:"none"}},{event["start"]:.6f});'
    return base


def _build(path: str, *, final: bool = False, audio_check=None, repair: bool = False) -> Path:
    import web_ai_transaction
    web_ai_transaction.recover("clip", path)
    t = render.render_draft(path, "", dry=True, final=final, repair=repair)
    render.motion_plan.verify_timeline(path, t)
    t["broll"] = render.insert_playback(path, t["broll"], materialize=True)
    W, H, D = t["width"], t["height"], t["duration"]
    st = t["style"] or {}
    subtitle_font = render._font(render.pair_look(st, W, H).get("font"))[1]
    k = min(W, H) / 1080 * render.CAPCUT_PX
    box, lh = render.safe_box(st), float(st.get("line_h", render.LINE_H))
    fit = t["fit"]
    c = st.get("color") or {}
    w = float(c.get("warmth", 0))
    # white balance gains and temperature as one per-channel matrix (SVG), then the CSS tone filters
    wb = (float(c.get("r", 1)) * (1 + w * 0.5), float(c.get("g", 1)), float(c.get("b", 1)) * (1 - w * 0.5))
    flt = (f"url(#wb) brightness({1 + float(c.get('brightness', 0)) * 1.6:.3f}) contrast({float(c.get('contrast', 1)):.3f}) "
           f"saturate({float(c.get('saturation', 1)):.3f})") if c else "none"
    svg = (f'<svg width="0" height="0" style="position:absolute"><filter id="wb" color-interpolation-filters="sRGB">'
           f'<feColorMatrix type="matrix" values="{wb[0]:.3f} 0 0 0 0 0 {wb[1]:.3f} 0 0 0 0 0 {wb[2]:.3f} 0 0 0 0 0 1 0"/>'
           f'</filter></svg>')
    els, anim = [], []
    n = 0
    out = Path(path) / "clipkit_hf"
    out.mkdir(exist_ok=True)
    segs = t["segments"]
    foot = _proxy(out, t["source"], segs, float(st.get("skin", 0)))

    def nid(p: str) -> str:
        nonlocal n
        n += 1
        return f"{p}{n}"

    # the talking footage, already cut: one clip for the whole timeline
    els.append(f'<video id="{nid("v")}" class="clip cam" src="{foot}" muted playsinline data-start="0" '
               f'data-duration="{D:.3f}" data-media-start="0" data-track-index="0" style="position:absolute;'
               f'left:{fit["x"]}px;top:{fit["y"]}px;width:{fit["w"]}px;height:{fit["h"]}px;filter:{flt}"></video>')
    els.append(f'<audio id="{nid("a")}" src="{foot}" data-start="0" data-duration="{D:.3f}" '
               f'data-media-start="0" data-track-index="1" data-volume="1"></audio>')
    # zoom cut: punched in between real cuts (render.zoom_spans), same as the ffmpeg export
    for a, b in t["zoom"]:
        anim.append(f'tl.set(".cam",{{scale:{render.ZOOM}}},{a:.3f});tl.set(".cam",{{scale:1}},{b:.3f});')
    # b-roll over the speaker
    for b in t["broll"]:
        frame = b["frame"]
        rectangle = f'position:absolute;left:{frame["left"]}px;top:{frame["top"]}px;width:{frame["width"]}px;height:{frame["height"]}px'
        if b["kind"] == "image":
            els.append(f'<img id="{nid("b")}" class="clip" src="{_local(out, b["file"])}" data-start="{b["start"]:.9f}" '
                       f'data-duration="{b["dur"]:.9f}" data-track-index="2" '
                       f'style="{rectangle}">')
        elif b.get("file"):
            els.append(f'<video id="{nid("b")}" class="clip" src="{_local(out, b["play"])}" muted playsinline data-start="{b["start"]:.9f}" '
                       f'data-duration="{b["dur"]:.9f}" data-media-start="{b["media_start"]:.9f}" data-track-index="2" '
                       f'style="{rectangle}"></video>')

    def text(words: str, size: float, y: float, fill, stroke, width: float, a: float, b: float, grow: str = "both", floor: float = 0, one: bool = False,
             x: float = 0, rot: float = 0, shadow: bool = False, fixed: bool = False,
             role: str = "normal", cue_id: str | None = None) -> str:
        # same as the ffmpeg export: one line only, a long subtitle goes on as the next piece (render.one_line)
        first = ""
        for piece, pa, pb, px in render.one_line(words, subtitle_font, size * k, W * (box["w"] - abs(x)), a, b, one=True, fixed=fixed):
            for pa, pb in render.visible_spans(pa, pb, t.get("motions", []), role=role, cue_id=cue_id):
                i = nid("t")
                first = first or i
                # a lead grows upward and a punch downward, so the pair never covers each other
                top = H / 2 - y * H / 2 + {"up": px * 0.625, "down": -px * 0.625}.get(grow, 0)
                top = max(top, floor) if grow == "down" else top
                shift = {"up": "-100%", "down": "0"}.get(grow, "-50%")
                # same safe box as the ffmpeg export (render.safe_box): clear of the apps' top bar, buttons and caption
                block = px * lh
                upper = top - {"-100%": block, "0": 0}.get(shift, block / 2)
                top += min(max(upper, H * box["top"]), H * box["bottom"] - block) - upper
                tops[i] = top
                els.append(f'<div id="{i}" class="clip txt" data-start="{pa:.3f}" data-duration="{pb - pa:.3f}" data-track-index="3" '
                           f'style="top:{top:.0f}px;left:{5 + x * 50:g}%;transform:translateY({shift}) rotate({rot:g}deg);'
                           f'{"text-shadow:4px 4px 6px rgba(0,0,0,.9);" if shadow else ""}font-size:{px:.0f}px;line-height:{lh};color:{_css_color(fill)};'
                           f'-webkit-text-stroke:{px * width * 1.2 + 2:.1f}px {_css_color(stroke)}">{html.escape(piece)}</div>')
        return first

    tops: dict[str, float] = {}
    look = t["look"]
    subtitle_fonts = {}
    if look == "pair" or "plain_events" in t:
        for event in render.pair_events_from_timeline(t):
            face = subtitle_fonts.setdefault(event["font_file"], f"ClipKitSubtitle{len(subtitle_fonts)}")
            i = nid("t")
            els.append(subtitle_html(event, i, face))
            anim.append(subtitle_animation(event, i))
    else:
        for p in t["phrases"]:
            if t["hook"] and p["start"] < render.HOOK_DUR:
                continue
            i = text(p["lead"], 16, -0.6, [1, 1, 1], [0, 0, 0], 0.08, p["start"], p["end"], cue_id=p.get("cue_id"))
            if i and look == "pop":
                  anim.append(f'tl.fromTo("#{i}",{{scale:0.7}},{{scale:1,duration:0.2,ease:"back.out(2)"}},{p["start"]:.3f});')
    # clip title: each line punches in, the second a beat after the first
    first = ""
    for j, h in enumerate(t["hook"]):  # j: n is the id counter
        i = text(h["text"], h["size"], h["y"], h["fill"], h["stroke"], h["width"], h["start"], h["end"],
                 h["grow"], tops.get(first, 0) + 8, one=True, role="title")
        first = first or i
        if i:
            anim.append(f'tl.fromTo("#{i}",{{scale:1.2}},{{scale:1,duration:0.16}},{j * 0.15:.2f});')
    # progress bar along the bottom edge, filling over the clip (same as render.progress_bar)
    if t["progress"]:
        els.append(f'<div id="{nid("p")}" class="clip pbar" data-start="0" data-duration="{D:.3f}" data-track-index="6" '
                   f'style="position:absolute;left:0;bottom:0;width:{W}px;height:{max(4, round(H * 0.008))}px;background:#fff;'
                   f'transform-origin:0 50%;transform:scaleX(0)"></div>')
        anim.append(f'tl.fromTo(".pbar",{{scaleX:0}},{{scaleX:1,duration:{D:.3f},ease:"none"}},0);')
    # emoji on keywords, popping in (same place and times as the ffmpeg export, render.emoji_beats)
    es, ex, ey = render.emoji_box(W, H)
    for e in t["emoji"]:
        i = nid("e")
        els.append(f'<img id="{i}" class="clip" src="{_local(out, e["file"])}" data-start="{e["at"]:.3f}" data-duration="{e["dur"]:.3f}" '
                   f'data-track-index="7" style="position:absolute;left:{ex}px;top:{ey}px;width:{es}px;height:{es}px">')
        anim.append(f'tl.fromTo("#{i}",{{scale:0,opacity:0}},{{scale:1,opacity:1,duration:0.18,ease:"back.out(2.5)"}},{e["at"]:.3f});')
    for index, motion in enumerate(t.get("motions", [])):
        els.append(f'<video id="{nid("o")}" class="clip" src="{_motion(out, motion["file"])}" muted playsinline '
                   f'data-start="{motion["start"]:.3f}" data-duration="{motion["duration"]:.3f}" data-media-start="0" '
                   f'data-track-index="{8 + index}" style="position:absolute;top:0;left:50%;height:100%;width:auto;transform:translateX(-50%)"></video>')
    # sound: quiet steady music bed, pops on punches, whooshes as b-roll comes in
    mu, fx = t["music"], t["sfx"]
    sounds = {kind: render._sfx(kind) for kind in ("pop", "whoosh")} if fx.get("on") else {}
    audio_files = ([mu["file"]] if mu.get("file") else []) + list(sounds.values())
    if audio_check is not None:
        audio_check({str(Path(file).resolve()): file for file in audio_files})
    if mu.get("file"):
        els.append(f'<audio id="{nid("m")}" src="{_local(out, mu["file"])}" data-start="0" data-duration="{D:.3f}" '
                   f'data-media-start="0" data-track-index="4" data-volume="{mu["volume"]}"></audio>')
    if fx.get("on"):
        pop_s, whoosh_s = render.sfx_len("pop", sounds["pop"]), render.sfx_len("whoosh", sounds["whoosh"])
        for tp in t["pops"]:
            els.append(f'<audio id="{nid("s")}" src="{_local(out, sounds["pop"])}" data-start="{tp:.3f}" data-duration="{pop_s:.2f}" '
                       f'data-track-index="5" data-volume="{0.5 * fx["volume"]:.2f}"></audio>')
        for b in t["broll"]:
            if b.get("file"):
                els.append(f'<audio id="{nid("s")}" src="{_local(out, sounds["whoosh"])}" data-start="{max(0, b["start"] - 0.15):.3f}" '
                           f'data-duration="{whoosh_s:.2f}" data-track-index="5" data-volume="{0.35 * fx["volume"]:.2f}"></audio>')
    font = _local(out, subtitle_font)
    # the HyperFrames runtime (clip windows, media sync, seeking) for the live player; the renderer brings its own
    runtime = _local(out, str(Path(__file__).with_name("hyperframe.runtime.iife.js")))
    page = f"""<!doctype html>
<html lang="th"><head><meta charset="UTF-8"><meta name="viewport" content="width={W}, height={H}">
<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
<script src="{runtime}"></script>
<style>
@font-face{{font-family:"ClipKit Thai";src:url("{font}")}}
{chr(10).join('@font-face{font-family:"'+face+'";font-weight:700;src:url("'+_local(out,file)+'")}' for file,face in subtitle_fonts.items())}
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{W}px;height:{H}px;overflow:hidden;background:#000}}
.cam{{transform-origin:{W / 2 - fit["x"]}px {H / 2 - fit["y"]}px}}
.txt,.subtitle-glyph{{position:absolute;white-space:nowrap;text-align:center;font-family:"ClipKit Thai",sans-serif;
  font-weight:700;line-height:1.25;paint-order:stroke fill;text-shadow:0 3px 8px rgba(0,0,0,.45)}}
.txt{{left:5%;width:90%;transform:translateY(-50%)}}
</style></head><body>
{svg}
<div id="root" data-composition-id="main" data-start="0" data-duration="{D:.3f}" data-width="{W}" data-height="{H}">
{chr(10).join(els)}
</div>
<script>
const tl = gsap.timeline({{paused: true}});
{chr(10).join(anim)}
document.fonts.ready.then(() => {{
window.__timelines = window.__timelines || {{}};
window.__timelines["main"] = tl;
}});
</script>
</body></html>
"""
    atomic.write_text((out / "index.html"), page, encoding="utf-8")
    if not (out / "hyperframes.json").is_file():
        atomic.write_text((out / "hyperframes.json"), json.dumps({"name": Path(path).name}), encoding="utf-8")
    return out / "index.html"
