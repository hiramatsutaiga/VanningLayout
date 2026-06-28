"""Unit tests for the leaderboard ranking (vanning_viewer.streamlit_app).

streamlit ランタイムを起動せずに helper だけインポートしてテストする。
helper は副作用なしの純関数なので import コストも低い。

旧仕様 (重み付き総合スコア式) のテストは PR #26 (要件定義書 5.3 改訂) に
合わせて廃止。本ファイルは辞書式順位付けの動作確認に絞る。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import vanning_viewer.streamlit_app as app
from vanning_viewer.streamlit_app import (
    RECOMMENDED_FILL_RATE,
    _apply_canonical_gate,
    _count_above_fill_threshold,
    _items_group_key,
    _rank_entries,
    _rank_sort_key,
)


def _entry(
    *,
    verdict: str = "pass",
    containers_used: int | None = 1,
    cog_y_mean_deviation: float | None = 0.0,
    execution_time_ms: int | None = 0,
    fill_rate_per_container: list[float] | None = None,
    author: str = "T",
) -> dict:
    """テスト用 entry dict builder."""
    score: dict = {
        "containers_used": containers_used,
        "cog_y_mean_deviation": cog_y_mean_deviation,
        "execution_time_ms": execution_time_ms,
    }
    if fill_rate_per_container is not None:
        score["fill_rate_per_container"] = fill_rate_per_container
    return {"verdict": verdict, "score": score, "author": author}


# ---------------------------------------------------------------------------
# _rank_sort_key
# ---------------------------------------------------------------------------


def test_rank_sort_key_orders_by_containers_first():
    """コンテナ数が主指標。少ない方が小さいキー。"""
    e_few = _entry(containers_used=2, cog_y_mean_deviation=5000, execution_time_ms=9999)
    e_many = _entry(containers_used=5, cog_y_mean_deviation=0, execution_time_ms=0)
    assert _rank_sort_key(e_few) < _rank_sort_key(e_many)


def test_rank_sort_key_tiebreaker_cog():
    """コンテナ数同点なら重心ズレ平均の小さい方が上位。"""
    e_better_cog = _entry(containers_used=3, cog_y_mean_deviation=500, execution_time_ms=9999)
    e_worse_cog = _entry(containers_used=3, cog_y_mean_deviation=1500, execution_time_ms=0)
    assert _rank_sort_key(e_better_cog) < _rank_sort_key(e_worse_cog)


def test_rank_sort_key_tiebreaker_execution_time():
    """コンテナ数も重心ズレも同点なら処理時間の短い方が上位。"""
    e_fast = _entry(containers_used=3, cog_y_mean_deviation=1000, execution_time_ms=100)
    e_slow = _entry(containers_used=3, cog_y_mean_deviation=1000, execution_time_ms=500)
    assert _rank_sort_key(e_fast) < _rank_sort_key(e_slow)


def test_rank_sort_key_missing_values_go_last():
    """値欠損のエントリは最後 (inf でソート末尾)。"""
    e_missing = _entry(containers_used=None, cog_y_mean_deviation=None, execution_time_ms=None)
    e_normal = _entry(containers_used=10, cog_y_mean_deviation=2999, execution_time_ms=99999)
    assert _rank_sort_key(e_normal) < _rank_sort_key(e_missing)


# ---------------------------------------------------------------------------
# _rank_entries
# ---------------------------------------------------------------------------


def test_rank_entries_assigns_rank_to_pass_only():
    entries = [
        _entry(verdict="pass", containers_used=3, author="A"),
        _entry(verdict="pass", containers_used=2, author="B"),
        _entry(verdict="disqualified", containers_used=1, author="C"),
    ]
    ranked = _rank_entries(entries)
    by_author = {e["author"]: e for e in ranked}
    assert by_author["B"]["_rank"] == 1  # 2 コンテナ
    assert by_author["A"]["_rank"] == 2  # 3 コンテナ
    assert by_author["C"]["_rank"] is None  # 失格


def test_rank_entries_failed_at_bottom():
    """失格は末尾。"""
    entries = [
        _entry(verdict="disqualified", containers_used=1, author="FAIL"),
        _entry(verdict="pass", containers_used=5, author="PASS"),
    ]
    ranked = _rank_entries(entries)
    assert ranked[0]["author"] == "PASS"
    assert ranked[1]["author"] == "FAIL"


def test_rank_entries_full_lexicographic():
    """3 段すべてが効く順位付け。"""
    entries = [
        _entry(
            verdict="pass",
            containers_used=3,
            cog_y_mean_deviation=2000,
            execution_time_ms=500,
            author="C",
        ),
        _entry(
            verdict="pass",
            containers_used=3,
            cog_y_mean_deviation=1000,
            execution_time_ms=999,
            author="B",
        ),
        _entry(
            verdict="pass",
            containers_used=2,
            cog_y_mean_deviation=5000,
            execution_time_ms=999,
            author="A",
        ),
    ]
    ranked = _rank_entries(entries)
    assert [e["author"] for e in ranked] == ["A", "B", "C"]
    assert [e["_rank"] for e in ranked] == [1, 2, 3]


def test_rank_entries_does_not_mutate_input():
    """入力 entries は変更しない (rank 付与は新 dict に)。"""
    entries = [_entry(verdict="pass", containers_used=1)]
    _rank_entries(entries)
    assert "_rank" not in entries[0]


# ---------------------------------------------------------------------------
# _count_above_fill_threshold
# ---------------------------------------------------------------------------


def test_count_above_fill_threshold_default_80():
    """デフォルト閾値 80%。"""
    score = {"fill_rate_per_container": [0.79, 0.80, 0.81, 1.0]}
    assert _count_above_fill_threshold(score) == 3


def test_count_above_fill_threshold_custom():
    score = {"fill_rate_per_container": [0.49, 0.50, 0.51]}
    assert _count_above_fill_threshold(score, threshold=0.50) == 2


def test_count_above_fill_threshold_legacy_returns_none():
    """per-container 値が無い旧エントリは None (UI で「-」表示)。"""
    assert _count_above_fill_threshold({}) is None


def test_count_above_fill_threshold_empty_list():
    """空リストは 0 (None ではない: 「データはあるが該当なし」)。"""
    assert _count_above_fill_threshold({"fill_rate_per_container": []}) == 0


def test_recommended_fill_rate_is_80_percent():
    """要件定義書 5.4 の努力目標は 80%。"""
    assert RECOMMENDED_FILL_RATE == 0.80


# ---------------------------------------------------------------------------
# canonical グルーピング / 整合ゲート（旧 dataset_info-only バグの回帰）
# ---------------------------------------------------------------------------

_ITEM = {
    "item_id": "P001",
    "size_type": "small",
    "dimensions": {"w": 760, "l": 1130, "h": 550},
    "weight": 100.0,
    "destination_id": "A",
}


def _items_doc(weight: float, *, seed: int = 42) -> dict[str, Any]:
    it = dict(_ITEM, weight=weight)
    return {
        "dataset_info": {"dataset_name": "case_01", "seed": seed, "item_count": 1},
        "items": [it],
    }


def test_items_group_key_content_sensitive():
    """同 dataset_info でも items 内容（weight）が違えば別グループ。

    旧バグ（dataset_info のみ hash → 軽い別 input が case_01 に混入）の回帰。
    """
    canonical = json.dumps(_items_doc(2558.63)).encode("utf-8")
    impostor = json.dumps(_items_doc(1778.33)).encode("utf-8")  # 軽い別 input
    assert _items_group_key(canonical) != _items_group_key(impostor)


def test_items_group_key_ignores_dataset_info():
    """items 同一なら dataset_info 申告が違っても同一グループ。"""
    a = json.dumps(_items_doc(100.0, seed=42)).encode("utf-8")
    b = json.dumps(_items_doc(100.0, seed=999)).encode("utf-8")
    assert _items_group_key(a) == _items_group_key(b)


def _layout_doc(item_id: str, *, weight: float = 100.0) -> dict[str, Any]:
    return {
        "project_info": {"team_name": "T", "execution_time_ms": 10},
        "containers": [
            {
                "container_id": 1,
                "destination_id": "A",
                "total_weight": weight,
                "items": [
                    {
                        "item_id": item_id,
                        "size_type": "small",
                        "dimensions": {"w": 760, "l": 1130, "h": 550},
                        "position": {"x": 0, "y": 5435, "z": 0},  # center_y≈6000
                        "weight": weight,
                        "is_rotated": False,
                        "destination_id": "A",
                    }
                ],
            }
        ],
    }


def test_apply_canonical_gate_demotes_polluted_entry(tmp_path: Path, monkeypatch):
    """canonical 一致エントリは "ok"、登録外 items 申告は "non_canonical" として
    新規 input group 扱い（input/output 整合なら合格維持・別グループ表示）。"""
    from vanning_eval.canonical_input import load_canonical_registry

    # tmp を REPO_ROOT に見立て、official canonical + 2 提出を配置
    off = tmp_path / "input" / "official_case_01_seed42"
    off.mkdir(parents=True)
    (off / "items_input.json").write_text(json.dumps(_items_doc(100.0)), encoding="utf-8")
    sub = tmp_path / "scoreboard" / "submissions"
    sub.mkdir(parents=True)
    (sub / "ok_layout.json").write_text(json.dumps(_layout_doc("P001")), encoding="utf-8")
    (sub / "ok_items.json").write_text(json.dumps(_items_doc(100.0)), encoding="utf-8")
    # bad 側は items_input が registry と内容不一致（non_canonical）かつ layout↔items
    # は内部整合（重量も一致）させる。weight を別値にして両方に同じく当てる。
    (sub / "bad_layout.json").write_text(
        json.dumps(_layout_doc("P001", weight=1778.33)), encoding="utf-8"
    )
    (sub / "bad_items.json").write_text(json.dumps(_items_doc(1778.33)), encoding="utf-8")

    registry = load_canonical_registry(tmp_path / "input")
    monkeypatch.setattr(app, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(app, "_load_registry", lambda: registry)

    def _entry_files(layout: str, items: str) -> dict[str, Any]:
        return {
            "verdict": "pass",
            "score": {"containers_used": 1, "cog_y_mean_deviation": 0, "execution_time_ms": 5},
            "files": {
                "layout_result": {"path": f"scoreboard/submissions/{layout}"},
                "items_input": {"path": f"scoreboard/submissions/{items}"},
            },
        }

    gated = _apply_canonical_gate([
        _entry_files("ok_layout.json", "ok_items.json"),
        _entry_files("bad_layout.json", "bad_items.json"),
    ])
    ok, bad = gated
    assert ok["_gate"] == "ok" and ok["verdict"] == "pass"
    # 新ポリシー: 登録外 items は新規 input group として扱い、layout↔items が
    # 整合していれば合格を維持する（items_input グループでフィルタすれば
    # canonical 群と別土俵で表示される）。
    assert bad["_gate"] == "non_canonical" and bad["verdict"] == "pass"
