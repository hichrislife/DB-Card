#!/usr/bin/env python3
"""Generate lm-evaluation-harness task YAMLs, one per {lang}.{subject}, plus group files per language.

    python lm_eval/generate_tasks.py --hf-repo your-org/local-mmlu           # load from the Hub
    python lm_eval/generate_tasks.py --local out/hf                          # load the parquet files built by build_hf.py
    lm_eval --model hf --model_args pretrained=... --tasks local_mmlu_ja --include_path lm_eval/tasks

Each task: 5-shot from the subject's train split, multiple_choice output, acc + acc_norm.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from common import DATA_DIR, is_sample_subject, languages, load_prompt, load_subject_table, subjects_of  # noqa: E402

HERE = Path(__file__).resolve().parent


def ident(s: str) -> str:
    return re.sub(r"[^0-9a-zA-Z_]", "_", s)


def yq(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def task_yaml(lang: str, subject: str, subject_name: str, source: dict, prompt: dict) -> str:
    name = f"local_mmlu_{ident(lang)}_{subject}"
    lines = [f"task: {name}", f"tag:", f"  - local_mmlu_{ident(lang)}"]
    if "hf_repo" in source:
        lines += [f"dataset_path: {yq(source['hf_repo'])}", f"dataset_name: {yq(f'{lang}.{subject}')}"]
    else:
        base = Path(source["local"]) / "data" / lang / subject
        files = {p.stem: str(p.resolve()) for p in base.glob("*.parquet")} or {p.stem: str(p.resolve()) for p in base.glob("*.jsonl")}
        fmt = "parquet" if any(f.endswith(".parquet") for f in files.values()) else "json"
        lines += [f"dataset_path: {fmt}", "dataset_kwargs:", "  data_files:"]
        lines += [f"    {split}: {yq(path)}" for split, path in sorted(files.items())]
    lines += [
        "test_split: test",
        "fewshot_split: train",
        "fewshot_config:",
        "  sampler: first_n",
        "num_fewshot: 5",
        "output_type: multiple_choice",
        f"description: {yq(prompt['instruction'].format(subject=subject_name) + chr(10) + chr(10))}",
        f"doc_to_text: !function utils.doc_to_text_{ident(lang)}",
        "doc_to_choice: !function utils.doc_to_choice",
        "doc_to_target: !function utils.doc_to_target",
        f"target_delimiter: {yq(' ')}",
        f"fewshot_delimiter: {yq(prompt['fewshot_separator'])}",
        "metric_list:",
        "  - metric: acc",
        "    aggregation: mean",
        "    higher_is_better: true",
        "  - metric: acc_norm",
        "    aggregation: mean",
        "    higher_is_better: true",
        "metadata:",
        "  version: 1.0",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--hf-repo", help="Hugging Face dataset repo id")
    src.add_argument("--local", type=Path, help="folder produced by tools/build_hf.py")
    ap.add_argument("--out", type=Path, default=HERE / "tasks")
    ap.add_argument("--include-samples", action="store_true")
    args = ap.parse_args(argv)

    source = {"hf_repo": args.hf_repo} if args.hf_repo else {"local": args.local}
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    shutil.copy(HERE / "utils.py", args.out / "utils.py")
    # utils.py imports tools/common.py relative to the repo; make the copy self-locating.
    (args.out / "utils.py").write_text(
        (HERE / "utils.py").read_text(encoding="utf-8").replace(
            'Path(__file__).resolve().parent.parent / "tools"', f'Path({str((HERE.parent / "tools").resolve())!r})'
        ),
        encoding="utf-8",
    )

    groups: dict[str, list[str]] = defaultdict(list)
    n = 0
    for lang in languages():
        prompt = load_prompt(lang)
        table = load_subject_table(lang)
        for subject in subjects_of(lang):
            if is_sample_subject(subject) and not args.include_samples:
                continue
            if "local" in source and not (Path(source["local"]) / "data" / lang / subject).exists():
                continue
            name = table.get(subject, {}).get("name", subject.replace("_", " "))
            task_name = f"local_mmlu_{ident(lang)}_{subject}"
            (args.out / f"{task_name}.yaml").write_text(task_yaml(lang, subject, name, source, prompt), encoding="utf-8")
            groups[lang].append(task_name)
            n += 1
    for lang, tasks in groups.items():
        g = [f"group: local_mmlu_{ident(lang)}", "task:"] + [f"  - {t}" for t in tasks]
        g += ["aggregate_metric_list:", "  - metric: acc", "    aggregation: mean", "    weight_by_size: true",
              "  - metric: acc_norm", "    aggregation: mean", "    weight_by_size: true", "metadata:", "  version: 1.0"]
        (args.out / f"_local_mmlu_{ident(lang)}.yaml").write_text("\n".join(g) + "\n", encoding="utf-8")
    print(f"wrote {n} task(s) and {len(groups)} group(s) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
