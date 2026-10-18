"""ClipKit's judgement steps done by an AI in the browser instead of on this machine (prompts/web_ai/schema.md).

    python scripts/web_ai.py export stories "<video>"                  # file to upload to the AI
    python scripts/web_ai.py import stories "<video>" answer.json      # the AI's JSON back into ClipKit
    python scripts/web_ai.py export clip "<project folder>"
    python scripts/web_ai.py import clip "<project folder>" answer.json

Then run the same run_clip.py command again; it carries on from the imported files.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import web_ai_json as web_ai  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Export a step for a web AI, or import its JSON answer.")
    ap.add_argument("action", choices=["export", "import"])
    ap.add_argument("step", choices=["stories", "clip"])
    ap.add_argument("target", help="the video (stories) or the ClipKit project folder (clip)")
    ap.add_argument("answer", nargs="?", help="import: the file holding the AI's answer")
    ap.add_argument("--max-minutes", type=float, help="approximate clip target before selecting stories; keeps saved target when omitted")
    a = ap.parse_args()
    if a.action == "export":
        out = (web_ai.export_stories if a.step == "stories" else web_ai.export_clip)(a.target, a.max_minutes)
        print(json.dumps({"upload": str(out)}, ensure_ascii=False))
        return
    if not a.answer:
        ap.error("import needs the answer file")
    print(json.dumps(web_ai.import_answer(a.step, a.target, Path(a.answer).read_text(encoding="utf-8-sig")), ensure_ascii=False))


if __name__ == "__main__":
    main()
