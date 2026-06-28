"""Unit tests for scoring (teacher score metrics)."""

from __future__ import annotations

from vanning_eval.schema import (
    CONTAINER_SPEC_40FT,
    Container,
    ContainerSpec,
    Dimensions,
    LayoutResult,
    Placement,
    Position,
    ProjectInfo,
)
from vanning_eval.scoring import compute_teacher_metrics


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


def _layout(items: list[Placement], spec: ContainerSpec = CONTAINER_SPEC_40FT) -> LayoutResult:
    container = Container(
        container_id=1,
        destination_id="DEST_A",
        total_weight=sum(p.weight for p in items),
        items=items,
    )
    return LayoutResult(
        project_info=ProjectInfo(team_name="T", execution_time_ms=0, input_file=None),
        containers=[container],
        spec=spec,
    )


# ---------- fill_rate ----------


def test_fill_rate_zero_for_empty_container() -> None:
    layout = _layout([])
    metrics = compute_teacher_metrics(layout)
    assert metrics.fill_rates[0].fill_rate == 0.0


def test_fill_rate_full_container_is_one() -> None:
    spec = CONTAINER_SPEC_40FT
    layout = _layout([_placement("P1", 0, 0, 0, spec.w, spec.l, spec.h)])
    metrics = compute_teacher_metrics(layout)
    assert metrics.fill_rates[0].fill_rate == 1.0


def test_fill_rate_half() -> None:
    spec = CONTAINER_SPEC_40FT
    half_volume_placement = _placement("P1", 0, 0, 0, spec.w, spec.l, spec.h // 2)
    layout = _layout([half_volume_placement])
    metrics = compute_teacher_metrics(layout)
    assert metrics.fill_rates[0].fill_rate == 0.5


# ---------- center of gravity ----------


def test_cog_empty_container_defaults_to_center() -> None:
    """重量ゼロの空コンテナは幾何中心を返し、偏差ゼロ。"""
    layout = _layout([])
    metrics = compute_teacher_metrics(layout)
    cog = metrics.cog_y[0]
    assert cog.yg == CONTAINER_SPEC_40FT.center_y
    assert cog.deviation == 0.0


def test_cog_zero_deviation_at_center() -> None:
    """Y 中心に 1 個だけ置けば deviation=0。"""
    spec = CONTAINER_SPEC_40FT
    y_start = int(spec.center_y - 1000)
    layout = _layout([_placement("P1", 0, y_start, 0, 2000, 2000, 1000, weight=500)])
    cog = compute_teacher_metrics(layout).cog_y[0]
    assert cog.deviation == 0.0


def test_cog_large_deviation_near_edge() -> None:
    """端寄せ配置で大きな deviation が出る（失格判定は constraints 側）。"""
    spec = CONTAINER_SPEC_40FT
    layout = _layout([_placement("P1", 0, 0, 0, 500, 500, 500, weight=500)])
    cog = compute_teacher_metrics(layout).cog_y[0]
    # 中心から 5750mm 離れた位置
    assert cog.deviation == spec.center_y - 250


# ---------- containers_used & average ----------


def test_average_fill_rate_averages_across_containers() -> None:
    spec = CONTAINER_SPEC_40FT
    c1 = Container(
        container_id=1,
        destination_id="DEST_A",
        total_weight=0.0,
        items=[_placement("P1", 0, 0, 0, spec.w, spec.l, spec.h)],  # 100%
    )
    c2 = Container(
        container_id=2,
        destination_id="DEST_A",
        total_weight=0.0,
        items=[],  # 0%
    )
    layout = LayoutResult(
        project_info=ProjectInfo(team_name="T", execution_time_ms=0, input_file=None),
        containers=[c1, c2],
    )
    metrics = compute_teacher_metrics(layout)
    assert metrics.containers_used == 2
    assert metrics.average_fill_rate == 0.5


# ---------- ContainerSpec injection ----------


def test_teacher_metrics_respect_custom_spec() -> None:
    """spec を差し替えると幾何中心も追従する。"""
    small_spec = ContainerSpec(w=1000, l=2000, h=1000, max_weight=5000)
    # 長手中心は y=1000
    layout = _layout(
        [_placement("P1", 0, 0, 0, 500, 1000, 500, weight=100)],
        spec=small_spec,
    )
    cog = compute_teacher_metrics(layout).cog_y[0]
    assert cog.yg == 500.0
    assert cog.deviation == 500.0
