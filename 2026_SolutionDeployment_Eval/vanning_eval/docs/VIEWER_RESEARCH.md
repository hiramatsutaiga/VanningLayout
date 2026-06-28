# Viewer 調査メモ

このファイルは、3D 可視化機能を作成するために検討した内容を、現在の実装に合わせて整理したものです。

## 採用した構成

```text
vanning_eval/src/vanning_viewer/
├─ streamlit_app.py
├─ plotly_renderer.py
├─ view_model.py
├─ submitter.py
└─ scoreboard_client.py
```

## 採用した技術

- Web UI: Streamlit
- 3D 表示: Plotly
- 入力: `layout_result.json`
- 任意入力: `items_input.json`

## 目的

JSON だけでは配置状態を確認しにくいため、以下を画面上で確認できるようにしています。

- コンテナ内の積荷配置
- はみ出し、重なり、接地違反などの有無
- コンテナごとの積載状況
- 評価結果
- scoreboard への投稿結果

## 現在の使い方

公開サーバーが起動している場合:

```text
https://136.109.238.213.sslip.io/
```

ローカルで起動する場合:

```bash
cd 2026_SolutionDeployment_Eval/vanning_eval
python -m pip install -e ".[viewer]"
python main.py
```

または、一括評価として以下を使います。

```bash
python main.py --batch
```

評価対象は `vanning_eval/input/` に配置します。
