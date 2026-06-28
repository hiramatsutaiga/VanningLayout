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

## 使い方

### 公開サーバー版

すでにサーバーが起動している場合は、以下の URL をブラウザで開くだけで使用できます。
この場合、ローカルで `launch.bat` や `python main.py` を実行する必要はありません。

```text
https://136.109.238.213.sslip.io/
```

### ローカル起動

Windows では、以下を実行するのが簡単です。
初回起動時に必要な依存関係も自動でインストールされます。

```bat
cd 2026_SolutionDeployment_Eval\vanning_eval
launch.bat
```

起動後、ブラウザで以下を開きます。

```text
http://localhost:8502
```

手動で起動する場合は、先に依存関係をインストールします。

```bash
cd 2026_SolutionDeployment_Eval/vanning_eval
python -m pip install -e ".[viewer]"
python main.py
```

手動起動の場合の標準ポートは以下です。

```text
http://localhost:8501
```

ポートを指定する場合:

```bash
python main.py --port 8502
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
python -m pip install -e .
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
