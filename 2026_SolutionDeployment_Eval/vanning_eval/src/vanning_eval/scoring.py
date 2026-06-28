"""教員採点用メトリクス（要件定義書 Section 5）。

素のメトリクス（コンテナ数 / 充填率 / 重心ズレ）を per-container で算出する。
要件定義書 PR #26 で「重み付き総合スコア式」を廃止し辞書式順位付けに移行したため、
最終スコアの合成はここでは行わない。順位付けロジックは viewer 側 (`_rank_entries`)。
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema import (
    Container,
    ContainerSpec,
    LayoutResult,
)


@dataclass
class ContainerFillRate:
    container_id: int
    fill_rate: float


@dataclass
class ContainerCoG:
    container_id: int
    yg: float
    deviation: float


@dataclass
class TeacherScoreMetrics:
    containers_used: int
    average_fill_rate: float
    fill_rates: list[ContainerFillRate]
    cog_y: list[ContainerCoG]


def _fill_rate(container: Container, spec: ContainerSpec) -> float:
    used = sum(p.volume for p in container.items)
    return used / spec.volume


def _center_of_gravity_y(container: Container, spec: ContainerSpec) -> float:
    total_weight = sum(p.weight for p in container.items)
    if total_weight == 0:
        return spec.center_y
    weighted = sum(p.center_y * p.weight for p in container.items)
    return weighted / total_weight


def compute_teacher_metrics(layout: LayoutResult) -> TeacherScoreMetrics:
    spec = layout.spec
    fill_rates = [
        ContainerFillRate(
            container_id=c.container_id,
            fill_rate=_fill_rate(c, spec),
        )
        for c in layout.containers
    ]
    cog_y: list[ContainerCoG] = []
    for c in layout.containers:
        yg = _center_of_gravity_y(c, spec)
        deviation = abs(yg - spec.center_y)
        cog_y.append(
            ContainerCoG(
                container_id=c.container_id,
                yg=yg,
                deviation=deviation,
            )
        )
    avg_fill = sum(f.fill_rate for f in fill_rates) / len(fill_rates) if fill_rates else 0.0
    return TeacherScoreMetrics(
        containers_used=len(layout.containers),
        average_fill_rate=avg_fill,
        fill_rates=fill_rates,
        cog_y=cog_y,
    )
