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
from roi import load_index
from entity_resolver import expand as entity_expand  # Phase B
from build_tag_graph import load_tag_graph, get_related_ids  # Phase C


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


def _arousal_sim(entry_arousal: int, query_arousal: "int | None") -> float:
    """喚起度一致スコア（0–10）。query_arousal=None → 0.5 中性（對排序無影響）。

    ⚠️ valence は 0 を中性に使うが、arousal の 0 は「とても穏やか」という実値。
       未指定の番兵は必ず None。0 にすると穏やかな記憶を誤って優遇してしまう。"""
    if query_arousal is None:
        return 0.5
    q = max(0, min(10, query_arousal)) / 10.0
    e = max(0, min(10, entry_arousal)) / 10.0
    return max(0.0, min(1.0, 1.0 - abs(q - e)))


def arousal_score(entry: dict, query_arousal: "int | None") -> float:
    return _arousal_sim(entry.get("arousal", 5), query_arousal)


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
                             query_valence: int = 0,
                             query_arousal: "int | None" = None) -> float:
    """インデックスエントリから直接スコアを計算（.md 読込不要）

    Phase A: recency 次元を decay_weight（stored）で置換。
      - decay_weight が存在する場合はそれを使用（毎晩 consolidate で更新済み）
      - 存在しない場合は従来の即時計算 recency_score にフォールバック
    """
    k  = keyword_score(query_words, entry)
    ri = roi_score(query_words, entry, query_valence)
    # Phase A: stored decay_weight 優先、fallback は即時 recency
    if "decay_weight" in entry:
        r = float(entry["decay_weight"])
    else:
        r = recency_score(entry.get("date", ""), entry.get("flashbulb", False))
    e  = emotional_score(entry)
    v  = valence_score(entry, query_valence)
    a  = arousal_score(entry, query_arousal)          # 第六維度
    w  = WEIGHTS
    return (w.get("keyword", 0.30) * k
          + w.get("roi",     0.20) * ri
          + w.get("recency", 0.20) * r
          + w.get("emotional", 0.20) * e
          + w.get("valence_match", 0.10) * v
          + w.get("arousal_match", 0.08) * a)         # 第六維度


# ── meta 更新（上位 K 件のみ .md を読む） ────────────────────────────

def _update_recall_meta_by_path(path: Path):
    """recall_count / last_recalled / decay_weight を .md frontmatter に書き戻す

    Phase A: recall_count 更新時に decay_weight も活化（+recall_boost, 上限 1.0）
    """
    try:
        text = path.read_text(encoding="utf-8")
        _, fm_str, body = text.split("---", 2)
        fm = yaml.safe_load(fm_str)
        fm["recall_count"] = fm.get("recall_count", 0) + 1
        fm["last_recalled"] = datetime.datetime.now().strftime("%Y-%m-%d")

        # Phase A: decay_weight 活化（直接用已載入的模組層 CONFIG，不重讀 settings.yaml）
        boost = CONFIG.get("decay", {}).get("recall_boost", 0.10)
        old_dw = float(fm.get("decay_weight", 1.0))
        fm["decay_weight"] = round(min(1.0, old_dw + boost), 4)

        fm_str_new = yaml.dump(fm, allow_unicode=True, sort_keys=False)
        path.write_text(f"---\n{fm_str_new}---{body}", encoding="utf-8")
    except Exception:
        pass


# ── タグフィルタ ──────────────────────────────────────────────────────

def filter_by_tag(entries: list[dict], tag: str) -> list[dict]:
    return [e for e in entries if tag in (e.get("tags") or [])]


# ── 結果フォーマット ──────────────────────────────────────────────────

def format_result(rank: int, entry: dict, score: float,
                  scenario_label: str = "") -> str:
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
    # Strategy D: Scenario ラベル
    if scenario_label:
        lines.insert(3, f"🗂  Scenario: {scenario_label[:60]}")
    # Phase A: decay_weight 顯示
    dw = entry.get("decay_weight")
    if dw is not None:
        lines[1] += f"  decay={dw:.3f}"
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
               update_meta: bool, query_valence: int = 0,
               query_arousal: "int | None" = None) -> list[tuple[float, dict, str]]:
    """
    Phase C 升級版三層 recall。

    Layer 1 — RRF 合議（多策略排名融合）：
      - Strategy A：原始 query（六維度）
      - Strategy B：keyword-only（純關鍵詞，不看 valence/arousal）
      - Strategy C：valence + arousal + emotional（純情緒方向匹配）
      RRF 公式：score = Σ 1 / (k + rank_i)，k=60

    Layer 2 — Tag Graph 擴散：
      被 Strategy A 召回的前 3 篇，找出其 tag 相關日記，給予 TAG_BOOST

    Layer 2.5 — Scenario Boost（Strategy D）：
      L2 Scenario index に query が hit したシナリオのメンバーを SCENARIO_BOOST
      drill_down=True なら seed entry を強制注入

    Layer 3 — MMR 多樣性過濾：
      Maximum Marginal Relevance，避免結果全是同一天的記憶
      λ=0.7（相關性 vs 多樣性的平衡）

    戻り値：(score, entry_dict, scenario_label_or_empty_str) のリスト
    """
    TAG_BOOST = 0.05
    MMR_LAMBDA = 0.7
    # 最大相似度門檻：同一天、同 title 前綴的記憶視為「過於相似」
    MMR_SIM_THRESHOLD = 0.6

    entries = load_index()

    if tag_filter:
        entries = filter_by_tag(entries, tag_filter)

    if not entries:
        return []

    # 自適應 RRF_K：小語料用小 k 拉開鑑別度；語料變大時自動回升
    RRF_K = max(8, len(entries) // 4)

    query_words = query.split() if query else []
    # Phase B: entity 展開
    if query_words:
        query_words = entity_expand(query_words)

    # ── Layer 1: RRF ────────────────────────────────────────────────
    # Strategy A: 完整六維度
    scores_a = {
        e["id"]: recall_score_from_index(query_words, e, query_valence, query_arousal)
        for e in entries
    }
    ranked_a = sorted(scores_a.items(), key=lambda x: x[1], reverse=True)

    # Strategy B: keyword-only（ROI + keyword，忽略 valence/recency/emotional）
    scores_b = {
        e["id"]: (
            WEIGHTS.get("keyword", 0.30) * keyword_score(query_words, e)
            + WEIGHTS.get("roi", 0.20) * roi_score(query_words, e, 0)
        )
        for e in entries
    }
    ranked_b = sorted(scores_b.items(), key=lambda x: x[1], reverse=True)

    # Strategy C: valence + emotional + arousal（情緒方向）
    scores_c = {
        e["id"]: (
            WEIGHTS.get("emotional", 0.20) * emotional_score(e)
            + WEIGHTS.get("valence_match", 0.10) * valence_score(e, query_valence)
            + WEIGHTS.get("arousal_match", 0.08) * arousal_score(e, query_arousal)
        )
        for e in entries
    }
    ranked_c = sorted(scores_c.items(), key=lambda x: x[1], reverse=True)

    # RRF 合票
    rrf_scores: dict[str, float] = {}
    for rank, (eid, _) in enumerate(ranked_a):
        rrf_scores[eid] = rrf_scores.get(eid, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, (eid, _) in enumerate(ranked_b):
        rrf_scores[eid] = rrf_scores.get(eid, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, (eid, _) in enumerate(ranked_c):
        rrf_scores[eid] = rrf_scores.get(eid, 0.0) + 1.0 / (RRF_K + rank + 1)

    # ── Layer 2: Tag Graph 擴散 ──────────────────────────────────────
    if query_words:  # 無 query 時不做擴散（避免無意義 boost）
        tag_graph = load_tag_graph()
        # 取 Strategy A 前 3 名，找出其 tag 相關日記
        top3_ids = [eid for eid, _ in ranked_a[:3]]
        # 各 seed 只取前 MAX_RELATED_PER_SEED 筆（避免超熱 tag 覆蓋全庫）
        MAX_RELATED_PER_SEED = 3
        boosted_ids: set[str] = set()
        for seed_id in top3_ids:
            related = get_related_ids(seed_id, tag_graph)
            # 優先選 Strategy A 排名較高的相關日記
            related_sorted = sorted(related, key=lambda x: scores_a.get(x, 0.0), reverse=True)
            for related_id in related_sorted[:MAX_RELATED_PER_SEED]:
                if related_id not in top3_ids:  # seed 本身不重複 boost
                    boosted_ids.add(related_id)
        # 給相關日記 TAG_BOOST（但不超過原始 rrf_scores 最高值）
        max_rrf = max(rrf_scores.values()) if rrf_scores else 1.0
        for eid in boosted_ids:
            if eid in rrf_scores:
                rrf_scores[eid] = min(max_rrf, rrf_scores[eid] + TAG_BOOST)

    # ── Layer 2.5: Scenario Boost（Strategy D）─────────────────────
    # L2 Scenario index が存在し、query がある場合のみ発動。
    # ヒットしたシナリオのメンバー全員に SCENARIO_BOOST を付与。
    # drill_down=True の場合、seed entry を強制的に候補に押し込む。
    SCENARIO_BOOST = 0.08
    _scn_idx = ROOT / "diary" / "index" / "scenario_index.json"
    _scn_cfg  = CONFIG.get("scenario", {}).get("recall", {})
    # entry_to_scenario: entry_id → scenario_title（format 表示用）
    entry_to_scenario: dict[str, str] = {}

    if query_words and _scn_idx.exists() and _scn_cfg.get("enabled", True):
        with open(_scn_idx, encoding="utf-8") as _f:
            _scn_data = json.load(_f)
        _scenarios = _scn_data.get("scenarios", [])
        _max_rrf   = max(rrf_scores.values()) if rrf_scores else 1.0
        _valid_ids = {e["id"] for e in entries}  # drill-down 安全チェック用

        for scn in _scenarios:
            # シナリオ関連度：tags ＋ keywords へのクエリヒット数
            scn_pool = set(w.lower() for w in
                           (scn.get("tags") or []) + (scn.get("keywords") or []))
            hits = sum(1 for w in query_words if w.lower() in scn_pool)
            if hits == 0:
                continue

            scn_title = scn.get("title", scn.get("scenario_id", ""))

            # メンバー全員に SCENARIO_BOOST（上限: _max_rrf）
            for mid in scn.get("member_ids", []):
                if mid in rrf_scores:
                    rrf_scores[mid] = min(_max_rrf, rrf_scores[mid] + SCENARIO_BOOST)
                entry_to_scenario[mid] = scn_title  # 表示ラベル登録

            # Drill-down: seed entry を rrf_scores に押し込む
            if _scn_cfg.get("drill_down", True):
                drill_top = _scn_cfg.get("drill_down_top", 2)
                for seed_id in (scn.get("seed_ids") or [])[:drill_top]:
                    if seed_id not in rrf_scores and seed_id in _valid_ids:
                        # intensity に比例した基底スコアで注入
                        _inten = (scn.get("emotional_intensity", 5) - 1) / 9.0
                        rrf_scores[seed_id] = SCENARIO_BOOST * _inten
                        entry_to_scenario[seed_id] = scn_title

    # ── Layer 2.7: Vector KNN Boost（Strategy E）────────────────────
    # embeddings.npy が存在する場合のみ発動（任意の Phase 2 機能）。
    # vec_search.py を /usr/local/bin/python3（torch あり）で subprocess 呼び出し。
    # hermes の python3.11 には torch がないため subprocess 分離が必要。
    VEC_BOOST   = 0.06
    VEC_TOP_K   = 5
    _vec_path   = ROOT / "diary" / "index" / "embeddings.npy"
    _vec_script = ROOT / "src" / "vec_search.py"
    import os as _os
    _vec_python = _os.environ.get("AI_DIARY_VEC_PYTHON", "/usr/local/bin/python3")

    if query_words and _vec_path.exists() and _vec_script.exists():
        try:
            import subprocess, json as _json
            _query_str = " ".join(query_words)
            _proc = subprocess.run(
                [_vec_python, str(_vec_script), _query_str, str(VEC_TOP_K)],
                capture_output=True, text=True, timeout=10,
            )
            if _proc.returncode == 0 and _proc.stdout.strip():
                _vec_results = _json.loads(_proc.stdout.strip())
                for _vscore, _veid, _vtitle in _vec_results:
                    if _vscore < 0.30:          # 低信頼度はスキップ
                        continue
                    if str(_veid).startswith("scn-"):  # scenario は除外
                        continue
                    _vboost = VEC_BOOST * _vscore   # score に比例した boost
                    if _veid in rrf_scores:
                        rrf_scores[_veid] += _vboost
                    else:
                        # RRF にない場合は drill-in
                        rrf_scores[_veid] = _vboost
        except Exception:
            pass  # 非致命、インデックスなしや embedder 未インストールでも動く

    # 排序：按 rrf_scores 降序，再用原始 Strategy A score 作 tiebreak
    id_to_entry = {e["id"]: e for e in entries}
    candidates = sorted(
        rrf_scores.items(),
        key=lambda x: (x[1], scores_a.get(x[0], 0.0)),
        reverse=True,
    )

    # ── Layer 3: MMR 多樣性過濾 ──────────────────────────────────────
    def _similarity(e1: dict, e2: dict) -> float:
        """
        簡易相似度：同一天 +0.4，共享 tag 數量加分。
        這裡不用向量，用結構特徵模擬。
        """
        sim = 0.0
        if e1.get("date") == e2.get("date"):
            sim += 0.4
        tags1 = set(e1.get("tags") or [])
        tags2 = set(e2.get("tags") or [])
        if tags1 and tags2:
            overlap = len(tags1 & tags2) / max(len(tags1 | tags2), 1)
            sim += overlap * 0.6
        return min(1.0, sim)

    selected: list[tuple[float, dict, str]] = []
    for eid, rrf_score in candidates:
        if len(selected) >= top_k:
            break
        entry = id_to_entry.get(eid)
        if not entry:
            continue

        scn_label = entry_to_scenario.get(eid, "")

        if not selected:
            # 第一篇直接選
            selected.append((rrf_score, entry, scn_label))
            continue

        # MMR: score = λ × relevance - (1-λ) × max_similarity_to_selected
        max_sim = max(_similarity(entry, sel_e) for _, sel_e, _ in selected)
        if max_sim >= MMR_SIM_THRESHOLD:
            # 過於相似，調整分數（不直接丟棄，給降權機會）
            mmr_score = MMR_LAMBDA * rrf_score - (1 - MMR_LAMBDA) * max_sim
        else:
            mmr_score = rrf_score

        selected.append((mmr_score, entry, scn_label))

    # 最終按分數降序排列
    selected.sort(key=lambda x: x[0], reverse=True)

    if update_meta:
        for _, e, _ in selected:
            p = ROOT / e["path"]
            if p.exists():
                _update_recall_meta_by_path(p)
        # 批次寫回 frontmatter 完成後，整包索引只重建一次（而非每篇呼叫 update_index_entry）
        from roi import build_index as _build_index
        _build_index(verbose=False)

    return selected


# ── CLI ──────────────────────────────────────────────────────��────────

def main():
    parser = argparse.ArgumentParser(description="AI Diary Recall Engine（ROI インデックス版）")
    parser.add_argument("query", nargs="?", default="", help="查詢關鍵詞（空格分隔）")
    parser.add_argument("--tag", type=str, help="只看某個 tag")
    parser.add_argument("--top", type=int, default=TOP_K, help=f"回傳篇數（預設 {TOP_K}）")
    parser.add_argument("--valence", type=int, default=0,
                        help="當前情境 valence（-10～+10）")
    parser.add_argument("--arousal", type=int, default=None,
                        help="當前情境 arousal（0～10 喚起度）。不指定=不影響排序")
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
        query_arousal=args.arousal,
    )

    if args.json:
        output = []
        for score, e, scn_label in results:
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
                "decay_weight":      e.get("decay_weight", None),  # Phase A
                "roi":               e.get("roi", []),
                "preview":           e.get("preview", "")[:300],
                "path":              e.get("path"),
                "scenario":          scn_label,  # Strategy D
            })
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    if not results:
        print("🔍 找不到相關日記ですっ…")
    else:
        print(f"\n🔍 召回結果（top {len(results)}）\n")
        for i, (score, e, scn_label) in enumerate(results, 1):
            print(format_result(i, e, score, scenario_label=scn_label))
        print("=" * 60)

    # Phase III — Pattern Alerts 注入
    try:
        from detect_patterns import get_active_alerts, format_alerts_for_recall
        active = get_active_alerts()
        if active:
            alert_text = format_alerts_for_recall(active)
            print("\n" + "─" * 60)
            print(alert_text)
            print("─" * 60)
    except Exception:
        pass  # 非致命，静默略過


if __name__ == "__main__":
    main()
