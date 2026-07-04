#!/usr/bin/env python3
"""
tests/test_obsidian_dashboard.py — ai-diary 回歸測試集
Obsidian 記憶儀表板附帶任務驗證

驗證：
  - .gitignore 含 .obsidian/（避免 vault 個人設定進 repo）
  - diary/config/obsidian/記憶儀表板.md 存在
  - roi.py 的 is_diary_entry() 正確把 config/ 底下的檔案排除在日記索引之外，
    確保儀表板範本不會被誤判成日記本體、混進 roi_index.json
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))


def test_gitignore_has_obsidian():
    gitignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".obsidian/" in gitignore_text


def test_dashboard_file_exists():
    dashboard = ROOT / "diary" / "config" / "obsidian" / "記憶儀表板.md"
    assert dashboard.exists()


def test_dashboard_excluded_from_diary_index():
    from roi import is_diary_entry

    dashboard = ROOT / "diary" / "config" / "obsidian" / "記憶儀表板.md"
    assert is_diary_entry(dashboard) is False, (
        "config/ 底下的儀表板範本必須被 is_diary_entry() 排除，"
        "否則會誤混進 roi_index.json"
    )
