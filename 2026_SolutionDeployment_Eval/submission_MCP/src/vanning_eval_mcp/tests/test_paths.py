"""paths.py の resolve_pair / expand_paths をテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from vanning_eval_mcp.paths import expand_paths, resolve_pair


def test_resolve_pair_explicit_items(tmp_path: Path):
    layout = tmp_path / "layout_result.json"
    items = tmp_path / "items_input.json"
    layout.write_text("{}", encoding="utf-8")
    items.write_text("{}", encoding="utf-8")
    pair = resolve_pair(layout, items)
    assert pair.layout == layout
    assert pair.items == items


def test_resolve_pair_auto_items_in_same_dir(tmp_path: Path):
    layout = tmp_path / "layout_result.json"
    items = tmp_path / "items_input.json"
    layout.write_text("{}", encoding="utf-8")
    items.write_text("{}", encoding="utf-8")
    pair = resolve_pair(layout)
    assert pair.items == items


def test_resolve_pair_layout_to_items_rename(tmp_path: Path):
    """layout_foo.json -> items_foo.json への 1:1 推測。"""
    layout = tmp_path / "layout_foo.json"
    items = tmp_path / "items_foo.json"
    layout.write_text("{}", encoding="utf-8")
    items.write_text("{}", encoding="utf-8")
    pair = resolve_pair(layout)
    assert pair.items == items


def test_resolve_pair_no_items_found(tmp_path: Path):
    """items が見つからなければ None。"""
    layout = tmp_path / "layout_result.json"
    layout.write_text("{}", encoding="utf-8")
    pair = resolve_pair(layout)
    assert pair.items is None


def test_expand_paths_file(tmp_path: Path):
    f1 = tmp_path / "a.json"
    f1.write_text("{}", encoding="utf-8")
    out = expand_paths([f1])
    assert out == [f1.resolve()]


def test_expand_paths_directory_recursive(tmp_path: Path):
    """dir 指定で **/layout_result.json を再帰 glob する。"""
    (tmp_path / "subA").mkdir()
    (tmp_path / "subB" / "nested").mkdir(parents=True)
    (tmp_path / "subA" / "layout_result.json").write_text("{}", encoding="utf-8")
    (tmp_path / "subB" / "nested" / "layout_result.json").write_text(
        "{}", encoding="utf-8"
    )
    (tmp_path / "subA" / "items_input.json").write_text(
        "{}", encoding="utf-8"
    )  # 拾わない

    out = expand_paths([tmp_path])
    assert len(out) == 2
    assert all(p.name == "layout_result.json" for p in out)


def test_expand_paths_custom_glob(tmp_path: Path):
    (tmp_path / "subA").mkdir()
    (tmp_path / "subA" / "layout_x.json").write_text("{}", encoding="utf-8")
    (tmp_path / "subA" / "layout_y.json").write_text("{}", encoding="utf-8")
    out = expand_paths([tmp_path], glob_pattern="layout_*.json")
    assert len(out) == 2


def test_expand_paths_dedup(tmp_path: Path):
    f = tmp_path / "a.json"
    f.write_text("{}", encoding="utf-8")
    out = expand_paths([f, f, tmp_path])
    assert out == [f.resolve()]


def test_expand_paths_not_found_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        expand_paths([tmp_path / "does_not_exist.json"])


def test_expand_paths_skip_non_json(tmp_path: Path):
    """ファイル直指定で .json 以外は黙って除外。"""
    txt = tmp_path / "a.txt"
    txt.write_text("ignored", encoding="utf-8")
    out = expand_paths([txt])
    assert out == []
