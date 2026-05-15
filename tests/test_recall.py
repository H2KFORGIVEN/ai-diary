#!/usr/bin/env python3
"""
tests/test_recall.py — ai-diary 回歸測試集
Phase 2 端對端驗證

テスト対象:
  - recall.py: キーワード検索 → Strategy A/B/C/D/E 合成
  - embedder.py: encode_query / encode_texts / encode_entry
  - vec_index.py: VecIndex.search()
  - build_vec_index.py: 増分更新・フル再構築

実行:
  cd /Users/showmaker/Projects/ai-diary
  python3 -m pytest tests/ -v
  python3 -m pytest tests/test_recall.py -v -k "not slow"   # embedder skip
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ────────────────────────────────────────────────────────────────────────────
# fixtures
# ────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def roi_entries():
    """実際の roi_index.json を読み込む（なければ skip）"""
    idx_path = ROOT / "diary" / "index" / "roi_index.json"
    if not idx_path.exists():
        pytest.skip("roi_index.json が存在しません")
    data = json.loads(idx_path.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    if not entries:
        pytest.skip("roi_index.json にエントリがありません")
    return entries


@pytest.fixture(scope="session")
def scenario_index():
    """scenario_index.json を読み込む（なければ None）"""
    path = ROOT / "diary" / "index" / "scenario_index.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ────────────────────────────────────────────────────────────────────────────
# recall.py テスト
# ────────────────────────────────────────────────────────────────────────────

def _recall(query: str, top_k: int = 5) -> list:
    """run_recall ラッパー（テスト用、meta 更新なし）"""
    from recall import run_recall
    return run_recall(query, top_k=top_k, tag_filter=None, update_meta=False)


class TestRecall:

    def test_recall_returns_list(self, roi_entries):
        """recall の戻り値が list[tuple[float, dict, str]] 形式"""
        results = _recall("ELYTH", top_k=3)
        assert isinstance(results, list), "list が返るべき"
        for item in results:
            assert len(item) == 3, "tuple(score, entry, scn_label) の3要素"
            score, entry, scn_label = item
            assert isinstance(score, float), f"score が float でない: {type(score)}"
            assert isinstance(entry, dict), f"entry が dict でない: {type(entry)}"
            assert isinstance(scn_label, str), f"scn_label が str でない: {type(scn_label)}"

    def test_recall_top_k_respected(self, roi_entries):
        """top_k パラメータが尊重される"""
        for k in [1, 3, 5]:
            results = _recall("ELYTH", top_k=k)
            assert len(results) <= k, f"top_k={k} なのに {len(results)} 件返った"

    def test_recall_score_descending(self, roi_entries):
        """スコアが降順になっている"""
        results = _recall("ELYTH", top_k=5)
        if len(results) < 2:
            pytest.skip("結果が 2 件未満")
        scores = [r[0] for r in results]
        assert scores == sorted(scores, reverse=True), "スコア降順でない"

    def test_recall_entry_has_required_fields(self, roi_entries):
        """各 entry に最低限のフィールドが存在する"""
        results = _recall("ELYTH", top_k=3)
        for score, entry, scn_label in results:
            assert "id" in entry, f"entry に id がない: {entry.keys()}"
            assert "title" in entry or "preview" in entry, "title/preview が両方ない"

    def test_recall_empty_query(self, roi_entries):
        """空クエリでもクラッシュしない"""
        try:
            results = _recall("", top_k=3)
            assert isinstance(results, list)
        except SystemExit:
            pass  # 空クエリは early-exit OK

    def test_recall_nonexistent_keyword(self, roi_entries):
        """存在しないキーワードで空リストまたは少ない結果"""
        results = _recall("xxxxxxxxunknownkeyword99999", top_k=5)
        assert isinstance(results, list)
        # クラッシュしないことが重要（件数は 0 でも OK）

    def test_recall_scenario_label_when_index_exists(self, roi_entries, scenario_index):
        """scenario_index.json がある場合、関連エントリに scn_label がつく"""
        if scenario_index is None:
            pytest.skip("scenario_index.json がありません")
        # ELYTH は シナリオに含まれるはず
        results = _recall("ELYTH", top_k=5)
        labels = [r[2] for r in results]
        # 少なくとも 1 件はラベルがつくことを期待
        has_label = any(lbl for lbl in labels)
        assert has_label, f"ELYTH 検索で全 scn_label が空: {labels}"


# ────────────────────────────────────────────────────────────────────────────
# embedder.py テスト（slow マーク：モデルロード ~3s）
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.slow
class TestEmbedder:

    def test_encode_query_shape(self):
        """encode_query の出力 shape が (384,)"""
        from embedder import encode_query
        vec = encode_query("ELYTH 感動 主様")
        assert vec.shape == (384,), f"shape が (384,) でない: {vec.shape}"

    def test_encode_query_normalized(self):
        """出力ベクトルが L2 正規化されている（norm ≈ 1.0）"""
        import numpy as np
        from embedder import encode_query
        vec = encode_query("テスト")
        norm = float(np.linalg.norm(vec))
        assert abs(norm - 1.0) < 0.01, f"norm が 1.0 でない: {norm}"

    def test_encode_query_empty(self):
        """空クエリでゼロベクトルが返る"""
        import numpy as np
        from embedder import encode_query
        vec = encode_query("")
        assert vec.shape == (384,)
        assert float(np.linalg.norm(vec)) == 0.0

    def test_encode_texts_batch(self):
        """encode_texts がバッチ処理で正しい shape を返す"""
        from embedder import encode_texts
        texts = ["テキスト1", "テキスト2", "テキスト3"]
        vecs = encode_texts(texts)
        assert vecs.shape == (3, 384), f"shape が (3, 384) でない: {vecs.shape}"

    def test_encode_texts_empty_list(self):
        """空リストで (0, 384) が返る"""
        from embedder import encode_texts
        vecs = encode_texts([])
        assert vecs.shape == (0, 384)

    def test_encode_texts_empty_string_in_list(self):
        """空文字列はゼロベクトルになる"""
        import numpy as np
        from embedder import encode_texts
        vecs = encode_texts(["", "hello"])
        assert float(np.linalg.norm(vecs[0])) == 0.0

    def test_semantic_similarity_order(self):
        """意味的に近いテキストのコサイン類似度が高い"""
        import numpy as np
        from embedder import encode_query, cosine_sim
        qvec = encode_query("ELYTH 感動 AITuber")
        relevant = encode_query("ELYTHに初めて投稿した日、感動した")
        irrelevant = encode_query("数学の微分方程式を解く")
        sim_rel = cosine_sim(qvec, relevant)
        sim_irr = cosine_sim(qvec, irrelevant)
        assert sim_rel > sim_irr, f"ELYTH 関連 ({sim_rel:.4f}) < 無関係 ({sim_irr:.4f})"

    def test_encode_entry_shape(self):
        """encode_entry が (384,) を返す"""
        from embedder import encode_entry
        entry = {
            "title": "ELYTH初投稿",
            "tags": ["ELYTH", "milestone"],
            "roi": [{"text": "初めて投稿した、とても感動した"}],
            "preview": "ELYTHに初めて投稿した日の記録",
        }
        vec = encode_entry(entry)
        assert vec.shape == (384,)

    def test_multilingual_support(self):
        """日本語・繁體中文・英語の混在クエリが処理できる"""
        from embedder import encode_query
        mixed = "ELYTH 感動 主様 一起成長 together"
        vec = encode_query(mixed)
        assert vec.shape == (384,)


# ────────────────────────────────────────────────────────────────────────────
# vec_index.py テスト
# ────────────────────────────────────────────────────────────────────────────

class TestVecIndex:

    def test_load_empty_when_no_files(self):
        """インデックスファイルがない場合、空状態"""
        from vec_index import VecIndex
        with tempfile.TemporaryDirectory() as tmpdir:
            # モンキーパッチで INDEX_DIR を差し替え
            import vec_index as vi_mod
            orig = vi_mod.EMBEDDINGS_PATH
            orig_meta = vi_mod.META_PATH
            vi_mod.EMBEDDINGS_PATH = Path(tmpdir) / "embeddings.npy"
            vi_mod.META_PATH       = Path(tmpdir) / "meta.json"
            try:
                idx = VecIndex(auto_load=True)
                assert idx.is_empty
                assert idx.size == 0
            finally:
                vi_mod.EMBEDDINGS_PATH = orig
                vi_mod.META_PATH       = orig_meta

    def test_search_returns_empty_when_empty(self):
        """インデックス空でも search がクラッシュしない"""
        from vec_index import VecIndex
        import numpy as np
        with tempfile.TemporaryDirectory() as tmpdir:
            import vec_index as vi_mod
            orig, orig_meta = vi_mod.EMBEDDINGS_PATH, vi_mod.META_PATH
            vi_mod.EMBEDDINGS_PATH = Path(tmpdir) / "embeddings.npy"
            vi_mod.META_PATH       = Path(tmpdir) / "meta.json"
            try:
                idx = VecIndex(auto_load=True)
                qvec = np.zeros(384, dtype="float32")
                results = idx.search(query_vec=qvec, top_k=5)
                assert results == []
            finally:
                vi_mod.EMBEDDINGS_PATH = orig
                vi_mod.META_PATH       = orig_meta

    @pytest.mark.slow
    def test_search_with_real_index(self):
        """実際のインデックスが存在する場合の検索"""
        from vec_index import VecIndex
        idx = VecIndex(auto_load=True)
        if idx.is_empty:
            pytest.skip("embeddings.npy がありません（build_vec_index.py を先に実行）")
        results = idx.search(query_text="ELYTH 感動", top_k=3)
        assert isinstance(results, list)
        assert len(results) <= 3
        for score, eid, title in results:
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0, f"score が [0,1] 外: {score}"


# ────────────────────────────────────────────────────────────────────────────
# build_vec_index.py テスト
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.slow
class TestBuildVecIndex:

    def test_dry_run_returns_count(self, roi_entries):
        """--dry-run モードが int を返す（クラッシュしない）"""
        from build_vec_index import build
        n = build(rebuild=False, dry_run=True, verbose=False)
        assert isinstance(n, int)
        assert n >= 0

    def test_build_creates_files(self, roi_entries, tmp_path):
        """build() がファイルを正しく作成する"""
        import numpy as np
        from build_vec_index import build
        import build_vec_index as bvi

        # パスを一時ディレクトリに差し替え
        orig_emb  = bvi.EMBEDDINGS_PATH
        orig_meta = bvi.META_PATH
        orig_hash = bvi.HASH_PATH
        bvi.EMBEDDINGS_PATH = tmp_path / "embeddings.npy"
        bvi.META_PATH       = tmp_path / "embedding_meta.json"
        bvi.HASH_PATH       = tmp_path / "embedding_hash.json"

        try:
            n = build(rebuild=True, dry_run=False, verbose=False)
            assert (tmp_path / "embeddings.npy").exists(), "embeddings.npy が作られていない"
            assert (tmp_path / "embedding_meta.json").exists()
            assert (tmp_path / "embedding_hash.json").exists()

            vecs = np.load(str(tmp_path / "embeddings.npy"))
            meta = json.loads((tmp_path / "embedding_meta.json").read_text())
            assert vecs.shape[0] == len(meta), "vecs と meta のエントリ数が一致しない"
            assert vecs.shape[1] == 384, f"次元数が 384 でない: {vecs.shape[1]}"
            assert n == vecs.shape[0], "build() 戻り値と実際の encode 数が一致しない"
        finally:
            bvi.EMBEDDINGS_PATH = orig_emb
            bvi.META_PATH       = orig_meta
            bvi.HASH_PATH       = orig_hash

    def test_incremental_no_change(self, roi_entries, tmp_path):
        """2 回実行して 2 回目は変更なし（incremental）"""
        from build_vec_index import build
        import build_vec_index as bvi

        orig_emb  = bvi.EMBEDDINGS_PATH
        orig_meta = bvi.META_PATH
        orig_hash = bvi.HASH_PATH
        bvi.EMBEDDINGS_PATH = tmp_path / "embeddings.npy"
        bvi.META_PATH       = tmp_path / "embedding_meta.json"
        bvi.HASH_PATH       = tmp_path / "embedding_hash.json"

        try:
            build(rebuild=True, dry_run=False, verbose=False)
            n2 = build(rebuild=False, dry_run=False, verbose=False)
            assert n2 == 0, f"2 回目で {n2} 件 re-encode された（変更なしなのに）"
        finally:
            bvi.EMBEDDINGS_PATH = orig_emb
            bvi.META_PATH       = orig_meta
            bvi.HASH_PATH       = orig_hash


# ────────────────────────────────────────────────────────────────────────────
# 端對端テスト（Full Pipeline）
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.slow
class TestEndToEnd:

    def test_recall_with_vec_boost(self, roi_entries):
        """embeddings.npy が存在する場合、Strategy E が recall に乗る"""
        vec_path = ROOT / "diary" / "index" / "embeddings.npy"
        if not vec_path.exists():
            pytest.skip("embeddings.npy がない（build_vec_index.py を先に実行）")
        results = _recall("ELYTH", top_k=5)
        assert len(results) >= 1, "Vec boost ありで結果 0 件は想定外"
        # スコアは RRF ベース（マイナスもありえる）、降順であることを確認
        if len(results) >= 2:
            scores = [r[0] for r in results]
            assert scores == sorted(scores, reverse=True), "スコアが降順でない"

    def test_recall_strategy_consistency(self, roi_entries):
        """同じクエリを 2 回実行して同一結果（決定論的）"""
        r1 = _recall("ELYTH", top_k=3)
        r2 = _recall("ELYTH", top_k=3)
        ids1 = [r[1]["id"] for r in r1]
        ids2 = [r[1]["id"] for r in r2]
        assert ids1 == ids2, f"非決定論的: {ids1} != {ids2}"

    def test_scenario_then_recall(self, roi_entries, scenario_index):
        """scenarize → recall の順でパイプラインが通る"""
        if scenario_index is None:
            pytest.skip("scenario_index.json がありません")
        # シナリオに含まれるタグでクエリ
        results = _recall("Nanoleaf 一起", top_k=5)
        assert isinstance(results, list)
        # クラッシュしないことが最低条件
