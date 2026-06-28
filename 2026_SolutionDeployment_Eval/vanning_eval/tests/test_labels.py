"""items_labels.json の fetch/update を local FS モードで検証。

GitHub API を叩かないよう VANNING_LOCAL_SCOREBOARD=1 + VANNING_LOCAL_ROOT を
tmp_path に向けてテストする (scoreboard_client._local_root と同じ仕組み)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vanning_viewer.scoreboard_client import (
    ScoreboardConfig,
    fetch_labels,
    update_label,
)


def _cfg() -> ScoreboardConfig:
    return ScoreboardConfig(
        owner="o", repo="r", path="scoreboard/history.json", branch="main", token="t"
    )


@pytest.fixture
def local_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VANNING_LOCAL_SCOREBOARD", "1")
    monkeypatch.setenv("VANNING_LOCAL_ROOT", str(tmp_path))
    return tmp_path


def test_fetch_labels_missing_returns_empty(local_root: Path) -> None:
    data, sha = fetch_labels(_cfg(), "scoreboard/items_labels.json")
    assert data == {}
    assert sha is None


def test_update_label_creates_and_roundtrips(local_root: Path) -> None:
    update_label(
        _cfg(),
        "scoreboard/items_labels.json",
        items_sha="abc123",
        label="お試しデータ A",
        author="rui",
    )
    data, sha = fetch_labels(_cfg(), "scoreboard/items_labels.json")
    assert sha == "local"
    assert data["abc123"]["label"] == "お試しデータ A"
    assert data["abc123"]["author"] == "rui"
    assert "updated_at" in data["abc123"]

    # 別ファイルが JSON object として有効
    raw = (local_root / "scoreboard" / "items_labels.json").read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)


def test_update_label_empty_string_removes_override(local_root: Path) -> None:
    path = "scoreboard/items_labels.json"
    update_label(_cfg(), path, "k1", "ラベル1")
    update_label(_cfg(), path, "k2", "ラベル2")
    update_label(_cfg(), path, "k1", "")  # remove
    data, _ = fetch_labels(_cfg(), path)
    assert "k1" not in data
    assert data["k2"]["label"] == "ラベル2"


def test_update_label_overwrites_existing(local_root: Path) -> None:
    path = "scoreboard/items_labels.json"
    update_label(_cfg(), path, "k1", "v1", author="a1")
    update_label(_cfg(), path, "k1", "v2", author="a2")
    data, _ = fetch_labels(_cfg(), path)
    assert data["k1"]["label"] == "v2"
    assert data["k1"]["author"] == "a2"
