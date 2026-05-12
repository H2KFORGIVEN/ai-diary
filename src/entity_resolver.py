#!/usr/bin/env python3
"""
entity_resolver.py — Phase B: 實體台帳解析器

功能：
  1. resolve(name)   → 回傳正規化 entity_id（找不到回傳 None）
  2. expand(query)   → 展開 query 詞列表（把每個詞替換為其 canonical + aliases）
  3. canonical(name) → 回傳標準名稱字串

比對策略（按優先順序）：
  1. 精確比對（大小寫不敏感）
  2. Levenshtein 相似度 ≥ FUZZY_THRESHOLD（預設 0.80）
  → 不使用 LLM，純本地比對，零延遲

用法：
  from entity_resolver import resolve, expand, canonical

  resolve("主")       → "master"
  canonical("47")     → "主様"
  expand(["主様", "燈"]) → ["主様", "47", "星詠者47", "主", ..., "Nanoleaf", "燈", ...]
"""

import json
import re
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
LEDGER_PATH = ROOT / "diary" / "config" / "entity_ledger.json"

FUZZY_THRESHOLD = 0.80  # Levenshtein 相似度門檻，低於此值不匹配


# ── 讀取台帳 ──────────────────────────────────────────────────────────

def _load_ledger() -> list[dict]:
    """讀取 entity_ledger.json，失敗回傳空列表"""
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        return data.get("entities", [])
    except Exception:
        return []


# ── Levenshtein 相似度（不依賴外部庫） ──────────────────────────────

def _levenshtein_similarity(a: str, b: str) -> float:
    """回傳 0-1 的相似度（1 = 完全相同）"""
    a, b = a.lower(), b.lower()
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0

    # DP matrix
    rows, cols = len(a) + 1, len(b) + 1
    dp = list(range(cols))
    for i in range(1, rows):
        prev = dp[:]
        dp[0] = i
        for j in range(1, cols):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev[j - 1] + cost)

    distance = dp[cols - 1]
    max_len = max(len(a), len(b))
    return 1.0 - distance / max_len


# ── 核心 API ─────────────────────────────────────────────────────────

def resolve(name: str) -> Optional[str]:
    """
    name → entity_id

    比對順序：精確比對（忽略大小寫）→ Levenshtein ≥ FUZZY_THRESHOLD
    找不到回傳 None。
    """
    name_lower = name.lower().strip()
    entities = _load_ledger()

    # Pass 1: 精確比對
    for entity in entities:
        aliases = [a.lower() for a in entity.get("aliases", [])]
        if name_lower in aliases:
            return entity["id"]

    # Pass 2: Levenshtein 模糊比對
    best_id: Optional[str] = None
    best_sim = 0.0
    for entity in entities:
        for alias in entity.get("aliases", []):
            sim = _levenshtein_similarity(name_lower, alias.lower())
            if sim > best_sim:
                best_sim = sim
                best_id = entity["id"]

    if best_sim >= FUZZY_THRESHOLD:
        return best_id
    return None


def canonical(name: str) -> Optional[str]:
    """
    name → canonical 名稱字串
    例：canonical("47") → "主様"
    """
    entity_id = resolve(name)
    if entity_id is None:
        return None
    entities = _load_ledger()
    for entity in entities:
        if entity["id"] == entity_id:
            return entity.get("canonical", name)
    return None


def get_aliases(name: str) -> list[str]:
    """
    name → 同一實體的所有別名（含 canonical）
    找不到回傳 [name]（原樣傳回）
    """
    entity_id = resolve(name)
    if entity_id is None:
        return [name]
    entities = _load_ledger()
    for entity in entities:
        if entity["id"] == entity_id:
            return list(entity.get("aliases", [name]))
    return [name]


def expand(query_words: list[str]) -> list[str]:
    """
    query 詞列表展開：把每個詞替換成其 canonical + 所有 aliases（去重）

    用於 recall.py 的 query 展開：
      expand(["主様"]) → ["主様", "47", "星詠者47", "主", "user", "master", "しゅさま"]
    """
    result: list[str] = []
    seen: set[str] = set()

    for word in query_words:
        aliases = get_aliases(word)
        for a in aliases:
            if a not in seen:
                seen.add(a)
                result.append(a)

    return result


def normalize_tags(tags: list[str]) -> list[str]:
    """
    tags 列表正規化：把非標準別名替換成 canonical 名稱。
    write_diary 時使用，確保日記 tags 用統一名稱。

    例：normalize_tags(["主", "燈"]) → ["主様", "Nanoleaf"]
    """
    result = []
    for tag in tags:
        c = canonical(tag)
        result.append(c if c is not None else tag)
    return result


# ── 台帳管理 API ──────────────────────────────────────────────────────

def add_entity(
    entity_id: str,
    canonical_name: str,
    entity_type: str,
    aliases: list[str],
    tags: list[str] | None = None,
    note: str = "",
) -> bool:
    """
    新增一個實體到台帳。已存在則跳過（回傳 False）。
    成功回傳 True。
    """
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        entities = data.get("entities", [])

        # 檢查 id 是否已存在
        existing_ids = {e["id"] for e in entities}
        if entity_id in existing_ids:
            return False

        new_entity = {
            "id": entity_id,
            "canonical": canonical_name,
            "type": entity_type,
            "aliases": aliases,
            "tags": tags or [],
            "note": note,
        }
        entities.append(new_entity)
        data["entities"] = entities

        import datetime
        data["_updated"] = datetime.date.today().isoformat()
        LEDGER_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def add_alias(entity_id: str, new_alias: str) -> bool:
    """
    對現有實體新增一個別名。
    成功回傳 True，找不到 entity_id 回傳 False。
    """
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        entities = data.get("entities", [])

        for entity in entities:
            if entity["id"] == entity_id:
                aliases = entity.get("aliases", [])
                if new_alias not in aliases:
                    aliases.append(new_alias)
                    entity["aliases"] = aliases

                import datetime
                data["_updated"] = datetime.date.today().isoformat()
                LEDGER_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                return True
        return False
    except Exception:
        return False


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, sys

    parser = argparse.ArgumentParser(description="Phase B: Entity Resolver CLI")
    sub = parser.add_subparsers(dest="cmd")

    p_resolve = sub.add_parser("resolve", help="name → entity_id")
    p_resolve.add_argument("name")

    p_expand = sub.add_parser("expand", help="展開 query 詞列表")
    p_expand.add_argument("words", nargs="+")

    p_list = sub.add_parser("list", help="列出所有實體")

    p_add = sub.add_parser("add-alias", help="新增別名")
    p_add.add_argument("entity_id")
    p_add.add_argument("alias")

    args = parser.parse_args()

    if args.cmd == "resolve":
        eid = resolve(args.name)
        c = canonical(args.name)
        print(f"'{args.name}' → entity_id={eid}  canonical={c}")

    elif args.cmd == "expand":
        expanded = expand(args.words)
        print(f"展開前：{args.words}")
        print(f"展開後：{expanded}")

    elif args.cmd == "list":
        for e in _load_ledger():
            print(f"[{e['id']}] {e['canonical']} ({e['type']})")
            print(f"  aliases: {', '.join(e.get('aliases', []))}")

    elif args.cmd == "add-alias":
        ok = add_alias(args.entity_id, args.alias)
        print("✅ 新增成功" if ok else "❌ 找不到 entity_id")

    else:
        parser.print_help()
