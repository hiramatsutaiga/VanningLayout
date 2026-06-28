"""Unit tests for report aggregation."""

from __future__ import annotations

import json
from pathlib import Path

from vanning_eval.constraints import ViolationCode
from vanning_eval.report import build_report, save_report
from vanning_eval.schema import (
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


def _clean_layout() -> LayoutResult:
    # Y=5750 に 500mm のアイテム → 重心 y=6000 (コンテナ中心) で COG_VIOLATION を回避
    container = Container(
        container_id=1,
        destination_id="DEST_A",
        total_weight=100.0,
        items=[_placement("P1", 0, 5750, 0, 500, 500, 500, weight=100.0)],
    )
    return LayoutResult(
        project_info=ProjectInfo(team_name="T", execution_time_ms=0, input_file=None),
        containers=[container],
    )


def _dirty_layout() -> LayoutResult:
    """OUT_OF_BOUNDS 違反を含むレイアウト。"""
    container = Container(
        container_id=1,
        destination_id="DEST_A",
        total_weight=100.0,
        items=[_placement("P1", 1, 0, 0, 3000, 500, 500, weight=100.0)],  # w over
    )
    return LayoutResult(
        project_info=ProjectInfo(team_name="T", execution_time_ms=5, input_file=None),
        containers=[container],
    )


# ---------- build_report structure ----------


def test_build_report_has_required_top_level_keys() -> None:
    report = build_report(_clean_layout(), None)
    assert set(report.keys()) == {
        "evaluator_info",
        "verdict",
        "disqualifications",
        "teacher_score_metrics",
        "internal_metrics",
    }


def test_build_report_pass_verdict_for_clean_layout() -> None:
    report = build_report(_clean_layout(), None)
    assert report["verdict"] == "pass"
    assert report["disqualifications"] == []


def test_build_report_disqualified_verdict_with_violations() -> None:
    report = build_report(_dirty_layout(), None)
    assert report["verdict"] == "disqualified"
    assert len(report["disqualifications"]) >= 1
    codes = {d["code"] for d in report["disqualifications"]}
    assert ViolationCode.OUT_OF_BOUNDS in codes


def test_build_report_metrics_are_computed_even_when_disqualified() -> None:
    """CLAUDE.md 禁止項 23: 失格判定とメトリクス算出は互いに独立。"""
    report = build_report(_dirty_layout(), None)
    assert report["verdict"] == "disqualified"
    assert "fill_rates" in report["teacher_score_metrics"]
    assert "occupancy" in report["internal_metrics"]


def test_teacher_metrics_include_per_container_lists() -> None:
    """リーダーボード総合スコア (F/G) で必要な per-container 配列を含む。"""
    report = build_report(_clean_layout(), None)
    teacher = report["teacher_score_metrics"]
    assert "fill_rate_per_container" in teacher
    assert "cog_dev_per_container" in teacher
    # _clean_layout は 1 コンテナなので長さ 1
    assert len(teacher["fill_rate_per_container"]) == 1
    assert len(teacher["cog_dev_per_container"]) == 1
    # 値の型確認: float / 数値
    assert isinstance(teacher["fill_rate_per_container"][0], float)
    assert isinstance(teacher["cog_dev_per_container"][0], (int, float))


def test_build_report_evaluator_info_has_version_and_timestamp() -> None:
    report = build_report(_clean_layout(), None)
    info = report["evaluator_info"]
    assert "version" in info
    assert "evaluated_at" in info
    assert "T" in info["evaluated_at"]  # ISO 8601 の区切り


# ---------- disqualification shape ----------


def test_disqualification_entry_has_expected_shape() -> None:
    report = build_report(_dirty_layout(), None)
    entry = report["disqualifications"][0]
    assert set(entry.keys()) == {"code", "container_id", "items", "detail"}
    assert isinstance(entry["items"], list)
    assert isinstance(entry["detail"], dict)


# ---------- save_report round-trip ----------


def test_save_report_writes_valid_json(tmp_path: Path) -> None:
    report = build_report(_clean_layout(), None)
    out = tmp_path / "report.json"
    save_report(report, out)
    assert out.exists()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["verdict"] == "pass"
    assert loaded["teacher_score_metrics"]["containers_used"] == 1
