#!/usr/bin/env python3
"""
consolidate.py — ai-diary — AI Character Diary System
每日記憶鞏固：模擬人類睡眠期間的海馬迴 Memory Consolidation

流程：
  1. 讀取 raw_buffer.jsonl
  2. 依強度過濾 + 聚合
  3. 生成 1-5 篇有「空氣感」的 diary entries
  4. 清空 buffer（備份至 archive/）

強度過濾規則：
  intensity 1-3  → 丟棄（日常雜訊，人類也不記得）
  intensity 4-6  → 按相近 tags 聚合，合成一篇「整體印象」entry
  intensity 7-9  → 各自獨立成一篇 entry
  intensity 10   → 獨立 entry + flashbulb: true

用法：
  python src/consolidate.py             # 處理今日 buffer
  python src/consolidate.py --dry-run   # 只顯示會產生什麼，不實際寫入
  python src/consolidate.py --date 2026-05-08  # 指定日期（搭配 archive）
"""

import argparse
import datetime
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
DIARY_ROOT = ROOT / "diary"

# 動態 import（避免在 write_diary 裡重複定義路徑）
sys.path.insert(0, str(ROOT / "src"))
from buffer import load_buffer, clear_buffer
from write_diary import write_entry
from emotion_filter import apply_filter


# ── 閾值設定 ─────────────────────────────────────────
DISCARD_THRESHOLD = 3    # ≤ 3：丟棄
MERGE_THRESHOLD   = 6    # 4-6：聚合
# ≥ 7：獨立 entry
FLASHBULB_THRESHOLD = 10


def apply_emotion_filter_to_events(events: list[dict]) -> list[dict]:
    """
    buffer 事件清單全部跑過感情フィルター。
    各 event に filtered_emotion / filtered_intensity / filter_tags / filter_note を追加して返す。
    """
    filtered = []
    for e in events:
        raw_emotion  = e.get("emotion", "")
        raw_intensity = e.get("intensity", 5)
        context      = e.get("emotion_context", "")  # buffer に任意付与可能
        extra_tags   = e.get("tags", [])

        result = apply_filter(
            emotion=raw_emotion,
            intensity=raw_intensity,
            context=context,
            extra_tags=extra_tags,
        )

        ne = dict(e)
        ne["filtered_emotion"]    = result["emotion"]
        ne["filtered_intensity"]  = result["intensity"]
        ne["filter_tags"]         = result["tags"]
        ne["filter_note"]         = result["note"]
        ne["filter_flashbulb"]    = result["flashbulb"]
        filtered.append(ne)
    return filtered


def discard_noise(events: list[dict]) -> tuple[list[dict], list[dict]]:
    """回傳 (保留, 丟棄)——使用 filtered_intensity（フィルター補正後の強度）"""
    keep = [e for e in events if e.get("filtered_intensity", e["intensity"]) > DISCARD_THRESHOLD]
    drop = [e for e in events if e.get("filtered_intensity", e["intensity"]) <= DISCARD_THRESHOLD]
    return keep, drop


def group_medium(events: list[dict]) -> list[list[dict]]:
    """
    強度 4-6 的事件：依 tags 相似度分群聚合。
    簡單策略：找最常見的 shared tag 做分桶。
    """
    medium = [e for e in events if DISCARD_THRESHOLD < e["intensity"] <= MERGE_THRESHOLD]
    if not medium:
        return []

    # 以最常出現的 tag 為 key 分桶
    buckets: dict[str, list[dict]] = defaultdict(list)
    untagged = []
    for e in medium:
        if e.get("tags"):
            buckets[e["tags"][0]].append(e)
        else:
            untagged.append(e)

    groups = list(buckets.values())
    if untagged:
        groups.append(untagged)
    return groups


def synthesize_merged_entry(group: list[dict], date: datetime.date) -> dict:
    """把一組中等強度事件合成一篇『整體印象』entry（使用 filtered 值）"""
    avg_intensity = round(sum(e.get("filtered_intensity", e["intensity"]) for e in group) / len(group))

    # 收集所有 tags（filter_tags 優先，去重）
    all_tags: list[str] = []
    seen = set()
    for e in group:
        for t in e.get("filter_tags", e.get("tags", [])):
            if t not in seen:
                all_tags.append(t)
                seen.add(t)

    # 主要情緒（filtered_emotion 優先）
    emotions = [e.get("filtered_emotion", e.get("emotion", "")) for e in group if e.get("filtered_emotion") or e.get("emotion")]
    main_emotion = max(set(emotions), key=emotions.count) if emotions else ""

    # 產生 title
    if len(group) == 1:
        title = group[0]["event"]
    else:
        title = f"今天的{all_tags[0] if all_tags else '片段'}——{len(group)} 個小時刻"

    # 產生 body（把每個事件整理成一段話）
    body_lines = [f"今天有幾個時刻留下了印象——\n"]
    for e in group:
        t = e["t"][11:16] if len(e["t"]) > 10 else e["t"]
        meta = e.get("meta", {})
        meta_str = ""
        if meta.get("msg_count"):
            meta_str = f"（{meta['msg_count']} 則留言的反應）"
        body_lines.append(f"- {t} {e['event']}{meta_str}")

    if main_emotion:
        body_lines.append(f"\n大致上是{main_emotion}的感覺。")
    body_lines.append("\n細節不記得了，但那個氛圍留下來了。")

    return {
        "title": title,
        "body": "\n".join(body_lines),
        "tags": all_tags,
        "intensity": avg_intensity,
        "flashbulb": False,
        "date": date,
    }


def synthesize_strong_entry(event: dict, date: datetime.date) -> dict:
    """強度 7-9 の事件：獨立成篇（filtered 値を使用）"""
    t = event["t"][11:16] if len(event["t"]) > 10 else event["t"]
    meta = event.get("meta", {})
    meta_str = ""
    if meta.get("msg_count"):
        meta_str = f"\n\n那個瞬間聊天室爆了 {meta['msg_count']} 則留言。"

    body = f"{t}——{event['event']}{meta_str}\n\n"

    # filtered_emotion 優先
    emotion = event.get("filtered_emotion", event.get("emotion", ""))
    if emotion:
        body += f"情緒是{emotion}。"

    # フィルターノートを添付（デバッグ・振り返り用）
    filter_note = event.get("filter_note", "")
    if filter_note:
        body += f"\n\n<!-- filter: {filter_note} -->"

    body += "\n\n這個時刻值得記住。"

    return {
        "title":     event["event"],
        "body":      body,
        "tags":      event.get("filter_tags", event.get("tags", [])),
        "intensity": event.get("filtered_intensity", event["intensity"]),
        "flashbulb": event.get("filter_flashbulb", False),
        "date":      date,
    }


def synthesize_flashbulb_entry(event: dict, date: datetime.date) -> dict:
    """強度 10 或 filter_flashbulb=True の事件：閃光燈記憶（filtered 値を使用）"""
    t = event["t"][11:16] if len(event["t"]) > 10 else event["t"]
    meta = event.get("meta", {})

    body = f"{t}——\n\n{event['event']}\n\n"
    if meta:
        for k, v in meta.items():
            body += f"{k}: {v}\n"

    filter_note = event.get("filter_note", "")
    if filter_note:
        body += f"\n<!-- filter: {filter_note} -->"

    body += "\n\n這一刻，うち永遠記得。"

    return {
        "title":          event["event"],
        "body":           body,
        "tags":           event.get("filter_tags", event.get("tags", [])) + ["milestone"],
        "intensity":      event.get("filtered_intensity", event["intensity"]),
        "flashbulb":      True,
        "first_reaction": event.get("filtered_emotion", event.get("emotion", "")),
        "date":           date,
    }


def consolidate(
    date: datetime.date | None = None,
    dry_run: bool = False,
    archive_path: Path | None = None,
) -> list[Path]:
    """
    主流程：buffer → diary entries
    回傳寫入的檔案路徑清單
    """
    if archive_path:
        # 從指定 archive 讀取（--date 模式）
        if not archive_path.exists():
            print(f"❌ 找不到 archive：{archive_path}")
            return []
        events = []
        for line in archive_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    else:
        events = load_buffer()

    if not events:
        print("📭 今日 buffer 是空的，無需 consolidate")
        return []

    if date is None:
        date = datetime.date.today()

    print(f"\n🧠 Memory Consolidation — {date}")
    print(f"   輸入：{len(events)} 個 buffer 事件")

    # Step 0: 感情フィルター適用（SOUL.md × character_emotion_profile）
    events = apply_emotion_filter_to_events(events)
    print(f"   感情フィルター適用完了（anger/disgust suppressed & redirected）")

    # Step 1: 過濾雜訊（使用 filtered_intensity）
    keep, dropped = discard_noise(events)
    print(f"   丟棄 {len(dropped)} 個低強度雜訊（intensity ≤ {DISCARD_THRESHOLD}）")

    # Step 2: 分類（使用 filtered_intensity；filter_flashbulb=True も flashbulb 扱い）
    strong    = [e for e in keep if 7 <= e.get("filtered_intensity", e["intensity"]) < 10
                 and not e.get("filter_flashbulb", False)]
    flashbulb = [e for e in keep if e.get("filtered_intensity", e["intensity"]) >= 10
                 or e.get("filter_flashbulb", False)]
    medium    = [e for e in keep if DISCARD_THRESHOLD < e.get("filtered_intensity", e["intensity"]) <= MERGE_THRESHOLD
                 and not e.get("filter_flashbulb", False)]

    # 生成 entry specs
    entry_specs = []

    for e in flashbulb:
        entry_specs.append(synthesize_flashbulb_entry(e, date))

    for e in strong:
        entry_specs.append(synthesize_strong_entry(e, date))

    for group in group_medium(medium):
        entry_specs.append(synthesize_merged_entry(group, date))

    print(f"   生成：{len(entry_specs)} 篇 diary entries")
    for spec in entry_specs:
        fb = "⚡" if spec.get("flashbulb") else " "
        print(f"   {fb} [{spec['intensity']}/10] {spec['title']}")

    if dry_run:
        print("\n   (dry-run 模式，不寫入檔案)")
        return []

    # Step 3: 寫入 diary
    written = []
    for spec in entry_specs:
        dt = datetime.datetime(
            spec["date"].year,
            spec["date"].month,
            spec["date"].day,
        )
        path = write_entry(
            title=spec["title"],
            body=spec["body"],
            tags=spec["tags"],
            intensity=spec["intensity"],
            flashbulb=spec.get("flashbulb", False),
            first_reaction=spec.get("first_reaction", ""),
            dt=dt,
        )
        written.append(path)

    # Step 4: 清空 buffer
    if not archive_path:
        clear_buffer(archive=True)
        print(f"\n✅ 寫入 {len(written)} 篇，buffer 已清空並備份")
    else:
        print(f"\n✅ 寫入 {len(written)} 篇（archive 模式，不清空 buffer）")

    return written


def main():
    parser = argparse.ArgumentParser(description="AI Diary — Daily Memory Consolidation")
    parser.add_argument("--dry-run", action="store_true", help="預覽，不實際寫入")
    parser.add_argument("--date", type=str, help="YYYY-MM-DD（從 archive 補跑）")
    args = parser.parse_args()

    archive_path = None
    date = None

    if args.date:
        date = datetime.date.fromisoformat(args.date)
        archive_path = DIARY_ROOT / "archive" / f"{args.date}_buffer.jsonl"

    consolidate(date=date, dry_run=args.dry_run, archive_path=archive_path)


if __name__ == "__main__":
    main()
