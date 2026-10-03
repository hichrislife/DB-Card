#!/usr/bin/env python3
"""Export a subject to the flat TMMLU+/MMLU layout (question, A, B, C, D, answer) for tools that expect it.

    python tools/to_tmmlu_format.py zh-TW administrative_law --out out/tmmlu_compat/

Items with more than four choices are skipped with a notice (the flat layout cannot hold them).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA_DIR, LETTERS, read_jsonl  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lang")
    ap.add_argument("subject")
    ap.add_argument("--out", type=Path, default=Path("out/tmmlu_compat"))
    args = ap.parse_args(argv)

    sdir = DATA_DIR / args.lang / args.subject
    if not sdir.exists():
        print(f"no such subject: {sdir}", file=sys.stderr)
        return 1
    skipped = 0
    for split_file in sorted(sdir.glob("*.jsonl")):
        out = args.out / args.lang / args.subject / f"{split_file.stem}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "question", "A", "B", "C", "D", "answer"])
            for _, obj, _ in read_jsonl(split_file):
                if not obj:
                    continue
                if len(obj["choices"]) != 4:
                    skipped += 1
                    continue
                w.writerow([obj["id"], obj["question"], *obj["choices"], LETTERS[obj["answer_index"]]])
        print(f"wrote {out}")
    if skipped:
        print(f"skipped {skipped} item(s) that do not have exactly 4 choices")
    return 0


if __name__ == "__main__":
    sys.exit(main())
