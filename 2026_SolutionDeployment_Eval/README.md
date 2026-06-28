# 投稿・評価プラットフォーム

このフォルダには、バンニングレイアウト結果を評価、3D 可視化、投稿するためのプラットフォームをまとめています。

## 構成

```text
2026_SolutionDeployment_Eval/
├─ vanning_eval/       # 評価器・3Dビューア・Web UI
├─ submission_MCP/     # MCP による一括投稿用サーバ
├─ scoreboard/         # 投稿履歴・順位表データ
└─ README.md
```

## 主な機能

- `layout_result.json` の形式チェック
- はみ出し、重なり、接地、重量、配送先混在、重心などの制約チェック
- 使用コンテナ数、平均重心ズレ、処理時間による評価
- Plotly による 3D 可視化
- scoreboard への投稿と履歴管理
- MCP による一括投稿

## ローカル起動

```bash
cd 2026_SolutionDeployment_Eval/vanning_eval
python main.py
```

Windows では以下でも起動できます。

```bat
launch.bat
```

## 一括評価

`vanning_eval/input/<名前>/` に `layout_result.json` を置くと評価できます。
完全な配置チェックを行う場合は、同じフォルダに `items_input.json` も置きます。

```text
vanning_eval/input/<名前>/
├─ layout_result.json
└─ items_input.json
```

実行:

```bash
cd 2026_SolutionDeployment_Eval/vanning_eval
python main.py --batch
```

## MCP 投稿

MCP による一括投稿機能は以下にあります。

```text
submission_MCP/
```

詳細は `submission_MCP/README.md` を参照してください。

## scoreboard

```text
scoreboard/
├─ history.json
├─ items_labels.json
└─ submissions/
```

投稿履歴と順位表用のデータです。
