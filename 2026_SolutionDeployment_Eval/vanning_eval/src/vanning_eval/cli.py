"""評価ツールのコマンドラインエントリポイント。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .report import build_report, print_summary, save_report
from .schema import SchemaError, load_items, load_layout


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="vanning-eval", description="Evaluate a vanning layout result."
    )
    parser.add_argument("layout", type=Path, help="Path to layout_result.json")
    parser.add_argument(
        "--items",
        type=Path,
        default=None,
        help="Optional items_input.json for cross-validation (complete-placement check).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation_report.json"),
        help="Output path for the JSON report (default: evaluation_report.json).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stdout summary.",
    )
    args = parser.parse_args(argv)

    try:
        layout = load_layout(args.layout)
        items_input = load_items(args.items) if args.items else None
    except SchemaError as e:
        print(f"SCHEMA ERROR: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"FILE NOT FOUND: {e}", file=sys.stderr)
        return 2

    report = build_report(layout, items_input)
    save_report(report, args.output)

    if not args.quiet:
        print_summary(report)
        print(f"\nReport written: {args.output}")

    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
