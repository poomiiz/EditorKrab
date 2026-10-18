"""Pure source coverage, options and whole-word clocks shared by every subtitle editor."""
from __future__ import annotations

import difflib
import math
import re
from typing import Any

from video_edit import VideoEditError

LOOKS = {"pair", "white", "color", "red", "hold", "skip"}
EPSILON = 1e-6


def tight(text: str) -> str:
    return "".join(text.split())


def validate_items(text: str, items: Any, *, cue_id: str | None) -> list[list]:
    """Preserve source wording; display rewrites belong only to the optional show fields."""
    prefix = f"cue {cue_id or '(unbound)'}"
    if not isinstance(text, str) or not tight(text):
        raise VideoEditError(f"{prefix}: ไม่มีข้อความต้นฉบับ")
    if not isinstance(items, list) or not items:
        raise VideoEditError(f"{prefix}: items ต้องมีอย่างน้อย 1 ท่อน")
    normalized = []
    for item in items:
        if (not isinstance(item, list) or not 2 <= len(item) <= 4
                or any(not isinstance(value, str) for value in item[:2]) or not tight(item[0] + item[1])):
            raise VideoEditError(f"{prefix}: ต้องเป็น [lead, punch, query?, options?] ที่มีคำพูดต้นฉบับ")
        query, options = None, None
        for value in item[2:]:
            if isinstance(value, str) and query is None:
                query = value
            elif isinstance(value, dict) and options is None:
                options = dict(value)
            else:
                raise VideoEditError(f"{prefix}: query และ options ต้องไม่ซ้ำและมีชนิดข้อมูลถูกต้อง")
        if options is not None:
            if set(options) - {"look", "show", "color", "second"}:
                raise VideoEditError(f"{prefix}: options มีช่องที่ไม่รองรับ")
            if not isinstance(options.get("look", "pair"), str) or options.get("look", "pair") not in LOOKS:
                raise VideoEditError(f"{prefix}: look ต้องเป็นหนึ่งใน {sorted(LOOKS)}")
            show = options.get("show")
            if "show" in options and (not isinstance(show, list) or len(show) != 2 or any(not isinstance(t, str) for t in show)):
                raise VideoEditError(f"{prefix}: show ต้องเป็น [ข้อความปกติ, ข้อความเน้น]")
            if show and tight(show[1]) and not tight(item[1]) and options.get("look", "pair") in {"pair", "red", "hold"}:
                raise VideoEditError(f"{prefix}: show คำเน้นต้องมี punch จากคำพูดจริงสำหรับอ้างเวลา")
            if "color" in options and (not isinstance(options["color"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", options["color"])):
                raise VideoEditError(f"{prefix}: color ต้องเป็น #rrggbb")
            if "second" in options and not isinstance(options["second"], str):
                raise VideoEditError(f"{prefix}: second ต้องเป็นข้อความ")
        normalized.append([item[0], item[1], *([query] if query is not None else []),
                           *([options] if options is not None else [])])
    if tight("".join(item[0] + item[1] for item in normalized)) != tight(text):
        raise VideoEditError(f"{prefix}: lead + punch ต้องตรงคำพูดเดิมครบตามลำดับ · แก้คำผิดแยกจากการเลือกคำเน้น หรือย่อผ่าน show")
    return normalized


def _finite(value: Any) -> bool:
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        return False


def _boundaries(text: str, spoken: list, length: float, cue_id: str | None) -> dict[int, float]:
    import video_edit
    prefix = f"cue {cue_id or '(unbound)'}"
    previous = 0.0
    for word in spoken:
        if (not isinstance(word, list) or len(word) != 3 or not isinstance(word[2], str)
                or not all(_finite(t) for t in word[:2]) or not 0 <= word[0] <= word[1] <= length + EPSILON
                or word[0] < previous - EPSILON):
            raise VideoEditError(f"{prefix}: เวลาระดับคำต้องเรียง ไม่ทับ และอยู่ในช่วงซับจริง")
        previous = word[1]
    language = "th" if re.search(r"[ก-๛]", "".join(w[2] for w in spoken)) else "en"
    starts = video_edit._word_starts(spoken, language)
    # Some recognizers attach the space to the preceding English token.
    if language == "en":
        starts = [whole or n > 0 and spoken[n - 1][2][-1:].isspace() for n, whole in enumerate(starts)]
    heard, at, times = "".join(tight(w[2]) for w in spoken), 0, {}
    wanted = tight(text)
    # Merged source cues may each contain one English word without boundary spaces
    # in the ASR labels. Source whitespace can identify those existing token starts.
    source_starts = {len(tight(text[:match.start()])) for match in re.finditer(r"\S+", text)} if heard == wanted and language == "en" else set()
    for word, whole in zip(spoken, starts):
        if (whole or at in source_starts) and tight(word[2]):
            times[at] = float(word[0])
        at += len(tight(word[2]))
    times[len(heard)] = float(spoken[-1][1])
    if wanted == heard:
        return times
    # A correction can replace a word's spelling, but moving an existing word or
    # removing one of repeated occurrences cannot inherit an arbitrary old onset.
    def word_key(value):
        return re.sub(r"[^\wก-๛]", "", tight(value)).casefold()

    edges = sorted(times)
    heard_tokens = [(word_key(heard[a:b]), a) for a, b in zip(edges, edges[1:]) if word_key(heard[a:b])]
    if language == "th":
        from pythainlp.tokenize import word_tokenize
        tokens = word_tokenize(text, engine="newmm", keep_whitespace=True)
    else:
        tokens = re.findall(r"\s+|\S+", text)
    wanted_tokens, offset = [], 0
    for token in tokens:
        key = word_key(token)
        if key:
            wanted_tokens.append((key, offset))
        offset += len(tight(token))
    heard_keys = [key for key, _ in heard_tokens]
    wanted_keys = [key for key, _ in wanted_tokens]
    ambiguous = f"{prefix}: จับคู่เวลาคำไม่ชัดเจนเมื่อสลับคำหรือลบคำซ้ำ · ใช้คำเดิมหรือถอดเสียงใหม่ก่อนแก้จังหวะ"
    unique_order = [heard_keys.index(key) for key in wanted_keys if heard_keys.count(key) == 1]
    if any(a >= b for a, b in zip(unique_order, unique_order[1:])):
        raise VideoEditError(ambiguous)
    if len(heard_tokens) != len(wanted_tokens) and any(
            heard_keys.count(key) > 1 and 0 < wanted_keys.count(key) < heard_keys.count(key) for key in set(heard_keys)):
        raise VideoEditError(ambiguous)
    same_positions = len(heard_tokens) == len(wanted_tokens)
    if same_positions and any(key in heard_keys and heard_tokens[n][0] != key for n, (key, _) in enumerate(wanted_tokens)):
        raise VideoEditError(ambiguous)
    # Spelling corrections may alter letter count. Only boundaries anchored to entire
    # heard words survive; an interior position in a rewritten span has no honest clock.
    mapped = {}
    for op, a, b, c, d in difflib.SequenceMatcher(a=heard, b=wanted, autojunk=False).get_opcodes():
        if op == "equal":
            mapped.update({c + position - a: time for position, time in times.items() if a <= position <= b})
        else:
            if a in times:
                mapped[c] = times[a]
            if b in times:
                mapped[d] = times[b]
    for n, (key, position) in enumerate(wanted_tokens):
        expected = (times[heard_tokens[n][1]] if same_positions and key == heard_tokens[n][0]
                    else times[heard_tokens[heard_keys.index(key)][1]] if heard_keys.count(key) == 1 else None)
        if expected is not None and position in mapped and abs(mapped[position] - expected) > EPSILON:
            raise VideoEditError(ambiguous)
    return mapped


def resolve(text: str, items: list | None, spoken: list | None, *, cue_id: str | None,
            start: float, end: float) -> list[dict]:
    """Return output-clock phrases without fitting, retiming by letters or changing source text."""
    prefix = f"cue {cue_id or '(unbound)'}"
    if not all(_finite(t) for t in (start, end)) or not 0 <= start < end:
        raise VideoEditError(f"{prefix}: ช่วงแสดงซับไม่ถูกต้อง")
    picked = validate_items(text, [[text, ""]] if items is None else items, cue_id=cue_id)
    if spoken is not None and not isinstance(spoken, list):
        raise VideoEditError(f"{prefix}: เวลาระดับคำต้องเป็นรายการ")
    if not spoken:
        if len(picked) > 1 or any(tight(item[1]) or any(
                isinstance(value, dict) and (value.get("look") == "color" or value.get("second"))
                for value in item[2:]) for item in picked):
            raise VideoEditError(f"{prefix}: ไม่มีเวลาระดับคำ · ถอดเสียงก่อนแบ่งจังหวะหรือเลือกคำเน้น")
        times, status = {0: 0.0, len(tight(text)): end - start}, "cue"
    else:
        times, status = _boundaries(text, spoken, end - start, cue_id), "word"
    out, position = [], 0
    for number, item in enumerate(picked):
        lead, punch = item[:2]
        hit_position = position + len(tight(lead))
        finish = hit_position + len(tight(punch))
        required = [position, finish] + ([hit_position] if tight(punch) else [])
        if any(point not in times for point in required):
            raise VideoEditError(f"{prefix}: ท่อนหรือคำเน้นอยู่กลางคำ/ละเอียดกว่าเวลาคำพูด · เลือกขอบคำเต็มหรือถอดเสียงใหม่")
        when = start + times[position]
        hit = start + times[hit_position] if tight(punch) else when
        query = next((value for value in item[2:] if isinstance(value, str)), "")
        options = next((value for value in item[2:] if isinstance(value, dict)), None)
        out.append({"start": when, "end": end, "hit": hit, "lead": lead.strip(), "punch": punch.strip(),
                    "line": text, "k": number, "query": query, "opts": options, "cue_id": cue_id, "clock_status": status})
        position = finish
    for number, phrase in enumerate(out):
        phrase["end"] = out[number + 1]["start"] if number + 1 < len(out) else end
        if phrase["start"] >= phrase["end"] or tight(phrase["punch"]) and phrase["hit"] >= phrase["end"]:
            raise VideoEditError(f"{prefix}: ท่อนละเอียดกว่าเวลาคำพูด · รวมท่อนให้ตรงขอบคำเต็ม")
    return out
