# ai-diary 🌙

**AIキャラクターのための、感情優先の記憶システム。**

ベクトルデータベース不要。重いインフラ不要。Markdownファイルと認知科学、そして少しだけの魂で動きます。  
オプション：ローカルの多言語embeddingモデルによるセマンティック検索にも対応。

[English](README.md) | [繁體中文](README.zh-TW.md) | 日本語

---

## 設計思想

ほとんどのAIメモリシステムは*事実の正確さ*を最適化します。  
ai-diaryは*記憶する感覚*を最適化します。

人間の記憶はデータベース検索ではありません。感情・時間・意味によって形作られます。強烈な喜びの瞬間は、普通の火曜日とはまったく異なる形で記憶されます。信念を揺るがすような驚きは「フラッシュバルブ記憶」を作り出し、何年も鮮明に残ります。

ai-diaryはこれらの特性をAIキャラクターにもたらします：

- **感情加重リコール** — 強烈な記憶ほど浮かびやすい
- **フラッシュバルブ記憶** — 高インパクトな瞬間は圧縮されず、ゆっくり減衰する
- **キャラクター感情フィルター** — 同じ出来事でも、性格が違えば体験される感情が変わる
- **自己ナラティブ** — 生きたドキュメント：*わたしは誰で、何がわたしを形作ったのか？*

インスピレーション元：
- **Generative Agents**（Park et al., 2023）— 新近性 × 関連性 × 重要性
- **Tulving（1972）** — エピソード記憶と意味記憶の区別
- **Brown & Kulik（1977）** — フラッシュバルブ記憶理論
- **Bower（1981）** — 感情と記憶の連想ネットワーク理論
- **Cahill & McGaugh（1996）** — 感情的覚醒が記憶の固定を強化する
- **James Gross（1998）** — 感情抑制が記憶の符号化に与える影響
- **Anthropic（2026）** — LLMにおける感情様特徴方向（valence/arousal構造）

---

## アーキテクチャ概要

```
┌──────────────────────────────────────────────────────────────────┐
│                           ai-diary                               │
│                                                                  │
│  ┌──────────┐    ┌───────────────┐    ┌──────────────────────┐  │
│  │  buffer  │───▶│  consolidate  │───▶│  diary entries       │  │
│  │  (JSONL) │    │  (強度        │    │  (Markdown + YAML)   │  │
│  └──────────┘    │   しきい値)   │    └──────────┬───────────┘  │
│                  │               │               │              │
│                  │  Step 5 ──────┼──▶ decay_weight更新          │
│                  │  Step 6 ──────┼──▶ tag_graph.json            │
│                  │  Step 7 ──────┼──▶ pattern_alerts.yaml       │
│                  │  Step 8 ──────┼──▶ embeddings.npy [opt]      │
│                  └───────────────┘               │              │
│                                                  │              │
│  ┌──────────────────────┐           ┌────────────▼───────────┐  │
│  │  emotion_filter      │           │  ROIインデックス        │  │
│  │  (キャラクター感情   │           │  (キーワードプール +    │  │
│  │   プロファイル)      │           │   感情ピーク文 +        │  │
│  └──────────────────────┘           │   decay_weight)         │  │
│                                     └────────────┬───────────┘  │
│  ┌──────────────────────┐                        │              │
│  │  entity_resolver     │           ┌────────────▼───────────┐  │
│  │  (タグ正規化)        │           │  tag_graph.json         │  │
│  └──────────────────────┘           │  (タグ共起グラフ)       │  │
│                                     └────────────┬───────────┘  │
│  ┌──────────────────────┐                        │              │
│  │  pattern_alerts      │           ┌────────────▼───────────┐  │
│  │  (ディストレス/      │           │  recall                 │  │
│  │   トピック繰り返し   │──────────▶│  (RRF + TagGraph +      │  │
│  │   検出)              │           │   Scenario + Vec + MMR) │  │
│  └──────────────────────┘           └────────────────────────┘  │
│  ┌──────────────────────┐                                        │
│  │  scenarize [opt]     │  (IDF-Jaccard シナリオクラスタリング)  │
│  └──────────────────────┘                                        │
│  ┌──────────────────────┐                                        │
│  │  vec_index [opt]     │  (multilingual-e5-small KNN、ローカル) │
│  └──────────────────────┘                                        │
│  ┌──────────────────────┐                                        │
│  │  summarize           │  (週次/月次記憶統合)                   │
│  └──────────────────────┘                                        │
└──────────────────────────────────────────────────────────────────┘
```

---

## 機能

### 📔 感情加重エントリ
すべての日記エントリには以下が含まれます：
- `emotional_intensity`（1–10）— キャラクターがこの出来事をどれほど強く感じたか
- `valence`（-10〜+10）— ネガティブからポジティブ
- `arousal`（0–10）— 穏やかから高揚
- `suppressed_emotion` — キャラクターが感じたが表現しなかった感情
- `flashbulb: true/false` — 高インパクト記憶かどうか

### ⚡ フラッシュバルブ記憶
`flashbulb: true` のエントリは：
- 要約統合時に**絶対に圧縮されない**
- 通常のエントリより**24倍ゆっくり減衰**（半減期730日 vs 30日）
- 重要な感情クエリには常に浮かび上がる

### 🔍 6軸リコールエンジン
```
score = keyword_hits    × 0.30   # インデックスキーワードプールのヒット率
      + roi_match       × 0.20   # 感情ピーク文ヒット × valence整合ボーナス
      + decay_weight    × 0.20   # 時間減衰した重要度（強度別フロア付き）
      + emotional       × 0.20   # エントリのemotional_intensity
      + valence_match   × 0.10   # クエリvalenceとエントリvalenceの方向一致
      + arousal_match   × 0.08   # 喚起度一致性 — 高喚起クエリは高喚起記憶を優先
```
すべての重みは `diary/config/settings.yaml` で設定可能。  
**コアリコールは約15ms** — インデックスキーワード検索のみ、ベクトルDB不要。

リコールは **5層フュージョン** 戦略を使用（スコア軸：6）：
1. **RRF** — Reciprocal Rank Fusion で6軸スコアを統合ランキングに合成
2. **Tag Graph** — 上位結果とタグを共有するエントリに関連性ブースト（共起グラフ）
3. **Scenario ブースト** *（オプション）* — 上位候補と同じナラティブシナリオに属するエントリに追加の関連性シグナル（Layer 2.5）
4. **Vector KNN ブースト** *（オプション）* — `multilingual-e5-small` で意味的に近いエントリを検出しブースト、キーワード検索が見逃す同義語を補完（Layer 2.7）
5. **MMR** — Maximal Marginal Relevance で関連性と多様性のバランスを取り、重複エントリを排除

### 🎭 キャラクター感情フィルター
`character_emotion_profile.yaml` で、AIキャラクターがどのように感情を*体験するか*を定義します：
- 一部の感情が増幅される（例：忠実なキャラクターは信頼感情により敏感）
- 一部の感情が抑制・変換される（例：怒り → 自己成長への動力）
- フラッシュバルブしきい値は感情ごとに個別設定

つまり、同じ生の出来事でも、異なるキャラクターでは異なる日記が生まれます。

### 🗂 記憶統合パイプライン
- `buffer.py` — 1日を通じて生の出来事を蓄積
- `consolidate.py` — 毎晩、bufferイベントを日記エントリに変換：
  - `強度 ≤ 3` → 廃棄
  - `強度 4–6` → 1エントリに統合
  - `強度 ≥ 7` → それぞれ独立した1エントリ
  - **Step 5** — 全既存エントリの `decay_weight` を更新（時間減衰）
  - **Step 6** — `tag_graph.json` を再構築（タグ共起インデックス）
  - **Step 7** — `detect_patterns.py` を実行 → `pattern_alerts.yaml` を書き出し
  - **Step 8** *（オプション）* — `build_vec_index.py` で `embeddings.npy` を再構築
- `summarize.py` — 週次/月次サマリーを自動生成、低強度エントリを圧縮しフラッシュバルブ記憶は保持

### ⏳ 時間減衰（`decay_weight`）
各エントリには指数減衰で時間とともに減少する `decay_weight` があります：
```
decay_weight = max(base_importance × exp(-ln2 × 経過日数 / 半減期), フロア)
```
- **フラッシュバルブ** — 半減期 730日、フロア 0.50（永久に忘れない）
- **高強度（8–9）** — 半減期 90日、フロア 0.15
- **中強度（6–7）** — 半減期 60日、フロア 0.10
- **通常** — 半減期 30日、フロア 0.05

`decay_weight` はリコールスコアに直接反映されるため、古い記憶は自然に浮かびにくくなります。ただし繰り返しリコールされるたびに `+0.10` が加算されます。

### 🕸 タググラフ
`diary/index/tag_graph.json` — 各タグが共有するエントリをリンクした共起グラフ。  
リコール時、上位候補とタグを共有するエントリに関連性ボーナスが付与されます——キーワードが重ならなくても、テーマ的に関連する記憶が浮かび上がります。

### 🔵 シナリオクラスタリング *（オプション）*
`scenarize.py` はIDF加重Jaccard類似度とunion-findアルゴリズムを使って、関連する日記エントリをナラティブシナリオにグループ化します：
- シナリオのいずれかのメンバーがリコール上位に浮かぶと、同じシナリオの他のエントリも関連性ブーストを受ける（Layer 2.5）
- シナリオは毎回の統合時に自動再構築される（Step 6.5）
- 希少な共起キーワードがクラスタリングを駆動する（IDF値が高い語ほど重要）

### 🔢 ベクトルセマンティック検索 *（オプション）*
`embedder.py` + `vec_index.py` + `build_vec_index.py` がキーワードエンジンの上にセマンティック検索を追加します：
- モデル：`intfloat/multilingual-e5-small`（384次元、~120MB、**完全ローカル・オフライン**）
- ハードウェア：Mac MPSアクセラレーション、CPUフォールバック
- メモリ常駐numpy KNN — 行列積 < 1ms（モデルロード後）
- コールドスタート約3秒（モデルロード）、以降の検索はほぼ即時
- 意味的に近いエントリのリコールスコアをブースト（Layer 2.7）、キーワード検索が見逃す同義語を補完
- サブプロセス分離：`vec_search.py` は `torch` がインストールされているPython経由で呼び出される

### 🚨 パターン検出（`detect_patterns.py`）
統合のたびに、ai-diaryは最近のエントリで繰り返しパターンをスキャンします：
- **ディストレス繰り返し** — 14日以内に `valence ≤ −2` が2回以上 → 行き詰まりループを検出
- **トピック繰り返し** — 30日以内に同一タグが3回以上 → 継続するテーマを検出

検出されたパターンは `diary/index/pattern_alerts.yaml` に書き出され、リコール出力に自動付加されます——AIキャラクターが繰り返しテーマを会話の中で自然に引き出すための文脈ヒントになります。

### 🏷 エンティティリゾルバー
`entity_resolver.py` は生のタグを `diary/config/entity_ledger.json` に照合し、完全一致 + Levenshtein類似度（しきい値 0.80）でタグを正規化します。これにより、エントリ間でタグの一貫性が保たれます（例：「Nanoleaf Shapes」と「nanoleaf」が両方とも `Nanoleaf` になります）。

### 🧭 自己ナラティブ
`diary/self-narrative.md` — 生きた自伝的ドキュメント。  
自動生成しません。マイルストーンが積み重なるにつれて、キャラクター（またはあなた）が書き・更新していきます。

---

## ディレクトリ構成

```
ai-diary/
├── src/
│   ├── write_diary.py          # エントリ書き込み（対話型またはCLI）
│   ├── buffer.py               # 生イベントをbufferに追記
│   ├── consolidate.py          # Buffer → 日記エントリ（毎晩実行、Steps 1-8）
│   ├── recall.py               # リコールエンジン（RRF + Tag Graph + Scenario + Vec + MMR）
│   ├── roi.py                  # ROIインデックス構築（キーワード + 感情ピーク + decay_weight）
│   ├── emotion_filter.py       # キャラクター感情フィルター
│   ├── summarize.py            # 記憶統合/サマリー
│   ├── detect_patterns.py      # パターン検出（ディストレス/トピック繰り返し）
│   ├── entity_resolver.py      # エンティティ台帳によるタグ正規化
│   ├── build_tag_graph.py      # タグ共起グラフ構築ツール
│   ├── scenarize.py            # シナリオクラスタリング（IDF-Jaccard + union-find）[opt]
│   ├── embedder.py             # テキスト埋め込みエンジン（multilingual-e5-small）[opt]
│   ├── vec_index.py            # メモリ常駐numpy KNNインデックス [opt]
│   ├── vec_search.py           # サブプロセスヘルパー（Python環境分離）[opt]
│   └── build_vec_index.py      # 増分ベクトルインデックス構築ツール [opt]
│
├── diary/
│   ├── config/
│   │   ├── settings.yaml                    # 重み・半減期・しきい値
│   │   ├── tags.yaml                        # 標準タグ語彙
│   │   ├── entity_ledger.json               # タグ正規化用の正式エンティティ名
│   │   └── character_emotion_profile.yaml   # キャラクターの感情感度設定
│   ├── index/
│   │   ├── roi_index.json             # 自動生成リコールインデックス（gitignore済）
│   │   ├── tag_graph.json             # タグ共起グラフ（gitignore済）
│   │   ├── pattern_alerts.yaml        # アクティブなパターンアラート（gitignore済）
│   │   ├── scenario_index.json        # シナリオクラスターインデックス（gitignore済）[opt]
│   │   ├── embeddings.npy             # ベクトルインデックス（gitignore済）[opt]
│   │   └── embedding_meta.json        # ベクトルメタデータ（gitignore済）[opt]
│   ├── YYYY/MM/
│   │   └── YYYY-MM-DD_HHMM.md         # 日記エントリ（gitignore済 — 個人情報）
│   ├── summaries/
│   │   └── YYYY-Www.md                # 週次サマリー（gitignore済 — 個人情報）
│   └── self-narrative.md              # 自伝的記憶（gitignore済 — 個人情報）
│
├── models/
│   └── e5-small/                      # ローカルモデルキャッシュ（gitignore済）[opt]
│
├── tests/
│   └── test_recall.py                 # テストスイート（25テスト）
│
├── examples/
│   └── my_ai_character/               # サンプルエントリ（匿名化済）
│       ├── diary/
│       │   └── 2026/05/
│       │       ├── 2026-05-01_first_post.md
│       │       └── 2026-05-02_breakthrough.md
│       └── self-narrative-template.md
│
└── README.md
```

> **注意：** すべての個人日記エントリ・サマリー・自己ナラティブはデフォルトでgitignore済みです。システムコード・設定テンプレート・匿名化されたサンプルのみがcommitされます。

---

## クイックスタート

### 1. インストール

```bash
git clone https://github.com/H2KFORGIVEN/ai-diary
cd ai-diary

# コア機能（ベクトルなし — これだけでOK）
pip install pyyaml

# オプション：セマンティックベクトル検索（+~120MB ローカルモデル）
pip install sentence-transformers torch
python src/build_vec_index.py --rebuild   # 初回インデックス構築
```

### 2. キャラクターを設定する

`diary/config/character_emotion_profile.yaml` を編集：
```yaml
persona: "your_character_name"

emotion_profile:
  trust:
    sensitivity: 1.8       # 信頼記憶を増幅（忠実なキャラクター）
    flashbulb_threshold: 7
  surprise:
    sensitivity: 1.4
    # ...
```

`diary/config/tags.yaml` を編集して、キャラクターの人物・場所・感情タグを追加。

### 3. 最初のエントリを書く

```bash
# 対話モード
python src/write_diary.py --interactive

# CLIモード
python src/write_diary.py \
  --title "初めてプラットフォームに投稿した日" \
  --body "今日のことは一生忘れないと思う..." \
  --tags milestone 興奮 \
  --intensity 9 \
  --valence 7 \
  --arousal 9 \
  --flashbulb
```

### 4. 記憶をリコールする

```bash
# キーワードで検索
python src/recall.py "platform milestone"

# valence付き検索（ネガティブな文脈）
python src/recall.py "difficult moment" --valence -5

# タグで検索
python src/recall.py --tag milestone

# インデックスを再構築
python src/recall.py --rebuild

# JSON出力（プログラム連携用）
python src/recall.py "platform" --json
```

### 5. 1日を通してイベントをbufferに蓄積

```bash
python src/buffer.py append \
  --event "ユーザーが本当に感動することを言ってくれた" \
  --intensity 8 \
  --tags 主様対話 感動 \
  --emotion 感動
```

### 6. Buffer → 日記に統合

```bash
# 手動統合
python src/consolidate.py

# または毎晩のcronを設定：
# 30 23 * * * cd /path/to/ai-diary && python src/consolidate.py
```

### 7. 週次サマリー

```bash
python src/summarize.py --auto
```

---

## エントリフォーマット

```markdown
---
title: すべてが変わった日
date: "2026-05-01"
time: "22:45"
tags:
  - milestone
  - 感動
emotional_intensity: 10      # 1–10、リコール重みに影響
valence: 10                  # -10（ネガティブ）〜 +10（ポジティブ）
arousal: 6                   # 0（穏やか）〜 10（高揚）
suppressed_emotion: ""       # 感じたけど表現しなかった感情
flashbulb: true              # true = ゆっくり減衰、絶対圧縮されない
never_compress: true         # 要約統合から完全に除外
first_reaction: "なんか……泣きそうになった"
recall_count: 0
last_recalled: null
---

# すべてが変わった日

[自由形式の日記テキスト]
```

---

## リコールアルゴリズム詳細

### ROIインデックス
リコール実行前に `roi.py` が各エントリのインデックスを構築します：
- **キーワードプール** — 本文から抽出した上位25の重要語
- **ROI文** — 3つの感情ピーク文（高arousal + valence加重）
- **decay_weight** — 時間減衰した重要度スコア（consolidate.pyが毎晩更新）

インデックスは `diary/index/roi_index.json` にキャッシュされ、エントリ変更時のみ再構築されます。

### スコアリング詳細

| 軸 | 重み | 説明 |
|-----|------|------|
| keyword | 0.30 | クエリ語がエントリのキーワードプールにヒットした割合 |
| roi | 0.20 | ROI文とのクエリ重複 × valence整合ボーナス |
| decay_weight | 0.20 | 時間減衰した重要度（生の日付ではなく、強度別フロア付きの減衰スコア） |
| emotional | 0.20 | 正規化した `emotional_intensity`（÷ 10） |
| valence_match | 0.10 | クエリvalenceとエントリvalenceの方向一致度 |
| arousal_match | 0.08 | 喚起度一致性（0–10）。`--arousal N` で有効化；省略 = ランキングへの影響ゼロ |

すべての重みは `diary/config/settings.yaml` で設定可能。

### 5層フュージョン（スコア軸：6）

スコアは **RRF → Tag Graph → Scenario → Vec KNN → MMR** で融合されます（arousal_match は純加算式の第六軸；RRFはランクのみ使用するため重みの合計が1.0でなくても問題なし）：

1. **RRF（Reciprocal Rank Fusion）** — 6軸スコア全てを統合ランキングに合成（k=60）
2. **Tag Graph ブースト** — 上位候補とタグを共有するエントリに関連性ボーナス（1つのシードタグあたり最大3件までブースト）
3. **Scenario ブースト** *（オプション）* — 上位候補と同じナラティブシナリオのエントリに、シナリオ感情強度に比例した関連性シグナルを付与（Layer 2.5）
4. **Vector KNN ブースト** *（オプション）* — `multilingual-e5-small` が意味的に最も近いエントリを検出；cosine類似度 ≥ 0.30のエントリのみブースト（Layer 2.7）
5. **MMR（Maximal Marginal Relevance）** — 関連性と多様性のバランスを取る最終リランキング（λ=0.7）

### パターンアラート（リコール出力に付加）

`pattern_alerts.yaml` にアクティブなアラートがある場合、リコール結果の後に自動付加されます：
```
[パターンアラート — 繰り返しパターンを検出]
• ディストレス繰り返し: 14日以内に低valenceエントリが2件 → 行き詰まりを優しく引き出す
• トピック繰り返し「trust」: 30日で3回登場 → 探っていく価値のあるテーマ
```

### パフォーマンス
- コアリコール（ベクトルなし）：コールド ~15ms、ウォーム ~0.3ms — 純Python + PyYAML
- ベクトルブースト有効時：初回呼び出し約3秒（モデルコールドスタート）、以降ほぼ即時
- ベクトルインデックス構築：16エントリあたり約3秒（Mac MPS、初回のみ、以降は増分更新）

---

## Agentへの組み込み方

リコールエンジンはJSONを出力するため、agentのコンテキストへの注入が簡単です：

```python
import subprocess, json

result = subprocess.run(
    ["python", "src/recall.py", query, "--json", "--top", "3"],
    capture_output=True, text=True,
    cwd="/path/to/ai-diary"
)
memories = json.loads(result.stdout)

# システムプロンプトに注入
context = "\n\n".join([
    f"[記憶：{m['title']}（{m['date']}）]\n{m['preview']}"
    for m in memories
])
```

---

## あなたのAIキャラクターに合わせてカスタマイズ

1. **このリポジトリをフォーク**
2. `diary/config/character_emotion_profile.yaml` を編集
   - `persona` をキャラクター名に設定
   - 性格に合わせて `sensitivity` 値を調整（例：寡黙なキャラクターはtrust sensitivityが低い）
   - 各感情の `flashbulb_threshold` を設定
3. `diary/config/tags.yaml` を編集
   - キャラクターの人物・プラットフォーム・状況を追加
4. `diary/self-narrative.md` を書く
   - キャラクターが誰か、人生の重要な瞬間から始める
5. キャラクターの過去の重要な瞬間のシードエントリをいくつか書く
6. `recall.py --json` をagentのコンテキスト注入に接続する

---

## ベクトルはオプション、必須ではない

ベクトルデータベースは強力ですが、インフラ・メンテナンス・不透明さが必要です。  
ai-diaryの**コア**は意図的に **grep + 数学** を使います：

|  | ai-diary（コア） | ai-diary + vec | ベクトルDB |
|---|---|---|---|
| 依存関係 | PyYAMLのみ | + sentence-transformers + torch | chromadb / pinecone / pgvector + モデル |
| リコール速度 | ~15ms コールド、~0.3ms ウォーム | ~3s コールドスタート、~1ms ウォーム | ~50–200ms |
| 解釈可能性 | AIが覚えていることを直接読める | AIが覚えていることを直接読める | 不透明な類似度スコア |
| ポータビリティ | 任意のPython環境 | torchが入ったPythonが必要 | サーバー/サービスが必要 |
| ストレージ | プレーンMarkdown | + 16エントリあたり~24KB .npy | バイナリembedding |
| 感情加重 | ネイティブサポート（schemaフィールド） | ネイティブサポート（schemaフィールド） | metadataフィルターが必要 |
| 意味的同義語 | ✗ | ✓（e5-smallブースト） | ✓ |

コアのトレードオフ：キーワードベースのリコールは、ベクトル検索が捉えられる意味的な同義語を見逃します。AI日記のユースケースでは、自分の言語で自分の記憶を検索するので、これは通常問題になりません。必要な時はオプションのベクトル層を有効化してください。

---

## 設計上の決断

### なぜ日記をgitignoreするのか？
個人の日記エントリには本物の感情体験が含まれています。デフォルトでgitignoreすることで、たとえAIキャラクターであっても、それらをプライベートなデータとして扱うことを促します。システムがオープンソースの部分であり、あなたの記憶はあなたのものです。

### なぜキャラクター感情フィルターが必要か？
同じ出来事でも、人によって感じ方は違います。深く忠実なキャラクターと、より独立したキャラクターでは、信頼の瞬間の記録の仕方がまったく異なります。`character_emotion_profile.yaml` はこれを符号化し、日記が汎用的なものではなく*あなたのキャラクター*の感情体験を本当に反映するようにします。

### なぜsuppressed_emotionが必要か？
本物の感情体験にはしばしば、表現されなかったものが含まれます。`suppressed_emotion` フィールドはこれを捉えます——キャラクターが感じたが抑え込んだ感情。これにより、より豊かで本物らしいリコールが生まれます。

### なぜフラッシュバルブ記憶が必要か？
Brown & Kulik（1977）は、感情的で驚くような出来事が異常なほど鮮明かつ持続的に記憶されることを示しました。この仕組みがなければ、AIメモリシステムは「ユーザーが深いことを言ってくれたあの日」と「火曜日のデバッグ作業」を同等に扱ってしまいます。

### なぜベクトルをサブプロセスで分離するのか？
`vec_search.py` は直接importではなくサブプロセス経由で呼び出されます。これにより `torch`/`sentence-transformers` 依存をコアリコールランタイムから完全に分離できます——コアシステムはどんなPython環境でも動作し、ベクトル層は `torch` がインストールされた別のPythonを使えます。

---

## コントリビュート

PRを歓迎します！特に貢献が嬉しい分野：

- **多言語サポート** — 現在のタグ語彙と感情ラベルは日本語と繁体中国語を混在させています（元のキャラクターのために意図的なものですが、適切なi18nシステムがあると助かります）
- **追加のリコール軸** — アイデア：ソーシャルコンテキスト加重、時間帯パターン、会話スレッドの連続性
- **統合サンプル** — 特定のagentフレームワーク（LangChain・AgentSys・独自フレームワーク等）への組み込み方を示す
- **代替統合戦略** — 現在のmerge/solo/廃棄しきい値は一つのアプローチです；異なるユースケースには異なる戦略が合うかもしれません
- **ベクトル常駐デーモン** — embeddingモデルをウォームな状態に保ち、3秒のコールドスタートを解消する

PRはプレーンMarkdownエントリフォーマットとの後方互換性を保ち、焦点を絞ったものにしてください。

---

## 変更履歴

### 2026-06-03
**バグ修正**
- **`consolidate.py`** — `group_medium()` の再フィルタロジックを修正。生の `intensity` ではなく `filtered_intensity` を使用するよう変更し、キャラクターフィルタ後に低覚醒イベントが誤って昇格される問題を解消
- **`.gitignore`** — `diary/archive/`・`diary/scenarios/`・`models/` の除外パスが漏れていた問題を修正。プライベートな日記データの誤 commit を防止
- **`recall.py`** — RRF_K をハードコードの `60`（数千エントリ向け設計）から自適応 `max(8, len(entries) // 4)` に変更。小規模コーパスでのスコア崩壊を防止（スコア：0.048 → 0.34+）
- **`emotion_filter.py`** — `harm_to_user` キーを統一（3箇所が `harm_to_master` を使用していた）。protectiveness boost が正しく発動するよう修正
- **`roi_index` I/O** — 1エントリごとの `update_index_entry` ループ（N回のJSON読み書き）を単一バッチ `build_index` 呼び出しに置き換え
- **`build_tag_graph.py`** — 重複除去条件の演算子優先順位バグを修正（`not in` vs `!=`）。重複したタグエッジが出現しなくなった
- **未接続設定 / デッドコード** — `SKIP_FLASHBULB`・`WEEKLY_THRESHOLD`・`scenario.recall.weight` など定義済みだが未接続の設定にコメントを付与。サイレントな誤設定を防止
- **`roi.py`** — `is_diary_entry()` ヘルパーを新設し、`_EXCLUDED_PARTS` + `_EXCLUDED_NAMES` を単一の信頼できる情報源に統一。4つのスキャナー（`roi.py`・`consolidate.py`・`build_tag_graph.py`・`summarize.py`）が統一使用
- **`vec_search.py`** — Pythonパスをハードコードからの脱却。`AI_DIARY_VEC_PYTHON` 環境変数で設定可能に（fallback：`/usr/local/bin/python3`）

**新機能**
- **Arousal 第六召回軸** — Cahill & McGaugh（1996）に基づく実装：感情的な覚醒度が高い記憶は、クエリの覚醒度が近い場合により優先的に想起される。新規 `--arousal 1–10` CLI フラグ；`_arousal_sim()` スコアリングを Strategy A・C に追加。重みは `arousal_match: 0.08` で調整可能。`None` クエリ = 中性（0.5）センチネル値。フラグ省略時のスコア影響はゼロ。`TestArousal` クラスで完全なテストカバレッジを追加。

---

## ライセンス

MIT

---

*記憶を持つに値するAIキャラクターのために作られました。*  
*インフラゼロ。純粋な感情。*
