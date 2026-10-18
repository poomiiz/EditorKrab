"""Style lab: take the subtitle look of a CapCut project or a finished video, show it as a still and a GIF, and
keep the ones the person confirms as presets/my-<name>.json (their own collection, git-ignored).

A CapCut project is read directly (preset_from_capcut.learn). A video has no style data, so its frames are
pulled out and an agent looks at them and writes style.json (skills/style-extract/SKILL.md).

    python scripts/style_lab.py start "<CapCut project folder | video file>"
    python scripts/style_lab.py preview "<lab folder>"        # after style.json was written or changed
    python scripts/style_lab.py save "<lab folder>" <name> [main] [caption] [second]
    python scripts/style_lab.py approve "<style json from the [style] issue>" <name>   # -> presets/team-<name>.json

Each source gets <output_dir>/style_lab/<name>/: source.json, frames/*.jpg (video), style.json, preview.png,
preview.gif. Kept styles: presets/parts/<main|caption|second>/<name>.* (pieces to mix) and presets/my-<name>.json
(ready to cut with). Sharing: share() makes a GitHub issue link asking for approval; approve() turns the approved
JSON into presets/team-<name>.json, committed, so every machine gets it on its next update. Prints JSON.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "capcut"))
sys.path.insert(0, str(ROOT / "scripts"))
import kitconfig  # noqa: E402
import preset_from_capcut  # noqa: E402

PRESETS = ROOT / "presets"
PREVIEWS = PRESETS / "previews"
PARTS = PRESETS / "parts"       # kept pieces by category, to mix into new presets
CATS = {"main": ("normal", "emphasis"), "caption": ("caption",), "second": ("second",)}
FONT = ROOT / "fonts" / "Kanit-Bold.ttf"
CAPCUT_PX = 5.2  # same as app/render.py: pixels per CapCut size unit per 1080 px of the short side
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
ROLES = ("normal", "emphasis", "caption", "second")
SAMPLE = {"normal": "นี่คือซับบรรทัดปกติ", "emphasis": "คำเน้นตรงนี้", "caption": "This is the reading subtitle",
          "second": "จริงเหรอ"}
W, H = 540, 960         # still preview (half of 1080x1920)
GW, GH = 360, 640       # GIF, smaller so it loads fast
FRAMES = 6              # frames pulled from a video for the agent to look at


def lab() -> Path:
    # same fallback as the app's covers and motion: no output folder set yet = the workspace folder
    d = Path(kitconfig.CFG.get("output_dir") or kitconfig.need("work_root")) / "style_lab"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(name: str) -> str:
    # letters, digits and marks (Thai vowels and tone marks are marks, not \w); anything else becomes "-"
    s = re.sub(r"-+", "-", "".join(c if c.isalnum() or c in "-_" or unicodedata.category(c).startswith("M") else "-"
                                   for c in name)).strip("-").lower()
    if not s:
        raise ValueError(f"name has no letters or digits: {name!r}")
    return s[:60]


def capcut_projects(limit: int = 40) -> list[dict]:
    """CapCut projects in the drafts folder, newest first."""
    drafts = Path(kitconfig.need("capcut_drafts"))
    rows = [p for p in drafts.iterdir() if (p / "draft_content.json").is_file()]
    rows.sort(key=lambda p: (p / "draft_content.json").stat().st_mtime, reverse=True)
    return [{"name": p.name, "path": str(p)} for p in rows[:limit]]


def _duration(video: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True)
    if r.returncode or not r.stdout.strip():
        raise ValueError(f"cannot read the length of {video}: {r.stderr.strip()[-300:]}")
    return float(r.stdout.strip())


def _grab(video: Path, at: float, out: Path) -> None:
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=-2:960", str(out)], capture_output=True, text=True)
    if r.returncode or not out.is_file():
        raise ValueError(f"cannot take a frame at {at:.1f}s from {video}: {r.stderr.strip()[-300:]}")


def _draft_video(project: Path) -> Path | None:
    """The first video file the CapCut project uses that is still on disk (for the preview background)."""
    d = json.loads((project / "draft_content.json").read_text(encoding="utf-8"))
    for v in d.get("materials", {}).get("videos", []):
        p = Path(v.get("path") or "")
        if v.get("type") == "video" and p.suffix.lower() in VIDEO_EXT and p.is_file():
            return p
    return None


def start(source: str) -> dict:
    """Make the lab folder for a CapCut project or a video. CapCut: style.json and previews right away.
    Video: frames only; status 'waiting_agent' with the command to paste to Claude."""
    src = Path(source)
    if (src / "draft_content.json").is_file():
        kind = "capcut"
    elif src.is_file() and src.suffix.lower() in VIDEO_EXT:
        kind = "video"
    else:
        raise ValueError(f"not a CapCut project folder or a video file: {source}")
    folder = lab() / _slug(src.stem if kind == "video" else src.name)
    (folder / "frames").mkdir(parents=True, exist_ok=True)
    for old in ("style.json", "preview.png", "preview.gif"):
        (folder / old).unlink(missing_ok=True)
    (folder / "source.json").write_text(json.dumps({"source": str(src), "kind": kind}, ensure_ascii=False),
                                        encoding="utf-8")
    video = src if kind == "video" else _draft_video(src)
    if video:
        dur = _duration(video)
        n = FRAMES if kind == "video" else 1
        for k in range(n):
            _grab(video, dur * (k + 1) / (n + 1), folder / "frames" / f"{k + 1}.jpg")
    if kind == "capcut":
        style = preset_from_capcut.learn(src)
        (folder / "style.json").write_text(json.dumps(style, ensure_ascii=False, indent=1), encoding="utf-8")
        preview(folder)
    return item(folder)


def _sig(r: dict) -> str:
    """One subtitle line as the eye sees it: fill, outline and size to the nearest point."""
    return f"{r['color'].lower()}/{r['outline'].lower()}/{round(float(r['size']))}"


def looks(style: dict) -> tuple[str, str]:
    """(family, look) of a style. Projects whose normal line matches are one family on the style page; each
    different look inside it (emphasis, caption or second line) is a variant the person can keep on its own."""
    have = [r for r in ROLES if style.get(r)]
    look = " ".join(f"{r}:{_sig(style[r])}" for r in have)
    return (_sig(style["normal"]) if style.get("normal") else look), look


def item(folder: Path) -> dict:
    folder = Path(folder)
    meta = json.loads((folder / "source.json").read_text(encoding="utf-8"))
    has = lambda n: (folder / n).is_file()  # noqa: E731
    status = "ready" if has("preview.gif") else "waiting_agent" if not has("style.json") else "needs_preview"
    out = {"folder": str(folder), "name": folder.name, **meta, "status": status,
           "frames": [str(f) for f in sorted((folder / "frames").glob("*.jpg"))],
           "style": json.loads((folder / "style.json").read_text(encoding="utf-8")) if has("style.json") else None,
           "png": str(folder / "preview.png") if has("preview.png") else None,
           "gif": str(folder / "preview.gif") if has("preview.gif") else None}
    if status == "ready":   # its style passed check() when the preview was made
        out["family"], out["look"] = looks(out["style"])
    if status == "waiting_agent":
        out["command"] = f'ClipKit: ถอดสไตล์ "{folder}"'
    return out


def items() -> list[dict]:
    rows = [f for f in lab().iterdir() if (f / "source.json").is_file()]
    rows.sort(key=lambda f: (f / "source.json").stat().st_mtime, reverse=True)
    return [item(f) for f in rows]


def check(style: dict, full: bool = False) -> dict:
    """Raise ValueError unless every line in style has the preset keys (as presets/default.json).
    full: also needs the normal + emphasis pair, which run_clip/render need to use it as a preset."""
    have = [r for r in ROLES if style.get(r)]
    if not have:
        raise ValueError("style has no subtitle line (normal, emphasis, caption or second)")
    if full and not {"normal", "emphasis"} <= set(have):
        raise ValueError("a preset needs both the normal and the emphasis line")
    for role in have:
        r = style[role]
        for k in ("size", "y", "color", "outline", "outline_width"):
            if k not in r:
                raise ValueError(f"{role}: missing {k}")
        for k in ("color", "outline"):
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(r[k])):
                raise ValueError(f"{role}.{k} must be #rrggbb: {r[k]}")
        if not 1 <= float(r["size"]) <= 80 or not -1 <= float(r["y"]) <= 1:
            raise ValueError(f"{role}: size 1-80 and y -1..1 (got size {r['size']}, y {r['y']})")
    return style


def _bg_of(folder: Path) -> Path | None:
    frames = sorted((folder / "frames").glob("*.jpg"))
    return frames[len(frames) // 2] if frames else None


def _background(src: Path | None, w: int, h: int):
    from PIL import Image
    if src is None:  # no footage to show: plain dark card (the project had no video file on disk)
        return Image.new("RGB", (w, h), (24, 26, 34))
    im = Image.open(src).convert("RGB")
    s = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)))
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def _line(canvas, role: str, r: dict, scale: float = 1.0, alpha: float = 1.0, font_file=FONT) -> None:
    """Draw one sample line of a role onto an RGBA canvas, as render.py places it (y up, outline px*w*0.6+2)."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    cw, ch = canvas.size
    px = float(r["size"]) * CAPCUT_PX * min(cw, ch) / 1080 * scale
    if px < 1 or alpha <= 0:
        return
    font = ImageFont.truetype(str(font_file), max(1, round(px)))
    stroke = round(px * float(r["outline_width"]) * 0.6 + 2 * min(cw, ch) / 1080) if r["outline_width"] else 0
    text = SAMPLE[role]
    pad = round(px) + stroke * 2
    tw = round(font.getlength(text)) + pad * 2
    layer = Image.new("RGBA", (tw, round(px * 2) + pad * 2), (0, 0, 0, 0))
    c = (layer.width / 2, layer.height / 2)
    if r.get("shadow"):
        sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((c[0] + px * 0.06, c[1] + px * 0.06), text, font=font, anchor="mm", fill=(0, 0, 0, 200))
        layer = Image.alpha_composite(layer, sh.filter(ImageFilter.GaussianBlur(px * 0.12)))
    ImageDraw.Draw(layer).text(c, text, font=font, anchor="mm", fill=r["color"], stroke_width=stroke,
                               stroke_fill=r["outline"])
    if r.get("rotation"):
        layer = layer.rotate(-float(r["rotation"]), expand=True, resample=Image.BICUBIC)
    if alpha < 1:
        layer.putalpha(layer.getchannel("A").point(lambda a: round(a * alpha)))
    x = cw / 2 + float(r.get("x", 0)) * cw / 2
    y = ch / 2 - float(r["y"]) * ch / 2
    canvas.alpha_composite(layer, (round(x - layer.width / 2), round(y - layer.height / 2)))


def _frame(bg, style: dict, t: float):
    """The preview at time t (seconds): each role pops in one after another, then everything holds."""
    img = bg.convert("RGBA")
    for k, role in enumerate(r for r in ROLES if style.get(r)):
        p = min(1.0, max(0.0, (t - k * 0.45) / 0.25))  # 0.25 s pop per line, 0.45 s apart
        font = Path(style.get("font") or FONT)
        _line(img, role, style[role], scale=0.7 + 0.3 * p + 0.15 * math.sin(math.pi * p), alpha=p,
              font_file=font if font.is_file() else FONT)  # grow, overshoot, settle
    return img.convert("RGB")


def _render(style: dict, bg: Path | None, out: Path) -> None:
    """<out>.png (all lines) and <out>.gif (lines popping in, looped)."""
    _frame(_background(bg, W, H), style, 99).save(out.with_suffix(".png"))
    small = _background(bg, GW, GH)
    shots = [_frame(small, style, k / 12) for k in range(36)]           # 3 s at 12 fps
    shots[0].save(out.with_suffix(".gif"), save_all=True, append_images=shots[1:], duration=83, loop=0, optimize=True)


def preview(folder: str | Path) -> dict:
    """Write preview.png and preview.gif from style.json."""
    folder = Path(folder)
    f = folder / "style.json"
    if not f.is_file():
        raise ValueError(f"no style.json in {folder} yet")
    _render(check(json.loads(f.read_text(encoding="utf-8"))), _bg_of(folder), folder / "preview")
    return item(folder)


def _pics(base: Path) -> dict:
    return {e: str(base.with_suffix("." + e)) if base.with_suffix("." + e).is_file() else None for e in ("png", "gif")}


def compose(name: str, main: str, caption: str | None = None, second: str | None = None,
            overwrite: bool = False) -> dict:
    """A ready preset presets/my-<name>.json from kept parts: normal + emphasis from part `main`, and optionally
    the reading subtitle and the second line from other parts. Its preview uses the main part's background."""
    key = "my-" + _slug(name).removeprefix("my-")
    out = PRESETS / f"{key}.json"
    if out.exists() and not overwrite:
        raise FileExistsError(f"{out.name} already exists: pick another name")
    style, about = {}, []
    for cat, part in (("main", main), ("caption", caption), ("second", second)):
        if not part:
            continue
        f = PARTS / cat / f"{part}.json"
        if part != _slug(part) or not f.is_file():
            raise ValueError(f"no kept {cat} part named {part}")
        d = json.loads(f.read_text(encoding="utf-8"))
        style.update({r: d[r] for r in CATS[cat]})
        if cat == "main":
            style.update({k: d[k] for k in ("fixed_size", "font") if k in d})
        about.append(f"{cat}: {part}")
    style = {"about": "Made in the style lab from " + ", ".join(about), **check(style, full=True)}
    style.setdefault("caption", None)
    out.write_text(json.dumps(style, ensure_ascii=False, indent=1), encoding="utf-8")
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    bg = PARTS / "main" / f"{main}.jpg"
    _render(style, bg if bg.is_file() else None, PREVIEWS / key)
    return {"preset": key, "file": str(out)}


def save(folder: str | Path, name: str, parts: list[str] | None = None, overwrite: bool = False) -> dict:
    """Keep the ticked parts (main = normal + emphasis, caption, second) of a previewed style in the collection
    (presets/parts/<part>/<name>.json, each with its own preview), and, when main is one of them, also a ready
    preset presets/my-<name>.json made of all ticked parts."""
    folder = Path(folder)
    if not (folder / "preview.gif").is_file():
        raise ValueError("look at the preview first: this style has no preview yet")
    style = check(json.loads((folder / "style.json").read_text(encoding="utf-8")))
    parts = parts or [c for c, roles in CATS.items() if all(style.get(r) for r in roles)]
    slug = _slug(name).removeprefix("my-")
    for cat in parts:
        if cat not in CATS:
            raise ValueError(f"unknown part {cat} (have: {', '.join(CATS)})")
        if not all(style.get(r) for r in CATS[cat]):
            raise ValueError(f"this style has no {cat} part")
        if (PARTS / cat / f"{slug}.json").exists() and not overwrite:
            raise FileExistsError(f"a {cat} part named {slug} already exists: pick another name")
    if "main" in parts and (PRESETS / f"my-{slug}.json").exists() and not overwrite:
        raise FileExistsError(f"my-{slug}.json already exists: pick another name")
    meta = json.loads((folder / "source.json").read_text(encoding="utf-8"))
    bg = _bg_of(folder)
    for cat in parts:
        d = PARTS / cat
        d.mkdir(parents=True, exist_ok=True)
        piece = {r: style[r] for r in CATS[cat]}
        if cat == "main":
            piece.update({k: style[k] for k in ("fixed_size", "font") if k in style})
        (d / f"{slug}.json").write_text(json.dumps({**piece, "about": style.get("about") or "", "source": meta["source"]},
                                                   ensure_ascii=False, indent=1), encoding="utf-8")
        if bg:
            shutil.copyfile(bg, d / f"{slug}.jpg")
        _render(piece, bg, d / slug)
    preset = None
    if "main" in parts:
        preset = compose(slug, slug, slug if "caption" in parts else None, slug if "second" in parts else None,
                         overwrite)["preset"]
    return {"parts": parts, "preset": preset}


def _repo() -> str:
    """owner/name of the GitHub repo this install updates from (ClipKit, or ClipKit-Team on team machines)."""
    r = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"], capture_output=True, text=True)
    m = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$", r.stdout.strip())
    if r.returncode or not m:
        raise ValueError(f"this ClipKit is not a GitHub clone, so it cannot share: {r.stdout.strip() or r.stderr.strip()}")
    return m.group(1)


def share(preset: str) -> dict:
    """A GitHub 'new issue' link asking the team to approve presets/my-<name>.json, with the preset in the text.
    Nothing is uploaded from here: the person opens the link, drops the preview GIF in and submits."""
    import urllib.parse
    f = PRESETS / f"{preset}.json"
    if not preset.startswith("my-") or preset != _slug(preset) or not f.is_file():
        raise ValueError(f"no kept style named {preset}")
    style = check(json.loads(f.read_text(encoding="utf-8")), full=True)
    style.pop("font", None)  # Local font paths are machine-specific and must not enter a public issue.
    name = preset.removeprefix("my-")
    body = "\n".join([f"ขออนุมัติสไตล์ `{name}` ให้ทีมใช้ (preset team-{name})", "",
                      "ลากไฟล์พรีวิว GIF มาวางตรงนี้:", "", "", "```json",
                      json.dumps(style, ensure_ascii=False, indent=1), "```"])
    url = f"https://github.com/{_repo()}/issues/new?" + urllib.parse.urlencode({"title": f"[style] {name}", "body": body})
    return {"url": url, "gif": str(PREVIEWS / f"{preset}.gif")}


def approve(src: str | Path, name: str) -> dict:
    """Approve a shared style (the JSON from its issue, saved to a file): presets/team-<name>.json, which is
    committed, so every machine gets it with its next update. The approver commits it."""
    style = check(json.loads(Path(src).read_text(encoding="utf-8")), full=True)
    key = "team-" + _slug(name).removeprefix("team-").removeprefix("my-")
    out = PRESETS / f"{key}.json"
    if out.exists():
        raise FileExistsError(f"{out.name} already exists: pick another name")
    style = {k: v for k, v in style.items() if k != "source"}       # a path on the sender's machine
    style["approved"] = time.strftime("%Y-%m-%d")
    style.setdefault("caption", None)
    out.write_text(json.dumps(style, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"preset": key, "file": str(out)}


def collection() -> dict:
    """The person's own styles, newest first: ready presets (presets/my-*.json) and kept parts per category."""
    new = lambda fs: sorted(fs, key=lambda p: p.stat().st_mtime, reverse=True)  # noqa: E731
    out = {"presets": [], "team": [], "parts": {c: [] for c in CATS}}
    for p in new(PRESETS.glob("team-*.json")):    # approved for the team: draw a preview the first time it is seen
        d = json.loads(p.read_text(encoding="utf-8"))
        if not (PREVIEWS / f"{p.stem}.gif").is_file():
            PREVIEWS.mkdir(parents=True, exist_ok=True)
            _render(check(d, full=True), None, PREVIEWS / p.stem)
        out["team"].append({"name": p.stem, "about": d.get("about", ""), **_pics(PREVIEWS / p.stem)})
    for p in new(PRESETS.glob("my-*.json")):
        out["presets"].append({"name": p.stem, "about": json.loads(p.read_text(encoding="utf-8")).get("about", ""),
                               **_pics(PREVIEWS / p.stem)})
    for cat in CATS:
        for p in new((PARTS / cat).glob("*.json")):
            out["parts"][cat].append({"name": p.stem, "about": json.loads(p.read_text(encoding="utf-8")).get("about", ""),
                                      **_pics(p)})
    return out


def remove(preset: str) -> dict:
    """Delete one of the person's own presets (presets/my-<name>.json) and its previews. Team and built-in
    presets come from the repo and are only hidden on the library page, never deleted here."""
    f = PRESETS / f"{preset}.json"
    if not preset.startswith("my-") or preset != _slug(preset) or not f.is_file():
        raise ValueError(f"not one of your own presets: {preset}")
    f.unlink()
    for ext in ("png", "gif"):
        (PREVIEWS / f"{preset}.{ext}").unlink(missing_ok=True)
    return {"removed": preset}


def main() -> int:
    args = sys.argv[1:]
    try:
        if args[:1] == ["start"] and len(args) == 2:
            out = start(args[1])
        elif args[:1] == ["preview"] and len(args) == 2:
            out = preview(args[1])
        elif args[:1] == ["approve"] and len(args) == 3:
            out = approve(args[1], args[2])
        elif args[:1] == ["save"] and len(args) >= 3:
            out = save(args[1], args[2], args[3:] or None)
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except (ValueError, FileExistsError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
