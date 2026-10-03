# Local-MMLU

在地考題、多語言、社群可自行擴充的語言模型知識評測集框架。
以 [TMMLU+](https://huggingface.co/datasets/ikala/tmmluplus) 的做法為起點（用當地真實考試測當地知識），把格式、評測器與貢獻流程抽成與語言無關的工具，目前支援：

| lang | 地區 | 提示模板 | 科目表 | 授權說明 |
|---|---|---|---|---|
| `zh-TW` | 臺灣 | `prompts/zh-TW.yaml` | `subjects/zh-TW.tsv` | `data/zh-TW/LICENSES.md` |
| `ja` | 日本 | `prompts/ja.yaml` | `subjects/ja.tsv` | `data/ja/LICENSES.md` |
| `ko` | 韓國 | `prompts/ko.yaml` | `subjects/ko.tsv` | `data/ko/LICENSES.md` |
| `en` | 英語圈 | `prompts/en.yaml` | `subjects/en.tsv` | `data/en/LICENSES.md` |
| `nl` | 荷蘭、比利時 | `prompts/nl.yaml` | `subjects/nl.tsv` | `data/nl/LICENSES.md` |
| `de` | 德國、奧地利、瑞士 | `prompts/de.yaml` | `subjects/de.tsv` | `data/de/LICENSES.md` |
| `it` | 義大利 | `prompts/it.yaml` | `subjects/it.tsv` | `data/it/LICENSES.md` |

新增一種語言只需要三個檔案：`prompts/{lang}.yaml`、`subjects/{lang}.tsv`、`data/{lang}/LICENSES.md`，再把 `{lang}` 加進 `schema/question.schema.json` 的 `lang.enum`。

> 這個目錄目前放在 DB-Card repo 的分支裡只是為了方便檢視。它是獨立專案，拆出去的指令見最下方。

## 與 TMMLU+ 的差異

| | TMMLU+ | Local-MMLU |
|---|---|---|
| 欄位 | `question, A, B, C, D, answer` | `choices` 陣列 + `answer_index`，2 到 8 個選項 |
| 語言 | 繁中，提示詞與答案正則寫死在評測器裡 | 每語言一份 `prompts/{lang}.yaml`，評測器完全不含語言字串 |
| 來源與授權 | 僅資料卡整體說明 | 每題必填 `source`、`license`，非商用授權只能放在隔離目錄 |
| 評測器 | 自家 `ievals` | 產生 lm-evaluation-harness 任務；另提供 TMMLU+ 格式匯出以相容舊工具 |
| 貢獻流程 | 無 | CSV 範本、PR 檢查清單、CI 驗證、雙人審核、污染檢查、canary |

## 目錄

```
schema/question.schema.json   每題的 JSON Schema
schema/licenses.json          允許的授權標記；非商用授權的隔離目錄
schema/CANARY.txt             污染偵測字串
prompts/{lang}.yaml           指令、選項格式、「答案：」對應詞、CoT 抽取正則
subjects/{lang}.tsv           subject 鍵、當地語名稱、分類、建議來源
data/{lang}/{subject}/{train,validation,test}.jsonl
data/{lang}/LICENSES.md       該地區各類考題的法律依據
templates/                    給不會用 git 的貢獻者的 CSV 範本
tools/validate.py             結構、授權、重複、數量、審核人檢查（CI 會跑）
tools/csv2jsonl.py            CSV 轉 jsonl 並自動編號
tools/build_hf.py             產生可直接上傳 Hugging Face 的 parquet + 資料卡
tools/to_tmmlu_format.py      匯出成 TMMLU+ 平面格式
tools/decontaminate.py        n-gram 與語料比對
lm_eval/generate_tasks.py     產生 lm-evaluation-harness 任務與語言群組
tests/                        pytest
.github/workflows/validate.yml
```

## 快速開始

```bash
pip install -r requirements.txt
python tools/validate.py                         # 檢查所有資料
python tools/build_hf.py --out out/hf --repo-id your-org/local-mmlu
python lm_eval/generate_tasks.py --hf-repo your-org/local-mmlu
lm_eval --model hf --model_args pretrained=... --tasks local_mmlu_ja --include_path lm_eval/tasks
```

`sample_general_knowledge` 是各語言的範例科目，只用來驗證流程，預設不會被 `build_hf.py` 匯出，也不應計入任何排行榜。

## 資料格式

```json
{"id": "ja-civil_law-00123", "lang": "ja", "country": "JP",
 "subject": "civil_law", "category": "humanities",
 "question": "...", "choices": ["...", "...", "...", "..."], "answer_index": 2,
 "explanation": null,
 "source": "司法試験 2022 短答式", "source_url": "https://...", "year": 2022,
 "license": "government-work", "difficulty": "professional",
 "contributed_by": "handle", "reviewed_by": ["reviewer1", "reviewer2"], "tags": []}
```

`train` 分割放 5 題 few-shot 範例，`test` 是正式題目，發布前必須 `validate.py --strict` 通過（每科 test ≥ 100 題、每題 ≥ 2 位審核者）。`test` 不可用於訓練模型。

## 評測集與訓練集

這是評測集。若要同時提供訓練資料，請另開 `train` 分割且規模明顯大於 5 題，並確保與 `test` 無重疊（`tools/decontaminate.py` 可用 test 當 corpus 反向比對）。

## 授權

- 程式碼：MIT（`LICENSE`）
- 題目：逐題標示，見 `license` 欄位與各 `data/{lang}/LICENSES.md`；彙編本身以 CC BY 4.0 發布
- 若收錄 TMMLU+ 衍生題目，只能放在 `data/zh-TW/_derived_tmmluplus/`，並沿用其 CC BY-NC-SA 4.0（請以 HF 頁面為準）

## 拆成獨立 repo

```bash
git subtree split -P local-mmlu -b local-mmlu-main
git push git@github.com:your-org/local-mmlu.git local-mmlu-main:main
```

---

## English summary

Local-MMLU is a language-agnostic framework for building TMMLU+-style benchmarks from locally sourced exam questions. Seven languages are wired up (zh-TW, ja, ko, en, nl, de, it); adding one means a prompt YAML, a subject table and a licence note. Every item carries `source` and `license`; non-commercial items are quarantined. `tools/validate.py` runs in CI, `tools/build_hf.py` emits a ready-to-upload Hugging Face layout, and `lm_eval/generate_tasks.py` produces lm-evaluation-harness tasks so one command scores a model across all languages. See `CONTRIBUTING.md` for the contributor flow in each language.
