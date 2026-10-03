#!/usr/bin/env python3
"""Flag questions whose text overlaps a reference corpus (pre-training data, web dump, another benchmark).

    python tools/decontaminate.py --corpus /path/to/corpus.txt [--lang ja] [--ngram 13] [--char-ngram 20]

The corpus is one document per line (plain text). Word n-grams are used for space-delimited languages;
character n-grams for zh-TW, ja and ko. A question is flagged if any of its n-grams appears in the corpus.
This is the same idea as the GPT-3 / MMLU 13-gram check; it is a filter, not a proof of cleanliness.
Output: TSV of (id, matching n-gram) on stdout. Exit code 2 when anything is flagged.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA_DIR, languages, read_jsonl, subjects_of  # noqa: E402

CJK = {"zh-TW", "ja", "ko"}
_WS = re.compile(r"\s+")


def ngrams(text: str, lang: str, n_word: int, n_char: int):
    if lang in CJK:
        t = _WS.sub("", text)
        return {t[i : i + n_char] for i in range(0, max(0, len(t) - n_char + 1))}
    words = re.findall(r"\w+", text.lower())
    return {" ".join(words[i : i + n_word]) for i in range(0, max(0, len(words) - n_word + 1))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, required=True, help="one document per line")
    ap.add_argument("--lang", help="limit to one language")
    ap.add_argument("--ngram", type=int, default=13, help="word n-gram size for space-delimited languages")
    ap.add_argument("--char-ngram", type=int, default=20, help="character n-gram size for zh-TW/ja/ko")
    args = ap.parse_args(argv)

    langs = [args.lang] if args.lang else languages()
    # Build the question n-gram index first, then stream the corpus once.
    index: dict[str, dict[str, list[str]]] = {}
    for lang in langs:
        index[lang] = {}
        for subject in subjects_of(lang):
            for p in (DATA_DIR / lang / subject).glob("*.jsonl"):
                for _, obj, _ in read_jsonl(p):
                    if not obj:
                        continue
                    text = obj["question"] + " " + " ".join(obj["choices"])
                    for g in ngrams(text, lang, args.ngram, args.char_ngram):
                        index[lang].setdefault(g, []).append(obj["id"])

    flagged: dict[str, str] = {}
    with open(args.corpus, encoding="utf-8", errors="replace") as f:
        for doc in f:
            for lang in langs:
                for g in ngrams(doc, lang, args.ngram, args.char_ngram):
                    for qid in index[lang].get(g, ()):
                        flagged.setdefault(qid, g)
    for qid, g in sorted(flagged.items()):
        print(f"{qid}\t{g}")
    print(f"# {len(flagged)} question(s) flagged", file=sys.stderr)
    return 2 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
