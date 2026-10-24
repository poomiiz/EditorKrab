"""Checks every cut and caption card the export would make against the audio and the transcript.

Read-only. Input: the JSON from `cargo run --example cut_review`. Output: one HTML page with the cut
clip's waveform (cuts, camera changes and cards drawn on it) and a row per cut saying whether the
audio there is quiet, whether a word is cut, and whether the line it leaves is finished.

    python scripts/cut_review.py <cuts.json> <ffmpeg.exe> <out.html>
"""
import html, json, math, subprocess, sys
from array import array

RATE = 16000
STEP_MS = 20
QUIET_DB = -38  # below this the 20 ms window counts as quiet (speech in these clips sits around -25..-10 dB)


def levels(ffmpeg, path):
    raw = subprocess.run([ffmpeg, '-v', 'error', '-i', path, '-vn', '-ac', '1', '-ar', str(RATE), '-f', 's16le', '-'],
                         capture_output=True, check=True).stdout
    s = array('h'); s.frombytes(raw)
    n = RATE * STEP_MS // 1000
    out = []
    for i in range(0, len(s) - n, n):
        chunk = s[i:i + n]
        rms = math.sqrt(sum(x * x for x in chunk) / n) / 32768
        out.append(20 * math.log10(max(rms, 1e-5)))
    return out


def db_at(lv, a, b):
    """Loudest 20 ms window in [a, b) ms of the source."""
    w = lv[max(0, a // STEP_MS):max(a // STEP_MS + 1, b // STEP_MS)]
    return max(w) if w else -100.0


def in_word(words, t):
    """The word said across moment t (it starts 40 ms before and ends 40 ms after)."""
    for text, a, b in words:
        if a + 40 < t < b - 40:
            return text
    return None


def main():
    data = json.load(open(sys.argv[1], encoding='utf-8'))
    ffmpeg, out = sys.argv[2], sys.argv[3]
    lines = data['lines']
    order = {l['id']: i for i, l in enumerate(sorted(lines, key=lambda l: (l['path'], l['start_ms'])))}
    by_id = {l['id']: l for l in lines}
    parts = []
    for story in data['stories']:
        lv = {src: levels(ffmpeg, src.replace('\\\\?\\', '')) for src in story['sources']}
        pieces, rows, at, total = story['pieces'], [], 0, 0
        for p in pieces:
            p['at'] = at
            at += p['end_ms'] - p['start_ms']
        total = at
        for i in range(1, len(pieces)):
            a, b = pieces[i - 1], pieces[i]
            contiguous = a['source'] == b['source'] and abs(b['start_ms'] - a['end_ms']) <= 40
            camera = a['crop'] != b['crop']
            if contiguous and not camera:
                continue
            L = lv[story['sources'][a['source']]]
            before, after = db_at(L, a['end_ms'] - 100, a['end_ms']), db_at(L, b['start_ms'], b['start_ms'] + 100)
            removed = b['start_ms'] - a['end_ms'] if a['source'] == b['source'] else None
            cut_word = in_word(a['words'], a['end_ms']) or in_word(b['words'], b['start_ms'])
            la, lb = by_id.get(a['segment'], {}), by_id.get(b['segment'], {})
            line_done = a['segment'] != b['segment'] and abs(la.get('end_ms', 0) - a['end_ms']) <= 300
            skipped = [l['text'] for l in lines if a['segment'] != b['segment'] and order.get(a['segment'], 0) < order[l['id']] < order.get(b['segment'], 0)]
            back = order.get(b['segment'], 0) < order.get(a['segment'], 0)
            problems = []
            if cut_word: problems.append(f'ตัดกลางคำ "{cut_word}"')
            # what is taken out must be quiet; a camera change must fall between words, not inside a phrase
            # a click or breath (a few 20 ms windows) is fine; 200 ms or more of sound taken out is not
            loud_ms = sum(STEP_MS for x in L[a['end_ms'] // STEP_MS:b['start_ms'] // STEP_MS] if x > QUIET_DB) if removed and removed > 0 else 0
            if loud_ms >= 200:
                problems.append(f'ส่วนที่ตัดออกมีเสียง {loud_ms / 1000:.1f} วิ')
            if removed and removed > 0 and db_at(L, b['start_ms'] - 60, b['start_ms']) > QUIET_DB:
                problems.append('ตัดหัวคำ (เสียงเริ่มก่อนจุดตัด)')
            if camera and contiguous:
                said = sorted(a['words'] + b['words'], key=lambda w: w[1])
                ends = [w[2] for w in said if w[2] <= a['end_ms'] + 40]
                starts = [w[1] for w in said if w[1] >= a['end_ms'] - 40]
                pause = (min(starts) - max(ends)) if ends and starts else 999
                if pause < 120 and a['segment'] == b['segment']:
                    problems.append(f'สลับกล้องกลางวลี (เว้นแค่ {max(pause, 0)} ms)')
            if not contiguous and a['segment'] == b['segment']:
                kind = 'ตัดช่วงเงียบกลางประโยค'
            elif not contiguous:
                kind = 'ต่อประโยคถัดไป' if not skipped and not back else ('ข้ามไปเนื้อหาอื่น' if skipped else 'ย้อนกลับไปประโยคก่อน')
            else:
                kind = 'สลับกล้อง'
            if not contiguous and a['segment'] != b['segment'] and not line_done:
                problems.append('ประโยคก่อนหน้ายังพูดไม่จบ')
            rows.append(dict(at=b['at'], kind=kind, removed=removed, before=before, after=after, problems=problems,
                             left=' '.join(w[0] for w in a['words'][-4:]) or a['text'][-24:],
                             right=' '.join(w[0] for w in b['words'][:4]) or b['text'][:24], skipped=skipped))
        # cards: shown while there is speech under them, not sitting on silence
        cards = story['cards']
        card_rows = []
        for (s, e, text) in cards:
            # map output time back to the source to read the level
            for p in pieces:
                if p['at'] <= s < p['at'] + p['end_ms'] - p['start_ms']:
                    src = p['start_ms'] + s - p['at']
                    L = lv[story['sources'][p['source']]]
                    card_rows.append((s, e, text, db_at(L, src, src + 300)))
                    break
        # waveform of the cut clip
        px = 40
        W, H = max(800, int(total / 1000 * px) + 20), 220
        svg = [f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">']
        for t in range(0, int(total / 1000) + 1, 5):
            x = 10 + t * px
            svg.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{H}" class="grid"/><text x="{x + 2}" y="12" class="t">{t}s</text>')
        bars = []
        for p in pieces:
            L = lv[story['sources'][p['source']]]
            for k in range(p['start_ms'] // STEP_MS, p['end_ms'] // STEP_MS):
                o = p['at'] + k * STEP_MS - p['start_ms']
                v = max(0.0, (L[k] if k < len(L) else -100) + 60) / 60
                x = 10 + o / 1000 * px
                bars.append(f'M{x:.1f},{70 - v * 50:.1f}V{70 + v * 50:.1f}')
        svg.append(f'<path d="{"".join(bars)}" class="wave"/>')
        qy = 70 - (QUIET_DB + 60) / 60 * 50
        svg.append(f'<line x1="10" x2="{W}" y1="{qy:.1f}" y2="{qy:.1f}" class="quiet"/>')
        for r in rows:
            x = 10 + r['at'] / 1000 * px
            cls = 'bad' if r['problems'] else ('cam' if r['kind'] == 'สลับกล้อง' else 'cut')
            svg.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="16" y2="124" class="{cls}"><title>{html.escape(r["kind"])} {r["at"] / 1000:.2f}s</title></line>')
        for n, (s, e, text, _) in enumerate(card_rows):
            x, w, y = 10 + s / 1000 * px, max(2, (e - s) / 1000 * px - 2), 134 + (n % 2) * 26
            svg.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="22" rx="3" class="card"/><text x="{x + 3:.1f}" y="{y + 15}" class="c">{html.escape(text)}</text>')
        for (s, e, text) in story['emphasis']:
            x, w = 10 + s / 1000 * px, max(2, (e - s) / 1000 * px - 2)
            svg.append(f'<rect x="{x:.1f}" y="188" width="{w:.1f}" height="22" rx="3" class="emph"/><text x="{x + 3:.1f}" y="203" class="c">{html.escape(text)}</text>')
        svg.append('</svg>')
        bad = [r for r in rows if r['problems']]
        silent_cards = [c for c in card_rows if c[3] < QUIET_DB]
        table = ''.join(
            f'<tr class="{"bad" if r["problems"] else ""}"><td>{r["at"] / 1000:.2f}</td><td>{html.escape(r["kind"])}</td>'
            f'<td>{"" if r["removed"] is None else f"{r["removed"] / 1000:.2f}"}</td><td>{r["before"]:.0f} / {r["after"]:.0f}</td>'
            f'<td>…{html.escape(r["left"])} <b>|</b> {html.escape(r["right"])}…</td>'
            f'<td>{html.escape(" · ".join(r["problems"]) or "ผ่าน")}{"<br><small>ข้าม: " + html.escape(" / ".join(r["skipped"])) + "</small>" if r["skipped"] else ""}</td></tr>'
            for r in rows)
        parts.append(f'<h2>{html.escape(story["title"])}</h2><p>ยาว {total / 1000:.1f} วิ · จุดตัดและสลับกล้อง {len(rows)} จุด · '
                     f'<b>มีปัญหา {len(bad)} จุด</b> · การ์ดซับ {len(card_rows)} ใบ ขึ้นตอนเงียบ {len(silent_cards)} ใบ</p>'
                     + f'<div class="scroll">{"".join(svg)}</div>'
                     + f'<p class="legend"><span class="k cut"></span>ตัด <span class="k cam"></span>สลับกล้อง <span class="k bad"></span>มีปัญหา '
                     f'<span class="k quiet"></span>เส้นเงียบ ({QUIET_DB} dB) · แถวล่าง: การ์ดขาว แล้วการ์ดส้ม</p>'
                     + (f'<p>การ์ดที่ขึ้นตอนเงียบ: ' + ' · '.join(f'{c[0] / 1000:.1f} วิ "{html.escape(c[2])}" ({c[3]:.0f} dB)' for c in silent_cards) + '</p>' if silent_cards else '')
                     + f'<table><tr><th>วิ</th><th>ชนิด</th><th>ตัดออก (วิ)</th><th>เสียงก่อน/หลัง (dB)</th><th>คำรอบจุดตัด</th><th>ผล</th></tr>{table}</table>')
    page = f'''<!doctype html><html lang="th"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Cut Review</title>
<style>:root{{--bg:#0f1420;--fg:#e7e9ee;--muted:#9aa3b2;--line:#2a3344;--bad:#ff5a5a;--cut:#ffb020;--cam:#4aa8ff;--card:#2f3a52;--emph:#ff8000}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.5 "Leelawadee UI",Tahoma,sans-serif}}
.scroll{{overflow-x:auto;border:1px solid var(--line);border-radius:6px}}svg .wave{{stroke:#7f8ba3;stroke-width:1}}svg .grid{{stroke:var(--line)}}
svg .t{{fill:var(--muted);font-size:11px}}svg .c{{fill:var(--fg);font-size:12px}}svg .quiet{{stroke:#5d6b85;stroke-dasharray:3 3}}
svg .cut{{stroke:var(--cut);stroke-width:2}}svg .cam{{stroke:var(--cam);stroke-width:2}}svg .bad{{stroke:var(--bad);stroke-width:3}}
svg .card{{fill:var(--card)}}svg .emph{{fill:var(--emph);opacity:.75}}table{{border-collapse:collapse;width:100%;margin-top:12px}}
td,th{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}tr.bad td{{color:#ffb3b3}}
.legend{{color:var(--muted)}}.k{{display:inline-block;width:14px;height:4px;margin:0 4px 2px 10px}}.k.cut{{background:var(--cut)}}.k.cam{{background:var(--cam)}}.k.bad{{background:var(--bad)}}.k.quiet{{background:#5d6b85}}</style>
<h1>ตรวจจุดตัดกับเส้นเสียง</h1>{"".join(parts)}</html>'''
    open(out, 'w', encoding='utf-8').write(page)
    print(out)


main()
