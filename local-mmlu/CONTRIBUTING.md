# Contributing / 貢獻指南

The flow is the same in every language. Pick your section.

---

## 繁體中文（zh-TW）

1. 先讀 `data/zh-TW/LICENSES.md`，確認你的題目來源可以合法收錄（考選部與教育部的國家考試可以，坊間題庫不行）。
2. 在 `subjects/zh-TW.tsv` 找到或新增科目（snake_case 鍵、中文名、四大分類之一、來源）。
3. 複製 `templates/contribution_template.csv`，每行一題，`answer` 填正確選項字母。
4. 轉檔並驗證：
   ```bash
   python tools/csv2jsonl.py 我的題目.csv --split test
   python tools/validate.py --lang zh-TW
   ```
5. 開 PR，勾完 PR 範本的授權檢查清單。test 分割需兩位審核者在 `reviewed_by` 簽名後才會合併。

## 日本語（ja）

1. まず `data/ja/LICENSES.md` を読み、出典が収録可能か確認してください（共通テストは許諾が必要です）。
2. `subjects/ja.tsv` で科目を探す、または追加する（snake_case キー、日本語名、4 分類のいずれか、出典）。
3. `templates/contribution_template.csv` をコピーし、1 行 1 問、`answer` に正解の文字（A〜H）を記入。
4. 変換と検証：
   ```bash
   python tools/csv2jsonl.py my_questions.csv --split test
   python tools/validate.py --lang ja
   ```
5. PR を作成し、テンプレートのライセンス確認項目にチェックを入れてください。test 分割は 2 名のレビュー（`reviewed_by`）後にマージされます。

## 한국어（ko）

1. 먼저 `data/ko/LICENSES.md` 를 읽고 출처를 수록할 수 있는지 확인하세요 (수능 문항은 KICE 허락이 필요합니다).
2. `subjects/ko.tsv` 에서 과목을 찾거나 추가하세요 (snake_case 키, 한국어 이름, 4개 분류 중 하나, 출처).
3. `templates/contribution_template.csv` 를 복사해 한 줄에 한 문항씩 작성하고 `answer` 에 정답 글자(A~H)를 적으세요.
4. 변환 및 검증:
   ```bash
   python tools/csv2jsonl.py my_questions.csv --split test
   python tools/validate.py --lang ko
   ```
5. PR 을 열고 템플릿의 라이선스 체크리스트를 모두 확인하세요. test 분할은 검토자 2명이 `reviewed_by` 에 서명해야 병합됩니다.

## English (en)

1. Read `data/en/LICENSES.md` first and make sure your source may be included (UK exam boards need permission; US federal material is public domain).
2. Find or add the subject in `subjects/en.tsv` (snake_case key, display name, one of the four categories, source hint).
3. Copy `templates/contribution_template.csv`, one question per row, `answer` is the letter of the correct choice.
4. Convert and validate:
   ```bash
   python tools/csv2jsonl.py my_questions.csv --split test
   python tools/validate.py --lang en
   ```
5. Open a PR and tick the licence checklist in the template. Test-split items are merged after two reviewers sign `reviewed_by`.

## Nederlands (nl)

1. Lees eerst `data/nl/LICENSES.md` en controleer of je bron mag worden opgenomen (CvTE-examens vereisen toestemming).
2. Zoek of voeg het vak toe in `subjects/nl.tsv` (snake_case-sleutel, Nederlandse naam, een van de vier categorieën, bron).
3. Kopieer `templates/contribution_template.csv`, één vraag per rij, `answer` is de letter van het juiste antwoord.
4. Converteren en valideren:
   ```bash
   python tools/csv2jsonl.py mijn_vragen.csv --split test
   python tools/validate.py --lang nl
   ```
5. Open een PR en vink de licentiechecklist in het sjabloon af. Items in de test-split worden samengevoegd na twee reviewers in `reviewed_by`.

## Deutsch (de)

1. Lesen Sie zuerst `data/de/LICENSES.md` und prüfen Sie, ob Ihre Quelle aufgenommen werden darf (Abitur- und IMPP-Aufgaben brauchen eine Genehmigung).
2. Suchen oder ergänzen Sie das Fach in `subjects/de.tsv` (snake_case-Schlüssel, deutscher Name, eine der vier Kategorien, Quelle).
3. Kopieren Sie `templates/contribution_template.csv`, eine Frage pro Zeile, `answer` ist der Buchstabe der richtigen Option.
4. Konvertieren und prüfen:
   ```bash
   python tools/csv2jsonl.py meine_fragen.csv --split test
   python tools/validate.py --lang de
   ```
5. Öffnen Sie einen PR und haken Sie die Lizenz-Checkliste ab. Test-Items werden nach zwei Reviewern in `reviewed_by` gemergt.

## Italiano (it)

1. Leggi prima `data/it/LICENSES.md` e verifica che la fonte possa essere inclusa (le banche dati ufficiali dei concorsi sì, le prove di Maturità richiedono autorizzazione).
2. Trova o aggiungi la materia in `subjects/it.tsv` (chiave snake_case, nome italiano, una delle quattro categorie, fonte).
3. Copia `templates/contribution_template.csv`, una domanda per riga, `answer` è la lettera della risposta corretta.
4. Converti e valida:
   ```bash
   python tools/csv2jsonl.py mie_domande.csv --split test
   python tools/validate.py --lang it
   ```
5. Apri una PR e spunta la checklist delle licenze nel template. Gli item del test vengono uniti dopo la firma di due revisori in `reviewed_by`.

---

## Rules that apply to everyone

- `test` items are never used for training and never pasted into public chats, issues or model prompts outside evaluation.
- Do not machine-translate items from another language's set. Each locale collects its own exams.
- Before a release, maintainers run `tools/validate.py --strict` and `tools/decontaminate.py` against the reference corpora listed in `CHANGELOG.md`.
- Corrections to answers or removals are recorded in `CHANGELOG.md` with the item ids, following the TMMLU+ v1.0 → v1.1 practice.
