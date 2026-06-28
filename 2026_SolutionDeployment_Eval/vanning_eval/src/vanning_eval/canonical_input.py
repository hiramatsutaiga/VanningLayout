"""Canonical input identity & integrity gate.

リーダーボードの「同じ問題インスタンス」判定を、申告 `dataset_info`
ではなく **items の実内容ハッシュ** に基づかせ、さらに登録済みの
canonical input（ground truth）に対して再検証するための純粋関数群。

背景: 旧 `streamlit_app._items_group_key` は `dataset_info`
（dataset_name+seed+item_count）のみを SHA-256 していたため、軽い別
items を `case_01 seed=42` と*申告*するだけで同一グループに混入し、
少コンテナで不正に上位を取れる穴があった（本モジュールで是正）。

すべて副作用なし・入力は dict / Path（CLAUDE.md「純粋関数」準拠）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .report import build_report
from .schema import SchemaError, load_items, load_layout

# layout↔items の ID 不整合を示す違反コード（constraints.ViolationCode と一致）。
# canonical items に対し build_report したとき、提出 layout が別 input で
# 解かれていればこれらが必ず立つ。
_ITEM_MISMATCH_CODES = frozenset(
    {"MISSING_ITEMS", "UNKNOWN_ITEMS", "DUPLICATE_ITEMS", "ITEM_ATTR_MISMATCH"}
)


def canonical_json_sha256(content_bytes: bytes) -> str:
    """JSON として正規化（sort_keys, separators）してから SHA-256。

    生バイト列の hash は indent/改行/CRLF/BOM/キー順で別 SHA になるため、
    parse → canonical dump してから hash する。JSON 不正は生バイト hash に
    フォールバック。`streamlit_app._canonical_json_sha256` と同一仕様
    （重複ロジックを本モジュールへ集約し、両所から参照する）。
    """
    try:
        obj = json.loads(content_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return hashlib.sha256(content_bytes).hexdigest()
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return hashlib.sha256(canonical).hexdigest()


def _item_tuple(raw: dict[str, Any]) -> tuple[str, str, int, int, int, float, str]:
    """1 item dict → 正規化タプル。

    dimensions のネスト・int/float 揺れ・weight の再生成揺れ（1g 粒度で
    round）を吸収。dataset_info は **意図的に含めない**（申告は信用しない）。
    """
    dims = raw.get("dimensions") or {}
    return (
        str(raw.get("item_id", "")),
        str(raw.get("size_type", "")),
        int(round(float(dims.get("w", 0)))),
        int(round(float(dims.get("l", 0)))),
        int(round(float(dims.get("h", 0)))),
        round(float(raw.get("weight", 0.0)), 3),
        str(raw.get("destination_id", "")),
    )


def normalize_items_payload(obj: dict[str, Any]) -> str:
    """items_input dict → 順序非依存・dataset_info 非依存の内容ハッシュ。

    items を正規化タプル化 → item_id 昇順 sort → canonical JSON →
    SHA-256。items の並び替えや weight の <1g 揺れでは不変、items の
    実体（重量分布・寸法・目的地）が違えば必ず別ハッシュになる。
    """
    items = obj.get("items")
    if not isinstance(items, list):
        items = []
    tuples = sorted(
        (_item_tuple(it) for it in items if isinstance(it, dict)),
        key=lambda t: t[0],
    )
    payload = json.dumps(tuples, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return "items:" + hashlib.sha256(payload).hexdigest()


def content_group_key(content_bytes: bytes) -> str:
    """items_input.json の bytes → 内容ベースのグループキー。

    JSON parse 可能かつ dict なら `normalize_items_payload`、不正なら
    `canonical_json_sha256` にフォールバック（=「壊れた入力」は別グループ）。
    """
    try:
        obj = json.loads(content_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return canonical_json_sha256(content_bytes)
    if not isinstance(obj, dict):
        return canonical_json_sha256(content_bytes)
    return normalize_items_payload(obj)


@dataclass(frozen=True)
class CanonicalDataset:
    """ground truth として登録された 1 つの公式問題入力。"""

    dataset_id: str
    content_hash: str
    items_input_path: Path
    dataset_info: dict[str, Any]


def load_canonical_registry(input_root: Path) -> dict[str, CanonicalDataset]:
    """`input_root/official_*/items_input.json` を走査して registry を作る。

    返り値は `content_hash -> CanonicalDataset`。これがランキングの
    ground truth。`official_` プレフィックスのフォルダのみ対象（他の
    `input/*` は非正規）。読めない / 壊れたファイルは黙ってスキップ。
    """
    registry: dict[str, CanonicalDataset] = {}
    if not input_root.is_dir():
        return registry
    for d in sorted(input_root.glob("official_*")):
        f = d / "items_input.json"
        if not f.is_file():
            continue
        try:
            content = f.read_bytes()
            obj = json.loads(content.decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        chash = normalize_items_payload(obj)
        registry[chash] = CanonicalDataset(
            dataset_id=d.name,
            content_hash=chash,
            items_input_path=f,
            dataset_info=obj.get("dataset_info") or {},
        )
    return registry


def resolve_canonical(
    content_bytes: bytes, registry: dict[str, CanonicalDataset]
) -> CanonicalDataset | None:
    """提出 items_input bytes が registry のエントリと内容一致すれば返す。

    一致は **内容ハッシュ完全一致のみ**（dataset_info 申告は一切見ない）。
    registry は `input/official_*` を ground truth として読み込むだけの索引で、
    一致しない input でも整合性 OK ならそのまま受理する（公式・非公式の区別なし）。
    """
    return registry.get(content_group_key(content_bytes))


def score_from_report(report: dict[str, Any]) -> dict[str, Any]:
    """report → リーダーボード score dict（投稿時と canonical 再採点で
    同一スキーマを保証する単一の抽出点）。streamlit に依存しないため
    マイグレーションスクリプトからも再利用する。
    """
    teacher = report["teacher_score_metrics"]
    internal = report["internal_metrics"]
    return {
        "containers_used": teacher["containers_used"],
        "average_fill_rate": teacher["average_fill_rate"],
        "violation_count": len(report["disqualifications"]),
        "cog_y_mean_deviation": internal["cog_y_stats"]["mean_deviation"],
        "execution_time_ms": internal["execution_time_ms"],
        "fill_rate_per_container": teacher.get("fill_rate_per_container", []),
        "cog_dev_per_container": teacher.get("cog_dev_per_container", []),
    }


@dataclass(frozen=True)
class GateResult:
    """1 提出に対する整合ゲートの判定。"""

    status: str  # ok | no_items_input | non_canonical | item_mismatch | schema_error
    canonical: CanonicalDataset | None
    rescored_report: dict[str, Any] | None
    reason: str


def gate_entry_against_canonical(
    layout_path: Path,
    submitted_items_path: Path | None,
    registry: dict[str, CanonicalDataset],
) -> GateResult:
    """提出 layout を items_input で再検証してゲート判定する。

    すべての input.json は平等で「公式 / 非公式」の区別はしない。
    registry は単に過去に登録された items の索引で、内容一致したエントリは
    そちらのファイルを ground truth として使う（書式揺れ吸収目的）。

    1. items_input 未提出 → ``no_items_input``（非ランキング）
    2. 検証 input を決める:
       - 提出 items が registry のエントリと内容一致 → registry 側のファイル
       - 一致しない → 提出 items 自体
    3. ``build_report(layout, 検証 input)`` を実行
    4. report に MISSING/UNKNOWN/DUPLICATE_ITEMS → ``item_mismatch``
       （= layout の item 集合が input と不一致＝別 input で解いた layout）
    5. それ以外 → 内部 status は ``ok`` / ``non_canonical`` で区別するが
       UI 上は等価扱い。どちらも ``rescored_report`` を返す。

    layout/items が読めない等は ``schema_error``。
    """
    if submitted_items_path is None:
        return GateResult("no_items_input", None, None, "items_input 未提出")
    try:
        submitted_bytes = Path(submitted_items_path).read_bytes()
    except OSError as exc:
        return GateResult("schema_error", None, None, f"items 読込失敗: {exc}")

    canon = resolve_canonical(submitted_bytes, registry)

    try:
        layout = load_layout(layout_path)
        validation_items_path = canon.items_input_path if canon else Path(submitted_items_path)
        validation_items = load_items(validation_items_path)
        report = build_report(layout, validation_items)
    except (OSError, SchemaError, ValueError, KeyError) as exc:
        return GateResult("schema_error", canon, None, f"再検証失敗: {exc}")

    mismatch = [
        d for d in report.get("disqualifications", []) if str(d.get("code")) in _ITEM_MISMATCH_CODES
    ]
    if mismatch:
        codes = ",".join(sorted({str(d.get("code")) for d in mismatch}))
        return GateResult(
            "item_mismatch",
            canon,
            report,
            f"layout の item 集合が items_input と不一致 ({codes})",
        )
    if canon is None:
        return GateResult(
            "non_canonical",
            None,
            report,
            f"items_input 整合 OK（registry 未登録）verdict={report.get('verdict')}",
        )
    return GateResult(
        "ok",
        canon,
        report,
        f"items_input 整合 OK（registry={canon.dataset_id}）verdict={report.get('verdict')}",
    )
