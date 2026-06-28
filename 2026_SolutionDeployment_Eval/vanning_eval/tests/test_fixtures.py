"""`input/test/` フィクスチャの回帰テスト。

input/test/ は「全違反コードを発火する保証フィクスチャ」として固定する。
新しい違反コードを追加するときは `ViolationCode` と合わせてこの
フィクスチャも更新し、`EXPECTED_CODES` を拡張すること。
"""

from __future__ import annotations

from pathlib import Path

from vanning_eval.constraints import ViolationCode, run_all_checks
from vanning_eval.schema import load_items, load_layout

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "input" / "test"

EXPECTED_CODES: set[str] = {
    ViolationCode.OUT_OF_BOUNDS,
    ViolationCode.OVERLAP,
    ViolationCode.DESTINATION_MIX,
    ViolationCode.WEIGHT_OVER,
    ViolationCode.WEIGHT_DECLARATION_MISMATCH,
    ViolationCode.FLOATING,
    ViolationCode.COG_VIOLATION,
    ViolationCode.MISSING_ITEMS,
    ViolationCode.UNKNOWN_ITEMS,
    ViolationCode.DUPLICATE_ITEMS,
    ViolationCode.ITEM_ATTR_MISMATCH,
}


def test_full_violations_fixture_fires_all_codes() -> None:
    """input/test/ のフィクスチャで ViolationCode 全種が過不足なく発火する。"""
    layout = load_layout(FIXTURE_DIR / "layout_result.json")
    items = load_items(FIXTURE_DIR / "items_input.json")
    codes = {v.code for v in run_all_checks(layout, items)}
    assert codes == EXPECTED_CODES


def test_expected_codes_covers_all_enum_members() -> None:
    """ViolationCode に追加されたコードは必ずフィクスチャの EXPECTED_CODES にも反映する。

    このテストが落ちたら、新 code 追加時に input/test/ の更新を忘れている合図。
    """
    all_enum_values = {c.value for c in ViolationCode}
    assert all_enum_values == EXPECTED_CODES
