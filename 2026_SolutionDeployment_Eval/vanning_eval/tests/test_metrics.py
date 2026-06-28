"""Unit tests for metrics (internal metrics)."""

from __future__ import annotations

from vanning_eval.metrics import compute_internal_metrics
from vanning_eval.schema import (
    CONTAINER_SPEC_40FT,
    Container,
    Dimensions,
    LayoutResult,
    Placement,
    Position,
    ProjectInfo,
)


def _placement(
    item_id: str,
    x: int,
    y: int,
    z: int,
    w: int,
    l: int,  # noqa: E741
    h: int,
    weight: float = 100.0,
) -> Placement:
    return Placement(
        item_id=item_id,
        size_type="small",
        dimensions=Dimensions(w=w, l=l, h=h),
        position=Position(x=x, y=y, z=z),
        weight=weight,
        is_rotated=False,
        destination_id="DEST_A",
    )


def _layout(containers: list[Container], execution_time_ms: int = 0) -> LayoutResult:
    return LayoutResult(
        project_info=ProjectInfo(
            team_name="T", execution_time_ms=execution_time_ms, input_file=None
        ),
        containers=containers,
    )


def _container(cid: int, items: list[Placement]) -> Container:
    return Container(
        container_id=cid,
        destination_id="DEST_A",
        total_weight=sum(p.weight for p in items),
        items=items,
    )


# ---------- occupancy ----------


def test_occupancy_zero_for_empty_layout() -> None:
    layout = _layout([])
    metrics = compute_internal_metrics(layout)
    assert metrics.occupancy == 0.0


def test_occupancy_one_when_fully_packed_column() -> None:
    """底全面を覆うアイテム1個なら occupancy=1.0（列としてスキマなし）。"""
    spec = CONTAINER_SPEC_40FT
    layout = _layout([_container(1, [_placement("P1", 0, 0, 0, spec.w, spec.l, 500)])])
    metrics = compute_internal_metrics(layout)
    assert metrics.occupancy == 1.0


def test_occupancy_half_when_half_width_column() -> None:
    """底に幅半分のアイテムを置くと、column は W*L*h、placed は (W/2)*L*h → 0.5。"""
    spec = CONTAINER_SPEC_40FT
    layout = _layout([_container(1, [_placement("P1", 0, 0, 0, spec.w // 2, spec.l, 500)])])
    metrics = compute_internal_metrics(layout)
    assert abs(metrics.occupancy - 0.5) < 1e-9


# ---------- stacking ----------


def test_stacking_single_layer_z0_ratio_one() -> None:
    layout = _layout(
        [
            _container(
                1,
                [
                    _placement("P1", 0, 0, 0, 500, 500, 500),
                    _placement("P2", 500, 0, 0, 500, 500, 500),
                ],
            )
        ]
    )
    metrics = compute_internal_metrics(layout)
    assert metrics.stacking.max_layers == 1
    assert metrics.stacking.mean_layers == 1.0
    assert metrics.stacking.z0_ratio == 1.0


def test_stacking_two_layers_detected() -> None:
    """z_min の distinct 値の数が層数。段積み 2 層は max_layers=2。"""
    layout = _layout(
        [
            _container(
                1,
                [
                    _placement("P1", 0, 0, 0, 500, 500, 500),
                    _placement("P2", 0, 0, 500, 500, 500, 500),
                ],
            )
        ]
    )
    metrics = compute_internal_metrics(layout)
    assert metrics.stacking.max_layers == 2
    assert metrics.stacking.z0_ratio == 0.5


def test_stacking_empty_layout() -> None:
    metrics = compute_internal_metrics(_layout([]))
    assert metrics.stacking.max_layers == 0
    assert metrics.stacking.mean_layers == 0.0
    assert metrics.stacking.z0_ratio == 0.0


# ---------- weight balance ----------


def test_weight_balance_across_containers() -> None:
    layout = _layout(
        [
            _container(1, [_placement("P1", 0, 0, 0, 500, 500, 500, weight=1000.0)]),
            _container(2, [_placement("P2", 0, 0, 0, 500, 500, 500, weight=3000.0)]),
        ]
    )
    metrics = compute_internal_metrics(layout)
    wb = metrics.weight_balance
    assert wb.min == 1000.0
    assert wb.max == 3000.0
    assert wb.mean == 2000.0
    assert wb.std > 0


def test_weight_balance_std_zero_for_single_container() -> None:
    """母集団 1 個なら pstdev=0。"""
    layout = _layout([_container(1, [_placement("P1", 0, 0, 0, 500, 500, 500, weight=500.0)])])
    metrics = compute_internal_metrics(layout)
    assert metrics.weight_balance.std == 0.0


# ---------- cog_y_stats ----------


def test_cog_stats_echoes_max_and_mean() -> None:
    spec = CONTAINER_SPEC_40FT
    # コンテナ1: 中心配置（偏差0）/ コンテナ2: 端配置（偏差大）
    layout = _layout(
        [
            _container(
                1,
                [_placement("P1", 0, int(spec.center_y - 250), 0, 500, 500, 500, weight=500.0)],
            ),
            _container(
                2,
                [_placement("P2", 0, 0, 0, 500, 500, 500, weight=500.0)],
            ),
        ]
    )
    metrics = compute_internal_metrics(layout)
    assert metrics.cog_y_stats.max_deviation > metrics.cog_y_stats.mean_deviation
    assert metrics.cog_y_stats.mean_deviation > 0


# ---------- execution_time passthrough ----------


def test_execution_time_is_passed_through() -> None:
    layout = _layout([], execution_time_ms=1234)
    metrics = compute_internal_metrics(layout)
    assert metrics.execution_time_ms == 1234
