"""Serialize a project's whole edit across API, CLI and export processes."""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import inspect
import os
from pathlib import Path
import threading


_held = threading.local()
_active: set[str] = set()
_guard = threading.Lock()


def busy() -> bool:
    with _guard:
        return bool(_active)


@contextmanager
def writer(path: str | Path, *, recover: bool = True):
    import hf_build

    canonical = os.path.normcase(str(Path(path).resolve()))
    held = getattr(_held, "paths", None)
    if held is None:
        held = _held.paths = set()
    if canonical in held:
        yield
        return
    with hf_build.build_lock(canonical):
        held.add(canonical)
        with _guard:
            _active.add(canonical)
        try:
            if recover and (Path(canonical) / "draft_content.json").is_file():
                import web_ai_transaction
                if not web_ai_transaction.staged(canonical):
                    web_ai_transaction.recover("clip", canonical)
            yield
        finally:
            held.remove(canonical)
            with _guard:
                _active.remove(canonical)


def edit(function=None):
    """Lock the first path/request argument, or an explicit destination such as 'req.target'."""
    if isinstance(function, str):
        return lambda operation: _edit(operation, function)
    return _edit(function)


def _edit(function, destination: str | None = None):
    signature = inspect.signature(function, eval_str=True)
    first = next(iter(signature.parameters))
    names = (destination or first).split(".")

    @wraps(function)
    def wrapped(*args, **kwargs):
        import hf_build

        bound = signature.bind(*args, **kwargs)
        value = bound.arguments[names[0]]
        for name in names[1:]:
            value = getattr(value, name)
        path = getattr(value, "path", value)
        try:
            with writer(path):
                return function(*args, **kwargs)
        except hf_build.BuildBusy as exc:
            if first == "req":
                from fastapi import HTTPException
                raise HTTPException(409, str(exc)) from exc
            raise

    # FastAPI resolves string annotations in the wrapper's globals unless these are already evaluated.
    wrapped.__signature__ = signature
    return wrapped
