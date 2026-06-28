"""ViewModel 変換のユニットテスト。

描画レイヤに依存せず、`build_scene` が layout + 評価結果から正しい
ViewScene を構築できるかを検証する。
"""

from __future__ import annotations

from vanning_eval.constraints import Violation, run_all_checks
from vanning_eval.schema import (
    Container,
    Dimensions,
    LayoutResult,
    Placement,
    Position,
    ProjectInfo,
)
from vanning_eval.scoring import compute_teacher_metrics
from vanning_viewer.colors import VIOLATION_COLOR
from vanning_viewer.view_model import build_scene


def _p(
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


def test_build_scene_basic_pass() -> None:
    """違反なしレイアウトで verdict=pass、ボックスがそのまま ViewBox 化される。"""
    # 中心 (y=5500, l=1000 → center_y=6000) に配置して COG_VIOLATION を回避
    layout = _layout(
        [
            Container(
                container_id=1,
                destination_id="DEST_A",
                total_weight=200.0,
                items=[
                    _p("P1", 0, 5500, 0, 1000, 1000, 1000),
                    _p("P2", 1000, 5500, 0, 1000, 1000, 1000),
                ],
            )
        ]
    )
    scene = build_scene(layout)

    assert scene.verdict == "pass"
    assert scene.disqualification_count == 0
    assert len(scene.containers) == 1
    c = scene.containers[0]
    assert len(c.boxes) == 2
    assert {b.item_id for b in c.boxes} == {"P1", "P2"}
    for b in c.boxes:
        assert not b.violated
        assert b.violation_codes == ()
        assert b.color != VIOLATION_COLOR
        assert b.w == 1000.0 and b.l == 1000.0 and b.h == 1000.0


def test_build_scene_marks_violated_items_red() -> None:
    """重なりのある 2 ボックスは両方 violated=True で色が赤になる。"""
    # 2 つのアイテムが同一領域を占有 → OVERLAP 違反
    layout = _layout(
        [
            Container(
                container_id=1,
                destination_id="DEST_A",
                total_weight=200.0,
                items=[_p("A", 0, 0, 0, 1000, 1000, 1000), _p("B", 500, 0, 0, 1000, 1000, 1000)],
            )
        ]
    )
    scene = build_scene(layout)

    assert scene.verdict == "disqualified"
    assert scene.disqualification_count > 0
    boxes = {b.item_id: b for b in scene.containers[0].boxes}
    assert boxes["A"].violated and boxes["B"].violated
    assert boxes["A"].color == VIOLATION_COLOR
    assert boxes["B"].color == VIOLATION_COLOR
    assert "OVERLAP" in boxes["A"].violation_codes


def test_build_scene_accepts_precomputed_violations() -> None:
    """呼び出し側で violations / teacher を渡せば再計算しない。"""
    layout = _layout(
        [
            Container(
                container_id=1,
                destination_id="DEST_A",
                total_weight=100.0,
                items=[_p("P1", 0, 0, 0, 500, 500, 500)],
            )
        ]
    )
    # 偽の違反を強制注入
    custom_violations = [Violation(code="CUSTOM_CODE", container_id=1, items=["P1"], detail={})]
    teacher = compute_teacher_metrics(layout)

    scene = build_scene(layout, violations=custom_violations, teacher=teacher)

    assert scene.verdict == "disqualified"
    assert scene.disqualification_count == 1
    box = scene.containers[0].boxes[0]
    assert box.violated
    assert box.violation_codes == ("CUSTOM_CODE",)


def test_build_scene_destination_colors_distinct() -> None:
    """異なる destination_id のコンテナは異なる色を持つ。"""
    # COG_VIOLATION を避けるため、各コンテナを y=5750 (center_y=6000) に配置
    layout = _layout(
        [
            Container(
                container_id=1,
                destination_id="DEST_A",
                total_weight=100.0,
                items=[_p("P1", 0, 5750, 0, 500, 500, 500, destination_id="DEST_A")],
            ),
            Container(
                container_id=2,
                destination_id="DEST_B",
                total_weight=100.0,
                items=[_p("P2", 0, 5750, 0, 500, 500, 500, destination_id="DEST_B")],
            ),
        ]
    )
    scene = build_scene(layout)
    # destination ごとに違う色が割り当てられているはず
    color_a = scene.containers[0].boxes[0].color
    color_b = scene.containers[1].boxes[0].color
    assert color_a != color_b


def test_build_scene_container_geometry_is_40ft_spec() -> None:
    """ViewContainer の枠寸法は 40ft 仕様に固定される。"""
    layout = _layout(
        [Container(container_id=1, destination_id="DEST_A", total_weight=0.0, items=[])]
    )
    scene = build_scene(layout)
    c = scene.containers[0]
    assert c.w == 2300.0
    assert c.l == 12000.0
    assert c.h == 2400.0


def test_build_scene_hover_label_contains_item_info() -> None:
    """hover ラベルに item_id / dims / weight / 違反コードが含まれる。"""
    layout = _layout(
        [
            Container(
                container_id=1,
                destination_id="DEST_A",
                total_weight=100.0,
                items=[
                    _p("A", 0, 0, 0, 1000, 1000, 1000, weight=123.4),
                    _p("B", 500, 0, 0, 1000, 1000, 1000, weight=123.4),
                ],
            )
        ]
    )
    # run_all_checks 経由で OVERLAP 違反が出る
    violations = run_all_checks(layout, None)
    scene = build_scene(layout, violations=violations)

    boxes = {b.item_id: b for b in scene.containers[0].boxes}
    label_a = boxes["A"].label
    assert "A" in label_a
    assert "123.4" in label_a
    assert "w=1000" in label_a
    # 違反コード OVERLAP は日本語「重なり」で表示される
    assert "重なり" in label_a
    assert "違反:" in label_a
