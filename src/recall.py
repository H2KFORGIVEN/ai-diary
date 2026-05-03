#!/usr/bin/env python3
"""
recall.py — ai-diary — AI Character Diary System
五維度記憶召回引擎（ROI インデックス版）

パフォーマンス設計：
  1. ROI インデックス（roi_index.json）から全スコアリング → .md は上位 K 件のみ読込
  2. stale チェックは stat() のみ（read なし）→ O(N) で高速
  3. flashbulb は 730日半減期を実際に適用

recall_score = keyword×0.30 + roi×0.20 + recency×0.20 + emotional×0.20 + valence_match×0.10

   keyword      : インデックスの keyword pool へのヒット数（全文検索不要）
   roi          : ROI 文の keyword ヒット × valence 方向一致（局所感情ピーク）
   recency      : 指数衰減（flashbulb=730日、通常=30日半減）
   emotional    : emotional_intensity 1-10
   valence_match: 現在の valence 方向と日記 valence の一致度

用法:
  python src/recall.py "主様 開心"
  python src/recall.py --query "ELYTH" --top 3
  python src/recall.py --tag 感動 --top 5
  python src/recall.py "失敗 自責" --valence -5
  python src/recall.py --rebuild   # インデックス再構築
"""

import argparse
import datetime
import json
import math
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
DIARY_ROOT = ROOT / "diary"
CONFIG = yaml.safe_load((DIARY_ROOT / "config" / "settings.yaml").read_text())
WEIGHTS = CONFIG["recall"]["weights"]
TOP_K = CONFIG["recall"]["top_k"]
HALFLIFE_NORMAL    = CONFIG["recall"]["recency_halflife_days"]      # 30日
HALFLIFE_FLASHBULB = CONFIG["flashbulb"]["recency_halflife_days"]   # 730日

# roi.py から load
import sys
sys.path.insert(0, str(ROOT / "src"))
from roi import load_index, update_index_entry


# ── スコアリング関数 ──────────────────────────────────────────────────

def recency_score(date_str: str, is_flashbulb: bool = False) -> float:
    """新近度：指數衰減。flashbulb は 730日半減、通常は 30日半減。"""
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return 0.0
    days_ago = (datetime.datetime.now() - dt).days
    halflife = HALFLIFE_FLASHBULB if is_flashbulb else HALFLIFE_NORMAL
    return math.exp(-math.log(2) * days_ago / halflife)


def keyword_score(query_words: list[str], entry: dict) -> float:
    """
    インデックスの keyword pool へのヒット率（全文検索不要）。
    title + tags + body 頻出語の union を事前計算済み。
    """
    if not query_words:
        return 0.0
    pool = set(w.lower() for w in entry.get("keywords", []))
    pool.add(entry.get("title", "").lower())
    hits = sum(1 for w in query_words if w.lower() in pool)
    return hits / len(query_words)


def emotional_score(entry: dict) -> float:
    """情緒強度分數：1-10 → 0-1"""
    intensity = entry.get("intensity", 5)
    return max(0.0, min(1.0, (intensity - 1) / 9.0))


def _valence_sim(entry_valence: int, query_valence: int) -> float:
    """valence 方向一致スコア（-10～+10）。query=0 → 0.5 中性"""
    if query_valence == 0:
        return 0.5
    q = query_valence / 10.0
    e = entry_valence / 10.0
    return max(0.0, min(1.0, 1.0 - abs(q - e) / 2.0))


def valence_score(entry: dict, query_valence: int) -> float:
    return _valence_sim(entry.get("valence", 0), query_valence)


def roi_score(query_words: list[str], entry: dict, query_valence: int) -> float:
    """
    ROI 文スコア（局所感情ピーク）。

    各 ROI 文について：
      - keyword ヒット × 2.0 重み（ROI 文内命中は強い）
      - valence 方向一致スコアも加算
    上位 1 文のスコアを返す（max pooling）。

    ROI なし → keyword_score にフォールバック（退行なし）
    """
    rois = entry.get("roi", [])
    if not rois:
        return keyword_score(query_words, entry)

    best = 0.0
    for roi in rois:
        text_lower = roi.get("text", "").lower()
        kw_hits = sum(1 for w in query_words if w.lower() in text_lower)
        kw_s = min(1.0, (kw_hits / len(query_words)) * 2.0) if query_words else 0.5
        v_s  = _valence_sim(roi.get("valence", 0), query_valence)
        # ROI に query_words ヒットがなくても valence 一致で 0.3 程度
        s = kw_s * 0.7 + v_s * 0.3
        best = max(best, s)
    return best


def recall_score_from_index(query_words: list[str], entry: dict,
                             query_valence: int = 0) -> float:
    """インデックスエントリから直接スコアを計算（.md 読込不要）"""
    k  = keyword_score(query_words, entry)
    ri = roi_score(query_words, entry, query_valence)
    r  = recency_score(entry.get("date", ""), entry.get("flashbulb", False))
    e  = emotional_score(entry)
    v  = valence_score(entry, query_valence)
    w  = WEIGHTS
    return (w.get("keyword", 0.30) * k
          + w.get("roi",     0.20) * ri
          + w.get("recency", 0.20) * r
          + w.get("emotional", 0.20) * e
          + w.get("valence_match", 0.10) * v)


# ── meta 更新（上位 K 件のみ .md を読む） ────────────────────────────

def _update_recall_meta_by_path(path: Path):
    """recall_count / last_recalled を .md frontmatter に書き戻す"""
    try:
        text = path.read_text(encoding="utf-8")
        _, fm_str, body = text.split("---", 2)
        fm = yaml.safe_load(fm_str)
        fm["recall_count"] = fm.get("recall_count", 0) + 1
        fm["last_recalled"] = datetime.datetime.now().strftime("%Y-%m-%d")
        fm_str_new = yaml.dump(fm, allow_unicode=True, sort_keys=False)
        path.write_text(f"---\n{fm_str_new}---{body}", encoding="utf-8")
        # インクリメンタルインデックス更新
        update_index_entry(path)
    except Exception:
        pass


# ── タグフィルタ ──────────────────────────────────────────────────────

def filter_by_tag(entries: list[dict], tag: str) -> list[dict]:
    return [e for e in entries if tag in (e.get("tags") or [])]


# ── 結果フォーマット ──────────────────────────────────────────────────

def format_result(rank: int, entry: dict, score: float) -> str:
    tags      = ", ".join(entry.get("tags") or [])
    intensity = entry.get("intensity", "?")
    valence   = entry.get("valence", None)
    arousal   = entry.get("arousal", None)
    suppressed = entry.get("suppressed_emotion", "") or ""
    fb        = "⚡" if entry.get("flashbulb") else ""
    first_reaction = entry.get("first_reaction", "") or ""

    va_parts = []
    if valence is not None:
        sign = "+" if valence > 0 else ""
        va_parts.append(f"V:{sign}{valence}")
    if arousal is not None:
        va_parts.append(f"A:{arousal}")
    va_str = "  " + " ".join(va_parts) if va_parts else ""

    lines = [
        f"{'='*60}",
        f"#{rank}  [{score:.3f}]  {entry.get('date', '?')}  {fb}",
        f"📔 {entry.get('title', '（無標題）')}",
        f"🏷  {tags}  |  強度: {intensity}/10{va_str}",
    ]
    if suppressed:
        lines.append(f"🤫 壓抑: {suppressed}")
    if first_reaction:
        lines.append(f"⚡ 第一反應: {first_reaction}")

    # ROI 文プレビュー
    rois = entry.get("roi") or []
    if rois:
        lines.append(f"📍 ROI: {rois[0]['text'][:80]}")

    preview = (entry.get("preview") or "")[:200].replace("\n", " ")
    if preview:
        lines.append(f"\n{preview}…" if len(entry.get("preview", "")) > 200 else f"\n{preview}")
    return "\n".join(lines)


# ── メイン recall ─────────────────────────────────────────────────────

def run_recall(query: str, top_k: int, tag_filter: str | None,
               update_meta: bool, query_valence: int = 0) -> list[tuple[float, dict]]:
    """
    高速 5 次元 recall。

    パフォーマンス：
      - 全スコアリングはインデックス（メモリ内）のみ
      - .md ファイル読込は update_meta=True の上位 K 件のみ
    """
    entries = load_index()

    if tag_filter:
        entries = filter_by_tag(entries, tag_filter)

    query_words = query.split() if query else []
    scored = [
        (recall_score_from_index(query_words, e, query_valence), e)
        for e in entries
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    if update_meta:
        for _, e in top:
            p = ROOT / e["path"]
            if p.exists():
                _update_recall_meta_by_path(p)

    return top


# ── CLI ──────────────────────────────────────────────────────��────────

def main():
    parser = argparse.ArgumentParser(description="AI Diary Recall Engine（ROI インデックス版）")
    parser.add_argument("query", nargs="?", default="", help="查詢關鍵詞（空格分隔）")
    parser.add_argument("--tag", type=str, help="只看某個 tag")
    parser.add_argument("--top", type=int, default=TOP_K, help=f"回傳篇數（預設 {TOP_K}）")
    parser.add_argument("--valence", type=int, default=0,
                        help="當前情境 valence（-10～+10）")
    parser.add_argument("--no-update", action="store_true", help="不更新 recall_count")
    parser.add_argument("--rebuild", action="store_true", help="強制重建 ROI index")
    parser.add_argument("--json", action="store_true", help="輸出 JSON")
    args = parser.parse_args()

    if args.rebuild:
        from roi import build_index
        n = build_index(verbose=True)
        if not args.query:
            return

    results = run_recall(
        args.query,
        args.top,
        args.tag,
        update_meta=not args.no_update,
        query_valence=args.valence,
    )

    if args.json:
        output = []
        for score, e in results:
            output.append({
                "score":             round(score, 4),
                "date":              e.get("date"),
                "title":             e.get("title"),
                "tags":              e.get("tags", []),
                "emotional_intensity": e.get("intensity"),
                "valence":           e.get("valence", 0),
                "arousal":           e.get("arousal", 5),
                "suppressed_emotion": e.get("suppressed_emotion", ""),
                "flashbulb":         e.get("flashbulb", False),
                "roi":               e.get("roi", []),
                "preview":           e.get("preview", "")[:300],
                "path":              e.get("path"),
            })
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    if not results:
        print("🔍 找不到相關日記ですっ…")
        return

    print(f"\n🔍 召回結果（top {len(results)}）\n")
    for i, (score, e) in enumerate(results, 1):
        print(format_result(i, e, score))
    print("=" * 60)


if __name__ == "__main__":
    main()
