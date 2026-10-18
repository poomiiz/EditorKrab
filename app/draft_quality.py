"""Read-only structural gate. Passing it does not establish audio sync or CapCut acceptance."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import capcut_edit
import cue_data
from video_edit import US, VideoEditError, is_image, probe


def _number(value: Any, where: str, *, positive: bool = False) -> float:
    try:
        valid = (not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
                 and (value > 0 if positive else value >= 0))
    except OverflowError:
        valid = False
    if not valid:
        raise VideoEditError(f"invalid draft: {where} must be finite and {'positive' if positive else 'nonnegative'}")
    return value


def _range(value: Any, where: str) -> tuple[float, float]:
    if not isinstance(value, dict):
        raise VideoEditError(f"invalid draft: {where} must be a timerange")
    start = _number(value.get("start"), where + ".start") / US
    end = start + _number(value.get("duration"), where + ".duration", positive=True) / US
    if end <= start:
        raise VideoEditError(f"invalid draft: {where} must span a positive time")
    return start, end


def check(path: str) -> dict[str, Any]:
    """Check referenced media and timeline bounds; never write files or infer spoken-word accuracy."""
    try:
        draft = capcut_edit._load(Path(path))
        if not isinstance(draft, dict):
            raise VideoEditError("invalid draft: root must be an object")
        canvas = draft.get("canvas_config")
        if not isinstance(canvas, dict):
            raise VideoEditError("invalid draft: canvas_config must be an object")
        for side in ("width", "height"):
            value = _number(canvas.get(side), "canvas." + side, positive=True)
            if not isinstance(value, int):
                raise VideoEditError("invalid draft: canvas dimensions must be whole pixels")
        if not isinstance(draft.get("tracks"), list) or not isinstance(draft.get("materials"), dict):
            raise VideoEditError("invalid draft: tracks and materials are missing")
        index = capcut_edit._index(draft)
        if len(index) != sum(1 for group in draft["materials"].values() if isinstance(group, list)
                             for item in group if isinstance(item, dict) and "id" in item):
            raise VideoEditError("invalid draft: duplicate material IDs")
        video = capcut_edit._video_track(draft)
        ranges = [_range(s.get("target_timerange"), "main video target") for s in video["segments"]]
        duration = 0.0
        for start, end in sorted(ranges):
            if abs(start - duration) > 0.000001:
                raise VideoEditError("unsupported draft: main video must be contiguous from zero, without gaps or overlaps")
            duration = end
        if "duration" in draft:
            declared = _number(draft["duration"], "duration", positive=True)
            end_us = max(s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                         for s in video["segments"])
            if abs(declared - end_us) > 1:
                raise VideoEditError("invalid draft: declared duration does not match the main video timeline")
        media, segments = {}, 0
        for n, track in enumerate(draft["tracks"]):
            if not isinstance(track, dict) or not isinstance(track.get("segments"), list):
                raise VideoEditError(f"invalid draft: track {n} must contain segments")
            for segment in track["segments"]:
                where = f"track {n} segment {segments}"
                segments += 1
                if not isinstance(segment, dict):
                    raise VideoEditError(f"invalid draft: {where} must be an object")
                start, end = _range(segment.get("target_timerange"), where + " target")
                if end > duration + 0.000001:
                    raise VideoEditError(f"invalid draft: {where} target extends beyond the main video")
                refs = segment.get("extra_material_refs", [])
                if not isinstance(refs, list):
                    raise VideoEditError(f"invalid draft: {where} extra_material_refs must be an array")
                for ref in [segment.get("material_id"), *refs]:
                    if not isinstance(ref, str) or ref not in index:
                        raise VideoEditError(f"invalid draft: {where} references a missing material")
                kind, material = index[segment["material_id"]]
                expected = {"video": "videos", "audio": "audios", "text": "texts"}.get(track.get("type"))
                if expected and kind != expected:
                    raise VideoEditError(f"invalid draft: {where} references the wrong material type")
                if kind not in {"videos", "audios"}:
                    continue
                raw = material.get("path")
                if not isinstance(raw, str) or not raw or not Path(raw).is_file():
                    raise VideoEditError(f"invalid draft: {where} source media is missing")
                source_start, source_end = _range(segment.get("source_timerange"), where + " source")
                if track is video:
                    if abs((source_end - source_start) - (end - start)) > 0.000001:
                        raise VideoEditError("unsupported draft: main video source and target durations must match")
                    speeds = [segment, *(index[ref][1] for ref in refs if index[ref][0] == "speeds")]
                    if any(_number(item.get("speed", 1), where + " speed", positive=True) != 1
                           or item.get("curve_speed") is not None for item in speeds):
                        raise VideoEditError("unsupported draft: main video speed must be 1 without a speed curve")
                file = str(Path(raw).resolve())
                if file not in media:
                    media[file] = (None if is_image(raw) else
                                   (capcut_edit._music_meta(Path(raw)) if kind == "audios" else probe(raw))["duration"])
                    if media[file] is not None:
                        _number(media[file], where + " media duration", positive=True)
                # Probe helpers round to 0.01 s (video) / 0.1 s (audio).
                if media[file] is not None and source_end > media[file] + 0.05:
                    raise VideoEditError(f"invalid draft: {where} source range exceeds the media duration")
        return {"structural_pass": True, "duration_seconds": round(duration, 6),
                "canvas": {key: canvas[key] for key in ("width", "height")},
                "media_files_checked": len(media), "segments_checked": segments,
                "text_coverage_gaps": capcut_edit.subtitle_gaps(path, min_gap=1.0, pad=0),
                "text_coverage_note": "Temporal text coverage only; gaps may be intentional and text may be titles.",
                "human_audio_sync": "pending", "capcut_open": "pending"}
    except VideoEditError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise VideoEditError(f"invalid draft structure: {exc}") from exc


def caption_errors(phrases: list[dict], look: dict, captions: dict) -> list[dict]:
    """Report required translations by source cue; never fabricate one from source wording."""
    if not look.get("caption") or look.get("caption_lang") == "th":
        return []
    missing = {}
    for phrase in phrases:
        key = cue_data.phrase_key(phrase)
        text = captions.get(key)
        if isinstance(text, str) and text.strip():
            continue
        if key not in missing:
            missing[key] = {"code": "required_caption_missing", "severity": "error",
                            "cue_id": phrase.get("cue_id"), "language": look.get("caption_lang"),
                            "start": phrase["start"], "end": phrase["end"]}
        missing[key]["end"] = max(missing[key]["end"], phrase["end"])
    return list(missing.values())
