#!/usr/bin/env python3
"""
migrate_decay_weights.py — Phase A 一次性遷移腳本

對所有尚未有 decay_weight 欄位（或 decay_weight=None）的舊日記，
批次計算並寫入正確的初始 decay_weight。

用法：
  python src/migrate_decay_weights.py            # 實際寫入
  python src/migrate_decay_weights.py --dry-run  # 只顯示，不寫
  python src/migrate_decay_weights.py --all      # 強制重算所有（包含已有值的）
"""

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from consolidate import compute_decay_weight, update_all_decay_weights

DIARY_ROOT = ROOT / "diary"


def migrate(dry_run: bool = False, force_all: bool = False) -> None:
    """遷移所有舊日記的 decay_weight"""
    import datetime

    today = datetime.date.today()
    total = 0
    skipped = 0
    updated = 0
    errors = 0

    print(f"\n🧠 Phase A Migration — decay_weight 補全")
    print(f"   模式：{'dry-run（不寫入）' if dry_run else 'force_all（重算所有）' if force_all else '只補缺失值'}")
    print(f"   日期基準：{today}\n")

    for md in sorted(DIARY_ROOT.rglob("*.md")):
        # 跳過非日記
        if any(part in md.parts for part in ("summaries", "config")):
            continue
        if md.name.startswith("README") or md.name == "self-narrative.md":
            continue

        total += 1
        try:
            text = md.read_text(encoding="utf-8")
            if not text.startswith("---"):
                skipped += 1
                continue

            _, fm_str, body = text.split("---", 2)
            fm = yaml.safe_load(fm_str)
            if not fm or not isinstance(fm, dict):
                skipped += 1
                continue

            # 判斷是否需要更新
            has_decay = "decay_weight" in fm and fm["decay_weight"] is not None
            if has_decay and not force_all:
                skipped += 1
                continue

            intensity    = fm.get("emotional_intensity", 5)
            flashbulb    = bool(fm.get("flashbulb", False))
            date_str     = fm.get("date", "")
            recall_count = fm.get("recall_count", 0)

            new_weight = compute_decay_weight(
                date_str=date_str,
                intensity=intensity,
                flashbulb=flashbulb,
                recall_count=recall_count,
                today=today,
            )

            fb_mark = "⚡" if flashbulb else " "
            old_str = f"{fm.get('decay_weight', 'None'):>6}" if has_decay else "  None"
            print(f"  {fb_mark} {md.name}  [{date_str}]  i={intensity}  "
                  f"{old_str} → {new_weight:.4f}")

            if not dry_run:
                fm["decay_weight"] = new_weight
                fm_str_new = yaml.dump(fm, allow_unicode=True, sort_keys=False)
                md.write_text(f"---\n{fm_str_new}---{body}", encoding="utf-8")

            updated += 1

        except Exception as ex:
            print(f"  ❌ {md.name}: {ex}")
            errors += 1

    print(f"\n📊 結果：")
    print(f"   總計掃描：{total} 篇")
    print(f"   已更新：  {updated} 篇{'（dry-run，未實際寫入）' if dry_run else ''}")
    print(f"   跳過：    {skipped} 篇")
    if errors:
        print(f"   錯誤：    {errors} 篇")

    if not dry_run and updated > 0:
        # 重建 ROI index 讓 decay_weight 同步進 index
        print("\n🔄 重建 ROI index...")
        from roi import build_index
        n = build_index(verbose=True)
        print(f"✅ Migration 完成！{updated} 篇日記已補上 decay_weight，index 已重建（{n} 筆）")
    elif dry_run:
        print("\n（dry-run 完成，未寫入任何檔案）")
    else:
        print("\n✅ 全部日記已有 decay_weight，無需遷移！")


def main():
    parser = argparse.ArgumentParser(description="Phase A: decay_weight 一次性遷移")
    parser.add_argument("--dry-run", action="store_true", help="只顯示，不寫入")
    parser.add_argument("--all", dest="force_all", action="store_true",
                        help="強制重算所有日記（包含已有值的）")
    args = parser.parse_args()

    migrate(dry_run=args.dry_run, force_all=args.force_all)


if __name__ == "__main__":
    main()
