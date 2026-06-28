"""canonical_input の正規化ハッシュ・registry・解決のテスト。

旧バグ（dataset_info のみ hash で軽い別 items が同一グループ化）の回帰を
含む。純関数なので streamlit ランタイム不要。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vanning_eval.canonical_input import (
    content_group_key,
    load_canonical_registry,
    normalize_items_payload,
    resolve_canonical,
)


def _item(
    item_id: str, w: int, length: int, h: int, weight: float, dest: str
) -> dict[str, Any]:
    size = "small" if w <= 800 else ("large" if w >= 2000 else "medium")
    return {
        "item_id": item_id,
        "size_type": size,
        "dimensions": {"w": w, "l": length, "h": h},
        "weight": weight,
        "destination_id": dest,
    }


def _payload(items: list[dict[str, Any]], *, seed: int = 42) -> dict[str, Any]:
    return {
        "dataset_info": {"dataset_name": "case_01", "seed": seed, "item_count": len(items)},
        "items": items,
    }


def test_normalize_items_payload_order_independent() -> None:
    """items の並び順を入れ替えても content hash は不変。"""
    a = _payload([_item("P001", 760, 1130, 550, 100.0, "A"),
                  _item("P002", 1490, 2260, 900, 200.0, "B")])
    b = _payload([_item("P002", 1490, 2260, 900, 200.0, "B"),
                  _item("P001", 760, 1130, 550, 100.0, "A")])
    assert normalize_items_payload(a) == normalize_items_payload(b)


def test_normalize_payload_ignores_dataset_info() -> None:
    """dataset_info（申告メタ）が違っても items 同一なら同ハッシュ。"""
    items = [_item("P001", 760, 1130, 550, 100.0, "A")]
    a = _payload(items, seed=42)
    b = _payload(items, seed=999)
    b["dataset_info"]["dataset_name"] = "totally_different"
    assert normalize_items_payload(a) == normalize_items_payload(b)


def test_weight_change_above_1g_changes_hash() -> None:
    """weight が 1g 超変われば別ハッシュ（軽い偽 input を弾く回帰）。"""
    base = _payload([_item("P001", 760, 1130, 550, 1778.330, "A")])
    heavier = _payload([_item("P001", 760, 1130, 550, 2558.630, "A")])
    assert normalize_items_payload(base) != normalize_items_payload(heavier)


def test_weight_subgram_jitter_is_absorbed() -> None:
    """weight の <1g 揺れ（再生成の丸め）では同ハッシュ。"""
    a = _payload([_item("P001", 760, 1130, 550, 100.0000, "A")])
    b = _payload([_item("P001", 760, 1130, 550, 100.0004, "A")])
    assert normalize_items_payload(a) == normalize_items_payload(b)


def test_content_group_key_fallback_on_broken_json() -> None:
    """壊れた JSON は例外でなくフォールバックハッシュ（別グループ化）。"""
    k = content_group_key(b"{not valid json")
    assert isinstance(k, str) and len(k) == 64  # sha256 hex


def _write_official(root: Path, name: str, items: list[dict[str, Any]]) -> Path:
    d = root / f"official_{name}"
    d.mkdir(parents=True)
    f = d / "items_input.json"
    f.write_text(json.dumps(_payload(items)), encoding="utf-8")
    return f


def test_registry_resolves_registered_and_rejects_impostor(tmp_path: Path) -> None:
    """登録 canonical は解決され、同じ dataset_info を申告した別 items
    （= 軽い 14 箱なりすまし系）は resolve_canonical=None。"""
    canonical_items = [_item("P001", 2280, 2550, 2355, 2558.63, "C"),
                       _item("P002", 760, 1130, 550, 300.0, "A")]
    _write_official(tmp_path, "case_01_seed42", canonical_items)
    registry = load_canonical_registry(tmp_path)
    assert len(registry) == 1

    canonical_bytes = json.dumps(_payload(canonical_items)).encode("utf-8")
    resolved = resolve_canonical(canonical_bytes, registry)
    assert resolved is not None
    assert resolved.dataset_id == "official_case_01_seed42"

    # 同 dataset_info を申告するが weight が軽い別 input（なりすまし）
    impostor_items = [_item("P001", 2280, 2550, 2355, 1778.33, "C"),
                      _item("P002", 760, 1130, 550, 300.0, "A")]
    impostor_bytes = json.dumps(_payload(impostor_items)).encode("utf-8")
    assert resolve_canonical(impostor_bytes, registry) is None


def test_registry_skips_non_official_dirs(tmp_path: Path) -> None:
    """official_ プレフィックスでないフォルダは ground truth に含めない。"""
    (tmp_path / "shisa").mkdir()
    (tmp_path / "shisa" / "items_input.json").write_text(
        json.dumps(_payload([_item("P001", 760, 1130, 550, 100.0, "A")])),
        encoding="utf-8",
    )
    assert load_canonical_registry(tmp_path) == {}
