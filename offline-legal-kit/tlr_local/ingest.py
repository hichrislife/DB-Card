"""匯入工具：司法院裁判書開放資料、全國法規資料庫、函釋 JSONL。

皆可直接吃資料夾或 .zip；司法院每月釋出的 .rar 請先解壓縮。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import zipfile
from pathlib import Path
from typing import Iterator

from . import db, service


def _iter_sources(path: Path, suffixes: tuple[str, ...]) -> Iterator[tuple[str, bytes]]:
    """逐一產出 (名稱, 內容)，支援單檔、資料夾（遞迴）與 zip。"""
    if path.is_dir():
        for p in sorted(path.rglob("*")):
            if p.is_file():
                yield from _iter_sources(p, suffixes)
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for name in sorted(zf.namelist()):
                if name.lower().endswith(suffixes):
                    yield f"{path}!{name}", zf.read(name)
    elif path.suffix.lower() in suffixes:
        yield str(path), path.read_bytes()


def _loads(raw: bytes):
    return json.loads(raw.decode("utf-8-sig"), strict=False)


def ingest_judgments(conn: sqlite3.Connection, paths: list[Path], *, index_chars: int = 0,
                     batch: int = 2000) -> tuple[int, int]:
    ok = bad = 0
    for path in paths:
        for name, raw in _iter_sources(path, (".json",)):
            try:
                data = _loads(raw)
            except (ValueError, UnicodeDecodeError):
                bad += 1
                print(f"略過無法解析的檔案：{name}", file=sys.stderr)
                continue
            for rec in data if isinstance(data, list) else [data]:
                if isinstance(rec, dict) and db.upsert_judgment(conn, rec, index_chars=index_chars):
                    ok += 1
                    if ok % batch == 0:
                        conn.commit()
                        print(f"已匯入 {ok} 筆裁判書", file=sys.stderr)
                else:
                    bad += 1
    conn.commit()
    return ok, bad


def ingest_laws(conn: sqlite3.Connection, paths: list[Path]) -> tuple[int, int]:
    """全國法規資料庫 ChLaw.json / ChOrder.json（{"Laws": [...]}）。"""
    n_laws = n_articles = 0
    for path in paths:
        for _name, raw in _iter_sources(path, (".json",)):
            data = _loads(raw)
            laws = data.get("Laws") if isinstance(data, dict) else data
            for law in laws or []:
                name = (law.get("LawName") or "").strip()
                if not name:
                    continue
                old = conn.execute("SELECT id FROM laws WHERE law_name = ?", (name,)).fetchone()
                if old:
                    conn.execute("DELETE FROM laws WHERE id = ?", (old["id"],))
                cur = conn.execute(
                    """INSERT INTO laws (law_name, law_name_norm, law_level, law_url,
                           law_modified_date, abolished) VALUES (?,?,?,?,?,?)""",
                    (name, db.law_name_norm(name), law.get("LawLevel"), law.get("LawURL"),
                     law.get("LawModifiedDate"), 1 if (law.get("LawAbandonNote") or "").strip() else 0),
                )
                n_laws += 1
                for art in law.get("LawArticles") or []:
                    if (art.get("ArticleType") or "A") != "A":
                        continue  # C = 編章節標題
                    label = (art.get("ArticleNo") or "").strip()
                    key = db.article_key(label)
                    if not key:
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO law_articles VALUES (?,?,?,?)",
                        (cur.lastrowid, key, label, art.get("ArticleContent") or ""),
                    )
                    n_articles += 1
    conn.commit()
    return n_laws, n_articles


def ingest_refs(conn: sqlite3.Connection, paths: list[Path]) -> tuple[int, int]:
    """函釋 JSONL：每行一筆 {authority, serial_no, title, issue_date, status, fulltext, source_url, ...}。"""
    ok = bad = 0
    for path in paths:
        for name, raw in _iter_sources(path, (".jsonl",)):
            for lineno, line in enumerate(raw.decode("utf-8-sig").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line, strict=False)
                except ValueError:
                    bad += 1
                    print(f"{name}:{lineno} JSON 格式錯誤，略過", file=sys.stderr)
                    continue
                if service.upsert_ref(conn, rec):
                    ok += 1
                else:
                    bad += 1
    conn.commit()
    return ok, bad
