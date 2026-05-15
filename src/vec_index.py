#!/usr/bin/env python3
"""
vec_index.py — ai-diary — AI Character Diary System
Phase 2: numpy KNN ベクトルインデックス（インメモリ）

設計:
  - ファイル: diary/index/embeddings.npy        (N, 384) float32
              diary/index/embedding_meta.json   [{id, path, date, title}, ...]
              diary/index/embedding_hash.json   {entry_id: content_hash}
  - クエリ: encode_query(q) → cosine sim (内積, L2 正規化済み) → top-K
  - 増分更新: content_hash が変わったエントリのみ再 encode

用法:
  from vec_index import VecIndex
  idx = VecIndex()
  results = idx.search(query_text="ELYTH 感動", top_k=5)
  # returns: [(score, entry_id, title), ...]

コマンドライン:
  python src/vec_index.py "ELYTH 感動" --top 5
"""

import json
import sys
from pathlib import Path
from typing import Optional

import numpy as np

ROOT = Path(__file__).parent.parent
DIARY_ROOT = ROOT / "diary"
INDEX_DIR  = DIARY_ROOT / "index"

EMBEDDINGS_PATH = INDEX_DIR / "embeddings.npy"
META_PATH       = INDEX_DIR / "embedding_meta.json"
HASH_PATH       = INDEX_DIR / "embedding_hash.json"

DIM = 384  # multilingual-e5-small の次元数


class VecIndex:
    """
    numpy ベースのインメモリ KNN インデックス。
    常駐プロセスなら embeddings は 1 回ロードするだけ（< 1ms 検索）。
    スクリプト起動は embedder の cold-start ~3s が支配（Phase 2 recall daemon で解決予定）。
    """

    def __init__(self, auto_load: bool = True):
        self._vecs: Optional[np.ndarray] = None   # (N, 384)
        self._meta: list[dict] = []
        if auto_load:
            self.load()

    # ── ロード ────────────────────────────────────────���─────────────────

    def load(self) -> bool:
        """
        保存済みインデックスをロード。
        ファイルが存在しない場合は空状態のまま（False を返す）。
        """
        if not EMBEDDINGS_PATH.exists() or not META_PATH.exists():
            return False
        try:
            self._vecs = np.load(str(EMBEDDINGS_PATH))
            self._meta = json.loads(META_PATH.read_text(encoding="utf-8"))
            return True
        except Exception as e:
            print(f"⚠️  VecIndex load failed: {e}", file=sys.stderr)
            self._vecs = None
            self._meta = []
            return False

    # ── 検索 ────────────────────────────────────────────────────────────

    def search(
        self,
        query_vec: Optional[np.ndarray] = None,
        query_text: Optional[str] = None,
        top_k: int = 5,
    ) -> list[tuple[float, str, str]]:
        """
        KNN 検索。query_vec または query_text のどちらかを渡す。

        Args:
            query_vec:  事前に encode_query() した (384,) ベクトル
            query_text: テキスト（embedder.encode_query で内部 encode）
            top_k:      返す件数

        Returns:
            [(cosine_score, entry_id, title), ...] 降順
        """
        if self._vecs is None or len(self._meta) == 0:
            return []

        if query_vec is None:
            if query_text is None:
                raise ValueError("query_vec または query_text が必要です")
            sys.path.insert(0, str(ROOT / "src"))
            from embedder import encode_query
            query_vec = encode_query(query_text)

        if query_vec.shape != (DIM,):
            raise ValueError(f"query_vec は ({DIM},) である必要があります")

        # コサイン類似度 = 内積（L2 正規化済み前提）
        sims = self._vecs @ query_vec  # (N,)

        # top-K を効率よく取る（argpartition）
        n = len(self._meta)
        k = min(top_k, n)
        top_indices = np.argpartition(sims, -k)[-k:]
        top_indices = top_indices[np.argsort(sims[top_indices])[::-1]]

        results = []
        for i in top_indices:
            score = float(sims[i])
            meta  = self._meta[i]
            results.append((score, meta["id"], meta.get("title", "")))
        return results

    # ── プロパティ ───────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        return len(self._meta)

    @property
    def is_empty(self) -> bool:
        return self._vecs is None or len(self._meta) == 0

    def get_meta(self, entry_id: str) -> Optional[dict]:
        for m in self._meta:
            if m["id"] == entry_id:
                return m
        return None


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="VecIndex CLI — KNN 検索")
    parser.add_argument("query", nargs="?", default="", help="検索テキスト")
    parser.add_argument("--top", type=int, default=5, help="件数（デフォルト 5）")
    parser.add_argument("--info", action="store_true", help="インデックス情報を表示")
    args = parser.parse_args()

    idx = VecIndex()
    if idx.is_empty:
        print("⚠️  インデックスが空です。先に build_vec_index.py を実行してください。")
        return

    print(f"📦 VecIndex: {idx.size} entries loaded")
    if args.info:
        return

    if not args.query:
        print("（クエリを指定してください）")
        return

    print(f"🔍 query: {args.query!r}  top={args.top}\n")
    results = idx.search(query_text=args.query, top_k=args.top)

    for rank, (score, eid, title) in enumerate(results, 1):
        print(f"  #{rank}  [{score:.4f}]  {eid}  {title[:60]}")

    if not results:
        print("（結果なし）")


if __name__ == "__main__":
    main()
