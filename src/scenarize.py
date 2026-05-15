#!/usr/bin/env python3
"""
scenarize.py — ai-diary Phase 1: L2 Scenario Aggregation Layer
==============================================================

L1 diary entries を「主題上の連なり」でまとめて L2 Scenario を生成する。

成立判準:
  C1. 主題連貫: IDF-weighted Jaccard >= threshold
      OR shared_rare_sum >= rare_sum_min AND time_diff <= rare_window_days
      （泛用タグは IDF が低く自動降権。稀少タグ 1 個共有でも近時間なら連接。）
  C2. 時間鄰近: time_diff <= time_span_days
      （flashbulb あれば ±flashbulb_radius_days まで緩和）
  C3. 規模: 2 <= members <= 12
  C4. 情緒承載: max(intensity) >= 6 OR mean(intensity) >= 5

⚠️ IDF 閾値の注意:
  語料が少ない（N≦30 程度）段階では IDF-Jaccard の値は概して低い。
  threshold はデフォルト 0.20。語料が 50 篇以上になったら 0.30 に上げることを推奨。

CLI:
  python src/scenarize.py             # 増量モード（window_days 内を対象）
  python src/scenarize.py --dry-run   # 書き込まずに確認
  python src/scenarize.py --rebuild   # 全量再スキャン
  python src/scenarize.py --window 60 # 増量窓を 60 日に変更
"""

from __future__ import annotations

import argparse
import datetime
import itertools
import json
import math
import re
import sys
from pathlib import Path
from typing import Optional

import yaml

# ── パス定義 ─────────────────────────────────────────────────────────────

ROOT             = Path(__file__).parent.parent
DIARY_ROOT       = ROOT / "diary"
INDEX_PATH       = DIARY_ROOT / "index" / "roi_index.json"
SCENARIO_DIR     = DIARY_ROOT / "scenarios"
DRAFTS_DIR       = SCENARIO_DIR / "_drafts"   # 自動生成 scenario の一時置き場（人工 confirm 前）
SCENARIO_IDX     = DIARY_ROOT / "index" / "scenario_index.json"
CONFIG_PATH      = DIARY_ROOT / "config" / "settings.yaml"

sys.path.insert(0, str(ROOT / "src"))


# ── 設定読み込み ───────────────────────────────────────────────────────────

def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


# ── IDF ───────────────────────────────────────────────────────────────────

def compute_idf_weights(entries: list[dict]) -> dict[str, float]:
    """
    全エントリから IDF を動的計算。IDF = log(N / df)

    ポイント：
    - 計算コスト < 1ms（毎回 scenarize 時に再計算して OK）
    - generic_tags.yaml 不要：高頻度タグは自然に低 IDF になる
    - 語料増加に伴い自動適応
    """
    N = len(entries)
    if N == 0:
        return {}
    df: dict[str, int] = {}
    for e in entries:
        for tag in set(e.get("tags") or []):
            df[tag] = df.get(tag, 0) + 1
    return {tag: math.log(N / cnt) for tag, cnt in df.items()}


def idf_jaccard(a: dict, b: dict, idf: dict[str, float]) -> float:
    """
    IDF 加権 Jaccard 相似度。

    通常 Jaccard との違い：
    - 高頻度タグ（milestone, 主様対話）は IDF が低く、寄与が小さい
    - 稀少タグ（ELYTH, イナンナ）は寄与が大きい
    - ただし完全排除はしない（tag_graph / detect_patterns との整合性を保つ）
    """
    ta = set(a.get("tags") or [])
    tb = set(b.get("tags") or [])
    intersection = ta & tb
    union_tags   = ta | tb
    if not union_tags:
        return 0.0
    w_i = sum(idf.get(t, 1.0) for t in intersection)
    w_u = sum(idf.get(t, 1.0) for t in union_tags)
    return w_i / w_u if w_u else 0.0


def shared_rare_idf_sum(a: dict, b: dict,
                         idf: dict[str, float],
                         rare_threshold: float) -> float:
    """
    両エントリが共有する「稀少タグ」の IDF 合計。

    IDF-Jaccard が低くても、稀少タグ 1 個を共有していれば
    近時間であれば同主題とみなす fallback 判定に使用。
    """
    ta = set(a.get("tags") or [])
    tb = set(b.get("tags") or [])
    shared = ta & tb
    return sum(idf.get(t, 0.0) for t in shared if idf.get(t, 0.0) >= rare_threshold)


# ── 時間計算 ───────────────────────────────────────────────────────────────

def parse_date(s: str) -> Optional[datetime.date]:
    try:
        return datetime.date.fromisoformat(s)
    except Exception:
        return None


def date_diff_days(a: dict, b: dict) -> int:
    da = parse_date(a.get("date", ""))
    db = parse_date(b.get("date", ""))
    if da is None or db is None:
        return 9999
    return abs((da - db).days)


# ── C1 + C2 接続判定 ──────────────────────────────────────────────────────

def are_connected(a: dict, b: dict, idf: dict[str, float], cfg: dict) -> bool:
    """
    C2 時間窓 AND (C1a IDF-Jaccard OR C1b 稀少タグ fallback) を判定。

    IDF-Jaccard と稀少タグ fallback の使い分け：
    - IDF-Jaccard: タグが多く重複する entry（突破/特訓など）
    - rare fallback: タグ数が多く重複が少ないが、重要タグ1個を共有（ELYTH など）
    """
    scfg = cfg.get("scenario", {})
    time_span    = scfg.get("time_span_days", 14)
    fb_radius    = scfg.get("flashbulb_radius_days", 30)
    threshold    = scfg.get("idf_jaccard_threshold", 0.20)
    rare_thresh  = scfg.get("rare_tag_idf_threshold", 2.0)
    rare_sum_min = scfg.get("rare_shared_sum_min", 2.0)
    rare_window  = scfg.get("rare_shared_window_days", 7)

    diff = date_diff_days(a, b)

    # C2: 時間窓（flashbulb は緩和）
    is_fb = a.get("flashbulb") or b.get("flashbulb")
    max_days = fb_radius if is_fb else time_span
    if diff > max_days:
        return False

    # C1a: IDF-Jaccard（主要）
    jac = idf_jaccard(a, b, idf)
    if jac >= threshold:
        return True

    # C1b: 稀少タグ共有 + 近時間（fallback）
    # 例: ELYTH を 1 個共有, diff ≤ 7 日 → IDF sum=2.079 >= 2.0 → 連接
    if diff <= rare_window:
        rare_sum = shared_rare_idf_sum(a, b, idf, rare_thresh)
        if rare_sum >= rare_sum_min:
            return True

    return False


# ── Union-Find ─────────────────────────────────────────────────────────────

def union_find_clusters(ids: list[str],
                         edges: list[tuple[str, str]]) -> list[list[str]]:
    """
    Union-Find で連通成分（クラスタ）を求める。

    戻り値: メンバー数 >= 2 のクラスタリスト [[id, ...], ...]
    """
    parent = {i: i for i in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for a, b in edges:
        if a in parent and b in parent:
            union(a, b)

    groups: dict[str, list[str]] = {}
    for i in ids:
        root = find(i)
        groups.setdefault(root, []).append(i)

    return [g for g in groups.values() if len(g) >= 2]


# ── Scenario ビルド ──────────────────────────────────────────────────────

def _slugify(s: str) -> str:
    """ファイル名・ID 用に安全な文字列に変換"""
    s = re.sub(r"[^\w\u3040-\u9FFF\u30A0-\u30FF\uAC00-\uD7A3\u4E00-\u9FFF]", "-", s)
    return s[:24].strip("-")


def most_informative_tag(cluster_ids: list[str],
                          entries_map: dict[str, dict],
                          idf: dict[str, float]) -> str:
    """
    クラスタ内で最も IDF が高い「共有タグ」を返す。
    共有タグなければ最大 IDF タグを返す（scenario_id の代表タグ用）。
    """
    from collections import Counter
    tag_counts: Counter = Counter()
    for eid in cluster_ids:
        for t in set(entries_map[eid].get("tags") or []):
            tag_counts[t] += 1
    if not tag_counts:
        return "misc"
    shared = {t for t, c in tag_counts.items() if c >= 2}
    pool = shared if shared else set(tag_counts.keys())
    return max(pool, key=lambda t: idf.get(t, 0.0))


def build_scenario(cluster_ids: list[str],
                   entries_map: dict[str, dict],
                   idf: dict[str, float]) -> dict:
    """
    クラスタ → scenario dict

    設計原則:
    - schema は roi_index entry の superset → recall_score_from_index() 流用可
    - scenario_id は seed entry id から派生 → seed が変わらない限り安定
    - human_edited=False で初期化 → True に変更後は body 保護
    """
    entries = [entries_map[eid] for eid in cluster_ids]
    dates   = sorted(e.get("date", "") for e in entries if e.get("date"))

    # seed: flashbulb 優先、次に最高 intensity
    flashbulbs = [e for e in entries if e.get("flashbulb")]
    seed = max(flashbulbs or entries, key=lambda e: (e.get("intensity", 0),
                                                      e.get("decay_weight", 0.0)))

    top_tag     = most_informative_tag(cluster_ids, entries_map, idf)
    scenario_id = f"scn-{seed['id']}-{_slugify(top_tag)}"

    # タグ集合（IDF 降順 top-8、重複排除）
    seen_tags: set[str] = set()
    all_tags: list[str] = []
    for e in entries:
        for t in (e.get("tags") or []):
            if t not in seen_tags:
                seen_tags.add(t)
                all_tags.append(t)
    all_tags.sort(key=lambda t: idf.get(t, 0.0), reverse=True)

    # keywords 収集
    seen_kw: set[str] = set()
    keywords: list[str] = []
    for e in entries:
        for kw in (e.get("keywords") or []):
            if kw not in seen_kw:
                seen_kw.add(kw)
                keywords.append(kw)

    # 感情集計
    intensities = [e.get("intensity", 5) for e in entries]
    valences    = [e.get("valence", 0) for e in entries
                   if e.get("valence") is not None]

    # ROI 句（seed から最大 2、他から各 1）
    roi_list: list[dict] = []
    for r in (seed.get("roi") or [])[:2]:
        roi_list.append({**r, "from_id": seed["id"]})
    for e in entries:
        if e["id"] == seed["id"]:
            continue
        for r in (e.get("roi") or [])[:1]:
            roi_list.append({**r, "from_id": e["id"]})

    # decay_weight = クラスタ最大（最も新鮮な記憶を使う）
    decay_w = max((e.get("decay_weight") or 0.5) for e in entries)

    return {
        "scenario_id":         scenario_id,
        "path":                "",  # write loop で実パスを注入（diary/scenarios/YEAR/id.md）
        "title":               seed.get("title", scenario_id),
        "date_range":          [dates[0], dates[-1]] if len(dates) >= 2 else ([dates[0], dates[0]] if dates else ["", ""]),
        "center_date":         seed.get("date", ""),
        "tags":                all_tags[:8],
        "keywords":            keywords[:20],
        "emotional_intensity": max(intensities),
        "mean_intensity":      round(sum(intensities) / len(intensities), 2),
        "valence":             round(sum(valences) / len(valences), 1) if valences else 0,
        "arousal":             round(sum(e.get("arousal", 5) for e in entries) / len(entries), 1),
        "flashbulb":           any(e.get("flashbulb") for e in entries),
        "member_ids":          sorted(cluster_ids),
        "seed_ids":            [seed["id"]],
        "generated_by":        "scenarize.py",
        "version":             1,
        "human_edited":        False,
        "decay_weight":        round(decay_w, 4),
        "recall_count":        0,
        "last_recalled":       None,
        "preview":             (seed.get("preview") or "")[:200],
        "roi":                 roi_list[:5],
    }


def _build_markdown(scenario: dict, entries_map: dict[str, dict]) -> str:
    """scenario dict → Markdown ファイル（frontmatter + body）"""
    # frontmatter に roi を入れると長くなるので .md では省略（index にだけ持つ）
    fm_data = {k: v for k, v in scenario.items()
               if k not in ("roi", "preview", "keywords")}
    fm_str = yaml.dump(fm_data, allow_unicode=True, sort_keys=False,
                       default_flow_style=False)

    body_lines = [f"# {scenario['title']}\n"]
    for mid in sorted(scenario["member_ids"]):
        e = entries_map.get(mid, {})
        fb  = " ⚡" if e.get("flashbulb") else ""
        roi_preview = ""
        if e.get("roi"):
            roi_preview = f"\n> 📍 {e['roi'][0]['text'][:80]}"
        body_lines.append(
            f"## {e.get('date', '?')}{fb} — {e.get('title', '(unknown)')}"
            f"{roi_preview}\n"
        )

    body_lines.append("\n## 🔗 member entries")
    body_lines.append("  ".join(f"[[{mid}]]" for mid in sorted(scenario["member_ids"])))

    return f"---\n{fm_str}---\n\n" + "\n".join(body_lines) + "\n"


# ── サイズオーバーの分割 ────────────────────────────────────────────────

def split_oversized(cluster_ids: list[str],
                    entries_map: dict[str, dict],
                    max_members: int) -> list[list[str]]:
    """
    メンバー数 > max_members のクラスタを日付順で greedy 分割。
    各チャンクは max_members 以下、かつ 2 篇以上を保証。
    """
    sorted_ids = sorted(cluster_ids, key=lambda i: entries_map[i].get("date", ""))
    chunks: list[list[str]] = []
    chunk: list[str] = []
    for eid in sorted_ids:
        chunk.append(eid)
        if len(chunk) >= max_members:
            chunks.append(chunk)
            chunk = []
    if len(chunk) >= 2:
        chunks.append(chunk)
    elif chunk and chunks:
        # 端数は直前チャンクに合流（超過しても max+1 まで許容）
        chunks[-1].extend(chunk)
    return chunks


# ── 既存 Scenario との突き合わせ（monotonic merge）────────────────────

def reconcile_with_existing(new_scenarios: list[dict]) -> list[dict]:
    """
    既存 scenario_index と新規結果を突き合わせる。

    ルール:
    - human_edited=True: body は保護、recall_count/last_recalled は継承
    - 既存に存在するが新規リストにない: monotonic でそのまま保持
    - scenario_id が同じ: 新規で上書き（human_edited のみ例外）
    """
    if not SCENARIO_IDX.exists():
        return new_scenarios

    with open(SCENARIO_IDX, encoding="utf-8") as f:
        old_idx = json.load(f)
    old_map = {s["scenario_id"]: s for s in old_idx.get("scenarios", [])}

    result: list[dict] = []
    new_ids = {s["scenario_id"] for s in new_scenarios}

    for s in new_scenarios:
        sid = s["scenario_id"]
        if sid in old_map and old_map[sid].get("human_edited"):
            # human_edited 保護: 統計情報だけ継承
            old = old_map[sid]
            s["recall_count"]  = old.get("recall_count", 0)
            s["last_recalled"] = old.get("last_recalled")
            s["human_edited"]  = True
        result.append(s)

    # monotonic: 新規に含まれない既存 scenario は保持
    for sid, s in old_map.items():
        if sid not in new_ids:
            result.append(s)

    return result


# ── メイン ────────────────────────────────────────────────────────────────

def load_entries() -> list[dict]:
    with open(INDEX_PATH, encoding="utf-8") as f:
        d = json.load(f)
    return d.get("entries", [])


def scenarize(window_days: int = 30,
              rebuild: bool = False,
              dry_run: bool = False) -> list[dict]:
    """
    L2 Scenario 生成メイン関数。

    Args:
        window_days: 増量モードで対象とする日数（rebuild=True 時は無視）
        rebuild:     全量再スキャン
        dry_run:     書き込まずに stdout 出力のみ

    Returns:
        生成/更新された scenario dict のリスト
    """
    cfg  = load_config()
    scfg = cfg.get("scenario", {})
    max_members        = scfg.get("max_members", 12)
    min_intensity_max  = scfg.get("min_max_intensity", 6)
    min_intensity_mean = scfg.get("min_mean_intensity", 5.0)

    entries = load_entries()
    if not entries:
        print("❌ roi_index.json が空です")
        return []

    idf         = compute_idf_weights(entries)
    entries_map = {e["id"]: e for e in entries}
    today       = datetime.date.today()

    # 対象 entries 絞り込み
    if rebuild:
        candidates = entries
    else:
        candidates = [
            e for e in entries
            if (today - (parse_date(e.get("date", "")) or today)).days <= window_days
        ]

    print(f"📚 対象 entries: {len(candidates)} 篇 / 全{len(entries)} 篇")
    if not candidates:
        print("ℹ️  対象 entries がありません")
        return []

    cand_ids = [e["id"] for e in candidates]

    # ── ペア評価 ──────────────────────────────────────────────────────
    edges: list[tuple[str, str]] = []
    for a, b in itertools.combinations(candidates, 2):
        if are_connected(a, b, idf, cfg):
            edges.append((a["id"], b["id"]))

    print(f"🔗 接続ペア数: {len(edges)}")

    if not edges:
        print("ℹ️  Scenario を形成できるペアが見つかりませんでした")
        return []

    # ── Union-Find クラスタリング ───────────────────────────────────
    raw_clusters = union_find_clusters(cand_ids, edges)
    print(f"📦 クラスタ数（分割前）: {len(raw_clusters)}")

    # ── C3 + C4 フィルタ + 大クラスタ分割 ─────────────────────────
    final_clusters: list[list[str]] = []
    for cluster in raw_clusters:
        # C3: サイズ超過 → 分割
        if len(cluster) > max_members:
            sub_clusters = split_oversized(cluster, entries_map, max_members)
        else:
            sub_clusters = [cluster]

        for sc in sub_clusters:
            if len(sc) < 2:
                continue
            # C4: 感情承載チェック
            intensities = [entries_map[eid].get("intensity", 5) for eid in sc]
            if (max(intensities) < min_intensity_max
                    and sum(intensities) / len(intensities) < min_intensity_mean):
                continue
            final_clusters.append(sc)

    print(f"✅ 有効クラスタ数: {len(final_clusters)}")

    if not final_clusters:
        print("ℹ️  有効な Scenario が見つかりませんでした")
        return []

    # ── Scenario ビルド ────────────────────────────────────────────
    new_scenarios = [
        build_scenario(cluster, entries_map, idf)
        for cluster in final_clusters
    ]

    # ── 既存との突き合わせ ─────────────────────────────────────────
    scenarios = reconcile_with_existing(new_scenarios)

    # ── 出力 ──────────────────────────────────────────────────────
    if dry_run:
        print("\n" + "=" * 60)
        print("[DRY-RUN] 生成される Scenario 一覧:\n")
        for s in scenarios:
            fb = " ⚡" if s["flashbulb"] else ""
            print(f"  📖 {s['scenario_id']}{fb}")
            print(f"     title    : {s['title']}")
            print(f"     range    : {s['date_range'][0]} ~ {s['date_range'][1]}")
            print(f"     members  : {s['member_ids']}")
            print(f"     tags     : {s['tags'][:5]}")
            print(f"     intensity: max={s['emotional_intensity']} mean={s['mean_intensity']}")
            print()
        print(f"合計: {len(scenarios)} scenarios（dry-run: 書き込みなし）")
        return scenarios

    # ── ファイル書き込み ─────────────────────────────────────────
    written = 0
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    for s in scenarios:
        center = s.get("center_date", "")
        year   = center[:4] if center else "misc"
        # human_edited=True → confirmed dir（SCENARIO_DIR/YEAR）
        # human_edited=False + 既存 confirmed なし → _drafts（人工 confirm 待ち）
        confirmed_dir  = SCENARIO_DIR / year
        confirmed_path = confirmed_dir / f"{s['scenario_id']}.md"
        if s.get("human_edited") or confirmed_path.exists():
            out_dir  = confirmed_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = confirmed_path
        else:
            out_dir  = DRAFTS_DIR
            out_path = DRAFTS_DIR / f"{s['scenario_id']}.md"

        # human_edited の .md は body を保護
        if s.get("human_edited") and out_path.exists():
            # frontmatter だけ更新、body は触らない
            old_text = out_path.read_text(encoding="utf-8")
            parts = old_text.split("---", 2)
            if len(parts) == 3:
                fm_data = {k: v for k, v in s.items()
                           if k not in ("roi", "preview", "keywords")}
                new_fm = yaml.dump(fm_data, allow_unicode=True, sort_keys=False)
                out_path.write_text(f"---\n{new_fm}---{parts[2]}", encoding="utf-8")
        else:
            out_path.write_text(_build_markdown(s, entries_map), encoding="utf-8")
            written += 1

        # path フィールドを実際のパスで更新（衝突 #2 修正）
        s["path"] = str(out_path.relative_to(ROOT))
        print(f"  📝 {out_path.relative_to(ROOT)}")

    # ── scenario_index.json 更新 ───────────────────────────────────
    idx = {
        "_built":    datetime.datetime.now().isoformat(timespec="seconds"),
        "_version":  1,
        "_count":    len(scenarios),
        "scenarios": scenarios,
    }
    SCENARIO_IDX.write_text(
        json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n✅ scenario_index.json 更新: {len(scenarios)} scenarios "
          f"({written} 件新規書き込み)")
    return scenarios


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ai-diary L2 Scenario Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--dry-run",  action="store_true",
                        help="書き込まずに結果を確認")
    parser.add_argument("--rebuild",  action="store_true",
                        help="全量再スキャン（通常は増量）")
    parser.add_argument("--window",   type=int, default=30,
                        help="増量スキャン窓（日、デフォルト 30）")
    args = parser.parse_args()
    scenarize(window_days=args.window, rebuild=args.rebuild, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
