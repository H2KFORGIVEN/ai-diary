#!/usr/bin/env python3
"""
build_vec_index.py — ai-diary — AI Character Diary System
Phase 2: ROI インデックスから numpy ベクトルインデックスを構築・増分更新

ファイル構成:
  diary/index/embeddings.npy       (N, 384) float32  — ベクトル本体
  diary/index/embedding_meta.json  [{id, path, date, title}, ...]  — メタ情報
  diary/index/embedding_hash.json  {entry_id: sha256_hex}  — 増分更新用

フロー（--incremental モード、デフォルト）:
  1. roi_index.json から全エントリ読み込み
  2. embedding_hash.json と比較 → 変更/新規エントリのみ encode
  3. embeddings.npy を全量書き直し（9MB 未満、<50ms）

フロー（--rebuild モード）:
  1. 全エントリを強制 re-encode

用法:
  python src/build_vec_index.py                 # 増分更新
  python src/build_vec_index.py --rebuild       # フル再構築
  python src/build_vec_index.py --dry-run       # 更新対象の確認のみ
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent
DIARY_ROOT = ROOT / "diary"
sys.path.insert(0, str(ROOT / "src"))

from roi import load_index
from embedder import encode_entry, encode_query, encode_texts

INDEX_DIR       = DIARY_ROOT / "index"
EMBEDDINGS_PATH = INDEX_DIR / "embeddings.npy"
META_PATH       = INDEX_DIR / "embedding_meta.json"
HASH_PATH       = INDEX_DIR / "embedding_hash.json"

DIM = 384


# ── コンテンツハッシュ ────────────────────────────────────────────────────

def _entry_hash(entry: dict) -> str:
    """
    encode するテキストが変わったかを検出するためのハッシュ。
    title + tags + roi texts + preview の組み合わせ。
    """
    parts = [
        entry.get("title", ""),
        " ".join(entry.get("tags") or []),
    ]
    for roi in (entry.get("roi") or [])[:2]:
        parts.append(roi.get("text", ""))
    parts.append((entry.get("preview") or "")[:200])
    text = "\n".join(parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ── ロード ────────────────────────────────────────────────────────────────

def _load_existing() -> tuple[np.ndarray, list[dict], dict[str, str]]:
    """既存インデックスをロード（なければ空を返す）"""
    vecs: np.ndarray = np.empty((0, DIM), dtype=np.float32)
    meta: list[dict] = []
    hashes: dict[str, str] = {}

    if EMBEDDINGS_PATH.exists() and META_PATH.exists():
        try:
            vecs = np.load(str(EMBEDDINGS_PATH))
            meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"⚠️  既存インデックスのロード失敗（フル再構築に切り替え）: {e}")
            vecs = np.empty((0, DIM), dtype=np.float32)
            meta = []

    if HASH_PATH.exists():
        try:
            hashes = json.loads(HASH_PATH.read_text(encoding="utf-8"))
        except Exception:
            hashes = {}

    return vecs, meta, hashes


# ── メイン構築 ─────────────────────────────────────────────────────────────

def build(rebuild: bool = False, dry_run: bool = False, verbose: bool = True) -> int:
    """
    ベクトルインデックスを構築・更新する。

    Returns:
        int: 更新（新規 encode）したエントリ数
    """
    t0 = time.perf_counter()

    all_entries = load_index()
    # scenario entries（scn- prefix）は vec index から除外する
    # recall.py Strategy E は diary entry の rrf_scores に boost するため
    entries = [e for e in all_entries if not str(e.get("id", "")).startswith("scn-")]
    if not entries:
        if verbose:
            print("📭 ROI インデックスにエントリがありません")
        return 0
    if verbose and len(all_entries) != len(entries):
        print(f"   ℹ️  scenario entries 除外: {len(all_entries) - len(entries)} 件")

    if verbose:
        print(f"\n🔨 build_vec_index — {len(entries)} entries")

    # 既存インデックスをロード
    old_vecs, old_meta, old_hashes = _load_existing()

    # 既存のベクトルマップ（id → row index）
    id_to_row: dict[str, int] = {m["id"]: i for i, m in enumerate(old_meta)}

    # 各エントリのハッシュ計算
    entry_map: dict[str, dict] = {e["id"]: e for e in entries}
    new_hashes: dict[str, str] = {}
    to_encode: list[str] = []   # encode が必要な entry id

    for entry in entries:
        eid  = entry["id"]
        h    = _entry_hash(entry)
        new_hashes[eid] = h

        if rebuild or eid not in id_to_row or old_hashes.get(eid) != h:
            to_encode.append(eid)

    if verbose:
        print(f"   変更/新規: {len(to_encode)} / {len(entries)}")
        if dry_run:
            for eid in to_encode:
                e = entry_map[eid]
                print(f"     → {eid}  {e.get('title', '')[:50]}")
            print("   (dry-run: 書き込みなし)")
            return len(to_encode)

    if not to_encode and len(old_meta) == len(entries):
        if verbose:
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"   ✅ 変更なし（{elapsed:.0f}ms）")
        return 0

    # ── encode ─────────────────────────────────────────────────────────
    new_vecs: np.ndarray = np.empty((0, DIM), dtype=np.float32)
    if to_encode:
        if verbose:
            print(f"   encode 開始...")
        t1 = time.perf_counter()

        # テキストを一括構築
        texts: list[str] = []
        for eid in to_encode:
            e = entry_map[eid]
            parts = [
                e.get("title", ""),
                e.get("title", ""),  # title ×3 重み付け
                e.get("title", ""),
                " ".join(e.get("tags") or []),
            ]
            for roi in (e.get("roi") or [])[:2]:
                parts.append(roi.get("text", ""))
            parts.append((e.get("preview") or "")[:200])
            texts.append(" ".join(filter(None, parts)))

        new_vecs = encode_texts(texts)  # (len(to_encode), 384)

        encode_ms = (time.perf_counter() - t1) * 1000
        if verbose:
            print(f"   encode 完了: {encode_ms:.0f}ms ({len(to_encode)} 件, {encode_ms/len(to_encode):.1f}ms/件)")

    # ── 全量再構築 ─────────────────────────────────────────────────────
    # 古い vec を引き継ぎつつ、更新分で上書き、削除済みを除外
    new_encode_map: dict[str, np.ndarray] = {}
    if to_encode:
        for i, eid in enumerate(to_encode):
            new_encode_map[eid] = new_vecs[i]

    final_vecs_list: list[np.ndarray] = []
    final_meta: list[dict] = []

    for entry in entries:
        eid = entry["id"]
        if eid in new_encode_map:
            vec = new_encode_map[eid]
        elif eid in id_to_row:
            vec = old_vecs[id_to_row[eid]]
        else:
            # 新規だが to_encode に入っていない（論理的にはあり得ないが安全策）
            vec = np.zeros(DIM, dtype=np.float32)

        final_vecs_list.append(vec)
        final_meta.append({
            "id":           eid,
            "path":         entry.get("path", ""),
            "date":         entry.get("date", ""),
            "title":        entry.get("title", ""),
            "layer":        "L1",
            "tags":         entry.get("tags") or [],
            "intensity":    entry.get("intensity"),
            "valence":      entry.get("valence"),
            "flashbulb":    entry.get("flashbulb", False),
            "decay_weight": entry.get("decay_weight"),
        })

    final_vecs = np.vstack(final_vecs_list).astype(np.float32)

    # ── 書き込み ────────────────────────────────────────────────────────
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(str(EMBEDDINGS_PATH), final_vecs)
    META_PATH.write_text(json.dumps(final_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    HASH_PATH.write_text(json.dumps(new_hashes, ensure_ascii=False, indent=2), encoding="utf-8")

    total_ms = (time.perf_counter() - t0) * 1000
    size_kb  = EMBEDDINGS_PATH.stat().st_size / 1024
    if verbose:
        print(f"   💾 保存: {EMBEDDINGS_PATH.name} ({size_kb:.0f}KB, {final_vecs.shape})")
        print(f"   ✅ 完了: {total_ms:.0f}ms")

    return len(to_encode)


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ベクトルインデックス構築")
    parser.add_argument("--rebuild",   action="store_true", help="全エントリ強制 re-encode")
    parser.add_argument("--dry-run",   action="store_true", help="更新対象の確認のみ")
    parser.add_argument("--quiet",     action="store_true", help="最小ログ")
    args = parser.parse_args()

    n = build(
        rebuild=args.rebuild,
        dry_run=args.dry_run,
        verbose=not args.quiet,
    )
    if args.quiet:
        print(n)


if __name__ == "__main__":
    main()
