"""tool 関数を local-scoreboard モードで end-to-end に呼ぶ smoke。

GitHub には触らず、`VANNING_LOCAL_SCOREBOARD=1` で tmp_path をルートにする。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# server を import すると FastMCP インスタンスが生成されるが、tool 関数自体は
# 通常の Python 関数として直接呼び出せる（FastMCP decorator は wrap しない）。
from vanning_eval_mcp import server


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
_ITEM = {
    "item_id": "P001",
    "size_type": "small",
    "dimensions": {"w": 760, "l": 1130, "h": 550},
    "weight": 100.0,
    "destination_id": "A",
}


def _items_doc() -> dict[str, Any]:
    return {
        "dataset_info": {"dataset_name": "case_01", "seed": 42, "item_count": 1},
        "items": [dict(_ITEM)],
    }


def _layout_doc() -> dict[str, Any]:
    return {
        "project_info": {"team_name": "smoke", "execution_time_ms": 10},
        "containers": [
            {
                "container_id": 1,
                "destination_id": "A",
                "total_weight": 100.0,
                "items": [
                    {
                        "item_id": "P001",
                        "size_type": "small",
                        "dimensions": {"w": 760, "l": 1130, "h": 550},
                        "position": {"x": 0, "y": 5435, "z": 0},
                        "weight": 100.0,
                        "is_rotated": False,
                        "destination_id": "A",
                    }
                ],
            }
        ],
    }


@pytest.fixture
def local_env(tmp_path: Path, monkeypatch):
    """local-scoreboard モード + ダミー env + tmp/input を準備し、server を fresh ロード。"""
    # input/official_* を tmp に置いて canonical registry を構築
    official = tmp_path / "input" / "official_case_01_seed42"
    official.mkdir(parents=True)
    (official / "items_input.json").write_text(
        json.dumps(_items_doc()), encoding="utf-8"
    )

    monkeypatch.setenv("VANNING_LOCAL_SCOREBOARD", "1")
    monkeypatch.setenv("VANNING_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setenv("VANNING_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("VANNING_INPUT_ROOT", str(tmp_path / "input"))
    monkeypatch.setenv("VANNING_GITHUB_TOKEN", "dummy")
    monkeypatch.setenv("VANNING_GITHUB_OWNER", "test-owner")
    monkeypatch.setenv("VANNING_GITHUB_REPO", "test-repo")
    monkeypatch.setenv("VANNING_SCOREBOARD_PATH", "scoreboard/history.json")
    monkeypatch.setenv("VANNING_SCOREBOARD_BRANCH", "main")

    # server モジュールの caching を強制リセット
    monkeypatch.setattr(server, "_CFG", None)
    monkeypatch.setattr(server, "_REGISTRY_CACHE", None)

    return tmp_path


def _write_pair(root: Path) -> tuple[Path, Path]:
    d = root / "submission_A"
    d.mkdir()
    layout = d / "layout_result.json"
    items = d / "items_input.json"
    layout.write_text(json.dumps(_layout_doc()), encoding="utf-8")
    items.write_text(json.dumps(_items_doc()), encoding="utf-8")
    return layout, items


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_score_dry_run_returns_pass(local_env: Path):
    layout, items = _write_pair(local_env)
    result = server.vanning_score_dry_run(str(layout), str(items))
    assert result["verdict"] == "pass"
    assert result["gate_status"] == "ok"  # canonical registry 一致
    assert result["score"]["containers_used"] == 1
    assert result["execution_time_ms"] == 10


def test_score_dry_run_auto_pair(local_env: Path):
    """items_path 省略時に同ディレクトリ items_input.json を拾う。"""
    layout, _ = _write_pair(local_env)
    result = server.vanning_score_dry_run(str(layout))
    assert result["verdict"] == "pass"
    assert result["items_path"] is not None


def test_submit_then_skip_then_list_then_hide(local_env: Path):
    layout, items = _write_pair(local_env)

    # 1. dry_run preview
    preview = server.vanning_submit(
        layout_path=str(layout),
        author="smoke",
        items_path=str(items),
        dry_run=True,
    )
    assert preview["status"] == "preview"
    assert preview["preview_score"]["verdict"] == "pass"

    # 2. 本番 submit
    result = server.vanning_submit(
        layout_path=str(layout),
        author="smoke",
        items_path=str(items),
        note="first run",
    )
    assert result["status"] == "submitted"
    entry_id = result["entry_id"]
    assert isinstance(entry_id, str) and entry_id
    # [mcp] 自動 prefix 確認
    assert result["note"].startswith("[mcp]")
    # local fs に history.json が書かれている
    history_file = local_env / "scoreboard" / "history.json"
    assert history_file.exists()
    history = json.loads(history_file.read_text(encoding="utf-8"))
    assert len(history) == 1
    assert history[0]["id"] == entry_id
    # submit したファイルが repo_root 配下に mirror されている
    # (後段 apply_canonical_gate が読みに行く path と一致することが要件)
    layout_rel = result["files"]["layout_result"]["path"]
    items_rel = result["files"]["items_input"]["path"]
    assert (local_env / layout_rel).exists()
    assert (local_env / items_rel).exists()

    # 3. 同じ入力で再 submit → skip
    again = server.vanning_submit(
        layout_path=str(layout),
        author="smoke",
        items_path=str(items),
    )
    assert again["status"] == "skipped"
    assert again["existing_entry_id"] == entry_id

    # 4. list で 1 件取得
    listed = server.vanning_list_submissions(limit=10)
    assert listed["total"] == 1
    assert listed["shown"] == 1
    assert listed["entries"][0]["id"] == entry_id
    assert listed["entries"][0]["verdict"] == "pass"

    # 5. hide
    hide = server.vanning_hide_submission(entry_id=entry_id, hidden=True)
    assert hide["status"] == "ok" and hide["hidden"] is True
    # 公開リストからは消える、hidden=True 指定で取れる
    visible = server.vanning_list_submissions(limit=10)
    assert visible["filtered"] == 0
    hidden_only = server.vanning_list_submissions(limit=10, hidden=True)
    assert hidden_only["filtered"] == 1

    # 6. unhide で復元
    unhide = server.vanning_hide_submission(entry_id=entry_id, hidden=False)
    assert unhide["hidden"] is False
    restored = server.vanning_list_submissions(limit=10)
    assert restored["filtered"] == 1


def test_submit_batch_dry_run(local_env: Path):
    """ディレクトリ受け渡しで paired を auto-glob し、dry_run preview を返す。"""
    layout, items = _write_pair(local_env)
    # 別 dir に 2 件目を作る
    other = local_env / "submission_B"
    other.mkdir()
    layout2 = other / "layout_result.json"
    items2 = other / "items_input.json"
    doc = _layout_doc()
    doc["project_info"]["execution_time_ms"] = 20
    layout2.write_text(json.dumps(doc), encoding="utf-8")
    items2.write_text(json.dumps(_items_doc()), encoding="utf-8")

    result = server.vanning_submit_batch(
        paths=[str(local_env / "submission_A"), str(local_env / "submission_B")],
        author="batch_smoke",
        dry_run=True,
    )
    assert result["status"] == "preview"
    assert result["total"] == 2
    assert result["to_submit"] == 2
    assert result["to_skip"] == 0
    # 各 preview に preview_score が付いている
    for item in result["items"]:
        assert item["status"] == "submit"
        assert item["preview_score"]["verdict"] == "pass"


def test_submit_batch_real_then_partial_dedup(local_env: Path):
    """本番 batch 提出後、追加 1 件のみの batch が「2 件中 1 件 skip + 1 件 submit」になる。"""
    layout, _ = _write_pair(local_env)
    # まず submission_A だけ本番投入
    server.vanning_submit_batch(
        paths=[str(local_env / "submission_A")],
        author="batch_smoke",
        dry_run=False,
    )
    # 次に submission_B を追加して 2 件 batch
    other = local_env / "submission_B"
    other.mkdir()
    doc = _layout_doc()
    doc["project_info"]["execution_time_ms"] = 33
    (other / "layout_result.json").write_text(json.dumps(doc), encoding="utf-8")
    (other / "items_input.json").write_text(json.dumps(_items_doc()), encoding="utf-8")

    result = server.vanning_submit_batch(
        paths=[
            str(local_env / "submission_A"),
            str(local_env / "submission_B"),
        ],
        author="batch_smoke",
        dry_run=False,
        note_template="{layout_filename} verdict={verdict}",
    )
    assert result["status"] == "submitted"
    assert result["to_submit"] == 1
    assert result["to_skip"] == 1
    assert result["submitted_count"] == 1
    # note_template が展開され、[mcp] prefix も付いている
    submitted_items = [it for it in result["items"] if it.get("status") == "submitted"]
    assert len(submitted_items) == 1
    assert submitted_items[0]["note"].startswith("[mcp]")
    assert "verdict=pass" in submitted_items[0]["note"]


def test_score_dry_run_no_items_returns_disqualified(local_env: Path):
    """items_path 省略 + paired も無い場合は verdict='disqualified' で、
    本番 submit のダミー report と同じ verdict を返す（H-2）。"""
    # paired を作らずに layout 単独を置く
    d = local_env / "lone"
    d.mkdir()
    layout = d / "layout_result.json"
    layout.write_text(json.dumps(_layout_doc()), encoding="utf-8")

    result = server.vanning_score_dry_run(str(layout))
    assert result["verdict"] == "disqualified"
    assert result["gate_status"] == "no_items_input"
    assert result["score"] is None
    assert result["items_path"] is None


def test_submit_batch_internal_dedup_same_layout_twice(local_env: Path):
    """batch 内に同 layout が 2 件含まれる場合、2 件目は batch_dedup_skipped で skipped (C-2)。"""
    layout, items = _write_pair(local_env)
    # 2 件目の dir も同じ中身を置く
    other = local_env / "submission_dup"
    other.mkdir()
    (other / "layout_result.json").write_text(layout.read_text(encoding="utf-8"), encoding="utf-8")
    (other / "items_input.json").write_text(items.read_text(encoding="utf-8"), encoding="utf-8")

    result = server.vanning_submit_batch(
        paths=[str(local_env / "submission_A"), str(local_env / "submission_dup")],
        author="dedup_test",
        dry_run=False,
    )
    assert result["status"] == "submitted"
    # 2 件とも初期 decide では submit 判定（history 空、layout sha 同じだが片方しかまだ history に居ない）
    # 1 件目 submit 成功 → ローカル history に追加 → 2 件目の fresh decide で skip 化
    assert result["submitted_count"] == 1
    assert result["batch_dedup_skipped"] == 1


def test_submit_batch_partial_failure_continues(local_env: Path, monkeypatch):
    """1 件目の submit が失敗しても 2 件目以降は実行を継続する（高優先度の path カバー）。"""
    layout1, items1 = _write_pair(local_env)
    other = local_env / "submission_B"
    other.mkdir()
    doc2 = _layout_doc()
    doc2["project_info"]["execution_time_ms"] = 22
    (other / "layout_result.json").write_text(json.dumps(doc2), encoding="utf-8")
    (other / "items_input.json").write_text(json.dumps(_items_doc()), encoding="utf-8")

    # _submit_one を最初の 1 回だけ例外、2 回目以降は本物を呼ぶ
    real_submit_one = server._submit_one
    state = {"n": 0}

    def fake_submit_one(*args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("simulated upload failure")
        return real_submit_one(*args, **kwargs)

    monkeypatch.setattr(server, "_submit_one", fake_submit_one)

    result = server.vanning_submit_batch(
        paths=[
            str(local_env / "submission_A"),
            str(local_env / "submission_B"),
        ],
        author="partial_fail",
        dry_run=False,
    )
    # 1 件失敗、1 件成功
    assert result["status"] == "submitted"
    assert result["submitted_count"] == 1
    errored = [it for it in result["items"] if it.get("status") == "error"]
    submitted = [it for it in result["items"] if it.get("status") == "submitted"]
    assert len(errored) == 1
    assert len(submitted) == 1
    assert "simulated upload failure" in errored[0].get("reason", "")


def test_append_entry_retries_on_409_conflict(local_env: Path, monkeypatch):
    """409 Conflict が 1 回返ったあと成功するなら、retry helper が拾って commit する（C-1）。"""
    from vanning_viewer import scoreboard_client as sc_mod

    layout, items = _write_pair(local_env)

    # 元 append_entry を保持し、最初の呼び出しだけ 409 を投げる
    real_append = sc_mod.append_entry
    state = {"n": 0}

    def flaky_append(cfg, entry, commit_message):
        state["n"] += 1
        if state["n"] == 1:
            raise sc_mod.ScoreboardError("PUT contents failed: 409 Conflict (simulated)")
        return real_append(cfg, entry, commit_message=commit_message)

    # server.py が import 済の名前を差し替える（モジュール内参照を patch）
    monkeypatch.setattr(server, "append_entry", flaky_append)
    # sleep を短縮してテスト時間を切り詰める
    monkeypatch.setattr(server.time, "sleep", lambda _: None)

    result = server.vanning_submit(
        layout_path=str(layout),
        author="retry_test",
        items_path=str(items),
    )
    assert result["status"] == "submitted"
    # 1 回失敗 + 1 回成功 = 2 回呼ばれた
    assert state["n"] == 2


def test_local_mirror_writes_when_remote_put_is_noop(local_env: Path, monkeypatch):
    """GitHub PUT 経路 (= local FS を触らない put_file) でも、_mirror_to_local が
    `<repo_root>/<rel_path>` にファイルを置き、後段 apply_canonical_gate が
    読みに行くパスを成立させる。

    Regression: 2026-05-22 a73da046 / hard_01..12 全件 schema_error → DQ。
    """
    from vanning_viewer import scoreboard_client as sc_mod

    layout, items = _write_pair(local_env)

    # put_file を「remote には書く想定で local FS には触らない」ダミーに差し替え
    # (本番 GitHub 経路の挙動再現)。get_file_sha も「remote 未存在」を返させて
    # upload_if_absent に put を発火させる。
    monkeypatch.setattr(sc_mod, "put_file", lambda *a, **kw: {"content": {}, "commit": {}})
    monkeypatch.setattr(sc_mod, "get_file_sha", lambda cfg, path: None)

    # submit 前に submissions/ ディレクトリが空であることを確認
    sub_dir = local_env / "scoreboard" / "submissions"
    pre_files = set(sub_dir.glob("*.json")) if sub_dir.exists() else set()

    result = server.vanning_submit(
        layout_path=str(layout),
        author="mirror_test",
        items_path=str(items),
    )
    assert result["status"] == "submitted"

    # ファイルは local mirror 経路でのみ作成されたはず
    layout_rel = result["files"]["layout_result"]["path"]
    items_rel = result["files"]["items_input"]["path"]
    layout_local = local_env / layout_rel
    items_local = local_env / items_rel
    assert layout_local.exists(), (
        f"local mirror missing: {layout_local} (regression: gate will schema_error)"
    )
    assert items_local.exists(), f"local mirror missing: {items_local}"
    # 内容が submit したものと一致
    assert layout_local.read_bytes() == layout.read_bytes()
    assert items_local.read_bytes() == items.read_bytes()
    # 新規 2 件 (layout/items) が増えた
    post_files = set(sub_dir.glob("*.json"))
    assert len(post_files - pre_files) == 2


def test_append_entry_gives_up_after_max_retries(local_env: Path, monkeypatch):
    """全リトライ失敗時は ScoreboardError が再 raise され、batch では status='error' に落ちる。"""
    from vanning_viewer import scoreboard_client as sc_mod

    layout, items = _write_pair(local_env)

    def always_conflict(cfg, entry, commit_message):
        raise sc_mod.ScoreboardError("PUT contents failed: 409 Conflict")

    monkeypatch.setattr(server, "append_entry", always_conflict)
    monkeypatch.setattr(server.time, "sleep", lambda _: None)

    # vanning_submit は ScoreboardError を batch try/except の外で起こすので、例外として伝搬
    import pytest as _pytest

    with _pytest.raises(sc_mod.ScoreboardError):
        server.vanning_submit(
            layout_path=str(layout),
            author="retry_giveup",
            items_path=str(items),
        )


def test_submit_without_author_returns_error(local_env: Path):
    """author 引数も VANNING_DEFAULT_AUTHOR env も無い時、誤投稿防止のため error を返す。"""
    layout, items = _write_pair(local_env)
    result = server.vanning_submit(layout_path=str(layout), items_path=str(items))
    assert result["status"] == "error"
    assert "VANNING_DEFAULT_AUTHOR" in result["reason"]


def test_submit_batch_without_author_returns_error(local_env: Path):
    """batch 側も同じく author 未設定でエラー終了 (GitHub に何も投げない)。"""
    layout, _ = _write_pair(local_env)
    result = server.vanning_submit_batch(paths=[str(layout)])
    assert result["status"] == "error"
    assert "VANNING_DEFAULT_AUTHOR" in result["reason"]


def test_submit_uses_default_author_from_env(local_env: Path, monkeypatch):
    """author 省略時、VANNING_DEFAULT_AUTHOR env が author として使われる。"""
    monkeypatch.setenv("VANNING_DEFAULT_AUTHOR", "env_user")
    monkeypatch.setattr(server, "_CFG", None)  # cfg を再ロードさせる

    layout, items = _write_pair(local_env)
    result = server.vanning_submit(layout_path=str(layout), items_path=str(items))
    assert result["status"] == "submitted"
    # local-scoreboard モードで書かれた history.json を読み返して author を確認
    import json
    hist = json.loads((local_env / "scoreboard" / "history.json").read_text(encoding="utf-8"))
    assert hist[-1]["author"] == "env_user"
