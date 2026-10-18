"""Shared, read-only insert geometry and source consumption in displayed pixels/seconds."""
from __future__ import annotations

import math

from video_edit import VideoEditError

RAMP_IN, RAMP_FROM = .6, 3.0


def _positive(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise VideoEditError(f"insert {name} must be finite and positive")
    return float(value)


def consumption(dur: float, *, ramp: bool = False, still: bool = False) -> float:
    duration = _positive(dur, "duration")
    if not ramp or still:
        return duration
    active = min(duration, RAMP_IN)
    return RAMP_FROM * active - (RAMP_FROM - 1) * active * active / (2 * RAMP_IN) + max(0, duration - RAMP_IN)


def geometry(info: dict, *, W: int, H: int, focus_x: float = .5, focus_y: float = .5) -> dict:
    """Cover the canvas using rotation-normalized source dimensions; compute the crop only once.

    Integer frame dimensions feed FFmpeg and HTML. Separate native axis scales account for
    the subpixel rounding so its placement describes that same rectangle.
    """
    width, height = _positive(info.get("width"), "source width"), _positive(info.get("height"), "source height")
    _positive(W, "canvas width"); _positive(H, "canvas height")
    if int(W) != W or int(H) != H:
        raise VideoEditError("insert canvas dimensions must be whole pixels")
    for name, value in (("focus_x", focus_x), ("focus_y", focus_y)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise VideoEditError(f"insert {name} must be between 0 and 1")
    scale, fit = max(W / width, H / height), min(W / width, H / height)
    sw, sh = math.ceil(width * scale - 1e-9), math.ceil(height * scale - 1e-9)
    left = round(max(W - sw, min(0, W / 2 - focus_x * sw)))
    top = round(max(H - sh, min(0, H / 2 - focus_y * sh)))
    return {"frame": {"width": sw, "height": sh, "left": left, "top": top},
            "clip": {"scale": scale / fit, "scale_x": sw / (width * fit), "scale_y": sh / (height * fit),
                     "x": 2 * (left - (W - sw) / 2) / W, "y": -2 * (top - (H - sh) / 2) / H}}
