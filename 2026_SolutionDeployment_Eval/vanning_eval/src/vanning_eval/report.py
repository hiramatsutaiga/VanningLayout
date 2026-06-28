"""レポート集約処理。

制約違反・教員指定メトリクス・内部メトリクスを 1 つの JSON 成果物に統合し、
人間向けのサマリーを標準出力に表示する。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .constraints import Violation, run_all_checks
from .metrics import InternalMetrics, compute_internal_metrics
from .schema import ItemsInput, LayoutResult
from .scoring import TeacherScoreMetrics, compute_teacher_metrics


def _violation_to_dict(v: Violation) -> dict[str, Any]:
    return {
        "code": v.code,
        "container_id": v.container_id,
        "items": v.items,
        "detail": v.detail,
    }


def _teacher_to_dict(t: TeacherScoreMetrics) -> dict[str, Any]:
    return {
        "containers_used": t.containers_used,
        "average_fill_rate": round(t.average_fill_rate, 4),
        "fill_rates": [asdict(f) for f in t.fill_rates],
        "cog_y": [asdict(c) for c in t.cog_y],
        "fill_rate_per_container": [round(f.fill_rate, 4) for f in t.fill_rates],
        "cog_dev_per_container": [round(c.deviation, 1) for c in t.cog_y],
    }


def _internal_to_dict(m: InternalMetrics) -> dict[str, Any]:
    return {
        "execution_time_ms": m.execution_time_ms,
        "cog_y_stats": asdict(m.cog_y_stats),
        "stacking": asdict(m.stacking),
        "occupancy": round(m.occupancy, 4),
        "weight_balance": asdict(m.weight_balance),
    }


def build_report(layout: LayoutResult, items_input: ItemsInput | None) -> dict[str, Any]:
    """Run all checks + metrics and return the report dict."""
    violations = run_all_checks(layout, items_input)
    teacher = compute_teacher_metrics(layout)
    internal = compute_internal_metrics(layout)
    return {
        "evaluator_info": {
            "version": __version__,
            "evaluated_at": datetime.now(UTC).isoformat(),
        },
        "verdict": "disqualified" if violations else "pass",
        "disqualifications": [_violation_to_dict(v) for v in violations],
        "teacher_score_metrics": _teacher_to_dict(teacher),
        "internal_metrics": _internal_to_dict(internal),
    }


def save_report(report: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _vw(s: str) -> int:
    """見かけの幅（非 ASCII 文字は 2、それ以外は 1）。"""
    return sum(2 if ord(c) > 0x7F else 1 for c in s)


def _lpad(s: str, w: int) -> str:
    return s + " " * max(0, w - _vw(s))


def print_summary(report: dict[str, Any]) -> None:
    """人間向けの簡潔な標準出力サマリ。"""
    verdict = report["verdict"]
    teacher = report["teacher_score_metrics"]
    internal = report["internal_metrics"]
    verdict_ja = {"pass": "合格", "disqualified": "失格"}.get(verdict, verdict)
    w = 18
    print(f"{_lpad('判定', w)}: {verdict_ja}")
    print(f"{_lpad('使用コンテナ数', w)}: {teacher['containers_used']}")
    print(f"{_lpad('平均充填率', w)}: {teacher['average_fill_rate']:.4f}")
    devs = [entry["deviation"] for entry in teacher["cog_y"]]
    cog_mean = sum(devs) / len(devs) if devs else 0.0
    cog_max = max(devs) if devs else 0.0
    print(f"{_lpad('重心Y偏差', w)}: 平均={cog_mean:.0f}mm, 最大={cog_max:.0f}mm")
    print(f"{_lpad('実行時間', w)}: {internal['execution_time_ms']} ms")
    print(
        f"{_lpad('段積み', w)}: "
        f"最大={internal['stacking']['max_layers']}, "
        f"平均={internal['stacking']['mean_layers']:.2f}, "
        f"接地率={internal['stacking']['z0_ratio']:.2f}"
    )
    print(f"{_lpad('占有率', w)}: {internal['occupancy']:.4f}")
    if report["disqualifications"]:
        print("\n失格事由:")
        for d in report["disqualifications"]:
            print(f"  - [{d['code']}] コンテナ={d['container_id']} items={d['items']}")
