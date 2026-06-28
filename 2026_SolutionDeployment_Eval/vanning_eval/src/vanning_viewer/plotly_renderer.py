"""Plotly で `ViewScene` を 3D インタラクティブ図に変換するレンダラ。

性能面の工夫:
  - 1 コンテナの全ボックスを 1 つの Mesh3d に結合（traces 数を削減）
  - 違反アイテムは別 Mesh3d で上書き描画（赤 + 黒エッジ）
  - 非アクティブコンテナのトレースは `visible=False` で隠す

updatemenus のドロップダウンで `visible` マスクと軸範囲を切替えてコンテナを選択する。
違反トレースは HTML 読み込み時に起動する JS でパルス（点滅）させて注目させる。
"""

from __future__ import annotations

from collections.abc import Iterable

from .colors import CONTAINER_FRAME_COLOR, VIOLATION_COLOR
from .view_model import ViewBox, ViewContainer, ViewScene

# 1 立方体 = 8 頂点 / 12 三角形。頂点順は (x,y,z) ∈ {min,max}^3 の 2^3 = 8 組。
_CUBE_TRI_I: tuple[int, ...] = (0, 0, 4, 4, 0, 0, 3, 3, 0, 0, 1, 1)
_CUBE_TRI_J: tuple[int, ...] = (1, 2, 5, 6, 1, 5, 2, 6, 3, 7, 2, 6)
_CUBE_TRI_K: tuple[int, ...] = (2, 3, 6, 7, 5, 4, 6, 7, 7, 4, 6, 5)

# 範囲計算時のマージン（mm）。枠外はみ出しが綺麗に見える余白。
_RANGE_MARGIN = 400


def _cuboid_vertices(
    x: float,
    y: float,
    z: float,
    w: float,
    l: float,  # noqa: E741
    h: float,
) -> tuple[list[float], list[float], list[float]]:
    xs = [x, x + w, x + w, x, x, x + w, x + w, x]
    ys = [y, y, y + l, y + l, y, y, y + l, y + l]
    zs = [z, z, z, z, z + h, z + h, z + h, z + h]
    return xs, ys, zs


def _box_edges_xyz(
    box: ViewBox,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """1 ボックスの 12 エッジを None 区切りで返す。"""
    x0, x1 = box.x, box.x + box.w
    y0, y1 = box.y, box.y + box.l
    z0, z1 = box.z, box.z + box.h
    # 底面ループ → 天面ループ → 4 本の垂直エッジ
    x: list[float | None] = [x0, x1, x1, x0, x0, None, x0, x1, x1, x0, x0]
    y: list[float | None] = [y0, y0, y1, y1, y0, None, y0, y0, y1, y1, y0]
    z: list[float | None] = [z0, z0, z0, z0, z0, None, z1, z1, z1, z1, z1]
    for cx, cy in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        x.extend([None, cx, cx])
        y.extend([None, cy, cy])
        z.extend([None, z0, z1])
    return x, y, z


def _merge_boxes(boxes: Iterable[ViewBox]) -> dict[str, list]:
    """複数 ViewBox を 1 つの Mesh3d 用頂点 / 面 / 属性配列に結合。"""
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    ii: list[int] = []
    jj: list[int] = []
    kk: list[int] = []
    vertex_color: list[str] = []
    hovertext: list[str] = []
    for box in boxes:
        base = len(xs)
        bxs, bys, bzs = _cuboid_vertices(box.x, box.y, box.z, box.w, box.l, box.h)
        xs.extend(bxs)
        ys.extend(bys)
        zs.extend(bzs)
        ii.extend(base + o for o in _CUBE_TRI_I)
        jj.extend(base + o for o in _CUBE_TRI_J)
        kk.extend(base + o for o in _CUBE_TRI_K)
        vertex_color.extend([box.color] * 8)
        hovertext.extend([box.label] * 8)
    return {
        "x": xs,
        "y": ys,
        "z": zs,
        "i": ii,
        "j": jj,
        "k": kk,
        "vertexcolor": vertex_color,
        "hovertext": hovertext,
    }


def _merge_edges(
    boxes: Iterable[ViewBox],
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """複数ボックスのエッジを None 区切りで 1 本の Scatter3d 用に結合。"""
    xs: list[float | None] = []
    ys: list[float | None] = []
    zs: list[float | None] = []
    for box in boxes:
        bx, by, bz = _box_edges_xyz(box)
        if xs:
            xs.append(None)
            ys.append(None)
            zs.append(None)
        xs.extend(bx)
        ys.extend(by)
        zs.extend(bz)
    return xs, ys, zs


def _container_frame_xyz(
    w: float,
    l: float,  # noqa: E741
    h: float,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """コンテナ枠の 12 エッジを、None で区切られた連続線として返す。"""
    x: list[float | None] = [0, w, w, 0, 0, None, 0, w, w, 0, 0]
    y: list[float | None] = [0, 0, l, l, 0, None, 0, 0, l, l, 0]
    z: list[float | None] = [0, 0, 0, 0, 0, None, h, h, h, h, h]
    for x0, y0 in ((0, 0), (w, 0), (w, l), (0, l)):
        x.extend([None, x0, x0])
        y.extend([None, y0, y0])
        z.extend([None, 0, h])
    return x, y, z


def _container_ranges(
    container: ViewContainer,
) -> tuple[list[float], list[float], list[float]]:
    """コンテナ枠＋全ボックスを包含する軸範囲を返す（枠外はみ出しもクリップしない）。"""
    min_x, max_x = 0.0, container.w
    min_y, max_y = 0.0, container.l
    min_z, max_z = 0.0, container.h
    for b in container.boxes:
        min_x = min(min_x, b.x)
        max_x = max(max_x, b.x + b.w)
        min_y = min(min_y, b.y)
        max_y = max(max_y, b.y + b.l)
        min_z = min(min_z, b.z)
        max_z = max(max_z, b.z + b.h)
    return (
        [min_x - _RANGE_MARGIN, max_x + _RANGE_MARGIN],
        [min_y - _RANGE_MARGIN, max_y + _RANGE_MARGIN],
        [min_z - _RANGE_MARGIN, max_z + _RANGE_MARGIN],
    )


def _container_title(container: ViewContainer, scene: ViewScene) -> str:
    verdict_ja = {"pass": "合格", "disqualified": "失格"}.get(scene.verdict, scene.verdict)
    return (
        f"Container {container.container_id} "
        f"[{container.destination_id}] — "
        f"判定: {verdict_ja} / "
        f"充填率: {container.fill_rate:.1%} / "
        f"Y重心偏差: {container.cog_y_offset_mm:.0f} mm / "
        f"重量: {container.total_weight_kg:.0f} kg"
    )


# JS: 違反トレースを Plotly.restyle で点滅させる post_script。
#
#   視点操作（wheel / drag 等）中は WebGL 再描画と衝突するため、capture phase で
#   マウス・ホイール・タッチイベントを先取りし、「直近 1.5 秒内に操作があれば
#   点滅をスキップ」するタイムスタンプ方式で抑制する。右上の ON/OFF トグルで
#   点滅そのものを停止可能（停止時は固定で明るい赤を維持）。
_PULSE_SCRIPT = """
(function() {
  function pickDiv() {
    var divs = document.querySelectorAll('.plotly-graph-div');
    return divs.length ? divs[divs.length - 1] : null;
  }
  function start() {
    var gd = pickDiv();
    if (!gd || !gd.data) { setTimeout(start, 150); return; }
    var indices = [];
    gd.data.forEach(function(t, i) {
      if (t.name && t.name.indexOf('violation') !== -1) indices.push(i);
    });
    if (!indices.length) return;

    gd.parentElement.style.position = 'relative';

    // --- Toggle button for 3D pulse ---
    var state = { on: true, pulseOn: true, lastInteraction: 0 };
    var btn = document.createElement('button');
    btn.textContent = '点滅: ON';
    btn.style.cssText = 'position:absolute;top:10px;right:12px;z-index:9999;' +
      'padding:4px 10px;background:#fff;border:1px solid #888;border-radius:4px;' +
      'font-size:12px;cursor:pointer;';
    btn.addEventListener('click', function() {
      state.pulseOn = !state.pulseOn;
      btn.textContent = '点滅: ' + (state.pulseOn ? 'ON' : 'OFF');
      if (!state.pulseOn) Plotly.restyle(gd, {opacity: 0.95}, indices);
    });
    gd.parentElement.appendChild(btn);

    // --- Interaction tracking (capture phase = before Plotly's own handlers) ---
    function touch() { state.lastInteraction = Date.now(); }
    var events = ['wheel', 'mousedown', 'mousemove', 'touchstart', 'touchmove'];
    events.forEach(function(ev) {
      gd.addEventListener(ev, touch, { passive: true, capture: true });
    });
    gd.on('plotly_relayouting', touch);

    // --- 3D pulse loop: only fires when idle for >= QUIET_MS ---
    var QUIET_MS = 1500;
    setInterval(function() {
      if (!state.pulseOn) return;
      if (Date.now() - state.lastInteraction < QUIET_MS) return;
      state.on = !state.on;
      Plotly.restyle(gd, {opacity: state.on ? 0.95 : 0.3}, indices);
    }, 700);
  }
  start();
})();
"""


def render_scene(scene: ViewScene):  # -> plotly.graph_objects.Figure
    """ViewScene → plotly.graph_objects.Figure。Plotly の遅延インポート。"""
    import plotly.graph_objects as go  # noqa: PLC0415 - heavy optional dep

    if not scene.containers:
        return go.Figure()

    fig = go.Figure()
    traces_per_container: list[int] = []

    for idx, container in enumerate(scene.containers):
        visible = idx == 0
        count = 0

        normal_boxes = [b for b in container.boxes if not b.violated]
        violated_boxes = [b for b in container.boxes if b.violated]

        if normal_boxes:
            merged = _merge_boxes(normal_boxes)
            fig.add_trace(
                go.Mesh3d(
                    x=merged["x"],
                    y=merged["y"],
                    z=merged["z"],
                    i=merged["i"],
                    j=merged["j"],
                    k=merged["k"],
                    vertexcolor=merged["vertexcolor"],
                    opacity=0.75,
                    flatshading=True,
                    hoverinfo="text",
                    hovertext=merged["hovertext"],
                    name=f"container {container.container_id} items",
                    visible=visible,
                    showlegend=False,
                )
            )
            count += 1

        if violated_boxes:
            merged = _merge_boxes(violated_boxes)
            fig.add_trace(
                go.Mesh3d(
                    x=merged["x"],
                    y=merged["y"],
                    z=merged["z"],
                    i=merged["i"],
                    j=merged["j"],
                    k=merged["k"],
                    color=VIOLATION_COLOR,
                    opacity=0.9,
                    flatshading=True,
                    hoverinfo="text",
                    hovertext=merged["hovertext"],
                    name=f"container {container.container_id} violation_fill",
                    visible=visible,
                    showlegend=False,
                )
            )
            count += 1

            ex, ey, ez = _merge_edges(violated_boxes)
            fig.add_trace(
                go.Scatter3d(
                    x=ex,
                    y=ey,
                    z=ez,
                    mode="lines",
                    line={"color": "#000000", "width": 5},
                    hoverinfo="skip",
                    name=f"container {container.container_id} violation_edges",
                    visible=visible,
                    showlegend=False,
                )
            )
            count += 1

        fx, fy, fz = _container_frame_xyz(container.w, container.l, container.h)
        fig.add_trace(
            go.Scatter3d(
                x=fx,
                y=fy,
                z=fz,
                mode="lines",
                line={"color": CONTAINER_FRAME_COLOR, "width": 3},
                hoverinfo="skip",
                name=f"container {container.container_id} frame",
                visible=visible,
                showlegend=False,
            )
        )
        count += 1

        traces_per_container.append(count)

    total_traces = sum(traces_per_container)
    buttons = []
    offset = 0
    for idx, container in enumerate(scene.containers):
        cnt = traces_per_container[idx]
        mask = [False] * total_traces
        for t in range(offset, offset + cnt):
            mask[t] = True
        offset += cnt
        rx, ry, rz = _container_ranges(container)
        buttons.append(
            {
                "label": f"Container {container.container_id} ({container.destination_id})",
                "method": "update",
                "args": [
                    {"visible": mask},
                    {
                        "title": _container_title(container, scene),
                        "scene.xaxis.range": rx,
                        "scene.yaxis.range": ry,
                        "scene.zaxis.range": rz,
                    },
                ],
            }
        )

    first = scene.containers[0]
    rx0, ry0, rz0 = _container_ranges(first)
    fig.update_layout(
        title=_container_title(first, scene),
        updatemenus=[
            {
                "active": 0,
                "buttons": buttons,
                "direction": "down",
                "x": 0.01,
                "y": 1.12,
                "xanchor": "left",
                "yanchor": "top",
                "showactive": True,
            }
        ],
        scene={
            "xaxis": {"title": "X (幅) mm", "range": rx0},
            "yaxis": {"title": "Y (長手) mm", "range": ry0},
            "zaxis": {"title": "Z (高さ) mm", "range": rz0},
            "aspectmode": "data",
            "camera": {"eye": {"x": 1.6, "y": -2.0, "z": 1.1}},
            # uirevision を固定値にすると、restyle（点滅）後もユーザー操作した
            # カメラ位置・ズーム・回転が保持される。コンテナ切替時は変わってよいので
            # value は "scene" のまま固定。
            "uirevision": "scene",
        },
        uirevision="figure",
        margin={"l": 0, "r": 0, "t": 100, "b": 0},
        height=760,
    )
    return fig


def save_html(fig, path: str) -> None:
    """Figure を自己完結 HTML として保存（オフラインでも開ける）。

    違反トレースを点滅させる post_script を埋め込み、読み込み直後に起動する。
    """
    fig.write_html(
        path,
        include_plotlyjs="cdn",
        full_html=True,
        post_script=_PULSE_SCRIPT,
    )


def to_html(fig) -> str:
    """Figure を自己完結 HTML 文字列として返す（Streamlit embed 用）。"""
    return fig.to_html(
        include_plotlyjs="cdn",
        full_html=True,
        post_script=_PULSE_SCRIPT,
    )
