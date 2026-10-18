"""Crash-safe file writes: the new text goes to a temp file beside the target and replaces it in one step,
so a crash, power cut or full disk never leaves a half-written draft_content.json or config.json."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


def write_text(path: str | os.PathLike[str], text: str, encoding: str = "utf-8") -> None:
    _write(path, text, encoding)


def write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    _write(path, data, None)


def _write(path: str | os.PathLike[str], data: str | bytes, encoding: str | None) -> None:
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb" if isinstance(data, bytes) else "w", encoding=encoding) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):   # Windows refuses the swap while another program holds the file for a moment
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
