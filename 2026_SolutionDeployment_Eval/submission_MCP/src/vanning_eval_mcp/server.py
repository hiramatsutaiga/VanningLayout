"""vanning-eval-mcp: MCP server for automating vanning-eval leaderboard submissions.

5 tools:
    - vanning_score_dry_run    GitHub 非接触のローカル採点
    - vanning_list_submissions history.json を canonical_gate 適用 + rank で整形
    - vanning_submit           単発提出（content-hash で重複なら自動 skip）
    - vanning_submit_batch     ファイル/ディレクトリ混在 list の bulk 提出
    - vanning_hide_submission  history entry の hidden フラグ操作

note には自動で `[mcp]` prefix、commit message には `via MCP` を含める
（UI 提出と区別可能、leaderboard 上で追跡しやすい）。
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from vanning_eval.canonical_input import (
    gate_entry_against_canonical,
    load_canonical_registry,
    score_from_report,
)
from vanning_eval.constraints import Violation
from vanning_eval.report import build_report
from vanning_eval.schema import load_items, load_layout
from vanning_viewer.scoreboard_client import (
    ScoreboardError,
    append_entry,
    blob_url,
    fetch_history,
    update_entry,
)
from vanning_viewer.submitter import (
    JST,
    apply_canonical_gate,
    build_scoreboard_entry,
    rank_entries,
    upload_input_file,
)

from vanning_eval_mcp.config import ConfigError, McpConfig, load_config
from vanning_eval_mcp.dedupe import SubmitDecision, decide_submit
from vanning_eval_mcp.paths import SubmissionPair, expand_paths, resolve_pair

# ---------------------------------------------------------------------------
# Logging (stdout/stderr are reserved for MCP protocol)
# ---------------------------------------------------------------------------
_pkg_root = Path(__file__).resolve().parent.parent.parent
_log_path = Path(os.environ.get("VANNING_MCP_LOG", _pkg_root / "mcp_server.log"))
_log_path.parent.mkdir(parents=True, exist_ok=True)
_handler = RotatingFileHandler(
    _log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
)
log = logging.getLogger("vanning_eval_mcp")
log.setLevel(logging.DEBUG)
log.addHandler(_handler)

mcp = FastMCP("vanning-eval-mcp")
_CFG: McpConfig | None = None
_REGISTRY_CACHE: dict[str, Any] | None = None


def _cfg() -> McpConfig:
    """McpConfig を遅延ロード（CLI 起動失敗時に tool 呼出で再試行できるように）。"""
    global _CFG
    if _CFG is None:
        _CFG = load_config()
        log.info(
            "cfg loaded: owner=%s repo=%s path=%s branch=%s repo_root=%s input_root=%s",
            _CFG.scoreboard.owner,
            _CFG.scoreboard.repo,
            _CFG.scoreboard.path,
            _CFG.scoreboard.branch,
            _CFG.repo_root,
            _CFG.input_root,
        )
    return _CFG


def _registry() -> dict[str, Any]:
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        _REGISTRY_CACHE = load_canonical_registry(_cfg().input_root)
    return _REGISTRY_CACHE


def _mcp_note(note: str | None) -> str:
    """UI と区別できるよう `[mcp]` を自動 prefix する（既に付いていれば二重に付けない）。"""
    body = (note or "").strip()
    if body.startswith("[mcp]"):
        return body
    return f"[mcp] {body}".strip() if body else "[mcp]"


def _resolve_author(cfg: McpConfig, author_arg: str | None) -> str:
    """投稿者を解決: tool 引数 > env (VANNING_DEFAULT_AUTHOR) > エラー。

    どちらも空なら ConfigError を投げる。誤投稿防止のため「無音で誰か別人扱い」
    にはせず明示的に失敗させる方針。
    """
    a = (author_arg or "").strip()
    if a:
        return a
    if cfg.default_author:
        return cfg.default_author
    raise ConfigError(
        "投稿者 (author) が未設定です。.mcp.json の env に "
        '"VANNING_DEFAULT_AUTHOR": "あなたの名前" を追加するか、'
        "tool 呼出で author 引数を明示してください。"
    )


def _format_decision(d: SubmitDecision) -> dict[str, Any]:
    """SubmitDecision -> JSON-friendly dict（preview 用）。"""
    return {
        "layout_path": str(d.layout_path),
        "items_path": str(d.items_path) if d.items_path else None,
        "layout_sha": d.layout_sha,
        "items_sha": d.items_sha,
        "status": d.status,
        "reason": d.reason,
        "existing_entry_id": d.existing_entry_id,
        "existing_timestamp": d.existing_timestamp,
        "existing_author": d.existing_author,
    }


def _score_layout(layout_path: Path, items_path: Path | None) -> dict[str, Any]:
    """ローカル採点（GitHub 非接触）。verdict / score / violations / gate_status を返す。"""
    layout = load_layout(layout_path)
    if items_path is None:
        # 本番 _build_report_for_submit と同じ verdict にして preview と submit
        # の結果が乖離しないようにする（旧 "unknown" は混乱の元）。
        return {
            "verdict": "disqualified",
            "gate_status": "no_items_input",
            "gate_reason": "items_input.json が指定されていません",
            "score": None,
            "violations": [],
        }
    items = load_items(items_path)
    report = build_report(layout, items)
    gate = gate_entry_against_canonical(layout_path, items_path, _registry())
    return {
        "verdict": report.get("verdict"),
        "gate_status": gate.status,
        "gate_reason": gate.reason,
        "canonical_dataset_id": gate.canonical.dataset_id if gate.canonical else None,
        "score": score_from_report(report),
        "violations": [
            {
                "code": d.get("code"),
                "container_id": d.get("container_id"),
                "detail": d.get("detail"),
            }
            for d in report.get("disqualifications", [])
        ],
        "execution_time_ms": report["internal_metrics"]["execution_time_ms"],
    }


# 409 Conflict のリトライ回数とバックオフ秒。UI 並行 commit との衝突で append_entry
# が `PUT contents failed: 409 ...` を投げるため、`scoreboard_client.py` docstring
# の「呼び出し側でリトライする」契約を満たすラッパを置く。
_RETRY_BACKOFFS_SEC = (0.5, 1.0, 2.0)


def _is_conflict_error(exc: ScoreboardError) -> bool:
    """GitHub Contents API の楽観ロック衝突を示すエラーか判定。"""
    msg = str(exc)
    return "409" in msg or "Conflict" in msg or "422" in msg


def _append_entry_with_retry(
    cfg: McpConfig,
    entry: dict[str, Any],
    commit_message: str,
) -> None:
    """`append_entry` を 409/422 衝突時に最大 3 回までリトライする。

    各リトライは内部で `fetch_history` を再実行し新しい sha を取得するので、
    UI 並行 commit と素直に協調する。それ以外のエラーは即座に再 raise。
    """
    for attempt, delay in enumerate((*_RETRY_BACKOFFS_SEC, None)):
        try:
            append_entry(cfg.scoreboard, entry, commit_message=commit_message)
            return
        except ScoreboardError as exc:
            if delay is None or not _is_conflict_error(exc):
                raise
            log.warning(
                "append_entry conflict (attempt %d/%d): %s — retry in %.1fs",
                attempt + 1,
                len(_RETRY_BACKOFFS_SEC) + 1,
                str(exc)[:200],
                delay,
            )
            time.sleep(delay)


def _build_report_for_submit(
    layout_path: Path, items_path: Path | None
) -> tuple[dict[str, Any], list[Violation]]:
    """submit 用の report と violations を構築。items 無しは UI 同様ダミー entry。"""
    if items_path is None:
        report: dict[str, Any] = {
            "verdict": "disqualified",
            "teacher_score_metrics": {
                "containers_used": 0,
                "average_fill_rate": 0.0,
                "fill_rate_per_container": [],
                "cog_dev_per_container": [],
            },
            "internal_metrics": {
                "execution_time_ms": 0,
                "cog_y_stats": {"mean_deviation": 0.0},
            },
            "disqualifications": [],
        }
        return report, []
    layout = load_layout(layout_path)
    items = load_items(items_path)
    report = build_report(layout, items)
    violations = [
        Violation(
            code=str(d.get("code")),
            container_id=d.get("container_id"),
            items=list(d.get("items") or []),
            detail=str(d.get("detail") or ""),
        )
        for d in report.get("disqualifications", [])
    ]
    return report, violations


def _mirror_to_local(cfg: McpConfig, rel_path: str, content_bytes: bytes) -> None:
    """submit したファイルを `<repo_root>/<rel_path>` にも書き出す。

    MCP は GitHub に直 push する一方、後段の `apply_canonical_gate` は
    `<repo_root>/<files.layout_result.path>` をローカル read する。git pull
    していないかぎりこのパスは存在せず `schema_error → DQ` 落ちする
    （2026-05-22 a73da046 / hard_01..12 の 13 件で実証）。git pull の代わりに
    submit 成功時に同じ bytes をローカルへ書き、後段 gate を成立させる。

    既に同一内容のファイルがあれば何もしない（Drive 同期負荷を抑える）。
    repo_root が解決済みでないケースは無条件 noop。
    """
    if not cfg.repo_root or not rel_path:
        return
    dest = cfg.repo_root / Path(rel_path)
    try:
        if dest.exists() and dest.read_bytes() == content_bytes:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content_bytes)
    except OSError as exc:
        # mirror 失敗は GitHub push 自体は成功しているので warn のみ
        log.warning("local mirror failed: dest=%s err=%s", dest, exc)


def _submit_one(
    cfg: McpConfig,
    layout_path: Path,
    items_path: Path | None,
    *,
    author: str,
    note: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """1 ペアを実際に GitHub へ提出する（重複判定は呼出側で実施済み前提）。

    戻り値は `(public_result, posted_entry)` の tuple:
    - `public_result`: tool 戻り値に含める dict（entry_id / score / files / verdict 等）
    - `posted_entry`: batch loop が `history.append` で内部 dedup に使うフル entry dict
    両者を物理的に分けることで、内部用フィールドが tool 戻り値に漏れる経路を構造的に塞ぐ。
    """
    layout_bytes = layout_path.read_bytes()
    source_dir = layout_path.parent.name
    files_meta: dict[str, dict[str, Any]] = {
        "layout_result": upload_input_file(
            cfg.scoreboard,
            content_bytes=layout_bytes,
            original_name=layout_path.name,
            registry=_registry(),
            source_dir=source_dir,
        )
    }
    _mirror_to_local(cfg, files_meta["layout_result"].get("path", ""), layout_bytes)
    if items_path is not None:
        items_bytes = items_path.read_bytes()
        files_meta["items_input"] = upload_input_file(
            cfg.scoreboard,
            content_bytes=items_bytes,
            original_name=items_path.name,
            registry=_registry(),
            role="items_input",
            source_dir=items_path.parent.name,
        )
        _mirror_to_local(cfg, files_meta["items_input"].get("path", ""), items_bytes)

    report, violations = _build_report_for_submit(layout_path, items_path)
    entry = build_scoreboard_entry(
        author=author,
        note=note,
        input_filename=layout_path.name,
        report=report,
        violations=violations,
        files=files_meta,
    )
    commit_msg = f"scoreboard: {author} via MCP ({entry['verdict']})"
    _append_entry_with_retry(cfg, entry, commit_message=commit_msg)

    public_result: dict[str, Any] = {
        "status": "submitted",
        "entry_id": entry["id"],
        "verdict": entry["verdict"],
        "score": entry["score"],
        "files": files_meta,
        "history_url": blob_url(cfg.scoreboard, cfg.scoreboard.path),
    }
    return public_result, entry


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool()
def vanning_score_dry_run(
    layout_path: str,
    items_path: str | None = None,
) -> dict[str, Any]:
    """layout (+items) をローカルで採点して結果を返す。GitHub には触らない（offline OK）。

    items_path 省略時は同ディレクトリの items_input.json を自動探索する。
    """
    pair = resolve_pair(Path(layout_path), Path(items_path) if items_path else None)
    log.info("score_dry_run: layout=%s items=%s", pair.layout, pair.items)
    result = _score_layout(pair.layout, pair.items)
    result["layout_path"] = str(pair.layout)
    result["items_path"] = str(pair.items) if pair.items else None
    return result


@mcp.tool()
def vanning_list_submissions(
    limit: int = 20,
    hidden: bool = False,
    author: str | None = None,
    items_group: str | None = None,
) -> dict[str, Any]:
    """history.json を canonical_gate 適用 + 順位付けして返す。

    - hidden=False: 公開分のみ / True: 非表示分のみ
    - author: 部分一致フィルタ
    - items_group: items_input の content_hash prefix で絞込（例: "items:abc..."）
    """
    cfg = _cfg()
    history, _ = fetch_history(cfg.scoreboard)
    log.info(
        "list_submissions: total=%d hidden=%s author=%s", len(history), hidden, author
    )
    gated = apply_canonical_gate(history, repo_root=cfg.repo_root, registry=_registry())
    ranked = rank_entries(gated)

    def keep(e: dict[str, Any]) -> bool:
        if bool(e.get("hidden")) != hidden:
            return False
        if author and author not in str(e.get("author", "")):
            return False
        if items_group:
            files = e.get("files") or {}
            items = files.get("items_input") or {}
            key = str(items.get("content_hash") or items.get("group_key") or "")
            if not key.startswith(items_group):
                return False
        return True

    filtered = [e for e in ranked if keep(e)]
    shown = filtered[: max(0, int(limit))]
    return {
        "total": len(history),
        "filtered": len(filtered),
        "shown": len(shown),
        "entries": [
            {
                "rank": e.get("_rank"),
                "id": e.get("id"),
                "author": e.get("author"),
                "note": e.get("note"),
                "timestamp": e.get("timestamp"),
                "verdict": e.get("verdict"),
                "score": e.get("score"),
                "gate_status": e.get("_gate"),
                "gate_reason": e.get("_gate_reason"),
                "input_filename": e.get("input_filename"),
                "hidden": bool(e.get("hidden")),
            }
            for e in shown
        ],
    }


@mcp.tool()
def vanning_submit(
    layout_path: str,
    author: str | None = None,
    items_path: str | None = None,
    note: str | None = None,
    dry_run: bool = False,
    require_items: bool = False,
) -> dict[str, Any]:
    """1 ペアを提出する。重複は自動 skip。dry_run=True なら preview のみ。

    - author 省略時は env `VANNING_DEFAULT_AUTHOR` から取得。両方未設定ならエラー。
    - items_path 省略時は paired を自動探索
    - require_items=True かつ items が見つからない場合はエラーを返す
    """
    cfg = _cfg()
    try:
        author = _resolve_author(cfg, author)
    except ConfigError as exc:
        return {"status": "error", "reason": str(exc)}
    pair = resolve_pair(Path(layout_path), Path(items_path) if items_path else None)
    if require_items and pair.items is None:
        return {
            "status": "error",
            "reason": "items_input が見つかりません (require_items=True)",
            "layout_path": str(pair.layout),
        }

    history, _ = fetch_history(cfg.scoreboard)
    decision = decide_submit(pair.layout, pair.items, history)
    log.info(
        "submit: layout=%s items=%s status=%s reason=%s",
        pair.layout,
        pair.items,
        decision.status,
        decision.reason,
    )
    preview = _format_decision(decision)

    if decision.status == "skip":
        return {**preview, "status": "skipped"}

    preview["preview_score"] = _score_layout(pair.layout, pair.items)
    if dry_run:
        return {**preview, "status": "preview"}

    full_note = _mcp_note(note)
    # `_submit_one` は `(public_result, posted_entry)` の tuple を返す。
    # 単発側は posted_entry を捨てる（batch dedup 用なので）。
    result, _ = _submit_one(cfg, pair.layout, pair.items, author=author, note=full_note)
    return {**preview, **result, "note": full_note}


@mcp.tool()
def vanning_submit_batch(
    paths: list[str],
    author: str | None = None,
    dry_run: bool = True,
    note_template: str | None = None,
    glob_pattern: str = "layout_result.json",
    require_items: bool = False,
) -> dict[str, Any]:
    """ファイル/ディレクトリ混在の list を受けて未提出のみ submit。

    - author 省略時は env `VANNING_DEFAULT_AUTHOR` から取得。両方未設定ならエラー。
    - paths は file/dir 混在可。dir は `**/{glob_pattern}` で再帰 glob
    - dry_run=True (default): submit せず preview だけ
    - note_template は f-string 風置換可:
        `{layout_filename}`, `{items_filename}`, `{verdict}`, `{containers_used}`
    """
    cfg = _cfg()
    try:
        author = _resolve_author(cfg, author)
    except ConfigError as exc:
        return {"status": "error", "reason": str(exc)}
    try:
        layout_files = expand_paths(list(paths), glob_pattern=glob_pattern)
    except FileNotFoundError as exc:
        return {"status": "error", "reason": str(exc)}

    history, _ = fetch_history(cfg.scoreboard)
    log.info(
        "submit_batch: paths=%d expanded=%d dry_run=%s",
        len(paths),
        len(layout_files),
        dry_run,
    )

    decisions: list[dict[str, Any]] = []
    pairs: list[SubmissionPair | None] = []
    for layout in layout_files:
        pair = resolve_pair(layout)
        if require_items and pair.items is None:
            decisions.append(
                {
                    "layout_path": str(layout),
                    "items_path": None,
                    "status": "error",
                    "reason": "items_input が見つかりません (require_items=True)",
                }
            )
            pairs.append(None)
            continue
        d = decide_submit(pair.layout, pair.items, history)
        pairs.append(pair)
        decisions.append(_format_decision(d))

    to_submit_idx = [i for i, d in enumerate(decisions) if d.get("status") == "submit"]
    to_skip = [i for i, d in enumerate(decisions) if d.get("status") == "skip"]
    to_error = [i for i, d in enumerate(decisions) if d.get("status") == "error"]

    for i in to_submit_idx:
        pair = pairs[i]
        assert pair is not None
        decisions[i]["preview_score"] = _score_layout(pair.layout, pair.items)

    summary: dict[str, Any] = {
        "total": len(decisions),
        "to_submit": len(to_submit_idx),
        "to_skip": len(to_skip),
        "to_error": len(to_error),
        "items": decisions,
    }
    if dry_run:
        return {"status": "preview", **summary}

    submitted: list[dict[str, Any]] = []
    for i in to_submit_idx:
        pair = pairs[i]
        assert pair is not None
        # batch 内 dedup: 直前 submit までで growth した history に対し再判定。
        # 同じバッチに同 layout が複数あった場合、2 件目以降は skip される。
        # `_batch_dedup` フラグはサマリ集計の構造化判定用（reason 文字列依存を避ける）。
        fresh = decide_submit(pair.layout, pair.items, history)
        if fresh.status == "skip":
            decisions[i] = {
                **decisions[i],
                **_format_decision(fresh),
                "status": "skipped",
                "reason": "duplicate (already submitted earlier in this batch)",
                "_batch_dedup": True,
            }
            continue
        preview = decisions[i].get("preview_score") or {}
        verdict = str(preview.get("verdict", ""))
        score = preview.get("score") or {}
        containers_used = score.get("containers_used", "")
        items_filename = pair.items.name if pair.items else ""
        body = ""
        if note_template:
            try:
                body = note_template.format(
                    layout_filename=pair.layout.name,
                    items_filename=items_filename,
                    verdict=verdict,
                    containers_used=containers_used,
                )
            except (KeyError, IndexError, AttributeError, ValueError) as exc:
                body = f"{note_template} (template error: {exc})"
        full_note = _mcp_note(body)
        try:
            result, posted_entry = _submit_one(
                cfg, pair.layout, pair.items, author=author, note=full_note
            )
            # 投稿成功 → ローカル history に entry を append、後続 iteration の
            # `decide_submit` に反映する（GitHub 再 fetch せずに dedup を効かせる）。
            history.append(posted_entry)
            decisions[i].update(result)
            decisions[i]["note"] = full_note
            submitted.append({"index": i, **result})
        except Exception as exc:  # noqa: BLE001 - GitHub API は何でも投げうるので捕捉して継続
            log.exception("submit failed for %s", pair.layout)
            decisions[i]["status"] = "error"
            decisions[i]["reason"] = f"submit failed: {exc}"

    # 実 submit 結果のサマリ。batch 内 dedup で skipped 化された件数は
    # `_batch_dedup` フラグ（構造化判定）で集計し、文字列マッチに依存しない。
    final_submitted_idx = [
        i for i, d in enumerate(decisions) if d.get("status") == "submitted"
    ]
    batch_dedup_skipped = sum(1 for d in decisions if d.get("_batch_dedup"))
    summary["status"] = "submitted"
    summary["submitted_count"] = len(final_submitted_idx)
    summary["batch_dedup_skipped"] = batch_dedup_skipped
    # tool 戻り値に `_` prefix の内部フィールドを漏らさない（構造的 sanitize）。
    summary["items"] = [
        {k: v for k, v in d.items() if not k.startswith("_")} for d in decisions
    ]
    return summary


@mcp.tool()
def vanning_hide_submission(entry_id: str, hidden: bool = True) -> dict[str, Any]:
    """history entry の hidden フラグ操作。hidden=False で復元。"""
    cfg = _cfg()
    patch: dict[str, Any] = {"hidden": bool(hidden)}
    if hidden:
        patch["hidden_at"] = datetime.now(JST).isoformat(timespec="seconds")
    else:
        patch["hidden_at"] = None
    commit_msg = f"scoreboard: {'hide' if hidden else 'unhide'} {entry_id} via MCP"
    log.info("hide_submission: entry_id=%s hidden=%s", entry_id, hidden)
    update_entry(cfg.scoreboard, entry_id, patch, commit_message=commit_msg)
    return {
        "status": "ok",
        "entry_id": entry_id,
        "hidden": bool(hidden),
        "patch": patch,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    try:
        _cfg()
    except ConfigError as exc:
        log.warning("config not ready at startup: %s", exc)
    log.info("vanning-eval-mcp starting, log=%s", _log_path)
    mcp.run()


if __name__ == "__main__":
    main()
