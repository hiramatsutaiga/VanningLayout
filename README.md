# Vanning Layout Algorithm

## プロジェクト概要

本プロジェクトは、複数種類の積荷を 40ft コンテナへ効率的かつ安全に配置するバンニングレイアウトアルゴリズムを作成し、その結果を評価・可視化するための成果物です。

積荷データを入力として、コンテナ内の配置結果を `layout_result.json` として出力します。出力結果は、評価プラットフォームで制約違反、使用コンテナ数、重心バランス、処理時間などを確認できます。

## フォルダ構成

```text
VanningLayout/
├─ 2026_SolutionDeployment_Eval/   # 評価・可視化・投稿プラットフォーム
├─ taiga/                          # taiga の配置アルゴリズムと出力結果
├─ adv_hard_v2cmaes/               # 敵対的積み荷データ
├─ README.md
└─ 成果物仕様.md
```

## 各フォルダの内容

### 2026_SolutionDeployment_Eval

`layout_result.json` を評価、3D 可視化、投稿するためのプラットフォームです。

- `vanning_eval/`: 評価器、Web UI、3D ビューア
- `submission_MCP/`: MCP による一括投稿
- `scoreboard/`: 投稿履歴・順位表データ

### taiga

taiga の配置アルゴリズムと出力結果です。

- `algorithm.py`: 配置アルゴリズム本体
- `items_input.json`: 標準入力
- `layout_result.json`: 標準入力に対する出力
- `standard_run/`: 標準入力の実行結果一式
- `out_hard/`: 敵対的データ 12 件に対する出力

### adv_hard_v2cmaes

性能検証に使用した敵対的積み荷データです。

```text
hard_01.json ～ hard_12.json
```

## 入出力の対応

標準データ:

```text
taiga/items_input.json
taiga/layout_result.json
```

敵対的データ:

```text
adv_hard_v2cmaes/hard_01.json  ->  taiga/out_hard/layout_01.json
...
adv_hard_v2cmaes/hard_12.json  ->  taiga/out_hard/layout_12.json
```

投稿者名は `taiga` を使用しています。

## 実行例

標準入力を実行する場合:

```bash
python taiga/algorithm.py --input taiga/items_input.json --output taiga/layout_result.json --team-name taiga
```

評価プラットフォームを起動する場合:

```bash
cd 2026_SolutionDeployment_Eval/vanning_eval
python main.py
```
