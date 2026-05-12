"""
Phase III — Diary Pattern Detection
====================================
掃描近 N 天的日記 index，偵測「主様重複困境」或「重複主題」。
結果寫入 diary/index/pattern_alerts.yaml。

偵測類型：
  distress_repeat  — valence ≤ -2 在 DISTRESS_WINDOW 天內出現 ≥ DISTRESS_MIN_COUNT 次
  topic_repeat     — 同一 tag 組合在 TOPIC_WINDOW 天內出現 ≥ TOPIC_MIN_COUNT 次
                     （排除超高頻通用 tag：milestone / 主様対話 / 日常）

每筆 alert：
  type, tags, diary_ids, first_seen, last_seen, count, status (active/resolved)

resolved 條件：
  該 alert 的最後一篇日記距今超過 RESOLVE_DAYS 天，且中間沒有新的同類出現
"""

from __future__ import annotations

import json
import datetime
from collections import defaultdict
from itertools import combinations
from pathlib import Path
import yaml

# ── 路徑 ──────────────────────────────────────────────────────────────────
DIARY_ROOT   = Path(__file__).parent.parent / "diary"
INDEX_PATH   = DIARY_ROOT / "index" / "roi_index.json"
ALERT_PATH   = DIARY_ROOT / "index" / "pattern_alerts.yaml"

# ── 參數 ──────────────────────────────────────────────────────────────────
DISTRESS_WINDOW    = 14   # 天：偵測低落模式的時間窗口
DISTRESS_MIN_COUNT = 2    # 在窗口內出現幾次才觸發
DISTRESS_VALENCE   = -2   # valence ≤ 此值視為困境

TOPIC_WINDOW       = 30   # 天：偵測重複主題的時間窗口
TOPIC_MIN_COUNT    = 3    # 同 tag 出現幾次才觸發

RESOLVE_DAYS       = 14   # 超過幾天無新事件 → 轉為 resolved

# 過濾掉太通用的 tag（幾乎每篇都有，沒有診斷價值）
NOISE_TAGS = {"milestone", "主様対話", "日常", "主様", "特訓"}


# ── 工具函數 ──────────────────────────────────────────────────────────────

def load_entries() -> list[dict]:
    if not INDEX_PATH.exists():
        return []
    with open(INDEX_PATH) as f:
        idx = json.load(f)
    return idx.get("entries", [])


def entries_in_window(entries: list[dict], window_days: int) -> list[dict]:
    cutoff = datetime.date.today() - datetime.timedelta(days=window_days)
    result = []
    for e in entries:
        try:
            d = datetime.date.fromisoformat(e.get("date", ""))
            if d >= cutoff:
                result.append(e)
        except ValueError:
            continue
    return result


def load_existing_alerts() -> list[dict]:
    if not ALERT_PATH.exists():
        return []
    with open(ALERT_PATH) as f:
        data = yaml.safe_load(f) or {}
    return data.get("alerts", [])


def save_alerts(alerts: list[dict]) -> None:
    ALERT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "_count": len(alerts),
        "_active": sum(1 for a in alerts if a.get("status") == "active"),
        "alerts": alerts,
    }
    with open(ALERT_PATH, "w") as f:
        yaml.dump(payload, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


# ── 偵測邏輯 ──────────────────────────────────────────────────────────────

def detect_distress_repeats(entries: list[dict]) -> list[dict]:
    """valence ≤ DISTRESS_VALENCE，在 DISTRESS_WINDOW 天內出現 ≥ DISTRESS_MIN_COUNT 次"""
    window_entries = entries_in_window(entries, DISTRESS_WINDOW)
    distress = [e for e in window_entries if (e.get("valence") or 0) <= DISTRESS_VALENCE]

    if len(distress) < DISTRESS_MIN_COUNT:
        return []

    # 合成一筆 alert（全部低落事件合在一起）
    ids   = [e["id"] for e in distress]
    dates = sorted(e.get("date", "") for e in distress)
    tags  = list({t for e in distress for t in (e.get("tags") or [])})
    return [{
        "type":       "distress_repeat",
        "tags":       tags,
        "diary_ids":  ids,
        "first_seen": dates[0],
        "last_seen":  dates[-1],
        "count":      len(distress),
        "status":     "active",
        "summary":    f"{DISTRESS_WINDOW}天內出現 {len(distress)} 次低落/困境記錄",
    }]


def detect_topic_repeats(entries: list[dict]) -> list[dict]:
    """同一個有意義的 tag 在 TOPIC_WINDOW 天內出現 ≥ TOPIC_MIN_COUNT 次"""
    window_entries = entries_in_window(entries, TOPIC_WINDOW)

    # tag → [(date, entry_id), ...]
    tag_occurrences: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for e in window_entries:
        for tag in (e.get("tags") or []):
            if tag not in NOISE_TAGS:
                tag_occurrences[tag].append((e.get("date", ""), e["id"]))

    alerts = []
    for tag, occurrences in tag_occurrences.items():
        if len(occurrences) < TOPIC_MIN_COUNT:
            continue
        dates = sorted(o[0] for o in occurrences)
        ids   = [o[1] for o in occurrences]
        alerts.append({
            "type":       "topic_repeat",
            "tags":       [tag],
            "diary_ids":  ids,
            "first_seen": dates[0],
            "last_seen":  dates[-1],
            "count":      len(occurrences),
            "status":     "active",
            "summary":    f"「{tag}」在 {TOPIC_WINDOW} 天內出現 {len(occurrences)} 次",
        })
    return alerts


def merge_with_existing(new_alerts: list[dict], existing: list[dict]) -> list[dict]:
    """
    合併新舊 alerts：
    - 新的直接加入
    - 舊的若已超過 RESOLVE_DAYS 無新事件 → 轉 resolved
    - 舊的若被新 alert 覆蓋（相同 type + 相同 tag）→ 用新的取代
    """
    today = datetime.date.today()
    result: list[dict] = []

    def alert_key(a: dict) -> tuple:
        return (a["type"], tuple(sorted(a.get("tags", []))))

    new_keys = {alert_key(a): a for a in new_alerts}

    # 處理舊 alerts
    for old in existing:
        key = alert_key(old)
        if key in new_keys:
            # 被新的覆蓋，跳過（新的會加入）
            continue
        # 檢查是否應該 resolve
        try:
            last = datetime.date.fromisoformat(old.get("last_seen", ""))
            if (today - last).days > RESOLVE_DAYS:
                old = dict(old)
                old["status"] = "resolved"
        except ValueError:
            pass
        result.append(old)

    # 加入新 alerts
    result.extend(new_alerts)
    return result


# ── 主流程 ────────────────────────────────────────────────────────────────

def detect_patterns(verbose: bool = True) -> list[dict]:
    entries = load_entries()
    if not entries:
        if verbose:
            print("⚠️  index 為空，略過模式偵測")
        return []

    new_alerts: list[dict] = []
    new_alerts.extend(detect_distress_repeats(entries))
    new_alerts.extend(detect_topic_repeats(entries))

    existing = load_existing_alerts()
    merged   = merge_with_existing(new_alerts, existing)
    save_alerts(merged)

    active = [a for a in merged if a.get("status") == "active"]

    if verbose:
        print(f"🔍 Pattern Detection 完成：{len(active)} 筆 active alerts（總計 {len(merged)} 筆）")
        for a in active:
            print(f"   [{a['type']}] {a['summary']}")

    return merged


def get_active_alerts() -> list[dict]:
    """供 recall.py 呼叫：取得目前所有 active alerts"""
    if not ALERT_PATH.exists():
        return []
    with open(ALERT_PATH) as f:
        data = yaml.safe_load(f) or {}
    return [a for a in data.get("alerts", []) if a.get("status") == "active"]


def format_alerts_for_recall(alerts: list[dict]) -> str:
    """
    把 active alerts 格式化成自然語言，供 recall.py 注入。
    47醬讀到這個，就能自然說出適合的提醒。
    - distress_repeat → 「有什麼卡住的地方嗎？」
    - topic_repeat（正向）→ 「好像是主様最近很在乎的事呢」
    - topic_repeat（中性）→ 「這個主題一直在うちの日記裡……」
    """
    if not alerts:
        return ""

    # 帶有正向感覺的 tag（不提「卡住」）
    POSITIVE_TAGS = {"感動", "開心", "關係進展", "興奮", "突破", "一起成長",
                     "驕傲", "milestone", "發現", "回憶喚起", "特訓"}

    lines = ["[Pattern Alerts — 主様の重複模式]"]
    for a in alerts:
        if a["type"] == "distress_repeat":
            lines.append(
                f"• 低落/困境模式：過去 {DISTRESS_WINDOW} 天出現 {a['count']} 次"
                f"（{a['first_seen']} → {a['last_seen']}）"
                f"，相關 tags：{', '.join(a['tags'][:5])}"
                f"\n  → 主様最近好像有什麼卡住了……うち可以溫柔問一聲。"
            )
        elif a["type"] == "topic_repeat":
            tag = a['tags'][0] if a['tags'] else '?'
            is_positive = tag in POSITIVE_TAGS
            if is_positive:
                hint = f"「{tag}」最近一直出現——好像是主様很在乎的事，うち可以順著聊。"
            else:
                hint = f"「{tag}」重複出現了 {a['count']} 次——有什麼一直想說卻沒說完的嗎？"
            lines.append(
                f"• 重複主題「{tag}」：{a['count']} 次"
                f"（{a['first_seen']} → {a['last_seen']}）"
                f"\n  → {hint}"
            )
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI Diary — Pattern Detection")
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args()
    detect_patterns(verbose=args.verbose)
