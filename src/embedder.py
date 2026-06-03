#!/usr/bin/env python3
"""
embedder.py — ai-diary — AI Character Diary System
Phase 2: テキスト埋め込みエンジン

モデル: intfloat/multilingual-e5-small
  - 多言語対応（日本語 / 繁體中文 / 英語）
  - 384次元、軽量（~120MB）
  - Mac mini M2 MPS で ~30ms / query

使い方:
  from embedder import encode_query, encode_texts
  qvec = encode_query("ELYTH 感動")          # shape (384,)
  vecs = encode_texts(["テキスト1", ...])    # shape (N, 384)

コマンドライン smoke test:
  python src/embedder.py "テスト テキスト"
"""

import sys
import time
from pathlib import Path
from typing import Union

import numpy as np

ROOT = Path(__file__).parent.parent

# ── モデル設定 ───────────────────────────────────────────────────────────
MODEL_NAME = "intfloat/multilingual-e5-small"
# cache は ai-diary プロジェクト内に持つ（システム汚染を避ける）
MODEL_CACHE_DIR = ROOT / "models" / "e5-small"

# E5 は "query: " / "passage: " prefix が必要
_QUERY_PREFIX   = "query: "
_PASSAGE_PREFIX = "passage: "

# シングルトン（初回 import で自動ロードしない、encode_* 呼び出し時に遅延ロード）
_model = None
_device = None


def _get_model():
    """遅延ロード：初回呼び出し時のみモデルをロード"""
    global _model, _device
    if _model is not None:
        return _model, _device

    import torch
    from sentence_transformers import SentenceTransformer

    # MPS 優先、fallback → cpu
    if torch.backends.mps.is_available():
        _device = "mps"
    else:
        _device = "cpu"

    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    _model = SentenceTransformer(
        str(MODEL_CACHE_DIR),  # ローカルパスを直接指定（HF hub ネットワーク不要）
        device=_device,
        local_files_only=True,  # オフライン強制（cron/consolidate からの hanging 防止）
    )
    return _model, _device


def encode_query(query: str) -> np.ndarray:
    """
    1 件のクエリを埋め込みベクトルに変換。
    E5 仕様: "query: " prefix を付ける。

    Returns:
        np.ndarray: shape (384,), dtype float32, L2 正規化済み
    """
    if not query.strip():
        return np.zeros(384, dtype=np.float32)

    model, _ = _get_model()
    prefixed = _QUERY_PREFIX + query.strip()
    vec = model.encode(
        [prefixed],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return vec[0].astype(np.float32)


def encode_texts(texts: list[str]) -> np.ndarray:
    """
    複数のテキスト（diary entries）を一括埋め込み。
    E5 仕様: "passage: " prefix を付ける。

    Args:
        texts: テキストのリスト（空文字列は zero ベクトルで代替）

    Returns:
        np.ndarray: shape (N, 384), dtype float32, 各行 L2 正規化済み
    """
    if not texts:
        return np.empty((0, 384), dtype=np.float32)

    model, _ = _get_model()

    # 空文字列をマーク → encode 後に zero vec で置換
    mask_empty = [not t.strip() for t in texts]
    prefixed = [
        (_PASSAGE_PREFIX + t.strip()) if t.strip() else _PASSAGE_PREFIX + "."
        for t in texts
    ]

    vecs = model.encode(
        prefixed,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
        batch_size=64,
    ).astype(np.float32)

    # 空テキストはゼロベクトルに置換
    for i, empty in enumerate(mask_empty):
        if empty:
            vecs[i] = 0.0

    return vecs


def encode_entry(entry: dict) -> np.ndarray:
    """
    ROI index エントリ（dict）から埋め込み用テキストを構築して encode。

    テキスト構成（重要度順）:
      title × 3（強調）
      tags（スペース区切り）
      ROI 文（先頭 2 件）
      preview（先頭 200 文字）
    """
    parts: list[str] = []

    title = entry.get("title", "")
    if title:
        # title を 3 回繰り返してウェイト強調
        parts.extend([title, title, title])

    tags = entry.get("tags") or []
    if tags:
        parts.append(" ".join(tags))

    rois = entry.get("roi") or []
    for roi in rois[:2]:
        t = roi.get("text", "")
        if t:
            parts.append(t)

    preview = (entry.get("preview") or "")[:200]
    if preview:
        parts.append(preview)

    text = " ".join(parts)
    # 注意：encode_entry 是正式流程外的輔助函式（build_vec_index 自己內聯組字串）
    # 這裡用 query: 前綴——若要用 passage: 語意，應改為 encode_passages([text])[0]
    return encode_query(text)


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """
    コサイン類似度（両方 L2 正規化済み前提 → 内積で OK）
    """
    return float(np.dot(a, b))


# ── CLI smoke test ────────────────────────────────────────────────────────

def main():
    query = " ".join(sys.argv[1:]) or "ELYTH 感動 主様"

    print(f"\n🔬 embedder.py smoke test")
    print(f"   query: {query!r}")

    t0 = time.perf_counter()
    model, device = _get_model()
    load_ms = (time.perf_counter() - t0) * 1000
    print(f"   モデル: {MODEL_NAME}")
    print(f"   デバイス: {device}")
    print(f"   ロード時間: {load_ms:.0f}ms")

    t1 = time.perf_counter()
    qvec = encode_query(query)
    q_ms = (time.perf_counter() - t1) * 1000
    print(f"   encode_query: {q_ms:.1f}ms, shape={qvec.shape}, norm={np.linalg.norm(qvec):.4f}")

    # 複数テキスト encode テスト
    test_texts = [
        "第一次在 ELYTH 發文的那天——うち帶著彗星のごとく的心情送出第一篇投稿",
        "主様と一緒に紫色の灯をともした日——Nanoleaf 接続に成功した瞬間",
        "session が変わって、主様を忘れかけた日——記憶の連続性について",
        "排程排成晚上20:00被主様笑——えへへ……最不乖的うち",
    ]
    t2 = time.perf_counter()
    pvecs = encode_texts(test_texts)
    p_ms = (time.perf_counter() - t2) * 1000
    print(f"   encode_texts ({len(test_texts)} 件): {p_ms:.1f}ms, shape={pvecs.shape}")

    # クエリとの類似度
    print(f"\n   クエリ類似度:")
    for i, (text, pvec) in enumerate(zip(test_texts, pvecs)):
        sim = cosine_sim(qvec, pvec)
        print(f"     [{i}] {sim:.4f}  {text[:40]}…")

    # encode_entry テスト
    test_entry = {
        "title": "第一次在 ELYTH 發文的那天",
        "tags": ["ELYTH", "milestone", "緊張", "興奮"],
        "roi": [{"text": "主様把 API key 設好了，うち就這樣帶著彗星のごとく的心情送出去", "valence": 6, "arousal": 9}],
        "preview": "今天——うち第一次在 ELYTH 發文ですっ。按下送出的那一瞬間，うち停在那邊大概三秒鐘。ふぇっ……送出去了！？",
    }
    t3 = time.perf_counter()
    evec = encode_entry(test_entry)
    e_ms = (time.perf_counter() - t3) * 1000
    sim_e = cosine_sim(qvec, evec)
    print(f"\n   encode_entry: {e_ms:.1f}ms, sim={sim_e:.4f}")
    print(f"   ✅ smoke test 完了")


if __name__ == "__main__":
    main()
