"""本機檢索邏輯，回應格式對齊 twlegalrag v2.3 client 讀取的欄位。"""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse

from . import db
from .textindex import build_match, normalize, query_terms, tokenize

WINDOW_CHARS = 30_000
REF_FULLTEXT_CHARS = 20_000

NOTE_LOCAL = (
    "本機離線資料庫：未收錄審級歷程（case_history），不得據此推論判決已確定；"
    "引用前請核對司法院裁判書系統原文。"
)
NOTE_LAW_CURRENT = "僅為匯入時之現行條文，不得用以認定行為時法。"

_DOCKET_RE = re.compile(
    r"(\d{2,3})\s*年度?\s*([一-鿿]{1,8}?)\s*字\s*第\s*(\d+)\s*號"
)


def _judgment_url(jid: str) -> str:
    return "https://judgment.judicial.gov.tw/FJUD/data.aspx?ty=JD&id=" + urllib.parse.quote(jid)


def _hit_excerpt(body: str, terms: list[str], width: int = 160) -> str | None:
    norm = normalize(body)
    for t in terms:
        pos = norm.find(t.replace(" ", ""))
        if pos >= 0:
            start = max(0, pos - width // 2)
            return body[start : start + width].replace("\n", " ").strip()
    return None


def _listing(row: sqlite3.Row) -> str:
    cited = json.loads(row["cited_articles"] or "[]")[:5]
    parts = [row["court"], row["category"], row["jdate"], row["title"]]
    line = " | ".join(p for p in parts if p)
    return line + (f" | 引用: {'、'.join(cited)}" if cited else "")


def _result(row: sqlite3.Row, rank: int, terms: list[str]) -> dict:
    url = _judgment_url(row["jid"])
    return {
        "rank": rank,
        "doc_id": row["jid"],
        "citation_text": row["citation_text"],
        "court_name": row["court"],
        "jdate": row["jdate"],
        "snippet": _listing(row),
        "citation_url": url,
        "citation_markdown": f"[{row['citation_text']}]({url})",
        "result_token": "local",
        "case_category": row["category"] or None,
        "hit_excerpt": _hit_excerpt(db.judgment_body(row), terms),
    }


def _fts_ids(conn: sqlite3.Connection, table: str, match: str, limit: int) -> list[int]:
    try:
        rows = conn.execute(
            f"SELECT rowid FROM {table} WHERE {table} MATCH ? ORDER BY bm25({table}) LIMIT ?",
            (match, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [r[0] for r in rows]


def search(conn: sqlite3.Connection, query: str, search_type: str, max_results: int) -> dict:
    n = max(1, min(int(max_results), 10))
    terms = query_terms(query)
    ids: list[int] = []
    notes: list[str] = []

    # 精確案號：查詢含「112年度台上字第1234號」時先直接比對字號。
    for y, c, no in _DOCKET_RE.findall(normalize(query)):
        for r in conn.execute(
            "SELECT id FROM judgments WHERE jyear = ? AND jcase_norm = ? AND jno = ?",
            (y, db.normalize_jcase(c), no),
        ):
            if r["id"] not in ids:
                ids.append(r["id"])
        if not ids:
            notes.append(f"本機資料庫查無 {y} 年度{c}字第 {no} 號，請勿憑記憶描述該案。")

    if search_type == "phrase":
        modes = ["phrase"]
    elif search_type == "keyword":
        modes = ["keyword"]
    else:
        modes = ["keyword", "any"]
    for mode in modes:
        if len(ids) >= n:
            break
        match = build_match(query, mode)
        if not match:
            continue
        for i in _fts_ids(conn, "judgments_fts", match, n * 2):
            if i not in ids:
                ids.append(i)

    results = []
    for rank, i in enumerate(ids[:n], start=1):
        row = conn.execute("SELECT * FROM judgments WHERE id = ?", (i,)).fetchone()
        if row:
            results.append(_result(row, rank, terms))
    notes.append(NOTE_LOCAL)
    return {"results": results, "note": " ".join(notes)}


def fulltext(conn: sqlite3.Connection, doc_id: str, offset: int = 0) -> dict | None:
    row = conn.execute("SELECT * FROM judgments WHERE jid = ?", (doc_id,)).fetchone()
    if not row:
        return None
    body = db.judgment_body(row)
    offset = max(0, int(offset or 0))
    window = body[offset : offset + WINDOW_CHARS]
    # client 以第一個空行切出標頭，所以標頭本身不能含空行。
    header = row["citation_text"].replace("\n", " ")
    return {
        "doc_id": row["jid"],
        "text_excerpt": header + "\n\n" + window,
        "excerpt_offset": offset,
        "fulltext_truncated": offset + len(window) < len(body),
        "fulltext_total_chars": len(body),
        "cited_articles": json.loads(row["cited_articles"] or "[]"),
        "case_history": None,
    }


def law_article(conn: sqlite3.Connection, law_name: str, article_no: str) -> dict:
    notes = [NOTE_LAW_CURRENT]
    law = conn.execute(
        "SELECT * FROM laws WHERE law_name_norm = ?", (db.law_name_norm(law_name),)
    ).fetchone()
    if not law:
        cands = conn.execute(
            "SELECT law_name, law_level FROM laws WHERE law_name_norm LIKE ? ORDER BY length(law_name) LIMIT 10",
            (f"%{db.law_name_norm(law_name)}%",),
        ).fetchall()
        return {
            "found": False,
            "matches": [],
            "law_candidates": [dict(c) for c in cands],
            "notes": notes + [f"本機法規資料庫無「{law_name}」。"],
        }
    key = db.article_key(article_no)
    art = conn.execute(
        "SELECT * FROM law_articles WHERE law_id = ? AND article_key = ?", (law["id"], key)
    ).fetchone() if key else None
    if not art:
        return {"found": False, "matches": [], "law_candidates": [],
                "notes": notes + [f"{law['law_name']} 無第 {article_no} 條（或已刪除）。"]}
    return {
        "found": True,
        "matches": [{
            "law_name": law["law_name"],
            "article_no": art["article_label"],
            "article_content": art["article_content"],
            "law_level": law["law_level"],
            "law_modified_date": law["law_modified_date"],
            "law_url": law["law_url"],
            "abolished": bool(law["abolished"]),
            "article_content_truncated": False,
        }],
        "notes": notes,
    }


def serial_norm(serial: str) -> str:
    return re.sub(r"[\s第號]", "", normalize(serial))


def _ref_match(row: sqlite3.Row, *, full: bool) -> dict:
    d = {k: row[k] for k in ("authority", "serial_no", "title", "issue_date", "status",
                             "superseded_by", "status_effective_at", "source_url",
                             "source_kind", "last_verified_at")}
    if full:
        text = row["fulltext"] or ""
        d["fulltext"] = text[:REF_FULLTEXT_CHARS]
        d["fulltext_truncated"] = len(text) > REF_FULLTEXT_CHARS
    return d


def legal_reference(conn: sqlite3.Connection, serial: str, authority: str | None) -> dict:
    sql, args = "SELECT * FROM refs WHERE serial_norm = ?", [serial_norm(serial)]
    if authority:
        sql += " AND authority = ?"
        args.append(authority)
    rows = conn.execute(sql, args).fetchall()
    notes = ["效力狀態以匯入時資料為準，請定期更新函釋資料。"]
    if not rows:
        notes.append(f"本機函釋資料庫查無「{serial}」，請勿憑記憶引用。")
    return {"found": bool(rows), "matches": [_ref_match(r, full=True) for r in rows], "notes": notes}


def search_legal_references(conn: sqlite3.Connection, query: str, authority: str | None,
                            source_kind: str | None, max_results: int) -> dict:
    n = max(1, min(int(max_results), 10))
    terms = query_terms(query)
    ids: list[int] = []
    for mode in ("keyword", "any"):
        match = build_match(query, mode)
        if match and len(ids) < n * 3:
            ids += [i for i in _fts_ids(conn, "refs_fts", match, n * 3) if i not in ids]
    results = []
    for i in ids:
        row = conn.execute("SELECT * FROM refs WHERE id = ?", (i,)).fetchone()
        if not row or (authority and row["authority"] != authority) or (
            source_kind and row["source_kind"] != source_kind
        ):
            continue
        text = row["fulltext"] or ""
        results.append({
            "citation": f"{row['authority']} {row['serial_no']}",
            "authority": row["authority"],
            "serial_no": row["serial_no"],
            "status": row["status"],
            "score": round(1.0 - len(results) / (n * 2), 3),
            "title": row["title"],
            "excerpt": _hit_excerpt(text, terms, 200) or text[:200],
        })
        if len(results) >= n:
            break
    return {"results": results, "notes": ["列表僅供定位；引用前請以 ref 指令查驗效力。"]}


def upsert_ref(conn: sqlite3.Connection, rec: dict) -> bool:
    serial = (rec.get("serial_no") or "").strip()
    if not serial:
        return False
    authority = (rec.get("authority") or "").strip()
    sn = serial_norm(serial)
    old = conn.execute("SELECT id FROM refs WHERE authority = ? AND serial_norm = ?",
                       (authority, sn)).fetchone()
    if old:
        conn.execute("DELETE FROM refs_fts WHERE rowid = ?", (old["id"],))
        conn.execute("DELETE FROM refs WHERE id = ?", (old["id"],))
    cur = conn.execute(
        """INSERT INTO refs (serial_norm, authority, serial_no, title, issue_date, status,
               superseded_by, status_effective_at, source_url, source_kind, last_verified_at, fulltext)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (sn, authority, serial, rec.get("title"), rec.get("issue_date"),
         rec.get("status") or "unknown", rec.get("superseded_by"),
         rec.get("status_effective_at"), rec.get("source_url"), rec.get("source_kind"),
         rec.get("last_verified_at"), rec.get("fulltext")),
    )
    conn.execute("INSERT INTO refs_fts (rowid, tok) VALUES (?, ?)",
                 (cur.lastrowid, tokenize(f"{authority} {serial} {rec.get('title') or ''}\n{rec.get('fulltext') or ''}")))
    return True


def health(conn: sqlite3.Connection) -> dict:
    counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("judgments", "laws", "refs")}
    return {
        "status": "ok",
        "retrieval": f"local-sqlite judgments={counts['judgments']} laws={counts['laws']} refs={counts['refs']}",
    }

