"""Tests for the local-mmlu tooling. Run with: pytest tests/"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import common  # noqa: E402
import validate  # noqa: E402

LANGS = ["zh-TW", "ja", "ko", "en", "nl", "de", "it"]


def test_repo_data_is_valid():
    rep = validate.validate(strict=False)
    assert rep.errors == []


def test_every_language_has_prompt_subjects_and_licenses():
    for lang in LANGS:
        assert (ROOT / "prompts" / f"{lang}.yaml").exists(), lang
        assert (ROOT / "subjects" / f"{lang}.tsv").exists(), lang
        assert (ROOT / "data" / lang / "LICENSES.md").exists(), lang


@pytest.mark.parametrize("lang", LANGS)
def test_prompt_roundtrip(lang):
    """Formatting a question and parsing the model's echo of the answer must agree."""
    prompt = common.load_prompt(lang)
    row = {"question": "Q?", "choices": ["x", "y", "z", "w"], "answer_index": 2}
    rendered = common.format_question(row, prompt, include_answer=True)
    assert rendered.endswith(prompt["answer_prefix"] + "C")
    assert "A. x" in rendered and "D. w" in rendered
    # the CoT suffix tells the model which sentence to end with; that sentence must be parseable
    sentence = prompt["cot_suffix"].split("「")[-1].split("\"")[-2] if "\"" in prompt["cot_suffix"] else prompt["cot_suffix"].split("「")[-1].split("」")[0]
    sentence = sentence.replace("X", "B")
    assert common.extract_answer("blah blah. " + sentence, prompt) == "B", (lang, sentence)


def test_extract_answer_fallback_lone_letter():
    prompt = common.load_prompt("en")
    assert common.extract_answer(" (D) ", prompt) == "D"
    assert common.extract_answer("I do not know", prompt) is None


def _write_item(path: Path, **over):
    item = {"id": "en-sample_general_knowledge-00099", "lang": "en", "country": "GB", "subject": "sample_general_knowledge",
            "category": "other", "question": "Which?", "choices": ["a", "b", "c", "d"], "answer_index": 0,
            "explanation": None, "source": "test", "source_url": None, "year": None, "license": "CC-BY-4.0",
            "difficulty": None, "contributed_by": None, "reviewed_by": [], "tags": []}
    item.update(over)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point the validator at a scratch data dir that reuses the real schema/subjects/prompts."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(common, "DATA_DIR", data)
    monkeypatch.setattr(validate, "DATA_DIR", data)
    monkeypatch.setattr(validate, "ROOT", tmp_path)
    return data


def test_validator_catches_bad_answer_index_and_license(sandbox):
    p = sandbox / "en" / "sample_general_knowledge" / "test.jsonl"
    _write_item(p, answer_index=7)
    _write_item(p, id="en-sample_general_knowledge-00100", license="CC-BY-NC-4.0")
    rep = validate.validate()
    msgs = "\n".join(rep.errors)
    assert "out of range" in msgs
    assert "non-commercial license" in msgs


def test_validator_catches_duplicate_ids_and_questions(sandbox):
    p = sandbox / "en" / "sample_general_knowledge" / "test.jsonl"
    _write_item(p)
    _write_item(p, question="Which ?")  # same id, same normalised text
    rep = validate.validate()
    msgs = "\n".join(rep.errors)
    assert "duplicate id" in msgs
    assert "duplicate question text" in msgs


def test_validator_catches_wrong_directory(sandbox):
    p = sandbox / "ja" / "sample_general_knowledge" / "test.jsonl"
    _write_item(p)  # lang=en inside data/ja
    rep = validate.validate()
    assert any("but file is under data/ja/" in e for e in rep.errors)


def test_validator_strict_requires_reviewers_and_sizes(sandbox):
    p = sandbox / "en" / "chemistry" / "test.jsonl"
    _write_item(p, id="en-chemistry-00000", subject="chemistry", category="STEM", reviewed_by=["one"])
    rep = validate.validate(strict=True)
    msgs = "\n".join(rep.errors)
    assert "distinct reviewers" in msgs
    assert "need >= 100" in msgs
    assert "need >= 5" in msgs
    rep2 = validate.validate(strict=False)
    assert not any("need >=" in e for e in rep2.errors)
    assert any("need >= 100" in w for w in rep2.warnings)


def test_csv2jsonl_roundtrip(tmp_path, monkeypatch):
    import csv2jsonl

    data = tmp_path / "data"
    monkeypatch.setattr(csv2jsonl, "DATA_DIR", data)
    csv_path = tmp_path / "in.csv"
    csv_path.write_text(
        "lang,country,subject,category,question,choice_a,choice_b,choice_c,choice_d,choice_e,answer,explanation,source,source_url,year,license,difficulty,contributed_by\n"
        'ja,JP,civil_law,humanities,"Q1",a,b,c,d,e,E,,"src",,2022,government-work,professional,me\n'
        'ja,JP,civil_law,humanities,"Q2",a,b,c,,,B,"why","src",,,CC-BY-4.0,,\n',
        encoding="utf-8",
    )
    assert csv2jsonl.main([str(csv_path), "--split", "test"]) == 0
    rows = [o for _, o, _ in common.read_jsonl(data / "ja" / "civil_law" / "test.jsonl")]
    assert [r["id"] for r in rows] == ["ja-civil_law-00000", "ja-civil_law-00001"]
    assert rows[0]["answer_index"] == 4 and len(rows[0]["choices"]) == 5
    assert rows[1]["answer_index"] == 1 and len(rows[1]["choices"]) == 3 and rows[1]["year"] is None
    # refuses to overwrite, appends with --append and continues numbering
    assert csv2jsonl.main([str(csv_path), "--split", "test"]) == 1
    assert csv2jsonl.main([str(csv_path), "--split", "test", "--append"]) == 0
    rows = [o for _, o, _ in common.read_jsonl(data / "ja" / "civil_law" / "test.jsonl")]
    assert rows[-1]["id"] == "ja-civil_law-00003"


def test_build_and_generate_tasks(tmp_path):
    out = tmp_path / "hf"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "build_hf.py"), "--out", str(out), "--include-samples"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert 'config_name: "ja.sample_general_knowledge"' in readme
    assert common.canary() in readme
    assert (out / "data" / "ja" / "sample_general_knowledge").exists()

    tasks = tmp_path / "tasks"
    r = subprocess.run([sys.executable, str(ROOT / "lm_eval" / "generate_tasks.py"), "--local", str(out),
                        "--include-samples", "--out", str(tasks)], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    yaml_files = sorted(p.name for p in tasks.glob("*.yaml"))
    assert "local_mmlu_zh_TW_sample_general_knowledge.yaml" in yaml_files
    assert "_local_mmlu_ja.yaml" in yaml_files
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_constructor("!function", lambda loader, node: node.value)
    for p in tasks.glob("*.yaml"):
        doc = yaml.load(p.read_text(encoding="utf-8"), Loader=Loader)
        assert doc
    # the copied utils module must import and expose one doc_to_text per language
    spec = __import__("importlib.util").util.spec_from_file_location("utils_copy", tasks / "utils.py")
    mod = __import__("importlib.util").util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.doc_to_text_zh_TW({"question": "q", "choices": ["1", "2"]}).endswith("答案：")
    assert mod.doc_to_choice({"choices": ["1", "2", "3", "4", "5"]}) == ["A", "B", "C", "D", "E"]


def test_decontaminate_flags_overlap(tmp_path):
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("Which river flows through London? Severn Thames Trent Mersey and more words here\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "decontaminate.py"), "--corpus", str(corpus), "--lang", "en", "--ngram", "5"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 2
    assert "en-sample_general_knowledge-00000" in r.stdout
