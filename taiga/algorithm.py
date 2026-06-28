from __future__ import annotations

import argparse
import itertools
import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd


# Match evaluator-side 40ft constants exactly.
CONTAINER_LENGTH_MM = 12000
CONTAINER_WIDTH_MM = 2300
CONTAINER_HEIGHT_MM = 2400
MAX_CONTAINER_WEIGHT_KG = 24000.0
MIN_FILL_RATE = 0.50
Y_CENTER_MM = CONTAINER_LENGTH_MM / 2.0
# Match the updated Y-axis scoring guide for a 12000 mm container.
Y_DEVIATION_FULL_SCORE_MM = 1200.0
Y_DEVIATION_LIMIT_MM = 3000.0
ROTATIONS = [0, 90]
CONTAINER_VOLUME_MM3 = CONTAINER_LENGTH_MM * CONTAINER_WIDTH_MM * CONTAINER_HEIGHT_MM
ENFORCE_WEIGHT_LIMIT = True
PLACEMENT_WEIGHT_LIMIT_KG = MAX_CONTAINER_WEIGHT_KG

SIZE_SPECS = {
    "small": (760, 1130, 550),
    "medium": (1490, 2260, 900),
    "large": (2280, 2550, 2355),
}

REQUIRED_COLUMNS = [
    "item_id",
    "size_type",
    "width",
    "length",
    "height",
    "weight",
    "destination_id",
]


@dataclass(frozen=True)
class Item:
    item_id: str
    size_type: str
    width: int
    length: int
    height: int
    weight: float
    destination_id: str
    volume: int


@dataclass(frozen=True)
class PlacedItem:
    item_id: str
    size_type: str
    width: int
    length: int
    height: int
    x: int
    y: int
    z: int
    weight: float
    destination_id: str
    is_rotated: bool

    @property
    def x2(self) -> int:
        return self.x + self.width

    @property
    def y2(self) -> int:
        return self.y + self.length

    @property
    def z2(self) -> int:
        return self.z + self.height

    @property
    def center_y(self) -> float:
        return self.y + self.length / 2.0


@dataclass
class Container:
    container_id: int
    destination_id: str
    items: List[PlacedItem] = field(default_factory=list)
    total_weight: float = 0.0
    used_volume: int = 0
    y_weighted_sum: float = 0.0
    max_x: int = 0
    max_y: int = 0
    max_z: int = 0

    @property
    def fill_rate(self) -> float:
        return self.used_volume / CONTAINER_VOLUME_MM3

    def add_item(self, item: PlacedItem) -> None:
        self.items.append(item)
        self.total_weight += item.weight
        self.used_volume += item.width * item.length * item.height
        self.y_weighted_sum += item.center_y * item.weight
        self.max_x = max(self.max_x, item.x2)
        self.max_y = max(self.max_y, item.y2)
        self.max_z = max(self.max_z, item.z2)


@dataclass(frozen=True)
class ShelfColumn:
    destination_id: str
    length: int
    weight: float
    items: Tuple[Item, ...]
    kind: str


def read_generated_items(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"input json not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw_data = json.load(f)

    # Accept either a bare item list or an object with an items array.
    if isinstance(raw_data, dict):
        items_data = raw_data.get("items")
    else:
        items_data = raw_data

    if not isinstance(items_data, list):
        raise ValueError("input json must be a list or contain an 'items' list")

    df = pd.DataFrame(items_data)

    # Accept evaluator-compatible items_input.json shape with nested dimensions.
    if "dimensions" in df.columns:
        dimensions = df["dimensions"].apply(lambda value: value if isinstance(value, dict) else {})
        df = df.assign(
            width=dimensions.apply(lambda value: value.get("w")),
            length=dimensions.apply(lambda value: value.get("l")),
            height=dimensions.apply(lambda value: value.get("h")),
        )

    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    df = df[REQUIRED_COLUMNS].copy()
    df = df.dropna(subset=REQUIRED_COLUMNS)

    for col in ["width", "length", "height", "weight"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if df[["width", "length", "height", "weight"]].isna().any().any():
        bad_rows = df[df[["width", "length", "height", "weight"]].isna().any(axis=1)]
        raise ValueError(f"numeric conversion failed:\n{bad_rows}")

    if (df[["width", "length", "height", "weight"]] <= 0).any().any():
        bad_rows = df[(df[["width", "length", "height", "weight"]] <= 0).any(axis=1)]
        raise ValueError(f"non-positive dimensions or weight:\n{bad_rows}")

    if df["item_id"].duplicated().any():
        duplicated = df.loc[df["item_id"].duplicated(), "item_id"].tolist()
        raise ValueError(f"duplicated item_id detected: {duplicated}")

    df["item_id"] = df["item_id"].astype(str).str.strip()
    df["size_type"] = df["size_type"].astype(str).str.strip().str.lower()
    df["destination_id"] = df["destination_id"].astype(str).str.strip()
    df["width"] = df["width"].astype(int)
    df["length"] = df["length"].astype(int)
    df["height"] = df["height"].astype(int)
    df["weight"] = df["weight"].astype(float)
    return df.reset_index(drop=True)


def build_items(df: pd.DataFrame) -> List[Item]:
    items: List[Item] = []
    for row in df.itertuples(index=False):
        items.append(
            Item(
                item_id=row.item_id,
                size_type=row.size_type,
                width=row.width,
                length=row.length,
                height=row.height,
                weight=row.weight,
                destination_id=row.destination_id,
                volume=int(row.width * row.length * row.height),
            )
        )
    return sorted(items, key=lambda item: (item.destination_id, -item.volume, -item.weight, item.item_id))


def weight_lower_bound_by_destination(items: Sequence[Item]) -> int:
    """目的地混載禁止と重量上限から決まる最小コンテナ数の下限。"""
    weights_by_destination: Dict[str, float] = defaultdict(float)
    for item in items:
        weights_by_destination[item.destination_id] += item.weight
    return sum(math.ceil(weight / MAX_CONTAINER_WEIGHT_KG) for weight in weights_by_destination.values())


def rotated_dims(item: Item, rotation: int) -> Tuple[int, int, int]:
    if rotation == 0:
        return item.width, item.length, item.height
    if rotation == 90:
        return item.length, item.width, item.height
    raise ValueError(f"unsupported rotation: {rotation}")


def fits_in_container(x: int, y: int, z: int, width: int, length: int, height: int) -> bool:
    return (
        x >= 0
        and y >= 0
        and z >= 0
        and x + width <= CONTAINER_WIDTH_MM
        and y + length <= CONTAINER_LENGTH_MM
        and z + height <= CONTAINER_HEIGHT_MM
    )


def overlaps(a: PlacedItem, b: PlacedItem) -> bool:
    return not (
        a.x2 <= b.x
        or b.x2 <= a.x
        or a.y2 <= b.y
        or b.y2 <= a.y
        or a.z2 <= b.z
        or b.z2 <= a.z
    )


def is_supported(candidate: PlacedItem, existing: Sequence[PlacedItem]) -> bool:
    if candidate.z == 0:
        return True

    for base in existing:
        if (
            base.z2 == candidate.z
            and base.x <= candidate.x
            and candidate.x2 <= base.x2
            and base.y <= candidate.y
            and candidate.y2 <= base.y2
        ):
            return True
    return False


def compute_y_center_of_gravity(items: Sequence[PlacedItem]) -> float:
    if not items:
        return Y_CENTER_MM
    total_weight = sum(item.weight for item in items)
    return sum(item.center_y * item.weight for item in items) / total_weight


def y_deviation(items: Sequence[PlacedItem]) -> float:
    return abs(compute_y_center_of_gravity(items) - Y_CENTER_MM)


def y_deviation_with_candidate(container: Container, candidate: PlacedItem) -> float:
    total_weight = container.total_weight + candidate.weight
    y_weighted_sum = container.y_weighted_sum + candidate.center_y * candidate.weight
    return abs(y_weighted_sum / total_weight - Y_CENTER_MM)


def make_placed_item(item: Item, container_id: int, rotation: int, x: int, y: int, z: int) -> PlacedItem:
    width, length, height = rotated_dims(item, rotation)
    return PlacedItem(
        item_id=item.item_id,
        size_type=item.size_type,
        width=width,
        length=length,
        height=height,
        x=int(x),
        y=int(y),
        z=int(z),
        weight=float(item.weight),
        destination_id=item.destination_id,
        is_rotated=(rotation == 90),
    )


def can_place(container: Container, candidate: PlacedItem) -> bool:
    if candidate.destination_id != container.destination_id:
        return False
    if not fits_in_container(candidate.x, candidate.y, candidate.z, candidate.width, candidate.length, candidate.height):
        return False
    if any(overlaps(candidate, placed) for placed in container.items):
        return False
    if not is_supported(candidate, container.items):
        return False
    if ENFORCE_WEIGHT_LIMIT and container.total_weight + candidate.weight > PLACEMENT_WEIGHT_LIMIT_KG + 1e-9:
        return False
    if y_deviation_with_candidate(container, candidate) > Y_DEVIATION_LIMIT_MM + 1e-9:
        return False
    return True


def generate_candidate_points(container: Container) -> List[Tuple[int, int, int]]:
    points = {(0, 0, 0)}
    for item in container.items:
        points.add((item.x2, item.y, item.z))
        points.add((item.x, item.y2, item.z))
        points.add((item.x, item.y, item.z2))
        points.add((item.x2, item.y2, item.z))
        points.add((item.x2, item.y, item.z2))
        points.add((item.x, item.y2, item.z2))
    feasible = [
        point
        for point in points
        if point[0] <= CONTAINER_WIDTH_MM and point[1] <= CONTAINER_LENGTH_MM and point[2] <= CONTAINER_HEIGHT_MM
    ]
    return sorted(feasible, key=lambda point: (point[2], point[1], point[0]))


def centered_floor_point(width: int, length: int) -> Tuple[int, int, int]:
    x = max(0, min(CONTAINER_WIDTH_MM - width, int(round((CONTAINER_WIDTH_MM - width) / 2.0))))
    y = max(0, min(CONTAINER_LENGTH_MM - length, int(round((CONTAINER_LENGTH_MM - length) / 2.0))))
    return x, y, 0


def bounding_box_volume(items: Sequence[PlacedItem]) -> int:
    if not items:
        return 0
    max_x = max(item.x2 for item in items)
    max_y = max(item.y2 for item in items)
    max_z = max(item.z2 for item in items)
    return max_x * max_y * max_z


def candidate_score(container: Container, candidate: PlacedItem) -> Tuple[float, float, float, int, int, int]:
    deviation = y_deviation_with_candidate(container, candidate)
    bounding_volume = (
        max(container.max_x, candidate.x2)
        * max(container.max_y, candidate.y2)
        * max(container.max_z, candidate.z2)
    )
    candidate_volume = candidate.width * candidate.length * candidate.height
    dead_space = bounding_volume - (container.used_volume + candidate_volume)
    return (deviation, dead_space, candidate.z, candidate.y, candidate.x, int(candidate.is_rotated))


def find_best_placement(container: Container, item: Item) -> Optional[PlacedItem]:
    best_candidate: Optional[PlacedItem] = None
    best_score: Optional[Tuple[float, float, float, int, int, int]] = None
    base_points = generate_candidate_points(container)

    for rotation in ROTATIONS:
        width, length, _ = rotated_dims(item, rotation)
        candidate_points = sorted(
            set([*base_points, centered_floor_point(width, length)]),
            key=lambda point: (point[2], point[1], point[0]),
        )
        for x, y, z in candidate_points:
            candidate = make_placed_item(item, container.container_id, rotation, x, y, z)
            if not can_place(container, candidate):
                continue
            score = candidate_score(container, candidate)
            if best_score is None or score < best_score:
                best_candidate = candidate
                best_score = score

    return best_candidate


def replace_container_items(container: Container, items: Sequence[PlacedItem]) -> None:
    container.items = []
    container.total_weight = 0.0
    container.used_volume = 0
    container.y_weighted_sum = 0.0
    container.max_x = 0
    container.max_y = 0
    container.max_z = 0
    for item in items:
        container.add_item(item)


def center_containers_y_balance(containers: Sequence[Container]) -> None:
    for container in containers:
        if not container.items:
            continue

        min_y = min(item.y for item in container.items)
        max_y = max(item.y2 for item in container.items)
        desired_shift = int(round(Y_CENTER_MM - compute_y_center_of_gravity(container.items)))
        shift = max(-min_y, min(CONTAINER_LENGTH_MM - max_y, desired_shift))

        if shift == 0:
            continue

        shifted_items = [
            PlacedItem(
                item_id=item.item_id,
                size_type=item.size_type,
                width=item.width,
                length=item.length,
                height=item.height,
                x=item.x,
                y=item.y + shift,
                z=item.z,
                weight=item.weight,
                destination_id=item.destination_id,
                is_rotated=item.is_rotated,
            )
            for item in container.items
        ]
        replace_container_items(container, shifted_items)


def can_replace_item(container: Container, original: PlacedItem, candidate: PlacedItem) -> bool:
    other_items = [item for item in container.items if item.item_id != original.item_id]
    if not fits_in_container(candidate.x, candidate.y, candidate.z, candidate.width, candidate.length, candidate.height):
        return False
    if any(overlaps(candidate, placed) for placed in other_items):
        return False
    items = [*other_items, candidate]
    return all(is_supported(item, [other for other in items if other.item_id != item.item_id]) for item in items)


def optimize_y_balance_by_sliding(containers: Sequence[Container]) -> None:
    for container in containers:
        if len(container.items) <= 1:
            continue

        improved = True
        while improved:
            improved = False
            current_deviation = y_deviation(container.items)

            for original in sorted(container.items, key=lambda item: item.weight, reverse=True):
                other_items = [item for item in container.items if item.item_id != original.item_id]
                candidate_ys = {
                    0,
                    CONTAINER_LENGTH_MM - original.length,
                    int(round(Y_CENTER_MM - original.length / 2.0)),
                }
                for item in other_items:
                    candidate_ys.add(item.y2)
                    candidate_ys.add(item.y - original.length)

                best_item = original
                best_deviation = current_deviation
                for y in sorted(candidate_ys):
                    candidate = PlacedItem(
                        item_id=original.item_id,
                        size_type=original.size_type,
                        width=original.width,
                        length=original.length,
                        height=original.height,
                        x=original.x,
                        y=int(y),
                        z=original.z,
                        weight=original.weight,
                        destination_id=original.destination_id,
                        is_rotated=original.is_rotated,
                    )
                    if not can_replace_item(container, original, candidate):
                        continue

                    new_deviation = y_deviation([*other_items, candidate])
                    if new_deviation + 1e-9 < best_deviation:
                        best_item = candidate
                        best_deviation = new_deviation

                if best_item is not original:
                    replace_container_items(container, [*other_items, best_item])
                    current_deviation = best_deviation
                    improved = True
                    break


def optimize_y_balance_by_reinsertion(containers: Sequence[Container]) -> None:
    for container in containers:
        if len(container.items) <= 1:
            continue

        improved = True
        while improved:
            improved = False
            current_deviation = y_deviation(container.items)

            for original in sorted(container.items, key=lambda item: item.weight, reverse=True):
                other_items = [item for item in container.items if item.item_id != original.item_id]
                temporary = Container(container_id=container.container_id, destination_id=container.destination_id)
                for item in other_items:
                    temporary.add_item(item)

                candidate = find_best_placement(temporary, placed_to_item(original))
                if candidate is None:
                    continue
                new_items = [*other_items, candidate]
                if not all(is_supported(item, [other for other in new_items if other.item_id != item.item_id]) for item in new_items):
                    continue

                new_deviation = y_deviation(new_items)
                if new_deviation + 1e-9 < current_deviation:
                    replace_container_items(container, new_items)
                    improved = True
                    break


def clone_container(container: Container) -> Container:
    cloned = Container(container_id=container.container_id, destination_id=container.destination_id)
    for item in container.items:
        cloned.add_item(item)
    return cloned


def renumber_containers(containers: Sequence[Container]) -> None:
    for index, container in enumerate(containers, start=1):
        container.container_id = index


def placed_to_item(item: PlacedItem) -> Item:
    if item.is_rotated:
        width, length = item.length, item.width
    else:
        width, length = item.width, item.length
    height = item.height
    return Item(
        item_id=item.item_id,
        size_type=item.size_type,
        width=width,
        length=length,
        height=height,
        weight=item.weight,
        destination_id=item.destination_id,
        volume=width * length * height,
    )


def make_shelf_columns(items: Sequence[Item]) -> List[ShelfColumn]:
    columns: List[ShelfColumn] = []
    by_type: Dict[str, List[Item]] = defaultdict(list)
    for item in items:
        by_type[item.size_type].append(item)

    for item in sorted(by_type.get("large", []), key=lambda value: (-value.weight, value.item_id)):
        columns.append(
            ShelfColumn(
                destination_id=item.destination_id,
                length=2550,
                weight=item.weight,
                items=(item,),
                kind="large",
            )
        )

    medium_items = sorted(by_type.get("medium", []), key=lambda value: (-value.weight, value.item_id))
    small_items = sorted(by_type.get("small", []), key=lambda value: (-value.weight, value.item_id))
    small_index = 0
    for index in range(0, len(medium_items), 2):
        group_items = list(medium_items[index : index + 2])
        while small_index < len(small_items) and len(group_items) < 4:
            group_items.append(small_items[small_index])
            small_index += 1
        group = tuple(group_items)
        columns.append(
            ShelfColumn(
                destination_id=group[0].destination_id,
                length=1490,
                weight=sum(item.weight for item in group),
                items=group,
                kind="medium",
            )
        )

    remaining_small_items = small_items[small_index:]
    for index in range(0, len(remaining_small_items), 12):
        group = tuple(remaining_small_items[index : index + 12])
        columns.append(
            ShelfColumn(
                destination_id=group[0].destination_id,
                length=1130,
                weight=sum(item.weight for item in group),
                items=group,
                kind="small",
            )
        )

    return sorted(columns, key=lambda column: (-column.length, -column.weight, column.kind))


def assign_shelf_columns(columns: Sequence[ShelfColumn], bin_count: int) -> Optional[List[List[ShelfColumn]]]:
    bins: List[List[ShelfColumn]] = [[] for _ in range(bin_count)]
    used_lengths = [0 for _ in range(bin_count)]
    used_weights = [0.0 for _ in range(bin_count)]
    failed_states = set()

    def backtrack(index: int) -> bool:
        if index == len(columns):
            return True

        state = (
            index,
            tuple(sorted((used_lengths[i], int(round(used_weights[i] * 100))) for i in range(bin_count))),
        )
        if state in failed_states:
            return False

        column = columns[index]
        seen_states = set()
        for bin_index in sorted(range(bin_count), key=lambda value: (used_lengths[value], used_weights[value])):
            state = (used_lengths[bin_index], round(used_weights[bin_index], 3))
            if state in seen_states:
                continue
            seen_states.add(state)

            if used_lengths[bin_index] + column.length > CONTAINER_LENGTH_MM:
                continue
            if used_weights[bin_index] + column.weight > MAX_CONTAINER_WEIGHT_KG + 1e-9:
                continue

            bins[bin_index].append(column)
            used_lengths[bin_index] += column.length
            used_weights[bin_index] += column.weight
            if backtrack(index + 1):
                return True
            used_weights[bin_index] -= column.weight
            used_lengths[bin_index] -= column.length
            bins[bin_index].pop()

        failed_states.add(state)
        return False

    if backtrack(0):
        return bins
    return None


def can_fit_column_lengths(columns: Sequence[ShelfColumn], bin_count: int) -> bool:
    lengths = tuple(sorted((column.length for column in columns), reverse=True))
    used_lengths = [0 for _ in range(bin_count)]
    failed_states = set()

    def backtrack(index: int) -> bool:
        if index == len(lengths):
            return True

        state = (index, tuple(sorted(used_lengths)))
        if state in failed_states:
            return False

        length = lengths[index]
        seen_lengths = set()
        for bin_index in sorted(range(bin_count), key=lambda value: used_lengths[value]):
            if used_lengths[bin_index] in seen_lengths:
                continue
            seen_lengths.add(used_lengths[bin_index])

            if used_lengths[bin_index] + length > CONTAINER_LENGTH_MM:
                continue

            used_lengths[bin_index] += length
            if backtrack(index + 1):
                return True
            used_lengths[bin_index] -= length

        failed_states.add(state)
        return False

    return backtrack(0)


def add_column_to_container(container: Container, column: ShelfColumn, y: int) -> None:
    if column.kind == "large":
        item = column.items[0]
        container.add_item(
            PlacedItem(
                item_id=item.item_id,
                size_type=item.size_type,
                width=2280,
                length=2550,
                height=2355,
                x=10,
                y=y,
                z=0,
                weight=item.weight,
                destination_id=item.destination_id,
                is_rotated=False,
            )
        )
        return

    if column.kind == "medium":
        medium_items = [item for item in column.items if item.size_type == "medium"]
        small_items = [item for item in column.items if item.size_type == "small"]
        for level, item in enumerate(medium_items):
            container.add_item(
                PlacedItem(
                    item_id=item.item_id,
                    size_type=item.size_type,
                    width=2260,
                    length=1490,
                    height=900,
                    x=10,
                    y=y,
                    z=level * 900,
                    weight=item.weight,
                    destination_id=item.destination_id,
                    is_rotated=True,
                )
            )
        top_z = len(medium_items) * 900
        for slot, item in enumerate(small_items):
            container.add_item(
                PlacedItem(
                    item_id=item.item_id,
                    size_type=item.size_type,
                    width=760,
                    length=1130,
                    height=550,
                    x=10 + slot * 760,
                    y=y + 180,
                    z=top_z,
                    weight=item.weight,
                    destination_id=item.destination_id,
                    is_rotated=False,
                )
            )
        return

    if column.kind == "small":
        for index, item in enumerate(column.items):
            x_slot = index % 3
            z_level = index // 3
            container.add_item(
                PlacedItem(
                    item_id=item.item_id,
                    size_type=item.size_type,
                    width=760,
                    length=1130,
                    height=550,
                    x=x_slot * 760,
                    y=y,
                    z=z_level * 550,
                    weight=item.weight,
                    destination_id=item.destination_id,
                    is_rotated=False,
                )
            )
        return

    raise ValueError(f"unknown shelf column kind: {column.kind}")


def order_columns_for_y_balance(columns: Sequence[ShelfColumn]) -> List[ShelfColumn]:
    columns = list(columns)
    if len(columns) <= 1:
        return columns

    used_length = sum(column.length for column in columns)
    start_y = max(0, int(round((CONTAINER_LENGTH_MM - used_length) / 2.0)))

    def deviation_for_order(order: Sequence[ShelfColumn]) -> float:
        y = start_y
        total_weight = 0.0
        weighted_y = 0.0
        for column in order:
            total_weight += column.weight
            weighted_y += (y + column.length / 2.0) * column.weight
            y += column.length
        return abs(weighted_y / total_weight - Y_CENTER_MM)

    if len(columns) <= 8:
        return list(min(itertools.permutations(columns), key=deviation_for_order))

    ordered: List[ShelfColumn] = []
    for column in sorted(columns, key=lambda value: value.weight, reverse=True):
        left = [column, *ordered]
        right = [*ordered, column]
        ordered = left if deviation_for_order(left) < deviation_for_order(right) else right
    return ordered


def build_shelf_containers(items: Sequence[Item]) -> Optional[List[Container]]:
    if any((item.width, item.length, item.height) != SIZE_SPECS.get(item.size_type) for item in items):
        return None

    containers: List[Container] = []
    by_destination: Dict[str, List[Item]] = defaultdict(list)
    for item in items:
        by_destination[item.destination_id].append(item)

    for destination_id in sorted(by_destination):
        destination_items = by_destination[destination_id]
        columns = make_shelf_columns(destination_items)
        total_length = sum(column.length for column in columns)
        total_weight = sum(item.weight for item in destination_items)
        bin_count = max(
            1,
            math.ceil(total_length / CONTAINER_LENGTH_MM),
            math.ceil(total_weight / MAX_CONTAINER_WEIGHT_KG),
        )

        assignment: Optional[List[List[ShelfColumn]]] = None
        while assignment is None and bin_count <= len(columns):
            if can_fit_column_lengths(columns, bin_count):
                assignment = assign_shelf_columns(columns, bin_count)
            if assignment is None:
                bin_count += 1

        if assignment is None:
            return None

        for column_group in assignment:
            container = Container(container_id=len(containers) + 1, destination_id=destination_id)
            used_length = sum(column.length for column in column_group)
            y = max(0, int(round((CONTAINER_LENGTH_MM - used_length) / 2.0)))
            for column in order_columns_for_y_balance(column_group):
                add_column_to_container(container, column, y)
                y += column.length
            containers.append(container)

    center_containers_y_balance(containers)
    optimize_y_balance_by_sliding(containers)
    return containers


def try_place_item_best_fit(containers: Sequence[Container], item: Item) -> bool:
    best_container: Optional[Container] = None
    best_candidate: Optional[PlacedItem] = None
    best_score: Optional[Tuple[float, int, Tuple[float, float, float, int, int, int]]] = None

    for container in containers:
        if container.destination_id != item.destination_id:
            continue
        candidate = find_best_placement(container, item)
        if candidate is None:
            continue

        candidate_volume = candidate.width * candidate.length * candidate.height
        score = (
            -(container.total_weight + candidate.weight),
            -(container.used_volume + candidate_volume),
            candidate_score(container, candidate),
        )
        if best_score is None or score < best_score:
            best_container = container
            best_candidate = candidate
            best_score = score

    if best_container is None or best_candidate is None:
        return False

    best_container.add_item(best_candidate)
    return True


def compact_containers_by_relocation(containers: Sequence[Container]) -> List[Container]:
    compacted = [clone_container(container) for container in containers]
    changed = True

    while changed:
        changed = False
        for source in sorted(list(compacted), key=lambda container: (container.total_weight, len(container.items))):
            remaining = [clone_container(container) for container in compacted if container.container_id != source.container_id]
            move_items = sorted(
                (placed_to_item(item) for item in source.items),
                key=lambda item: (-item.volume, -item.weight, item.item_id),
            )

            if all(try_place_item_best_fit(remaining, item) for item in move_items):
                compacted = remaining
                renumber_containers(compacted)
                changed = True
                break

    return compacted


def pack_items(items: Sequence[Item]) -> List[Container]:
    shelf_containers = build_shelf_containers(items)
    if shelf_containers is not None:
        return shelf_containers

    weight_lower_bound = (
        weight_lower_bound_by_destination(items)
        if ENFORCE_WEIGHT_LIMIT and PLACEMENT_WEIGHT_LIMIT_KG == MAX_CONTAINER_WEIGHT_KG
        else 0
    )
    containers: List[Container] = []
    for item in items:
        best_container: Optional[Container] = None
        best_candidate: Optional[PlacedItem] = None
        best_score: Optional[Tuple[float, int, Tuple[float, float, float, int, int, int]]] = None

        for container in containers:
            if container.destination_id != item.destination_id:
                continue
            candidate = find_best_placement(container, item)
            if candidate is None:
                continue

            candidate_volume = candidate.width * candidate.length * candidate.height
            score = (
                -(container.total_weight + candidate.weight),
                -(container.used_volume + candidate_volume),
                candidate_score(container, candidate),
            )
            if best_score is None or score < best_score:
                best_container = container
                best_candidate = candidate
                best_score = score

        if best_container is not None and best_candidate is not None:
            best_container.add_item(best_candidate)
            continue

        new_container = Container(container_id=len(containers) + 1, destination_id=item.destination_id)
        candidate = find_best_placement(new_container, item)
        if candidate is None:
            raise RuntimeError(f"item {item.item_id} cannot be placed even in an empty container")
        new_container.add_item(candidate)
        containers.append(new_container)

    # 目的地別重量下限に到達しているなら、これ以上の本数削減は物理的に不可能。
    # 無駄な再配置探索を省き、処理時間のタイブレークを改善する。
    if len(containers) > weight_lower_bound:
        containers = compact_containers_by_relocation(containers)
    center_containers_y_balance(containers)
    optimize_y_balance_by_sliding(containers)
    optimize_y_balance_by_reinsertion(containers)
    center_containers_y_balance(containers)
    optimize_y_balance_by_sliding(containers)
    return containers


def evaluate_solution(containers: Sequence[Container]) -> Dict[str, object]:
    violations: List[str] = []
    low_fill_container_ids: List[int] = []
    summaries: List[Dict[str, object]] = []

    for container in containers:
        destination_set = {item.destination_id for item in container.items}
        if len(destination_set) > 1:
            violations.append(f"mixed destinations in container {container.container_id}: {sorted(destination_set)}")

        for item in container.items:
            if not fits_in_container(item.x, item.y, item.z, item.width, item.length, item.height):
                violations.append(f"out of bounds: {item.item_id} in container {container.container_id}")
            if not is_supported(item, [other for other in container.items if other.item_id != item.item_id]):
                violations.append(f"unsupported item: {item.item_id} in container {container.container_id}")

        for i, a in enumerate(container.items):
            for b in container.items[i + 1:]:
                if overlaps(a, b):
                    violations.append(f"overlap: {a.item_id} vs {b.item_id} in container {container.container_id}")

        if container.total_weight > MAX_CONTAINER_WEIGHT_KG + 1e-9:
            violations.append(f"overweight container {container.container_id}: {container.total_weight}")

        deviation = y_deviation(container.items)
        if deviation > Y_DEVIATION_LIMIT_MM + 1e-9:
            violations.append(f"excessive Y-axis deviation in container {container.container_id}: {deviation:.1f}mm")

        if container.fill_rate < MIN_FILL_RATE:
            low_fill_container_ids.append(container.container_id)

        summaries.append(
            {
                "container_id": container.container_id,
                "destination_id": container.destination_id,
                "item_count": len(container.items),
                "total_weight": round(container.total_weight, 3),
                "fill_rate": round(container.fill_rate, 6),
                "y_center_of_gravity": round(compute_y_center_of_gravity(container.items), 3),
                "y_deviation": round(deviation, 3),
            }
        )

    return {
        "disqualified": len(violations) > 0,
        "violations": violations,
        "low_fill_container_ids": low_fill_container_ids,
        "container_count": len(containers),
        "average_fill_rate": round(sum(container.fill_rate for container in containers) / max(len(containers), 1), 6),
        "max_y_deviation": round(max((y_deviation(container.items) for container in containers), default=0.0), 3),
        "container_summaries": summaries,
    }


def build_output_json(containers: Sequence[Container], team_name: str, execution_time_ms: int) -> Dict[str, object]:
    output_containers = []
    for container in containers:
        # Fail fast if the container-level destination is missing.
        if not container.destination_id:
            raise ValueError(f"container {container.container_id} is missing destination_id")

        # Copy destination_id to each item for evaluator compatibility.
        output_items = [
            {
                "item_id": item.item_id,
                "size_type": item.size_type,
                "dimensions": {"w": item.width, "l": item.length, "h": item.height},
                "position": {"x": item.x, "y": item.y, "z": item.z},
                "weight": round(item.weight, 3),
                "is_rotated": item.is_rotated,
                "destination_id": container.destination_id,
            }
            for item in sorted(container.items, key=lambda placed: placed.item_id)
        ]

        output_containers.append(
            {
                "container_id": container.container_id,
                "destination_id": container.destination_id,
                "total_weight": round(container.total_weight, 3),
                "items": output_items,
            }
        )

    return {
        "project_info": {
            "team_name": team_name,
            "execution_time_ms": execution_time_ms,
        },
        "containers": output_containers,
    }


def validate_output_schema(data: Dict[str, object]) -> None:
    if "project_info" not in data or "containers" not in data:
        raise ValueError("output json must contain project_info and containers")

    if not isinstance(data["project_info"], dict):
        raise ValueError("project_info must be a dict")

    containers = data["containers"]
    if not isinstance(containers, list):
        raise ValueError("containers must be a list")

    for index, container in enumerate(containers, start=1):
        required_container_keys = ["container_id", "destination_id", "total_weight", "items"]
        missing_container_keys = [key for key in required_container_keys if key not in container]
        if missing_container_keys:
            raise ValueError(f"container[{index}] is missing keys: {missing_container_keys}")
        if "destination_id" not in container or not container["destination_id"]:
            raise ValueError(f"container[{index}] is missing destination_id")
        if not isinstance(container["container_id"], int):
            raise ValueError(f"container[{index}].container_id must be an int")
        if not isinstance(container["total_weight"], (int, float)):
            raise ValueError(f"container[{index}].total_weight must be numeric")
        if "items" not in container or not isinstance(container["items"], list):
            raise ValueError(f"container[{index}].items must be a list")

        for item_index, item in enumerate(container["items"], start=1):
            required_item_keys = [
                "item_id",
                "size_type",
                "dimensions",
                "position",
                "weight",
                "is_rotated",
                "destination_id",
            ]
            missing_keys = [key for key in required_item_keys if key not in item]
            if missing_keys:
                raise ValueError(f"container[{index}].items[{item_index}] is missing keys: {missing_keys}")
            if not item["destination_id"]:
                raise ValueError(f"container[{index}].items[{item_index}] has empty destination_id")
            if not isinstance(item["weight"], (int, float)):
                raise ValueError(f"container[{index}].items[{item_index}].weight must be numeric")
            if not isinstance(item["is_rotated"], bool):
                raise ValueError(f"container[{index}].items[{item_index}].is_rotated must be bool")

            dimensions = item["dimensions"]
            if not isinstance(dimensions, dict):
                raise ValueError(f"container[{index}].items[{item_index}].dimensions must be a dict")
            missing_dimension_keys = [key for key in ["w", "l", "h"] if key not in dimensions]
            if missing_dimension_keys:
                raise ValueError(
                    f"container[{index}].items[{item_index}].dimensions is missing keys: {missing_dimension_keys}"
                )
            for dimension_key in ["w", "l", "h"]:
                if not isinstance(dimensions[dimension_key], int):
                    raise ValueError(
                        f"container[{index}].items[{item_index}].dimensions.{dimension_key} must be an int"
                    )

            position = item["position"]
            if not isinstance(position, dict):
                raise ValueError(f"container[{index}].items[{item_index}].position must be a dict")
            missing_position_keys = [key for key in ["x", "y", "z"] if key not in position]
            if missing_position_keys:
                raise ValueError(
                    f"container[{index}].items[{item_index}].position is missing keys: {missing_position_keys}"
                )
            for position_key in ["x", "y", "z"]:
                if not isinstance(position[position_key], int):
                    raise ValueError(
                        f"container[{index}].items[{item_index}].position.{position_key} must be an int"
                    )


def resolve_output_path(output: Path, submission_name: str | None, eval_root: Path | None) -> Path:
    if submission_name and eval_root:
        # Write directly into evaluator batch input/<submission_name>/layout_result.json.
        return eval_root / "input" / submission_name / "layout_result.json"
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run container layout from generated items json")
    parser.add_argument("--input", type=Path, default=Path("generated_items.json"))
    parser.add_argument("--output", type=Path, default=Path("layout_result.json"))
    parser.add_argument("--team-name", default="Team_Alpha")
    parser.add_argument("--submission-name", default=None)
    parser.add_argument("--eval-root", type=Path, default=None)
    parser.add_argument(
        "--ignore-weight-limit",
        action="store_true",
        help="Experimental compact mode: ignore 24,000kg limit while placing items.",
    )
    parser.add_argument(
        "--placement-weight-limit",
        type=float,
        default=MAX_CONTAINER_WEIGHT_KG,
        help="Experimental compact mode: allow placement up to this kg limit. Evaluator still checks 24,000kg.",
    )
    return parser.parse_args()


def main() -> None:
    global ENFORCE_WEIGHT_LIMIT, PLACEMENT_WEIGHT_LIMIT_KG
    args = parse_args()
    ENFORCE_WEIGHT_LIMIT = not args.ignore_weight_limit
    PLACEMENT_WEIGHT_LIMIT_KG = float(args.placement_weight_limit)
    started_at = time.perf_counter()

    table = read_generated_items(args.input)
    items = build_items(table)
    weight_lower_bound = weight_lower_bound_by_destination(items) if ENFORCE_WEIGHT_LIMIT else None
    containers = pack_items(items)

    execution_time_ms = int((time.perf_counter() - started_at) * 1000)
    result_json = build_output_json(containers, args.team_name, execution_time_ms)
    validate_output_schema(result_json)
    evaluation = evaluate_solution(containers)
    output_path = resolve_output_path(args.output, args.submission_name, args.eval_root)

    # Create the destination directory so evaluator-oriented paths work without manual setup.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(result_json, f, ensure_ascii=False, indent=2)

    print(f"input_file={args.input.resolve()}")
    print(f"output_file={output_path.resolve()}")
    print(f"container_count={evaluation['container_count']}")
    if weight_lower_bound is not None:
        print(f"weight_lower_bound={weight_lower_bound}")
    print(f"average_fill_rate={evaluation['average_fill_rate']}")
    print(f"max_y_deviation={evaluation['max_y_deviation']}")
    print(f"low_fill_container_ids={evaluation['low_fill_container_ids']}")
    print(f"violations={evaluation['violations']}")
    print(json.dumps(evaluation['container_summaries'], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
