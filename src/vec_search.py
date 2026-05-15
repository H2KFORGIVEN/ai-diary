#!/usr/local/bin/python3
"""
vec_search.py — ai-diary subprocess helper
Strategy E（Vector KNN Boost）用：recall.py から subprocess で呼び出す。

理由：recall.py は hermes の python3.11 で動くが、torch は /usr/local/bin/python3 にしかない。
subprocess で分離することで、どちらの環境でも動作する。

用法：
  python3 src/vec_search.py "query text" [top_k]

出力：JSON array [(score, entry_id, title), ...]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main():
    if len(sys.argv) < 2:
        print("[]")
        return

    query = sys.argv[1].strip()
    top_k = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    if not query:
        print("[]")
        return

    try:
        from vec_index import VecIndex
        from embedder import encode_query

        vidx = VecIndex(auto_load=True)
        if vidx.is_empty:
            print("[]")
            return

        qvec = encode_query(query)
        results = vidx.search(query_vec=qvec, top_k=top_k)
        # [(score, entry_id, title), ...]
        print(json.dumps(results))
    except Exception as e:
        print("[]", file=sys.stdout)
        print(f"vec_search error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
