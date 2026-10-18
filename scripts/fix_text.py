"""Fix one subtitle line's words in a CapCut project (the agent's ClipKit: ตรวจคำผิด step).

    python scripts/fix_text.py "<project folder>" "<line as it is>" "<fixed line>"

Close CapCut first. History retains the old version and the changed words are learned only in this project.
Use --learn-global explicitly to teach the machine dictionary as well.
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import capcut_edit  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("project")
parser.add_argument("old")
parser.add_argument("new")
parser.add_argument("--cue-id", help="Subtitle segment ID; required when wording repeats")
parser.add_argument("--learn-global", action="store_true", help="Also learn these corrections for every project on this machine")
args = parser.parse_args()
capcut_edit.set_line_text(args.project, args.old, args.new, args.cue_id, learn_global=args.learn_global)
print("ok:", args.old, "->", args.new)
