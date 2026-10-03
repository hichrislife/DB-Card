#!/usr/bin/env python3
"""Build a Hugging Face dataset repository layout from data/.

    python tools/build_hf.py --out out/hf [--include-samples] [--repo-id your-org/local-mmlu]

Produces
  out/hf/data/{lang}/{subject}/{split}.parquet   (falls back to .jsonl if pyarrow is missing)
  out/hf/README.md   dataset card with one `configs:` entry per {lang}.{subject} and one `{lang}` aggregate
  out/hf/CANARY.txt
Push the folder with `huggingface-cli upload <repo-id> out/hf .` (or git lfs).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    CANARY_PATH, DATA_DIR, ROOT, canary, is_sample_subject, languages, load_subject_table, read_jsonl, subjects_of,
)

LANG_NAMES = {"zh-TW": "Traditional Chinese (Taiwan)", "ja": "Japanese", "ko": "Korean", "en": "English",
              "nl": "Dutch", "de": "German", "it": "Italian"}
HF_LANG_CODES = {"zh-TW": "zh", "ja": "ja", "ko": "ko", "en": "en", "nl": "nl", "de": "de", "it": "it"}


def write_split(rows: list[dict], out: Path) -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        # Normalise optional fields so every row has the same keys.
        keys = ["id", "lang", "country", "subject", "category", "question", "choices", "answer_index", "explanation",
                "source", "source_url", "year", "license", "difficulty", "contributed_by", "reviewed_by", "tags"]
        cols = {k: [r.get(k) for r in rows] for k in keys}
        for k in ("reviewed_by", "tags"):
            cols[k] = [v or [] for v in cols[k]]
        table = pa.table(cols)
        pq.write_table(table, out.with_suffix(".parquet"))
        return out.with_suffix(".parquet").name
    except ImportError:
        import json

        with open(out.with_suffix(".jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return out.with_suffix(".jsonl").name


def build(out_dir: Path, include_samples: bool, repo_id: str | None) -> dict:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    configs = []
    per_lang_files: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for lang in languages():
        table = load_subject_table(lang)
        for subject in subjects_of(lang):
            if is_sample_subject(subject) and not include_samples:
                continue
            data_files = []
            for split_file in sorted((DATA_DIR / lang / subject).glob("*.jsonl")):
                rows = [obj for _, obj, _ in read_jsonl(split_file) if obj]
                if not rows:
                    continue
                rel = Path("data") / lang / subject / split_file.stem
                name = write_split(rows, out_dir / rel)
                path = f"data/{lang}/{subject}/{name}"
                data_files.append({"split": split_file.stem, "path": path})
                per_lang_files[lang][split_file.stem].append(path)
                stats[lang][split_file.stem] += len(rows)
            if data_files:
                configs.append({"config_name": f"{lang}.{subject}", "data_files": data_files,
                                "_name": table.get(subject, {}).get("name", subject),
                                "_category": table.get(subject, {}).get("category", "")})
    for lang, splits in per_lang_files.items():
        configs.append({"config_name": lang,
                        "data_files": [{"split": s, "path": sorted(paths)} for s, paths in sorted(splits.items())]})
    write_readme(out_dir, configs, stats, repo_id)
    shutil.copy(CANARY_PATH, out_dir / "CANARY.txt")
    return {"configs": len(configs), "stats": {k: dict(v) for k, v in stats.items()}}


def yaml_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_readme(out_dir: Path, configs: list[dict], stats: dict, repo_id: str | None) -> None:
    langs = sorted({c["config_name"].split(".")[0] for c in configs})
    lines = ["---", "license: cc-by-4.0", "language:"]
    lines += [f"  - {HF_LANG_CODES.get(l, l)}" for l in langs]
    lines += ["task_categories:", "  - question-answering", "  - multiple-choice", "tags:", "  - local-mmlu",
              "  - exam", "  - multilingual", "size_categories:", "  - 1K<n<10K", "pretty_name: Local-MMLU", "configs:"]
    for c in configs:
        lines.append(f"  - config_name: {yaml_quote(c['config_name'])}")
        lines.append("    data_files:")
        for df in c["data_files"]:
            lines.append(f"      - split: {df['split']}")
            if isinstance(df["path"], list):
                lines.append("        path:")
                lines += [f"          - {yaml_quote(p)}" for p in df["path"]]
            else:
                lines.append(f"        path: {yaml_quote(df['path'])}")
    lines.append("---")
    lines += ["", "# Local-MMLU", "",
              "Locally sourced multiple-choice exam questions for evaluating language models, one subject per config.",
              "Built with the tooling in the `local-mmlu` repository. Each item carries its source and licence.", "",
              "## Loading", "", "```python", "from datasets import load_dataset",
              f"ds = load_dataset({yaml_quote(repo_id or 'YOUR_ORG/local-mmlu')}, {yaml_quote(configs[0]['config_name']) if configs else '...'})",
              "```", "", "## Item schema", "",
              "`id, lang, country, subject, category, question, choices (list), answer_index (0-based), explanation, source, source_url, year, license, difficulty, contributed_by, reviewed_by, tags`",
              "", "## Sizes", "", "| lang | train | validation | test |", "|---|---|---|---|"]
    for lang in langs:
        s = stats.get(lang, {})
        lines.append(f"| {lang} ({LANG_NAMES.get(lang, lang)}) | {s.get('train', 0)} | {s.get('validation', 0)} | {s.get('test', 0)} |")
    lines += ["", "## Subjects", "", "| config | name | category |", "|---|---|---|"]
    for c in configs:
        if "_name" in c:
            lines.append(f"| `{c['config_name']}` | {c['_name']} | {c['_category']} |")
    lines += ["", "## Contamination canary", "", "```", canary(), "```", "",
              "If a model can reproduce the string above, it has seen this benchmark during training.", "",
              "## Licence", "",
              "Items are individually licensed; see the `license` field and each `data/{lang}/LICENSES.md` in the source repository.",
              "The compilation is released under CC BY 4.0."]
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "hf")
    ap.add_argument("--include-samples", action="store_true", help="also export sample_* subjects (for pipeline tests)")
    ap.add_argument("--repo-id", help="Hugging Face repo id used in the README loading example")
    args = ap.parse_args(argv)
    result = build(args.out, args.include_samples, args.repo_id)
    print(f"wrote {result['configs']} config(s) to {args.out}")
    for lang, s in sorted(result["stats"].items()):
        print(f"  {lang}: {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
