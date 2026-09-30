"""SQLite 資料庫：結構定義與裁判書欄位解析。

全文以 zlib 壓縮存放；FTS5 索引為 contentless，只存 bigram token，不重複存全文。
"""

from __future__ import annotations

import json
import re
import sqlite3
import zlib
from pathlib import Path

from .textindex import normalize, tokenize

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS judgments (
    id INTEGER PRIMARY KEY,
    jid TEXT UNIQUE NOT NULL,
    court TEXT, category TEXT,
    jyear TEXT, jcase TEXT, jcase_norm TEXT, jno TEXT,
    jdate TEXT, title TEXT,
    citation_text TEXT,
    body BLOB, body_chars INTEGER,
    cited_articles TEXT
);
CREATE INDEX IF NOT EXISTS judgments_docket ON judgments (jyear, jcase_norm, jno);

CREATE TABLE IF NOT EXISTS laws (
    id INTEGER PRIMARY KEY,
    law_name TEXT UNIQUE NOT NULL, law_name_norm TEXT,
    law_level TEXT, law_url TEXT, law_modified_date TEXT, abolished INTEGER
);
CREATE TABLE IF NOT EXISTS law_articles (
    law_id INTEGER NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
    article_key TEXT NOT NULL, article_label TEXT, article_content TEXT,
    PRIMARY KEY (law_id, article_key)
);

CREATE TABLE IF NOT EXISTS refs (
    id INTEGER PRIMARY KEY,
    serial_norm TEXT NOT NULL,
    authority TEXT, serial_no TEXT, title TEXT, issue_date TEXT,
    status TEXT, superseded_by TEXT, status_effective_at TEXT,
    source_url TEXT, source_kind TEXT, last_verified_at TEXT, fulltext TEXT,
    UNIQUE (authority, serial_norm)
);
CREATE INDEX IF NOT EXISTS refs_serial ON refs (serial_norm);
"""

_FTS = {
    "judgments_fts": "CREATE VIRTUAL TABLE judgments_fts USING fts5(tok, content='', contentless_delete=1)",
    "refs_fts": "CREATE VIRTUAL TABLE refs_fts USING fts5(tok, content='', contentless_delete=1)",
}


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    for name, ddl in _FTS.items():
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ?", (name,)
        ).fetchone()
        if not exists:
            conn.execute(ddl)
    return conn


# ── 裁判書欄位解析 ────────────────────────────────────────────────────────

_HEADER_RE = re.compile(
    r"^\s*(\S{2,20}?(?:法院(?:\S{1,4}分院)?|法庭|委員會))\s*(?:\S{0,8}?)"
    r"(民事|刑事|行政|家事|少年|懲戒|憲法|智慧財產)?\s*(?:\S{0,6}?)(判決|裁定|判例|決議)"
)
# 先定位「第N條」（前一字須為 法/例/則），再往回取法規名稱，避免在長中文段落上回溯。
_ARTICLE_RE = re.compile(r"(?<=[法例則])第\s*(\d+)\s*條(?:之\s*(\d+))?")
_NAME_TAIL_RE = re.compile(r"[一-鿿]{2,15}$")
# 法規名稱前常見的引導詞，取最後一個之後的部分當名稱。
_NAME_LEAD_RE = re.compile(r"條及|條與|條、|項及|款及|準用|適用|參照|違反|前開|上開|依|按|並|即|於|係|另")
_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_jcase(jcase: str) -> str:
    return normalize(jcase).replace(" ", "")


def parse_header(full: str) -> tuple[str, str]:
    """從全文開頭抓「法院名」與「民事/刑事…判決/裁定」。抓不到回傳空字串。"""
    m = _HEADER_RE.search(full[:120].replace("　", " "))
    if not m:
        return "", ""
    return m.group(1), (m.group(2) or "") + m.group(3)


def extract_cited_articles(full: str, limit: int = 30) -> list[str]:
    """啟發式抽取「某某法第N條」，只當列表資訊，不保證完整或精確。"""
    text = full.translate(_DIGITS)
    seen: list[str] = []
    for m in _ARTICLE_RE.finditer(text):
        tail = _NAME_TAIL_RE.search(text, max(0, m.start() - 15), m.start())
        if not tail:
            continue
        law = tail.group()
        leads = list(_NAME_LEAD_RE.finditer(law))
        if leads:
            law = law[leads[-1].end():]
        if len(law) < 2:
            continue
        no, sub = m.groups()
        s = f"{law}第{no}條" + (f"之{sub}" if sub else "")
        if s not in seen:
            seen.append(s)
            if len(seen) >= limit:
                break
    return seen


def citation_text_for(court: str, jyear: str, jcase: str, jno: str, kind: str) -> str:
    return f"{court} {jyear} 年度{jcase}字第 {jno} 號{kind}".strip()


def upsert_judgment(conn: sqlite3.Connection, rec: dict, *, index_chars: int = 0) -> bool:
    """寫入一筆司法院開放資料格式的裁判書（JID/JYEAR/JCASE/JNO/JDATE/JTITLE/JFULL）。"""
    jid = (rec.get("JID") or "").strip()
    full = (rec.get("JFULL") or "").replace("\r\n", "\n").replace("\r", "\n")
    if not jid or not full.strip():
        return False
    parts = [p.strip() for p in jid.split(",")]
    jyear = str(rec.get("JYEAR") or (parts[1] if len(parts) > 1 else "")).strip()
    jcase = str(rec.get("JCASE") or (parts[2] if len(parts) > 2 else "")).strip()
    jno = str(rec.get("JNO") or (parts[3] if len(parts) > 3 else "")).strip()
    jdate = str(rec.get("JDATE") or (parts[4] if len(parts) > 4 else "")).strip()
    title = (rec.get("JTITLE") or "").strip()
    court, kind = parse_header(full)
    court = court or parts[0]
    category = kind[:2] if kind[:2] in ("民事", "刑事", "行政", "家事", "少年", "懲戒", "憲法") else ""
    citation = citation_text_for(court, jyear, jcase, jno, kind)
    cited = extract_cited_articles(full)

    old = conn.execute("SELECT id FROM judgments WHERE jid = ?", (jid,)).fetchone()
    if old:
        conn.execute("DELETE FROM judgments_fts WHERE rowid = ?", (old["id"],))
        conn.execute("DELETE FROM judgments WHERE id = ?", (old["id"],))
    cur = conn.execute(
        """INSERT INTO judgments (jid, court, category, jyear, jcase, jcase_norm, jno,
               jdate, title, citation_text, body, body_chars, cited_articles)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            jid, court, category, jyear, jcase, normalize_jcase(jcase), jno, jdate, title,
            citation, zlib.compress(full.encode("utf-8"), 6), len(full),
            json.dumps(cited, ensure_ascii=False),
        ),
    )
    indexed = full if index_chars <= 0 else full[:index_chars]
    conn.execute(
        "INSERT INTO judgments_fts (rowid, tok) VALUES (?, ?)",
        (cur.lastrowid, tokenize(title + "\n" + indexed)),
    )
    return True


def judgment_body(row: sqlite3.Row) -> str:
    return zlib.decompress(row["body"]).decode("utf-8")


# ── 法條 ──────────────────────────────────────────────────────────────────

_CN_NUM = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}


def _cn_to_int(s: str) -> int | None:
    if not s or any(c not in _CN_NUM and c not in _CN_UNIT for c in s):
        return None
    total, cur = 0, 0
    for c in s:
        if c in _CN_NUM:
            cur = _CN_NUM[c]
        else:
            total += (cur or 1) * _CN_UNIT[c]
            cur = 0
    return total + cur


def article_key(raw: str) -> str | None:
    """「第 184-1 條」「184之1」「第一百八十四條」→ "184-1" / "184"。"""
    s = normalize(raw).replace(" ", "").strip("第條")
    s = s.replace("條之", "-").replace("之", "-")
    pieces = s.split("-")
    out = []
    for p in pieces:
        p = p.strip("第條")
        if p.isdigit():
            out.append(str(int(p)))
        else:
            n = _cn_to_int(p)
            if n is None:
                return None
            out.append(str(n))
    return "-".join(out) if out else None


def law_name_norm(name: str) -> str:
    return normalize(name).replace(" ", "")
