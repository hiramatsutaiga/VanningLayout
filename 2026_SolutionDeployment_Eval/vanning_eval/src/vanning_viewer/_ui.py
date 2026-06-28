"""Streamlit 向け HTML/CSS 注入ヘルパー。

コックピット風デザインシステム (Claude Design 納品) を Streamlit 画面に被せる。
- inject_css: tokens.css + streamlit_skin.css をまとめて `<style>` で注入
- render_header: 上部ネームプレート
- render_hud_metrics: 判定タイル＋4 KPI タイル
- render_violation_groups: 違反コードごとの vgroup ドリルダウン
- render_pass_empty_state: 違反なしの空状態
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import streamlit as st

_STATIC_DIR = Path(__file__).resolve().parent / "static"

_FONTS_HREF = (
    "https://fonts.googleapis.com/css2?"
    "family=Space+Grotesk:wght@400;500;600;700;800&"
    "family=IBM+Plex+Sans+JP:wght@300;400;500;600;700&"
    "family=JetBrains+Mono:wght@400;500;700&display=swap"
)


@st.cache_resource
def _read_css(name: str) -> str:
    path = _STATIC_DIR / name
    return path.read_text(encoding="utf-8")


def inject_css() -> None:
    """tokens.css + streamlit_skin.css を Streamlit に注入する。

    1ページ 1回で十分。`st.set_page_config` の直後に呼ぶこと。

    Streamlit 1.56 は `st.markdown(unsafe_allow_html=True)` も `st.html()` も
    `<style>` / `<link>` をサニタイザで除去する（2026-04-24 実測済み）。
    回避策として `st.iframe` で iframe を作り、その中の JS から
    `window.parent.document.head` にスタイル要素と Google Fonts link を追加する
    （同一オリジンなので親 DOM にアクセス可能）。
    旧 `components.v1.html` は 2026-06-01 で削除予定のため `st.iframe` に移行。
    """
    tokens = _read_css("tokens.css")
    # tokens.css の先頭 @import（Google Fonts）は link タグで別途差し込むので除去
    lines = tokens.splitlines()
    tokens_stripped = "\n".join(ln for ln in lines if not ln.strip().startswith("@import"))
    skin = _read_css("streamlit_skin.css")
    css_payload = f"{tokens_stripped}\n{skin}"
    # JS テンプレートリテラル内に CSS をそのまま埋めるため、`、$、\ をエスケープ
    css_js_safe = css_payload.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    st.iframe(
        f"""
<script>
(function() {{
  const doc = window.parent.document;
  if (doc.getElementById('vn-injected-style')) return;
  // Google Fonts
  const link = doc.createElement('link');
  link.id = 'vn-injected-fonts';
  link.rel = 'stylesheet';
  link.href = {_FONTS_HREF!r};
  doc.head.appendChild(link);
  // Tokens + skin
  const style = doc.createElement('style');
  style.id = 'vn-injected-style';
  style.textContent = `{css_js_safe}`;
  doc.head.appendChild(style);
}})();
</script>
""",
        height=1,
    )


def render_header(
    *,
    title: str = "Vanning Evaluator",
    input_filename: str = "",
) -> None:
    """ネームプレート（タイトル + 入力ファイル名）を描画。"""
    filename = html.escape(input_filename) if input_filename else "—"
    st.markdown(
        f"""
<div class="vn-nameplate">
  <div class="vn-nameplate__mark"></div>
  <div class="vn-nameplate__title">{html.escape(title)}</div>
  <div class="vn-nameplate__sep">//</div>
  <span class="vn-nameplate__sub"><b>LOT</b> {filename}</span>
</div>
""",
        unsafe_allow_html=True,
    )


def _verdict_tile(verdict: str) -> str:
    """判定の verdict プレート (HUD tile の一種として 1 列ぶん描画する)。"""
    is_pass = verdict == "pass"
    mod = "" if is_pass else "vn-verdict--fail"
    label = "合格" if is_pass else "失格"
    return f"""
<div class="vn-verdict {mod}">
  <div class="vn-verdict__bar"></div>
  <div class="vn-verdict__body">
    <div class="vn-verdict__label">判定</div>
    <div class="vn-verdict__value">{label}</div>
  </div>
</div>
"""


def _kpi_tile(
    label: str,
    value: str,
    *,
    unit: str = "",
    sub: str = "",
    tone: str = "default",
) -> str:
    """KPI タイル。tone: default / pass / fail"""
    tile_mod = ""
    value_mod = ""
    if tone == "pass":
        tile_mod = "vn-hud--pass"
        value_mod = "is-pass"
    elif tone == "fail":
        tile_mod = "vn-hud--fail"
        value_mod = "is-fail"
    unit_html = f'<span class="unit">{html.escape(unit)}</span>' if unit else ""
    sub_html = f'<div class="vn-hud__sub">{html.escape(sub)}</div>' if sub else ""
    return f"""
<div class="vn-hud {tile_mod}">
  <div class="vn-hud__label">{html.escape(label)}</div>
  <div class="vn-hud__value {value_mod}">{html.escape(value)}{unit_html}</div>
  {sub_html}
</div>
"""


def render_hud_metrics(
    *,
    verdict: str,
    container_count: int,
    item_count: int,
    violation_count: int,
    average_fill_rate: float,
) -> None:
    """判定 + 4 KPI の HUD タイル行を描画する。"""
    viol_tone = "fail" if violation_count > 0 else "pass"
    tiles = [
        _verdict_tile(verdict),
        _kpi_tile("コンテナ数", str(container_count), unit="個"),
        _kpi_tile("アイテム数", str(item_count), unit="点"),
        _kpi_tile("違反件数", str(violation_count), unit="件", tone=viol_tone),
        _kpi_tile("平均充填率", f"{average_fill_rate * 100:.1f}", unit="%"),
    ]
    st.markdown(
        f'<div class="vn-hud-row">{"".join(tiles)}</div>',
        unsafe_allow_html=True,
    )


def render_pass_empty_state(message: str = "制約違反は検出されませんでした。") -> None:
    """違反ゼロ時の空状態。"""
    st.markdown(
        f"""
<div class="vn-empty">
  <div class="vn-empty__ico">✓</div>
  <div class="vn-empty__msg">{html.escape(message)}</div>
</div>
""",
        unsafe_allow_html=True,
    )


def render_violation_groups(
    violations: Iterable[Any],
    *,
    ja_label: Any,
) -> None:
    """違反コードごとに vgroup を描画する。

    violations: Violation dataclass のイテラブル (code / container_id / items / detail を持つ)。
    ja_label: code -> 日本語ラベル の callable。
    """
    # コード単位にグルーピング（登場順を保持）
    groups: dict[str, list[Any]] = {}
    for v in violations:
        groups.setdefault(v.code, []).append(v)

    parts: list[str] = []
    for code, vs in groups.items():
        label = html.escape(ja_label(code))
        code_esc = html.escape(code)
        rows: list[str] = []
        for v in vs:
            container = html.escape(str(v.container_id)) if v.container_id is not None else "—"
            items = html.escape(", ".join(v.items)) if v.items else "—"
            detail_str = json.dumps(v.detail, ensure_ascii=False) if v.detail else ""
            detail_esc = html.escape(detail_str)
            rows.append(
                f"""
<tr>
  <td>{container}</td>
  <td>{items}</td>
  <td class="detail">{detail_esc}</td>
</tr>
"""
            )
        parts.append(
            f"""
<div class="vn-vgroup">
  <div class="vn-vgroup__hd">
    <div class="vn-vgroup__icon">!</div>
    <div class="vn-vgroup__title">
      {label}
      <small>{code_esc}</small>
    </div>
    <div class="vn-vgroup__count">{len(vs)}</div>
  </div>
  <div class="vn-vgroup__body">
    <table class="vn-vtable">
      <thead>
        <tr>
          <th scope="col" style="width: 120px;">コンテナ</th>
          <th scope="col" style="width: 30%;">対象アイテム</th>
          <th scope="col">詳細</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows)}
      </tbody>
    </table>
  </div>
</div>
"""
        )
    st.html("".join(parts))
