"""3D viewer module for vanning-eval.

描画非依存の ViewModel 層（view_model）と、描画バックエンド（plotly_renderer 等）
に分離した可視化パッケージ。設計詳細は docs/VIEWER_DESIGN.md を参照。
"""

from __future__ import annotations

__version__ = "0.1.0"

from .view_model import ViewBox, ViewContainer, ViewScene, build_scene

__all__ = ["ViewBox", "ViewContainer", "ViewScene", "build_scene", "__version__"]
