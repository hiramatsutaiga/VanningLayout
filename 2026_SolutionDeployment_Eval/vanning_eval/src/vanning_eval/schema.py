"""入力 JSON のスキーマ定義とローダ。

`layout_result.json` と `items_input.json` の dataclass モデルを定義し、
`load_layout` / `load_items` でファイルを読み込んで基本的な形状バリデーション
（フィールド欠損、型不一致）を事前に行う。これにより下流モジュールは入力を
信頼済みデータとして扱える。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VALID_SIZE_TYPES = {"small", "medium", "large"}


@dataclass(frozen=True)
class ContainerSpec:
    """コンテナの物理仕様。

    将来 20ft・異種サイズが来たときに、LayoutResult.spec 経由で差し替えられるよう
    グローバル定数ではなく dataclass として保持する。既存コードは
    `CONTAINER_SPEC_40FT` を参照するか、`layout.spec.*` を使うこと。
    """

    w: int  # x axis (width, mm)
    l: int  # noqa: E741 - spec uses "l" for long side. y axis (depth, mm)
    h: int  # z axis (height, mm)
    max_weight: int  # kg

    @property
    def center_y(self) -> float:
        """Yc = L / 2（重心評価の幾何中心）。"""
        return self.l / 2.0

    @property
    def volume(self) -> int:
        return self.w * self.l * self.h


# Spec from the requirements (Section 3, 40ft container).
CONTAINER_SPEC_40FT = ContainerSpec(w=2300, l=12000, h=2400, max_weight=24000)

# Backward-compat: モジュールトップレベル定数として従来通り公開。
# 新しいコードは layout.spec 経由で参照することを推奨。
CONTAINER_W = CONTAINER_SPEC_40FT.w
CONTAINER_L = CONTAINER_SPEC_40FT.l
CONTAINER_H = CONTAINER_SPEC_40FT.h
CONTAINER_MAX_WEIGHT = CONTAINER_SPEC_40FT.max_weight
CONTAINER_CENTER_Y = CONTAINER_SPEC_40FT.center_y


class SchemaError(ValueError):
    """Raised when an input JSON does not match the expected schema."""


@dataclass
class Dimensions:
    w: int
    l: int  # noqa: E741 - spec uses "l" for long side
    h: int


@dataclass
class Position:
    x: int
    y: int
    z: int


@dataclass
class Placement:
    item_id: str
    size_type: str
    dimensions: Dimensions
    position: Position
    weight: float
    is_rotated: bool
    destination_id: str

    @property
    def x_min(self) -> int:
        return self.position.x

    @property
    def x_max(self) -> int:
        return self.position.x + self.dimensions.w

    @property
    def y_min(self) -> int:
        return self.position.y

    @property
    def y_max(self) -> int:
        return self.position.y + self.dimensions.l

    @property
    def z_min(self) -> int:
        return self.position.z

    @property
    def z_max(self) -> int:
        return self.position.z + self.dimensions.h

    @property
    def volume(self) -> int:
        return self.dimensions.w * self.dimensions.l * self.dimensions.h

    @property
    def center_y(self) -> float:
        return self.position.y + self.dimensions.l / 2.0


@dataclass
class Container:
    container_id: int
    destination_id: str
    total_weight: float
    items: list[Placement] = field(default_factory=list)


@dataclass
class ProjectInfo:
    team_name: str
    execution_time_ms: int
    input_file: str | None = None


@dataclass
class LayoutResult:
    project_info: ProjectInfo
    containers: list[Container]
    spec: ContainerSpec = CONTAINER_SPEC_40FT


@dataclass
class ItemSpec:
    item_id: str
    size_type: str
    dimensions: Dimensions
    weight: float
    destination_id: str


@dataclass
class DatasetInfo:
    dataset_name: str
    seed: int
    item_count: int


@dataclass
class ItemsInput:
    dataset_info: DatasetInfo
    items: list[ItemSpec]


# ------------------------------------------------------------------
# Loaders
# ------------------------------------------------------------------


def _require(
    data: dict[str, Any],
    key: str,
    kind: type | tuple[type, ...],
    where: str,
) -> Any:
    if key not in data:
        raise SchemaError(f"{where}: missing required key '{key}'")
    value = data[key]
    if not isinstance(value, kind):
        expected = kind.__name__ if isinstance(kind, type) else "/".join(k.__name__ for k in kind)
        raise SchemaError(f"{where}: key '{key}' expected {expected}, got {type(value).__name__}")
    return value


def _parse_dimensions(raw: dict[str, Any], where: str) -> Dimensions:
    return Dimensions(
        w=int(_require(raw, "w", (int, float), where)),
        l=int(_require(raw, "l", (int, float), where)),
        h=int(_require(raw, "h", (int, float), where)),
    )


def _parse_position(raw: dict[str, Any], where: str) -> Position:
    return Position(
        x=int(_require(raw, "x", (int, float), where)),
        y=int(_require(raw, "y", (int, float), where)),
        z=int(_require(raw, "z", (int, float), where)),
    )


def _parse_placement(raw: dict[str, Any], container_id: int) -> Placement:
    where = f"container[{container_id}].items"
    dims = _parse_dimensions(_require(raw, "dimensions", dict, where), f"{where}.dimensions")
    pos = _parse_position(_require(raw, "position", dict, where), f"{where}.position")
    size_type = str(_require(raw, "size_type", str, where))
    if size_type not in VALID_SIZE_TYPES:
        raise SchemaError(f"{where}: invalid size_type '{size_type}'")
    return Placement(
        item_id=str(_require(raw, "item_id", str, where)),
        size_type=size_type,
        dimensions=dims,
        position=pos,
        weight=float(_require(raw, "weight", (int, float), where)),
        is_rotated=bool(_require(raw, "is_rotated", bool, where)),
        destination_id=str(_require(raw, "destination_id", str, where)),
    )


def _parse_container(raw: dict[str, Any]) -> Container:
    where = "container"
    container_id = int(_require(raw, "container_id", (int, float), where))
    destination_id = str(_require(raw, "destination_id", str, where))
    total_weight = float(_require(raw, "total_weight", (int, float), where))
    items_raw = _require(raw, "items", list, where)
    items = [_parse_placement(item, container_id) for item in items_raw]
    return Container(
        container_id=container_id,
        destination_id=destination_id,
        total_weight=total_weight,
        items=items,
    )


def _parse_project_info(raw: dict[str, Any]) -> ProjectInfo:
    where = "project_info"
    return ProjectInfo(
        team_name=str(_require(raw, "team_name", str, where)),
        execution_time_ms=int(_require(raw, "execution_time_ms", (int, float), where)),
        input_file=str(raw["input_file"]) if "input_file" in raw else None,
    )


def load_layout(path: str | Path) -> LayoutResult:
    """Load and validate `layout_result.json`."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SchemaError("layout_result: top-level must be an object")
    project_info = _parse_project_info(_require(data, "project_info", dict, "layout_result"))
    containers_raw = _require(data, "containers", list, "layout_result")
    containers = [_parse_container(c) for c in containers_raw]
    return LayoutResult(project_info=project_info, containers=containers)


def _parse_item_spec(raw: dict[str, Any]) -> ItemSpec:
    where = "items_input.items"
    dims = _parse_dimensions(_require(raw, "dimensions", dict, where), f"{where}.dimensions")
    size_type = str(_require(raw, "size_type", str, where))
    if size_type not in VALID_SIZE_TYPES:
        raise SchemaError(f"{where}: invalid size_type '{size_type}'")
    return ItemSpec(
        item_id=str(_require(raw, "item_id", str, where)),
        size_type=size_type,
        dimensions=dims,
        weight=float(_require(raw, "weight", (int, float), where)),
        destination_id=str(_require(raw, "destination_id", str, where)),
    )


def load_items(path: str | Path) -> ItemsInput:
    """Load and validate `items_input.json`."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SchemaError("items_input: top-level must be an object")
    info_raw = _require(data, "dataset_info", dict, "items_input")
    info = DatasetInfo(
        dataset_name=str(_require(info_raw, "dataset_name", str, "items_input.dataset_info")),
        seed=int(_require(info_raw, "seed", (int, float), "items_input.dataset_info")),
        item_count=int(_require(info_raw, "item_count", (int, float), "items_input.dataset_info")),
    )
    items_raw = _require(data, "items", list, "items_input")
    items = [_parse_item_spec(it) for it in items_raw]
    return ItemsInput(dataset_info=info, items=items)
