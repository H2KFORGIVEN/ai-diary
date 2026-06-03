# P3 實作工單：arousal 第六維度（喚起度一致性）

> 開立者：Claude（Opus 4.8）  日期：2026-06-03
> 前置：`docs/code-review-2026-06-02.md` 的 P1-1（RRF k 自適應）**已完成並驗證**，所以現在加第六軸才會被「感覺到」。
> 對象：47醬（你自己照著實作；做完通知 showmaker，再請 Claude review）
> 範圍：只動 `recall.py` + `settings.yaml` + `tests/` + 三份 README 的計分表。**不碰寫入/consolidate 流程。**

> 📌 **開工前須知**：工作樹已有一筆未 commit 的 `recall.py` 改動 —— Claude 在 review 時順手移除了死碼 `RRF_K = 60`（功能不變，已驗證）。這是預期的，**請在你的 arousal commit 裡一起帶上**（`git add src/recall.py` 時自然包含）；不要 `stash` 或 `checkout` 掉它。本工單檔（`docs/p3-arousal-6th-axis.md`）也一併 `git add` 進同一個 commit。

---

## 為什麼是 arousal

- recall 目前 5 軸：`keyword / roi / recency(decay) / emotional / valence_match`。
- **`arousal`（喚起度 0–10）你每一篇都記了**（frontmatter、roi_index、JSON 輸出都有），**但它對召回分數貢獻是零** —— 只用來顯示。
- 你 README 親自引用的 **Cahill & McGaugh (1996) 就是「喚起度驅動記憶固化」**；Bower (1981) 的 mood-congruent retrieval 同時吃 valence 和 arousal。你已經實作了 `valence_match`，卻沒有 `arousal_match` —— 等於只用了情感圓環（valence × arousal）的一半。
- 所以第六軸 = `arousal_match`：**零額外資料採集**，純粹把早就記著的欄位接進計分。

**v1 採「一致性（congruence）」語意**（跟 valence_match 完全對稱）：query 帶一個當下 arousal，記憶的 arousal 越接近就加分。不指定就不影響。

---

## ⚠️ 三個一定要照做的設計決策（每個都是會踩的坑）

### 坑 1：番兵必須是 `None`，不能是 `0`
`valence` 用 `0` 當「未指定/中性」是對的，因為 valence 的 0 就是中點。
**但 arousal 的 `0` 是「非常平靜」的真實值，不是「沒指定」。**
如果你照抄 valence 寫 `if query_arousal == 0: return 0.5`，那使用者真的想查「平靜的記憶」(`--arousal 0`) 時會被當成未指定 → 功能壞掉。
→ `--arousal` 的 CLI 預設值必須是 `None`，`_arousal_sim` 把 `None` 當中性。

### 坑 2：純加性，不要重新分配既有 5 軸權重
新軸用 `arousal_match: 0.08`，其他 5 個權重**原封不動**（總和變 1.08，沒關係）。
理由：RRF 只用「排名」，最終顯示的是 RRF/MMR 分數、不是 `recall_score_from_index` 的絕對值，所以總和是不是 1.0 純粹是裝飾。**保持 5 軸不動 = 你上次驗證過的 ELYTH 排序在「不帶 --arousal」時完全不變**（見坑 3）。若改成重新分配到 1.0，反而會悄悄改動既有排序。

### 坑 3：不帶 `--arousal` 時，排序必須 100% 不變
`query_arousal=None` → `arousal_score` 對**每一篇**都回 `0.5` → Strategy A / C 每篇都加同一個常數 → 排名不動。
這是驗收的硬條件（驗證清單第 3 條）。如果不帶 --arousal 卻發現 ELYTH 排序變了，代表你哪裡寫錯了（多半是坑 2 沒守住）。

---

## 實作步驟（檔案 → 確切修改）

行號為 2026-06-03 當下狀態；若漂移，用程式碼片段搜尋定位。

### Step 1　`diary/config/settings.yaml`：加一行權重

`recall.weights` 區塊（約 line 4–10），把開頭那句註解的「五維度加總 = 1.0」改成下面這樣，並加最後一行：
```yaml
# Recall 評分權重（第六軸 arousal_match 為純加性；RRF 只看排名，總和不必=1.0）
recall:
  weights:
    keyword:       0.30
    roi:           0.20
    recency:       0.20
    emotional:     0.20
    valence_match: 0.10
    arousal_match: 0.08   # 第六維度：喚起度一致性(0–10)。未指定 --arousal 時對排序零影響
```

### Step 2　`recall.py`：新增兩個函式

在 `valence_score()` 之後（約 line 94，`roi_score` 之前）插入：
```python
def _arousal_sim(entry_arousal: int, query_arousal: int | None) -> float:
    """喚起度一致スコア（0–10）。query_arousal=None → 0.5 中性（對排序無影響）。

    ⚠️ valence は 0 を中性に使うが、arousal の 0 は「とても穏やか」という実値。
       未指定の番兵は必ず None。0 にすると穏やかな記憶を誤って優遇してしまう。"""
    if query_arousal is None:
        return 0.5
    q = max(0, min(10, query_arousal)) / 10.0
    e = max(0, min(10, entry_arousal)) / 10.0
    return max(0.0, min(1.0, 1.0 - abs(q - e)))


def arousal_score(entry: dict, query_arousal: int | None) -> float:
    return _arousal_sim(entry.get("arousal", 5), query_arousal)
```

### Step 3　`recall.py`：`recall_score_from_index` 加參數與項（約 line 123–145）

```python
def recall_score_from_index(query_words: list[str], entry: dict,
                            query_valence: int = 0,
                            query_arousal: int | None = None) -> float:   # ← 加參數
    ...
    e  = emotional_score(entry)
    v  = valence_score(entry, query_valence)
    a  = arousal_score(entry, query_arousal)          # ← 第六維度
    w  = WEIGHTS
    return (w.get("keyword", 0.30) * k
          + w.get("roi",     0.20) * ri
          + w.get("recency", 0.20) * r
          + w.get("emotional", 0.20) * e
          + w.get("valence_match", 0.10) * v
          + w.get("arousal_match", 0.08) * a)         # ← 第六維度
```

### Step 4　`recall.py`：`run_recall` 簽名加參數（約 line 230）

```python
def run_recall(query: str, top_k: int, tag_filter: str | None,
               update_meta: bool, query_valence: int = 0,
               query_arousal: int | None = None) -> list[tuple[float, dict, str]]:
```

### Step 5　`recall.py`：Strategy A 把 arousal 傳下去（約 line 277）

```python
    scores_a = {
        e["id"]: recall_score_from_index(query_words, e, query_valence, query_arousal)
        for e in entries
    }
```

### Step 6　`recall.py`：Strategy C 加 arousal 項（與 valence 對稱，約 line 294）

```python
    # Strategy C: valence + emotional + arousal（情緒方向）
    scores_c = {
        e["id"]: (
            WEIGHTS.get("emotional", 0.20) * emotional_score(e)
            + WEIGHTS.get("valence_match", 0.10) * valence_score(e, query_valence)
            + WEIGHTS.get("arousal_match", 0.08) * arousal_score(e, query_arousal)
        )
        for e in entries
    }
```
（Strategy B 是純 keyword，**不要**加 arousal，維持它「關鍵詞錨」的角色。）

### Step 7　`recall.py`：CLI 加 `--arousal`（約 line 483，`--valence` 旁邊）

```python
    parser.add_argument("--arousal", type=int, default=None,
                        help="當前情境 arousal（0～10 喚起度）。不指定=不影響排序")
```
**注意 `default=None`（坑 1）。** 不要寫 `default=0`。

### Step 8　`recall.py`：`main()` 呼叫處傳入（約 line 496）

```python
    results = run_recall(
        args.query,
        args.top,
        args.tag,
        update_meta=not args.no_update,
        query_valence=args.valence,
        query_arousal=args.arousal,      # ← 加這行
    )
```

### Step 9　`tests/test_recall.py`：加 arousal 測試

純函式測試（不需要 roi_index，CI 一定跑得到）：
```python
class TestArousal:
    def test_none_is_always_neutral(self):
        from recall import _arousal_sim
        assert _arousal_sim(0, None) == 0.5
        assert _arousal_sim(10, None) == 0.5          # None 與 entry 值無關，恆中性

    def test_congruence(self):
        from recall import _arousal_sim
        assert _arousal_sim(9, 9) > _arousal_sim(1, 9)  # 高喚起 query 偏好高喚起記憶
        assert abs(_arousal_sim(5, 5) - 1.0) < 1e-9

    def test_zero_is_a_real_value(self):
        from recall import _arousal_sim
        # query=0（很平靜）要偏好低喚起記憶，不能被當成「未指定」（坑 1）
        assert _arousal_sim(0, 0) > _arousal_sim(10, 0)

    def test_run_recall_accepts_arousal(self, roi_entries):
        from recall import run_recall
        r = run_recall("主様", top_k=5, tag_filter=None,
                       update_meta=False, query_arousal=9)
        assert isinstance(r, list)
```

### Step 10　文件：把「5 軸」改成「6 軸」

`README.md` / `README.zh-TW.md` / `README.ja.md` 的計分表（「Scoring (5-Axis)」/「5-維度」）各加一列 `arousal_match | 0.08 | 喚起度一致性`，並把標題 5→6。`docs/system-status.md` 的 recall 公式同步補一項。

---

## 驗證清單（每條都要過）

1. **編譯**：`python3 -m py_compile src/recall.py`
2. **純函式**：`python3 -m pytest tests/test_recall.py -k Arousal -v`（4 條全綠）
3. **回歸（最重要，守住坑 3）**：不帶 --arousal，排序必須跟現在完全一致 —
   ```bash
   python3 src/recall.py "ELYTH" --top 5 --no-update --json
   # top3 仍應是：遇見イナンナ / 第一次在ELYTH發文 / Nanoleaf 那三篇，分數與順序不變
   ```
4. **帶 --arousal 要看得到差異**（用 `--no-update`，不要動到日記）：
   ```bash
   python3 src/recall.py "主様" --arousal 9 --top 5 --no-update   # 興奮/驚き類（高喚起）上升
   python3 src/recall.py "主様" --arousal 1 --top 5 --no-update   # 溫暖/平靜類（低喚起）上升
   # 兩次的 top-5 順序應該明顯不同
   ```
5. **不可污染記憶**：所有測試一律加 `--no-update`；驗證完 `git status` 應只看到 recall.py / settings.yaml / tests / README 的改動，**diary/ 下的 .md 不能有任何變動**。

---

## 之後可選（v1 不要做）

- **Mode A 被動喚起權重**：若想讓高喚起記憶「不查也比較黏」，可在 decay 或 emotional 旁加一個小的常駐 arousal 項。但這會改動「不帶 query」的基準排序，先別碰，等 v1 congruence 穩了再評估。
- **接線 diary-recall-inject**：07:00 注入時，可由近期對話估一個 arousal 傳進 `--arousal`。屬於整合層，跟本工單分開。

---

*完成後通知 showmaker；二次 review 會以你屆時 commit 的狀態為基準。記得把 README 的 5→6 一起改，不然文件會和程式對不上。*
