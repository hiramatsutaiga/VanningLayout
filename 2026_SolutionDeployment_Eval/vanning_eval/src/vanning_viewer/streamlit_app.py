"""Streamlit WebUI for vanning_viewer.

localhost:8501 で評価と 3D 可視化を統合した UI を提供する。

起動:
    streamlit run src/vanning_viewer/streamlit_app.py

構成:
    - サイドバー: input/ 配下のサンプル or scoreboard/ の過去投稿を選択、
      または layout_result.json / items_input.json を手動アップロード
    - メトリクス行: 判定 / コンテナ数 / 違反数 / 平均充填率
    - タブ: 3Dビュー / 違反詳細 / 評価レポート / リーダーボード / JSON 生データ

採点方式 (要件定義書 PR #26 で改訂):
    重み付き総合スコア式は廃止し、辞書式順位付けを採用する。
    1. コンテナ数 (少ないほど上位)
    2. 重心ズレ平均 (小さいほど上位)
    3. 処理時間 (短いほど上位)
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from vanning_eval.canonical_input import (
    CanonicalDataset,
    canonical_json_sha256,
    content_group_key,
    load_canonical_registry,
)
from vanning_eval.constraints import Violation, run_all_checks
from vanning_eval.report import build_report
from vanning_eval.schema import load_items, load_layout
from vanning_eval.scoring import compute_teacher_metrics

# streamlit run はこのファイルをスクリプトとして直接実行するため、絶対 import を使う
# （vanning_eval / vanning_viewer は pip install -e で登録済み）
from vanning_viewer._ui import (
    inject_css,
    render_header,
    render_hud_metrics,
    render_pass_empty_state,
    render_violation_groups,
)
from vanning_viewer.colors import ja_violation_label
from vanning_viewer.plotly_renderer import render_scene, to_html
from vanning_viewer.scoreboard_client import (
    ScoreboardConfig,
    ScoreboardError,
    append_entry,
    blob_url,
    fetch_history,
    fetch_labels,
    update_entry,
    update_label,
)

# テストから `from vanning_viewer.streamlit_app import _rank_sort_key` 等で参照されるため、
# 本ファイル内で使わない alias も保持（noqa: F401 で ruff の未使用警告を抑制）。
from vanning_viewer.submitter import (
    JST,
    RECOMMENDED_FILL_RATE,  # noqa: F401
)
from vanning_viewer.submitter import (
    apply_canonical_gate as _apply_canonical_gate_impl,
)
from vanning_viewer.submitter import (
    build_scoreboard_entry as _build_scoreboard_entry,
)
from vanning_viewer.submitter import (
    count_above_fill_threshold as _count_above_fill_threshold,  # noqa: F401
)
from vanning_viewer.submitter import (
    rank_entries as _rank_entries,  # noqa: F401
)
from vanning_viewer.submitter import (
    rank_sort_key as _rank_sort_key,  # noqa: F401
)
from vanning_viewer.submitter import (
    upload_input_file as _upload_input_file_impl,
)
from vanning_viewer.view_model import build_scene

# streamlit_app.py → src/vanning_viewer/ の 2 つ上が vanning_eval_rui/、3 つ上が vanning-eval/ リポルート
# （cwd に依存せず input/ や scoreboard/ を解決するため、起動方法が変わっても壊れないようにする）
RUI_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]
INPUT_DIR = RUI_ROOT / "input"
SCOREBOARD_DIR = REPO_ROOT / "scoreboard"

# items_input ラベル上書きファイル (scoreboard リポ内、誰でも編集可)。
# scoreboard/history.json と同じディレクトリに置く。
_LABELS_FILENAME = "items_labels.json"


def _labels_path(cfg: ScoreboardConfig) -> str:
    """history.json と同じディレクトリの items_labels.json パスを返す。"""
    base = cfg.path.rsplit("/", 1)[0] if "/" in cfg.path else ""
    return f"{base}/{_LABELS_FILENAME}" if base else _LABELS_FILENAME

# canonical registry はプロセス内で 1 度だけ走査（streamlit スクリプトは
# 再実行で関数が再評価されるためモジュール変数でキャッシュ）。input/official_*
# を ground truth として読む。
_REGISTRY_CACHE: dict[str, CanonicalDataset] | None = None


def _load_registry() -> dict[str, CanonicalDataset]:
    """input/official_* の canonical registry（content_hash -> dataset）。"""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        _REGISTRY_CACHE = load_canonical_registry(INPUT_DIR)
    return _REGISTRY_CACHE


def _save_uploaded(uploaded) -> Path:
    """Streamlit UploadedFile → 一時ファイルのパス。"""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".json", delete=False) as f:
        f.write(uploaded.getvalue())
        return Path(f.name)


def _load_scoreboard_config() -> ScoreboardConfig | None:
    """Streamlit secrets からリーダーボード設定を読む。未設定なら None を返す。

    secrets.toml が存在しない / キーが欠けている場合は設定なしとして扱う。
    """
    required = ("GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO", "SCOREBOARD_PATH")
    try:
        for k in required:
            if k not in st.secrets:
                return None
        return ScoreboardConfig(
            owner=st.secrets["GITHUB_OWNER"],
            repo=st.secrets["GITHUB_REPO"],
            path=st.secrets["SCOREBOARD_PATH"],
            branch=st.secrets.get("SCOREBOARD_BRANCH", "main"),
            token=st.secrets["GITHUB_TOKEN"],
        )
    except Exception:  # noqa: BLE001 - secrets 読み取り失敗はすべて「未設定」扱い
        return None


def _canonical_json_sha256(content_bytes: bytes) -> str:
    """JSON として正規化したうえで SHA-256 を返す。

    生バイト列の hash は indent/改行/CRLF/BOM/キー順で別 SHA になり、
    「同じ items_input なのに別グループ扱い」のバグを引き起こすため、
    parse → canonical dump（sort_keys, separators(',',':')）してから hash する。
    JSON として読めない場合は生バイト列の hash にフォールバック。
    """
    # ロジックは vanning_eval.canonical_input に集約（両所同一仕様を担保）。
    return canonical_json_sha256(content_bytes)


def _items_group_key(content_bytes: bytes) -> str:
    """items_input.json の **items 実内容** から「同じ問題インスタンス」キーを作る。

    旧実装は `dataset_info`（name+seed+item_count）のみを hash していたため、
    軽い別 items を `case_01 seed=42` と*申告*するだけで同一グループに混入し
    少コンテナで不正に上位を取れる穴があった。本実装は items の
    (item_id/寸法/weight/目的地/size_type) を順序非依存・dataset_info 非依存に
    正規化してハッシュする（`canonical_input.content_group_key`）。
    """
    return content_group_key(content_bytes)


def _upload_input_file(
    cfg: ScoreboardConfig,
    *,
    content_bytes: bytes,
    original_name: str,
    role: str = "",
) -> dict[str, Any]:
    """submitter.upload_input_file の Streamlit 側ラッパ（registry を `_load_registry()` から注入）。"""
    return _upload_input_file_impl(
        cfg,
        content_bytes=content_bytes,
        original_name=original_name,
        registry=_load_registry(),
        role=role,
    )


def _items_sha(entry: dict[str, Any]) -> str:
    """エントリの items_input グループキーを返す。

    新エントリは `group_key` (dataset_info hash) を、旧エントリは `sha256` を返す。
    未アップロードは空文字。
    """
    files = entry.get("files") or {}
    items = files.get("items_input") or {}
    # content_hash（新・items 実内容）→ group_key（移行後は同値）→ sha256（旧）。
    key = items.get("content_hash") or items.get("group_key") or items.get("sha256")
    return str(key) if key else ""


# items_input フィルタ selectbox のセンチネル
_ITEMS_FILTER_ALL = "__all__"
_ITEMS_FILTER_NONE = "__no_items__"
_ITEMS_FILTER_HIDDEN = "__hidden__"


def _resolve_items_label(
    sample_entry: dict[str, Any],
    *,
    items_sha: str,
    label_overrides: dict[str, dict[str, Any]] | None = None,
) -> str:
    """items_sha グループの表示ラベルを解決する (override 最優先)。

    優先順:
        1. items_labels.json の override
        2. items_input.json 内の dataset_info.label (人手命名)
        3. source_dir (MCP 提出元の親フォルダ名)
        4. dataset_info.dataset_name + seed (自動命名)
        5. original_name (final fallback)
    """
    if label_overrides:
        ov = label_overrides.get(items_sha)
        if isinstance(ov, dict) and str(ov.get("label", "")).strip():
            return str(ov["label"]).strip()
    files = sample_entry.get("files") or {}
    items_meta = files.get("items_input") or {}
    original = str(items_meta.get("original_name", "items_input.json"))
    di = (
        items_meta.get("dataset_info") if isinstance(items_meta.get("dataset_info"), dict) else None
    )
    di_label = str(di.get("label") or "").strip() if di else ""
    source_dir = str(items_meta.get("source_dir") or "").strip()
    dataset_label = ""
    if di and di.get("dataset_name"):
        seed = di.get("seed")
        dataset_label = f"{di['dataset_name']}" + (f" seed={seed}" if seed is not None else "")
    return di_label or source_dir or dataset_label or original


def _filter_by_items_input(
    history: list[dict[str, Any]],
    current_items_bytes: bytes | None,
    *,
    label_overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """selectbox で items_input を選ばせ、(絞り込み結果, 非表示ビュー判定) を返す。

    - 公開エントリのみを items_input でグルーピング（非表示分は専用オプションに集約）
    - 末尾に「非表示 — N件」を追加し、選択時は hidden=True のエントリだけを返す
    - 戻り値の bool が True のとき、呼び出し側は復元 UI を出すなど分岐する
    """
    visible = [e for e in history if not e.get("hidden")]
    hidden = [e for e in history if e.get("hidden")]

    # 公開エントリだけを items_sha でグルーピング
    # 代表エントリは「label/source_dir を持つ entry」を優先（後発の改名済 entry が
    # 既存 entry より弱い情報源にならないようにするため）
    def _label_rank(e: dict[str, Any]) -> int:
        m = (e.get("files") or {}).get("items_input") or {}
        if isinstance(m.get("dataset_info"), dict) and (m["dataset_info"] or {}).get("label"):
            return 2  # 最強: 明示 label
        if m.get("source_dir"):
            return 1  # 中: MCP 由来の親フォルダ名
        return 0  # 弱: dataset_name / original_name fallback

    counts: dict[str, int] = {}
    sample_entry: dict[str, dict[str, Any]] = {}
    for e in visible:
        k = _items_sha(e) or _ITEMS_FILTER_NONE
        counts[k] = counts.get(k, 0) + 1
        cur = sample_entry.get(k)
        if cur is None or _label_rank(e) > _label_rank(cur):
            sample_entry[k] = e

    labels: dict[str, str] = {_ITEMS_FILTER_ALL: f"(すべて) — {len(visible)}件"}
    for k, cnt in counts.items():
        if k == _ITEMS_FILTER_NONE:
            labels[k] = f"(items_input なし) — {cnt}件"
            continue
        head = _resolve_items_label(sample_entry[k], items_sha=k, label_overrides=label_overrides)
        labels[k] = f"{head} ({k[:8]}) — {cnt}件"

    options = [_ITEMS_FILTER_ALL] + list(counts.keys())
    if hidden:
        labels[_ITEMS_FILTER_HIDDEN] = f"非表示 — {len(hidden)}件"
        options.append(_ITEMS_FILTER_HIDDEN)

    # 現在ロード中の items_input が履歴のどれかと一致するなら初期選択
    default_key = _ITEMS_FILTER_ALL
    if current_items_bytes is not None:
        cur_key = _items_group_key(current_items_bytes)
        if cur_key in counts:
            default_key = cur_key

    selected = st.selectbox(
        "items_input で絞り込み",
        options=options,
        index=options.index(default_key),
        format_func=lambda k: labels.get(k, k),
        help=(
            "同じ items_input.json で評価された投稿だけに絞ります。"
            "異なる問題入力同士は比較不能（コンテナ数/充填率のスケールが違う）ので、"
            "フェアなランキングにはグループ内比較が必要です。"
            "末尾の「非表示」を選ぶと非表示にしたエントリ一覧が表示されます。"
        ),
        key="items_input_filter",
    )
    if selected == _ITEMS_FILTER_HIDDEN:
        return hidden, True, None
    if selected == _ITEMS_FILTER_ALL:
        return visible, False, None
    filtered = [e for e in visible if (_items_sha(e) or _ITEMS_FILTER_NONE) == selected]
    if not filtered:
        st.info("この items_input と一致する投稿はありません。")
    selected_sha = selected if selected != _ITEMS_FILTER_NONE else None
    return filtered, False, selected_sha


def _build_leaderboard_row(
    e: dict[str, Any],
    cfg: ScoreboardConfig,
    *,
    include_hidden_meta: bool = False,
) -> dict[str, Any]:
    files = e.get("files") or {}
    layout_meta = files.get("layout_result") or {}
    items_meta = files.get("items_input") or {}
    layout_path = layout_meta.get("path") or ""
    items_path = items_meta.get("path") or ""
    items_sha_short = _items_sha(e)[:8]
    items_ident = (
        f"{items_meta.get('original_name', 'items_input.json')} ({items_sha_short})"
        if items_sha_short
        else "-"
    )
    score = e.get("score", {})
    n_containers = score.get("containers_used")
    above80 = _count_above_fill_threshold(score)
    is_pass = e.get("verdict") == "pass"
    # legacy: per-container 値が無いと「80% 達成本数」が出せない (順位付けは独立に可能)
    is_legacy = above80 is None and is_pass
    note = e.get("note", "")
    if is_legacy:
        note = f"[要再投稿] {note}".strip()
    gate = e.get("_gate")
    # 失格扱いの gate status のみ 備考に前置（ok / non_canonical はどちらも整合性 OK なので何もしない）
    if gate in ("item_mismatch", "no_items_input", "schema_error"):
        note = f"[{_GATE_LABEL.get(gate, gate)}] {e.get('_gate_reason', '')} / {note}".strip(" /")
    rank = e.get("_rank")
    fill_avg_raw = score.get("average_fill_rate")
    above80_label = "-" if above80 is None or n_containers is None else f"{above80}/{n_containers}"
    row: dict[str, Any] = {
        "順位": "-" if rank is None else int(rank),
        "判定": "合格" if is_pass else "失格",
        "コンテナ数": n_containers,
        "重心ズレ平均[mm]": round(float(score.get("cog_y_mean_deviation", 0) or 0)),
        "処理時間[ms]": score.get("execution_time_ms"),
        "平均充填率": f"{float(fill_avg_raw):.1%}" if fill_avg_raw is not None else "-",
        "80%達成本数": above80_label,
        "違反数": score.get("violation_count"),
        "投稿者": e.get("author", ""),
        "日時": e.get("timestamp", "")[:19].replace("T", " "),
        "備考": note,
        "items識別": items_ident,
        "入力ファイル": e.get("input_filename", ""),
        "layout_result": blob_url(cfg, layout_path) if layout_path else None,
        "items_input": blob_url(cfg, items_path) if items_path else None,
    }
    if include_hidden_meta:
        row["非表示日時"] = (e.get("hidden_at") or "")[:19].replace("T", " ")
    return row


def _render_leaderboard_table(rows: list[dict[str, Any]]) -> None:
    """順位付きリーダーボードを描画。

    失格行も合格行と同じ skin で描画する（旧 row-style グレーアウトは
    ダーク cockpit テーマで文字が灰背景に潰れて読めなくなるため廃止）。
    失格は「判定」「順位」列の値で識別できる。
    """
    if not rows:
        st.info("表示できる投稿がありません。")
        return

    try:
        import pandas as pd
    except ImportError:
        st.dataframe(rows, width="stretch", hide_index=True)
        return

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        column_config={
            "順位": st.column_config.TextColumn(
                "順位",
                help=(
                    "辞書式順位付けの結果。"
                    "1. コンテナ数 → 2. 重心ズレ平均 → 3. 処理時間 の優先順で決定。"
                    "失格チームは順位対象外 (「-」表示)。"
                ),
            ),
            "コンテナ数": st.column_config.NumberColumn(
                "コンテナ数",
                help="使用コンテナ本数 (主指標、少ないほど上位)",
            ),
            "重心ズレ平均[mm]": st.column_config.NumberColumn(
                "重心ズレ平均[mm]",
                help="全コンテナの |Yg-Yc| の平均 (タイブレーク 1、小さいほど上位)",
                format="%d",
            ),
            "処理時間[ms]": st.column_config.NumberColumn(
                "処理時間[ms]",
                help="execution_time_ms (タイブレーク 2、短いほど上位)",
                format="%d",
            ),
            "平均充填率": st.column_config.TextColumn(
                "平均充填率",
                help="参考値。順位付けには使用しない (コンテナ数の従属指標)",
            ),
            "80%達成本数": st.column_config.TextColumn(
                "80%達成本数",
                help="充填率 80% 以上のコンテナ本数 (努力目標、順位付けには影響しない)",
            ),
            "layout_result": st.column_config.LinkColumn(
                "layout_result",
                help="投稿時の layout_result.json を GitHub で開く",
                display_text="🔗 開く",
            ),
            "items_input": st.column_config.LinkColumn(
                "items_input",
                help="投稿時の items_input.json を GitHub で開く",
                display_text="🔗 開く",
            ),
        },
    )


def _entry_label(e: dict[str, Any]) -> str:
    ts = e.get("timestamp", "")[:19].replace("T", " ")
    author = e.get("author", "")
    note = (e.get("note") or "")[:30]
    return f"{ts} / {author} / {note}".rstrip(" /")


def _render_label_editor(
    cfg: ScoreboardConfig,
    labels_path: str,
    history: list[dict[str, Any]],
    label_overrides: dict[str, dict[str, Any]],
    *,
    author: str = "",
    preselect_sha: str | None = None,
) -> None:
    """selectbox 直下に「items_input ラベル編集」expander を出す (常時表示)。

    "(すべて)" を選んでいても編集できるよう、expander 内に独立のグループ選択を持つ。
    `preselect_sha` が selectbox 側で選ばれているならそれを初期選択にする。
    """
    # 公開エントリだけを items_sha ごとに集約 (selectbox と同じ集合に揃える)
    visible = [e for e in history if not e.get("hidden")]
    groups: dict[str, dict[str, Any]] = {}
    for e in visible:
        k = _items_sha(e)
        if not k:
            continue
        if k not in groups:
            groups[k] = e
    if not groups:
        return

    with st.expander("📝 items_input ラベル編集", expanded=False):
        st.caption(
            "items_input.json ごとの表示ラベルを上書きします。"
            " 空欄で保存すると既定ラベル (dataset_info.label / source_dir / ...) に戻ります。"
            " 同じ items_input で評価された全投稿に反映されます。"
        )
        keys = list(groups.keys())

        def _opt_label(k: str) -> str:
            head = _resolve_items_label(groups[k], items_sha=k, label_overrides=label_overrides)
            return f"{head} ({k[:8]})"

        default_index = keys.index(preselect_sha) if preselect_sha in keys else 0
        chosen = st.selectbox(
            "編集するグループ",
            options=keys,
            index=default_index,
            format_func=_opt_label,
            key="label_edit_group",
        )
        current = ""
        ov = label_overrides.get(chosen)
        if isinstance(ov, dict):
            current = str(ov.get("label") or "")
        new_label = st.text_input(
            "表示ラベル (空欄保存で既定に戻す)",
            value=current,
            max_chars=80,
            key="label_edit_input",
        )
        if st.button("ラベルを保存", key="label_edit_save"):
            try:
                with st.spinner("更新中..."):
                    update_label(
                        cfg,
                        labels_path,
                        chosen,
                        new_label,
                        author=(author or "").strip(),
                    )
                st.success("ラベルを更新しました。")
                st.rerun()
            except ScoreboardError as exc:
                st.error(f"更新失敗: {exc}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"想定外のエラー: {exc}")


def _render_hide_controls(entries: list[dict[str, Any]], cfg: ScoreboardConfig) -> None:
    if not entries:
        return
    options = {_entry_label(e): e["id"] for e in entries}
    label = st.selectbox(
        "非表示にするエントリ",
        list(options.keys()),
        key="hide_select",
    )
    if st.button("選択を非表示にする", key="hide_btn"):
        try:
            with st.spinner("更新中..."):
                update_entry(
                    cfg,
                    options[label],
                    {
                        "hidden": True,
                        "hidden_at": datetime.now(JST).isoformat(timespec="seconds"),
                    },
                    commit_message="scoreboard: hide entry",
                )
            st.success("非表示にしました。")
            st.rerun()
        except ScoreboardError as exc:
            st.error(f"更新失敗: {exc}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"想定外のエラー: {exc}")


def _render_unhide_controls(entries: list[dict[str, Any]], cfg: ScoreboardConfig) -> None:
    if not entries:
        return
    options = {_entry_label(e): e["id"] for e in entries}
    label = st.selectbox(
        "復元するエントリ",
        list(options.keys()),
        key="unhide_select",
    )
    if st.button("選択を復元する", key="unhide_btn"):
        try:
            with st.spinner("更新中..."):
                update_entry(
                    cfg,
                    options[label],
                    {"hidden": False, "hidden_at": None},
                    commit_message="scoreboard: unhide entry",
                )
            st.success("公開タブに戻しました。")
            st.rerun()
        except ScoreboardError as exc:
            st.error(f"更新失敗: {exc}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"想定外のエラー: {exc}")


# 「整合性」列の表示ラベル。input/output 整合性チェックが通った投稿は同等扱い
# として badge を出さない（"ok" も "non_canonical" も区別なく "—"）。
# 「公式 / 非公式」の概念は廃止：すべての input.json は平等。
_GATE_LABEL = {
    "ok": "—",
    "non_canonical": "—",
    "item_mismatch": "items不一致",
    "no_items_input": "items_input無し",
    "schema_error": "再検証不可",
}


def _apply_canonical_gate(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """submitter.apply_canonical_gate の Streamlit 側ラッパ（REPO_ROOT / registry を注入）。

    `REPO_ROOT` と `_load_registry` を module global 経由で読むため、
    既存テストの monkeypatch（`setattr(app, "REPO_ROOT", ...)` 等）はそのまま効く。
    """
    return _apply_canonical_gate_impl(entries, repo_root=REPO_ROOT, registry=_load_registry())


def _render_scoreboard_tab(
    *,
    report: dict[str, Any],
    violations: list[Violation],
    input_filename: str,
    layout_bytes: bytes,
    items_bytes: bytes | None,
    items_filename: str | None,
) -> None:
    """リーダーボード投稿フォーム＋履歴表示。"""
    cfg = _load_scoreboard_config()
    if cfg is None:
        st.warning(
            "リーダーボード機能は未設定です。\n\n"
            "`.streamlit/secrets.toml` にチーム共有のトークン・リポジトリ情報を設定してください。"
            "（`.streamlit/secrets.toml.example` を参考にコピー）"
        )
        return

    st.subheader("この評価結果を投稿")
    default_author = st.secrets.get("DEFAULT_AUTHOR", "")
    col1, col2 = st.columns([1, 2])
    with col1:
        author = st.text_input("投稿者", value=default_author, max_chars=30)
    with col2:
        note = st.text_input("備考（アルゴリズム名・工夫点など）", max_chars=200)

    st.caption(
        "投稿時に layout_result.json（および items_input.json）もリポジトリに保存され、"
        "他の検証者が同じ入力で再現できるようになります。"
        "同じ内容のファイルは再アップロードされません（SHA-256 で重複排除）。"
    )

    submitted = st.button(
        "リーダーボードに投稿",
        type="primary",
        disabled=not author.strip(),
    )
    if submitted:
        try:
            with st.spinner("入力ファイルをアップロード中..."):
                files_meta: dict[str, dict[str, Any]] = {
                    "layout_result": _upload_input_file(
                        cfg,
                        content_bytes=layout_bytes,
                        original_name=input_filename,
                    )
                }
                if items_bytes is not None and items_filename is not None:
                    files_meta["items_input"] = _upload_input_file(
                        cfg,
                        content_bytes=items_bytes,
                        original_name=items_filename,
                        role="items_input",
                    )
            entry = _build_scoreboard_entry(
                author=author.strip(),
                note=note.strip(),
                input_filename=input_filename,
                report=report,
                violations=violations,
                files=files_meta,
            )
            with st.spinner("GitHub に書き込み中..."):
                append_entry(
                    cfg,
                    entry,
                    commit_message=f"scoreboard: {author} ({entry['verdict']})",
                )
            st.success("投稿しました！下に反映されるまで数秒かかる場合があります。")
            # sidebar の「既存 submission から選択」selectbox は _render_page の
            # 冒頭で 1 回だけ描画されるため、投稿直後の新規エントリを反映するには
            # ページ全体を rerun する必要がある（非表示/復元と同じ扱い）。
            st.rerun()
        except ScoreboardError as exc:
            st.error(f"投稿に失敗しました: {exc}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"想定外のエラー: {exc}")

    st.divider()
    st.subheader("履歴")

    if st.button("最新を取得"):
        st.rerun()

    try:
        history, _ = fetch_history(cfg)
    except ScoreboardError as exc:
        st.error(f"履歴取得に失敗しました: {exc}")
        return

    if not history:
        st.info("まだ投稿がありません。評価を実行して上の「投稿」ボタンを押してください。")
        return

    # --- items_input でフィルタリング ---
    # 異なる items_input（＝異なる問題入力）同士を同じ土俵に乗せても比較にならないため、
    # sha256 が一致するグループ内でのみランキング & 正規化を行う。
    # 末尾の「非表示」を選んだ時は hidden=True のエントリだけ返ってくる (is_hidden_view=True)。
    labels_path = _labels_path(cfg)
    try:
        label_overrides, _ = fetch_labels(cfg, labels_path)
    except ScoreboardError as exc:
        st.warning(f"ラベル取得に失敗しました ({exc})。既定ラベルで表示します。")
        label_overrides = {}
    full_history = history
    history, is_hidden_view, selected_sha = _filter_by_items_input(
        history, items_bytes, label_overrides=label_overrides
    )
    if not is_hidden_view:
        _render_label_editor(
            cfg,
            labels_path,
            full_history,
            label_overrides,
            author=author,
            preselect_sha=selected_sha,
        )
    if not history:
        return

    st.caption(
        "順位付けは辞書式順序 (要件定義書 5.3):"
        " 1. コンテナ数 → 2. 重心ズレ平均 → 3. 処理時間。"
        " 失格チームは順位対象外で、表の最下段に表示します。"
        " 「平均充填率」「80%達成本数」は参考表示で順位付けには影響しません。"
        "\n\n各投稿は items_input.json と layout_result.json の整合性"
        "（layout が並べた item 集合が items_input の集合と一致するか）で再検証されます。"
        "整合性が取れている投稿はすべて同等にランキングされ、items_input が異なる投稿同士は"
        "上の selectbox で別グループとして切替表示されます。"
    )

    # filter（content hash でグループ化）→ canonical 再検証ゲート → 辞書式ランク
    gated = _apply_canonical_gate(history)
    ranked = _rank_entries(gated)
    rows = [_build_leaderboard_row(e, cfg, include_hidden_meta=is_hidden_view) for e in ranked]
    _render_leaderboard_table(rows)

    if is_hidden_view:
        _render_unhide_controls(ranked, cfg)
    else:
        _render_hide_controls(ranked, cfg)

    pass_count = sum(1 for e in ranked if e.get("verdict") == "pass")
    fail_count = len(ranked) - pass_count
    st.caption(
        f"表示件数: {len(ranked)} ({'非表示' if is_hidden_view else '公開'})"
        f" — 合格 {pass_count} / 失格 {fail_count}"
    )


def _collect_input_submissions() -> list[tuple[str, Path, Path | None]]:
    """input/ 配下の submission を (表示ラベル, layout_path, items_path|None) で返す。"""
    out: list[tuple[str, Path, Path | None]] = []
    if not INPUT_DIR.exists():
        return out
    for p in sorted(INPUT_DIR.iterdir()):
        if not p.is_dir():
            continue
        layout = p / "layout_result.json"
        if not layout.exists():
            continue
        items = p / "items_input.json"
        out.append((f"[input] {p.name}", layout, items if items.exists() else None))
    return out


def _collect_scoreboard_submissions() -> list[tuple[str, Path, Path | None]]:
    """scoreboard/history.json から files.layout_result 付きエントリを解決して返す。

    他メンバーの投稿（main を pull 済み）を選んで再評価/可視化できるようにする。
    timestamp 降順で新しい投稿を上に並べる。
    """
    history_path = SCOREBOARD_DIR / "history.json"
    if not history_path.exists():
        return []
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(history, list):
        return []
    enriched: list[tuple[str, Path, Path | None, str]] = []
    for e in history:
        if not isinstance(e, dict):
            continue
        files = e.get("files")
        if not files or "layout_result" not in files:
            continue
        layout_rel = files["layout_result"].get("path", "")
        if not layout_rel:
            continue
        layout_abs = REPO_ROOT / layout_rel
        if not layout_abs.exists():
            continue
        items_abs: Path | None = None
        items_info = files.get("items_input")
        if items_info and items_info.get("path"):
            cand = REPO_ROOT / items_info["path"]
            items_abs = cand if cand.exists() else None
        author = str(e.get("author", "unknown"))
        ts = str(e.get("timestamp", ""))[:16].replace("T", " ")  # YYYY-MM-DD HH:MM
        note = str(e.get("note", ""))
        note_suffix = f" — {note[:25]}" if note else ""
        verdict = e.get("verdict", "")
        verdict_mark = "PASS" if verdict == "pass" else "FAIL"
        label = f"[scoreboard] [{verdict_mark}] {author} @ {ts}{note_suffix}"
        enriched.append((label, layout_abs, items_abs, ts))
    enriched.sort(key=lambda x: x[3], reverse=True)
    return [(lb, lp, ip) for lb, lp, ip, _ in enriched]


def _render_page() -> None:
    st.set_page_config(
        page_title="Vanning Evaluator",
        layout="wide",
    )
    inject_css()

    with st.sidebar:
        st.header("入力 JSON")

        submissions = _collect_input_submissions() + _collect_scoreboard_submissions()
        labels = [s[0] for s in submissions]
        label_to_paths: dict[str, tuple[Path, Path | None]] = {
            s[0]: (s[1], s[2]) for s in submissions
        }

        selected = st.selectbox(
            "既存 submission から選択",
            options=["(選択しない)"] + labels,
            index=0,
            help="input/ のサンプル、および scoreboard/ に投稿された過去 layout を選べます",
        )

        st.divider()
        upload_disabled = selected != "(選択しない)"
        if upload_disabled:
            st.caption("選択中のためアップロードは無効化されています。")
        else:
            st.caption("またはファイルを手動アップロード")

        layout_file = st.file_uploader(
            "layout_result.json （必須）",
            type="json",
            key="layout",
            help="アルゴリズムが出力したコンテナ配置データ",
            disabled=upload_disabled,
        )
        items_file = st.file_uploader(
            "items_input.json （任意）",
            type="json",
            key="items",
            help="アップロードすると全アイテム配置チェックが走る",
            disabled=upload_disabled,
        )

    input_filename = ""
    layout_bytes = b""
    items_bytes: bytes | None = None
    items_filename: str | None = None
    layout_path: Path
    items_path: Path | None

    if selected != "(選択しない)":
        layout_p, items_p = label_to_paths[selected]
        layout_path = layout_p
        input_filename = selected
        layout_bytes = layout_p.read_bytes()
        if items_p is not None:
            items_path = items_p
            items_filename = f"{selected} (items_input.json)"
            items_bytes = items_p.read_bytes()
        else:
            items_path = None
    elif layout_file:
        layout_path = _save_uploaded(layout_file)
        items_path = _save_uploaded(items_file) if items_file else None
        input_filename = layout_file.name
        layout_bytes = layout_file.getvalue()
        if items_file:
            items_filename = items_file.name
            items_bytes = items_file.getvalue()
    else:
        render_header(title="Vanning Evaluator", input_filename="")
        st.info(
            "左のサイドバーから既存 submission を選択するか、"
            "layout_result.json をアップロードしてください。"
        )
        st.stop()

    try:
        layout = load_layout(layout_path)
        items_input = load_items(items_path) if items_path else None
    except Exception as exc:
        st.error(f"JSON のスキーマエラー: {exc}")
        st.stop()

    violations = run_all_checks(layout, items_input)
    teacher = compute_teacher_metrics(layout)
    scene = build_scene(layout, violations=violations, teacher=teacher)
    report = build_report(layout, items_input)

    # ネームプレート + HUD メトリクス行
    render_header(title="Vanning Evaluator", input_filename=input_filename)
    render_hud_metrics(
        verdict=scene.verdict,
        container_count=int(scene.summary["container_count"]),
        item_count=int(scene.summary["item_count"]),
        violation_count=int(scene.disqualification_count),
        average_fill_rate=float(scene.summary["average_fill_rate"]),
    )

    tab_3d, tab_viol, tab_report, tab_board, tab_json = st.tabs(
        [
            "3Dビュー",
            "違反詳細",
            "評価レポート",
            "リーダーボード",
            "JSON 生データ",
        ]
    )

    with tab_3d:
        fig = render_scene(scene)
        st.iframe(to_html(fig), height=820)

    with tab_viol:
        if violations:
            render_violation_groups(violations, ja_label=ja_violation_label)
        else:
            render_pass_empty_state()

    with tab_report:
        teacher_data = report["teacher_score_metrics"]
        internal = report["internal_metrics"]

        st.subheader("コンテナ別 充填率")
        st.dataframe(teacher_data["fill_rates"], width="stretch", hide_index=True)

        st.subheader("コンテナ別 Y 重心偏差")
        st.dataframe(teacher_data["cog_y"], width="stretch", hide_index=True)

        st.subheader("内部メトリクス")
        c1, c2, c3 = st.columns(3)
        c1.metric("実行時間", f"{internal['execution_time_ms']} ms")
        c1.metric("占有率", f"{internal['occupancy']:.1%}")
        stacking = internal["stacking"]
        c2.metric("最大段数", stacking["max_layers"])
        c2.metric("平均段数", f"{stacking['mean_layers']:.2f}")
        c3.metric("接地率", f"{stacking['z0_ratio']:.1%}")
        c3.metric("Y 重心平均偏差", f"{internal['cog_y_stats']['mean_deviation']:.0f} mm")

    with tab_board:
        _render_scoreboard_tab(
            report=report,
            violations=violations,
            input_filename=input_filename,
            layout_bytes=layout_bytes,
            items_bytes=items_bytes,
            items_filename=items_filename,
        )

    with tab_json:
        with st.expander("評価レポート（build_report 出力）", expanded=False):
            st.json(report)
        with st.expander("入力 layout_result.json", expanded=False):
            st.json(json.loads(layout_bytes))
        if items_bytes:
            with st.expander("入力 items_input.json", expanded=False):
                st.json(json.loads(items_bytes))


# テスト等で helper だけ import するときに副作用が起きないよう、
# Streamlit ScriptRunContext がある場合のみページを描画する。
# (streamlit run 経由ではコンテキストが存在し、import 経由では存在しない)
try:
    from streamlit.runtime.scriptrunner import get_script_run_ctx as _get_ctx

    if _get_ctx() is not None:
        _render_page()
except ImportError:
    _render_page()
