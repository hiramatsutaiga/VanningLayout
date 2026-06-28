# taiga 配置アルゴリズム

このフォルダには、taiga が作成した配置アルゴリズムと実行結果を格納しています。

## 構成

```text
taiga/
├─ algorithm.py              # 配置アルゴリズム本体
├─ generate_items_json.py    # 標準入力生成スクリプト
├─ items_input.json          # 標準入力データ
├─ layout_result.json        # 標準入力に対する出力
├─ standard_run/             # 標準入力の実行結果一式
└─ out_hard/                 # 敵対的データ 12 件への出力
```

## 標準入力の成果物

```text
standard_run/
├─ items_input.json
├─ layout_result.json
├─ layout_result_tmp.json
├─ layout_result_compact.json
└─ profile.out
```

`layout_result.json` が評価・投稿用の最終出力です。
`layout_result_tmp.json`、`layout_result_compact.json`、`profile.out` は実行過程の確認用成果物です。

## 敵対的データの成果物

```text
out_hard/
├─ layout_01.json
├─ layout_02.json
├─ ...
└─ layout_12.json
```

対応する入力:

```text
../adv_hard_v2cmaes/hard_01.json  ->  out_hard/layout_01.json
...
../adv_hard_v2cmaes/hard_12.json  ->  out_hard/layout_12.json
```

## 実行例

標準入力:

```bash
python taiga/algorithm.py --input taiga/items_input.json --output taiga/layout_result.json --team-name taiga
```

敵対的データ 1 件:

```bash
python taiga/algorithm.py --input adv_hard_v2cmaes/hard_01.json --output taiga/out_hard/layout_01.json --team-name taiga
```

## アルゴリズムの特徴

- 配送先ごとにコンテナを分け、混載を避ける
- はみ出し、重なり、接地、重量、重心制約を確認する
- 0 度 / 90 度回転を試す
- 標準入力では棚型配置を使い、規則的なサイズに強い
- 敵対的データでは候補点 Best Fit により配置する
- 配置後に Y 軸重心を中央に近づける調整を行う

標準入力では安定した結果が出やすい一方、敵対的データのように寸法パターンが多い入力では、上位アルゴリズムよりコンテナ数が増えやすい傾向があります。
