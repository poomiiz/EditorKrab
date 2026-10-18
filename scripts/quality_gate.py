"""Read-only CapCut draft structure check; human listening and native CapCut opening stay pending."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import draft_quality
import delivery_manifest
from video_edit import VideoEditError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", help="CapCut project folder")
    parser.add_argument("--delivery", help="Private delivery manifest to verify")
    parser.add_argument("--output", help="Exact MP4 or native folder named by the manifest")
    args = parser.parse_args()
    if bool(args.delivery) != bool(args.output):
        parser.error("--delivery and --output must be supplied together")
    try:
        report = draft_quality.check(args.project)
        if args.delivery:
            report["delivery"] = delivery_manifest.status(args.project, args.delivery, args.output)
        print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
        return 2 if args.delivery and not report["delivery"]["current"] else 0
    except VideoEditError as exc:
        print(json.dumps({"structural_pass": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
