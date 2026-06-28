"""submitter.py の純関数を直接テスト（streamlit_app 経由のラッパとは別）。

test_streamlit_score.py が rank/canonical_gate 系を間接的にカバーしているので、
こちらは MCP サーバーや自動化スクリプトから直接呼ぶ用途の signature・出力スキーマを固定する。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vanning_eval.canonical_input import load_canonical_registry
from vanning_eval.constraints import Violation
from vanning_viewer.scoreboard_client import ScoreboardConfig
from vanning_viewer.submitter import (
    apply_canonical_gate,
    build_scoreboard_entry,
    submissions_dir,
    upload_input_file,
)


def _cfg(path: str = "scoreboard/history.json") -> ScoreboardConfig:
    return ScoreboardConfig(
        owner="test-owner", repo="test-repo", path=path, branch="main", token="dummy"
    )


def test_submissions_dir_alongside_history():
    assert submissions_dir(_cfg("scoreboard/history.json")) == "scoreboard/submissions"


def test_submissions_dir_nested_path():
    assert submissions_dir(_cfg("nested/board/h.json")) == "nested/board/submissions"


def test_submissions_dir_root_path():
    assert submissions_dir(_cfg("history.json")) == "submissions"


def test_build_scoreboard_entry_schema():
    """entry に id / timestamp / verdict / score / violations_summary が必ず入る。"""
    report = {
        "verdict": "pass",
        "teacher_score_metrics": {
            "containers_used": 3,
            "average_fill_rate": 0.75,
            "fill_rate_per_container": [0.7, 0.8],
            "cog_dev_per_container": [100.0, 200.0],
        },
        "internal_metrics": {
            "execution_time_ms": 1234,
            "cog_y_stats": {"mean_deviation": 150.0},
        },
        "disqualifications": [],
    }
    entry = build_scoreboard_entry(
        author="alice",
        note="test note",
        input_filename="layout_x.json",
        report=report,
        violations=[],
    )
    assert isinstance(entry["id"], str) and len(entry["id"]) >= 36
    assert entry["author"] == "alice"
    assert entry["note"] == "test note"
    assert entry["input_filename"] == "layout_x.json"
    assert entry["verdict"] == "pass"
    assert entry["score"]["containers_used"] == 3
    assert entry["score"]["execution_time_ms"] == 1234
    assert entry["violations_summary"] == []
    assert "files" not in entry  # files=None なら付かない


def test_build_scoreboard_entry_violations_and_files():
    report = {
        "verdict": "disqualified",
        "teacher_score_metrics": {
            "containers_used": 5,
            "average_fill_rate": 0.4,
            "fill_rate_per_container": [],
            "cog_dev_per_container": [],
        },
        "internal_metrics": {
            "execution_time_ms": 99,
            "cog_y_stats": {"mean_deviation": 9000.0},
        },
        "disqualifications": [{"code": "WEIGHT_EXCEEDED", "container_id": 2}],
    }
    files: dict[str, dict[str, Any]] = {
        "layout_result": {"path": "scoreboard/submissions/abc.json", "sha256": "abc"}
    }
    entry = build_scoreboard_entry(
        author="bob",
        note="",
        input_filename="x.json",
        report=report,
        violations=[Violation(code="WEIGHT_EXCEEDED", container_id=2, items=[], detail="over")],
        files=files,
    )
    assert entry["verdict"] == "disqualified"
    assert entry["score"]["violation_count"] == 1
    assert entry["violations_summary"] == [{"code": "WEIGHT_EXCEEDED", "container_id": 2}]
    assert entry["files"] == files


@pytest.fixture
def local_scoreboard(tmp_path: Path, monkeypatch):
    """`VANNING_LOCAL_SCOREBOARD=1` で tmp_path をルートに upload_if_absent を localfs 動作させる。"""
    monkeypatch.setenv("VANNING_LOCAL_SCOREBOARD", "1")
    monkeypatch.setenv("VANNING_LOCAL_ROOT", str(tmp_path))
    return tmp_path


def test_upload_input_file_basic_meta(local_scoreboard: Path):
    cfg = _cfg("scoreboard/history.json")
    payload = b'{"hello":"world"}'
    meta = upload_input_file(
        cfg,
        content_bytes=payload,
        original_name="layout_result.json",
        registry={},
    )
    assert meta["path"].startswith("scoreboard/submissions/")
    assert meta["path"].endswith(".json")
    assert isinstance(meta["sha256"], str) and len(meta["sha256"]) == 64
    assert Path(meta["path"]).name == meta["sha256"][:16] + ".json"
    assert meta["original_name"] == "layout_result.json"
    # local fs に実際に書かれている
    written = local_scoreboard / meta["path"]
    assert written.exists() and written.read_bytes() == payload


def test_upload_input_file_items_role_extra_fields(local_scoreboard: Path):
    cfg = _cfg("scoreboard/history.json")
    items_doc = {
        "dataset_info": {"dataset_name": "case_01", "seed": 42, "item_count": 1},
        "items": [
            {
                "item_id": "P001",
                "size_type": "small",
                "dimensions": {"w": 760, "l": 1130, "h": 550},
                "weight": 100.0,
                "destination_id": "A",
            }
        ],
    }
    payload = json.dumps(items_doc).encode("utf-8")
    meta = upload_input_file(
        cfg,
        content_bytes=payload,
        original_name="items_input.json",
        registry={},
        role="items_input",
    )
    # role="items_input" 限定の追加フィールド
    assert meta["content_hash"].startswith("items:")
    assert meta["group_key"] == meta["content_hash"]
    assert meta["canonical_dataset_id"] is None
    assert meta["is_canonical"] is False
    assert meta["dataset_info"] == items_doc["dataset_info"]


def test_upload_input_file_canonical_match(local_scoreboard: Path, tmp_path: Path):
    """registry に同内容が登録されていれば canonical_dataset_id / is_canonical が立つ。"""
    cfg = _cfg("scoreboard/history.json")
    items_doc = {
        "dataset_info": {"dataset_name": "case_01", "seed": 42, "item_count": 1},
        "items": [
            {
                "item_id": "P001",
                "size_type": "small",
                "dimensions": {"w": 760, "l": 1130, "h": 550},
                "weight": 100.0,
                "destination_id": "A",
            }
        ],
    }
    official = tmp_path / "input" / "official_case_01_seed42"
    official.mkdir(parents=True)
    (official / "items_input.json").write_text(json.dumps(items_doc), encoding="utf-8")
    registry = load_canonical_registry(tmp_path / "input")
    assert registry

    meta = upload_input_file(
        cfg,
        content_bytes=json.dumps(items_doc).encode("utf-8"),
        original_name="items_input.json",
        registry=registry,
        role="items_input",
    )
    assert meta["is_canonical"] is True
    assert meta["canonical_dataset_id"] == "official_case_01_seed42"


def test_apply_canonical_gate_injection(tmp_path: Path):
    """apply_canonical_gate が repo_root / registry の注入版で動く（streamlit_app 非依存）。"""
    items_doc = {
        "dataset_info": {"dataset_name": "case_01", "seed": 42, "item_count": 1},
        "items": [
            {
                "item_id": "P001",
                "size_type": "small",
                "dimensions": {"w": 760, "l": 1130, "h": 550},
                "weight": 100.0,
                "destination_id": "A",
            }
        ],
    }
    layout_doc = {
        "project_info": {"team_name": "T", "execution_time_ms": 10},
        "containers": [
            {
                "container_id": 1,
                "destination_id": "A",
                "total_weight": 100.0,
                "items": [
                    {
                        "item_id": "P001",
                        "size_type": "small",
                        "dimensions": {"w": 760, "l": 1130, "h": 550},
                        "position": {"x": 0, "y": 5435, "z": 0},
                        "weight": 100.0,
                        "is_rotated": False,
                        "destination_id": "A",
                    }
                ],
            }
        ],
    }
    sub = tmp_path / "scoreboard" / "submissions"
    sub.mkdir(parents=True)
    (sub / "L.json").write_text(json.dumps(layout_doc), encoding="utf-8")
    (sub / "I.json").write_text(json.dumps(items_doc), encoding="utf-8")
    entries = [
        {
            "verdict": "pass",
            "score": {"containers_used": 1, "cog_y_mean_deviation": 0, "execution_time_ms": 5},
            "files": {
                "layout_result": {"path": "scoreboard/submissions/L.json"},
                "items_input": {"path": "scoreboard/submissions/I.json"},
            },
        }
    ]
    gated = apply_canonical_gate(entries, repo_root=tmp_path, registry={})
    assert gated[0]["_gate"] == "non_canonical"
    assert gated[0]["verdict"] == "pass"


def test_apply_canonical_gate_missing_layout(tmp_path: Path):
    """layout_result が記録なしのエントリは schema_error で失格化。"""
    entries = [{"verdict": "pass", "score": {}, "files": {}}]
    gated = apply_canonical_gate(entries, repo_root=tmp_path, registry={})
    assert gated[0]["_gate"] == "schema_error"
    assert gated[0]["verdict"] == "disqualified"
