"""content-hash で history と突き合わせて未提出判定する。

UI 提出も MCP 提出も `canonical_json_sha256(bytes)` で同じハッシュを作るので、
history.json の `files.layout_result.sha256` (+ items_input がある場合は同 sha256)
と一致する entry を skip 対象とみなす。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vanning_eval.canonical_input import canonical_json_sha256


@dataclass(frozen=True)
class SubmitDecision:
    """1 ペアに対する dedupe 判定。"""

    layout_path: Path
    items_path: Path | None
    layout_sha: str
    items_sha: str | None
    status: str  # "submit" | "skip"
    reason: str
    existing_entry_id: str | None = None
    existing_timestamp: str | None = None
    existing_author: str | None = None


def _entry_layout_sha(entry: dict[str, Any]) -> str | None:
    files = entry.get("files") or {}
    layout = files.get("layout_result") or {}
    sha = layout.get("sha256")
    return sha if isinstance(sha, str) else None


def _entry_items_sha(entry: dict[str, Any]) -> str | None:
    files = entry.get("files") or {}
    items = files.get("items_input") or {}
    sha = items.get("sha256")
    return sha if isinstance(sha, str) else None


def find_duplicate(
    history: list[dict[str, Any]],
    layout_sha: str,
    items_sha: str | None,
) -> dict[str, Any] | None:
    """history 内で layout (+items) sha が一致する entry を返す。hidden=True も対象。

    - items_sha=None のときは layout sha のみ一致を見る
    - items_sha 指定時は layout sha と items sha の **両方一致** を要求
      （layout のみ偶発一致で別 items に skip 判定が出るのを防ぐ）
    """
    for entry in history:
        if _entry_layout_sha(entry) != layout_sha:
            continue
        if items_sha is not None and _entry_items_sha(entry) != items_sha:
            continue
        return entry
    return None


def decide_submit(
    layout_path: Path,
    items_path: Path | None,
    history: list[dict[str, Any]],
) -> SubmitDecision:
    """1 ペアに対し submit/skip を判定する。"""
    layout_bytes = layout_path.read_bytes()
    layout_sha = canonical_json_sha256(layout_bytes)
    items_sha: str | None = None
    if items_path is not None:
        items_sha = canonical_json_sha256(items_path.read_bytes())

    existing = find_duplicate(history, layout_sha, items_sha)
    if existing is not None:
        return SubmitDecision(
            layout_path=layout_path,
            items_path=items_path,
            layout_sha=layout_sha,
            items_sha=items_sha,
            status="skip",
            reason="duplicate",
            existing_entry_id=str(existing.get("id") or ""),
            existing_timestamp=str(existing.get("timestamp") or ""),
            existing_author=str(existing.get("author") or ""),
        )
    return SubmitDecision(
        layout_path=layout_path,
        items_path=items_path,
        layout_sha=layout_sha,
        items_sha=items_sha,
        status="submit",
        reason="new",
    )
