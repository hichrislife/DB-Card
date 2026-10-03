#!/usr/bin/env python3
"""Validate every data/{lang}/{subject}/{split}.jsonl file.

Exit code 1 on any error. Warnings never fail the run unless --strict is given.

Checks
  * file layout: data/{lang}/{subject}/{train|validation|test}.jsonl
  * each line is JSON and matches schema/question.schema.json
  * id is globally unique and starts with "{lang}-{subject}-"
  * lang / subject fields match the directory they live in
  * answer_index points inside choices, choices are unique
  * license is in schema/licenses.json (non-commercial only under the allowed dirs)
  * subject exists in subjects/{lang}.tsv and category matches it
  * no duplicate question text within a subject (after normalisation)
  * no canary string inside question/choices
  * minimum sizes: train >= 5, test >= 100 (warning, error with --strict; sample_* subjects exempt)
  * strict mode: every test item has >= 2 reviewers
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DATA_DIR, LICENSES_PATH, ROOT, SCHEMA_PATH, SPLITS, canary, is_sample_subject,
    languages, load_json, load_subject_table, normalize_text, read_jsonl, subjects_of,
)

MIN_TRAIN = 5
MIN_TEST = 100
MIN_REVIEWERS = 2


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where, msg):
        self.errors.append(f"{where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"{where}: {msg}")


def make_schema_validator():
    schema = load_json(SCHEMA_PATH)
    try:
        import jsonschema

        validator = jsonschema.Draft202012Validator(schema)

        def check(obj):
            return [e.message for e in sorted(validator.iter_errors(obj), key=lambda e: list(e.path))]

        return check
    except ImportError:  # minimal fallback so the validator still runs without jsonschema
        required = schema["required"]
        allowed = set(schema["properties"])

        def check(obj):
            msgs = [f"'{k}' is a required property" for k in required if k not in obj]
            msgs += [f"unexpected property '{k}'" for k in obj if k not in allowed]
            return msgs

        return check


def license_ok(lic: str, rel_path: str, licenses: dict) -> str | None:
    if lic in licenses["commercial_ok"]:
        return None
    if lic in licenses["noncommercial"]:
        if any(rel_path.startswith(d) for d in licenses["noncommercial_dirs"]):
            return None
        return f"non-commercial license {lic!r} is only allowed under {licenses['noncommercial_dirs']}"
    return f"unknown license {lic!r} (see schema/licenses.json)"


def validate_file(path: Path, lang: str, subject: str, split: str, ctx: dict, rep: Report) -> int:
    rel = str(path.relative_to(ROOT))
    seen_norm: dict[str, str] = {}
    count = 0
    for n, obj, raw in read_jsonl(path):
        where = f"{rel}:{n}"
        if obj is None:
            rep.error(where, "invalid JSON")
            continue
        count += 1
        for msg in ctx["schema_check"](obj):
            rep.error(where, msg)
        if not isinstance(obj, dict):
            continue
        if obj.get("lang") != lang:
            rep.error(where, f"lang={obj.get('lang')!r} but file is under data/{lang}/")
        if obj.get("subject") != subject:
            rep.error(where, f"subject={obj.get('subject')!r} but file is under data/{lang}/{subject}/")
        qid = obj.get("id")
        if isinstance(qid, str):
            if not qid.startswith(f"{lang}-{subject}-"):
                rep.error(where, f"id {qid!r} must start with '{lang}-{subject}-'")
            if qid in ctx["ids"]:
                rep.error(where, f"duplicate id {qid!r} (first seen at {ctx['ids'][qid]})")
            else:
                ctx["ids"][qid] = where
        choices = obj.get("choices")
        ai = obj.get("answer_index")
        if isinstance(choices, list) and isinstance(ai, int) and not (0 <= ai < len(choices)):
            rep.error(where, f"answer_index {ai} out of range for {len(choices)} choices")
        lic = obj.get("license")
        if isinstance(lic, str):
            msg = license_ok(lic, rel, ctx["licenses"])
            if msg:
                rep.error(where, msg)
        table = ctx["subject_tables"][lang]
        if table and subject not in table:
            rep.error(where, f"subject {subject!r} not listed in subjects/{lang}.tsv")
        elif table and obj.get("category") != table[subject]["category"]:
            rep.error(where, f"category {obj.get('category')!r} differs from subjects/{lang}.tsv ({table[subject]['category']!r})")
        q = obj.get("question")
        if isinstance(q, str):
            key = normalize_text(q)
            if key in seen_norm:
                rep.error(where, f"duplicate question text (same as {seen_norm[key]})")
            else:
                seen_norm[key] = where
            if ctx["canary"] in q or any(ctx["canary"] in c for c in (choices or []) if isinstance(c, str)):
                rep.error(where, "canary string must not appear inside question content")
        if split == "test" and ctx["strict"]:
            reviewers = obj.get("reviewed_by") or []
            if len(set(reviewers)) < MIN_REVIEWERS:
                rep.error(where, f"test items need >= {MIN_REVIEWERS} distinct reviewers in strict mode")
    return count


def validate(strict: bool = False, only_lang: str | None = None) -> Report:
    rep = Report()
    ctx = {
        "schema_check": make_schema_validator(),
        "licenses": load_json(LICENSES_PATH),
        "ids": {},
        "canary": canary(),
        "strict": strict,
        "subject_tables": {},
    }
    for lang in languages():
        if only_lang and lang != only_lang:
            continue
        ctx["subject_tables"][lang] = load_subject_table(lang)
        if not ctx["subject_tables"][lang]:
            rep.warn(f"subjects/{lang}.tsv", "missing or empty; subject/category cross-check skipped")
        for subject in subjects_of(lang):
            sdir = DATA_DIR / lang / subject
            counts = defaultdict(int)
            for p in sdir.iterdir():
                if p.suffix != ".jsonl":
                    if p.name != ".gitkeep":
                        rep.warn(str(p.relative_to(ROOT)), "unexpected file (only *.jsonl is read)")
                    continue
                split = p.stem
                if split not in SPLITS:
                    rep.error(str(p.relative_to(ROOT)), f"split must be one of {SPLITS}")
                    continue
                counts[split] = validate_file(p, lang, subject, split, ctx, rep)
            if is_sample_subject(subject):
                continue
            where = f"data/{lang}/{subject}"
            size_issue = rep.error if strict else rep.warn
            if counts["train"] < MIN_TRAIN:
                size_issue(where, f"train has {counts['train']} items, need >= {MIN_TRAIN} for few-shot")
            if counts["test"] < MIN_TEST:
                size_issue(where, f"test has {counts['test']} items, need >= {MIN_TEST}")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true", help="treat size/reviewer warnings as errors (use before a release)")
    ap.add_argument("--lang", help="validate one language only")
    args = ap.parse_args(argv)
    rep = validate(strict=args.strict, only_lang=args.lang)
    for w in rep.warnings:
        print(f"WARN  {w}")
    for e in rep.errors:
        print(f"ERROR {e}")
    print(f"\n{len(rep.errors)} error(s), {len(rep.warnings)} warning(s)")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
