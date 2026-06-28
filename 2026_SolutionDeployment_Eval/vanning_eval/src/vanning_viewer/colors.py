"""ViewModel 用のカラーパレット・透明度ルール。

destination_id ごとに色を安定割当し、違反アイテムは赤で上書きする。
size_type で opacity を段階付けて立体感を出す。
"""

from __future__ import annotations

from collections.abc import Iterable

VIOLATION_COLOR = "#FF1E64"
CONTAINER_FRAME_COLOR = "#666666"
FALLBACK_COLOR = "#888888"

# 色覚配慮寄りのパレット。destination が増えたらラウンドロビンで再利用される。
DEST_PALETTE: tuple[str, ...] = (
    "#4C78A8",  # 青
    "#F58518",  # オレンジ
    "#54A24B",  # 緑
    "#72B7B2",  # ティール
    "#EECA3B",  # 黄
    "#B279A2",  # 紫
    "#9D755D",  # 茶
    "#FF9DA6",  # 薄ピンク
)

SIZE_OPACITY: dict[str, float] = {
    "large": 0.65,
    "medium": 0.80,
    "small": 0.92,
}

# 違反コード → 日本語表示ラベル。hover テキストで英語コードの代わりに使う。
VIOLATION_LABELS_JA: dict[str, str] = {
    "OUT_OF_BOUNDS": "枠外はみ出し",
    "OVERLAP": "重なり",
    "FLOATING": "浮遊・支持不足",
    "DESTINATION_MIX": "配送先不一致",
    "WEIGHT_OVER": "重量超過",
    "WEIGHT_DECLARATION_MISMATCH": "重量申告不一致",
    "COG_VIOLATION": "重心違反 (±3000mm 超)",
    "MISSING_ITEMS": "未配置",
    "UNKNOWN_ITEMS": "想定外アイテム",
    "DUPLICATE_ITEMS": "重複配置",
}


def ja_violation_label(code: str) -> str:
    """違反コードを日本語ラベルに変換。未知コードは英語コードのまま返す。"""
    return VIOLATION_LABELS_JA.get(code, code)


def build_destination_color_map(destination_ids: Iterable[str]) -> dict[str, str]:
    """登場順に destination_id → 色 を安定マッピング。重複は先勝ち。"""
    seen: list[str] = []
    for d in destination_ids:
        if d not in seen:
            seen.append(d)
    return {d: DEST_PALETTE[i % len(DEST_PALETTE)] for i, d in enumerate(seen)}


def opacity_for_size(size_type: str) -> float:
    return SIZE_OPACITY.get(size_type, 0.80)
