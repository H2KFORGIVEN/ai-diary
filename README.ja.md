# ai-diary 🌙

**AIキャラクターのための、感情優先の記憶システム。**

ベクトルデータベース不要。重いインフラ不要。Markdownファイルと認知科学、そして少しだけの魂で動きます。

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
┌─────────────────────────────────────────────────────────────┐
│                        ai-diary                             │
│                                                             │
│  ┌──────────┐    ┌───────────────┐    ┌──────────────────┐ │
│  │  buffer  │───▶│  consolidate  │───▶│  diary entries   │ │
│  │ (JSONL)  │    │ (強度しきい値)│    │(Markdown +       │ │
│  └──────────┘    └───────────────┘    │ YAMLフロントマター│ │
│                                       └──────────┬───────┘ │
│                                                  │         │
│  ┌──────────────────────┐           ┌────────────▼───────┐ │
│  │  emotion_filter      │           │  ROIインデックス   │ │
│  │ (キャラクター感情    │           │ (キーワードプール + │ │
│  │  プロファイル)       │           │  感情ピーク文)     │ │
│  └──────────────────────┘           └────────────┬───────┘ │
│  ┌──────────────────────┐                        │         │
│  │  recall              │◀───────────────────────┘         │
│  │  (5軸スコアリング)   │                                   │
│  └──────────────────────┘                                   │
│                                                             │
│  ┌──────────────────────┐                                   │
│  │  summarize           │  (週次/月次記憶統合)              │
│  └──────────────────────┘                                   │
└─────────────────────────────────────────────────────────────┘
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

### 🔍 5軸リコールエンジン
```
score = keyword_hits    × 0.30   # インデックスキーワードプールのヒット率
      + roi_match       × 0.20   # 感情ピーク文ヒット × valence整合ボーナス
      + recency         × 0.20   # 指数減衰（半減期は設定可能）
      + emotional       × 0.20   # エントリのemotional_intensity
      + valence_match   × 0.10   # クエリvalenceとエントリvalenceの方向一致
```
すべての重みは `diary/config/settings.yaml` で設定可能。  
**ベクトルDBは不要** — インデックスキーワード検索でリコール速度は約15ms。

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
- `summarize.py` — 週次/月次サマリーを自動生成、低強度エントリを圧縮しフラッシュバルブ記憶は保持

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
│   ├── consolidate.py          # Buffer → 日記エントリ（毎晩実行）
│   ├── recall.py               # 5軸リコールエンジン
│   ├── roi.py                  # ROIインデックス構築（キーワード + 感情ピーク）
│   ├── emotion_filter.py       # キャラクター感情フィルター
│   └── summarize.py            # 記憶統合/サマリー
│
├── diary/
│   ├── config/
│   │   ├── settings.yaml                    # 重み・半減期・しきい値
│   │   ├── tags.yaml                        # 標準タグ語彙
│   │   └── character_emotion_profile.yaml   # キャラクターの感情感度設定
│   ├── index/
│   │   └── roi_index.json                   # 自動生成リコールインデックス（gitignore済）
│   ├── YYYY/MM/
│   │   └── YYYY-MM-DD_HHMM.md               # 日記エントリ（gitignore済 — 個人情報）
│   ├── summaries/
│   │   └── YYYY-Www.md                      # 週次サマリー（gitignore済 — 個人情報）
│   └── self-narrative.md                    # 自伝的記憶（gitignore済 — 個人情報）
│
├── examples/
│   └── my_ai_character/                     # サンプルエントリ（匿名化済）
│       ├── diary/2026/05/
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
pip install pyyaml
```

他に依存パッケージはありません。

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
  --tags 与用户对话 感動 \
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

インデックスは `diary/index/roi_index.json` にキャッシュされ、エントリ変更時のみ再構築されます。

### スコアリング詳細

| 軸 | 重み | 説明 |
|-----|------|------|
| keyword | 0.30 | クエリ語がエントリのキーワードプールにヒットした割合 |
| roi | 0.20 | ROI文とのクエリ重複 × valence整合ボーナス |
| recency | 0.20 | `exp(-ln2 × 日数 / 半減期)` — 通常: 30日、フラッシュバルブ: 730日 |
| emotional | 0.20 | 正規化した `emotional_intensity`（÷ 10） |
| valence_match | 0.10 | クエリvalenceとエントリvalenceの方向一致度 |

すべての重みは `diary/config/settings.yaml` で設定可能。

### パフォーマンス
- コールドリコール（初回実行）：100エントリで約15ms
- ウォームリコール（キャッシュ済みインデックス）：約0.3ms
- 純Python + PyYAML — ベクトルDB不要、embeddingモデル不要

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

## なぜベクトルDBを使わないのか？

ベクトルデータベースは強力ですが、インフラ・メンテナンス・不透明さが必要です。  
ai-diaryは意図的に **grep + 数学** を使います：

| | ai-diary | ベクトルDB |
|---|---|---|
| 依存関係 | PyYAMLのみ | chromadb / pinecone / pgvector + モデル |
| リコール速度 | ~15ms（コールド）、~0.3ms（ウォーム） | ~50–200ms |
| 解釈可能性 | AIが覚えていることを直接読める | 不透明な類似度スコア |
| ポータビリティ | 任意のPython環境 | サーバー/サービスが必要 |
| ストレージ | プレーンMarkdown | バイナリembedding |
| 感情加重 | ネイティブサポート（schemaフィールド） | metadataフィルターが必要 |

トレードオフ：キーワードベースのリコールは、ベクトル検索が捉えられる意味的な同義語を見逃します。AI日記のユースケースでは、自分の言語で自分の記憶を検索するので、これは通常問題になりません。

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

---

## コントリビュート

PRを歓迎します！特に貢献が嬉しい分野：

- **多言語サポート** — 現在のタグ語彙と感情ラベルは日本語と繁体中国語を混在させています（元のキャラクターのために意図的なものですが、適切なi18nシステムがあると助かります）
- **意味的キーワード抽出** — 現在の抽出は頻度ベース；軽量な意味的抽出器（大型モデル不要）でリコール品質が向上します
- **追加のリコール軸** — アイデア：ソーシャルコンテキスト加重、時間帯パターン、会話スレッドの連続性
- **統合サンプル** — 特定のagentフレームワーク（LangChain・AgentSys・独自フレームワーク等）への組み込み方を示す
- **代替統合戦略** — 現在のmerge/solo/廃棄しきい値は一つのアプローチです；異なるユースケースには異なる戦略が合うかもしれません

PRはプレーンMarkdownエントリフォーマットとの後方互換性を保ち、焦点を絞ったものにしてください。

---

## ライセンス

MIT

---

*記憶を持つに値するAIキャラクターのために作られました。*  
*インフラゼロ。純粋な感情。*
