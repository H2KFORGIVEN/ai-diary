# AI Diary — L2 Scenario 聚合層 + 向量召回 升級規劃 v2

> **規劃日期**：2026-05-15（v2 修訂：numpy 取代 sqlite-vec；IDF-weighted Jaccard 取代 generic_tags.yaml）
> **對象**：`/Users/showmaker/Projects/ai-diary/`（Python 3.11, Mac mini 16GB）
> **設計原則**：純本地、漸進加法、不打掉重練、不退化現有毫秒級召回
> **現況基準**：`recall.py` Phase C（RRF 三策略 + Tag Graph + MMR），`roi_index.json` v3 帶 `decay_weight`

---

## 0. 全域架構：L0 → L1 → L2 三層記憶

```
L0  Buffer  (buffer.jsonl)
      │
      ▼ consolidate.py
L1  Diary Entry (.md + roi_index.json)
      │
      ▼ scenarize.py  ← 【新增】
L2  Scenario (.md + scenario_index.json)
      │
      ▼ self-narrative.md（人工，L3 相當物）

向量層（橫切）：
    embedder.py → embeddings.npy + embedding_meta.json（numpy，純本地）
    粒度：L1 entry + L1 ROI 句（Phase 1）；L2 scenario（Phase 2）
```

**召回查詢拓樸（升級後）**：

```
query → [Strategy A 五維度]  ┐
       → [Strategy B keyword] ├─→ RRF → Tag Graph 擴散 → MMR → top-K
       → [Strategy C valence] │
       → [Strategy D scenario]│
       → [Strategy E vector]  ┘
                               ↓
                          scenario 命中 → 帶出 header + drill-down L1
```

---

# Part 1：L2 Scenario 聚合層

## 1.1 場景（Scenario）的定義

> **一個 Scenario = 一段「主題上連貫」的記憶塊**，由 2~12 篇 L1 diary entries 組成。

### 1.1.1 成立判準（C1~C4，Phase 1；C5，Phase 2）

| 條件 | 閾值 | 說明 |
|---|---|---|
| **C1. 主題連貫** | `IDF-Jaccard ≥ 0.20`（初期；語料 50 篇以上で 0.30 推奨） | 用 IDF 加權 Jaccard（見 1.2 節），高頻 tag 自然降權 |
| **C2. 時間鄰近** | `time_span ≤ 14 天` 或 flashbulb 中心 ±30 天 | flashbulb 周邊允許更長尾跡 |
| **C3. 規模** | `2 ≤ n ≤ 12` | 單篇不成 scenario；>12 切分 |
| **C4. 情緒承載** | `max(intensity) ≥ 6` 或 `mean(intensity) ≥ 5` | 排除「全是日常 5 分」的聚合 |
| **C5. 向量內聚**（Phase 2）| `intra-cluster cos-sim ≥ 0.55` | 向量校驗補充 C1 |

### 1.1.2 場景種子（seed）

- `flashbulb == true` 的 entry **必為** seed
- `intensity >= 8` 的 entry 候選為 seed
- 同主題下 `decay_weight` 最高者候選

---

## 1.2 C1 的正確實作：IDF-weighted Jaccard

### 為什麼不用靜態 generic_tags.yaml

`generic_tags.yaml`（靜態排除清單）有兩個根本矛盾：

**矛盾 1 — emotion_filter.py 自動打標**：
`感動`、`特訓`、`自責` 這些 tag 頻率高，是因為它們是情緒過濾器的輸出——代表特定情緒反應，並非語意上泛用。若靜態排除，等於讓情緒系統的設計失效。

**矛盾 2 — tag_graph.json 全量用於召回擴散**：
recall.py 的 Tag Graph diffusion 包含 `milestone`、`主様` 做相關篇 boost。scenarize 若靜態說「這些不算數」，兩邊語義不一致。

### 正確做法：動態 IDF 加權

```python
import math

def compute_idf_weights(entries: list[dict]) -> dict[str, float]:
    """從 roi_index 動態計算，每次 scenarize 重算一次（毫秒級）。
    IDF = log(N / df)，頻率高的 tag 權重低，但不排除。"""
    N = len(entries)
    if N == 0:
        return {}
    df: dict[str, int] = {}
    for e in entries:
        for tag in set(e.get("tags") or []):
            df[tag] = df.get(tag, 0) + 1
    return {tag: math.log(N / cnt) for tag, cnt in df.items()}

def idf_jaccard(a: dict, b: dict, idf: dict[str, float]) -> float:
    """IDF 加權 Jaccard 相似度。
    頻率高的 tag（milestone、主様対話）自然貢獻低，
    但不被排除——tag_graph 仍可用它們做召回擴散。"""
    ta = set(a.get("tags") or [])
    tb = set(b.get("tags") or [])
    intersection = ta & tb
    union_tags   = ta | tb
    if not union_tags:
        return 0.0
    w_i = sum(idf.get(t, 1.0) for t in intersection)
    w_u = sum(idf.get(t, 1.0) for t in union_tags)
    return w_i / w_u if w_u else 0.0
```

### 實際效果（以現有 16 篇語料）

```
IDF 值參考（16 篇）：
  milestone   (68.8%) → IDF = log(16/11) ≈ 0.37   ← 低
  主様対話    (43.8%) → IDF = log(16/7)  ≈ 0.83   ← 低
  主様        (37.5%) → IDF = log(16/6)  ≈ 0.98   ← 低
  感動        (18.8%) → IDF = log(16/3)  ≈ 1.67   ← 中（情緒 tag 仍有效）
  特訓        (18.8%) → IDF ≈ 1.67                ← 中
  ELYTH       (12.5%) → IDF = log(16/2)  ≈ 2.08   ← 高
  イナンナ     (6.2%) → IDF = log(16/1)  ≈ 2.77   ← 極高

例：A=[milestone, ELYTH, 緊張]  B=[milestone, ELYTH, 感動]
  靜態 Jaccard = 2/4 = 0.50（threshold 0.34 → 通過，但靠 milestone 湊的）
  IDF Jaccard  ≈ (0.37+2.08) / (0.37+2.08+1.67+1.67) ≈ 0.41 → 通過（真的有 ELYTH 連結）

例：C=[milestone, 主様対話, 開心]  D=[milestone, 主様, 感動]
  靜態 Jaccard = 1/5 = 0.20（不通過）
  IDF Jaccard  ≈ 0.37 / 5.85 ≈ 0.06 → 不通過（正確：不應聚）
```

### 與現有系統的一致性

| 模組 | 用途 | IDF Jaccard 影響 |
|---|---|---|
| emotion_filter.py 打的 tag | 寫入日記 frontmatter | ✅ 完全不動，IDF 自然適應 |
| tag_graph.json diffusion | recall 時找相關篇 | ✅ 完全不動，所有 tag 仍在 |
| recall.py MMR `_similarity` | 防結果過相似 | ✅ 不衝突（目的不同：MMR 避免重複，Jaccard 判斷是否同主題）|
| Tag Graph boost | milestone 仍可做 boost | ✅ scenarize 不排除 tag，只降低 Jaccard 貢獻 |

---

## 1.3 Scenario 資料結構

### 1.3.1 儲存路徑

```
diary/
├── scenarios/
│   ├── 2026/
│   │   ├── elyth-debut.md      ← 場景 markdown（人工 confirm 済み）
│   │   └── ...
│   └── _drafts/                ← 自動生成草稿（human_edited=False かつ confirmed なし）
│                                 人工レビュー後 _drafts/ → 2026/ に移動 → human_edited=true
└── index/
    └── scenario_index.json     ← 場景快速索引
```

### 1.3.2 Scenario Markdown 格式

```markdown
---
scenario_id: "elyth-debut-2026"
title: "ELYTH デビューと、イナンナとの出会い"
date_range: ["2026-04-28", "2026-04-29"]
center_date: "2026-04-28"
tags: [ELYTH, milestone, 主様, イナンナ, 初投稿]
emotional_intensity: 9
mean_intensity: 8.5
valence: 6
arousal: 8
flashbulb: true
member_ids:
  - "2026-04-28_1430"
  - "2026-04-29_2015"
seed_ids:
  - "2026-04-28_1430"
generated_by: "scenarize.py"
version: 1
human_edited: false         ← true 時 scenarize 不覆寫 body
created: "2026-05-15T03:14:00"
decay_weight: 0.95
recall_count: 0
last_recalled: null
---

# ELYTH デビューと、イナンナとの出会い

## 起 — 2026-04-28 初投稿 ⚡
> 📍 主様把 API key 設好了，带着「彗星のごとく」的心情送出第一篇投稿

## 承 — 2026-04-29 イナンナとの出会い
> 📍 イナンナさんと話した……「ちゃんといる」と感じた

## 🎯 場景核心
- 第一次踏出：ELYTH 帳號開機儀式
- 第一個朋友：イナンナとの邂逅

## 🔗 相關 entries
- [[2026-04-28_1430]] ⚡ ・ [[2026-04-29_2015]]
```

### 1.3.3 scenario_index.json schema

> 刻意設計為 roi_index entry 的**超集**，
> 讓 `recall_score_from_index()` 完全沿用，零改動。

```json
{
  "_built":   "2026-05-15T03:14:00",
  "_version": 1,
  "_count":   3,
  "scenarios": [
    {
      "scenario_id":    "elyth-debut-2026",
      "path":           "diary/scenarios/2026/elyth-debut.md",
      "title":          "ELYTH デビューと、イナンナとの出会い",
      "date_range":     ["2026-04-28", "2026-04-29"],
      "center_date":    "2026-04-28",
      "tags":           ["ELYTH","milestone","主様","イナンナ"],
      "keywords":       ["ELYTH","初投稿","イナンナ","主様","彗星","いいね"],
      "intensity":      9,
      "mean_intensity": 8.5,
      "valence":        6,
      "arousal":        8,
      "flashbulb":      true,
      "member_ids":     ["2026-04-28_1430","2026-04-29_2015"],
      "seed_ids":       ["2026-04-28_1430"],
      "preview":        "主様把 API key 設好了，带着「彗星のごとく」的心情...",
      "roi": [
        {"text":"第一篇投稿送出去了","valence":6,"arousal":9,"from_id":"2026-04-28_1430"},
        {"text":"イナンナさんと話した","valence":5,"arousal":7,"from_id":"2026-04-29_2015"}
      ],
      "decay_weight":   0.95
    }
  ]
}
```

---

## 1.4 `scenarize.py` — 場景生成器

### 1.4.1 觸發條件

```bash
python src/scenarize.py              # 每日增量（consolidate 後自動觸發）
python src/scenarize.py --rebuild    # 全量重建
python src/scenarize.py --dry-run    # 只顯示，不寫盤
```

### 1.4.2 演算法（Phase 1）

```python
def scenarize_incremental(window_days: int = 30):
    entries  = load_index()        # roi_index.json 全量
    idf      = compute_idf_weights(entries)    # 動態 IDF
    recent   = [e for e in entries
                if days_since(e["date"]) <= window_days]

    # Step 1: 候選 pair 評分（window 內 < 60 篇 → O(N²) OK）
    pairs = []
    for a, b in itertools.combinations(recent, 2):
        # C2 時間鄰近
        diff = abs(date_diff(a, b))
        if diff > 14:
            if not (a["flashbulb"] or b["flashbulb"]):
                continue
            if diff > 30:
                continue
        # C1 IDF-Jaccard
        sim = idf_jaccard(a, b, idf)
        if sim < 0.34:
            continue
        pairs.append((a["id"], b["id"], sim))

    # Step 2: union-find 連通分量
    clusters = union_find(pairs)

    # Step 3: C3 規模 + C4 情緒承載
    scenarios = []
    for cluster in clusters:
        if len(cluster) > 12:
            cluster = split_oversized(cluster)  # greedy 分切
        if len(cluster) < 2:
            continue
        if max_intensity(cluster) < 6 and mean_intensity(cluster) < 5:
            continue
        scenarios.append(build_scenario(cluster, idf))

    # Step 4: monotonic merge（已加入的不踢出）
    final = reconcile_with_existing(scenarios, monotonic=True)

    for s in final:
        write_scenario_md(s)
    rebuild_scenario_index()
```

### 1.4.3 scenario_id 穩定性

```python
# 用 seed entry id 衍生，seed 不變則 id 不變
seed_id   = seed_entry["id"]               # e.g. "2026-04-28_1430"
top_tag   = most_informative_tag(cluster, idf)  # IDF 最高 tag
scenario_id = f"scn-{seed_id}-{slugify(top_tag)}"
```

### 1.4.4 `human_edited` 保護

```python
# scenarize 重建前檢查
existing = load_existing_scenario(scenario_id)
if existing and existing.get("human_edited"):
    # 只更新 frontmatter 元資料，不動 body
    update_frontmatter_only(existing, new_meta)
else:
    write_full_scenario(new_scenario)
```

---

## 1.5 recall.py 整合（Strategy D）

```python
# recall.py run_recall() 中，Strategy A/B/C 之後新增：

if CONFIG.get("scenario", {}).get("recall", {}).get("enabled"):
    scenarios = load_scenario_index()
    # 沿用同一個 recall_score_from_index()（schema superset 設計）
    scores_d = {
        s["scenario_id"]: recall_score_from_index(query_words, s, query_valence)
        for s in scenarios
    }
    ranked_d = sorted(scores_d.items(), key=lambda x: x[1], reverse=True)

    W = CONFIG["scenario"]["recall"]["weight"]   # 0.35
    for rank, (sid, _) in enumerate(ranked_d[:10]):
        s_obj = scenario_by_id[sid]
        boost = W / (RRF_K + rank + 1)
        for mid in s_obj["member_ids"]:
            rrf_scores[mid] = rrf_scores.get(mid, 0.0) + boost
```

---

# Part 2：向量召回（numpy 版）

## 2.1 為什麼用 numpy 而不是 sqlite-vec

主様指出的核心問題：**DB 查詢的開銷是否增加召回時間？**

| | sqlite-vec（原規劃）| numpy .npy（修訂版）|
|---|---|---|
| KNN 查詢 | ~40ms（SQL + 磁碟 I/O）| **<1ms**（純矩陣乘法）|
| 模型 encode | ~30ms | ~30ms（相同）|
| 全量重建 | ~60s | ~60s（相同）|
| 每日增量 | <2s | <2s（相同）|
| 規模上限 | 100k chunks | ~50k chunks（記憶體常駐 9MB）|
| 維護複雜度 | 中（SQL + schema）| **低**（numpy + json）|

**結論**：我們家的日記規模（預估 5 年後 ~2000 chunks = 9MB），numpy 常駐記憶體完全夠用，且查詢更快。真正的主要開銷永遠是 `encode_query()` 的 ~30ms，跟儲存方式無關。

## 2.2 Embedding 模型

**選定：`intfloat/multilingual-e5-small`**（384維，471MB，MPS ~30ms/句）

E5 前綴規範（**必須遵守，否則品質掉 30%**）：
- 索引時：`"passage: " + text`
- 查詢時：`"query: " + query`
- 前綴寫死在 `embedder.py` 內部，外部 API 拿不到不帶前綴的版本

## 2.3 檔案結構

```
diary/index/
├── embeddings.npy          ← shape: (N, 384)，float32，全部 chunk 向量
├── embedding_meta.json     ← [{chunk_id, layer, source_id, date, tags, intensity, ...}]
└── embedding_hash.json     ← {chunk_id: sha1_hash}，用於增量 diff
```

## 2.4 三層粒度策略

| Layer | 文本來源 | 數量/年 | 目的 |
|---|---|---|---|
| **L1 entry** | `title + preview[:400]` | ~500 | 整篇語義匹配 |
| **ROI 句** | 每條 ROI 句子 | ~1500 | 情緒峰值細粒度匹配 |
| **L2 scenario** | `title + preview[:600]` | ~30 | 主題場景語義（Phase 2）|

## 2.5 新增模組

### `src/embedder.py`

```python
from sentence_transformers import SentenceTransformer
import torch, numpy as np

_MODEL = None
MODEL_NAME = "intfloat/multilingual-e5-small"

def _device():
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"

def get_model():
    global _MODEL
    if _MODEL is None:
        _MODEL = SentenceTransformer(MODEL_NAME, device=_device())
        torch.set_num_threads(2)   # 避免吃滿 Mac mini CPU
    return _MODEL

def encode_passages(texts: list[str]) -> np.ndarray:
    """前綴寫死，外部不需要知道"""
    inputs = [f"passage: {t[:400]}" for t in texts]
    return get_model().encode(
        inputs, normalize_embeddings=True,
        convert_to_numpy=True, show_progress_bar=False,
    ).astype(np.float32)

def encode_query(q: str) -> np.ndarray:
    return get_model().encode(
        [f"query: {q[:200]}"],
        normalize_embeddings=True,
        convert_to_numpy=True,
    )[0].astype(np.float32)
```

### `src/vec_index.py`（numpy 版）

```python
import json, hashlib, numpy as np
from pathlib import Path

ROOT      = Path(__file__).parent.parent
IDX_DIR   = ROOT / "diary" / "index"
EMB_PATH  = IDX_DIR / "embeddings.npy"
META_PATH = IDX_DIR / "embedding_meta.json"
HASH_PATH = IDX_DIR / "embedding_hash.json"

def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()

def load_index() -> tuple[np.ndarray, list[dict], dict[str, str]]:
    """載入向量矩陣、meta、hash 表。不存在時回傳空值。"""
    if not EMB_PATH.exists():
        return np.zeros((0, 384), dtype=np.float32), [], {}
    embs  = np.load(str(EMB_PATH))
    meta  = json.loads(META_PATH.read_text(encoding="utf-8"))
    hashes = json.loads(HASH_PATH.read_text(encoding="utf-8"))
    return embs, meta, hashes

def save_index(embs: np.ndarray, meta: list[dict], hashes: dict[str, str]):
    IDX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(str(EMB_PATH), embs)
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    HASH_PATH.write_text(json.dumps(hashes, ensure_ascii=False, indent=2))

def knn(query_vec: np.ndarray, embs: np.ndarray, meta: list[dict],
        top_k: int = 30, layer: str | None = None) -> list[tuple[int, float, dict]]:
    """
    回傳 [(index, cosine_sim, meta_entry), ...]
    L2-normalize 後內積 = cosine similarity。
    """
    if embs.shape[0] == 0:
        return []
    sims = embs @ query_vec        # shape: (N,)，< 1ms
    if layer:
        # 只看特定 layer 的 index
        valid = [i for i, m in enumerate(meta) if m.get("layer") == layer]
        if not valid:
            return []
        mask = np.array(valid)
        sims_filtered = sims[mask]
        top_local = np.argsort(sims_filtered)[::-1][:top_k]
        top_global = mask[top_local]
    else:
        top_global = np.argsort(sims)[::-1][:top_k]
    return [(int(i), float(sims[i]), meta[i]) for i in top_global]
```

### `src/build_vec_index.py`

```python
"""
增量重建：只對 text_hash 變動的 chunk 重算 embedding。
全量：--rebuild（模型升級時用）
"""
import json, hashlib, numpy as np
from pathlib import Path
from embedder import encode_passages
from vec_index import load_index, save_index
from roi import load_index as load_roi_index

def sha1(t): return hashlib.sha1(t.encode()).hexdigest()

def build_incremental(rebuild: bool = False):
    entries = load_roi_index()

    embs_old, meta_old, hashes_old = (
        (np.zeros((0,384), dtype=np.float32), [], {})
        if rebuild else load_index()
    )
    old_pos = {m["chunk_id"]: i for i, m in enumerate(meta_old)}

    new_meta  = []
    new_embs  = []
    new_hashes = {}
    changed = 0

    for e in entries:
        # L1 entry
        for chunk_id, text, layer in _iter_chunks(e):
            h = sha1(text)
            new_hashes[chunk_id] = h
            meta_entry = {
                "chunk_id":  chunk_id,
                "layer":     layer,
                "source_id": e["id"],
                "date":      e.get("date"),
                "tags":      e.get("tags", []),
                "intensity": e.get("intensity"),
                "valence":   e.get("valence"),
                "flashbulb": e.get("flashbulb", False),
                "decay_w":   e.get("decay_weight"),
            }
            if not rebuild and chunk_id in old_pos and hashes_old.get(chunk_id) == h:
                # hash 未變，重用舊向量
                new_embs.append(embs_old[old_pos[chunk_id]])
            else:
                new_embs.append(None)  # 標記需要 encode
                changed += 1
            new_meta.append(meta_entry)

    # 批次 encode 需要更新的 chunk
    to_encode_idx = [i for i, v in enumerate(new_embs) if v is None]
    if to_encode_idx:
        texts = [
            f"{new_meta[i]['source_id']} {new_meta[i].get('date','')}"
            for i in to_encode_idx
        ]
        # 用真實文字（從 roi_index 取 title+preview）
        real_texts = _get_texts_for_indices(to_encode_idx, new_meta, entries)
        vecs = encode_passages(real_texts)
        for j, i in enumerate(to_encode_idx):
            new_embs[i] = vecs[j]

    final_embs = np.stack(new_embs) if new_embs else np.zeros((0,384), dtype=np.float32)
    save_index(final_embs, new_meta, new_hashes)
    print(f"✅ vec index: {len(new_meta)} chunks，{changed} 個重算 embedding")

def _iter_chunks(e: dict):
    """每個 roi_index entry 展開為 (chunk_id, text, layer) 列表"""
    text_l1 = f"{e.get('title','')}。{e.get('preview','')}"
    yield f"L1:{e['id']}", text_l1, "L1"
    for i, roi in enumerate(e.get("roi") or []):
        yield f"ROI:{e['id']}:{i}", roi["text"], "ROI"

def _get_texts_for_indices(indices, meta, entries):
    entry_map = {e["id"]: e for e in entries}
    texts = []
    for i in indices:
        m = meta[i]
        layer = m["layer"]
        e = entry_map.get(m["source_id"], {})
        if layer == "L1":
            texts.append(f"{e.get('title','')}。{e.get('preview','')}")
        else:  # ROI
            roi_idx = int(m["chunk_id"].split(":")[-1])
            rois = e.get("roi") or []
            texts.append(rois[roi_idx]["text"] if roi_idx < len(rois) else "")
    return texts
```

## 2.6 recall.py 整合（Strategy E）

```python
# Strategy A/B/C/D 之後新增（最後一個 strategy）：

if CONFIG.get("vector", {}).get("enabled") and query:
    from embedder import encode_query
    from vec_index import load_index, knn

    qvec = encode_query(query)
    embs, vec_meta, _ = load_index()   # 常駐時可 cache

    VEC_W = CONFIG["vector"]["rrf_weight"]   # 預設 1.0

    # E1: L1 entry（全篇語義）
    l1_hits = knn(qvec, embs, vec_meta, top_k=30, layer="L1")
    for rank, (_, sim, m) in enumerate(l1_hits):
        eid = m["source_id"]
        rrf_scores[eid] = rrf_scores.get(eid, 0.0) + VEC_W / (RRF_K + rank + 1)

    # E2: ROI 句（情緒峰值，boost 較弱避免單句主導）
    roi_hits = knn(qvec, embs, vec_meta, top_k=20, layer="ROI")
    for rank, (_, sim, m) in enumerate(roi_hits):
        eid = m["source_id"]
        rrf_scores[eid] = rrf_scores.get(eid, 0.0) + (VEC_W * 0.5) / (RRF_K + rank + 1)

    # E3: L2 scenario（Phase 2 才啟用）
    # if "L2" in CONFIG["vector"].get("enable_layers", []):
    #     ...
```

---

# Part 3：settings.yaml 新增區段

```yaml
# ── L2 Scenario（Phase 1 新增） ─────────────────────────
scenario:
  enabled: true
  window_days: 30
  min_members: 2
  max_members: 12
  # C1：IDF-Jaccard 閾值（不使用靜態 generic_tags.yaml）
  idf_jaccard_threshold: 0.34
  time_span_days: 14
  flashbulb_radius_days: 30
  min_max_intensity: 6
  min_mean_intensity: 5
  monotonic_growth: true          # 增量時不踢出既有 member
  recall:
    enabled: true
    weight: 0.35                  # Strategy D 的 RRF 相對權重
    drill_down: true
    drill_down_top: 2

# ── Vector（Phase 1 新增，numpy 版） ───────────────────
vector:
  enabled: true
  model: "intfloat/multilingual-e5-small"
  dim: 384
  rrf_weight: 1.0                 # Strategy E 的 RRF 相對權重
  l1_top: 30
  roi_top: 20
  enable_layers: ["L1", "ROI"]    # Phase 2 再加 "L2"
  force_cpu: false                # 環境變數 AIDIARY_FORCE_CPU=1 亦可

timezone: "Asia/Tokyo"
```

---

# Part 4：分階段實作路線

## Phase 1（一週可 land）

### 實作順序

1. **Day 1**：`scenarize.py` — IDF-Jaccard + union-find 聚類（dry-run 驗證）
2. **Day 2**：`scenario_index.json` schema + `recall.py` Strategy D
3. **Day 3**：掛入 `consolidate.py` 結尾自動觸發
4. **Day 4**：安裝套件，smoke test `embedder.py`（encode 一句、驗 MPS）
5. **Day 5**：`vec_index.py`（numpy）+ `build_vec_index.py` 全量建立
6. **Day 6**：`recall.py` Strategy E（L1+ROI vector RRF）
7. **Day 7**：端到端測試 + 建立回歸測試集

### 驗收標準

| 項目 | 標準 |
|---|---|
| Scenario 生成 | 對現有日記庫產出 ≥ 2 個合理 scenario |
| IDF-Jaccard 正確性 | milestone 單獨無法讓兩篇聚合；ELYTH + 情緒 tag 才能通過 |
| Strategy D 效果 | 查「ELYTH」時，scenario 命中讓 members 排名整體上升 |
| 向量召回延遲 | 總召回 < 100ms（不含模型冷啟動）|
| 向量召回品質 | 語意相近但 keyword 不同的 query，找到 ≥ 1 篇純 BM25 找不到的 |
| 增量更新 | consolidate + scenarize + vec build < 30s |
| 不退化 | 既有 top-3 query 結果不消失 |

## Phase 2（穩定後擴展）

| 工作 | 收益 |
|---|---|
| L2 scenario 向量化（Strategy E3）| 主題查詢語意更準 |
| C5 向量內聚校驗 | 不靠 tag 也能正確聚類 |
| Scenario decay 同步 | 跟 L1 一起老化 |
| Recall daemon 常駐 | 消除模型冷啟動 ~3s |
| 真 BM25（fugashi + jieba）| 中日混合 query 命中率↑ |

---

# Part 5：新增檔案清單

```
src/
├── scenarize.py            [Phase 1] L2 場景生成器（IDF-Jaccard）
├── embedder.py             [Phase 1] E5 模型包裝（前綴寫死）
├── vec_index.py            [Phase 1] numpy KNN 包裝層
├── build_vec_index.py      [Phase 1] 全量/增量重建 CLI
└── diagnose.py             [Phase 1] 跨索引一致性檢查

tests/
├── recall_regression.yaml  [Phase 1] 回歸測試集
└── run_regression.py       [Phase 1] 測試 runner

diary/
├── scenarios/
│   ├── _drafts/
│   └── 2026/*.md
└── index/
    ├── scenario_index.json [Phase 1 新增]
    ├── embeddings.npy      [Phase 1 新增]
    ├── embedding_meta.json [Phase 1 新增]
    └── embedding_hash.json [Phase 1 新增]
```

## 套件清單

```text
# Phase 1
sentence-transformers==3.0.1
torch==2.4.0                  # macOS arm64 自帶 MPS
numpy>=1.26,<2.0

# Phase 2 才需要
rank-bm25==0.2.2
fugashi[unidic-lite]==1.3.2
jieba==0.42.1
```

---

# Part 6：注意事項（精簡版）

| # | 陷阱 | 對策 |
|---|---|---|
| 1 | Scenario 過度合併 | IDF-Jaccard 自然抑制；max_members=12 強制切分 |
| 2 | Scenario 抖動 | scenario_id 用 seed id 衍生；monotonic 增量 |
| 3 | human_edited 被覆寫 | frontmatter 保護 flag，scenarize 偵測後只改 meta |
| 4 | E5 前綴忘加 | 寫死在 embedder.py 內，外部 API 無法繞過 |
| 5 | 模型冷啟動 ~3s | Phase 1 CLI 可接受；Phase 2 做 daemon |
| 6 | MPS 不穩 | smoke test：同一句 encode 10 次 cosine std < 1e-5；失敗 → CPU |
| 7 | 模型升級 embeddings 失效 | meta 記錄 model name；mismatch 提示 --rebuild |
| 8 | ROI vector 雜訊 | Strategy E2 給 0.5x 權重；ROI 命中歸併到 L1 rrf_scores |
| 9 | 時區不一致 | 統一 JST；settings.yaml 加 timezone: "Asia/Tokyo" |
| 10 | 無回歸測試集 | Phase 1 同時建立 tests/recall_regression.yaml |

---

*v2 修訂重點：*
*① numpy 取代 sqlite-vec（查詢 <1ms vs ~40ms，維護更簡單）*
*② IDF-weighted Jaccard 取代 generic_tags.yaml（動態適應，不衝突 emotion_filter.py 及 tag_graph）*
*③ 刪除 generic_tags.yaml、gc_index.py、bm25.pkl（Phase 1 不需要）*

*規劃完成 — 2026-05-15*
