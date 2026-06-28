# Viewer 設計メモ

`vanning_eval` のビューアは、評価結果を確認するための Web UI です。

## 構成

```text
vanning_eval/src/vanning_viewer/
├─ streamlit_app.py      # Web UI 本体
├─ plotly_renderer.py    # 3D 表示
├─ view_model.py         # 表示用データ変換
├─ submitter.py          # 投稿処理
├─ scoreboard_client.py  # scoreboard 読み書き
└─ colors.py             # 表示色
```

## 役割

- `layout_result.json` をアップロードまたは読み込み
- 評価結果を表示
- コンテナ内の配置を 3D 表示
- 違反内容を確認
- scoreboard に投稿

## 入力例

```text
vanning_eval/input/taiga/
├─ layout_result.json
└─ items_input.json
```

`items_input.json` がある場合は、未配置・重複・未知アイテムも確認できます。
