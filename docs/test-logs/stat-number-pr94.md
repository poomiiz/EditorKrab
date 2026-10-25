> **History.** Written for the Python-era build, kept as a record. The Python app was removed on 25 Oct 2026; EditorKrab is now the Rust app only, so commands here that use Python no longer exist. / เอกสารเก่าของรุ่น Python เก็บไว้เป็นบันทึก แอป Python ถูกลบเมื่อ 25 ต.ค. 2026

# Test log: motion infographic `stat-number` (PR #94)

PR: https://github.com/poomiiz/EditorKrab/pull/94 · branch `claude/project-thread-knd6cv` · commit `d3647c7`
Machine: cloud container (Linux, Python 3.13, Node 22, HyperFrames 0.8.101, Chromium headless). gsap was served
locally because the container blocks cdn.jsdelivr.net; on a normal machine the template loads it from the CDN.

## 2026-10-13 ~04:05Z code checks
| test | result |
|---|---|
| `motion_params` valid: `stat-number {number: "87"}`, `"1,250"`, `"3.5"` | pass; missing text fields stay empty, duration = line length |
| `motion_params` bad: unknown key, colour `pink`, duration `"3"`, unknown template, non-object, no `number`, `number: "abc"` / `""` | each raises with a readable message |
| `_motion_menu()` (clip export) | lists hook-title, sentence-pair, stat-number with `number` required, others `?` |
| `python scripts/smoke_test.py` | all checks passed |
| `python scripts/motion_cut_test.py` | PASS |
| CI `tests` on PR #94 | success (04:11:25Z) |

## 2026-10-13 ~04:07Z render, Thai
`number 1,250 · unit ล้านบาท · label รายได้ที่หายไปเพราะลูกค้าไม่กลับมาซื้อซ้ำ` → `out.mov` ProRes 4444 `yuva444p12le`, 4.0 s,
rendered in 19.6 s. Frames 0.3 / 0.9 / 2.5 s: counts up, unit pops in, label types in, all inside the card, tone marks intact.
First render overflowed the card (the label measured empty while hidden, and the number was measured mid-count); fixed
before commit by typing with `visibility` and sizing for the final value.

## 2026-10-13 ~04:15Z render, other languages (3 s each, frame at 2.4 s)
| lang | number / unit / label | result |
|---|---|---|
| en | 87 % · of workers have felt burned out | pass |
| ja | 1,250 万円 · リピート客を失って消えた売上 | pass |
| zh | 3.5 倍 · 老客户比新客户更愿意再次购买 | pass |
| ko | 72 % · 직장인이 번아웃을 경험했습니다 | pass |
| ru | 1,250 млн · выручки потеряно без повторных покупок | pass, but the long label shrinks to fit one line and gets small |
| hi | 87 % · कर्मचारियों ने बर्नआउट महसूस किया | pass, conjuncts and matras intact |

## Not tested / UNKNOWN
- Fonts: the container used its fallback fonts (IPAGothic, WenQuanYi, FreeSerif). A Windows machine without CJK, Korean or
  Devanagari fonts may show boxes; untested.
- Importing a real web-AI answer into a CapCut project end to end.
- A long label wraps to one shrinking line; a two-line layout would read better for long Russian or English labels.
