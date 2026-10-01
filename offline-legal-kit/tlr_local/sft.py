"""把團隊過去的書狀拆成「依前文撰寫下一段」的訓練樣本，用來學事務所的寫法（不是記內容）。

每份書狀依章節標題（壹、貳…／一、二…／docx 標題樣式）切段，每段產生一筆：
    user：書狀類型＋前文摘錄＋要寫的段落標題
    assistant：該段內文
輸出為 chat messages 格式 JSONL，多數 QLoRA 訓練工具可直接讀取；並依檔名雜湊固定切出驗收集。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from .docs import extract, iter_files
from .pii import scrub

SYSTEM_PROMPT = "你是本所的書狀撰寫助理，請依本所慣用的格式、用語與論證順序撰寫。"

_TOP_HEADING_RES = [
    re.compile(r"^[壹貳參肆伍陸柒捌玖拾]+\s*[、.．]"),
    re.compile(r"^[一二三四五六七八九十]+\s*[、.．]"),
]
_DOC_TYPE_RE = re.compile(r"(民事|刑事|行政|家事)?\S{0,8}狀")
PREV_CHARS = 600


def _is_heading_style(style: str) -> bool:
    s = style.lower()
    return s.startswith("heading") or "標題" in style


def split_sections(paragraphs: list[tuple[str, str]]) -> tuple[list[str], list[tuple[str, str]]]:
    """回傳 (章節前的段落, [(標題, 內文)])。

    依序嘗試「壹、」→「一、」→ docx 標題樣式，取第一個切得出兩段以上的層級。
    """
    candidates = [lambda st, t, r=r: bool(r.match(t)) for r in _TOP_HEADING_RES]
    candidates.append(lambda st, t: _is_heading_style(st))
    for is_head in candidates:
        heads = [i for i, (st, t) in enumerate(paragraphs) if is_head(st, t) and len(t) <= 60]
        if len(heads) < 2:
            continue
        preamble = [t for _, t in paragraphs[: heads[0]]]
        sections = []
        for k, i in enumerate(heads):
            end = heads[k + 1] if k + 1 < len(heads) else len(paragraphs)
            body = "\n".join(t for _, t in paragraphs[i + 1 : end]).strip()
            sections.append((paragraphs[i][1].strip(), body))
        return preamble, sections
    return [t for _, t in paragraphs], []


def _paragraphs_for(path: Path) -> list[tuple[str, str]]:
    ex = extract(path, ocr=True)
    if ex.paragraphs:
        return ex.paragraphs
    text = "\n".join(t for _, t in ex.pages)
    return [("", line.strip()) for line in text.splitlines() if line.strip()]


def _doc_type(preamble: list[str], path: Path) -> str:
    for line in preamble[:8]:
        m = _DOC_TYPE_RE.search(line.replace(" ", "").replace("　", ""))
        if m:
            return m.group(0)
    return path.stem


def _is_holdout(path: Path, ratio: float) -> bool:
    if ratio <= 0:
        return False
    h = int(hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:8], 16)
    return (h % 10_000) / 10_000 < ratio


def export_sft(paths: list[Path], out: Path, *, team: str, scrub_pii: bool = True,
               holdout: float = 0.1, min_chars: int = 30, max_chars: int = 4000) -> dict:
    holdout_path = out.with_name(out.stem + ".holdout" + out.suffix)
    stats = {"files": 0, "train": 0, "holdout": 0, "skipped_files": [], "pii_hits": {}}
    with out.open("w", encoding="utf-8") as f_train, holdout_path.open("w", encoding="utf-8") as f_hold:
        for path in iter_files(paths):
            try:
                paras = _paragraphs_for(path)
            except Exception as exc:
                stats["skipped_files"].append(f"{path}：{exc}")
                continue
            preamble, sections = split_sections(paras)
            if not sections:
                stats["skipped_files"].append(f"{path}：找不到章節標題")
                continue
            stats["files"] += 1
            doc_type = _doc_type(preamble, path)
            target = f_hold if _is_holdout(path, holdout) else f_train
            prev = ""
            for heading, body in sections:
                if min_chars <= len(body) <= max_chars:
                    user = (f"書狀類型：{doc_type}\n"
                            f"前文摘錄：{prev[-PREV_CHARS:] or '（本段為第一段）'}\n"
                            f"請撰寫「{heading}」段落。")
                    assistant = body
                    if scrub_pii:
                        user, h1 = scrub(user)
                        assistant, h2 = scrub(assistant)
                        for hits in (h1, h2):
                            for k, v in hits.items():
                                stats["pii_hits"][k] = stats["pii_hits"].get(k, 0) + v
                    rec = {"messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                        {"role": "user", "content": user},
                                        {"role": "assistant", "content": assistant}],
                           "team": team, "source": path.name}
                    target.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    stats["holdout" if target is f_hold else "train"] += 1
                prev += f"\n{heading}\n{body}"
    print(f"[{team}] 訓練 {stats['train']} 筆 → {out}；驗收 {stats['holdout']} 筆 → {holdout_path}",
          file=sys.stderr)
    return stats
