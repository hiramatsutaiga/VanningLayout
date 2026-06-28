"""vanning-eval-mcp の設定読込（env 主、secrets.toml フォールバック）。

チーム配布時の主経路は env：`.mcp.json` の env フィールドに PAT・owner・repo 等を書く。
UI と同居している人向けに `.streamlit/secrets.toml` も後段フォールバックとして読む。

env キー:
    VANNING_GITHUB_TOKEN    Fine-grained PAT (Contents: Read and write) — required
    VANNING_GITHUB_OWNER    GitHub owner/org                            — required
    VANNING_GITHUB_REPO     scoreboard repo name                        — required
    VANNING_SCOREBOARD_PATH default "scoreboard/history.json"
    VANNING_SCOREBOARD_BRANCH default "main"
    VANNING_INPUT_ROOT      `<vanning_eval_rui>/input` の絶対パス      — canonical registry 用
    VANNING_REPO_ROOT       `<vanning-eval>` の絶対パス                — apply_canonical_gate 用
    VANNING_DEFAULT_AUTHOR  leaderboard 投稿時の author 既定値          — submit 系 tool で必須
                            (tool 呼出で author 引数があればそちらが優先)
    VANNING_SECRETS_TOML    フォールバックする .streamlit/secrets.toml のパス
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vanning_viewer.scoreboard_client import ScoreboardConfig


@dataclass(frozen=True)
class McpConfig:
    """MCP サーバーが起動時に確定する設定一式。"""

    scoreboard: ScoreboardConfig
    input_root: Path
    repo_root: Path
    default_author: str  # 投稿者既定値（空文字なら未設定 = submit 系 tool で要明示）


class ConfigError(RuntimeError):
    """設定が不足/不正なときに投げる。"""


def _env(key: str) -> str | None:
    v = os.environ.get(key)
    return v.strip() if v and v.strip() else None


def _load_secrets_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def _from_secrets(secrets: dict[str, Any], key: str) -> str | None:
    v = secrets.get(key)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def _resolve_secrets_path() -> Path | None:
    override = _env("VANNING_SECRETS_TOML")
    if override:
        return Path(override)
    repo_root = _env("VANNING_REPO_ROOT")
    if repo_root:
        candidate = Path(repo_root) / "vanning_eval_rui" / ".streamlit" / "secrets.toml"
        if candidate.is_file():
            return candidate
    return None


def load_config() -> McpConfig:
    """env → secrets.toml の順に解決して `McpConfig` を組む。失敗時は ConfigError。"""
    secrets_path = _resolve_secrets_path()
    secrets = _load_secrets_toml(secrets_path) if secrets_path else {}

    def pick(env_key: str, secrets_key: str) -> str | None:
        return _env(env_key) or _from_secrets(secrets, secrets_key)

    token = pick("VANNING_GITHUB_TOKEN", "GITHUB_TOKEN")
    owner = pick("VANNING_GITHUB_OWNER", "GITHUB_OWNER")
    repo = pick("VANNING_GITHUB_REPO", "GITHUB_REPO")
    path = (
        pick("VANNING_SCOREBOARD_PATH", "SCOREBOARD_PATH") or "scoreboard/history.json"
    )
    branch = pick("VANNING_SCOREBOARD_BRANCH", "SCOREBOARD_BRANCH") or "main"

    missing: list[str] = []
    if not token:
        missing.append("VANNING_GITHUB_TOKEN (or secrets.toml: GITHUB_TOKEN)")
    if not owner:
        missing.append("VANNING_GITHUB_OWNER (or secrets.toml: GITHUB_OWNER)")
    if not repo:
        missing.append("VANNING_GITHUB_REPO (or secrets.toml: GITHUB_REPO)")
    if missing:
        raise ConfigError("vanning-eval-mcp: 設定不足: " + ", ".join(missing))
    assert token and owner and repo  # for type checker

    repo_root_str = _env("VANNING_REPO_ROOT")
    if not repo_root_str:
        raise ConfigError(
            "vanning-eval-mcp: VANNING_REPO_ROOT が必要です "
            "(vanning-eval リポジトリのルート絶対パス)"
        )
    repo_root = Path(repo_root_str).resolve()
    if not repo_root.is_dir():
        raise ConfigError(f"VANNING_REPO_ROOT が存在しません: {repo_root}")

    input_root_str = _env("VANNING_INPUT_ROOT")
    input_root = (
        Path(input_root_str).resolve()
        if input_root_str
        else repo_root / "vanning_eval_rui" / "input"
    )
    if not input_root.is_dir():
        raise ConfigError(
            f"VANNING_INPUT_ROOT が存在しません: {input_root} "
            "(canonical registry を読むため必要)"
        )

    # default_author は env のみ参照（secrets.toml の DEFAULT_AUTHOR は UI 専用設定で
    # MCP 経路と意味が違うため意図的に流用しない。MCP は .mcp.json の env で明示）。
    default_author = _env("VANNING_DEFAULT_AUTHOR") or ""

    scoreboard = ScoreboardConfig(
        owner=owner, repo=repo, path=path, branch=branch, token=token
    )
    return McpConfig(
        scoreboard=scoreboard,
        input_root=input_root,
        repo_root=repo_root,
        default_author=default_author,
    )
