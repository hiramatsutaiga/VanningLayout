"""内部用の拡張メトリクス（RoboBPP を参考にした、自チーム反復改善用の指標）。"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from .schema import Container, ContainerSpec, LayoutResult


@dataclass
class CoGStats:
    max_deviation: float
    mean_deviation: float
    std_deviation: float


@dataclass
class StackingStats:
    max_layers: int
    mean_layers: float
    z0_ratio: float


@dataclass
class WeightBalanceStats:
    mean: float
    std: float
    min: float
    max: float


@dataclass
class InternalMetrics:
    execution_time_ms: int
    cog_y_stats: CoGStats
    stacking: StackingStats
    occupancy: float
    weight_balance: WeightBalanceStats


def _container_cog_deviation(container: Container, spec: ContainerSpec) -> float:
    total_weight = sum(p.weight for p in container.items)
    if total_weight == 0:
        return 0.0
    yg = sum(p.center_y * p.weight for p in container.items) / total_weight
    return abs(yg - spec.center_y)


def _container_layers(container: Container) -> int:
    """Distinct z_min levels used by items = number of layers."""
    levels = {p.z_min for p in container.items}
    return len(levels)


def _occupancy(layout: LayoutResult) -> float:
    """Total placed volume / total heightmap column volume.

    Heightmap volume per container is approximated as container W*L times the
    tallest z_max reached by any item (the bounding "occupied column"). The
    ratio approaches 1.0 when stacks are dense without voids.
    """
    placed_volume = 0
    column_volume = 0
    spec = layout.spec
    for c in layout.containers:
        if not c.items:
            continue
        max_z = max(p.z_max for p in c.items)
        column_volume += spec.w * spec.l * max_z
        placed_volume += sum(p.volume for p in c.items)
    return placed_volume / column_volume if column_volume else 0.0


def compute_internal_metrics(layout: LayoutResult) -> InternalMetrics:
    spec = layout.spec
    deviations = [_container_cog_deviation(c, spec) for c in layout.containers]
    layers = [_container_layers(c) for c in layout.containers]
    weights = [sum(p.weight for p in c.items) for c in layout.containers]

    z0_total = 0
    item_total = 0
    for c in layout.containers:
        for p in c.items:
            if p.z_min == 0:
                z0_total += 1
            item_total += 1

    cog_stats = CoGStats(
        max_deviation=max(deviations) if deviations else 0.0,
        mean_deviation=statistics.fmean(deviations) if deviations else 0.0,
        std_deviation=statistics.pstdev(deviations) if len(deviations) > 1 else 0.0,
    )
    stacking = StackingStats(
        max_layers=max(layers) if layers else 0,
        mean_layers=statistics.fmean(layers) if layers else 0.0,
        z0_ratio=(z0_total / item_total) if item_total else 0.0,
    )
    weight_balance = WeightBalanceStats(
        mean=statistics.fmean(weights) if weights else 0.0,
        std=statistics.pstdev(weights) if len(weights) > 1 else 0.0,
        min=min(weights) if weights else 0.0,
        max=max(weights) if weights else 0.0,
    )
    return InternalMetrics(
        execution_time_ms=layout.project_info.execution_time_ms,
        cog_y_stats=cog_stats,
        stacking=stacking,
        occupancy=_occupancy(layout),
        weight_balance=weight_balance,
    )
