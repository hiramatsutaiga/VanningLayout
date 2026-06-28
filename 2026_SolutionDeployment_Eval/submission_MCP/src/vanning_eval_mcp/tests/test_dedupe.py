"""dedupe.py の content-hash 突き合わせをテスト。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vanning_eval.canonical_input import canonical_json_sha256
from vanning_eval_mcp.dedupe import decide_submit, find_duplicate


def _hist_entry(
    layout_sha: str, items_sha: str | None = None, *, eid: str = "e1"
) -> dict[str, Any]:
    files: dict[str, Any] = {"layout_result": {"sha256": layout_sha}}
    if items_sha is not None:
        files["items_input"] = {"sha256": items_sha}
    return {
        "id": eid,
        "author": "alice",
        "timestamp": "2026-05-20T12:00:00+09:00",
        "files": files,
    }


def test_find_duplicate_layout_only():
    layout_sha = "L" * 64
    history = [_hist_entry(layout_sha)]
    hit = find_duplicate(history, layout_sha, None)
    assert hit is not None and hit["id"] == "e1"


def test_find_duplicate_layout_mismatch():
    history = [_hist_entry("L" * 64)]
    assert find_duplicate(history, "M" * 64, None) is None


def test_find_duplicate_requires_both_when_items_given():
    """items_sha 指定時は layout sha と items sha の両方一致を要求。"""
    history = [_hist_entry("L" * 64, items_sha="I" * 64, eid="e1")]
    # layout 一致でも items 不一致なら別エントリ
    assert find_duplicate(history, "L" * 64, "J" * 64) is None
    # 両方一致なら hit
    hit = find_duplicate(history, "L" * 64, "I" * 64)
    assert hit is not None and hit["id"] == "e1"


def test_find_duplicate_hidden_still_matches():
    """hidden=True の entry も skip 対象（再提出を許さない）。"""
    entry = _hist_entry("L" * 64, eid="hidden_one")
    entry["hidden"] = True
    history = [entry]
    hit = find_duplicate(history, "L" * 64, None)
    assert hit is not None and hit["id"] == "hidden_one"


def test_decide_submit_new(tmp_path: Path):
    layout = tmp_path / "L.json"
    layout.write_bytes(b'{"new":true}')
    d = decide_submit(layout, None, history=[])
    assert d.status == "submit"
    assert d.reason == "new"
    assert d.layout_sha == canonical_json_sha256(layout.read_bytes())
    assert d.items_sha is None
    assert d.existing_entry_id is None


def test_decide_submit_skip_duplicate(tmp_path: Path):
    layout = tmp_path / "L.json"
    layout.write_bytes(b'{"a":1}')
    layout_sha = canonical_json_sha256(layout.read_bytes())
    history = [_hist_entry(layout_sha, eid="dup-id")]
    d = decide_submit(layout, None, history=history)
    assert d.status == "skip"
    assert d.reason == "duplicate"
    assert d.existing_entry_id == "dup-id"
    assert d.existing_author == "alice"


def test_decide_submit_with_items_pair(tmp_path: Path):
    layout = tmp_path / "L.json"
    items = tmp_path / "I.json"
    layout_doc = {"layout": True}
    items_doc = {"items": [{"item_id": "P001"}]}
    layout.write_text(json.dumps(layout_doc), encoding="utf-8")
    items.write_text(json.dumps(items_doc), encoding="utf-8")
    layout_sha = canonical_json_sha256(layout.read_bytes())
    items_sha = canonical_json_sha256(items.read_bytes())
    history = [_hist_entry(layout_sha, items_sha=items_sha, eid="pair-dup")]
    d = decide_submit(layout, items, history=history)
    assert d.status == "skip"
    assert d.existing_entry_id == "pair-dup"
    assert d.layout_sha == layout_sha
    assert d.items_sha == items_sha
