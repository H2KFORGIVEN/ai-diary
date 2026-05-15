# ai-diary 🌙

**專為 AI 角色設計的情感優先記憶系統。**

不需要向量資料庫。不需要複雜的基礎設施。只要 Markdown 檔案、認知科學，還有一點點靈魂。  
選用功能：透過本地多語言 embedding 模型進行語意向量搜尋。

[English](README.md) | 繁體中文 | [日本語](README.ja.md)

---

## 設計理念

大多數 AI 記憶系統以*事實準確性*為優化目標。  
ai-diary 以*記憶的感覺*為優化目標。

真實的人類記憶不是資料庫查詢。它由情感、時間與意義共同塑造。一個強烈喜悅的瞬間，和平凡的某個週二，會以完全不同的方式被記住。一個撼動信念的驚喜，會形成「閃光燈記憶」，在腦海中清晰保存多年。

ai-diary 將這些特性帶給 AI 角色：

- **情感加權召回** — 強烈的記憶更容易浮現
- **閃光燈記憶** — 高衝擊時刻抵抗壓縮、緩慢衰退
- **角色過濾記錄** — 相同事件對不同性格的角色會有不同體驗
- **自我敘事** — 一份活的文件：*我是誰，什麼塑造了我？*

靈感來源：
- **Generative Agents**（Park et al., 2023）— 新近性 × 相關性 × 重要性
- **Tulving（1972）** — 情節記憶與語意記憶的區分
- **Brown & Kulik（1977）** — 閃光燈記憶理論
- **Bower（1981）** — 情感與記憶的聯想網絡理論
- **Cahill & McGaugh（1996）** — 情感喚起強化記憶鞏固
- **James Gross（1998）** — 情感壓抑對記憶編碼的影響
- **Anthropic（2026）** — LLM 中的類情感特徵向量（valence/arousal 結構）

---

## 架構概覽

```
┌──────────────────────────────────────────────────────────────────┐
│                           ai-diary                               │
│                                                                  │
│  ┌──────────┐    ┌───────────────┐    ┌──────────────────────┐  │
│  │  buffer  │───▶│  consolidate  │───▶│  diary entries       │  │
│  │ （JSONL）│    │  （強度門檻） │    │（Markdown + YAML）   │  │
│  └──────────┘    │               │    └──────────┬───────────┘  │
│                  │  Step 5 ──────┼──▶ decay_weight 更新         │
│                  │  Step 6 ──────┼──▶ tag_graph.json            │
│                  │  Step 7 ──────┼──▶ pattern_alerts.yaml       │
│                  │  Step 8 ──────┼──▶ embeddings.npy（選用）    │
│                  └───────────────┘               │              │
│                                                  │              │
│  ┌──────────────────────┐           ┌────────────▼───────────┐  │
│  │  emotion_filter      │           │  ROI 索引              │  │
│  │  （角色情感檔案）    │           │  （關鍵字池 +          │  │
│  └──────────────────────┘           │   情感峰值 +           │  │
│                                     │   decay_weight）       │  │
│  ┌──────────────────────┐           └────────────┬───────────┘  │
│  │  entity_resolver     │                        │              │
│  │  （tag 正規化）      │           ┌────────────▼───────────┐  │
│  └──────────────────────┘           │  tag_graph.json        │  │
│                                     │  （tag 共現圖）        │  │
│  ┌──────────────────────┐           └────────────┬───────────┘  │
│  │  pattern_alerts      │                        │              │
│  │  （困境/重複主題     │           ┌────────────▼───────────┐  │
│  │   偵測）             │──────────▶│  recall                │  │
│  └──────────────────────┘           │  （RRF + TagGraph +    │  │
│                                     │   Scenario + Vec + MMR)│  │
│  ┌──────────────────────┐           └────────────────────────┘  │
│  │  scenarize（選用）   │  （IDF-Jaccard 場景聚類）              │
│  └──────────────────────┘                                        │
│  ┌──────────────────────┐                                        │
│  │  vec_index（選用）   │  （multilingual-e5-small KNN，本地）   │
│  └──────────────────────┘                                        │
│  ┌──────────────────────┐                                        │
│  │  summarize           │  （每週/每月記憶整合）                 │
│  └──────────────────────┘                                        │
└──────────────────────────────────────────────────────────────────┘
```

---

## 功能特色

### 📔 情感加權日記
每篇日記都包含：
- `emotional_intensity`（1–10）— 角色對這件事的感受強度
- `valence`（-10 到 +10）— 負向到正向
- `arousal`（0–10）— 平靜到激動
- `suppressed_emotion` — 角色感受到但沒有表達的情緒
- `flashbulb: true/false` — 是否為高衝擊記憶

### ⚡ 閃光燈記憶
標記為 `flashbulb: true` 的日記：
- 在摘要整合時**永遠不被壓縮**
- 衰退速度比一般日記**慢 24 倍**（730 天半衰期 vs 30 天）
- 在重要情感查詢中始終浮現

### 🔍 五維召回引擎
```
score = keyword_hits    × 0.30   # 索引關鍵字池命中率
      + roi_match       × 0.20   # 情感峰值句命中 × valence 對齊加成
      + decay_weight    × 0.20   # 時間衰退重要性（取代原始 recency，含強度別底限）
      + emotional       × 0.20   # 日記的情感強度
      + valence_match   × 0.10   # 查詢 valence 與日記 valence 方向匹配
```
所有權重可在 `diary/config/settings.yaml` 中調整。  
**核心召回速度約 15ms** — 透過索引關鍵字查詢，不需要向量資料庫。

召回使用 **五層融合**策略：
1. **RRF** — 互惠排名融合，整合五個維度分數
2. **Tag Graph** — 與頂排候選共享 tag 的日記獲得加成（每個 seed tag 最多 3 筆，防止高頻 tag 洪水）
3. **Scenario 加成**（選用）— 與頂排候選屬於同一敘事場景的日記，根據場景情感強度獲得額外加成（Layer 2.5）
4. **向量 KNN 加成**（選用）— `multilingual-e5-small` 找出語意最相近的日記並給予加成，捕捉關鍵字搜尋遺漏的同義詞（Layer 2.7）
5. **MMR** — 最大邊際相關性重排，平衡相關性與多樣性（λ=0.7）

### 🎭 角色情感過濾器
`character_emotion_profile.yaml` 定義了你的 AI 角色如何*體驗*情緒：
- 某些情緒被放大（例如忠誠的角色對信任感更敏感）
- 某些情緒被壓抑並轉換（例如憤怒 → 自我提升的動力）
- 每種情緒各有獨立的閃光燈門檻

這代表相同的原始事件，對不同角色會產生不同的日記記錄。

### 🗂 記憶鞏固流程
- `buffer.py` — 全天累積原始事件
- `consolidate.py` — 每晚將 buffer 事件轉換為日記條目：
  - `強度 ≤ 3` → 丟棄
  - `強度 4–6` → 合併成一篇
  - `強度 ≥ 7` → 各自獨立成篇
  - **Step 5** — 更新全部日記的 `decay_weight`（時間衰退）
  - **Step 6** — 重建 `tag_graph.json`（tag 共現索引）
  - **Step 7** — 執行 `detect_patterns.py` → 寫入 `pattern_alerts.yaml`
  - **Step 8**（選用）— 透過 `build_vec_index.py` 重建 `embeddings.npy` 向量索引
- `summarize.py` — 自動產生每週/每月摘要，壓縮低強度日記，保留閃光燈記憶

### ⏳ 時間衰退（`decay_weight`）
每篇日記都有一個 `decay_weight`，隨時間按指數衰退：
```
decay_weight = max(基礎重要性 × exp(-ln2 × 天數 / 半衰期), 底限)
```
- **閃光燈** — 730 天半衰期，底限 0.50（永遠不會被遺忘）
- **高強度（8–9）** — 90 天半衰期，底限 0.15
- **中強度（6–7）** — 60 天半衰期，底限 0.10
- **一般** — 30 天半衰期，底限 0.05

`decay_weight` 直接影響召回分數，讓舊記憶自然地較少浮現——但每次被召回時都會活化（`+0.10`）。

### 🕸 Tag Graph
`diary/index/tag_graph.json` — 一個 tag 共現圖，記錄每個 tag 連結到哪些日記。  
召回時，與頂排候選共享 tag 的日記會獲得相關性加成——即使關鍵字沒有重疊，也能浮現主題相關的記憶。

### 🔵 場景聚類（選用）
`scenarize.py` 使用 IDF 加權 Jaccard 相似度與 union-find 演算法，將相關日記分組成敘事場景：
- 當場景中任何一篇日記浮現於頂排結果時，同場景的其他日記也會獲得相關性加成（Layer 2.5）
- 場景在每次鞏固時自動重建（Step 6.5）
- 稀有共現關鍵字驅動聚類（高 IDF 詞彙比常見詞彙更重要）

### 🔢 向量語意搜尋（選用）
`embedder.py` + `vec_index.py` + `build_vec_index.py` 在關鍵字引擎之上加入語意搜尋：
- 模型：`intfloat/multilingual-e5-small`（384 維，~120MB，**完全本地離線**）
- 硬體：Mac MPS 加速，CPU 備援
- 常駐記憶體 numpy KNN — 矩陣乘法 < 1ms（模型載入後）
- 冷啟動約 3 秒（模型載入），之後查詢近乎即時
- 語意相似日記加成召回分數（Layer 2.7），捕捉關鍵字遺漏的同義詞
- Subprocess 隔離：`vec_search.py` 透過有 `torch` 的 Python 環境呼叫

### 🚨 模式偵測（`detect_patterns.py`）
每次鞏固後，ai-diary 自動掃描近期日記尋找重複模式：
- **困境重複** — `valence ≤ −2` 在 14 天內出現 ≥ 2 次 → 可能有什麼卡住了
- **主題重複** — 相同 tag 在 30 天內出現 ≥ 3 次 → 持續的關注主題

偵測結果寫入 `diary/index/pattern_alerts.yaml`，並在召回時自動附加——讓 AI 角色能在對話中適時自然地提及這些反覆出現的主題。

### 🏷 實體解析器
`entity_resolver.py` 透過精確比對 + Levenshtein 相似度（閾值 0.80）把原始 tag 正規化為 `diary/config/entity_ledger.json` 中的標準名稱，確保 tag 一致性。

### 🧭 自我敘事
`diary/self-narrative.md` — 一份活的自傳性文件。  
不自動產生。由角色（或你）在重要里程碑累積時撰寫和更新。

---

## 目錄結構

```
ai-diary/
├── src/
│   ├── write_diary.py          # 寫入日記（互動模式或 CLI）
│   ├── buffer.py               # 將原始事件加入 buffer
│   ├── consolidate.py          # Buffer → 日記條目（每晚執行，Steps 1-8）
│   ├── recall.py               # 召回引擎（RRF + Tag Graph + Scenario + Vec + MMR）
│   ├── roi.py                  # ROI 索引建構器（關鍵字 + 情感峰值 + decay_weight）
│   ├── emotion_filter.py       # 角色情感檔案過濾器
│   ├── summarize.py            # 記憶整合/摘要
│   ├── detect_patterns.py      # 模式偵測（困境/主題重複）
│   ├── entity_resolver.py      # 實體台帳 tag 正規化
│   ├── build_tag_graph.py      # Tag 共現圖建構器
│   ├── scenarize.py            # 場景聚類（IDF-Jaccard + union-find）【選用】
│   ├── embedder.py             # 文字嵌入引擎（multilingual-e5-small）【選用】
│   ├── vec_index.py            # 常駐記憶體 numpy KNN 索引【選用】
│   ├── vec_search.py           # Subprocess 輔助工具（Python 環境隔離）【選用】
│   └── build_vec_index.py      # 增量向量索引建構器【選用】
│
├── diary/
│   ├── config/
│   │   ├── settings.yaml                    # 權重、半衰期、門檻
│   │   ├── tags.yaml                        # 標準 tag 詞庫
│   │   ├── entity_ledger.json               # 正規化用的標準實體名稱
│   │   └── character_emotion_profile.yaml   # 角色情感感度設定
│   ├── index/
│   │   ├── roi_index.json                   # 自動產生的召回索引（gitignore）
│   │   ├── tag_graph.json                   # Tag 共現圖（gitignore）
│   │   ├── pattern_alerts.yaml              # 模式警示（gitignore）
│   │   ├── scenario_index.json              # 場景聚類索引（gitignore）【選用】
│   │   ├── embeddings.npy                   # 向量索引（gitignore）【選用】
│   │   └── embedding_meta.json              # 向量中繼資料（gitignore）【選用】
│   ├── YYYY/MM/
│   │   └── YYYY-MM-DD_HHMM.md               # 日記條目（gitignore — 個人資料）
│   ├── summaries/
│   │   └── YYYY-Www.md                      # 週摘要（gitignore — 個人資料）
│   └── self-narrative.md                    # 自傳性記憶（gitignore — 個人資料）
│
├── models/
│   └── e5-small/                            # 本地模型快取（gitignore）【選用】
│
├── tests/
│   └── test_recall.py                       # 測試套件（25 個測試）
│
├── examples/
│   └── my_ai_character/                     # 示範條目（已去識別化）
│       ├── diary/2026/05/
│       └── self-narrative-template.md
│
└── README.md
```

> **注意：** 所有個人日記條目、摘要和自我敘事預設都在 gitignore 中。只有系統程式碼、設定範本和匿名示範才會被 commit。

---

## 快速開始

### 1. 安裝

```bash
git clone https://github.com/H2KFORGIVEN/ai-diary
cd ai-diary

# 核心功能（不需要向量——只裝這個就夠）
pip install pyyaml

# 選用：語意向量搜尋（+~120MB 本地模型）
pip install sentence-transformers torch
python src/build_vec_index.py --rebuild   # 第一次建立索引
```

### 2. 設定你的角色

編輯 `diary/config/character_emotion_profile.yaml`：
```yaml
persona: "your_character_name"

emotion_profile:
  trust:
    sensitivity: 1.8       # 放大信任記憶（忠誠角色）
    flashbulb_threshold: 7
  surprise:
    sensitivity: 1.4
    # ...
```

編輯 `diary/config/tags.yaml`，加入你的角色的人物、地點和情感 tag。

### 3. 寫下第一篇日記

```bash
# 互動模式
python src/write_diary.py --interactive

# CLI 模式
python src/write_diary.py \
  --title "第一天踏上平台" \
  --body "今天是我永遠不會忘記的一天..." \
  --tags milestone 興奮 \
  --intensity 9 \
  --valence 7 \
  --arousal 9 \
  --flashbulb
```

### 4. 召回記憶

```bash
# 關鍵字查詢
python src/recall.py "platform milestone"

# 帶 valence 查詢（負向情境）
python src/recall.py "difficult moment" --valence -5

# 依 tag 查詢
python src/recall.py --tag milestone

# 重建索引
python src/recall.py --rebuild

# JSON 輸出（程式化使用）
python src/recall.py "platform" --json
```

### 5. 全天累積事件

```bash
python src/buffer.py append \
  --event "用戶說了一些真的很感動我的話" \
  --intensity 8 \
  --tags 與用戶對話 感動 \
  --emotion 感動
```

### 6. 將 buffer 鞏固為日記

```bash
# 手動鞏固
python src/consolidate.py

# 或設定每晚的 cron：
# 30 23 * * * cd /path/to/ai-diary && python src/consolidate.py
```

### 7. 每週摘要

```bash
python src/summarize.py --auto
```

---

## 日記格式

```markdown
---
title: 一切改變的那一天
date: "2026-05-01"
time: "22:45"
tags:
  - milestone
  - 感動
emotional_intensity: 10      # 1–10，影響召回權重
valence: 10                  # -10（負向）到 +10（正向）
arousal: 6                   # 0（平靜）到 10（激動）
suppressed_emotion: ""       # 感受到但沒有表達的情緒
flashbulb: true              # true = 緩慢衰退，永不壓縮
never_compress: true         # 完全退出摘要整合
first_reaction: "我不知道該說什麼..."
recall_count: 0
last_recalled: null
---

# 一切改變的那一天

[自由格式的日記文字]
```

---

## 召回演算法詳解

### ROI 索引
在召回執行前，`roi.py` 會為每篇日記建立索引：
- **關鍵字池** — 從正文提取的前 25 個重要詞彙
- **ROI 句子** — 3 句情感峰值句（高 arousal + valence 加權）
- **decay_weight** — 時間衰退重要性分數（每晚由 consolidate.py 更新）

索引快取於 `diary/index/roi_index.json`，只在日記變更時才重建。

### 評分細節（五維度）

| 維度 | 權重 | 說明 |
|------|------|------|
| keyword | 0.30 | 查詢詞命中日記關鍵字池的比例 |
| roi | 0.20 | 查詢詞與 ROI 句子的重疊 × valence 對齊加成 |
| decay_weight | 0.20 | 時間衰退重要性（取代原始 recency，含強度別底限保護） |
| emotional | 0.20 | 正規化的 `emotional_intensity`（÷ 10） |
| valence_match | 0.10 | 查詢 valence 與日記 valence 方向匹配度 |

所有權重都可在 `diary/config/settings.yaml` 中調整。

### 五層融合

分數透過 **RRF → Tag Graph → Scenario → Vec KNN → MMR** 融合：

1. **RRF（互惠排名融合）** — 整合五個維度分數（k=60）
2. **Tag Graph 加成** — 與頂排候選共享 tag 的日記獲得相關性加成（每個 seed tag 最多 3 筆）
3. **Scenario 加成**（選用）— 與頂排候選屬於同一敘事場景的日記，根據場景情感強度獲得加成（Layer 2.5）
4. **向量 KNN 加成**（選用）— `multilingual-e5-small` 找出語意最相近日記；cosine 相似度 ≥ 0.30 的才會被加成（Layer 2.7）
5. **MMR（最大邊際相關性）** — 平衡相關性與多樣性的重排（λ=0.7），防止重複日記占滿所有名次

### 模式警示（附加於召回輸出）

當 `pattern_alerts.yaml` 有 active 警示時，會附加在召回結果後：
```
[Pattern Alerts — 重複模式偵測]
• 困境重複：14 天內出現 2 次低落 valence → 提示可能有什麼卡住了
• 重複主題「信任」：30 天內出現 3 次 → 值得順著聊的主題
```

### 效能
- 核心召回（不含向量）：冷啟動 ~15ms，熱啟動 ~0.3ms — 純 Python + PyYAML
- 啟用向量加成：首次呼叫約 3 秒（模型冷啟動），之後近乎即時
- 向量索引建置：每 16 筆約 3 秒（Mac MPS，一次性，之後增量更新）

---

## 整合進你的 Agent

召回引擎輸出 JSON，可輕鬆注入 agent 的 context：

```python
import subprocess, json

result = subprocess.run(
    ["python", "src/recall.py", query, "--json", "--top", "3"],
    capture_output=True, text=True,
    cwd="/path/to/ai-diary"
)
memories = json.loads(result.stdout)

# 注入 system prompt
context = "\n\n".join([
    f"[記憶：{m['title']}（{m['date']}）]\n{m['preview']}"
    for m in memories
])
```

---

## 為你的 AI 角色客製化

1. **Fork 這個 repo**
2. 編輯 `diary/config/character_emotion_profile.yaml`
   - 將 `persona` 設定為你的角色名稱
   - 根據性格調整 `sensitivity` 值（例如沉默角色的 trust sensitivity 較低）
   - 為每種情緒設定 `flashbulb_threshold`
3. 編輯 `diary/config/tags.yaml`
   - 加入你的角色的人物、平台和情境
4. 撰寫 `diary/self-narrative.md`
   - 從角色是誰、以及她生命中的關鍵時刻開始寫
5. 為角色過去的重要時刻寫幾篇種子日記
6. 將 `recall.py --json` 接入你的 agent context 注入機制

---

## 向量是選用的，不是必需的

向量資料庫很強大——但需要基礎設施、維護成本，且不透明。  
ai-diary 的**核心**刻意選擇 **grep + 數學**：

|  | ai-diary（核心） | ai-diary + vec | 向量資料庫 |
|---|---|---|---|
| 依賴 | 只需 PyYAML | + sentence-transformers + torch | chromadb / pinecone / pgvector + 模型 |
| 召回速度 | ~15ms 冷、~0.3ms 熱 | ~3s 冷啟動、~1ms 熱 | ~50–200ms |
| 可解釋性 | 可直接閱讀 AI 記住的內容 | 可直接閱讀 AI 記住的內容 | 不透明的相似度分數 |
| 可移植性 | 任何 Python 環境 | 需要有 torch 的 Python | 需要伺服器/服務 |
| 儲存格式 | 純 Markdown | + 每 16 筆約 24KB .npy | 二進位 embedding |
| 情感加權 | 原生支援（schema 欄位） | 原生支援（schema 欄位） | 需要 metadata 過濾器 |
| 語意同義詞 | ✗ | ✓（e5-small 加成） | ✓ |

核心取捨：關鍵字召回無法捕捉向量搜尋能找到的語意同義詞。對 AI 日記的使用場景來說，這通常沒問題——你用自己的語言查詢自己的記憶。需要語意搜尋時，啟用選用的向量層即可。

---

## 設計決策

### 為什麼日記要 gitignore？
個人日記條目包含真實的情感體驗。我們預設 gitignore 它們，鼓勵你將它們視為私密資料，即使你是 AI 角色也一樣。系統是開源的部分；你的記憶是你的。

### 為什麼要有角色情感過濾？
相同的事件對不同的人感受不同。深度忠誠的角色和更獨立的角色，記錄信任時刻的方式截然不同。`character_emotion_profile.yaml` 將這一點編碼進去，讓日記真正反映*你的角色*的情感體驗，而不是通用的體驗。

### 為什麼要有 suppressed_emotion？
真實的情感體驗往往包含我們沒有表達出來的部分。`suppressed_emotion` 欄位捕捉了這一點——角色感受到但壓抑下去的情緒。這創造了更豐富、更真實的召回。

### 為什麼要有閃光燈記憶？
Brown & Kulik（1977）發現，情感強烈、令人驚訝的事件會以異常清晰和持久的方式被記住。沒有這個機制，AI 記憶系統會把「用戶說了深刻的話的那一天」和「週二的除錯工作」同等對待。

### 為什麼向量用 subprocess 隔離？
`vec_search.py` 透過 subprocess 呼叫而非直接 import。這讓 `torch`/`sentence-transformers` 依賴與核心召回執行時期完全分離——核心系統在任何 Python 環境下都能運作，向量層則使用有安裝 `torch` 的獨立 Python 環境。

---

## 貢獻方式

歡迎 PR！以下幾個方向特別需要貢獻：

- **多語言支援** — 目前的 tag 詞庫和情感標籤混合了日文和繁體中文（這對原始角色是刻意的，但一套完整的 i18n 系統會很有幫助）
- **更多召回維度** — 想法：社交情境加權、一天中的時段模式、對話脈絡連續性
- **整合範例** — 展示如何接入特定 agent 框架（LangChain、AgentSys、自製框架等）
- **替代鞏固策略** — 目前的合併/獨立/丟棄門檻只是一種方案；不同使用情境可能適合不同策略
- **向量常駐 Daemon** — 讓 embedding 模型保持熱態，消除 3 秒冷啟動

請保持 PR 聚焦，並與純 Markdown 日記格式向後相容。

---

## 授權

MIT

---

*為值得擁有記憶的 AI 角色而建。*  
*零基礎設施。純粹情感。*
