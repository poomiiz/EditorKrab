"""Project corrections override retained machine rules; machine learning is an explicit opt-in."""
from __future__ import annotations

import atomic
import difflib
import json
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "corrections.json"
MAX_WORDS = 3  # a longer change is a rewrite, not a mishearing


def _words(text: str) -> list[str]:
    from pythainlp.tokenize import word_tokenize
    return word_tokenize(text, keep_whitespace=True)


def _file(project: str | Path | None) -> Path:
    return Path(project) / "clipkit_corrections.json" if project is not None else FILE


def _read(file: Path) -> dict[str, dict]:
    if not file.is_file():
        return {}
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or any(
        not isinstance(wrong, str) or not wrong.strip() or not isinstance(rule, dict)
        or not isinstance(rule.get("right"), str) or not rule["right"].strip()
        or isinstance(rule.get("n"), bool) or not isinstance(rule.get("n"), int) or rule["n"] < 1
        for wrong, rule in data.items()
    ):
        raise ValueError(f"{file.name} must contain word corrections with right and a positive n")
    return data


def load(project: str | Path | None = None) -> dict[str, dict]:
    """Read retained machine rules, overlaid by this project's rules when requested."""
    data = _read(FILE)
    if project is not None:
        data.update(_read(_file(project)))
    return data


def _updated(old: str, new: str, data: dict[str, dict]) -> tuple[dict[str, dict], list[tuple[str, str]]]:
    """Prepare learning before an edit writes anything."""
    a, b = _words(old), _words(new)
    pairs = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        wrong, right = "".join(a[i1:i2]).strip(), "".join(b[j1:j2]).strip()
        if op == "replace" and wrong and right and wrong != right and max(i2 - i1, j2 - j1) <= MAX_WORDS:
            pairs.append((wrong, right))
    data = dict(data)
    for wrong, right in pairs:
        seen = data.get(wrong)
        data[wrong] = {"right": right, "n": seen["n"] + 1 if seen and seen["right"] == right else 1}
    return data, pairs


def learn(old: str, new: str, project: str | Path | None = None) -> list[tuple[str, str]]:
    """Explicit learning: a project writes locally; no project explicitly targets machine rules."""
    file = _file(project)
    data, pairs = _updated(old, new, _read(file))
    if pairs:
        atomic.write_text(file, json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return pairs


def fix(text: str, data: dict[str, dict] | None = None) -> str:
    """The line with every learned mishearing swapped, matched on whole words only."""
    data = load() if data is None else data
    if not data:
        return text
    words, out, i = _words(text), [], 0
    longest = max(len(_words(w)) for w in data)
    while i < len(words):
        for n in range(min(longest, len(words) - i), 0, -1):
            hit = data.get("".join(words[i:i + n]).strip())
            if hit and words[i].strip() and words[i + n - 1].strip():
                out.append(hit["right"])
                i += n
                break
        else:
            out.append(words[i])
            i += 1
    return "".join(out)
