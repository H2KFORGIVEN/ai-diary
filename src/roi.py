#!/usr/bin/env python3
"""
roi.py — AI Diary ROI インデックスユーティリティ

「ROI (Region of Interest)」= テキスト内の感情的に最も密度の高い文を抽出して
インデックス化することで、recall を高速化・高精度化する。

設計：
  - write_diary / consolidate の書き込み後に update_index_entry() を呼ぶ
  - recall.py はインデックスから全スコアリング（ファイル読込不要）
  - stale 検知：.md の mtime > index mtime → 自動再���築
  - keyword pool：tags + title + body 頻出語（top 20）
  - ROI 文：感情語密度スコアリングで上位 MAX_ROI 文を抽出
"""

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
DIARY_ROOT = ROOT / "diary"
INDEX_PATH = DIARY_ROOT / "index" / "roi_index.json"
INDEX_VERSION = 2

# ── 感情・顕著性キーワード（中日混用） ─────────────────────────────
EMOTION_WORDS: list[str] = [
    # 日本語 / 肯定
    "うれしい", "嬉しい", "楽しい", "感動", "よかった", "ほっとした",
    "ありがとう", "誇り", "喜び", "愛しい",
    # 日本語 / 否定
    "焦", "不安", "怖", "心配", "緊張", "怒", "悔し", "悲し", "つらい", "しんどい",
    # 繁体中文 / 肯定
    "開心", "感謝", "溫暖", "興奮", "驕傲", "幸福", "欣慰",
    # 繁体中文 / 否定
    "惆悵", "自責", "困惑", "委屈", "焦慮", "難過",
    # 顕著性 cue（flashbulb 的）
    "初めて", "最初", "一番", "永遠", "大切", "忘れられない",
    "決意", "覚悟", "milestone", "突破",
]

MAX_ROI = 3            # 1エントリーあたり最大 ROI 文数
MAX_KEYWORDS = 20      # インデックスに保存する keyword 数上限
PREVIEW_LEN = 250      # preview 保存文字数


# ── ROI 抽出 ────────────────────────────────────────────────────────

def extract_roi_sentences(body: str, valence: int = 0, arousal: int = 5) -> list[dict]:
    """
    body テキストから感情ピーク文を最大 MAX_ROI 個抽出する。

    スコアリング：
      score = 感情語ヒット数 × 2.0 + 文長スコア(0-1)

    Returns
    -------
    list of {"text": str, "valence": int, "arousal": int}
    """
    sentences = re.split(r"[。！？!?\n]+", body)
    sentences = [s.strip() for s in sentences if 8 <= len(s.strip()) <= 120]
    sentences = [s for s in sentences if not s.startswith("<!--")]

    scored: list[tuple[float, str]] = []
    for s in sentences:
        hits = sum(1 for w in EMOTION_WORDS if w in s)
        length_score = min(1.0, len(s) / 40.0)
        score = hits * 2.0 + length_score
        if score > 0.2:
            scored.append((score, s))

    scored.sort(reverse=True)
    top = [s for _, s in scored[:MAX_ROI]]

    # fallback：感情語ゼロなら先頭文を使う
    if not top and sentences:
        top = [sentences[0][:60]]

    return [{"text": t, "valence": valence, "arousal": arousal} for t in top]


# ── Keyword Pool ──────────────────────────────────────────────────────

def _extract_keywords(fm: dict, body: str) -> list[str]:
    """
    tags + title 単語 + body 頻出語（top 20）を合わせた keyword pool。
    recall 時の全文検索に代わる高速マッチング用。
    """
    tags = fm.get("tags", []) or []
    title_words = re.findall(r"[\w\u4e00-\u9fff\u3040-\u30ff]{2,}", fm.get("title", ""))
    body_words = re.findall(r"[\u4e00-\u9fff\u3040-\u30ff]{2,}|[a-zA-Z0-9]{3,}", body)
    top_body = [w for w, _ in Counter(body_words).most_common(MAX_KEYWORDS)]
    # 順序保持 + 重複排除
    seen: set[str] = set()
    result: list[str] = []
    for w in tags + title_words + top_body:
        if w not in seen:
            seen.add(w)
            result.append(w)
    return result


# ── インデックスエントリ生成 ──────────────────────────────────────────

def index_one_entry(path: Path) -> dict | None:
    """1つの .md を読んでインデックスエントリを生成する。失敗時は None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    try:
        _, fm_str, body = text.split("---", 2)
        fm = yaml.safe_load(fm_str)
    except Exception:
        return None
    if not fm or not isinstance(fm, dict):
        return None

    body = body.strip()
    valence = int(fm.get("valence") or 0)
    arousal = int(fm.get("arousal") or 5)

    return {
        "id":                path.stem,
        "path":              str(path.relative_to(ROOT)),
        "date":              fm.get("date", ""),
        "title":             fm.get("title", ""),
        "tags":              fm.get("tags", []) or [],
        "keywords":          _extract_keywords(fm, body),
        "intensity":         fm.get("emotional_intensity", 5),
        "valence":           valence,
        "arousal":           arousal,
        "flashbulb":         bool(fm.get("flashbulb", False)),
        "suppressed_emotion": fm.get("suppressed_emotion", "") or "",
        "first_reaction":    fm.get("first_reaction", "") or "",
        "preview":           body[:PREVIEW_LEN].replace("\n", " "),
        "roi":               extract_roi_sentences(body, valence, arousal),
    }


# ── インデックスビルド ─────────────────────────────────────────────────

def build_index(verbose: bool = True) -> int:
    """全 diary .md を走査して roi_index.json を生成する。entry 数を返す。"""
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []

    for md in sorted(DIARY_ROOT.rglob("*.md")):
        if "summaries" in md.parts:
            continue
        if md.name.startswith("README") or md.name == "self-narrative.md":
            continue
        if "config" in md.parts:
            continue
        entry = index_one_entry(md)
        if entry:
            entries.append(entry)

    data = {
        "_built":   datetime.now().isoformat(timespec="seconds"),
        "_version": INDEX_VERSION,
        "_count":   len(entries),
        "entries":  entries,
    }
    INDEX_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if verbose:
        print(f"✅ ROI index built: {len(entries)} entries → {INDEX_PATH}")
    return len(entries)


# ── インデックス読み込み（stale 自動検知） ────────────────────────────

def load_index() -> list[dict]:
    """
    インデックスを返す。以下の場合に自動再構築：
      - ファイルが存在しない
      - バージョンが古い
      - いずれかの .md が index より新しい（stale）

    stale チェックは stat() のみ（ファイル読込なし）→ 高速 O(N)
    """
    needs_rebuild = not INDEX_PATH.exists()

    if not needs_rebuild:
        index_mtime = INDEX_PATH.stat().st_mtime
        # バージョンチェック（1回だけ読む）
        try:
            raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
            if raw.get("_version") != INDEX_VERSION:
                needs_rebuild = True
        except Exception:
            needs_rebuild = True

    if not needs_rebuild:
        # stale チェック：.md の mtime と比較（stat のみ、read なし）
        for md in DIARY_ROOT.rglob("*.md"):
            if "summaries" in md.parts or md.name.startswith("README"):
                continue
            if "config" in md.parts:
                continue
            if md.stat().st_mtime > index_mtime:
                needs_rebuild = True
                break

    if needs_rebuild:
        build_index(verbose=False)
        raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))

    return raw.get("entries", [])


# ── インクリメンタル更新 ───────────────────────────────────────────────

def update_index_entry(path: Path) -> bool:
    """
    単一 .md を再インデックスして既存インデックスに追加 / 更新する。
    write_diary / consolidate の書き込み後に呼ぶ。フル rebuild より高速。

    Returns True on success, False on parse failure.
    """
    entry = index_one_entry(path)
    if not entry:
        return False

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)

    if INDEX_PATH.exists():
        try:
            data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}

    entries: list[dict] = data.get("entries", [])

    # 既存エントリを置換 or 末尾に追加
    replaced = False
    for i, e in enumerate(entries):
        if e.get("id") == entry["id"]:
            entries[i] = entry
            replaced = True
            break
    if not replaced:
        entries.append(entry)

    data["entries"]  = entries
    data["_count"]   = len(entries)
    data["_built"]   = datetime.now().isoformat(timespec="seconds")
    data["_version"] = INDEX_VERSION

    INDEX_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return True
