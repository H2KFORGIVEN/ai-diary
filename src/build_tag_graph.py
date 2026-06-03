#!/usr/bin/env python3
"""
build_tag_graph.py — Phase C: Tag Graph 反向索引建立工具

建立 diary/index/tag_graph.json：
  {
    "tag_name": ["diary_id_1", "diary_id_2", ...],
    ...
  }

此索引用於 Phase C 的 Tag Graph 擴散——
當某篇日記被召回時，同 tag 的其他日記會得到額外分數（擴散 boost）。

用法：
  python src/build_tag_graph.py        # 建立 tag_graph.json
  python src/build_tag_graph.py --show # 建立並顯示內容摘要
"""

import json
from collections import defaultdict
from pathlib import Path

import yaml
import sys
sys.path.insert(0, str(Path(__file__).parent))
from roi import is_diary_entry

ROOT = Path(__file__).parent.parent
DIARY_ROOT = ROOT / "diary"
TAG_GRAPH_PATH = DIARY_ROOT / "index" / "tag_graph.json"


def build_tag_graph(verbose: bool = False) -> dict[str, list[str]]:
    """
    掃描所有日記，建立 tag → [diary_id] 反向索引。
    Returns: tag_graph dict
    """
    TAG_GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)

    graph: dict[str, list[str]] = defaultdict(list)
    count = 0

    for md in sorted(DIARY_ROOT.rglob("*.md")):
        if not is_diary_entry(md):
            continue

        try:
            text = md.read_text(encoding="utf-8")
            if not text.startswith("---"):
                continue
            _, fm_str, _ = text.split("---", 2)
            fm = yaml.safe_load(fm_str)
            if not fm or not isinstance(fm, dict):
                continue

            diary_id = md.stem
            tags = fm.get("tags") or []
            for tag in tags:
                if diary_id not in graph[tag]:
                    graph[tag].append(diary_id)
            count += 1
        except Exception:
            continue

    # 轉成普通 dict，排序以利 diff
    result = {tag: sorted(ids) for tag, ids in sorted(graph.items())}

    from datetime import datetime
    data = {
        "_built": datetime.now().isoformat(timespec="seconds"),
        "_count": count,
        "graph": result,
    }
    TAG_GRAPH_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    if verbose:
        print(f"✅ Tag Graph built: {count} diaries, {len(result)} tags → {TAG_GRAPH_PATH}")
        print("\n📊 Tag 分布（前 15）：")
        for tag, ids in sorted(result.items(), key=lambda x: len(x[1]), reverse=True)[:15]:
            print(f"  [{len(ids):2d}篇] {tag}")

    return result


def load_tag_graph() -> dict[str, list[str]]:
    """讀取 tag_graph.json，自動處理不存在的情況"""
    if not TAG_GRAPH_PATH.exists():
        return build_tag_graph(verbose=False)
    try:
        data = json.loads(TAG_GRAPH_PATH.read_text(encoding="utf-8"))
        return data.get("graph", {})
    except Exception:
        return {}


def get_related_ids(diary_id: str, graph: dict[str, list[str]] | None = None) -> list[str]:
    """
    給定一個 diary_id，回傳所有共享 tag 的其他 diary_id 列表（去重、排除自身）。
    用於 Tag Graph 擴散：被召回的日記 → 找出相關日記 → 加 boost。
    """
    if graph is None:
        graph = load_tag_graph()

    related: set[str] = set()
    for tag, ids in graph.items():
        if diary_id in ids:
            for i in ids:
                if i != diary_id:
                    related.add(i)
    return sorted(related)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Phase C: Tag Graph Builder")
    parser.add_argument("--show", action="store_true", help="顯示內容摘要")
    args = parser.parse_args()
    build_tag_graph(verbose=True)
