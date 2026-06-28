# input

このフォルダには、評価したい入力・出力ペアを置きます。

## 配置例

```text
input/
└─ taiga/
   ├─ layout_result.json
   └─ items_input.json
```

- `layout_result.json` は必須です。
- `items_input.json` は完全な配置チェックを行う場合に必要です。
- `layout_result.json` が無いフォルダは評価対象になりません。

## 公式入力

`official_` で始まるフォルダは、正規の評価用入力として扱います。

```text
input/
└─ official_case_01_seed42/
   └─ items_input.json
```

通常の動作確認では、`input/taiga/` のような名前でフォルダを作成して使います。
