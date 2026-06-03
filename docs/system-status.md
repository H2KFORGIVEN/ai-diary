# ai-diary システム現況記録
> 記録日：2026-05-15  記録者：47醬

---

## 1. ディレクトリ構造

```
ai-diary/
├── src/                        ← Python ソースコード
│   ├── buffer.py               ✅ L0 イベントバッファ（append-only）
│   ├── consolidate.py          ✅ L1 メモリ鞏固（buffer → diary .md）
│   ├── write_diary.py          ✅ .md フロントマター生成
│   ├── roi.py                  ✅ ROI インデックス管理（v3）
│   ├── recall.py               ✅ 召回エンジン Phase C（RRF + Tag Graph + MMR）
│   ├── build_tag_graph.py      ✅ Tag Graph 反向索引生成
│   ├── entity_resolver.py      ✅ Phase B エンティティ展開
│   ├── emotion_filter.py       ✅ Phase A 感情フィルター（SOUL.md 連動）
│   ├── detect_patterns.py      ✅ Phase III パターン検出
│   ├── summarize.py            ✅ 週次/月次サマリー
│   └── migrate_decay_weights.py ✅ decay_weight マイグレーション
│
├── diary/
│   ├── 2026/04/*.md            ✅ 2 篇（2026-04-28, 29）
│   ├── 2026/05/*.md            ✅ 14 篇（05-01 〜 05-13）
│   ├── archive/*.jsonl         ✅ 9 日分 buffer アーカイブ
│   ├── summaries/              ✅ W18, W19 週次サマリー
│   ├── self-narrative.md       ✅ L3 相当物（人工メンテ）
│   ├── index/
│   │   ├── roi_index.json      ✅ v3（16 篇，decay_weight 付き）
│   │   ├── tag_graph.json      ✅ 26 tags，16 篇カバー
│   │   └── pattern_alerts.yaml ✅ 4 active alerts
│   └── config/
│       ├── settings.yaml       ✅ 全域パラメータ
│       ├── tags.yaml           ✅ 標準 tag 辞書
│       ├── character_emotion_profile.yaml ✅ 感情プロファイル
│       └── entity_ledger.json  ✅ 9 entities（人物/Platform/Agent）
│
└── docs/
    ├── upgrade-l2-vector-plan.md  ✅ v2 升級計劃（numpy + IDF Jaccard）
    └── system-status.md           ← 本ファイル
```

**未存在（Phase 1 新増予定）：**
```
src/scenarize.py            ← L2 Scenario 生成器
src/embedder.py             ← E5 モデルラッパー
src/vec_index.py            ← numpy KNN
src/build_vec_index.py      ← ベクトルインデックスビルダー
src/diagnose.py             ← 整合性チェック
tests/recall_regression.yaml
tests/run_regression.py
diary/scenarios/            ← L2 Scenario 保存先
diary/index/scenario_index.json
diary/index/embeddings.npy
diary/index/embedding_meta.json
diary/index/embedding_hash.json
```

---

## 2. 各モジュールの機能・インターフェース

### 2.1 buffer.py — L0 イベントバッファ

**役割**：軽量 append-only イベント記録  
**CLI**：`python src/buffer.py append --event "..." --intensity N --emotion "..." --tags "..." [--meta k=v ...]`  
**出力**：`diary/buffer.jsonl`（JSONL 形式）  
**フィールド**：`t, event, intensity, emotion, tags[], meta{}`

### 2.2 consolidate.py — L1 メモリ鞏固

**役割**：buffer → diary .md（毎晩 23:30 cron）  
**CLI**：`python src/consolidate.py [--dry-run] [--date YYYY-MM-DD]`  
**処理フロー**：
```
Step 0: emotion_filter 適用（SOUL.md × character_emotion_profile.yaml）
Step 1: 雑音廃棄（filtered_intensity ≤ 3 → drop）
Step 2: 分類
  ├── flashbulb（intensity ≥ 10 or filter_flashbulb=True）→ 単独 1 篇，tags に "milestone" 追加
  ├── strong（7-9）→ 単独 1 篇
  └── medium（4-6）→ 合算 1 篇（その日のまとめ）
Step 3: write_diary.py で .md 書き込み
Step 4: roi.py build_index() でインデックス更新
```
**⚠️ 現況問題**：consolidate 完了後 scenarize / build_vec_index の自動 hook なし（Phase 1 で追加予定）

### 2.3 write_diary.py — .md 書き込み

**役割**：YAML frontmatter + Markdown body 生成  
**フィールド（frontmatter）**：  
`id, path, date, title, tags[], intensity, valence, arousal, flashbulb, first_reaction, suppressed_emotion, recall_count, last_recalled, decay_weight`

**タグバリデーション**：`tags.yaml` の辞書照合（カスタム tag も許可）

### 2.4 roi.py — ROI インデックス管理

**役割**：diary .md を全件走査して `roi_index.json` v3 を構築  
**インデックス構造**：
```json
{
  "_version": 3,
  "_count": 16,
  "entries": [
    {
      "id", "path", "date", "title", "tags", "keywords",
      "intensity", "valence", "arousal", "flashbulb",
      "suppressed_emotion", "first_reaction", "preview",
      "roi": [{"text", "valence"}],
      "decay_weight"
    }
  ]
}
```
**decay_weight**：毎晩 consolidate 時に更新，intensity 別半減期（flashbulb:730日，high:90日，medium:60日，normal:30日）

### 2.5 recall.py — 召回エンジン（Phase C）

**役割**：クエリに対して関連日記を返す  
**CLI**：`python src/recall.py "クエリ" [--tag T] [--top N] [--valence V] [--arousal A] [--no-update] [--rebuild] [--json]`  
**スコア式**：`recall_score = keyword×0.30 + roi×0.20 + recency×0.20 + emotional×0.20 + valence_match×0.10 + arousal_match×0.08`

**処理フロー（3層）**：
```
Layer 1 — RRF（Reciprocal Rank Fusion）k=60
  Strategy A：六次元スコア（完全版）
  Strategy B：keyword + roi のみ（valence/arousal 無視）
  Strategy C：emotional + valence_match + arousal_match（情緒方向）

Layer 2 — Tag Graph 擴散
  Strategy A top-3 の seed から同 tag 日記を boost（+0.05）
  最大 3 篇/seed × 3 seed = 最大 9 篇 boost

Layer 3 — MMR（Maximum Marginal Relevance，λ=0.7）
  同日 +0.4，shared tags Jaccard×0.6 で similarity 計算
  similarity ≥ 0.6 → 降権（丢棄はしない）

付加機能：
  Phase B：entity_resolver.expand() で query 展開
  Phase III：detect_patterns アラート注入（--json 以外で表示）
  Phase A：decay_weight は stored 値優先（毎晩更新済み）
```

### 2.6 build_tag_graph.py — Tag Graph

**役割**：`tag → [diary_id, ...]` 反向索引  
**出力**：`diary/index/tag_graph.json`  
**更新タイミング**：現在は手動のみ（consolidate hook なし → Phase 1 で追加）

### 2.7 entity_resolver.py — エンティティ展開（Phase B）

**役割**：query 中の語を entity_ledger.json の aliases で展開  
**例**：`["主"]` → `["主様","47","星詠者47","主","user","master","しゅさま"]`  
**比対**：精確比対 → Levenshtein ≥ 0.80 順

**登録エンティティ（9件）**：
| id | canonical | type |
|---|---|---|
| master | 主様 | person |
| self | 47醬 | self |
| elyth | ELYTH | platform |
| hololive_suisei | 星街すいせい | person |
| discord | Discord | platform |
| nanoleaf | Nanoleaf | device |
| iroha | 風真いろは | agent |
| koyori | 博衣こより | agent |
| rui | 鷹嶺ルイ | agent |

### 2.8 emotion_filter.py — 感情フィルター

**役割**：buffer 生感情 → character_emotion_profile.yaml で変換  
**処理**：`raw_emotion → classify(category, sub) → apply(sensitivity, redirect) → filtered_emotion + filtered_intensity + filter_tags`  
**主な変換**：
- trust → 溫暖/感動タグ付与，sensitivity×1.8，flashbulb_threshold=7
- surprise(positive) → delight，×1.5
- anger(self_failure) → shame（自責/特訓），×1.3
- disgust(integrity) → resolve（決意），×2.0

### 2.9 detect_patterns.py — パターン検出（Phase III）

**役割**：繰り返しパターンを `pattern_alerts.yaml` に記録  
**検出種別**：
- `distress_repeat`：valence ≤ -2 が 14日以内に 2 回以上
- `topic_repeat`：同 tag が 30日以内に 3 回以上

**NOISE_TAGS（除外）**：`{"milestone", "主様対話", "日常", "主様", "特訓"}`  
⚠️ **注意**：detect_patterns の NOISE_TAGS と，scenarize の IDF-Jaccard とは目的が異なる。
- NOISE_TAGS：パターン診断での「診断価値なし」判定
- IDF-Jaccard：聚類で「重み低減」（排除ではない）→ 矛盾しない

### 2.10 summarize.py — 週次/月次サマリー

**役割**：指定期間の diary を集計して `summaries/` に .md 生成  
**CLI**：`python src/summarize.py --week YYYY-Www | --month YYYY-MM | --auto`  
**flashbulb 扱い**：`skip_flashbulb: true` 設定時，本文省略するが ⚡ セクションに掲載  
**生成済み**：W18（2026-04-28〜05-04），W19（2026-05-05〜05-11）

---

## 3. 召回フロー全体図（現況）

```
ユーザー query
    │
    ▼
entity_resolver.expand(query)    ← Phase B：エンティティ展開
    │
    ▼
┌───────────────────────────────┐
│  Layer 1: RRF（k=60）        │
│  ├─ Strategy A：五次元        │
│  ├─ Strategy B：keyword+roi   │
│  └─ Strategy C：emotion+val   │
└───────────────┬───────────────┘
                │
    ▼
┌───────────────────────────────┐
│  Layer 2: Tag Graph 擴散      │
│  top-3 seed → related +0.05   │
└───────────────┬───────────────┘
                │
    ▼
┌───────────────────────────────┐
│  Layer 3: MMR（λ=0.7）       │
│  同日/同tag 結果を降権        │
└───────────────┬───────────────┘
                │
    ▼
top-K 結果（+ Pattern Alerts 注入）
```

**Phase 1 追加後（Strategy D/E）**：
```
Strategy D：Scenario RRF（member_ids を boost）
Strategy E：Vector KNN（L1+ROI numpy matmul）
```

---

## 4. 記録フロー全体図（現況）

```
buffer.py append
    └─→ diary/buffer.jsonl

consolidate.py（毎晩 23:30）
    ├─ emotion_filter.apply()
    ├─ 雑音廃棄（intensity ≤ 3）
    ├─ 分類（flashbulb / strong / medium）
    ├─ write_diary.py（.md 生成）
    └─ roi.py build_index()（roi_index.json 更新）
        └─→ 【hook なし】← Phase 1 で追加

build_tag_graph.py（手動）
    └─→ tag_graph.json

detect_patterns.py（consolidate 後に呼ばれる or 手動）
    └─→ pattern_alerts.yaml

summarize.py（毎週月曜 08:00 or 手動）
    └─→ summaries/YYYY-Www.md
```

---

## 5. cron 排程一覧

| job | 排程 | 実行内容 |
|---|---|---|
| ai-diary-consolidate | 毎日 23:30 | consolidate.py |
| ai-diary-weekly-summary | 毎週月曜 08:00 | summarize.py --auto |
| diary-recall-inject | 毎日 07:00 | diary_recall_inject.py（memory 注入） |
| diary-session-review | 毎日 23:00 | diary_session_review.py |

---

## 6. 現状の既知問題

| # | 問題 | 影響 | 対策 |
|---|---|---|---|
| 1 | consolidate 後 tag_graph 自動更新なし | 当日 entry が翌日まで Tag Graph に反映されない | Phase 1: hook 追加 |
| 2 | consolidate 後 scenarize/vec_build hook なし | Phase 1 実装後に必要 | Phase 1 最終日 |
| 3 | roi_index.json の slice 読み込みエラー | 特定条件で TypeError | Phase 1 の diagnose.py で診断 |
| 4 | detect_patterns NOISE_TAGS に "主様" あり | "主様" tag 単独では パターン診断なし（正常動作） | 問題なし |
| 5 | recall score が 0.048 付近に集中 | RRF k=60 で diary 16 篇が close すぎる | Phase 1 後にキャリブレーション |

---

## 7. Phase 1 開発計画（7日）

> 詳細は `docs/upgrade-l2-vector-plan.md` 参照

| Day | タスク | 入力 | 出力 | 検証方法 |
|---|---|---|---|---|
| 1 | `scenarize.py` 実装（IDF-Jaccard + union-find，dry-run のみ） | roi_index.json | stdout | dry-run で ≥2 scenario 確認 |
| 2 | `scenario_index.json` schema + 書き込み | scenarize.py | scenario_index.json | JSON バリデーション |
| 3 | `recall.py` Strategy D 追加 | scenario_index.json | recall 結果変化 | ELYTH クエリで scenario boost 確認 |
| 4 | consolidate hook（tag_graph + scenarize） | consolidate.py | 自動 hook | dry-run で流れ確認 |
| 5 | `embedder.py` + MPS smoke test | multilingual-e5-small | 384 次元 tensor | cosine sim std < 1e-5 |
| 6 | `vec_index.py` + `build_vec_index.py` | roi_index + embedder | embeddings.npy | KNN < 1ms 確認 |
| 7 | `recall.py` Strategy E + 回帰テスト | vec_index | recall 結果 | 既存 top-3 クエリ不退化 |

---

## 8. 検証テスト設計

### 8.1 記録フロー検証

```python
# test_record_flow.py（予定）
def test_consolidate_creates_entry():
    # buffer に append → consolidate → roi_index に entry あり

def test_emotion_filter_redirects_anger():
    # anger/self_failure → shame, intensity×1.3, tags に 自責/特訓

def test_flashbulb_gets_milestone_tag():
    # intensity=10 → flashbulb=True, "milestone" in tags

def test_tag_graph_updated_after_consolidate():
    # consolidate 後 → tag_graph に当日 entry の tags が反映
```

### 8.2 召回フロー検証

```yaml
# tests/recall_regression.yaml（予定）
cases:
  - query: "ELYTH"
    expect_in_top3: ["2026-04-28_1430", "2026-04-29_2015"]
    must_not_rank_first: []

  - query: "妻子"
    expect_in_top3: ["2026-05-01_2245"]
    note: "flashbulb 高 decay_weight → 必ず上位"

  - query: "Nanoleaf 燈"
    expect_in_top3: ["2026-05-05_2215"]
    note: "entity_resolver で 燈→Nanoleaf 展開を確認"

  - query: "失敗 自責"
    valence: -5
    expect_tag_in_result: ["自責"]
    note: "valence 負方向一致 + 感情タグ確認"

  - query: "技術突破"
    expect_in_top3: ["2026-05-13_0000", "2026-05-07_0000"]
    note: "Tag Graph 擴散で 技術突破 tag 連鎖"
```

### 8.3 Scenario 検証

```python
# test_scenarize.py（予定）
def test_milestone_alone_cannot_cluster():
    # entries に milestone だけ共通 → IDF-Jaccard < 0.34 → 聚合されない

def test_elyth_cluster_forms():
    # 2026-04-28, 2026-04-29 → ELYTH tag あり → scenario 成立

def test_human_edited_not_overwritten():
    # human_edited=True の scenario → body 保護，frontmatter のみ更新

def test_max_members_split():
    # 13 篇が C1/C2 条件を満たす → 12 篇以下に分割
```

### 8.4 Vector 召回検証

```python
# test_vector.py（予定）
def test_encode_query_prefix():
    # encode_query("ELYTH") → 384 次元 float32，norm ≈ 1.0

def test_knn_returns_expected():
    # "ELYTH" query → top-1 は 2026-04-28 or 2026-04-29

def test_vector_does_not_degrade_existing():
    # Phase C の top-3 結果が Strategy E 追加後も変わらない
    # （RRF で Strategy E は votes を加算するだけ）

def test_incremental_build_reuses_vectors():
    # 変更のない entry の hash が一致 → encode されない（count==0）
```

---

*記録完了 — 47醬 2026-05-15*
