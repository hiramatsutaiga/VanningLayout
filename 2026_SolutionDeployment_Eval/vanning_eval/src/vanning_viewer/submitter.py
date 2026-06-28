"""Streamlit 非依存の採点エントリ構築・整合ゲート・順位付け関数群。

`streamlit_app.py` から MCP サーバーや自動化スクリプトでも再利用するために
切り出した純粋関数群。streamlit / st.* / Forge / GitHub の I/O には依存しない
（GitHub への PUT は `scoreboard_client.upload_if_absent` 経由で発生するが、
これは streamlit-non-dependent な薄いクライアント）。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from posixpath import dirname as posix_dirname
from typing import Any

from vanning_eval.canonical_input import (
    CanonicalDataset,
    canonical_json_sha256,
    content_group_key,
    gate_entry_against_canonical,
    score_from_report,
)
from vanning_eval.constraints import Violation
from vanning_viewer.scoreboard_client import ScoreboardConfig, upload_if_absent

JST = timezone(timedelta(hours=9))

# 努力目標としての推奨充填率 (要件定義書 5.4 補足参照)。順位付けには使わない。
RECOMMENDED_FILL_RATE = 0.80

# 辞書式ソート key の欠損用センチネル (失格 / 旧スキーマを末尾に寄せる)
_RANK_MISSING = float("inf")


def count_above_fill_threshold(
    score: dict[str, Any], threshold: float = RECOMMENDED_FILL_RATE
) -> int | None:
    """80% 以上で詰まったコンテナ本数（順位付け用ではなく参考表示専用）。"""
    rates = score.get("fill_rate_per_container")
    if rates is None:
        return None
    return sum(1 for r in rates if float(r) >= threshold)


def rank_sort_key(e: dict[str, Any]) -> tuple[float, float, float]:
    """辞書式ソートキー: (コンテナ数, 重心ズレ平均, 処理時間)、すべて昇順。"""
    s = e.get("score", {})
    cu = s.get("containers_used")
    cog = s.get("cog_y_mean_deviation")
    et = s.get("execution_time_ms")
    return (
        float(cu) if cu is not None else _RANK_MISSING,
        float(cog) if cog is not None else _RANK_MISSING,
        float(et) if et is not None else _RANK_MISSING,
    )


def rank_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合格エントリを辞書式ソートして `_rank` を付与、失格は末尾 (rank=None)。新リストを返す。"""
    pass_entries = [e for e in entries if e.get("verdict") == "pass"]
    fail_entries = [e for e in entries if e.get("verdict") != "pass"]
    pass_sorted = sorted(pass_entries, key=rank_sort_key)
    ranked: list[dict[str, Any]] = []
    for i, e in enumerate(pass_sorted, start=1):
        ne = dict(e)
        ne["_rank"] = i
        ranked.append(ne)
    for e in fail_entries:
        ne = dict(e)
        ne["_rank"] = None
        ranked.append(ne)
    return ranked


def submissions_dir(cfg: ScoreboardConfig) -> str:
    """history.json のあるディレクトリ配下の submissions/ パス（posix 形式）。"""
    parent = posix_dirname(cfg.path)
    return f"{parent}/submissions" if parent else "submissions"


def build_scoreboard_entry(
    *,
    author: str,
    note: str,
    input_filename: str,
    report: dict[str, Any],
    violations: list[Violation],
    files: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """評価結果から、リーダーボードに保存する 1 エントリを作る。"""
    entry: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "author": author,
        "note": note,
        "input_filename": input_filename,
        "timestamp": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": report["verdict"],
        "score": score_from_report(report),
        "violations_summary": [
            {"code": v.code, "container_id": v.container_id} for v in violations
        ],
    }
    if files:
        entry["files"] = files
    return entry


def upload_input_file(
    cfg: ScoreboardConfig,
    *,
    content_bytes: bytes,
    original_name: str,
    registry: dict[str, CanonicalDataset],
    role: str = "",
    source_dir: str = "",
) -> dict[str, Any]:
    """bytes をコンテンツハッシュで配置（既存なら再アップロードしない）。エントリ用 dict を返す。

    role="items_input" のときは canonical registry を引いて
    `content_hash` / `group_key` / `canonical_dataset_id` / `is_canonical` / `dataset_info`
    を付与する（リーダーボードのグループ化・正規問題判定に使用）。

    `source_dir` は MCP 経由提出元の親ディレクトリ名（例: `v2cmaes_hard01_beam`）。
    items_input.json がそれ自体に dataset_name 等を持たない / 系列名で絞り込みたい
    ケース向けに、UI label 優先表示用ヒントとして meta に保持する。UI ドラッグ提出
    は browser sandbox で取れないため省略（既存挙動と互換）。
    """
    digest = canonical_json_sha256(content_bytes)
    short = digest[:16]
    path = f"{submissions_dir(cfg)}/{short}.json"
    upload_if_absent(
        cfg,
        path,
        content_bytes,
        commit_message=f"scoreboard: upload input {short} ({original_name})",
    )
    meta: dict[str, Any] = {"path": path, "sha256": digest, "original_name": original_name}
    if source_dir:
        meta["source_dir"] = source_dir
    if role == "items_input":
        content_key = content_group_key(content_bytes)
        meta["content_hash"] = content_key
        meta["group_key"] = content_key  # 後方互換: 旧キー名も content hash で統一
        canon = registry.get(content_key)
        meta["canonical_dataset_id"] = canon.dataset_id if canon else None
        meta["is_canonical"] = canon is not None
        try:
            obj = json.loads(content_bytes.decode("utf-8-sig"))
            if isinstance(obj, dict) and isinstance(obj.get("dataset_info"), dict):
                meta["dataset_info"] = obj["dataset_info"]
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    return meta


def apply_canonical_gate(
    entries: list[dict[str, Any]],
    *,
    repo_root: Path,
    registry: dict[str, CanonicalDataset],
) -> list[dict[str, Any]]:
    """各エントリを items_input で再検証し、ランキング用に score / verdict を上書きする。

    元 entries は破壊しない（dict copy を返す）。詳細仕様は
    `gate_entry_against_canonical` の docstring と要件定義書 5.3 を参照。
    """
    out: list[dict[str, Any]] = []
    for e in entries:
        ne = dict(e)
        files = ne.get("files") or {}
        layout_rel = (files.get("layout_result") or {}).get("path")
        items_rel = (files.get("items_input") or {}).get("path")
        if not layout_rel:
            ne["verdict"] = "disqualified"
            ne["_gate"] = "schema_error"
            ne["_gate_reason"] = "layout_result 未記録（再検証不能）"
            out.append(ne)
            continue
        layout_path = repo_root / Path(layout_rel)
        items_path = (repo_root / Path(items_rel)) if items_rel else None
        res = gate_entry_against_canonical(layout_path, items_path, registry)
        ne["_gate"] = res.status
        ne["_gate_reason"] = res.reason
        if res.status in ("ok", "non_canonical") and res.rescored_report is not None:
            ne["score"] = score_from_report(res.rescored_report)
            ne["verdict"] = res.rescored_report.get("verdict", ne.get("verdict"))
            ne["_canonical_dataset_id"] = res.canonical.dataset_id if res.canonical else None
        else:
            ne["verdict"] = "disqualified"
        out.append(ne)
    return out
