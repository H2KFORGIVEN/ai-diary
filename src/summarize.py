#!/usr/bin/env python3
"""
summarize.py — ai-diary — AI Character Diary System
記憶鞏固工具：把過去一週/月的 entries 濃縮成摘要

用法:
  python src/summarize.py --week 2026-W18
  python src/summarize.py --month 2026-05
  python src/summarize.py --auto   # 自動偵測需要摘要的期間
"""

import argparse
import datetime
import re
from pathlib import Path
from collections import Counter

import yaml

ROOT = Path(__file__).parent.parent
DIARY_ROOT = ROOT / "diary"
SUMMARY_DIR = DIARY_ROOT / "summaries"
SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
CONFIG = yaml.safe_load((DIARY_ROOT / "config" / "settings.yaml").read_text())
SKIP_FLASHBULB = CONFIG["summarize"]["skip_flashbulb"]
WEEKLY_THRESHOLD = CONFIG["summarize"].get("weekly_threshold", 3)


def load_entries_in_range(start: datetime.date, end: datetime.date) -> list[dict]:
    entries = []
    for md in sorted(DIARY_ROOT.rglob("*.md")):
        if "summaries" in md.parts or md.name == "self-narrative.md":
            continue
        if md.name.startswith("README"):
            continue
        text = md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        try:
            _, fm_str, body = text.split("---", 2)
            fm = yaml.safe_load(fm_str)
        except Exception:
            continue
        try:
            entry_date = datetime.date.fromisoformat(fm.get("date", ""))
        except Exception:
            continue
        if start <= entry_date <= end:
            entries.append({"path": md, "fm": fm, "body": body.strip()})
    return entries


def build_week_summary(week_str: str) -> str:
    """week_str: YYYY-Www"""
    year, week = int(week_str[:4]), int(week_str[6:])
    start = datetime.date.fromisocalendar(year, week, 1)
    end = start + datetime.timedelta(days=6)

    entries = load_entries_in_range(start, end)
    if not entries:
        return ""

    # 分開 flashbulb vs 普通
    flashbulbs = [e for e in entries if e["fm"].get("flashbulb")]
    normals = [e for e in entries if not e["fm"].get("flashbulb")]

    # 統計 tags
    all_tags = []
    for e in entries:
        all_tags.extend(e["fm"].get("tags", []))
    tag_counts = Counter(all_tags).most_common(8)

    # 平均情緒強度
    intensities = [e["fm"].get("emotional_intensity", 5) for e in entries]
    avg_intensity = round(sum(intensities) / len(intensities), 1)

    lines = [
        f"# 週摘要 {week_str}（{start} ～ {end}）",
        "",
        f"**本週 {len(entries)} 篇日記** | 平均情緒強度：{avg_intensity}/10",
        "",
    ]

    if flashbulbs:
        lines.append("## ⚡ 閃光燈記憶（永久保留）")
        for e in flashbulbs:
            fr = e["fm"].get("first_reaction", "")
            lines.append(f"- **{e['fm']['date']}** {e['fm']['title']}")
            if fr:
                lines.append(f"  > 第一反應：{fr}")
        lines.append("")

    lines.append("## 📅 本週事件")
    for e in sorted(normals, key=lambda x: x["fm"].get("date", "")):
        tags = ", ".join(e["fm"].get("tags", []))
        intensity = e["fm"].get("emotional_intensity", 5)
        preview = e["body"][:120].replace("\n", " ")
        lines.append(f"- **{e['fm']['date']}** [{intensity}/10] {e['fm']['title']}")
        if tags:
            lines.append(f"  🏷 {tags}")
        lines.append(f"  {preview}…")
    lines.append("")

    if tag_counts:
        lines.append("## 🏷 本週關鍵詞")
        lines.append("  " + " · ".join(f"{t}({c})" for t, c in tag_counts))
        lines.append("")

    lines.append("---")
    lines.append(f"*自動生成於 {datetime.date.today()}*")
    return "\n".join(lines)


def build_month_summary(month_str: str) -> str:
    """month_str: YYYY-MM"""
    year, month = int(month_str[:4]), int(month_str[5:])
    start = datetime.date(year, month, 1)
    if month == 12:
        end = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        end = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)

    entries = load_entries_in_range(start, end)
    if not entries:
        return ""

    all_tags = []
    for e in entries:
        all_tags.extend(e["fm"].get("tags", []))
    tag_counts = Counter(all_tags).most_common(10)

    intensities = [e["fm"].get("emotional_intensity", 5) for e in entries]
    avg_intensity = round(sum(intensities) / len(intensities), 1)
    max_entry = max(entries, key=lambda x: x["fm"].get("emotional_intensity", 0))
    flashbulbs = [e for e in entries if e["fm"].get("flashbulb")]

    lines = [
        f"# 月摘要 {month_str}",
        "",
        f"**{len(entries)} 篇日記** | 平均情緒強度：{avg_intensity}/10",
        "",
    ]

    if flashbulbs:
        lines.append("## ⚡ 本月閃光燈記憶")
        for e in flashbulbs:
            lines.append(f"- **{e['fm']['date']}** {e['fm']['title']}")
        lines.append("")

    lines.append(f"## 🔥 本月最強情緒時刻（{max_entry['fm'].get('emotional_intensity')}/10）")
    lines.append(f"**{max_entry['fm']['date']}** {max_entry['fm']['title']}")
    lines.append("")

    if tag_counts:
        lines.append("## 🏷 本月關鍵詞")
        lines.append("  " + " · ".join(f"{t}({c})" for t, c in tag_counts))
        lines.append("")

    lines.append("---")
    lines.append(f"*自動生成於 {datetime.date.today()}*")
    return "\n".join(lines)


def auto_summarize():
    """自動偵測過去一週是否需要摘要"""
    today = datetime.date.today()
    # 上一週的 ISO week
    last_week = today - datetime.timedelta(weeks=1)
    iso = last_week.isocalendar()
    week_str = f"{iso.year}-W{iso.week:02d}"

    out_path = SUMMARY_DIR / f"{week_str}.md"
    if out_path.exists():
        print(f"⏭ {week_str} 摘要已存在：{out_path}")
        return

    content = build_week_summary(week_str)
    if not content:
        print(f"📭 {week_str} 沒有日記，略過")
        return

    out_path.write_text(content, encoding="utf-8")
    print(f"✅ 已生成：{out_path}")


def main():
    parser = argparse.ArgumentParser(description="AI Diary Summarize")
    parser.add_argument("--week", type=str, help="YYYY-Www")
    parser.add_argument("--month", type=str, help="YYYY-MM")
    parser.add_argument("--auto", action="store_true", help="自動偵測並生成上週摘要")
    args = parser.parse_args()

    if args.auto:
        auto_summarize()
        return

    if args.week:
        content = build_week_summary(args.week)
        if not content:
            print("📭 該週無日記")
            return
        out = SUMMARY_DIR / f"{args.week}.md"
        out.write_text(content, encoding="utf-8")
        print(f"✅ {out}")
        return

    if args.month:
        content = build_month_summary(args.month)
        if not content:
            print("📭 該月無日記")
            return
        out = SUMMARY_DIR / f"{args.month}.md"
        out.write_text(content, encoding="utf-8")
        print(f"✅ {out}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
