"""scoreboard/history.json を canonical input で再検証し、汚染エントリを
非破壊で quarantine（hidden + 監査ログ）、正規エントリを再採点する。

背景: 旧 `_items_group_key` は `dataset_info` のみ hash していたため、軽い
別 items を `case_01 seed=42` と申告した提出が同グループに混入し不正に
上位を取れた。本スクリプトは各エントリを `input/official_*` の canonical
input で `build_report` 再検証し:

- ok          : score / verdict を正規 input 基準で再採点上書き
- non_canonical / item_mismatch / no_items_input / schema_error
              : hidden=True + hidden_reason + _audit（削除しない＝監査可能、
                streamlit の非表示タブから個別復元も可）

既に hidden のエントリは人手のモデレーションを尊重してスキップ。

Usage（必ず branch 上で）:
    python scripts/quarantine_noncanonical_history.py            # dry-run（差分のみ）
    python scripts/quarantine_noncanonical_history.py --apply    # history.json 書き換え

Run from `vanning_eval_rui/`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Windows cp932 stdout で非 ASCII（em-dash 等）print がクラッシュするため
# 早期に utf-8 へ再構成（adv_lane 罠3 / ga_bench と同じ対策）。
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        with contextlib.suppress(Exception):
            _s.reconfigure(encoding="utf-8")

THIS = Path(__file__).resolve()
RUI_ROOT = THIS.parents[1]
REPO_ROOT = THIS.parents[2]
sys.path.insert(0, str(RUI_ROOT / "src"))

from vanning_eval.canonical_input import (  # noqa: E402
    content_group_key,
    gate_entry_against_canonical,
    load_canonical_registry,
    score_from_report,
)

HISTORY_PATH = REPO_ROOT / "scoreboard" / "history.json"
INPUT_ROOT = RUI_ROOT / "input"


def _resolve(path_str: str) -> Path:
    """history.json の posix 相対パスを REPO_ROOT 起点に解決
    （backfill_per_container_score._resolve と同一規約）。"""
    return REPO_ROOT / Path(path_str)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def evaluate_entry(entry: dict, registry: dict) -> tuple[str, dict]:
    """1 エントリを canonical 再検証し (action, patch) を返す（書き込みはしない）。

    action ∈ {"rescored", "quarantined", "skip"}。patch は entry に上書き
    マージする差分 dict（_audit はリスト追記）。
    """
    author = entry.get("author", "?")
    if entry.get("hidden"):
        return "skip", {"_reason": "already hidden（人手モデレーション尊重）"}

    files = entry.get("files") or {}
    layout_rel = (files.get("layout_result") or {}).get("path")
    items_rel = (files.get("items_input") or {}).get("path")
    if not layout_rel:
        res_status, res_reason, rescored = "schema_error", "layout_result 未記録", None
    else:
        items_path = _resolve(items_rel) if items_rel else None
        res = gate_entry_against_canonical(_resolve(layout_rel), items_path, registry)
        res_status, res_reason, rescored = res.status, res.reason, res.rescored_report

    audit_base = {
        "at": _now(),
        "old_verdict": entry.get("verdict"),
        "old_score": entry.get("score"),
        "by": "quarantine_noncanonical_history",
    }

    if res_status == "ok" and rescored is not None:
        patch: dict = {
            "score": score_from_report(rescored),
            "verdict": rescored.get("verdict", entry.get("verdict")),
            "_audit": [{**audit_base, "action": "rescored", "reason": res_reason}],
        }
        # items_input メタを content hash 系へ更新（旧 dataset_info hash の是正）
        im = dict(files.get("items_input") or {})
        items_path = _resolve(items_rel) if items_rel else None
        if items_path and items_path.exists():
            chash = content_group_key(items_path.read_bytes())
            im["content_hash"] = chash
            im["group_key"] = chash
            patch["files"] = {**files, "items_input": im}
        print(f"  [RESCORE] {author}: verdict {entry.get('verdict')}->{patch['verdict']}"
              f" containers {(entry.get('score') or {}).get('containers_used')}"
              f"->{patch['score'].get('containers_used')}")
        return "rescored", patch

    patch = {
        "hidden": True,
        "hidden_at": _now(),
        "hidden_reason": res_status,
        "_audit": [{**audit_base, "action": "quarantined",
                    "status": res_status, "reason": res_reason}],
    }
    print(f"  [QUARANTINE] {author}: {res_status} — {res_reason}")
    return "quarantined", patch


def _apply_patch(entry: dict, patch: dict) -> None:
    """patch を entry に上書きマージ。_audit はリスト追記。"""
    for k, v in patch.items():
        if k == "_audit":
            entry.setdefault("_audit", [])
            entry["_audit"].extend(v)
        else:
            entry[k] = v


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="history.json を実際に書き換える（既定は dry-run で差分表示のみ）",
    )
    args = parser.parse_args()

    if not HISTORY_PATH.exists():
        print(f"history.json not found: {HISTORY_PATH}")
        return 1
    registry = load_canonical_registry(INPUT_ROOT)
    if not registry:
        print(f"canonical registry が空です（{INPUT_ROOT}/official_*/items_input.json を配置）")
        return 1
    print(f"canonical datasets: {sorted(d.dataset_id for d in registry.values())}")

    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    counts = {"rescored": 0, "quarantined": 0, "skip": 0}
    for entry in history:
        action, patch = evaluate_entry(entry, registry)
        counts[action] += 1
        if action == "skip":
            continue
        _apply_patch(entry, patch)

    print(f"\nrescored={counts['rescored']} quarantined={counts['quarantined']} "
          f"skip={counts['skip']} / total={len(history)}")

    if not args.apply:
        print("\n[dry-run] 書き込みは行わない。確認後 --apply を付けて再実行。")
        return 0
    HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nwrote: {HISTORY_PATH}")
    print("リーダーボードに反映するには git commit + push（PR レビュー推奨）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
