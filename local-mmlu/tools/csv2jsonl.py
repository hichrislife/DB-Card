#!/usr/bin/env python3
"""Convert a contributor CSV (templates/contribution_template.csv layout) into data/{lang}/{subject}/{split}.jsonl.

    python tools/csv2jsonl.py my_questions.csv --split test
    python tools/csv2jsonl.py my_questions.csv --split test --append   # keep existing items, continue numbering

Rows are grouped by (lang, subject). Ids are assigned sequentially per subject.
Run tools/validate.py afterwards; this script only does structural conversion.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA_DIR, LETTERS, SPLITS, read_jsonl, write_jsonl  # noqa: E402

CHOICE_COLS = [f"choice_{c.lower()}" for c in LETTERS]


def row_to_item(row: dict, lang: str, subject: str, seq: int) -> dict:
    choices = [row[c].strip() for c in CHOICE_COLS if c in row and row[c] and row[c].strip()]
    answer = row["answer"].strip().upper()
    if answer not in LETTERS[: len(choices)]:
        raise ValueError(f"answer {answer!r} is not one of {LETTERS[:len(choices)]} for question {row['question'][:40]!r}")
    year = row.get("year", "").strip()
    return {
        "id": f"{lang}-{subject}-{seq:05d}",
        "lang": lang,
        "country": row["country"].strip().upper(),
        "subject": subject,
        "category": row["category"].strip(),
        "question": row["question"].strip(),
        "choices": choices,
        "answer_index": LETTERS.index(answer),
        "explanation": row.get("explanation", "").strip() or None,
        "source": row["source"].strip(),
        "source_url": row.get("source_url", "").strip() or None,
        "year": int(year) if year else None,
        "license": row["license"].strip(),
        "difficulty": row.get("difficulty", "").strip() or None,
        "contributed_by": row.get("contributed_by", "").strip() or None,
        "reviewed_by": [r.strip() for r in row.get("reviewed_by", "").split(";") if r.strip()],
        "tags": [t.strip() for t in row.get("tags", "").split(";") if t.strip()],
    }


def existing_max_seq(lang: str, subject: str) -> int:
    top = -1
    sdir = DATA_DIR / lang / subject
    if not sdir.exists():
        return top
    for p in sdir.glob("*.jsonl"):
        for _, obj, _ in read_jsonl(p):
            if obj and isinstance(obj.get("id"), str):
                try:
                    top = max(top, int(obj["id"].rsplit("-", 1)[1]))
                except ValueError:
                    pass
    return top


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path", type=Path)
    ap.add_argument("--split", choices=SPLITS, required=True)
    ap.add_argument("--append", action="store_true", help="append to an existing split file instead of refusing to overwrite")
    args = ap.parse_args(argv)

    with open(args.csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["lang"].strip(), r["subject"].strip())].append(r)

    for (lang, subject), items in groups.items():
        out = DATA_DIR / lang / subject / f"{args.split}.jsonl"
        existing = []
        if out.exists():
            if not args.append:
                print(f"refusing to overwrite {out}; pass --append", file=sys.stderr)
                return 1
            existing = [obj for _, obj, _ in read_jsonl(out) if obj]
        seq = existing_max_seq(lang, subject) + 1
        converted = []
        for r in items:
            converted.append(row_to_item(r, lang, subject, seq))
            seq += 1
        write_jsonl(out, existing + converted)
        print(f"wrote {len(converted)} item(s) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
