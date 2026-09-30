"""從本機裁判書庫匯出 DeepForge 可用的訓練語料（JSONL，{"text": ...}）。

來源必須是自行下載的司法院開放資料；透過 tlr.dr-legal.com.tw 取得的內容依其服務條款
不得用於訓練，因此本工具只讀本機資料庫。
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

from . import db
from .pii import scrub

# 刑事判決多用「理由」、民事多用「事實及理由」；找不到時退回全文。
_REASON_RE = re.compile(r"\n\s*(?:事\s*實\s*及\s*)?理\s*由\s*\n")


def reasoning_section(body: str) -> str:
    m = _REASON_RE.search(body)
    return body[m.end():] if m else body


def export_training(conn: sqlite3.Connection, out: Path, *, category: str | None = None,
                    court: str | None = None, year_from: int | None = None,
                    min_chars: int = 500, max_chars: int = 16_000,
                    reasoning_only: bool = True, limit: int = 0) -> dict:
    sql, args = "SELECT * FROM judgments WHERE 1=1", []
    if category:
        sql += " AND category = ?"
        args.append(category)
    if court:
        sql += " AND court LIKE ?"
        args.append(f"%{court}%")
    if year_from:
        sql += " AND CAST(jyear AS INTEGER) >= ?"
        args.append(year_from)
    sql += " ORDER BY id"
    stats = {"written": 0, "skipped_short": 0, "pii_hits": {}}
    with out.open("w", encoding="utf-8") as f:
        for row in conn.execute(sql, args):
            body = db.judgment_body(row)
            text = reasoning_section(body) if reasoning_only else body
            text = text.strip()
            if len(text) < min_chars:
                stats["skipped_short"] += 1
                continue
            text, hits = scrub(text[:max_chars])
            for k, v in hits.items():
                stats["pii_hits"][k] = stats["pii_hits"].get(k, 0) + v
            f.write(json.dumps({"text": f"{row['citation_text']}\n\n{text}",
                                "doc_id": row["jid"]}, ensure_ascii=False) + "\n")
            stats["written"] += 1
            if limit and stats["written"] >= limit:
                break
    print(f"匯出 {stats['written']} 筆 → {out}", file=sys.stderr)
    return stats
