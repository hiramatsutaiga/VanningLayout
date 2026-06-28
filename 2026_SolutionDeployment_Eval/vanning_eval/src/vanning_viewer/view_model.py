"""描画非依存の ViewModel 層。

`LayoutResult` と評価結果（違反・教員メトリクス）を、描画ライブラリに依存しない
frozen dataclass へ変換する。Plotly / Streamlit / Three.js など、どのレンダラも
この型だけを入力に取る。設計詳細は docs/VIEWER_DESIGN.md 参照。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from vanning_eval.constraints import Violation, ViolationCode, run_all_checks
from vanning_eval.schema import (
    LayoutResult,
    Placement,
)
from vanning_eval.scoring import TeacherScoreMetrics, compute_teacher_metrics

from .colors import (
    FALLBACK_COLOR,
    VIOLATION_COLOR,
    build_destination_color_map,
    ja_violation_label,
    opacity_for_size,
)


@dataclass(frozen=True)
class ViewBox:
    """描画用の 1 アイテム分スナップショット。"""

    item_id: str
    x: float
    y: float
    z: float
    w: float
    l: float  # noqa: E741 - spec uses "l" for long side
    h: float
    color: str
    opacity: float
    label: str  # hover 用（HTML 改行可）
    violated: bool
    violation_codes: tuple[ViolationCode, ...]
    destination_id: str
    size_type: str
    weight_kg: float
    is_rotated: bool


@dataclass(frozen=True)
class ViewContainer:
    container_id: int
    destination_id: str
    w: float
    l: float  # noqa: E741
    h: float
    boxes: tuple[ViewBox, ...]
    fill_rate: float
    cog_y_offset_mm: float
    total_weight_kg: float


@dataclass(frozen=True)
class ViewScene:
    containers: tuple[ViewContainer, ...]
    verdict: str  # "pass" | "disqualified"
    disqualification_count: int
    summary: Mapping[str, object]


def _hover_label(p: Placement, codes: tuple[ViolationCode, ...]) -> str:
    rotated = "回転済み" if p.is_rotated else "通常向き"
    lines = [
        f"<b>{p.item_id}</b> ({p.size_type}, {rotated})",
        f"配送先: {p.destination_id}",
        f"位置 (mm): x={p.position.x}, y={p.position.y}, z={p.position.z}",
        f"寸法 (mm): w={p.dimensions.w}, l={p.dimensions.l}, h={p.dimensions.h}",
        f"重量: {p.weight:.1f} kg",
    ]
    if codes:
        ja = "、".join(ja_violation_label(c) for c in codes)
        lines.append(f"<b style='color:{VIOLATION_COLOR}'>違反: {ja}</b>")
    return "<br>".join(lines)


def _violation_index(violations: list[Violation]) -> dict[str, list[ViolationCode]]:
    """item_id → 違反コードのリスト（登場順・重複削除）。"""
    result: dict[str, list[ViolationCode]] = {}
    for v in violations:
        for item_id in v.items:
            lst = result.setdefault(item_id, [])
            if v.code not in lst:
                lst.append(v.code)
    return result


def build_scene(
    layout: LayoutResult,
    *,
    violations: list[Violation] | None = None,
    teacher: TeacherScoreMetrics | None = None,
) -> ViewScene:
    """LayoutResult + 評価結果 → ViewScene。

    violations / teacher が None なら layout から直接計算する（items_input 非依存の
    チェックのみが走る点に注意 — 完全配置チェックは caller 側で渡す必要がある）。
    """
    if violations is None:
        violations = run_all_checks(layout, None)
    if teacher is None:
        teacher = compute_teacher_metrics(layout)

    violation_codes = _violation_index(violations)
    dest_colors = build_destination_color_map(c.destination_id for c in layout.containers)
    fill_by_id = {entry.container_id: entry.fill_rate for entry in teacher.fill_rates}
    cog_by_id = {entry.container_id: entry.deviation for entry in teacher.cog_y}

    spec = layout.spec
    view_containers: list[ViewContainer] = []
    for c in layout.containers:
        boxes: list[ViewBox] = []
        for p in c.items:
            codes = tuple(violation_codes.get(p.item_id, ()))
            violated = len(codes) > 0
            color = (
                VIOLATION_COLOR if violated else dest_colors.get(p.destination_id, FALLBACK_COLOR)
            )
            boxes.append(
                ViewBox(
                    item_id=p.item_id,
                    x=float(p.position.x),
                    y=float(p.position.y),
                    z=float(p.position.z),
                    w=float(p.dimensions.w),
                    l=float(p.dimensions.l),
                    h=float(p.dimensions.h),
                    color=color,
                    opacity=opacity_for_size(p.size_type),
                    label=_hover_label(p, codes),
                    violated=violated,
                    violation_codes=codes,
                    destination_id=p.destination_id,
                    size_type=p.size_type,
                    weight_kg=p.weight,
                    is_rotated=p.is_rotated,
                )
            )
        offset = cog_by_id.get(c.container_id, 0.0)
        view_containers.append(
            ViewContainer(
                container_id=c.container_id,
                destination_id=c.destination_id,
                w=float(spec.w),
                l=float(spec.l),
                h=float(spec.h),
                boxes=tuple(boxes),
                fill_rate=float(fill_by_id.get(c.container_id, 0.0)),
                cog_y_offset_mm=float(offset),
                total_weight_kg=c.total_weight,
            )
        )

    return ViewScene(
        containers=tuple(view_containers),
        verdict="disqualified" if violations else "pass",
        disqualification_count=len(violations),
        summary={
            "container_count": len(layout.containers),
            "item_count": sum(len(c.items) for c in layout.containers),
            "average_fill_rate": teacher.average_fill_rate,
            "center_y_reference_mm": spec.center_y,
        },
    )
