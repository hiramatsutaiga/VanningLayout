"""gate_entry_against_canonical の分岐テスト（整合ゲート）。

ok / no_items_input / non_canonical / item_mismatch を網羅。
canonical items_input で再検証する穴塞ぎの中核。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vanning_eval.canonical_input import (
    gate_entry_against_canonical,
    load_canonical_registry,
)


def _items_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "dataset_info": {"dataset_name": "case_01", "seed": 42, "item_count": len(items)},
        "items": items,
    }


_P001 = {
    "item_id": "P001",
    "size_type": "small",
    "dimensions": {"w": 760, "l": 1130, "h": 550},
    "weight": 100.0,
    "destination_id": "A",
}


def _layout(placed_item_id: str, *, weight: float = 100.0) -> dict[str, Any]:
    """1 コンテナに *placed_item_id* を重心中央付近で置いた合格 layout。

    `weight` で placement と container.total_weight を一括上書きできる
    （items_input と layout を内部整合させたいテスト向け）。
    """
    return {
        "project_info": {"team_name": "T", "execution_time_ms": 10},
        "containers": [
            {
                "container_id": 1,
                "destination_id": "A",
                "total_weight": weight,
                "items": [
                    {
                        "item_id": placed_item_id,
                        "size_type": "small",
                        "dimensions": {"w": 760, "l": 1130, "h": 550},
                        # center_y = 5435 + 1130/2 = 6000 → COG 偏差 ~0
                        "position": {"x": 0, "y": 5435, "z": 0},
                        "weight": weight,
                        "is_rotated": False,
                        "destination_id": "A",
                    }
                ],
            }
        ],
    }


def _write(p: Path, obj: dict[str, Any]) -> Path:
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _registry_with_canonical(tmp_path: Path) -> tuple[dict, Path]:
    d = tmp_path / "input" / "official_case_01_seed42"
    d.mkdir(parents=True)
    _write(d / "items_input.json", _items_payload([_P001]))
    registry = load_canonical_registry(tmp_path / "input")
    assert len(registry) == 1
    return registry, tmp_path


def test_gate_no_items_input(tmp_path: Path) -> None:
    """items_input 未提出 → no_items_input（非ランキング）。"""
    registry, _ = _registry_with_canonical(tmp_path)
    layout = _write(tmp_path / "layout.json", _layout("P001"))
    res = gate_entry_against_canonical(layout, None, registry)
    assert res.status == "no_items_input"
    assert res.rescored_report is None


def test_gate_non_canonical(tmp_path: Path) -> None:
    """提出 items が registry と内容不一致でも、layout と内部整合していれば
    non_canonical + 提出 items 基準の rescored_report を返す（新規 input group）。

    layout 側の weight も impostor に合わせて書く（check_item_attr_consistency が
    本物の input-vs-layout 不整合を ITEM_ATTR_MISMATCH=item_mismatch として
    別途検出するため、ここは "内部整合の取れた非正規入力" を正確に再現する）。
    """
    registry, _ = _registry_with_canonical(tmp_path)
    layout = _write(tmp_path / "layout.json", _layout("P001", weight=1778.33))
    impostor = dict(_P001, weight=1778.33)  # 別 input（重量だけ違う）
    items = _write(tmp_path / "items.json", _items_payload([impostor]))
    res = gate_entry_against_canonical(layout, items, registry)
    assert res.status == "non_canonical"
    assert res.canonical is None
    assert res.rescored_report is not None


def test_gate_item_mismatch_attr_weight(tmp_path: Path) -> None:
    """canonical と一致する items を申告しつつ layout の weight が宣言と食い違う
    → item_mismatch（layout 内の weight 書き換えで weight cap をすり抜ける不正の検出）。
    """
    registry, _ = _registry_with_canonical(tmp_path)
    layout = _write(tmp_path / "layout.json", _layout("P001", weight=2558.63))
    items = _write(tmp_path / "items.json", _items_payload([_P001]))  # weight=100.0
    res = gate_entry_against_canonical(layout, items, registry)
    assert res.status == "item_mismatch"
    assert res.canonical is not None
    assert res.rescored_report is not None
    codes = {d["code"] for d in res.rescored_report["disqualifications"]}
    assert "ITEM_ATTR_MISMATCH" in codes


def test_gate_non_canonical_item_mismatch(tmp_path: Path) -> None:
    """registry 不一致 かつ layout が提出 items と item 集合不一致
    → item_mismatch（新規 input でも内部整合性チェックは効く）。"""
    registry, _ = _registry_with_canonical(tmp_path)
    layout = _write(tmp_path / "layout.json", _layout("X999"))  # P002 でない item
    impostor = dict(_P001, item_id="P002", weight=1778.33)
    items = _write(tmp_path / "items.json", _items_payload([impostor]))
    res = gate_entry_against_canonical(layout, items, registry)
    assert res.status == "item_mismatch"
    assert res.canonical is None


def test_gate_item_mismatch(tmp_path: Path) -> None:
    """canonical と一致する items を申告しつつ layout は別 item を配置
    → item_mismatch（別 input で解いた layout の申告を検出）。"""
    registry, _ = _registry_with_canonical(tmp_path)
    layout = _write(tmp_path / "layout.json", _layout("X999"))  # P001 でない
    items = _write(tmp_path / "items.json", _items_payload([_P001]))
    res = gate_entry_against_canonical(layout, items, registry)
    assert res.status == "item_mismatch"
    assert res.canonical is not None


def test_gate_ok(tmp_path: Path) -> None:
    """canonical 一致 & layout が canonical item を配置 → ok + 再score。"""
    registry, _ = _registry_with_canonical(tmp_path)
    layout = _write(tmp_path / "layout.json", _layout("P001"))
    items = _write(tmp_path / "items.json", _items_payload([_P001]))
    res = gate_entry_against_canonical(layout, items, registry)
    assert res.status == "ok"
    assert res.rescored_report is not None
    assert res.rescored_report["verdict"] == "pass"
    assert res.canonical is not None and res.canonical.dataset_id == "official_case_01_seed42"
