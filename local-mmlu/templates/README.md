# Contribution templates

`contribution_template.csv` is the spreadsheet-friendly format for people who do not use git.

- One row per question. `choice_e` (and `choice_f`, `choice_g`, `choice_h` if you add them) may be empty.
- `answer` is the letter of the correct choice (A, B, C, ...).
- `license` must be one of the tags in `schema/licenses.json`.
- Convert with `python tools/csv2jsonl.py your_file.csv --split test` and the validator will tell you what is missing.

Google Sheets users: File -> Download -> Comma-separated values, keep UTF-8.
