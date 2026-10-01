# 離線法律工作站套件（offline-legal-kit）

讓 [Legal-Pleading-Suite](https://github.com/lexchang53/Legal-Pleading-Suite) 在**完全離線**的單機上運作，並與 DeepForge（訓練）、DeepSafe（安全）共用同一台 Edge 3（RTX 5070 Ti 16GB、64GB 記憶體）。

原本的套件有兩處需要連網：

| 原本 | 離線替代 |
|---|---|
| 裁判、函釋、法條檢索送到 `tlr.dr-legal.com.tw` | **`tlr_local`**：相容 twlegalrag 的本機檢索服務，資料來自自行下載的公開資料 |
| 撰狀的 AI 在雲端（Claude Code、Antigravity、Codex 等） | **`scripts/start-llm.sh`**：llama.cpp 本機模型，提供 OpenAI 相容 API |

Legal-Pleading-Suite 和 twlegalrag **一行都不用改**。twlegalrag 本來就可以設定連線端點，`tlr_local` 實作同一組 API（`/v1/search`、`/v1/fulltext`、`/v1/law_article`、`/v1/legal_reference`、`/v1/legal_references/search`、`/v1/health`），`pack`、`check`、`law`、`ref`、`ref-search` 和套件的 `scripts/check.py` 都照常運作。

`tlr_local` 只用 Python 標準函式庫（3.10 以上），離線機器不需要額外安裝套件。

---

## 一、Edge 3 標準配置（i7-13700E、64GB、RTX 5070 Ti 16GB）

設定值集中在 `profiles/edge3.env`。部署後先跑一次檢查：

```bash
./scripts/preflight.sh profiles/edge3.env
```

它會檢查顯卡和 VRAM、功耗上限、記憶體、swap、磁碟空間，以及 PDF、OCR、LLM 等工具，並確認 twlegalrag 是否確實指向本機。

### VRAM（16GB）

| 項目 | 白天（推論） | 夜間（訓練） |
|---|---|---|
| 螢幕輸出（接內顯） | 0 | 0 |
| `tlr_local` 檢索、規則式個資遮蔽 | 0 | 0 |
| 本機 LLM（30B 級 MoE，expert 放記憶體） | 約 4–6GB | 建議停止 |
| DeepForge 訓練（8B QLoRA） | — | 約 9–11GB |
| DeepSafe 的 GPU 模型（實際用量待確認） | 預留 2–4GB | 預留 2–4GB |
| 安全餘裕 | 6GB 以上 | 1–3GB |

### 系統記憶體（64GB）

| 項目 | 用量 |
|---|---|
| 作業系統 | 約 4GB |
| LLM 的 MoE expert 權重（30B Q4） | 約 18–19GB |
| 載入 8B 原始權重再量化（訓練開始時的瞬間峰值） | 約 8–16GB |
| `tlr_local`、OCR、文件匯入 | 約 4–8GB |
| DeepSafe（實際用量待確認） | 預留 4–8GB |
| 合計 | **約 40–55GB，可同時運作** |

**64GB 的限制：**
- 訓練 14B 時，光載入原始權重就要約 28GB，再加上 LLM 的 19GB 會太緊。
- 要訓練 14B，請擇一：
  - 訓練期間停止 LLM。
  - 改用已經量化成 4-bit 的基底模型檔，不必先載入原始權重。
- 32B 以上不在 Edge 3 的範圍。

以上都是估計值，部署後請用 `watch -n 1 nvidia-smi` 和 `free -g` 實測，並把 DeepSafe 實際的用量填回這張表。

### 建議排程

- **白天：** 開 LLM 和檢索服務，GPU 留給推論。
- **夜間：** 停掉 LLM（`pkill llama-server`），讓出 VRAM 給 DeepForge 訓練。checkpoint 寫到 `TRAIN_CHECKPOINT_DIR`，也就是 NAS 或第二顆 SSD。
- **功耗：** 用 `sudo nvidia-smi -pl 250` 把上限降到 250W，可降低工業機箱內的溫度。

DeepForge 的起始參數也在 `profiles/edge3.env` 的 `TRAIN_*` 欄位：8B、4-bit QLoRA、序列長度 4096、batch 1、gradient accumulation 16、LoRA r=16、gradient checkpointing、paged AdamW 8-bit。請依 DeepForge 的實際參數名稱對應設定。

### 升級路線

DeepForge 和 DeepSafe 都有 ARM 版，所以可以加一台 GX10 或 DGX Spark（128GB 統一記憶體）負責 32B–70B 的訓練與大模型推論。Edge 3 則繼續擔任前端，負責檢索、DeepSafe 和文件匯入。

---

## 二、準備資料（在可上網的電腦下載，再搬進離線機）

1. **裁判書**：司法院資料開放平臺（opendata.judicial.gov.tw）的裁判書資料，每月一包 RAR，**先解壓縮**成 JSON。每筆欄位為 `JID / JYEAR / JCASE / JNO / JDATE / JTITLE / JFULL`。
2. **法規**：全國法規資料庫（law.moj.gov.tw）開放資料的 `ChLaw.json`（法律）與 `ChOrder.json`（命令），zip 檔可直接匯入。
3. **函釋**（選用）：沒有單一官方開放資料集，請自行整理成 JSONL，一行一筆：
   ```json
   {"authority":"財政部","serial_no":"台財稅第881945861號","title":"…","issue_date":"1999-07-01","status":"active","fulltext":"…","source_url":"…"}
   ```
   `status` 使用 `active`、`repealed`、`superseded` 等值，twlegalrag 會依此標色。

---

## 三、安裝與啟動

```bash
cd offline-legal-kit
export TLR_LOCAL_DB=/data/tlr_local.sqlite       # 放在資料碟，不要放系統碟

# 匯入（可重複執行，同一 JID 會覆蓋）
python -m tlr_local ingest-judgments /data/judicial/2026-08/ /data/judicial/2026-07/
python -m tlr_local ingest-laws /data/moj/ChLaw.zip /data/moj/ChOrder.zip
python -m tlr_local ingest-refs /data/refs/*.jsonl
python -m tlr_local stats

# 啟動檢索服務（預設只綁 127.0.0.1:8787）
python -m tlr_local serve

# 讓 twlegalrag 永久改連本機
./scripts/configure-twlegalrag.sh
twlegalrag search "違約金 過高 酌減" -n 5
```

`twlegalrag` 在離線機上的安裝：在可上網的電腦執行 `pip download twlegalrag -d wheels`，把 `wheels/` 搬過去，再執行 `pip install --no-index --find-links wheels twlegalrag`。

> **環境變數優先於設定檔。** 如果系統裡設了 `TWLEGALRAG_TLR_BASE_URL` 指向公開端點，它會蓋過 `configure-twlegalrag.sh` 寫入的設定。離線機建議再用防火牆封鎖所有對外連線，雙重保險。

### 本機 LLM

```bash
MODEL=/models/<30B 級 MoE 模型>-Q4_K_M.gguf ./scripts/start-llm.sh
# OpenAI 相容端點：http://127.0.0.1:8080/v1，模型名稱 local-legal
```

VRAM 不夠時調大 `CPU_MOE`，或把 `CTX` 從 32768 降到 16384。`--jinja` 開啟工具呼叫格式，Agent 執行 skill 時需要。

撰狀需要一個能在本機執行 skill 的 Agent 工具，並把它的模型端點設成上面的 OpenAI 相容 API。請選用支援「自訂 OpenAI 相容端點」的工具，並確認它在斷網時可以運作。

本機模型的撰狀品質明顯低於雲端大型模型。產出一律要經過 `check.py` 驗證，並由律師逐條核對。

---

## 四、匯出 DeepForge 訓練語料

```bash
python -m tlr_local export-training /data/train/civil.jsonl --category 民事 --year-from 105
python -m tlr_local export-training /data/train/supreme.jsonl --court 最高法院
```

- 輸出 JSONL，每行 `{"text": "<字號>\n\n<理由段>", "doc_id": "..."}`，可直接用於繼續預訓練（continued pretraining）。
- 預設只取「理由」或「事實及理由」段落，並截在 16,000 字；加 `--full` 輸出全文。
- 輸出前會遮蔽身分證號、手機、市話、Email、信用卡號，以及標示為帳號的數字。各類遮蔽次數會列在統計中。
- 姓名、地址這類需要語意判斷的個資**不在遮蔽範圍**。司法院公開資料已先遮蔽當事人姓名；如果要混入客戶自己的案卷，必須另外處理。

---

## 五、使用者自備文件（按團隊分開）

每個團隊一個獨立的 SQLite 檔（`<docs-root>/<團隊>.sqlite`），團隊之間在檔案層級就分開。要刪除某個團隊的資料，刪掉該檔案即可。

```bash
export TLR_DOCS_ROOT=/data/teams

# 匯入：資料夾內的 .pdf / .docx / .doc / .txt / .md，未變動的檔案會自動略過
python -m tlr_local docs-ingest --team 訴訟一組 /nas/訴訟一組/案卷/
python -m tlr_local docs-search --team 訴訟一組 "解除契約 返還價金"

# 書狀 → 寫作風格訓練樣本（chat JSONL），另自動切出 10% 驗收集
python -m tlr_local export-sft --team 訴訟一組 /nas/訴訟一組/歷年書狀/ --out /data/train/訴訟一組.jsonl
```

**文件格式**
- `.docx`、`.txt`、`.md` 只用標準函式庫讀取；文字檔支援 UTF-8 和 Big5（cp950）。
- `.pdf` 需要 `pip install pymupdf`，或系統裝有 poppler 的 `pdftotext`。
- `.doc` 需要 LibreOffice（`soffice`）先轉成 `.docx`。

**掃描檔**
- PDF 中沒有文字層的頁面，在系統裝有 `tesseract` 和繁中語言包（`chi_tra`）時會自動 OCR。
- 沒有 tesseract 時，這些頁面會列在匯入結果的 `needs_ocr` 欄位，不會默默漏掉。
- OCR 的錯字會被模型學進去，掃描檔進訓練前請先抽查。

**訓練樣本的切法**
- 依「壹、貳、參」→「一、二、三」→ Word 標題樣式的順序，找出能切出兩段以上的層級。
- 每一段產生一筆樣本：
  - 使用者訊息：書狀類型、前文摘錄（600 字）、要撰寫的段落標題
  - 助理訊息：該段內文
- 這是在學「寫法」，不是讓模型記住案情。要查詢內容請用 `docs-search`。
- 預設遮蔽個資。只在團隊內部使用的 adapter 可以加 `--no-scrub`。
- 輸出是常見的 `messages` 格式。DeepForge 實際要哪種格式，請以它的文件為準。

**權限**
- `docs-search` 目前只有 CLI，沒有 HTTP 介面，避免任何人只要改一個團隊參數就能讀到別組資料。
- 如果要做成網頁服務，請先加上登入與團隊權限控管。

---

## 六、授權與資料來源

- **只能用自行下載的公開資料訓練。** tw-legal-rag 的服務條款（TERMS.md）禁止把 `tlr.dr-legal.com.tw` 回傳的內容拿來訓練或微調模型，也禁止大量匯出它的資料庫。`tlr_local` 完全不連該服務。
- **Legal-Pleading-Suite** 採 CC BY-NC-SA 4.0 授權，另附豁免條款。要整合進收費產品，須先取得作者的書面授權。
- **twlegalrag client** 採 Elastic License 2.0 授權。本套件沒有複製它的程式碼，只實作相容的 HTTP API。

---

## 七、限制

- **排序方式**：使用 BM25 關鍵字排序（中文以兩字一組切詞），沒有語意向量檢索。查詢請用具體法律用語，例如「違約金 過高 酌減」，不要用口語描述。
- **單字詞查不到**：單一個中文字無法當作查詢詞，至少要兩個字。
- **沒有審級歷程**：`case_history` 一律為 `null`。搜尋結果會附註「未收錄審級歷程」，所以不能據此推論判決已確定。
- **法院名稱**：從全文開頭解析。解析失敗時會退回 JID 裡的法院代碼（例如 `TPSV`）。
- **容量與速度**（合成資料實測，真實資料會落在兩者之間）：
  - 資料庫大小約為原始 JSON 的 0.7–1.6 倍。接近判決用語的文字實測 0.72 倍；隨機文字是最差情況，1.6 倍。全文用 zlib 壓縮存放，另加 bigram 索引。
  - 匯入速度每秒約 70–350 筆，視篇幅而定。全量裁判書要跑一天以上，建議按月分批匯入。
  - 全量裁判書（約兩千萬篇）估計要數百 GB 到 1TB 以上，加上模型和訓練 checkpoint，**1TB SSD 不夠**。空間有限時可以：
    - 只匯入需要的年份和法院。
    - 用 `--index-chars 20000` 只索引每篇的前 2 萬字，全文照樣完整保存。

## 測試

```bash
python -m unittest discover -s tests
```

有安裝 `twlegalrag` 時，測試會另外用真正的 client 連線到本機服務，端對端驗證 `pack`、`check`、`law`、`ref`、`ref-search`。`tests/fixtures` 裡的裁判書是測試用的合成資料，不是真實案件。
