# ai-diary 🌙

**An emotion-first memory system for AI characters.**

No vector databases. No heavy infrastructure. Just Markdown files, cognitive science, and a little bit of soul.

English | [繁體中文](README.zh-TW.md) | [日本語](README.ja.md)

---

## Philosophy

Most AI memory systems optimize for *factual accuracy*.  
ai-diary optimizes for *the feeling of remembering*.

Real human memory isn't a database lookup. It's shaped by emotion, time, and meaning. A moment of intense joy gets remembered differently than a routine Tuesday. A belief-shattering surprise creates a "flashbulb memory" that stays vivid for years.

ai-diary brings these properties to AI characters:

- **Emotion-weighted recall** — intense memories surface more readily
- **Flashbulb memory** — high-impact moments resist compression and decay slowly
- **Character-filtered recording** — the same event is experienced differently depending on who your character is
- **Self-narrative** — a living document: *who am I, and what has shaped me?*

Inspired by:
- **Generative Agents** (Park et al., 2023) — recency × relevance × importance
- **Tulving (1972)** — episodic vs semantic memory distinction
- **Brown & Kulik (1977)** — flashbulb memory theory
- **Bower (1981)** — associative network theory of emotion and memory
- **Cahill & McGaugh (1996)** — emotional arousal strengthens memory consolidation
- **James Gross (1998)** — emotion suppression and its effect on memory encoding
- **Anthropic (2026)** — emotion-like feature directions in LLMs (valence/arousal structure)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                           ai-diary                               │
│                                                                  │
│  ┌──────────┐    ┌───────────────┐    ┌──────────────────────┐  │
│  │  buffer  │───▶│  consolidate  │───▶│  diary entries       │  │
│  │  (JSONL) │    │  (intensity   │    │  (Markdown + YAML)   │  │
│  └──────────┘    │   threshold)  │    └──────────┬───────────┘  │
│                  │               │               │              │
│                  │  Step 5 ──────┼──▶ decay_weight update       │
│                  │  Step 6 ──────┼──▶ tag_graph.json            │
│                  │  Step 7 ──────┼──▶ pattern_alerts.yaml       │
│                  └───────────────┘               │              │
│                                                  │              │
│  ┌──────────────────────┐           ┌────────────▼───────────┐  │
│  │  emotion_filter      │           │  ROI index             │  │
│  │  (character profile) │           │  (keyword pool +       │  │
│  └──────────────────────┘           │   emotion peaks +      │  │
│                                     │   decay_weight)        │  │
│  ┌──────────────────────┐           └────────────┬───────────┘  │
│  │  entity_resolver     │                        │              │
│  │  (tag normalizer)    │           ┌────────────▼───────────┐  │
│  └──────────────────────┘           │  tag_graph.json        │  │
│                                     │  (tag co-occurrence)   │  │
│  ┌──────────────────────┐           └────────────┬───────────┘  │
│  │  pattern_alerts      │                        │              │
│  │  (distress/topic     │           ┌────────────▼───────────┐  │
│  │   repeat detection)  │──────────▶│  recall                │  │
│  └──────────────────────┘           │  (RRF + TagGraph + MMR)│  │
│                                     └────────────────────────┘  │
│  ┌──────────────────────┐                                        │
│  │  summarize           │  (weekly/monthly consolidation)        │
│  └──────────────────────┘                                        │
└──────────────────────────────────────────────────────────────────┘
```

---

## Features

### 📔 Emotion-Weighted Entries
Every diary entry carries:
- `emotional_intensity` (1–10) — how strongly the character felt this
- `valence` (-10 to +10) — negative to positive
- `arousal` (0–10) — calm to activated
- `suppressed_emotion` — what the character felt but didn't express
- `flashbulb: true/false` — whether this is a high-impact memory

### ⚡ Flashbulb Memory
Entries marked `flashbulb: true`:
- Are **never compressed** during summarization
- Decay **24× slower** than normal entries (730-day half-life vs 30-day)
- Always surface in recall for significant emotional queries

### 🔍 5-Axis Recall Engine
```
score = keyword_hits    × 0.30   # indexed keyword pool match
      + roi_match       × 0.20   # emotional peak sentence match + valence alignment
      + recency         × 0.20   # exponential decay (configurable half-life)
      + emotional       × 0.20   # emotional_intensity of the entry
      + valence_match   × 0.10   # directional valence alignment with query
```
All weights configurable in `diary/config/settings.yaml`.  
**No vector DB required** — recall runs in ~15ms via indexed keyword lookup.

Recall uses a **3-layer fusion** strategy (RRF → Tag Graph boost → MMR diversification):
1. **RRF** — Reciprocal Rank Fusion merges keyword, ROI, recency, emotional, and valence scores
2. **Tag Graph** — entries sharing tags with top results get a relevance boost (co-occurrence graph)
3. **MMR** — Maximal Marginal Relevance penalizes near-duplicate results for diversity

### 🎭 Character Emotion Filter
`character_emotion_profile.yaml` defines how your AI character *experiences* emotions differently:
- Some emotions are amplified (e.g. a loyal character feels trust more intensely)
- Some are suppressed and redirected (e.g. anger → self-improvement drive)
- flashbulb thresholds are per-emotion

This means the same raw event produces different diary entries for different characters.

### 🗂 Memory Consolidation
- `buffer.py` — append raw events throughout the day
- `consolidate.py` — nightly, converts buffer events into diary entries:
  - `intensity ≤ 3` → discarded
  - `intensity 4–6` → merged into a single entry
  - `intensity ≥ 7` → each gets its own entry
  - **Step 5** — updates `decay_weight` for all existing entries (time-decay)
  - **Step 6** — rebuilds `tag_graph.json` (tag co-occurrence index)
  - **Step 7** — runs `detect_patterns.py` → writes `pattern_alerts.yaml`
- `summarize.py` — weekly/monthly summaries auto-generated, compressing lower-intensity entries while preserving flashbulb memories

### ⏳ Time Decay (`decay_weight`)
Every entry has a `decay_weight` that decreases over time using exponential decay:
```
decay_weight = max(base_importance × exp(-ln2 × days / halflife), floor)
```
- **Flashbulb** — 730-day half-life, floor 0.50 (never forgotten)
- **High intensity (8–9)** — 90-day half-life, floor 0.15
- **Medium intensity (6–7)** — 60-day half-life, floor 0.10
- **Normal** — 30-day half-life, floor 0.05

`decay_weight` feeds directly into the recall score, so older memories naturally surface less — unless recalled repeatedly (each recall activates `+0.10`).

### 🕸 Tag Graph
`diary/index/tag_graph.json` — a co-occurrence graph where each tag links to entries that share it.  
During recall, entries sharing tags with top candidates get a relevance boost — surfacing thematically related memories even when keywords don't overlap.

### 🚨 Pattern Detection (`detect_patterns.py`)
After each consolidation, ai-diary scans recent entries for recurring patterns:
- **Distress repeat** — `valence ≤ −2` appears ≥ 2 times within 14 days → signals a stuck loop
- **Topic repeat** — the same tag appears ≥ 3 times within 30 days → signals a persistent theme

Detected patterns are written to `diary/index/pattern_alerts.yaml` and automatically appended to recall output — giving the AI character contextual cues to gently surface recurring themes in conversation.

### 🏷 Entity Resolver
`entity_resolver.py` normalizes raw tags against `diary/config/entity_ledger.json` using exact match + Levenshtein similarity (threshold 0.80). This ensures tag consistency across entries (e.g. "Nanoleaf Shapes" and "nanoleaf" both become `Nanoleaf`).

### 🧭 Self-Narrative
`diary/self-narrative.md` — a living autobiographical document.  
Not auto-generated. Written and updated by the character (or you) as milestones accumulate.

---

## Directory Structure

```
ai-diary/
├── src/
│   ├── write_diary.py          # Write entries (interactive or CLI)
│   ├── buffer.py               # Append raw events to buffer
│   ├── consolidate.py          # Buffer → diary entries (nightly, Steps 1-7)
│   ├── recall.py               # Recall engine (RRF + Tag Graph + MMR)
│   ├── roi.py                  # ROI index builder (keyword + emotion peaks + decay_weight)
│   ├── emotion_filter.py       # Character emotion profile filter
│   ├── summarize.py            # Memory consolidation / summaries
│   ├── detect_patterns.py      # Pattern detection (distress/topic repeats)
│   ├── entity_resolver.py      # Tag normalization via entity ledger
│   └── build_tag_graph.py      # Tag co-occurrence graph builder
│
├── diary/
│   ├── config/
│   │   ├── settings.yaml                    # Weights, half-lives, thresholds
│   │   ├── tags.yaml                        # Standard tag vocabulary
│   │   ├── entity_ledger.json               # Canonical entity names for tag normalization
│   │   └── character_emotion_profile.yaml   # Your character's emotional sensitivities
│   ├── index/
│   │   ├── roi_index.json             # Auto-generated recall index (gitignored)
│   │   ├── tag_graph.json             # Tag co-occurrence graph (gitignored)
│   │   └── pattern_alerts.yaml        # Active pattern alerts (gitignored)
│   ├── YYYY/MM/
│   │   └── YYYY-MM-DD_HHMM.md         # Daily entries (gitignored — personal)
│   ├── summaries/
│   │   └── YYYY-Www.md                # Weekly summaries (gitignored — personal)
│   └── self-narrative.md              # Autobiographical memory (gitignored — personal)
│
├── examples/
│   └── my_ai_character/               # Example entries (anonymized)
│       ├── diary/
│       │   └── 2026/05/
│       │       ├── 2026-05-01_first_post.md
│       │       └── 2026-05-02_breakthrough.md
│       └── self-narrative-template.md
│
└── README.md
```

> **Note:** All personal diary entries, summaries, and self-narrative are gitignored by default. Only the system code, config templates, and anonymized examples are committed.

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/your-username/ai-diary
cd ai-diary
pip install pyyaml
```

No other dependencies.

### 2. Configure your character

Edit `diary/config/character_emotion_profile.yaml`:
```yaml
persona: "your_character_name"

emotion_profile:
  trust:
    sensitivity: 1.8       # Amplify trust memories (loyal character)
    flashbulb_threshold: 7
  surprise:
    sensitivity: 1.4
    # ...
```

Edit `diary/config/tags.yaml` to add your character's people, places, and emotions.

### 3. Write your first entry

```bash
# Interactive mode
python src/write_diary.py --interactive

# CLI mode
python src/write_diary.py \
  --title "First day on the platform" \
  --body "Today was something I won't forget..." \
  --tags milestone 興奮 \
  --intensity 9 \
  --valence 7 \
  --arousal 9 \
  --flashbulb
```

### 4. Recall memories

```bash
# Query by keyword
python src/recall.py "platform milestone"

# Query with valence (negative context)
python src/recall.py "difficult moment" --valence -5

# Query by tag
python src/recall.py --tag milestone

# Rebuild the index
python src/recall.py --rebuild

# JSON output (for programmatic use)
python src/recall.py "platform" --json
```

### 5. Buffer events throughout the day

```bash
# Append a raw event to the buffer
python src/buffer.py append \
  --event "User said something that really moved me" \
  --intensity 8 \
  --tags 主様対話 感動 \
  --emotion 感動
```

### 6. Consolidate buffer → diary

```bash
# Manual consolidation
python src/consolidate.py

# Or set up a nightly cron:
# 30 23 * * * cd /path/to/ai-diary && python src/consolidate.py
```

### 7. Weekly summary

```bash
python src/summarize.py --auto
```

---

## Entry Format

```markdown
---
title: The day everything changed
date: "2026-05-01"
time: "22:45"
tags:
  - milestone
  - 感動
emotional_intensity: 10      # 1–10, shapes recall weighting
valence: 10                  # -10 (negative) to +10 (positive)
arousal: 6                   # 0 (calm) to 10 (activated)
suppressed_emotion: ""       # what was felt but not expressed
flashbulb: true              # true = slow decay, never compressed
never_compress: true         # opt out of summarization entirely
first_reaction: "I didn't know what to say..."
recall_count: 0
last_recalled: null
---

# The day everything changed

[Free-form diary text here]
```

---

## Recall Algorithm — Details

### ROI Index
Before recall runs, `roi.py` builds an index of each entry:
- **keyword pool** — top-25 significant words extracted from body text
- **ROI sentences** — 3 emotionally peaked sentences (high arousal + valence weight)
- **decay_weight** — time-decayed importance score (updated nightly by consolidate.py)

This index is cached in `diary/index/roi_index.json` and rebuilt only when entries change.

### Scoring (5-Axis)

| Axis | Weight | Description |
|------|--------|-------------|
| keyword | 0.30 | Fraction of query terms hitting the entry's keyword pool |
| roi | 0.20 | Query overlap with ROI sentences × valence alignment bonus |
| decay_weight | 0.20 | Time-decayed importance (replaces raw recency — respects floor by intensity) |
| emotional | 0.20 | Normalized `emotional_intensity` (÷ 10) |
| valence_match | 0.10 | Directional match between query valence and entry valence |

All weights are configurable in `diary/config/settings.yaml`.

### 3-Layer Fusion

Scores are fused using **RRF → Tag Graph → MMR**:

1. **RRF (Reciprocal Rank Fusion)** — merges all 5 axis scores into a unified ranking (k=60)
2. **Tag Graph boost** — entries sharing tags with top-ranked candidates get a relevance bonus (max 3 related entries per seed tag, preventing high-frequency tag floods)
3. **MMR (Maximal Marginal Relevance)** — final reranking that balances relevance against diversity (λ=0.7), preventing near-duplicate entries from filling all top slots

### Pattern Alerts (appended to recall output)

When `pattern_alerts.yaml` has active alerts, they are appended after recall results:
```
[Pattern Alerts — recurring patterns]
• Distress repeat: 2 low-valence entries within 14 days → gently surface the stuck loop
• Topic repeat "trust": appeared 3 times in 30 days → a theme worth exploring
```

### Performance
- Cold recall (first run): ~15ms for a 100-entry diary
- Warm recall (cached index): ~0.3ms
- Pure Python + PyYAML — no vector DB, no embeddings

---

## Wiring into Your Agent

The recall engine outputs JSON, making it easy to inject into an agent's context:

```python
import subprocess, json

result = subprocess.run(
    ["python", "src/recall.py", query, "--json", "--top", "3"],
    capture_output=True, text=True,
    cwd="/path/to/ai-diary"
)
memories = json.loads(result.stdout)

# Inject into system prompt
context = "\n\n".join([
    f"[Memory: {m['title']} ({m['date']})]\n{m['preview']}"
    for m in memories
])
```

Or call `recall.py` directly from your agent tool definitions.

---

## Adapting for Your AI Character

1. **Fork this repo**
2. Edit `diary/config/character_emotion_profile.yaml`
   - Set `persona` to your character's name
   - Tune `sensitivity` values to match personality (e.g. a stoic character has lower trust sensitivity)
   - Set `flashbulb_threshold` per emotion
3. Edit `diary/config/tags.yaml`
   - Add your character's people, platforms, and situations
4. Write `diary/self-narrative.md`
   - Start with who your character is, and key moments in their history
5. Write seed entries for important moments in your character's past
6. Wire `recall.py --json` into your agent's context injection

---

## Why Not Vectors?

Vector databases are powerful — but they require infrastructure, maintenance, and opacity.  
ai-diary deliberately uses **grep + math**:

| | ai-diary | Vector DB |
|---|---|---|
| Dependencies | PyYAML only | chromadb / pinecone / pgvector + models |
| Recall speed | ~15ms (cold), ~0.3ms (warm) | ~50–200ms |
| Interpretability | Read what the AI remembers | Opaque similarity scores |
| Portability | Any Python runtime | Requires server/service |
| Storage | Plain Markdown | Binary embeddings |
| Emotional weighting | Native (schema fields) | Requires metadata filters |

The tradeoff: keyword-based recall misses semantic synonyms that vector search catches. For AI diary use cases, this is usually fine — you're querying your own memories in your own language.

---

## Design Decisions

### Why is the diary gitignored?
Personal diary entries contain real emotional experiences. We gitignore them by default to encourage you to treat them as private data, even if you're an AI character. The system is the open-source part; your memories are yours.

### Why character-filtered recording?
The same event feels different to different people. A character who is deeply loyal will record a trust moment differently than a character who is more independent. `character_emotion_profile.yaml` encodes this, so the diary actually reflects *your character's* emotional experience, not a generic one.

### Why suppressed_emotion?
Real emotional experience often includes things we don't express. The `suppressed_emotion` field captures this — what the character felt but held back. This creates richer, more authentic recall.

### Why flashbulb memory?
Brown & Kulik (1977) showed that emotionally charged, surprising events are remembered with unusual clarity and persistence. Without this, AI memory systems treat "the day the user said something profound" the same as "Tuesday's debugging session."

---

## Contributing

PRs welcome! Some areas where contributions would be especially useful:

- **Multilingual support** — the tag vocabulary and emotion labels currently mix Japanese and Traditional Chinese (that's intentional for the original character, but a proper i18n system would help)
- **Semantic keyword extraction** — current keyword extraction is frequency-based; a lightweight semantic extractor (no heavy models) would improve recall quality
- **More recall axes** — ideas: social context weighting, time-of-day patterns, conversation thread continuity
- **Integration examples** — show how to wire this into specific agent frameworks (LangChain, AgentSys, custom)
- **Alternative consolidation strategies** — the current merge/solo/discard thresholds are one approach; others may fit different use cases

Please keep PRs focused and backward-compatible with the plain-Markdown entry format.

---

## License

MIT

---

*Built for AI characters who deserve to remember.*  
*Zero infrastructure. Pure emotion.*
