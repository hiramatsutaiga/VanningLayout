# adv_hard_v2cmaes

このフォルダには、性能検証に使用した敵対的積み荷データ 12 件を格納しています。

## 構成

```text
adv_hard_v2cmaes/
├─ hard_01.json
├─ hard_02.json
├─ ...
├─ hard_12.json
├─ v2cmaes_beam_ref.json
└─ v2cmaes_ga_bench.json
```

## 内容

- `hard_01.json` ～ `hard_12.json`
  - 敵対的積み荷データ。
  - 標準入力より寸法パターンが多く、配置の難易度が高い。
- `v2cmaes_beam_ref.json`
  - beam search 系アルゴリズムの参考結果。
- `v2cmaes_ga_bench.json`
  - GA 系アルゴリズムの参考結果。

## taiga 出力との対応

```text
hard_01.json  ->  ../taiga/out_hard/layout_01.json
hard_02.json  ->  ../taiga/out_hard/layout_02.json
...
hard_12.json  ->  ../taiga/out_hard/layout_12.json
```

このデータは、通常の `items_input.json` と同じ形式で評価プラットフォームに入力できます。
