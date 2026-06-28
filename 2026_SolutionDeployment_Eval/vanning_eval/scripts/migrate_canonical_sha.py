"""scoreboard の input ファイル SHA を canonical JSON ベースに移行する。

旧仕様 (生 bytes hash) で保存された `submissions/<sha[:16]>.json` を
正規化 JSON (sort_keys, separators(',',':')) ベースの SHA に付け直し、
`history.json` の `files.layout_result.sha256/path` と
`files.items_input.sha256/path` を書き換える。

論理的に同一内容の JSON が同じ SHA に揃うので、フィルタが正しく統合される。

Usage (vanning_eval_rui/ から実行):
    python scripts/migrate_canonical_sha.py --dry-run
    python scripts/migrate_canonical_sha.py            # 適用
    python scripts/migrate_canonical_sha.py --keep-old # 旧ファイルを残す（既定は削除）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

THIS = Path(__file__).resolve()
RUI_ROOT = THIS.parents[1]
REPO_ROOT = THIS.parents[2]

HISTORY_PATH = REPO_ROOT / "scoreboard" / "history.json"
SUBMISSIONS_REL = "scoreboard/submissions"


def canonical_sha256(content_bytes: bytes) -> str:
    """JSON として正規化したうえで SHA-256 を返す（streamlit_app と同じロジック）。"""
    try:
        obj = json.loads(content_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return hashlib.sha256(content_bytes).hexdigest()
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(canonical).hexdigest()


def items_group_key_and_info(content_bytes: bytes) -> tuple[str, dict[str, Any] | None]:
    """items_input から (group_key=dataset_info hash, dataset_info dict) を返す。

    dataset_info が無ければ canonical_sha256 にフォールバックして None を返す。
    """
    try:
        obj = json.loads(content_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return canonical_sha256(content_bytes), None
    if not isinstance(obj, dict) or not isinstance(obj.get("dataset_info"), dict):
        return canonical_sha256(content_bytes), None
    di = obj["dataset_info"]
    canonical = json.dumps(di, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(canonical).hexdigest(), di


def resolve(path_str: str) -> Path:
    return REPO_ROOT / Path(path_str)


def plan_remap(
    history: list[dict[str, Any]],
) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """旧 path → (新 path, 新 sha) のマップと、ファイル不在で skip した path リストを返す。"""
    remap: dict[str, tuple[str, str]] = {}
    missing: list[str] = []
    for entry in history:
        files = entry.get("files") or {}
        for role in ("layout_result", "items_input"):
            meta = files.get(role)
            if not meta:
                continue
            old_path = meta.get("path")
            if not old_path or old_path in remap:
                continue
            local = resolve(old_path)
            if not local.exists():
                missing.append(old_path)
                continue
            content = local.read_bytes()
            new_sha = canonical_sha256(content)
            new_path = f"{SUBMISSIONS_REL}/{new_sha[:16]}.json"
            remap[old_path] = (new_path, new_sha)
    return remap, missing


def apply_remap(
    history: list[dict[str, Any]],
    remap: dict[str, tuple[str, str]],
) -> int:
    """history のエントリを新 SHA / path に書き換える。書き換えた件数を返す。

    items_input については dataset_info ベースの group_key と dataset_info dict も
    同時に埋め込む（リーダーボードのグループ化用）。
    """
    changed = 0
    for entry in history:
        files = entry.get("files") or {}
        for role in ("layout_result", "items_input"):
            meta = files.get(role)
            if not meta:
                continue
            old_path = meta.get("path")
            if not old_path or old_path not in remap:
                continue
            new_path, new_sha = remap[old_path]
            updated = False
            if meta.get("sha256") != new_sha or meta.get("path") != new_path:
                meta["sha256"] = new_sha
                meta["path"] = new_path
                updated = True
            if role == "items_input":
                # group_key が未設定 or 古い場合は再計算
                local = resolve(new_path)
                if local.exists():
                    gk, di = items_group_key_and_info(local.read_bytes())
                    if meta.get("group_key") != gk:
                        meta["group_key"] = gk
                        updated = True
                    if di is not None and meta.get("dataset_info") != di:
                        meta["dataset_info"] = di
                        updated = True
            if updated:
                changed += 1
    return changed


def relocate_files(
    remap: dict[str, tuple[str, str]],
    *,
    dry_run: bool,
    keep_old: bool,
) -> tuple[int, int, int]:
    """旧 path のファイルを新 path にコピー/移動。戻り値 = (rename, noop, conflict)。"""
    renamed = noop = conflict = 0
    for old_path, (new_path, _sha) in remap.items():
        if old_path == new_path:
            noop += 1
            continue
        old_local = resolve(old_path)
        new_local = resolve(new_path)
        if new_local.exists():
            # 既に新 path に同内容ファイルが存在（別エントリ経由で書かれたケース）
            if not dry_run and not keep_old and old_local.exists():
                old_local.unlink()
            conflict += 1
            continue
        if dry_run:
            renamed += 1
            continue
        new_local.parent.mkdir(parents=True, exist_ok=True)
        new_local.write_bytes(old_local.read_bytes())
        if not keep_old:
            old_local.unlink()
        renamed += 1
    return renamed, noop, conflict


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-old", action="store_true", help="旧 submission ファイルを残す")
    args = parser.parse_args()

    if not HISTORY_PATH.exists():
        print(f"history not found: {HISTORY_PATH}", file=sys.stderr)
        return 1

    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    if not isinstance(history, list):
        print("history.json is not a list", file=sys.stderr)
        return 1

    remap, missing = plan_remap(history)
    print(f"unique input paths: {len(remap)} (missing local: {len(missing)})")
    for old_path, (new_path, new_sha) in remap.items():
        flag = "==" if old_path == new_path else "->"
        print(f"  {old_path} {flag} {new_path}  sha={new_sha[:16]}")
    for m in missing:
        print(f"  [MISSING] {m}")

    renamed, noop, conflict = relocate_files(remap, dry_run=args.dry_run, keep_old=args.keep_old)
    print(f"files: rename={renamed} noop={noop} conflict={conflict}")

    changed = apply_remap(history, remap)
    print(f"history entries updated: {changed}")

    if args.dry_run:
        print("(dry-run) history.json は書き換えていない")
        return 0

    HISTORY_PATH.write_text(
        json.dumps(history, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {HISTORY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
