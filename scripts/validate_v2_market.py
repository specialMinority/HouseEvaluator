"""Create offline market validation reports. No network or runtime approval."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.v2.models import ValidationError
from backend.v2.store import local_path
from backend.v2.validation import read_json, render_markdown, validate_market


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="local validation manifest; its directory is the evidence root")
    parser.add_argument("--output", required=True, help="new JSON report path; matching .md companion is also created")
    parser.add_argument("--smoke", action="store_true", help="synthetic-only software exercise; never eligible for approval")
    args = parser.parse_args(argv)
    try:
        manifest_path = local_path(args.manifest)
        output = local_path(args.output)
        if output.suffix.lower() != ".json":
            raise ValidationError("output path must end in .json")
        markdown = output.with_suffix(".md")
        if output.exists() or markdown.exists():
            raise ValidationError("report output already exists; choose a new versioned path")
        manifest, _ = read_json(manifest_path)
        report = validate_market(manifest, base_dir=manifest_path.parent, smoke=args.smoke)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
        with markdown.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(render_markdown(report))
        print(json.dumps({"report": str(output), "markdown": str(markdown), "eligible_segments": report["eligible_segments"], "approval_changed": False}, ensure_ascii=False))
        return 0 if report["eligibility"]["passed"] or args.smoke else 3
    except (ValidationError, ValueError, OSError, UnicodeError, RecursionError, TypeError, AttributeError) as exc:
        print(f"Validation rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
