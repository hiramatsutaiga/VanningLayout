# vanning_eval 設計メモ

このドキュメントは、現在の `vanning_eval/` の役割を簡潔にまとめたものです。

## 目的

`layout_result.json` を読み込み、以下を行います。

- JSON 形式の確認
- 物理制約のチェック
- 採点指標の計算
- 3D 可視化
- scoreboard への投稿

## ローカル実行

```bash
cd 2026_SolutionDeployment_Eval/vanning_eval
python -m pip install -e ".[viewer]"
python main.py
```

ローカル実行時は標準で `http://localhost:8501` を使用します。
`launch.bat` を使う場合は `http://localhost:8502` を使用します。

## フォルダ構成

```text
vanning_eval/
├─ main.py
├─ src/
│  ├─ vanning_eval/
│  │  ├─ schema.py
│  │  ├─ constraints.py
│  │  ├─ scoring.py
│  │  ├─ metrics.py
│  │  └─ report.py
│  └─ vanning_viewer/
│     ├─ streamlit_app.py
│     ├─ plotly_renderer.py
│     ├─ submitter.py
│     └─ scoreboard_client.py
├─ input/
├─ output/
└─ tests/
```

## 評価の流れ

1. `layout_result.json` を読み込む
2. 必要に応じて `items_input.json` も読み込む
3. スキーマを確認する
4. はみ出し、重なり、接地、重量、配送先混在、重心を確認する
5. 合格結果に対して順位指標を計算する
6. 3D 表示または scoreboard 投稿に利用する

## 順位指標

順位は以下の順で決まります。

1. 使用コンテナ数
2. 平均 Y 軸重心ズレ
3. 処理時間

## taiga 成果物との対応

```text
../../taiga/layout_result.json
../../taiga/out_hard/layout_01.json ～ layout_12.json
```
