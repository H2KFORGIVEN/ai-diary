#!/usr/bin/env python3
"""
tests/test_consolidate_race.py — ai-diary 回歸測試集
consolidate 鞏固競態修復驗證（P0）

背景：
  修復前 consolidate() 流程是「讀取 buffer → 逐篇寫日記 → 清空整個 buffer」，
  中間 buffer.append_event() 若新寫入事件，會在最後 clear_buffer() 時被一併清掉。
  同時 clear_buffer 的 archive 檔名只用日期，同日二次 archive 會互相覆寫。

修復：
  (a) buffer.snapshot_buffer()：用 os.replace() 把 raw_buffer.jsonl 原子搬成
      diary/batch-<timestamp>.jsonl 快照；之後的 append_event() 寫進全新的
      raw_buffer.jsonl，不受快照後續處理影響。
  (b) buffer._unique_archive_path()：archive 檔名同日重複時序號遞增，不覆寫。

這裡直接針對這兩個機制做單元測試（不跑 consolidate() 整條 pipeline，
避免牽動 config/persona-engine/vector-index 等外部依賴）。
"""

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def isolated_buffer(tmp_path, monkeypatch):
    """把 buffer.py 的 ROOT/BUFFER_PATH 導向臨時目錄，避免污染真實 diary/"""
    import buffer as buffer_mod

    tmp_root = tmp_path
    (tmp_root / "diary").mkdir(parents=True, exist_ok=True)
    tmp_buffer = tmp_root / "diary" / "raw_buffer.jsonl"

    monkeypatch.setattr(buffer_mod, "ROOT", tmp_root)
    monkeypatch.setattr(buffer_mod, "BUFFER_PATH", tmp_buffer)
    monkeypatch.setattr(buffer_mod, "_push_to_persona", lambda entry: None)
    return buffer_mod


class TestSnapshotBuffer:

    def test_append_during_consolidation_not_lost(self, isolated_buffer):
        """
        核心回歸：鞏固中途 append 的事件不遺失。

        模擬時序：
          1. 寫入事件 A、B 到 buffer
          2. consolidate 開始 → snapshot_buffer()（原子搬移 A、B 到快照）
          3. 「鞏固處理快照期間」有新事件 C 進來（append_event）
          4. 快照處理完畢 → 只刪快照（不動新 buffer）
          5. 檢查：C 仍完整留在新的 raw_buffer.jsonl，沒有被步驟 4 清掉
        """
        isolated_buffer.append_event("事件A", 7, ["tag1"], "開心")
        isolated_buffer.append_event("事件B", 8, ["tag2"], "興奮")

        # Step 2: 模擬 consolidate 開頭的原子快照
        snapshot_path = isolated_buffer.snapshot_buffer()
        assert snapshot_path is not None
        assert snapshot_path.exists()
        assert not isolated_buffer.BUFFER_PATH.exists(), "快照後舊 buffer 應已被搬走"

        snapshot_events = isolated_buffer.load_snapshot(snapshot_path)
        assert len(snapshot_events) == 2
        assert {e["event"] for e in snapshot_events} == {"事件A", "事件B"}

        # Step 3: 鞏固「處理快照期間」新事件 C 寫入（落進全新 buffer）
        isolated_buffer.append_event("事件C（鞏固期間新寫入）", 6, ["tag3"], "平靜")

        # Step 4: 鞏固完成 → 只刪快照，不動新 buffer
        snapshot_path.unlink()

        # Step 5: C 必須還在，且只有 C（A、B 已經在快照裡處理掉了）
        remaining = isolated_buffer.load_buffer()
        assert len(remaining) == 1, "鞏固期間新寫入的事件不應被清掉"
        assert remaining[0]["event"] == "事件C（鞏固期間新寫入）"

    def test_snapshot_buffer_returns_none_when_empty(self, isolated_buffer):
        """buffer 不存在時 snapshot_buffer 回傳 None，不報錯"""
        assert not isolated_buffer.BUFFER_PATH.exists()
        result = isolated_buffer.snapshot_buffer()
        assert result is None

    def test_snapshot_is_atomic_rename_not_copy(self, isolated_buffer):
        """snapshot_buffer 使用 os.replace（rename），確保過程沒有『複製中』的中間態"""
        isolated_buffer.append_event("事件", 5, [], "")
        original_inode = isolated_buffer.BUFFER_PATH.stat().st_ino

        snapshot_path = isolated_buffer.snapshot_buffer()

        # rename 後檔案 inode 應該不變（同一份 inode 換了路徑，而非複製新檔案）
        assert snapshot_path.stat().st_ino == original_inode

    def test_concurrent_append_after_snapshot_never_touches_old_events(self, isolated_buffer):
        """多執行緒併發 append，快照後所有新事件都進新檔，與快照內容互不干擾"""
        isolated_buffer.append_event("鞏固前的事件", 7, [], "開心")
        snapshot_path = isolated_buffer.snapshot_buffer()

        def _worker(i):
            isolated_buffer.append_event(f"併發事件-{i}", 5, [], "")

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        new_buffer_events = isolated_buffer.load_buffer()
        snapshot_events = isolated_buffer.load_snapshot(snapshot_path)

        assert len(snapshot_events) == 1
        assert snapshot_events[0]["event"] == "鞏固前的事件"
        assert len(new_buffer_events) == 10
        assert all(e["event"].startswith("併發事件-") for e in new_buffer_events)


class TestArchiveNoOverwrite:

    def test_same_day_archive_twice_does_not_overwrite(self, isolated_buffer):
        """同一天兩次 archive（clear_buffer）不應互相覆寫，第二次要用序號後綴"""
        archive_dir = isolated_buffer.ROOT / "diary" / "archive"
        today = "2026-07-04"

        first = isolated_buffer._unique_archive_path(archive_dir, today)
        assert first.name == f"{today}_buffer.jsonl"
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_text("batch-1-content\n", encoding="utf-8")

        second = isolated_buffer._unique_archive_path(archive_dir, today)
        assert second != first, "第二次 archive 路徑必須與第一次不同"
        assert second.name == f"{today}_buffer-2.jsonl"
        second.write_text("batch-2-content\n", encoding="utf-8")

        # 兩份檔案都還在，內容互不覆蓋
        assert first.read_text(encoding="utf-8") == "batch-1-content\n"
        assert second.read_text(encoding="utf-8") == "batch-2-content\n"

    def test_clear_buffer_same_day_twice_keeps_both_archives(self, isolated_buffer):
        """透過 clear_buffer() 高階 API 驗證同日兩次不互覆"""
        isolated_buffer.append_event("第一批事件", 6, [], "")
        isolated_buffer.clear_buffer(archive=True)

        isolated_buffer.append_event("第二批事件", 6, [], "")
        isolated_buffer.clear_buffer(archive=True)

        archive_dir = isolated_buffer.ROOT / "diary" / "archive"
        archived_files = sorted(archive_dir.glob("*_buffer*.jsonl"))
        assert len(archived_files) == 2, f"應有兩份 archive 檔，實際: {archived_files}"

        contents = [f.read_text(encoding="utf-8") for f in archived_files]
        assert any("第一批事件" in c for c in contents)
        assert any("第二批事件" in c for c in contents)


class TestConsolidateArchiveSnapshot:
    """consolidate.py 內 _archive_snapshot()（Step 4 使用）的獨立驗證"""

    def test_archive_snapshot_same_day_twice_no_overwrite(self, tmp_path, monkeypatch):
        import consolidate as consolidate_mod

        tmp_diary_root = tmp_path / "diary"
        tmp_diary_root.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(consolidate_mod, "DIARY_ROOT", tmp_diary_root)

        snapshot1 = tmp_path / "batch-20260704_100000.jsonl"
        snapshot1.write_text('{"event": "批次一"}\n', encoding="utf-8")
        snapshot2 = tmp_path / "batch-20260704_180000.jsonl"
        snapshot2.write_text('{"event": "批次二"}\n', encoding="utf-8")

        archived1 = consolidate_mod._archive_snapshot(snapshot1)
        archived2 = consolidate_mod._archive_snapshot(snapshot2)

        assert archived1 != archived2, "同日兩次 archive 快照不應覆寫彼此"
        assert archived1.read_text(encoding="utf-8") == '{"event": "批次一"}\n'
        assert archived2.read_text(encoding="utf-8") == '{"event": "批次二"}\n'
