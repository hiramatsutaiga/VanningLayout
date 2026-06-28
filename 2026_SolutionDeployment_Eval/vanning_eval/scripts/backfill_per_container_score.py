"""scoreboard/history.json に新スコア用の per-container フィールドを backfill する。

旧スキーマで投稿されたエントリ (fill_rate_per_container / cog_dev_per_container を持たない)
のうち、`files.layout_result` に submission JSON のパスを持つものを再評価し、
per-container 配列を score dict に追加して保存する。

submission ファイルが残っていない / DQ エントリ / 既に新スキーマ のエントリはスキップ。

Usage:
    python scripts/backfill_per_container_score.py [--dry-run]

Run from `vanning_eval_rui/` (paths are resolved relative to this script).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

THIS = Path(__file__).resolve()
RUI_ROOT = THIS.parents[1]
REPO_ROOT = THIS.parents[2]

# `pip install -e` 済みであれば不要だが、独立スクリプト起動時の保険
sys.path.insert(0, str(RUI_ROOT / "src"))

from vanning_eval.report import build_report  # noqa: E402
from vanning_eval.schema import load_items, load_layout  # noqa: E402

HISTORY_PATH = REPO_ROOT / "scoreboard" / "history.json"


def _load_layout_with_items(layout_path: Path, items_path: Path | None):
    layout = load_layout(layout_path)
    items = load_items(items_path) if items_path and items_path.exists() else None
    return layout, items


def _resolve(path_str: str) -> Path:
    """history.json に書かれた posix 相対パスをリポジトリルート起点に解決。"""
    return REPO_ROOT / Path(path_str)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="書き込まずに変更点だけ表示")
    args = parser.parse_args()

    if not HISTORY_PATH.exists():
        print(f"history.json not found: {HISTORY_PATH}")
        return 1

    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    updated = 0
    skipped: list[tuple[str, str]] = []

    for entry in history:
        author = entry.get("author", "?")
        score = entry.get("score") or {}
        if "fill_rate_per_container" in score and "cog_dev_per_container" in score:
            skipped.append((author, "already migrated"))
            continue
        if entry.get("verdict") != "pass":
            skipped.append((author, f"verdict={entry.get('verdict')}"))
            continue
        files = entry.get("files") or {}
        layout_meta = files.get("layout_result") or {}
        layout_rel = layout_meta.get("path")
        if not layout_rel:
            skipped.append((author, "no layout_result file"))
            continue

        layout_path = _resolve(layout_rel)
        if not layout_path.exists():
            skipped.append((author, f"missing file: {layout_rel}"))
            continue

        items_meta = files.get("items_input") or {}
        items_rel = items_meta.get("path")
        items_path = _resolve(items_rel) if items_rel else None

        try:
            layout, items = _load_layout_with_items(layout_path, items_path)
            report = build_report(layout, items)
            teacher = report["teacher_score_metrics"]
            fill_pc = teacher.get("fill_rate_per_container", [])
            cog_pc = teacher.get("cog_dev_per_container", [])
        except Exception as exc:  # noqa: BLE001 - 個別エントリのエラーは握りつぶしてスキップ
            skipped.append((author, f"build_report failed: {exc}"))
            continue

        score["fill_rate_per_container"] = fill_pc
        score["cog_dev_per_container"] = cog_pc
        updated += 1
        print(f"  [OK] {author}: per-container {len(fill_pc)} 件を埋めた")

    print()
    print(f"updated: {updated} 件 / skipped: {len(skipped)} 件")
    for author, reason in skipped:
        print(f"  - skip {author}: {reason}")

    if args.dry_run:
        print("\n[dry-run] 書き込みは行わない")
        return 0

    if updated == 0:
        print("\n変更なし、書き込みスキップ")
        return 0

    HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nwrote: {HISTORY_PATH}")
    print("リーダーボードに反映するには git commit + push する必要があります。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
