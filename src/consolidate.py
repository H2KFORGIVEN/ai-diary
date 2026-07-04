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
import math
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
DIARY_ROOT = ROOT / "diary"

# 動態 import（避免在 write_diary 裡重複定義路徑）
sys.path.insert(0, str(ROOT / "src"))
from buffer import load_buffer, clear_buffer, snapshot_buffer, load_snapshot, _unique_archive_path
from write_diary import write_entry
from emotion_filter import apply_filter
from roi import is_diary_entry


# ── Phase A: 時間衰減輔助函數 ────────────────────────────────────────

def _get_decay_config() -> dict:
    """從 settings.yaml 讀取 decay 設定（含 backward compat 預設值）"""
    cfg = yaml.safe_load((DIARY_ROOT / "config" / "settings.yaml").read_text())
    return cfg.get("decay", {
        "halflife_by_intensity": {"flashbulb": 730, "high": 90, "medium": 60, "normal": 30},
        "floor_by_intensity":    {"flashbulb": 0.50, "high": 0.15, "medium": 0.10, "normal": 0.05},
        "recall_boost": 0.10,
    })


def compute_decay_weight(
    date_str: str,
    intensity: int,
    flashbulb: bool,
    recall_count: int = 0,
    today: datetime.date | None = None,
) -> float:
    """
    計算一篇日記當前的 decay_weight。

    公式：
      base_importance = intensity / 10.0
      halflife        = 由 flashbulb / intensity 決定
      floor           = 由 flashbulb / intensity 決定
      raw             = base_importance × exp(-ln2 × days_ago / halflife)
      decay_weight    = max(raw, floor)

    再疊加 recall_boost（被召回次數 × boost，上限 1.0）。
    """
    cfg = _get_decay_config()
    hl_cfg = cfg.get("halflife_by_intensity", {})
    fl_cfg = cfg.get("floor_by_intensity", {})
    boost  = cfg.get("recall_boost", 0.10)

    if today is None:
        today = datetime.date.today()

    # 計算距今天數
    try:
        dt = datetime.date.fromisoformat(str(date_str))
        days_ago = max(0, (today - dt).days)
    except Exception:
        days_ago = 0

    # 選半衰期與 floor
    if flashbulb:
        halflife = hl_cfg.get("flashbulb", 730)
        floor    = fl_cfg.get("flashbulb", 0.50)
    elif intensity >= 8:
        halflife = hl_cfg.get("high", 90)
        floor    = fl_cfg.get("high", 0.15)
    elif intensity >= 6:
        halflife = hl_cfg.get("medium", 60)
        floor    = fl_cfg.get("medium", 0.10)
    else:
        halflife = hl_cfg.get("normal", 30)
        floor    = fl_cfg.get("normal", 0.05)

    # 衰減計算
    base_importance = intensity / 10.0
    raw = base_importance * math.exp(-math.log(2) * days_ago / halflife)
    weight = max(raw, floor)

    # recall 活化（每次 +boost，上限 1.0）
    weight = min(1.0, weight + recall_count * boost)

    return round(weight, 4)


def update_all_decay_weights(dry_run: bool = False) -> int:
    """
    掃描所有日記 .md，批次更新 frontmatter 中的 decay_weight。
    consolidate() 每次執行後呼叫此函數（相當於「睡眠期間記憶重整」）。

    Returns: 更新的檔案數
    """
    today = datetime.date.today()
    updated = 0

    for md in sorted(DIARY_ROOT.rglob("*.md")):
        # 跳過非日記檔
        if not is_diary_entry(md):
            continue

        try:
            text = md.read_text(encoding="utf-8")
            if not text.startswith("---"):
                continue
            _, fm_str, body = text.split("---", 2)
            fm = yaml.safe_load(fm_str)
            if not fm or not isinstance(fm, dict):
                continue

            intensity   = fm.get("emotional_intensity", 5)
            flashbulb   = bool(fm.get("flashbulb", False))
            date_str    = fm.get("date", "")
            recall_count = fm.get("recall_count", 0)

            new_weight = compute_decay_weight(
                date_str=date_str,
                intensity=intensity,
                flashbulb=flashbulb,
                recall_count=recall_count,
                today=today,
            )

            old_weight = fm.get("decay_weight")
            if old_weight == new_weight:
                continue  # 無變化，跳過

            if not dry_run:
                fm["decay_weight"] = new_weight
                fm_str_new = yaml.dump(fm, allow_unicode=True, sort_keys=False)
                md.write_text(f"---\n{fm_str_new}---{body}", encoding="utf-8")

            updated += 1

        except Exception:
            continue

    return updated


def _archive_snapshot(snapshot_path: Path) -> Path:
    """
    把已處理完成的 buffer 快照備份進 diary/archive/YYYY-MM-DD_buffer.jsonl。
    同日多次鞏固（例如同日 append 多批、consolidate 跑多次）時序號遞增，不覆寫先前備份。
    """
    today = datetime.date.today().isoformat()
    archive_dir = DIARY_ROOT / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = _unique_archive_path(archive_dir, today)
    archive_path.write_bytes(snapshot_path.read_bytes())
    return archive_path


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
        ne["filter_valence"]      = result["valence"]   # ← Nanoleaf 燈色連動用
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
    medium = [
        e for e in events
        if DISCARD_THRESHOLD < e.get("filtered_intensity", e["intensity"]) <= MERGE_THRESHOLD
    ]
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
        "t": group[0].get("t", "") if group else "",
        "valence": round(sum(e.get("filter_valence", 0) for e in group) / len(group)) if group else 0,
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
        "t":         event.get("t", ""),
        "valence":   event.get("filter_valence", 0),
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
        "t":              event.get("t", ""),
        "valence":        event.get("filter_valence", 0),
    }


def consolidate(
    date: datetime.date | None = None,
    dry_run: bool = False,
    archive_path: Path | None = None,
) -> list[Path]:
    """
    主流程：buffer → diary entries
    回傳寫入的檔案路徑清單

    競態防護：非 archive_path 模式且非 dry_run 時，開頭會先把 raw_buffer.jsonl
    原子搬移（os.replace）成 diary/batch-<timestamp>.jsonl 快照，只處理快照內容。
    鞏固期間新寫入的事件會落進全新的 raw_buffer.jsonl，不會被本次處理誤刪。
    """
    snapshot_path: Path | None = None

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
    elif dry_run:
        # dry-run 不消費 buffer，直接預覽，不需要快照
        events = load_buffer()
    else:
        # 正式鞏固：原子快照，避免鞏固期間新事件被一併清掉
        snapshot_path = snapshot_buffer()
        events = load_snapshot(snapshot_path) if snapshot_path else []

    if not events:
        print("📭 今日 buffer 是空的，無需 consolidate")
        # 快照後發現是空的（理論上不會發生，snapshot_buffer 已檢查存在性）
        # 仍保留快照以便人工檢查，不靜默刪除
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
    # flashbulb 門檻：intensity >= 8（主様決定：「うちが記憶したい時刻は全部永駐」）
    FLASHBULB_THRESHOLD = 8
    strong    = [e for e in keep if 7 <= e.get("filtered_intensity", e["intensity"]) < FLASHBULB_THRESHOLD
                 and not e.get("filter_flashbulb", False)]
    flashbulb = [e for e in keep if e.get("filtered_intensity", e["intensity"]) >= FLASHBULB_THRESHOLD
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
        # buffer の実時刻（ISO "YYYY-MM-DDTHH:MM:SS"）を復元してファイル名に反映。
        # 時刻が取れない場合のみ 00:00 fallback（連番サフィックスで上書き回避）。
        spec_t = spec.get("t", "")
        if spec_t and len(spec_t) >= 16:
            try:
                parsed = datetime.datetime.fromisoformat(spec_t)
                dt = datetime.datetime(
                    spec["date"].year,
                    spec["date"].month,
                    spec["date"].day,
                    parsed.hour,
                    parsed.minute,
                    parsed.second,
                )
            except ValueError:
                dt = datetime.datetime(
                    spec["date"].year,
                    spec["date"].month,
                    spec["date"].day,
                )
        else:
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
            valence=spec.get("valence", 0),
        )
        written.append(path)

    # Step 4: 處理快照 —— 備份後刪除（非 archive_path 模式）
    if not archive_path:
        if snapshot_path and snapshot_path.exists():
            _archive_snapshot(snapshot_path)
            snapshot_path.unlink()
            print(f"\n✅ 寫入 {len(written)} 篇，快照已備份並清除")
        else:
            print(f"\n✅ 寫入 {len(written)} 篇")
    else:
        print(f"\n✅ 寫入 {len(written)} 篇（archive 模式，不清空 buffer）")

    # Step 5: Phase A — 批次更新全部日記 decay_weight
    if not dry_run:
        n_updated = update_all_decay_weights(dry_run=False)
        print(f"⏳ decay_weight 更新：{n_updated} 篇已重算")

    # Step 6: Phase C — 更新 Tag Graph
    if not dry_run:
        try:
            from build_tag_graph import build_tag_graph
            build_tag_graph(verbose=False)
            print("🕸  Tag Graph 已更新")
        except Exception as e:
            print(f"⚠️  Tag Graph 更新失敗（非致命）: {e}")

    # Step 6.5: Phase 1 — L2 Scenario 自動更新（Tag Graph 更新後）
    if not dry_run:
        try:
            import yaml as _yaml
            from scenarize import scenarize
            _scn_settings = _yaml.safe_load(
                (DIARY_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
            )
            _scn_cfg = _scn_settings.get("scenario", {})
            if _scn_cfg.get("enabled", True):
                _window = _scn_cfg.get("window_days", 30)
                _new_scns = scenarize(window_days=_window, rebuild=False, dry_run=False)
                print(f"🗂  Scenario 已更新：{len(_new_scns)} 個")
            else:
                print("🗂  Scenario 更新已跳過（scenario.enabled=false）")
        except Exception as e:
            print(f"⚠️  Scenario 更新失敗（非致命）: {e}")

    # Step 7: Phase III — 困境模式偵測
    if not dry_run:
        try:
            from detect_patterns import detect_patterns
            detect_patterns(verbose=True)
        except Exception as e:
            print(f"⚠️  Pattern Detection 失敗（非致命）: {e}")

    # Step 8: Vector Index 更新（build_vec_index.py を /usr/local/bin/python3 で実行）
    if not dry_run:
        try:
            import subprocess as _sp
            _vec_script = ROOT / "src" / "build_vec_index.py"
            _vec_python = "/usr/local/bin/python3"
            if _vec_script.exists():
                _result = _sp.run(
                    [_vec_python, str(_vec_script)],
                    capture_output=True, text=True, timeout=60,
                    cwd=str(ROOT),
                )
                if _result.returncode == 0:
                    # 最後の行だけ表示（要約行）
                    _last = [l for l in _result.stdout.splitlines() if l.strip()]
                    print(f"🔢  Vector Index: {_last[-1] if _last else 'ok'}")
                else:
                    print(f"⚠️  Vector Index 更新失敗: {_result.stderr[:100]}")
        except Exception as e:
            print(f"⚠️  Vector Index 更新失敗（非致命）: {e}")

    # Step 9: persona consolidate-hook（drift + reflect + goals + outreach）
    if not dry_run:
        try:
            import subprocess as _sp
            _persona_py = "/Users/showmaker/Projects/persona-engine/src/persona.py"
            _persona_python = "/Users/showmaker/.hermes/hermes-agent/venv/bin/python3"
            _result = _sp.run(
                [_persona_python, _persona_py, "consolidate-hook"],
                capture_output=True, text=True, timeout=60,
            )
            if _result.returncode == 0:
                print("🧠  Persona consolidate-hook: ok")
            else:
                print(f"⚠️  Persona consolidate-hook 失敗: {_result.stderr[:100]}")
        except Exception as e:
            print(f"⚠️  Persona consolidate-hook 失敗（非致命）: {e}")

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
