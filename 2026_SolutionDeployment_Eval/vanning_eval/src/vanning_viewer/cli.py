"""`vanning_viewer` の CLI エントリ。

使用例:
    python -m vanning_viewer input/shisa/layout_result.json
    python -m vanning_viewer layout.json --items items.json -o viewer.html
"""

from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

from vanning_eval.constraints import run_all_checks
from vanning_eval.schema import load_items, load_layout
from vanning_eval.scoring import compute_teacher_metrics

from .plotly_renderer import render_scene, save_html
from .view_model import build_scene


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vanning_viewer",
        description="バンニング配置結果を 3D 可視化して HTML に出力する。",
    )
    parser.add_argument("layout", type=Path, help="layout_result.json へのパス")
    parser.add_argument(
        "--items",
        type=Path,
        default=None,
        help="items_input.json へのパス（完全配置チェックのため）",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("viewer.html"),
        help="出力 HTML のパス（既定: viewer.html）",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="生成後にブラウザで開く",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    layout = load_layout(args.layout)
    items_input = load_items(args.items) if args.items else None
    violations = run_all_checks(layout, items_input)
    teacher = compute_teacher_metrics(layout)

    scene = build_scene(layout, violations=violations, teacher=teacher)
    fig = render_scene(scene)

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    save_html(fig, str(output))

    print(
        f"viewer: wrote {output}\n"
        f"  containers={len(scene.containers)} "
        f"items={scene.summary['item_count']} "
        f"verdict={scene.verdict} "
        f"violations={scene.disqualification_count}"
    )

    if args.open:
        webbrowser.open(output.as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
