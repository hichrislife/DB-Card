"""Shared helpers for the local-mmlu tooling."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SCHEMA_PATH = ROOT / "schema" / "question.schema.json"
LICENSES_PATH = ROOT / "schema" / "licenses.json"
PROMPTS_DIR = ROOT / "prompts"
SUBJECTS_DIR = ROOT / "subjects"
CANARY_PATH = ROOT / "schema" / "CANARY.txt"

SPLITS = ("train", "validation", "test")
LETTERS = "ABCDEFGH"
SAMPLE_PREFIX = "sample_"


def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_jsonl(path: Path):
    """Yield (line_number, obj | None, raw_line). obj is None when the line is not valid JSON."""
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                yield n, json.loads(line), line
            except json.JSONDecodeError:
                yield n, None, line


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def languages():
    return sorted(p.name for p in DATA_DIR.iterdir() if p.is_dir())


def subjects_of(lang: str):
    base = DATA_DIR / lang
    return sorted(p.name for p in base.iterdir() if p.is_dir())


def load_subject_table(lang: str) -> dict[str, dict]:
    path = SUBJECTS_DIR / f"{lang}.tsv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return {row["subject"]: row for row in reader}


def load_prompt(lang: str) -> dict:
    import yaml  # local import so validate.py works without PyYAML when prompts are not needed

    with open(PROMPTS_DIR / f"{lang}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def canary() -> str:
    return CANARY_PATH.read_text(encoding="utf-8").splitlines()[0].strip()


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[\W_]+", re.UNICODE)


def normalize_text(s: str) -> str:
    """Lowercase, strip punctuation and whitespace. Used for duplicate detection."""
    return _PUNCT.sub("", _WS.sub("", s)).lower()


def is_sample_subject(subject: str) -> bool:
    return subject.startswith(SAMPLE_PREFIX)


def format_question(row: dict, prompt: dict, include_answer: bool) -> str:
    """Render one item exactly the way the evaluator will see it."""
    letters = prompt["letters"]
    text = row["question"]
    for letter, choice in zip(letters, row["choices"]):
        text += "\n" + prompt["choice_format"].format(letter=letter, text=choice)
    text += "\n" + prompt["answer_prefix"]
    if include_answer:
        text += letters[row["answer_index"]]
    return text


def extract_answer(response: str, prompt: dict) -> str | None:
    """Pull the final letter out of a free-form answer using the language's regex list."""
    for pattern in prompt["answer_regexes"]:
        m = re.search(pattern, response)
        if m:
            return m.group(1).upper()
    # Fallback: a lone letter at the very start or end of the response.
    m = re.match(r"^\s*\(?([A-H])\)?[\s.:]*$", response.strip())
    return m.group(1).upper() if m else None
