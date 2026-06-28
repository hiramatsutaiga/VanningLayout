# vanning_eval 開発メモ

このフォルダは、`layout_result.json` を評価・可視化するプラットフォーム本体です。

## 構成

```text
vanning_eval/
├─ main.py
├─ src/
│  ├─ vanning_eval/      # スキーマ、制約チェック、採点、レポート生成
│  └─ vanning_viewer/    # Streamlit UI、3D 表示、投稿処理
├─ input/                # 評価対象を置く場所
├─ output/               # 評価結果の出力先
├─ tests/                # テスト
└─ docs/                 # 設計メモ
```

## 開発時の注意

- 入力は `items_input.json`
- 出力は `layout_result.json`
- 座標系は `x=幅`, `y=奥行/長手`, `z=高さ`
- コンテナサイズは 12000(L) × 2300(W) × 2400(H) mm
- 最大積載重量は 24,000 kg
- Y 軸重心は中心 6000 mm から ±3000 mm 以内
- 順位は、コンテナ数、平均 Y 軸重心ズレ、処理時間の順で決まる

## taiga 成果物との対応

標準データ:

```text
../../taiga/items_input.json
../../taiga/layout_result.json
```

敵対的データ:

```text
../../adv_hard_v2cmaes/hard_01.json
../../taiga/out_hard/layout_01.json
```
