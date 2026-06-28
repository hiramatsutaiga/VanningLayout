# submission_MCP

`layout_result.json` を MCP 経由で投稿するためのサーバです。
複数の出力結果をまとめて投稿したい場合に使用します。

## 構成

```text
submission_MCP/
├─ README.md
├─ pyproject.toml
├─ .mcp.json.example
└─ src/vanning_eval_mcp/
   ├─ server.py
   ├─ config.py
   ├─ paths.py
   └─ dedupe.py
```

## 役割

- `layout_result.json` と `items_input.json` のペアを検出する
- 投稿前にローカル採点する
- content hash により重複投稿を避ける
- scoreboard に投稿結果を反映する

## 主なツール

- `vanning_score_dry_run`: ローカル採点のみ行う
- `vanning_submit`: 1 件投稿する
- `vanning_submit_batch`: 複数件をまとめて投稿する
- `vanning_list_submissions`: 投稿履歴を確認する
- `vanning_hide_submission`: 投稿の表示・非表示を切り替える

## 設定

`.mcp.json.example` を参考に、MCP クライアント側で以下を設定します。

```text
VANNING_GITHUB_TOKEN
VANNING_GITHUB_OWNER
VANNING_GITHUB_REPO
VANNING_REPO_ROOT
VANNING_INPUT_ROOT
VANNING_DEFAULT_AUTHOR
```

投稿者名には `taiga` を使用します。
