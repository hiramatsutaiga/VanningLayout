"""デフォルト入口: Streamlit WebUI を起動する（`--batch` で従来の一括評価）。

使い方:
    python main.py                 # WebUI 起動（ブラウザで http://localhost:8501）
    python main.py --batch         # input/ 配下の全 submission を JSON レポート化
    python main.py --help          # オプション一覧

--batch モード:
    1. input/<submission_name>/ 以下にファイルを配置
       - layout_result.json (必須)
       - items_input.json   (任意。全アイテム配置チェックを有効化)
    2. python main.py --batch
    3. 結果は output/<submission_name>_report.json に出力される
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from vanning_eval.report import build_report, print_summary, save_report
from vanning_eval.schema import SchemaError, load_items, load_layout

ROOT = Path(__file__).parent
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
STREAMLIT_APP = ROOT / "src" / "vanning_viewer" / "streamlit_app.py"


def _setup_utf8_streams() -> None:
    """Windows の CP932 端末でも日本語を文字化けさせないため stdout/stderr を UTF-8 に再設定。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def run_evaluation(input_dir: Path, output_dir: Path) -> list[dict]:
    """input_dir 配下の全 submission を評価し、レポートのリストを返す。

    submission は layout_result.json を含む input_dir のサブディレクトリ。
    """
    output_dir.mkdir(exist_ok=True)
    reports: list[dict] = []
    for sub_dir in sorted(p for p in input_dir.iterdir() if p.is_dir()):
        layout_path = sub_dir / "layout_result.json"
        if not layout_path.exists():
            continue
        items_path = sub_dir / "items_input.json"
        layout = load_layout(layout_path)
        items = load_items(items_path) if items_path.exists() else None
        report = build_report(layout, items)
        report["submission_name"] = sub_dir.name
        save_report(report, output_dir / f"{sub_dir.name}_report.json")
        reports.append(report)
    return reports


def _vw(s: str) -> int:
    """見かけの幅（非 ASCII 文字は 2、それ以外は 1）。"""
    return sum(2 if ord(c) > 0x7F else 1 for c in s)


def _lpad(s: str, w: int) -> str:
    return s + " " * max(0, w - _vw(s))


def _rpad(s: str, w: int) -> str:
    return " " * max(0, w - _vw(s)) + s


def _print_table(reports: list[dict]) -> None:
    """submission 1 件 1 行のサマリテーブルを標準出力に表示する。"""
    widths = (20, 10, 12, 10, 8)
    header = (
        f"{_lpad('提出', widths[0])} "
        f"{_lpad('判定', widths[1])} "
        f"{_rpad('コンテナ数', widths[2])} "
        f"{_rpad('充填率', widths[3])} "
        f"{_rpad('違反数', widths[4])}"
    )
    print(header)
    print("-" * _vw(header))
    for r in reports:
        verdict_ja = {"pass": "合格", "disqualified": "失格"}.get(r["verdict"], r["verdict"])
        containers = r["teacher_score_metrics"]["containers_used"]
        fill = r["teacher_score_metrics"]["average_fill_rate"]
        violations = len(r["disqualifications"])
        print(
            f"{_lpad(r['submission_name'], widths[0])} "
            f"{_lpad(verdict_ja, widths[1])} "
            f"{_rpad(str(containers), widths[2])} "
            f"{_rpad(f'{fill:.4f}', widths[3])} "
            f"{_rpad(str(violations), widths[4])}"
        )


def _run_batch() -> int:
    """input/ 配下の全 submission を一括評価してレポートを出力する。"""
    if not INPUT_DIR.exists() or not any(p.is_dir() for p in INPUT_DIR.iterdir()):
        print(
            "input/ に submission がありません。\n"
            "  input/<あなたの名前>/layout_result.json を配置して再実行してください。",
            file=sys.stderr,
        )
        return 2

    try:
        reports = run_evaluation(INPUT_DIR, OUTPUT_DIR)
    except SchemaError as e:
        print(f"SCHEMA ERROR: {e}", file=sys.stderr)
        return 2

    if not reports:
        print("layout_result.json を含む submission が見つかりませんでした。", file=sys.stderr)
        return 2

    for r in reports:
        print(f"\n=== {r['submission_name']} ===")
        print_summary(r)

    print("\n=== 総合サマリ ===")
    _print_table(reports)
    print(f"\nレポート出力先: {OUTPUT_DIR}")

    return 0 if all(r["verdict"] == "pass" for r in reports) else 1


def _ensure_streamlit_credentials() -> None:
    """Streamlit 初回起動時のメール入力プロンプトを事前にスキップする。

    `streamlit run` は初回だけ `~/.streamlit/credentials.toml` が無いと標準入力から
    メールアドレスを要求する。main.py がサブプロセスで起動する場合このプロンプトで
    固まって見えるため、空ファイルを作って事前に抑止する。
    """
    credentials = Path.home() / ".streamlit" / "credentials.toml"
    if credentials.exists():
        return
    credentials.parent.mkdir(parents=True, exist_ok=True)
    credentials.write_text('[general]\nemail = ""\n', encoding="utf-8")


def _launch_webui(port: int) -> int:
    """Streamlit WebUI を起動してブラウザで開く。Ctrl+C で停止。"""
    if not STREAMLIT_APP.exists():
        print(f"ERROR: {STREAMLIT_APP} が見つかりません。", file=sys.stderr)
        return 2
    try:
        import streamlit  # noqa: F401
    except ImportError:
        print(
            "Streamlit がインストールされていません。\n"
            '  pip install -e ".[viewer]"  で追加してください。',
            file=sys.stderr,
        )
        return 2

    _ensure_streamlit_credentials()

    print(f"Streamlit WebUI を起動中... http://localhost:{port}")
    print("停止するには Ctrl+C を押してください。\n")
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(STREAMLIT_APP),
        "--server.port",
        str(port),
    ]
    try:
        # cwd=ROOT 指定により、Streamlit が .streamlit/secrets.toml を見つけられる
        return subprocess.call(cmd, stdin=subprocess.DEVNULL, cwd=str(ROOT))
    except KeyboardInterrupt:
        return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "バンニング配置評価ツール。"
            "デフォルトは Streamlit WebUI 起動。--batch で一括評価モード。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="input/ 配下の全 submission を一括評価して JSON レポートを output/ に出力",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8501,
        help="WebUI 起動ポート (既定: 8501)",
    )
    return parser


def main() -> int:
    _setup_utf8_streams()
    args = _build_parser().parse_args()
    if args.batch:
        return _run_batch()
    return _launch_webui(args.port)


if __name__ == "__main__":
    raise SystemExit(main())
