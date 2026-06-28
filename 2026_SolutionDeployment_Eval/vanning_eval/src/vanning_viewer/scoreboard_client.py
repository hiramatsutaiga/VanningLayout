"""GitHub Contents API 経由でリーダーボード JSON を読み書きするクライアント。

リポジトリ内の `scoreboard/history.json`（リスト）に評価結果を append する想定。
認証は Fine-grained PAT（Contents: Read and write）を Authorization ヘッダで渡す。

API 仕様:
    https://docs.github.com/en/rest/repos/contents
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

import requests

API_BASE = "https://api.github.com"


def _local_root() -> Path | None:
    """`VANNING_LOCAL_SCOREBOARD=1` のときローカル FS モードで使うルートを返す。

    GitHub API を叩かず `<root>/<cfg.path>` 等を直接読み書きする。
    push 前の動作確認用。`VANNING_LOCAL_ROOT` で root を上書き可能（既定: vanning-eval/）。
    """
    if os.environ.get("VANNING_LOCAL_SCOREBOARD", "").strip() not in ("1", "true", "True"):
        return None
    override = os.environ.get("VANNING_LOCAL_ROOT")
    if override:
        return Path(override).resolve()
    # scoreboard_client.py から見て: src/vanning_viewer/scoreboard_client.py
    # parents[3] = vanning_eval_rui の親 = vanning-eval/
    return Path(__file__).resolve().parents[3]


def _local_path(cfg_or_root: ScoreboardConfig | Path, path: str) -> Path:
    root = cfg_or_root if isinstance(cfg_or_root, Path) else _local_root()
    assert root is not None
    return root / path


@dataclass(frozen=True)
class ScoreboardConfig:
    """scoreboard_client を初期化するための設定。secrets.toml から生成する。"""

    owner: str
    repo: str
    path: str
    branch: str
    token: str


class ScoreboardError(RuntimeError):
    """GitHub API 呼び出しで回復不能なエラーが起きたときに投げる。"""


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _content_url(cfg: ScoreboardConfig, path: str | None = None) -> str:
    return f"{API_BASE}/repos/{cfg.owner}/{cfg.repo}/contents/{path or cfg.path}"


def blob_url(cfg: ScoreboardConfig, path: str) -> str:
    """GitHub の blob ページ URL を組み立てる（ログイン済みなら別タブで閲覧可）。"""
    return f"https://github.com/{cfg.owner}/{cfg.repo}/blob/{cfg.branch}/{path}"


def get_file_sha(cfg: ScoreboardConfig, path: str) -> str | None:
    """指定パスのファイルが存在すれば blob sha を、無ければ None を返す。"""
    root = _local_root()
    if root is not None:
        local = _local_path(root, path)
        if not local.exists():
            return None
        # blob sha は使われていないので存在マーカーとして bytes hash を返す
        return hashlib.sha1(local.read_bytes()).hexdigest()
    resp = requests.get(
        _content_url(cfg, path),
        headers=_headers(cfg.token),
        params={"ref": cfg.branch},
        timeout=15,
    )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ScoreboardError(f"GET contents failed: {resp.status_code} {resp.text[:200]}")
    payload = resp.json()
    sha = payload.get("sha")
    if not isinstance(sha, str):
        raise ScoreboardError(f"{path} contents response missing sha")
    return sha


def put_file(
    cfg: ScoreboardConfig,
    path: str,
    content_bytes: bytes,
    commit_message: str,
    sha: str | None = None,
) -> dict[str, Any]:
    """任意パスに bytes 内容のファイルを書き込む（sha=None で新規作成）。"""
    root = _local_root()
    if root is not None:
        local = _local_path(root, path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(content_bytes)
        return {"content": {"path": path}, "commit": {"message": commit_message}}
    payload: dict[str, Any] = {
        "message": commit_message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": cfg.branch,
    }
    if sha is not None:
        payload["sha"] = sha
    resp = requests.put(
        _content_url(cfg, path),
        headers=_headers(cfg.token),
        data=json.dumps(payload),
        timeout=20,
    )
    if resp.status_code not in (200, 201):
        raise ScoreboardError(f"PUT contents failed: {resp.status_code} {resp.text[:300]}")
    return resp.json()


def upload_if_absent(
    cfg: ScoreboardConfig,
    path: str,
    content_bytes: bytes,
    commit_message: str,
) -> bool:
    """指定パスが未存在なら bytes を新規アップロード。作成したら True、既存なら False。

    get_file_sha と put_file の間に他者が同じパスを先に作った場合 (TOCTOU)、
    PUT は 422 で失敗する。その場合は再度存在確認して既存扱い (False) にフォールバックする。
    """
    if get_file_sha(cfg, path) is not None:
        return False
    try:
        put_file(cfg, path, content_bytes, commit_message)
    except ScoreboardError:
        if get_file_sha(cfg, path) is not None:
            return False
        raise
    return True


def fetch_history(cfg: ScoreboardConfig) -> tuple[list[dict[str, Any]], str | None]:
    """リーダーボード履歴と、更新時に必要な blob SHA を返す。

    ファイル未存在（404）は空配列・SHA None として扱う（初回投稿で自動作成）。
    """
    root = _local_root()
    if root is not None:
        local = _local_path(root, cfg.path)
        if not local.exists():
            return [], None
        raw = local.read_text(encoding="utf-8")
        history = json.loads(raw) if raw.strip() else []
        if not isinstance(history, list):
            raise ScoreboardError(f"{cfg.path} is not a JSON array")
        return history, "local"
    resp = requests.get(
        _content_url(cfg),
        headers=_headers(cfg.token),
        params={"ref": cfg.branch},
        timeout=15,
    )
    if resp.status_code == 404:
        return [], None
    if resp.status_code != 200:
        raise ScoreboardError(f"GET contents failed: {resp.status_code} {resp.text[:200]}")
    payload = resp.json()
    raw = base64.b64decode(payload["content"]).decode("utf-8")
    history = json.loads(raw) if raw.strip() else []
    if not isinstance(history, list):
        raise ScoreboardError(f"{cfg.path} is not a JSON array")
    return history, payload["sha"]


def append_entry(
    cfg: ScoreboardConfig,
    entry: dict[str, Any],
    commit_message: str,
) -> dict[str, Any]:
    """履歴に 1 エントリを追記してコミットする。

    楽観的ロック: fetch_history で取った sha を PUT に渡す。
    他者が先に更新していた場合 GitHub 側で 409 が返るので呼び出し側でリトライする。
    """
    history, sha = fetch_history(cfg)
    history.append(entry)
    body = json.dumps(history, ensure_ascii=False, indent=2).encode("utf-8")
    root = _local_root()
    if root is not None:
        local = _local_path(root, cfg.path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(body)
        return {"content": {"path": cfg.path}, "commit": {"message": commit_message}}
    payload: dict[str, Any] = {
        "message": commit_message,
        "content": base64.b64encode(body).decode("ascii"),
        "branch": cfg.branch,
    }
    if sha is not None:
        payload["sha"] = sha

    resp = requests.put(
        _content_url(cfg),
        headers=_headers(cfg.token),
        data=json.dumps(payload),
        timeout=20,
    )
    if resp.status_code not in (200, 201):
        raise ScoreboardError(f"PUT contents failed: {resp.status_code} {resp.text[:300]}")
    return resp.json()


def fetch_labels(cfg: ScoreboardConfig, path: str) -> tuple[dict[str, dict[str, Any]], str | None]:
    """items_labels.json (items_sha -> {label, updated_at, author}) と blob SHA を返す。

    未存在は空 dict・SHA None として扱う (初回保存で自動作成)。
    """
    root = _local_root()
    if root is not None:
        local = _local_path(root, path)
        if not local.exists():
            return {}, None
        raw = local.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            raise ScoreboardError(f"{path} is not a JSON object")
        return data, "local"
    resp = requests.get(
        _content_url(cfg, path),
        headers=_headers(cfg.token),
        params={"ref": cfg.branch},
        timeout=15,
    )
    if resp.status_code == 404:
        return {}, None
    if resp.status_code != 200:
        raise ScoreboardError(f"GET contents failed: {resp.status_code} {resp.text[:200]}")
    payload = resp.json()
    raw = base64.b64decode(payload["content"]).decode("utf-8")
    data = json.loads(raw) if raw.strip() else {}
    if not isinstance(data, dict):
        raise ScoreboardError(f"{path} is not a JSON object")
    return data, payload["sha"]


def update_label(
    cfg: ScoreboardConfig,
    path: str,
    items_sha: str,
    label: str,
    *,
    author: str = "",
    commit_message: str = "scoreboard: rename items_input label",
) -> dict[str, Any]:
    """items_labels.json の `items_sha` エントリを上書き保存する。

    空文字 label のときは該当キーを削除する (上書きを解除)。
    """
    from datetime import datetime

    data, sha = fetch_labels(cfg, path)
    if label.strip():
        data[items_sha] = {
            "label": label.strip(),
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "author": author,
        }
    else:
        data.pop(items_sha, None)
    body = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    root = _local_root()
    if root is not None:
        local = _local_path(root, path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(body)
        return {"content": {"path": path}, "commit": {"message": commit_message}}
    payload: dict[str, Any] = {
        "message": commit_message,
        "content": base64.b64encode(body).decode("ascii"),
        "branch": cfg.branch,
    }
    if sha is not None:
        payload["sha"] = sha
    resp = requests.put(
        _content_url(cfg, path),
        headers=_headers(cfg.token),
        data=json.dumps(payload),
        timeout=20,
    )
    if resp.status_code not in (200, 201):
        raise ScoreboardError(f"PUT contents failed: {resp.status_code} {resp.text[:300]}")
    return resp.json()


def update_entry(
    cfg: ScoreboardConfig,
    entry_id: str,
    patch: dict[str, Any],
    commit_message: str,
) -> dict[str, Any]:
    """指定 id のエントリに patch をマージして履歴を更新する。

    append_entry と同じく楽観的ロック (fetch 時の sha を PUT に同梱)。
    該当 id が見つからなければ ScoreboardError を投げる。
    """
    history, sha = fetch_history(cfg)
    target_index: int | None = None
    for i, e in enumerate(history):
        if e.get("id") == entry_id:
            target_index = i
            break
    if target_index is None:
        raise ScoreboardError(f"entry_id={entry_id} not found in history")
    history[target_index] = {**history[target_index], **patch}
    body = json.dumps(history, ensure_ascii=False, indent=2).encode("utf-8")
    root = _local_root()
    if root is not None:
        local = _local_path(root, cfg.path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(body)
        return {"content": {"path": cfg.path}, "commit": {"message": commit_message}}
    payload: dict[str, Any] = {
        "message": commit_message,
        "content": base64.b64encode(body).decode("ascii"),
        "branch": cfg.branch,
    }
    if sha is not None:
        payload["sha"] = sha

    resp = requests.put(
        _content_url(cfg),
        headers=_headers(cfg.token),
        data=json.dumps(payload),
        timeout=20,
    )
    if resp.status_code not in (200, 201):
        raise ScoreboardError(f"PUT contents failed: {resp.status_code} {resp.text[:300]}")
    return resp.json()
