"""提出ファイルのペア解決と batch glob 展開。

`layout_result.json` と `items_input.json` は通常 paired で同じディレクトリに置かれる。
batch 提出で「フォルダパスを渡されたら配下を再帰 glob する」「単発提出で items_path 省略
なら同ディレクトリから自動探索する」のロジックをここに集約。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SubmissionPair:
    """1 提出単位の (layout, items?) ペア。items は任意。"""

    layout: Path
    items: Path | None


def resolve_pair(layout_path: Path, items_path: Path | None = None) -> SubmissionPair:
    """layout_path から paired items を解決する。

    優先順:
        1. items_path が明示されていればそれ
        2. layout と同ディレクトリの `items_input.json`
        3. layout のファイル名が `layout_*.json` なら `items_*.json` リネーム
        4. 見つからなければ None
    """
    layout_path = Path(layout_path)
    if items_path is not None:
        return SubmissionPair(layout=layout_path, items=Path(items_path))
    parent = layout_path.parent
    candidate = parent / "items_input.json"
    if candidate.is_file():
        return SubmissionPair(layout=layout_path, items=candidate)
    name = layout_path.name
    if name.startswith("layout_") and name.endswith(".json"):
        cand = parent / name.replace("layout_", "items_", 1)
        if cand.is_file():
            return SubmissionPair(layout=layout_path, items=cand)
    return SubmissionPair(layout=layout_path, items=None)


def expand_paths(
    paths: list[str | Path],
    *,
    glob_pattern: str = "layout_result.json",
) -> list[Path]:
    """ファイル/ディレクトリ混在のリストを layout 候補ファイルの list に展開。

    - ファイルパス: そのまま追加（拡張子 .json のみ）
    - ディレクトリ: 配下を `**/{glob_pattern}` で再帰 glob
    - 重複は順序を保って排除
    """
    out: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            if p.suffix != ".json":
                continue
            resolved = p.resolve()
            if resolved not in seen:
                seen.add(resolved)
                out.append(resolved)
        elif p.is_dir():
            for hit in sorted(p.glob(f"**/{glob_pattern}")):
                if not hit.is_file():
                    continue
                resolved = hit.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    out.append(resolved)
        else:
            raise FileNotFoundError(f"path not found: {p}")
    return out
