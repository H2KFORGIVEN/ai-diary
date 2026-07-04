#!/usr/bin/env python3
"""
buffer.py — ai-diary — AI Character Diary System
輕量事件 buffer：當日所有情緒觸發事件的 append-only 暫存區

設計原則：
  - 不存原始留言，只存「觸發了情緒的瞬間」
  - 一行一個 JSON，方便 append 和串流處理
  - consolidate.py 每天跑完後清空

用法：
  # 從程式碼呼叫
  from buffer import append_event
  append_event("觀眾集體說草草草", intensity=7, tags=["實況","觀眾"], emotion="開心", meta={"msg_count": 142})

  # CLI append
  python src/buffer.py append --event "boss 打死了" --intensity 8 --tags 遊戲 委屈

  # 查看今日 buffer
  python src/buffer.py show

  # 清空（consolidate 後自動呼叫）
  python src/buffer.py clear
"""

import argparse
import datetime
import json
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent
BUFFER_PATH = ROOT / "diary" / "raw_buffer.jsonl"


def append_event(
    event: str,
    intensity: int,
    tags: list[str],
    emotion: str = "",
    emotion_context: str = "",
    meta: dict | None = None,
    timestamp: str | None = None,
) -> dict:
    """
    寫入一個情緒觸發事件到 buffer。

    Parameters
    ----------
    event           : 事件描述（不是原始留言，是「發生了什麼」）
    intensity       : 情緒強度 1-10
    tags            : tag 清單
    emotion         : 主要情緒（開心 / 委屈 / 興奮 等）
    emotion_context : 感情フィルターのコンテキストヒント
                      （self_failure / external / injustice /
                        integrity_violation / harm_to_master /
                        positive / negative / trust）
    meta            : 額外資訊（如 msg_count、觀眾名、工具名稱等）
    timestamp       : ISO 時間字串（預設現在）
    """
    if timestamp is None:
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")

    entry = {
        "t":         timestamp,
        "event":     event,
        "intensity": max(1, min(10, intensity)),
        "emotion":   emotion,
        "tags":      tags,
    }
    if emotion_context:
        entry["emotion_context"] = emotion_context
    if meta:
        entry["meta"] = meta

    BUFFER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with BUFFER_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── persona-engine FIFO push（非阻塞；daemon 若未啟動就靜默）────────
    _push_to_persona(entry)

    return entry


def _push_to_persona(entry: dict) -> None:
    """把事件即時推給 persona daemon（FIFO，非阻塞）。
    daemon 未啟動時 fallback 寫 spool，daemon 重啟後自動補讀——不影響 buffer 本體。
    """
    # 迴圈斷點：self_outreach 事件不回授（防止自發開口被自己再次點燃）
    if "self_outreach" in (entry.get("tags") or []):
        return

    # 先在 try 外構建 payload，讓 fallback except 也能取到
    _valence = 0
    try:
        from emotion_filter import classify_emotion, emotion_to_valence
        _emo = entry.get("emotion", "")
        if _emo:
            _cat, _sub = classify_emotion(_emo)
            _valence = emotion_to_valence(_cat, _sub, int(entry.get("intensity", 5)))
    except Exception:
        _valence = 0

    payload = json.dumps({
        "text":      entry.get("event", ""),
        "valence":   _valence,                # 由 buffer emotion 經 emotion_filter 映射
        "arousal":   float(entry.get("intensity", 5)),
        "intensity": float(entry.get("intensity", 5)),
        "tags":      entry.get("tags", []),
    }, ensure_ascii=False)

    # FIFO push（O_WRONLY | O_NONBLOCK：沒有 reader 時立即返回 ENXIO，不掛住）
    # 絕對路徑：buffer.py 從 hermes session 執行，$HOME = profile home ≠ 主様 HOME
    _PERSONA_STATE = Path("/Users/showmaker/Projects/persona-engine/state")
    try:
        import os
        fifo_path = _PERSONA_STATE / "events.fifo"
        if not fifo_path.exists():
            raise FileNotFoundError("fifo not found")
        fd = os.open(str(fifo_path), os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, (payload + "\n").encode())
        os.close(fd)
    except Exception:
        # daemon 未啟動、FIFO 不存在、ENXIO — fallback 寫 spool（daemon 重啟後補讀）
        # FIFO 寫成功時不寫 spool（fallback-only → 讀端免去重）
        try:
            spool = _PERSONA_STATE / "events_spool.jsonl"
            with open(str(spool), "a", encoding="utf-8") as _f:
                _f.write(payload + "\n")
        except Exception:
            pass  # spool 寫失敗也靜默，不影響 buffer 本體


def load_buffer() -> list[dict]:
    """載入今日 buffer 所有事件"""
    if not BUFFER_PATH.exists():
        return []
    entries = []
    for line in BUFFER_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _unique_archive_path(archive_dir: Path, today: str) -> Path:
    """
    產生不會覆寫既有檔案的 archive 路徑。
    同一天第一次呼叫回傳 {today}_buffer.jsonl；
    若當天已存在（例如同日 consolidate 補跑兩次），改用 -2 / -3 … 序號後綴，
    避免覆寫先前的 archive 內容。
    """
    base = archive_dir / f"{today}_buffer.jsonl"
    if not base.exists():
        return base
    n = 2
    while True:
        cand = archive_dir / f"{today}_buffer-{n}.jsonl"
        if not cand.exists():
            return cand
        n += 1


def clear_buffer(archive: bool = True):
    """
    清空 buffer。
    archive=True 時先備份到 diary/archive/YYYY-MM-DD_buffer.jsonl（同日重複執行時序號遞增，不覆寫）。
    """
    if not BUFFER_PATH.exists():
        return

    if archive:
        today = datetime.date.today().isoformat()
        archive_dir = ROOT / "diary" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = _unique_archive_path(archive_dir, today)
        archive_path.write_bytes(BUFFER_PATH.read_bytes())

    BUFFER_PATH.unlink()


def snapshot_buffer() -> Path | None:
    """
    把目前的 raw_buffer.jsonl 原子搬移成 diary/batch-<YYYYmmdd_HHMMSS>.jsonl 快照。

    用途：consolidate.py 鞏固期間，避免「讀取 buffer → 逐篇寫日記 → 清空整個 buffer」
    這段期間新寫入的事件被一併清掉（競態條件）。呼叫此函式後，
    後續 append_event() 會寫進全新的 raw_buffer.jsonl，不受鞏固處理影響。

    os.replace() 在同一檔案系統內是原子操作（POSIX rename(2) 語意），
    不會有「搬到一半」的中間狀態。

    Returns
    -------
    快照檔路徑；若當下沒有 buffer 可搬（檔案不存在）則回傳 None。
    """
    if not BUFFER_PATH.exists():
        return None

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = ROOT / "diary" / f"batch-{ts}.jsonl"
    # 同一秒內重複呼叫的極端情況：加序號避免覆寫
    n = 2
    while snapshot_path.exists():
        snapshot_path = ROOT / "diary" / f"batch-{ts}-{n}.jsonl"
        n += 1

    os.replace(BUFFER_PATH, snapshot_path)
    return snapshot_path


def load_snapshot(snapshot_path: Path) -> list[dict]:
    """載入指定快照檔的所有事件（格式與 load_buffer 相同）"""
    if not snapshot_path.exists():
        return []
    entries = []
    for line in snapshot_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def show_buffer():
    entries = load_buffer()
    if not entries:
        print("📭 今日 buffer 是空的")
        return

    print(f"\n📋 今日 buffer（{len(entries)} 則事件）\n")
    for e in entries:
        stars = "★" * e["intensity"] + "☆" * (10 - e["intensity"])
        tags = " ".join(f"#{t}" for t in e.get("tags", []))
        meta = e.get("meta", {})
        meta_str = f"  ({', '.join(f'{k}={v}' for k,v in meta.items())})" if meta else ""
        print(f"  [{e['t'][11:16]}] {stars} {e['emotion']} | {e['event']}{meta_str}")
        if tags:
            print(f"         {tags}")
    print()


def main():
    parser = argparse.ArgumentParser(description="AI Diary Buffer Tool")
    sub = parser.add_subparsers(dest="cmd")

    # append
    a = sub.add_parser("append", help="新增事件到 buffer")
    a.add_argument("--event", required=True)
    a.add_argument("--intensity", type=int, default=5)
    a.add_argument("--tags", nargs="*", default=[])
    a.add_argument("--emotion", default="")
    a.add_argument("--msg-count", type=int, help="觀眾留言數（實況用）")

    # show
    sub.add_parser("show", help="查看今日 buffer")

    # clear
    c = sub.add_parser("clear", help="清空 buffer")
    c.add_argument("--no-archive", action="store_true", help="不備份直接清空")

    args = parser.parse_args()

    if args.cmd == "append":
        meta = {}
        if hasattr(args, "msg_count") and args.msg_count:
            meta["msg_count"] = args.msg_count
        e = append_event(args.event, args.intensity, args.tags, args.emotion, meta=meta or None)
        print(f"✅ 已記錄：[{e['t'][11:16]}] {e['event']} (強度 {e['intensity']})")

    elif args.cmd == "show":
        show_buffer()

    elif args.cmd == "clear":
        clear_buffer(archive=not args.no_archive)
        print("🗑 Buffer 已清空")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
