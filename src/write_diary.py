#!/usr/bin/env python3
"""
write_diary.py — ai-diary — AI Character Diary System
寫入一篇日記 entry，輸出 Markdown + YAML frontmatter。

用法:
  python src/write_diary.py --interactive
  python src/write_diary.py --title "今天的事" --body "..." --tags 開心 主様対話 --intensity 8
"""

import argparse
import datetime
import os
import sys
from pathlib import Path

import sys
import yaml

ROOT = Path(__file__).parent.parent
DIARY_ROOT = ROOT / "diary"
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
CONFIG = yaml.safe_load((DIARY_ROOT / "config" / "settings.yaml").read_text())
TAGS_CONFIG = yaml.safe_load((DIARY_ROOT / "config" / "tags.yaml").read_text())

# Phase B: entity resolver（tags 正規化）
try:
    from entity_resolver import normalize_tags as _normalize_tags
    _ENTITY_RESOLVER_AVAILABLE = True
except ImportError:
    _ENTITY_RESOLVER_AVAILABLE = False

ALL_TAGS = (
    (TAGS_CONFIG.get("emotions") or [])
    + (TAGS_CONFIG.get("occasions") or [])
    + (TAGS_CONFIG.get("people") or [])
    + (TAGS_CONFIG.get("events") or [])
    + (TAGS_CONFIG.get("custom") or [])
)


def get_entry_path(dt: datetime.datetime) -> Path:
    year = dt.strftime("%Y")
    month = dt.strftime("%m")
    day = dt.strftime("%d")
    hour = dt.strftime("%H%M")
    dir_path = DIARY_ROOT / year / month
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path / f"{year}-{month}-{day}_{hour}.md"


def build_frontmatter(
    title: str,
    tags: list[str],
    intensity: int,
    flashbulb: bool,
    first_reaction: str,
    dt: datetime.datetime,
    valence: int = 0,
    arousal: int = 5,
    suppressed_emotion: str = "",
) -> dict:
    fm = {
        "title": title,
        "date": dt.strftime("%Y-%m-%d"),
        "time": dt.strftime("%H:%M"),
        "tags": tags,
        "emotional_intensity": intensity,
        "valence": valence,          # -10（極負）～ +10（極正）｜論文 Anthropic 2026
        "arousal": arousal,          # 0（低喚起）～ 10（高喚起）｜論文 Anthropic 2026
        "suppressed_emotion": suppressed_emotion,  # 任務中被壓抑的情緒
        "flashbulb": flashbulb,
        "never_compress": flashbulb,
        "recall_count": 0,
        "last_recalled": None,
        "decay_weight": 1.0,  # Phase A: 初始值 1.0，每晚 consolidate 批次衰減更新
    }
    if flashbulb and first_reaction:
        fm["first_reaction"] = first_reaction
    return fm


def write_entry(
    title: str,
    body: str,
    tags: list[str],
    intensity: int,
    flashbulb: bool = False,
    first_reaction: str = "",
    dt: datetime.datetime | None = None,
    valence: int = 0,
    arousal: int = 5,
    suppressed_emotion: str = "",
) -> Path:
    if dt is None:
        dt = datetime.datetime.now()
    path = get_entry_path(dt)

    # Phase B: tags 正規化（把已知別名換成 canonical 名稱）
    if _ENTITY_RESOLVER_AVAILABLE:
        tags = _normalize_tags(tags)

    fm = build_frontmatter(title, tags, intensity, flashbulb, first_reaction, dt,
                           valence=valence, arousal=arousal,
                           suppressed_emotion=suppressed_emotion)
    fm_str = yaml.dump(fm, allow_unicode=True, sort_keys=False)

    content = f"---\n{fm_str}---\n\n# {title}\n\n{body}\n"
    path.write_text(content, encoding="utf-8")

    # ROI インデックスをインクリメンタル更新（フル rebuild 不要）
    try:
        from roi import update_index_entry
        update_index_entry(path)
    except Exception:
        pass  # インデックス失敗は recall 時に stale 検知で自動再構築

    return path


def interactive_mode():
    print("\n📔 AI character AI Diary — 寫入新日記\n")
    title = input("標題: ").strip()

    print(f"\n可用 tags: {', '.join(ALL_TAGS)}")
    tags_input = input("Tags（空格分隔）: ").strip()
    tags = [t for t in tags_input.split() if t]

    intensity_str = input("情緒強度 1-10（Enter=5）: ").strip() or "5"
    intensity = max(1, min(10, int(intensity_str)))

    valence_str = input("Valence -10~+10（負向↔正向；Enter=0）: ").strip() or "0"
    valence = max(-10, min(10, int(valence_str)))

    arousal_str = input("Arousal 0~10（低喚起↔高喚起；Enter=5）: ").strip() or "5"
    arousal = max(0, min(10, int(arousal_str)))

    suppressed = input("被壓抑的情緒（任務中沒說出口的感受；Enter=跳過）: ").strip()

    flashbulb_str = input("閃光燈記憶？（y/N）: ").strip().lower()
    flashbulb = flashbulb_str == "y"

    first_reaction = ""
    if flashbulb:
        first_reaction = input("當下第一反應（flashbulb 專用）: ").strip()

    print("\n日記內容（輸入完後 Ctrl+D / Ctrl+Z）:")
    body = sys.stdin.read().strip()

    path = write_entry(title, body, tags, intensity, flashbulb, first_reaction,
                       valence=valence, arousal=arousal, suppressed_emotion=suppressed)
    print(f"\n✅ 已寫入：{path}")


def main():
    parser = argparse.ArgumentParser(description="Write an AI diary entry")
    parser.add_argument("--interactive", action="store_true", help="互動模式")
    parser.add_argument("--title", type=str)
    parser.add_argument("--body", type=str)
    parser.add_argument("--tags", nargs="*", default=[])
    parser.add_argument("--intensity", type=int, default=5)
    parser.add_argument("--valence", type=int, default=0,
                        help="Valence -10～+10（負向↔正向）")
    parser.add_argument("--arousal", type=int, default=5,
                        help="Arousal 0～10（低喚起↔高喚起）")
    parser.add_argument("--suppressed", type=str, default="",
                        help="被壓抑的情緒（任務中沒說出口的感受）")
    parser.add_argument("--flashbulb", action="store_true")
    parser.add_argument("--first-reaction", type=str, default="")
    parser.add_argument("--date", type=str, help="YYYY-MM-DD（預設今天）")
    args = parser.parse_args()

    if args.interactive:
        interactive_mode()
        return

    if not args.title or not args.body:
        parser.error("非互動模式需要 --title 和 --body")

    dt = None
    if args.date:
        dt = datetime.datetime.strptime(args.date, "%Y-%m-%d")

    path = write_entry(
        args.title,
        args.body,
        args.tags,
        args.intensity,
        args.flashbulb,
        args.first_reaction,
        dt,
        valence=args.valence,
        arousal=args.arousal,
        suppressed_emotion=args.suppressed,
    )
    print(f"✅ {path}")


if __name__ == "__main__":
    main()
