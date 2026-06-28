"""scoreboard/history.json の `hidden_reason=non_canonical` エントリを
非破壊で復活させる（新ポリシー: 登録外 input でも新規 group として表示）。

`quarantine_noncanonical_history.py` が dac2070 で `hidden=True +
hidden_reason=non_canonical` を付けたエントリ（taiga / shisa×2 相当）が対象。
新ゲートでは layout↔items 整合性のみが失格条件となり、registry 不一致は
ランキング除外しない。本スクリプトはこの方針転換のための一回限りの是正。

復活時の処理:
- `hidden = False` に戻す
- `hidden_reason` / `hidden_at` を削除
- `_audit` に restore レコードを追記（元の quarantine 監査は保全）

`hidden_reason` が他の値（item_mismatch / schema_error / no_items_input）
の場合は引き続き hidden 維持（実際の汚染／整合性違反は止め続ける）。

Usage（必ず branch 上で）:
    python scripts/restore_non_canonical_history.py            # dry-run
    python scripts/restore_non_canonical_history.py --apply    # 書き換え

Run from `vanning_eval_rui/`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        with contextlib.suppress(Exception):
            _s.reconfigure(encoding="utf-8")

THIS = Path(__file__).resolve()
REPO_ROOT = THIS.parents[2]
HISTORY_PATH = REPO_ROOT / "scoreboard" / "history.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not HISTORY_PATH.exists():
        print(f"history.json not found: {HISTORY_PATH}")
        return 1
    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))

    now = datetime.now(UTC).isoformat(timespec="seconds")
    restored = 0
    for entry in history:
        if not entry.get("hidden"):
            continue
        if entry.get("hidden_reason") != "non_canonical":
            continue
        print(f"  [RESTORE] {entry.get('author','?')} id={entry.get('id','?')[:12]}")
        entry["hidden"] = False
        entry.pop("hidden_reason", None)
        entry.pop("hidden_at", None)
        entry.setdefault("_audit", []).append({
            "at": now,
            "action": "restored",
            "by": "restore_non_canonical_history",
            "reason": "policy change: registry mismatch is no longer a quarantine reason",
        })
        restored += 1

    print(f"\nrestored={restored} / total={len(history)}")
    if not args.apply:
        print("\n[dry-run] no write. rerun with --apply to persist.")
        return 0
    HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nwrote: {HISTORY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
