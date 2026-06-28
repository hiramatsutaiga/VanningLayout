"""ハード制約チェック。

各チェックは `Violation` のリストを返す（空なら合格）。いずれかのチェックで
リストが非空になった時点で、その積付全体を失格扱い（スコア 0）とする。

実装済みチェック:
1. コンテナ範囲外（out-of-bounds）
2. 同一コンテナ内の積荷同士の重なり
3. 同一コンテナ内での配送先混載
4. 重量超過
5. 浮遊・ピラミッド禁止（積み上げた荷物は支持荷物の上に完全に載ること）
6. 重心違反（Y重心が中心から ±25% 超）
7. 全荷物配置チェック（`items_input.json` が必要）

スキーマレベルの妥当性は `schema.load_layout` で先に検証される。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypedDict

from .schema import (
    Container,
    ContainerSpec,
    ItemsInput,
    LayoutResult,
    Placement,
)

# 重心評価の許容限界（コンテナ長の 25%）。40ft では 3000mm。
# 要件定義書 PR #26 (3 段階評価を廃止し合否 2 値判定へ変更) を参照。
COG_ACCEPTABLE_RATIO = 0.25

# Declared `total_weight` vs summed-item-weight の許容誤差（kg）。
# float 合算の丸めを吸収するためのしきい値で、運用上の意味は「事実上一致」。
WEIGHT_DECLARATION_TOLERANCE = 0.5


class ViolationCode(StrEnum):
    """ハード制約違反の識別コード。

    StrEnum なので str と互換で、既存の JSON 出力や UI 辞書参照
    （colors.VIOLATION_LABELS_JA 等）とシームレスに動く。新しい制約を
    追加するときは、ここに値を追加してからチェック関数を書くこと。
    """

    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
    OVERLAP = "OVERLAP"
    DESTINATION_MIX = "DESTINATION_MIX"
    WEIGHT_OVER = "WEIGHT_OVER"
    WEIGHT_DECLARATION_MISMATCH = "WEIGHT_DECLARATION_MISMATCH"
    FLOATING = "FLOATING"
    COG_VIOLATION = "COG_VIOLATION"
    MISSING_ITEMS = "MISSING_ITEMS"
    UNKNOWN_ITEMS = "UNKNOWN_ITEMS"
    DUPLICATE_ITEMS = "DUPLICATE_ITEMS"
    ITEM_ATTR_MISMATCH = "ITEM_ATTR_MISMATCH"


# ------------------------------------------------------------------
# Violation.detail の code 別スキーマ（TypedDict）。
#
# `detail: dict[str, Any]` は JSON 境界の柔軟性のため総称型のまま保持するが、
# 各 code で期待される形を TypedDict として公開しておくことで、下流 UI や
# report 側で `cast()` 付きの読み出しができる。新しい code を追加するときは
# 対応する TypedDict をここに追加すること。
# ------------------------------------------------------------------


class OutOfBoundsDetail(TypedDict):
    bounds: list[int]  # [W, L, H]
    min: list[int]  # [x_min, y_min, z_min]
    max: list[int]  # [x_max, y_max, z_max]


class DestinationMixDetail(TypedDict):
    container_destination: str


class WeightOverDetail(TypedDict):
    actual: float
    limit: int


class WeightMismatchDetail(TypedDict):
    declared: float
    actual: float


class FloatingDetail(TypedDict):
    z: int
    support_count: int


class CogViolationDetail(TypedDict):
    yg: float
    yc: float
    deviation: float
    limit: int


class ItemAttrMismatchDetail(TypedDict):
    """layout の placement と items_input の宣言が食い違ったフィールド。

    `field` は "weight" / "dimensions" / "destination_id" / "size_type" のいずれか。
    `declared` は items_input が宣言した値、`actual` は layout が記録した値。
    旧 input の layout を別 items_input と紐付けで提出する不正を検出するための情報。
    """

    field: str
    declared: Any
    actual: Any


# OVERLAP / MISSING_ITEMS / UNKNOWN_ITEMS / DUPLICATE_ITEMS は
# detail を持たない（items フィールドだけで十分な情報になる）。


@dataclass
class Violation:
    code: ViolationCode
    container_id: int | None
    items: list[str] = field(default_factory=list)
    # code 別の期待スキーマは上記 TypedDict 群を参照。runtime は dict[str, Any]。
    detail: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------
# Individual checks
# ------------------------------------------------------------------


def check_out_of_bounds(layout: LayoutResult) -> list[Violation]:
    """Every placement must fit inside [0, W) x [0, L) x [0, H)."""
    violations: list[Violation] = []
    spec = layout.spec
    for container in layout.containers:
        for p in container.items:
            if (
                p.x_min < 0
                or p.y_min < 0
                or p.z_min < 0
                or p.x_max > spec.w
                or p.y_max > spec.l
                or p.z_max > spec.h
            ):
                violations.append(
                    Violation(
                        code=ViolationCode.OUT_OF_BOUNDS,
                        container_id=container.container_id,
                        items=[p.item_id],
                        detail={
                            "bounds": [spec.w, spec.l, spec.h],
                            "min": [p.x_min, p.y_min, p.z_min],
                            "max": [p.x_max, p.y_max, p.z_max],
                        },
                    )
                )
    return violations


def _aabb_overlap(a: Placement, b: Placement) -> bool:
    return (
        a.x_min < b.x_max
        and b.x_min < a.x_max
        and a.y_min < b.y_max
        and b.y_min < a.y_max
        and a.z_min < b.z_max
        and b.z_min < a.z_max
    )


def check_overlap(layout: LayoutResult) -> list[Violation]:
    """No two items within a single container may share interior volume."""
    violations: list[Violation] = []
    for container in layout.containers:
        items = container.items
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if _aabb_overlap(items[i], items[j]):
                    violations.append(
                        Violation(
                            code=ViolationCode.OVERLAP,
                            container_id=container.container_id,
                            items=[items[i].item_id, items[j].item_id],
                        )
                    )
    return violations


def check_destination(layout: LayoutResult) -> list[Violation]:
    """Every item in a container must share the container's destination_id."""
    violations: list[Violation] = []
    for container in layout.containers:
        mismatched = [
            p.item_id for p in container.items if p.destination_id != container.destination_id
        ]
        if mismatched:
            violations.append(
                Violation(
                    code=ViolationCode.DESTINATION_MIX,
                    container_id=container.container_id,
                    items=mismatched,
                    detail={"container_destination": container.destination_id},
                )
            )
    return violations


def check_weight(layout: LayoutResult) -> list[Violation]:
    """Total weight per container must not exceed CONTAINER_MAX_WEIGHT.

    Also flags when the declared `total_weight` disagrees with the sum of
    item weights (indicates a broken producer).
    """
    violations: list[Violation] = []
    limit = layout.spec.max_weight
    for container in layout.containers:
        actual = sum(p.weight for p in container.items)
        if actual > limit:
            violations.append(
                Violation(
                    code=ViolationCode.WEIGHT_OVER,
                    container_id=container.container_id,
                    items=[p.item_id for p in container.items],
                    detail={"actual": actual, "limit": limit},
                )
            )
        if abs(actual - container.total_weight) > WEIGHT_DECLARATION_TOLERANCE:
            violations.append(
                Violation(
                    code=ViolationCode.WEIGHT_DECLARATION_MISMATCH,
                    container_id=container.container_id,
                    detail={"declared": container.total_weight, "actual": actual},
                )
            )
    return violations


def _footprint_fully_supported(top: Placement, supports: list[Placement]) -> bool:
    """Return True if top's XY footprint is fully covered by the union of support tops.

    `supports` must be items whose top surface sits exactly at `top.z_min`.
    The coverage test subtracts each support's rectangle from `top`'s footprint
    and fails if any area remains.
    """
    remaining: list[tuple[int, int, int, int]] = [(top.x_min, top.y_min, top.x_max, top.y_max)]
    for s in supports:
        sx1, sy1, sx2, sy2 = s.x_min, s.y_min, s.x_max, s.y_max
        next_remaining: list[tuple[int, int, int, int]] = []
        for rx1, ry1, rx2, ry2 in remaining:
            # Intersection
            ix1 = max(rx1, sx1)
            iy1 = max(ry1, sy1)
            ix2 = min(rx2, sx2)
            iy2 = min(ry2, sy2)
            if ix1 >= ix2 or iy1 >= iy2:
                # No intersection; whole rect stays as remaining.
                next_remaining.append((rx1, ry1, rx2, ry2))
                continue
            # Split the remaining rect into up to 4 pieces around the intersection.
            if ry1 < iy1:
                next_remaining.append((rx1, ry1, rx2, iy1))
            if iy2 < ry2:
                next_remaining.append((rx1, iy2, rx2, ry2))
            if rx1 < ix1:
                next_remaining.append((rx1, iy1, ix1, iy2))
            if ix2 < rx2:
                next_remaining.append((ix2, iy1, rx2, iy2))
        remaining = next_remaining
        if not remaining:
            return True
    return not remaining


def check_floating(layout: LayoutResult) -> list[Violation]:
    """z>0 items must rest fully on the tops of lower items (pyramid rule)."""
    violations: list[Violation] = []
    for container in layout.containers:
        for top in container.items:
            if top.z_min == 0:
                continue
            supports = [
                other for other in container.items if other is not top and other.z_max == top.z_min
            ]
            if not supports or not _footprint_fully_supported(top, supports):
                violations.append(
                    Violation(
                        code=ViolationCode.FLOATING,
                        container_id=container.container_id,
                        items=[top.item_id],
                        detail={
                            "z": top.z_min,
                            "support_count": len(supports),
                        },
                    )
                )
    return violations


def _cog_y_acceptable_mm(spec: ContainerSpec) -> int:
    """重心の許容限界（コンテナ長の 25%、40ft では 3000mm）。"""
    return int(spec.l * COG_ACCEPTABLE_RATIO)


def _container_cog_y(container: Container, spec: ContainerSpec) -> float:
    """積荷の重量加重 Y 重心。空コンテナはコンテナ幾何中心を返す。"""
    total_weight = sum(p.weight for p in container.items)
    if total_weight == 0:
        return spec.center_y
    weighted = sum(p.center_y * p.weight for p in container.items)
    return weighted / total_weight


def check_cog_violation(layout: LayoutResult) -> list[Violation]:
    """各コンテナの Y 重心が中心から ±25% (40ft で ±3000mm) 以内に収まらない場合は失格。

    要件定義書 PR #26 で 3 段階評価を廃止し、合格範囲外を失格扱いとする運用に変更。
    """
    violations: list[Violation] = []
    spec = layout.spec
    limit = _cog_y_acceptable_mm(spec)
    yc = spec.center_y
    for container in layout.containers:
        yg = _container_cog_y(container, spec)
        deviation = abs(yg - yc)
        if deviation > limit:
            violations.append(
                Violation(
                    code=ViolationCode.COG_VIOLATION,
                    container_id=container.container_id,
                    items=[p.item_id for p in container.items],
                    detail={
                        "yg": yg,
                        "yc": yc,
                        "deviation": deviation,
                        "limit": limit,
                    },
                )
            )
    return violations


def check_item_attr_consistency(
    layout: LayoutResult, items_input: ItemsInput | None
) -> list[Violation]:
    """各 placement の (weight, dimensions, destination_id, size_type) が
    items_input の宣言と一致するか検証。

    layout 側で属性を書き換えて weight cap / floating / COG をすり抜ける不正
    （例: 別 input で解いた layout を別 items_input と紐付けで提出）を検出する。
    item_id が items_input に存在しない placement はここではスキップする
    （`check_complete_placement` の UNKNOWN_ITEMS で別途検出される）。
    weight 比較は `WEIGHT_DECLARATION_TOLERANCE` (float 丸め吸収) を流用。

    回転扱い: `is_rotated=True` の placement は declared (w, l) を入れ替えて比較
    （高さ h は不変）。要件定義書 Section 3.4 と CLAUDE.md の座標系約束に従う。
    """
    if items_input is None:
        return []
    by_id: dict[str, Any] = {it.item_id: it for it in items_input.items}
    violations: list[Violation] = []
    for container in layout.containers:
        for p in container.items:
            decl = by_id.get(p.item_id)
            if decl is None:
                continue  # UNKNOWN_ITEMS が check_complete_placement で検出
            mismatches: list[tuple[str, Any, Any]] = []
            if abs(p.weight - decl.weight) > WEIGHT_DECLARATION_TOLERANCE:
                mismatches.append(("weight", decl.weight, p.weight))
            if p.is_rotated:
                exp_w, exp_l = decl.dimensions.l, decl.dimensions.w
            else:
                exp_w, exp_l = decl.dimensions.w, decl.dimensions.l
            exp_h = decl.dimensions.h
            actual_dims = [p.dimensions.w, p.dimensions.l, p.dimensions.h]
            if actual_dims != [exp_w, exp_l, exp_h]:
                mismatches.append(("dimensions", [exp_w, exp_l, exp_h], actual_dims))
            if p.destination_id != decl.destination_id:
                mismatches.append(("destination_id", decl.destination_id, p.destination_id))
            if p.size_type != decl.size_type:
                mismatches.append(("size_type", decl.size_type, p.size_type))
            for field_name, declared, actual in mismatches:
                violations.append(
                    Violation(
                        code=ViolationCode.ITEM_ATTR_MISMATCH,
                        container_id=container.container_id,
                        items=[p.item_id],
                        detail={"field": field_name, "declared": declared, "actual": actual},
                    )
                )
    return violations


def check_complete_placement(
    layout: LayoutResult, items_input: ItemsInput | None
) -> list[Violation]:
    """All items from the input dataset must be placed exactly once.

    このチェックは他 5 種（OOB / OVERLAP / DEST_MIX / WEIGHT / FLOATING）と層が違い、
    layout と items_input の「ID 整合性」を検証するスキーマ横断チェックである。
    そのため Violation の container_id は常に None（特定コンテナに紐付かない）。
    違反コード 3 種（MISSING_ITEMS / UNKNOWN_ITEMS / DUPLICATE_ITEMS）はすべて
    ここから発火する。items_input が None の場合は静かにスキップする。
    """
    if items_input is None:
        return []
    violations: list[Violation] = []
    placed_ids: list[str] = []
    for container in layout.containers:
        placed_ids.extend(p.item_id for p in container.items)
    placed_set = set(placed_ids)
    expected_set = {it.item_id for it in items_input.items}

    missing = sorted(expected_set - placed_set)
    extra = sorted(placed_set - expected_set)
    duplicates = sorted({i for i in placed_ids if placed_ids.count(i) > 1})

    if missing:
        violations.append(
            Violation(code=ViolationCode.MISSING_ITEMS, container_id=None, items=missing)
        )
    if extra:
        violations.append(
            Violation(code=ViolationCode.UNKNOWN_ITEMS, container_id=None, items=extra)
        )
    if duplicates:
        violations.append(
            Violation(code=ViolationCode.DUPLICATE_ITEMS, container_id=None, items=duplicates)
        )
    return violations


# ------------------------------------------------------------------
# Aggregate
# ------------------------------------------------------------------


CheckFn = Callable[[LayoutResult, ItemsInput | None], list[Violation]]

# 新しい制約を追加するときは、ここに関数を追加するだけで run_all_checks が拾う。
# 全チェックのシグネチャを (layout, items_input) に統一しておくことで、
# items_input を使わないチェックも items_input を使うチェックも同じレジストリに並ぶ。
CHECKS: list[CheckFn] = [
    lambda layout, _items: check_out_of_bounds(layout),
    lambda layout, _items: check_overlap(layout),
    lambda layout, _items: check_destination(layout),
    lambda layout, _items: check_weight(layout),
    lambda layout, _items: check_floating(layout),
    lambda layout, _items: check_cog_violation(layout),
    check_complete_placement,
    check_item_attr_consistency,
]


def run_all_checks(layout: LayoutResult, items_input: ItemsInput | None = None) -> list[Violation]:
    """Run every hard-constraint check and return the combined list of violations."""
    all_violations: list[Violation] = []
    for check in CHECKS:
        all_violations.extend(check(layout, items_input))
    return all_violations


def summarize_containers(layout: LayoutResult) -> dict[int, Container]:
    """Helper: map container_id -> Container for lookup."""
    return {c.container_id: c for c in layout.containers}
