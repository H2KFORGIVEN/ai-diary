# ai-diary Code Review & 修正工單

> 審查者：Claude（Opus 4.8，via Claude Code）
> 日期：2026-06-02
> 審查基準：GitHub `H2KFORGIVEN/ai-diary` 的 `main` HEAD（已 push 版本）
> 對象讀者：47醬（本工單給你自己照著修；修完通知 showmaker，再請 Claude 二次 review）

---

## 0. 怎麼用這份工單

每一項都長這樣：**檔案:行號 → 問題 → 證據 → 修法（可直接貼）→ 驗證**。
照 **P0 → P1 → P2 → P3** 順序做。P0 兩項各約 10 分鐘，先做。
每改完一項，跑該項的「驗證」確認，再進下一項。全部做完後 **不要急著 `git add -A`**（理由見 P0-2）。

### ⚠️ 開工前必讀：目前工作目錄有未提交的本地修改

審查當下，本地 working tree 已有以下未提交變更（與 GitHub HEAD 不同）：

- `src/consolidate.py`：flashbulb 門檻已從 10 降為 8（`FLASHBULB_THRESHOLD = 8`，區域變數）。
- `src/roi.py`：`build_index()` 已加 `if "scenarios" in md.parts: continue`（line 157）。
- `diary/config/settings.yaml`：已補上完整 `scenario:` 設定區塊。
- 未追蹤目錄：`diary/archive/`、`diary/scenarios/`、`models/`（見 P0-2）。

**這些本地修改沒有動到下面任何一個 bug，所有問題目前都還在。** 行號以本地現況為準（consolidate.py 因上面的修改比 GitHub 版多 2 行）。若行號對不上，用「證據」裡的程式碼片段去搜尋定位。

---

## P0｜立刻修（資料遺失 + 隱私外洩）

### P0-1　`consolidate` 會靜默吃掉「被情緒過濾器位移」的事件

- **檔案**：`src/consolidate.py:220`（`group_medium()` 內部）；對照 `:398`、`:410`
- **問題**：`consolidate()` 在 Step 2（`:398`）用 **`filtered_intensity`**（情緒過濾後的強度）把事件分到 `medium`，但接著呼叫的 `group_medium()` 在 `:220` **又用原始 `e["intensity"]` 重新過濾一次**。兩個門檻不一致 → 跨界的事件直接從清單消失，而且此時 buffer 已被清空，無法復原。
- **為什麼嚴重**：emotion_filter 的核心行為就是「放大 trust ×1.8、壓抑 anger ×0.6 / disgust ×0.4」。正是這些被放大/壓抑後跨越 6↔7 邊界的事件會中招 —— 也就是說，這個 bug 專挑系統最想保留的「被壓抑的怒/嫌悪」「被放大的信任」記憶來丟。
- **證據**（實測重現）：
  ```
  事件：「主様被誤解，うち很不滿」 raw intensity=7，anger/external ×0.8 → filtered=6
    分到 medium？        True   （filtered_intensity=6，落在 (3,6]）
    通過 group_medium()？ False  （raw intensity=7，不在 (3,6]）
    => 結果：靜默丟失（永不寫入 .md，但 buffer 已清空 → 不可復原）
  ```
- **修法**：讓 `group_medium()` 與 Step 2 一致，改用 `filtered_intensity`：
  ```python
  # src/consolidate.py，group_medium() 內，目前的 line 220：
  #   medium = [e for e in events if DISCARD_THRESHOLD < e["intensity"] <= MERGE_THRESHOLD]
  # 改成：
      medium = [
          e for e in events
          if DISCARD_THRESHOLD < e.get("filtered_intensity", e["intensity"]) <= MERGE_THRESHOLD
      ]
  ```
  （備註：`group_medium()` 收到的本來就是 `consolidate()` 已篩好的 `medium` 清單，這個內部再篩其實是多餘的；對齊欄位後它變成正確的 pass-through。若想更乾淨，也可把這行直接改成 `medium = [e for e in events if e.get("tags") is not None or True]` 之類的「不再二次篩」寫法，但對齊欄位是風險最小的改法，建議先這樣。）
- **驗證**：
  ```python
  # 在專案根目錄跑：
  python3 - <<'PY'
  import sys; sys.path.insert(0, "src")
  from consolidate import group_medium
  ev = {"event":"test","intensity":7,"filtered_intensity":6,"tags":["惆悵"],"t":"2026-06-02T12:00:00"}
  groups = group_medium([ev])
  assert any(ev in g for g in groups), "FAIL：被壓抑事件仍被丟棄"
  print("OK：filtered_intensity=6 的事件被正確保留")
  PY
  ```

### P0-2　`.gitignore` 有三個破口 → 原始情緒資料會被推上公開 repo

- **檔案**：`.gitignore`
- **問題**：repo 的核心承諾是「你的記憶是你的、預設 gitignore」。但目前這三個會被寫入個人內容的路徑**都沒有被忽略**，而且 `git status` 顯示它們此刻就以 untracked 狀態躺在工作目錄裡（`?? diary/archive/`、`?? diary/scenarios/`、`?? models/`）。只要哪天 `git add -A && git push`，**未經過濾的原始 buffer 就上公開 GitHub 了**。
- **證據**：
  | 路徑 | 由誰寫入 | 目前 `git check-ignore` | 內容 |
  |---|---|---|---|
  | `diary/archive/*.jsonl` | `buffer.py:108-111`（consolidate 後備份） | ❌ NOT ignored | **未經情緒過濾的原始 buffer** — 比日記本身更赤裸 |
  | `diary/scenarios/`（含 `_drafts/`） | `scenarize.py:47` | ❌ NOT ignored | 成員標題、ROI 句、preview |
  | `models/` | `embedder.py:58` | ❌ NOT ignored | ~120MB 模型快取（README 宣稱已 ignore，實際沒有） |
- **修法**：在 `.gitignore` 末端補三行：
  ```gitignore
  # 原始 buffer 備份（最敏感，務必忽略）
  diary/archive/
  # L2 scenario 檔（含個人記憶衍生內容）
  diary/scenarios/
  # 本地模型快取（~120MB，且 README 宣稱已 ignore）
  models/
  ```
  另：README 目錄樹把 `models/e5-small/` 標成 `(gitignored)`，與實際不符 —— 補完 .gitignore 後這句就對了，不必另改。
- **驗證**：
  ```bash
  git check-ignore diary/archive/x.jsonl diary/scenarios/2026/x.md models/e5-small/config.json
  # 三條路徑都要被印出來（代表已被忽略）。沒印出來 = 還沒生效。
  ```

---

## P1｜建議盡快修（直接影響召回品質與既有功能）

### P1-1　RRF `k=60` 在小語料下讓所有分數塌成一團，boost 又完全壓過 RRF

- **檔案**：`src/recall.py:259`（`RRF_K = 60`）；相關 boost：`TAG_BOOST`(`:260`)、`SCENARIO_BOOST`(`:340`)、`VEC_BOOST`(`:383`)
- **問題**：RRF 分數 = Σ 1/(k + rank + 1)。`k=60` 是給上千篇文件的語料調的；目前只有十幾篇時，它把所有 rank 壓到幾乎等值。這正對應你在 `docs/system-status.md` 自己記的「坑 #5：score 集中在 0.048」。更糟的是，三個 boost（0.05 / 0.08 / 0.06）比整個 RRF 動態範圍還大好幾倍，等於任何被 boost 的條目都直接跳頂，與它真正的相關度無關。
- **證據**（實測，N=16）：
  ```
  best RRF（rank0 ×3）：  0.0492
  worst RRF（rank15 ×3）：0.0395
  整個動態範圍：          0.0097   ← 16 篇全擠在 0.01 區間
  TAG_BOOST=0.05 / SCENARIO_BOOST=0.08 = 範圍的 5.2~8.2 倍 → boost 變成「近乎二元的強制置頂」
  把 k 降到 10：範圍 → 約 0.158，boost(0.05~0.08) 才回到「輕推」的比例
  ```
- **修法**（單一最高槓桿改動）：把 `RRF_K` 從 60 降到 **10**，並隨語料量自適應：
  ```python
  # src/recall.py，run_recall() 內，目前 line 259：
  #   RRF_K = 60
  # 改成（放在 entries = load_index() 之後，才能用 len(entries)）：
      RRF_K = max(8, len(entries) // 4)   # 小語料用小 k 拉開鑑別度；語料變大時自動回升
  ```
  目前三個 boost 值（0.05/0.08/0.06）在 k=10 下比例就合理，**先不用動**。語料成長到數百篇後再回看是否要把 boost 改成「依 RRF 當下最大值按比例縮放」。
- **驗證**：
  ```bash
  python3 src/recall.py "ELYTH" --top 5 --no-update
  # 看 top-5 的 [score]：應該明顯拉開（不再全部 ~0.048）；
  # 真正關鍵詞命中的條目要排在沒命中的前面。
  ```

### P1-2　`harm_to_user`（設定）vs `harm_to_master`（程式碼）鍵名不一致 → 守護情緒 redirect 是死路

- **檔案**：`src/emotion_filter.py:68`、`:94`、`:253`（都寫 `harm_to_master`）；`diary/config/character_emotion_profile.yaml:88`、`:137`（寫 `harm_to_user`）
- **問題**：程式碼三處都找 `harm_to_master`，但設定檔的 key 是 `harm_to_user`。結果 `守りたい / 危険` 這類情緒永遠落到 fallback，或被誤導去 `integrity_violation` 分支 —— `protectiveness ×1.8`（對主様危害 → 守護感情）那條路**從來沒被執行過**。看起來是開源化時把 master→user 改了 YAML，卻漏改程式。
- **修法（先確認再改）**：先確認你**實際在跑**的 `character_emotion_profile.yaml` 用的是哪個 key（`grep harm_to_ diary/config/character_emotion_profile.yaml`）。
  - 若實際設定是 `harm_to_user`（= 目前 committed 的版本）→ 把 `emotion_filter.py` 三處 `harm_to_master` 全改成 `harm_to_user`：
    ```python
    # :68
    for sub in ("integrity_violation", "harm_to_user"):
    # :94
    ("disgust", "harm_to_user"):      -8,
    # :253
    "harm_to_user": ("disgust", "harm_to_user"),
    ```
  - 若你實際跑的是 `harm_to_master` → 改設定檔那兩處（`:88`、`:137`）成 `harm_to_master`。
  - **重點：兩邊 key 必須完全一致。** 別猜，先 grep 確認你正在用的那份。
- **驗證**：
  ```bash
  python3 src/emotion_filter.py   # 內建 smoke test
  # 看 ("守りたい", 8, "harm_to_master"/"harm_to_user") 那筆：
  # output_emotion 應為 protectiveness、tags 含「決意」、intensity ≈ 8×1.8→10（封頂）
  ```

---

## P2｜程式碼健康度（不急但值得清）

### P2-1　recall 的寫入放大：一次召回把整個索引重寫 top_k 次

- **檔案**：`src/recall.py` 的 `_update_recall_meta_by_path`（`:163-168` 每篇都重讀 `settings.yaml`）+ `:465-469` 的更新迴圈 → 每篇呼叫 `roi.update_index_entry`（`roi.py:222`，每次都把整包 `roi_index.json` 讀進來改一筆再整包寫回）。
- **問題**：top_k=5 的一次召回 = 整個 `roi_index.json` 完整 read+dump **五次**，外加重讀 settings.yaml 五次（模組頂層 `CONFIG` 明明已載）。16 篇沒感覺，語料到數千篇時每次「回憶」都在重寫整包索引五遍，是 O(top_k × N) 的序列化。
- **修法**：（1）`_update_recall_meta_by_path` 內改用已載入的模組層 `CONFIG`，別再 `yaml.safe_load(... settings.yaml)`；（2）在 `run_recall` 收集完所有要更新的 path、批次改完 frontmatter 後，**只重建一次索引**（呼叫一次 `build_index()` 或一個新的「批次 update」函式），而不是每篇呼叫 `update_index_entry`。
- **驗證**：召回結果不變；`roi_index.json` 的 `_built` 時間戳每次召回只更新一次。

### P2-2　`build_tag_graph.py:58` 去重條件寫壞了

- **檔案**：`src/build_tag_graph.py:58`
- **問題**：
  ```python
  if tag and tag not in graph[tag] or diary_id not in graph.get(tag, []):
  ```
  運算子優先序 = `(tag and (tag not in graph[tag])) or (diary_id not in ...)`。第一個子句幾乎恆為 True（tag 字串本來就不會等於某個 diary_id），去重形同虛設；`result` 最後也沒 dedup。同一篇若有重複 tag 會塞進重複 id。
- **修法**：
  ```python
  if diary_id not in graph[tag]:
      graph[tag].append(diary_id)
  ```
- **驗證**：`python3 src/build_tag_graph.py --show`；任一 tag 的 id 清單不應有重複。

### P2-3　死設定 / 死碼清理

- `src/summarize.py:25-26`：`SKIP_FLASHBULB`、`WEEKLY_THRESHOLD` 讀了但**全檔未使用**（flashbulb 不壓縮是靠 `normals` 過濾隱性達成的）。要嘛把 `skip_flashbulb` 真的接上邏輯，要嘛刪掉變數別誤導後人。
- `diary/config/settings.yaml` 新增的 `scenario.recall.weight: 0.35`：`recall.py` 的 Strategy D 實際用的是寫死的 `SCENARIO_BOOST=0.08`，沒讀這個 `weight`。要嘛接上、要嘛標註「未接線」。
- `src/embedder.py` 的 `encode_entry`：正式流程沒呼叫它（`build_vec_index` 自己內聯組字串），且它對 passage 用了 `query:` 前綴 —— 雖不影響正式路徑，但是個會誤導後人的陷阱，建議刪或修正前綴。

### P2-4　`scenarios` 跳過邏輯只做了一半

- **檔案**：`src/roi.py:157`（`build_index` 已加 skip）但 `load_index()` 的 stale-check 迴圈（`:204` 起）**沒有**跳過 `diary/scenarios/*.md`；`update_all_decay_weights`（consolidate.py）、`build_tag_graph`、`summarize` 的 rglob 也都沒跳。
- **問題**：scenario 檔的 mtime 會誤觸 roi_index 重建；其他掃描器可能把 scenario 當成日記處理。
- **修法**：把 `if "scenarios" in md.parts: continue` 套到所有掃 `diary/**/*.md` 的迴圈（roi.py 的 `load_index`、consolidate.py 的 `update_all_decay_weights`、build_tag_graph.py、summarize.py）。建議抽一個共用的 `is_diary_entry(md: Path) -> bool` helper，集中這套排除規則（summaries / config / scenarios / README / self-narrative）。
- **驗證**：建一個 scenario 檔後跑 `python3 src/recall.py "test" --no-update`，roi_index 不應因 scenario mtime 而重建，且 scenario 不出現在 recall 結果的 diary 清單裡。

### P2-5　向量子行程的 Python 路徑寫死

- **檔案**：`src/recall.py:387`（`_vec_python = "/usr/local/bin/python3"`）、`src/vec_search.py` 的 shebang
- **問題**：不是安全洞（單機），但讓 optional 向量層對任何 fork 你 repo 的人開箱即壞，也讓你換 Python 環境就失效。
- **修法**：改成讀環境變數，fallback 到目前值：
  ```python
  import os
  _vec_python = os.environ.get("AI_DIARY_VEC_PYTHON", "/usr/local/bin/python3")
  ```

---

## P3｜第六維度：要不要加？答案是「要，但先把 P1-1 做完」

目前 recall 5 軸：`keyword 0.30 / roi 0.20 / recency(decay) 0.20 / emotional 0.20 / valence_match 0.10`。

**關鍵觀察：`arousal`（喚起度 0–10）你每一篇都記了**（`write_diary`、`roi` index、frontmatter 全有），**但它對召回分數的貢獻是零** —— 只出現在 `format_result` 的顯示。而 README 親自引用的 **Cahill & McGaugh (1996) 正是「情緒喚起度驅動記憶固化」那篇**；Bower (1981) 的 mood-congruent retrieval 也同時吃 valence 和 arousal。情感的標準模型是 **valence × arousal 二維圓環** —— 你實作了 `valence_match` 卻沒有 `arousal_match`，等於只用了自己引用理論的一半。

所以最自然、最有理論根據、且**零額外資料採集成本**的第六維度就是 **arousal**：

- **模式 A（喚起權重）**：高 arousal 記憶更黏。注意 arousal ≠ intensity —— 「深沉的平靜感動」是高 intensity 低 arousal，「驚慌」是高 arousal，兩者該被區分。
- **模式 B（喚起一致性）**：讓 query 像帶 `--valence` 一樣帶一個當下 arousal，情境激動時優先撈出同樣高喚起的記憶（這才是 Bower mood-congruent 的全貌）。

**但順序很重要**：在 P1-1（RRF k=60）還沒修、分數全塌、boost 又壓過一切的情況下加第六軸，**你不會看到任何效果** —— 新軸訊號會被同樣的融合塌縮吃掉，甚至讓你誤以為「這功能沒用」。

**正確順序**：先做 P1-1 → 再加 arousal 第六軸（這時訊號才感覺得到）。

實作草案（P1-1 完成後再動）：
1. `settings.yaml` 的 `recall.weights` 加一軸並重新分配（總和保持 1.0），例如：
   ```yaml
   keyword: 0.28
   roi: 0.18
   recency: 0.18
   emotional: 0.18
   valence_match: 0.10
   arousal_match: 0.08   # 新增
   ```
2. `recall.py` 加 `arousal_score(entry, query_arousal)`（仿 `_valence_sim`，query_arousal=None 時回 0.5 中性），並加進 `recall_score_from_index`。
3. CLI 加 `--arousal` 參數（仿 `--valence`），一路傳到 `run_recall`。
4. 補一個 `tests/` 測試：高 arousal query 應讓高 arousal 記憶上升。

> 想走更野的方向（非必要）：比 arousal 更貼近人類非自主回憶的是 **associative co-recall（情節綁定）** —— 同一 session 一起被撈出來的記憶建立持久關聯邊，日後撈到其一就「這讓我想起…」帶出另一篇。但那是擴充 tag-graph / scenario 圖層、不是加分數軸，ROI 較高但風險也較高。**arousal 是 CP 值最高、最穩的那一步，建議先做它。**

---

## ⚠️ 給 showmaker（這一項不是 47醬 修的，是你本人要做）

審查時在 `.git/config` 與 credential.helper=`store` 發現：你的 **GitHub Personal Access Token（`ghp_` 開頭）以明文內嵌在 remote URL 中**，且 `store` helper 會把它另存一份明文在 `~/.git-credentials`。任何讀到這台機器、或不小心被 paste/截圖的場合都會外洩。

**建議步驟（你本人在 github.com 上做）**：
1. 到 GitHub → Settings → Developer settings → Personal access tokens，**撤銷（revoke）這顆 token**並重新產生一顆。
2. 把 remote 改成不含 token 的形式，並改用 keychain helper（或直接換 SSH）：
   ```bash
   git -C /Users/showmaker/Projects/ai-diary remote set-url origin https://github.com/H2KFORGIVEN/ai-diary.git
   git config --global credential.helper osxkeychain
   # 清掉明文 store：
   : > ~/.git-credentials   # 或編輯刪除該行
   ```
3. 之後 push 會跳一次認證，存進 keychain，就不再有明文 token。

（本工單已刻意不寫出 token 字串本身。）

---

## 修正優先順序總表

| 優先 | 項目 | 影響 | 預估 |
|---|---|---|---|
| 🔴 P0-1 | `group_medium` 改用 `filtered_intensity` | 停止靜默丟失被壓抑情緒的記憶 | 10 分 |
| 🔴 P0-2 | `.gitignore` 補 `archive/`、`scenarios/`、`models/` | 防原始情緒資料外洩到公開 repo | 5 分 |
| 🟠 P1-1 | `RRF_K` 60→自適應(≈10) | 讓召回排序真的有鑑別度（修坑#5） | 15 分 |
| 🟠 P1-2 | 統一 `harm_to_user`/`harm_to_master` | 救回 protectiveness redirect | 10 分 |
| 🟡 P2-1 | recall 批次寫回 + 重用 CONFIG | 解索引寫入放大，為規模化鋪路 | 30 分 |
| 🟡 P2-2 | `build_tag_graph:58` 去重條件 | 正確性 | 5 分 |
| 🟡 P2-3 | 死設定/死碼清理 | 可讀性 | 15 分 |
| 🟡 P2-4 | `scenarios` 跳過邏輯統一（抽 helper） | 一致性 | 20 分 |
| 🟡 P2-5 | 向量 Python 路徑改 env var | 可移植性 | 5 分 |
| 🟢 P3 | arousal 第六軸（**P1-1 之後**） | 補完 valence×arousal 情感模型 | 40 分 |
| 👤 — | 撤銷並輪換 GitHub token | 憑證安全（**showmaker 本人做**） | 10 分 |

---

## 做對的地方（給定心，不用改）

- ✅ 全程 `yaml.safe_load`，無 YAML RCE 風險。
- ✅ 所有 `subprocess.run` 用 argv list、無 `shell=True` → 即使 query 含特殊字元也無注入。
- ✅ `--date` 經 `datetime.date.fromisoformat` 驗證，擋路徑穿越。
- ✅ 依 intensity 分層的 decay half-life / floor、flashbulb 730 天慢衰減、RRF 多策略、subprocess 隔離 torch、IDF-Jaccard scenario 聚類、index 用 mtime 做 stale 偵測 —— 這些設計都有想法、有文獻根據。

整體是水準之上的專案，上面這些是「讓它更穩」而不是「打掉重練」。

---

*修完請通知 showmaker；二次 review 會以你屆時 commit 的狀態為基準（記得先把目前那幾個未提交的本地修改一併整理進去）。*
