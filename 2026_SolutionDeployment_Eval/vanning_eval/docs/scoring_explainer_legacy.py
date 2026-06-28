"""総合スコア S の説明 PDF (旧仕様、廃止予定)。

【注意】このスクリプトが説明する重み付き総合スコア式 (S = 100 × (0.60·C + 0.25·F + 0.15·G)) と
3 段階重心評価は、要件定義書 PR #26 で廃止されている。
現行の採点方式は辞書式順位付け (コンテナ数 → 重心ズレ平均 → 処理時間) に変更。
新仕様の説明資料は別途作成予定。本ファイルは履歴として残すのみ。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import patches
from matplotlib.backends.backend_pdf import PdfPages

# 日本語フォント
plt.rcParams["font.family"] = ["Yu Gothic", "Meiryo", "MS Gothic"]
# monospace でも CJK が出るよう MS Gothic を使う (DejaVu Sans Mono は CJK 未対応)
plt.rcParams["font.monospace"] = ["MS Gothic", "Yu Gothic", "Consolas"]
plt.rcParams["axes.unicode_minus"] = False

OUT = Path(__file__).resolve().parent / "scoring_explainer.pdf"


def _slide_title(fig, ax, title: str, subtitle: str = ""):
    ax.text(
        0.02,
        0.96,
        title,
        transform=fig.transFigure,
        fontsize=22,
        fontweight="bold",
        color="#0E1014",
    )
    if subtitle:
        ax.text(
            0.02,
            0.92,
            subtitle,
            transform=fig.transFigure,
            fontsize=12,
            color="#555",
        )


def slide_overview() -> plt.Figure:
    fig = plt.figure(figsize=(11.69, 8.27), dpi=120)  # A4 横
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    _slide_title(
        fig, ax, "総合スコア S の構成", "要件定義書 Section 5 の評価軸を 1 つの 0-100 点に集約"
    )

    # 中央の式
    ax.text(
        0.5,
        0.78,
        r"S = 100 × ( 0.60·C  +  0.25·F  +  0.15·G )",
        transform=fig.transFigure,
        fontsize=24,
        ha="center",
        fontweight="bold",
        color="#0E1014",
    )
    ax.text(
        0.5,
        0.72,
        "失格 (ハード制約 7 項目のいずれかに違反) なら S = 0",
        transform=fig.transFigure,
        fontsize=11,
        ha="center",
        color="#B33",
    )

    # 3 指標カード
    cards = [
        (
            "C: コンテナ効率",
            "0.60",
            "理論下限本数 / 実使用本数",
            "物流コストに直結する第一指標。\n少ない便で運べたほど高得点。",
            "#FFE082",
        ),
        (
            "F: 充填スコア",
            "0.25",
            "50% 閾値超え分の平均\n× (1 − 違反コンテナ比率)",
            "コンテナ単位で 50% を合格ラインとし、\n違反 1 本ごとに乗算ペナルティ。",
            "#A5D6A7",
        ),
        (
            "G: 重心スコア",
            "0.15",
            "3 段階評価のコンテナ平均\n(満点≤1200 / 許容≤3000 / 違反>3000 mm)",
            "走行中の安定性。中心からのズレを\n3 段階で評価し平均。",
            "#90CAF9",
        ),
    ]
    n = len(cards)
    margin = 0.05
    gap = 0.02
    width = (1 - 2 * margin - gap * (n - 1)) / n
    y = 0.10
    height = 0.50
    for i, (head, weight, formula, body, color) in enumerate(cards):
        x = margin + i * (width + gap)
        rect = patches.FancyBboxPatch(
            (x, y),
            width,
            height,
            boxstyle="round,pad=0.005,rounding_size=0.015",
            transform=fig.transFigure,
            facecolor=color,
            edgecolor="#0E1014",
            linewidth=1.2,
        )
        fig.patches.append(rect)
        ax.text(
            x + width / 2,
            y + height - 0.04,
            head,
            transform=fig.transFigure,
            fontsize=15,
            fontweight="bold",
            ha="center",
            color="#0E1014",
        )
        ax.text(
            x + width / 2,
            y + height - 0.10,
            f"重み {weight}",
            transform=fig.transFigure,
            fontsize=11,
            ha="center",
            color="#333",
        )
        ax.text(
            x + width / 2,
            y + height - 0.20,
            formula,
            transform=fig.transFigure,
            fontsize=10,
            ha="center",
            color="#0E1014",
            family="monospace",
        )
        ax.text(
            x + width / 2,
            y + 0.07,
            body,
            transform=fig.transFigure,
            fontsize=10,
            ha="center",
            va="center",
            color="#0E1014",
        )

    ax.text(
        0.5,
        0.04,
        "重みは要件定義書 Section 5 の優先度順 (コンテナ数 > 充填率 > 重心)",
        transform=fig.transFigure,
        fontsize=9,
        ha="center",
        color="#777",
    )
    return fig


def slide_g_detail() -> plt.Figure:
    fig = plt.figure(figsize=(11.69, 8.27), dpi=120)
    ax = fig.add_axes([0.08, 0.18, 0.84, 0.62])
    _slide_title(
        fig, ax, "G: 重心スコア (3 段階評価)", "コンテナ中心 (Y=6,000 mm) からの前後ズレを評価"
    )

    # g_i のグラフ
    devs = np.linspace(0, 4000, 400)

    def g(d):
        if d <= 1200:
            return 1.0
        if d <= 3000:
            return 1.0 - (d - 1200) / 1800
        return 0.0

    ys = np.array([g(d) for d in devs])
    ax.plot(devs, ys, color="#0E1014", linewidth=2.5)

    # 区分の塗り分け
    ax.axvspan(0, 1200, alpha=0.20, color="#4CAF50", label="PERFECT (満点)")
    ax.axvspan(1200, 3000, alpha=0.20, color="#FFC107", label="ACCEPTABLE (許容・線形減点)")
    ax.axvspan(3000, 4000, alpha=0.20, color="#F44336", label="VIOLATION (違反)")

    ax.axvline(1200, color="#666", linestyle="--", linewidth=1)
    ax.axvline(3000, color="#666", linestyle="--", linewidth=1)
    ax.text(600, 1.05, "PERFECT\n(全長 10% 以内)", ha="center", fontsize=10)
    ax.text(2100, 1.05, "ACCEPTABLE\n(全長 10〜25%)", ha="center", fontsize=10)
    ax.text(3500, 1.05, "VIOLATION\n(全長 25% 超)", ha="center", fontsize=10)

    ax.set_xlim(0, 4000)
    ax.set_ylim(0, 1.18)
    ax.set_xlabel("コンテナ中心からの重心ズレ (mm)", fontsize=12)
    ax.set_ylabel("コンテナ点 g_i", fontsize=12)
    ax.set_xticks([0, 600, 1200, 2100, 3000, 4000])
    ax.set_yticks([0, 0.5, 1.0])
    ax.grid(alpha=0.3)

    # 説明文
    fig.text(
        0.08,
        0.10,
        "コンテナ 1 本ごとに g_i を算出 → 全コンテナで単純平均 → G",
        fontsize=12,
        color="#0E1014",
    )
    fig.text(
        0.08,
        0.06,
        "例: ズレ [500, 2100, 800] mm → g_i = [1.0, 0.5, 1.0] → G = 0.83",
        fontsize=10,
        color="#444",
        family="monospace",
    )
    fig.text(
        0.08,
        0.025,
        "1 本のひどいズレを他で隠せない設計 (平均は必ず引きずられる)",
        fontsize=10,
        color="#777",
        style="italic",
    )
    return fig


def main():
    with PdfPages(OUT) as pdf:
        for fig in (slide_overview(), slide_g_detail()):
            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)
    print(f"wrote: {OUT}")


if __name__ == "__main__":
    main()
