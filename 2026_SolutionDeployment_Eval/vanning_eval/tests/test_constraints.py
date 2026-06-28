"""Unit tests for constraints."""

from __future__ import annotations

from vanning_eval.constraints import (
    check_cog_violation,
    check_complete_placement,
    check_destination,
    check_floating,
    check_item_attr_consistency,
    check_out_of_bounds,
    check_overlap,
    check_weight,
    run_all_checks,
)
from vanning_eval.schema import (
    CONTAINER_H,
    CONTAINER_L,
    CONTAINER_W,
    Container,
    DatasetInfo,
    Dimensions,
    ItemsInput,
    ItemSpec,
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
    destination_id: str = "DEST_A",
    size_type: str = "small",
) -> Placement:
    return Placement(
        item_id=item_id,
        size_type=size_type,
        dimensions=Dimensions(w=w, l=l, h=h),
        position=Position(x=x, y=y, z=z),
        weight=weight,
        is_rotated=False,
        destination_id=destination_id,
    )


def _layout(containers: list[Container]) -> LayoutResult:
    return LayoutResult(
        project_info=ProjectInfo(team_name="T", execution_time_ms=0, input_file=None),
        containers=containers,
    )


def _container(
    cid: int,
    destination: str,
    items: list[Placement],
) -> Container:
    total = sum(p.weight for p in items)
    return Container(container_id=cid, destination_id=destination, total_weight=total, items=items)


# ---------- out-of-bounds ----------


def test_oob_pass_edge_case() -> None:
    layout = _layout(
        [
            _container(
                1, "DEST_A", [_placement("P1", 0, 0, 0, CONTAINER_W, CONTAINER_L, CONTAINER_H)]
            )
        ]
    )
    assert check_out_of_bounds(layout) == []


def test_oob_fail_overhang_x() -> None:
    layout = _layout([_container(1, "DEST_A", [_placement("P1", 1, 0, 0, CONTAINER_W, 100, 100)])])
    violations = check_out_of_bounds(layout)
    assert len(violations) == 1
    assert violations[0].code == "OUT_OF_BOUNDS"


# ---------- overlap ----------


def test_overlap_pass_side_by_side() -> None:
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [
                    _placement("P1", 0, 0, 0, 100, 100, 100),
                    _placement("P2", 100, 0, 0, 100, 100, 100),
                ],
            )
        ]
    )
    assert check_overlap(layout) == []


def test_overlap_fail() -> None:
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [
                    _placement("P1", 0, 0, 0, 100, 100, 100),
                    _placement("P2", 50, 50, 50, 100, 100, 100),
                ],
            )
        ]
    )
    violations = check_overlap(layout)
    assert len(violations) == 1
    assert violations[0].code == "OVERLAP"
    assert set(violations[0].items) == {"P1", "P2"}


# ---------- destination ----------


def test_destination_pass() -> None:
    layout = _layout(
        [
            _container(
                1, "DEST_A", [_placement("P1", 0, 0, 0, 100, 100, 100, destination_id="DEST_A")]
            )
        ]
    )
    assert check_destination(layout) == []


def test_destination_fail_mix() -> None:
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [
                    _placement("P1", 0, 0, 0, 100, 100, 100, destination_id="DEST_A"),
                    _placement("P2", 100, 0, 0, 100, 100, 100, destination_id="DEST_B"),
                ],
            )
        ]
    )
    violations = check_destination(layout)
    assert len(violations) == 1
    assert violations[0].code == "DESTINATION_MIX"
    assert violations[0].items == ["P2"]


# ---------- weight ----------


def test_weight_pass() -> None:
    layout = _layout(
        [_container(1, "DEST_A", [_placement("P1", 0, 0, 0, 100, 100, 100, weight=10000)])]
    )
    assert check_weight(layout) == []


def test_weight_fail_over() -> None:
    layout = _layout(
        [_container(1, "DEST_A", [_placement("P1", 0, 0, 0, 100, 100, 100, weight=30000)])]
    )
    codes = [v.code for v in check_weight(layout)]
    assert "WEIGHT_OVER" in codes


def test_weight_fail_declaration_mismatch() -> None:
    placements = [_placement("P1", 0, 0, 0, 100, 100, 100, weight=100.0)]
    container = Container(
        container_id=1, destination_id="DEST_A", total_weight=999.0, items=placements
    )
    layout = _layout([container])
    codes = [v.code for v in check_weight(layout)]
    assert "WEIGHT_DECLARATION_MISMATCH" in codes


# ---------- floating / pyramid ----------


def test_floating_pass_z_zero() -> None:
    layout = _layout([_container(1, "DEST_A", [_placement("P1", 0, 0, 0, 100, 100, 100)])])
    assert check_floating(layout) == []


def test_floating_pass_fully_supported() -> None:
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [
                    _placement("P_base", 0, 0, 0, 200, 200, 100),
                    _placement("P_top", 50, 50, 100, 100, 100, 100),
                ],
            )
        ]
    )
    assert check_floating(layout) == []


def test_floating_pass_multiple_supports() -> None:
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [
                    _placement("P_A", 0, 0, 0, 100, 200, 100),
                    _placement("P_B", 100, 0, 0, 100, 200, 100),
                    _placement("P_top", 0, 0, 100, 200, 200, 100),
                ],
            )
        ]
    )
    assert check_floating(layout) == []


def test_floating_fail_overhang() -> None:
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [
                    _placement("P_base", 0, 0, 0, 100, 100, 100),
                    _placement("P_top", 50, 0, 100, 100, 100, 100),
                ],
            )
        ]
    )
    violations = check_floating(layout)
    assert len(violations) == 1
    assert violations[0].code == "FLOATING"


def test_floating_fail_no_support() -> None:
    layout = _layout([_container(1, "DEST_A", [_placement("P_hover", 0, 0, 500, 100, 100, 100)])])
    violations = check_floating(layout)
    assert len(violations) == 1
    assert violations[0].code == "FLOATING"


# ---------- complete placement ----------


def _items_input(ids: list[str]) -> ItemsInput:
    return ItemsInput(
        dataset_info=DatasetInfo(dataset_name="t", seed=0, item_count=len(ids)),
        items=[
            ItemSpec(
                item_id=i,
                size_type="small",
                dimensions=Dimensions(w=100, l=100, h=100),
                weight=10.0,
                destination_id="DEST_A",
            )
            for i in ids
        ],
    )


def test_complete_placement_skipped_when_no_input() -> None:
    layout = _layout([_container(1, "DEST_A", [_placement("P1", 0, 0, 0, 100, 100, 100)])])
    assert check_complete_placement(layout, None) == []


def test_complete_placement_pass() -> None:
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [
                    _placement("P1", 0, 0, 0, 100, 100, 100),
                    _placement("P2", 100, 0, 0, 100, 100, 100),
                ],
            )
        ]
    )
    assert check_complete_placement(layout, _items_input(["P1", "P2"])) == []


def test_complete_placement_fail_missing_and_duplicate() -> None:
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [
                    _placement("P1", 0, 0, 0, 100, 100, 100),
                    _placement("P1", 100, 0, 0, 100, 100, 100),
                ],
            )
        ]
    )
    codes = {v.code for v in check_complete_placement(layout, _items_input(["P1", "P2"]))}
    assert "MISSING_ITEMS" in codes
    assert "DUPLICATE_ITEMS" in codes


# ---------- cog violation ----------


def test_cog_pass_at_center() -> None:
    # 1 placement centered at Yc=6000 (l=100 means center_y = y + 50 = 6000)
    layout = _layout(
        [_container(1, "DEST_A", [_placement("P1", 0, 5950, 0, 100, 100, 100, weight=1000.0)])]
    )
    assert check_cog_violation(layout) == []


def test_cog_pass_at_acceptable_boundary() -> None:
    # center_y = 3000 → deviation = 3000mm exactly (== limit, still pass)
    layout = _layout(
        [_container(1, "DEST_A", [_placement("P1", 0, 2950, 0, 100, 100, 100, weight=1000.0)])]
    )
    assert check_cog_violation(layout) == []


def test_cog_fail_over_acceptable() -> None:
    # center_y = 50 → deviation = 5950mm (> 3000mm limit)
    layout = _layout(
        [_container(1, "DEST_A", [_placement("P1", 0, 0, 0, 100, 100, 100, weight=1000.0)])]
    )
    violations = check_cog_violation(layout)
    assert len(violations) == 1
    assert violations[0].code == "COG_VIOLATION"
    assert violations[0].container_id == 1
    assert violations[0].detail["deviation"] == 5950
    assert violations[0].detail["limit"] == 3000


def test_cog_empty_container_passes() -> None:
    # 空コンテナは重心 = 幾何中心になる仕様（deviation = 0）
    layout = _layout([_container(1, "DEST_A", [])])
    assert check_cog_violation(layout) == []


def test_cog_weighted_average() -> None:
    # 軽い箱を端に、重い箱を反対端に配置して、重量加重で重心がほぼ重い側に寄ることを確認
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [
                    _placement("P1", 0, 0, 0, 100, 100, 100, weight=1.0),  # center_y=50, weight 1
                    _placement(
                        "P2", 0, 11900, 0, 100, 100, 100, weight=1000.0
                    ),  # center_y=11950, weight 1000
                ],
            )
        ]
    )
    # 加重平均で Yg ≒ 11938、deviation ≒ 5938 (> 3000mm 失格)
    violations = check_cog_violation(layout)
    assert len(violations) == 1
    assert violations[0].code == "COG_VIOLATION"


# ---------- item attr consistency ----------


def _attr_items_input(specs: list[ItemSpec]) -> ItemsInput:
    return ItemsInput(
        dataset_info=DatasetInfo(dataset_name="t", seed=0, item_count=len(specs)),
        items=specs,
    )


def _attr_spec(
    item_id: str,
    *,
    weight: float = 100.0,
    w: int = 100,
    l: int = 200,  # noqa: E741
    h: int = 50,
    destination_id: str = "DEST_A",
    size_type: str = "small",
) -> ItemSpec:
    return ItemSpec(
        item_id=item_id,
        size_type=size_type,
        dimensions=Dimensions(w=w, l=l, h=h),
        weight=weight,
        destination_id=destination_id,
    )


def test_item_attr_skipped_when_no_input() -> None:
    layout = _layout([_container(1, "DEST_A", [_placement("P1", 0, 0, 0, 100, 200, 50)])])
    assert check_item_attr_consistency(layout, None) == []


def test_item_attr_pass_matching() -> None:
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [_placement("P1", 0, 0, 0, 100, 200, 50, weight=100.0)],
            )
        ]
    )
    items = _attr_items_input([_attr_spec("P1")])
    assert check_item_attr_consistency(layout, items) == []


def test_item_attr_fail_weight_mismatch() -> None:
    """layout が weight を書き換えて weight cap をすり抜ける不正の検出。"""
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [_placement("P1", 0, 0, 0, 100, 200, 50, weight=100.0)],
            )
        ]
    )
    items = _attr_items_input([_attr_spec("P1", weight=14000.0)])
    violations = check_item_attr_consistency(layout, items)
    assert len(violations) == 1
    assert violations[0].code == "ITEM_ATTR_MISMATCH"
    assert violations[0].items == ["P1"]
    assert violations[0].detail["field"] == "weight"
    assert violations[0].detail["declared"] == 14000.0
    assert violations[0].detail["actual"] == 100.0


def test_item_attr_pass_within_weight_tolerance() -> None:
    """0.5 kg 以内の差は float 丸めとして許容。"""
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [_placement("P1", 0, 0, 0, 100, 200, 50, weight=100.4)],
            )
        ]
    )
    items = _attr_items_input([_attr_spec("P1", weight=100.0)])
    assert check_item_attr_consistency(layout, items) == []


def test_item_attr_fail_dimensions_mismatch() -> None:
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [_placement("P1", 0, 0, 0, 100, 200, 50, weight=100.0)],
            )
        ]
    )
    items = _attr_items_input([_attr_spec("P1", w=120, l=200, h=50)])
    violations = check_item_attr_consistency(layout, items)
    assert len(violations) == 1
    assert violations[0].detail["field"] == "dimensions"
    assert violations[0].detail["declared"] == [120, 200, 50]
    assert violations[0].detail["actual"] == [100, 200, 50]


def test_item_attr_pass_dimensions_rotated() -> None:
    """is_rotated=True なら declared (w, l) を入れ替えて比較する。"""
    placement = Placement(
        item_id="P1",
        size_type="small",
        dimensions=Dimensions(w=200, l=100, h=50),  # 入れ替わった寸法
        position=Position(x=0, y=0, z=0),
        weight=100.0,
        is_rotated=True,
        destination_id="DEST_A",
    )
    layout = _layout([_container(1, "DEST_A", [placement])])
    items = _attr_items_input([_attr_spec("P1", w=100, l=200, h=50)])
    assert check_item_attr_consistency(layout, items) == []


def test_item_attr_fail_destination_mismatch() -> None:
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [_placement("P1", 0, 0, 0, 100, 200, 50, destination_id="DEST_A")],
            )
        ]
    )
    items = _attr_items_input([_attr_spec("P1", destination_id="DEST_B")])
    violations = check_item_attr_consistency(layout, items)
    assert len(violations) == 1
    assert violations[0].detail["field"] == "destination_id"


def test_item_attr_fail_size_type_mismatch() -> None:
    layout = _layout(
        [
            _container(
                1,
                "DEST_A",
                [_placement("P1", 0, 0, 0, 100, 200, 50, size_type="small")],
            )
        ]
    )
    items = _attr_items_input([_attr_spec("P1", size_type="large")])
    violations = check_item_attr_consistency(layout, items)
    assert len(violations) == 1
    assert violations[0].detail["field"] == "size_type"


def test_item_attr_skip_unknown_item_id() -> None:
    """layout に items_input 未宣言の item_id があっても本チェックでは無視
    (UNKNOWN_ITEMS が check_complete_placement で別途検出される)。"""
    layout = _layout(
        [_container(1, "DEST_A", [_placement("X999", 0, 0, 0, 100, 200, 50, weight=100.0)])]
    )
    items = _attr_items_input([_attr_spec("P1")])
    assert check_item_attr_consistency(layout, items) == []


# ---------- aggregate ----------


def test_run_all_checks_clean() -> None:
    layout = _layout(
        [_container(1, "DEST_A", [_placement("P1", 0, 5950, 0, 100, 100, 100, weight=100.0)])]
    )
    assert run_all_checks(layout, None) == []
