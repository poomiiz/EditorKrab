"""Hear one subtitle line again (the agent's ClipKit: ตรวจคำผิด step, for lines that read cut short).

    python scripts/relisten.py "<project folder>" "<line as it is>"

Prints what the speech model hears over that line's time, a little wider. Changes nothing; fix the line with
scripts/fix_text.py.
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import capcut_edit  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("project")
parser.add_argument("text")
parser.add_argument("--cue-id", help="Subtitle segment ID; required when wording repeats")
args = parser.parse_args()
print(capcut_edit.relisten(args.project, args.text, cue_id=args.cue_id))
