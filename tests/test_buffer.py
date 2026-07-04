#!/usr/bin/env python3
"""
tests/test_buffer.py — ai-diary 回歸測試集
buffer.py CLI 位置參數錯位修復驗證（P0）

テスト対象:
  - buffer.py main()：CLI `append` 分支呼叫 append_event 時
    meta 必須以關鍵字傳參，不可誤落入 emotion_context 位置
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def isolated_buffer(tmp_path, monkeypatch):
    """把 buffer.py 的 BUFFER_PATH 導向臨時檔，避免污染真實 diary/raw_buffer.jsonl"""
    import buffer as buffer_mod

    tmp_buffer = tmp_path / "raw_buffer.jsonl"
    monkeypatch.setattr(buffer_mod, "BUFFER_PATH", tmp_buffer)
    # _push_to_persona 會嘗試寫 FIFO/spool，測試環境不需要，直接 no-op 掉避免副作用
    monkeypatch.setattr(buffer_mod, "_push_to_persona", lambda entry: None)
    return buffer_mod


class TestBufferCLIAppend:

    def test_cli_append_msg_count_goes_to_meta(self, isolated_buffer, monkeypatch, capsys):
        """
        模擬 CLI: python src/buffer.py append --event ... --intensity 7 \
                  --tags 實況 --emotion 開心 --msg-count 5

        修復前：meta dict 被塞進第 5 位置參數 emotion_context，
                導致 entry["emotion_context"] 變成 {"msg_count": 5}（型別錯誤），
                entry 完全沒有 "meta" 欄位。
        修復後：meta=meta or None 以關鍵字傳參，
                entry["meta"]["msg_count"] == 5 且 emotion_context 仍是字串。
        """
        argv = [
            "buffer.py", "append",
            "--event", "觀眾集體說草草草",
            "--intensity", "7",
            "--tags", "實況", "觀眾",
            "--emotion", "開心",
            "--msg-count", "5",
        ]
        monkeypatch.setattr(sys, "argv", argv)

        isolated_buffer.main()

        entries = isolated_buffer.load_buffer()
        assert len(entries) == 1
        entry = entries[0]

        # meta 必須正確落位，且 msg_count 為原始 int 5
        assert "meta" in entry, "meta 欄位遺失 — 位置參數錯位未修復"
        assert entry["meta"] == {"msg_count": 5}
        assert entry["meta"]["msg_count"] == 5

        # emotion_context 未被傳入，理應維持字串型別（空字串則整欄位省略）
        assert isinstance(entry.get("emotion_context", ""), str)
        assert "emotion_context" not in entry or isinstance(entry["emotion_context"], str)

    def test_append_event_meta_keyword_direct(self, isolated_buffer):
        """直接呼叫 append_event 關鍵字版，confirm emotion_context 與 meta 不互相污染"""
        entry = isolated_buffer.append_event(
            "boss 打死了",
            8,
            ["遊戲", "委屈"],
            "委屈",
            emotion_context="self_failure",
            meta={"msg_count": 142},
        )
        assert entry["emotion_context"] == "self_failure"
        assert entry["meta"] == {"msg_count": 142}
