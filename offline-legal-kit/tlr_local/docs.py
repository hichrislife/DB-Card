"""使用者自備文件：抽文字、切段、按團隊建立檢索索引。

每個團隊一個獨立 SQLite 檔（<docs_root>/<team>.sqlite），團隊之間的資料在檔案層級就分開；
刪除團隊資料只要刪掉該檔案。

支援格式：.txt / .md / .docx / .pdf；.doc 會在有 LibreOffice 時先轉成 .docx。
PDF 沒有文字層的頁面（掃描檔）：有 tesseract 時以 PyMuPDF 做 OCR，否則列入「需 OCR」清單。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .textindex import build_match, normalize, query_terms, tokenize

SUPPORTED = (".txt", ".md", ".docx", ".doc", ".pdf")
CHUNK_CHARS = 800
CHUNK_OVERLAP = 100
OCR_LANG = "chi_tra+eng"

_TEAM_RE = re.compile(r"[\w-]{1,40}")
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL, sha256 TEXT NOT NULL, title TEXT,
    pages INTEGER, chars INTEGER, ocr_pages TEXT, needs_ocr_pages TEXT, added_at TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq INTEGER, page INTEGER, text TEXT
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks (doc_id);
"""


def team_db_path(root: str | Path, team: str) -> Path:
    if not _TEAM_RE.fullmatch(team or ""):
        raise ValueError(f"團隊名稱只能用文字、數字、底線、連字號（1–40 字）：{team!r}")
    return Path(root) / f"{team}.sqlite"


def connect_team(root: str | Path, team: str) -> sqlite3.Connection:
    path = team_db_path(root, team)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'chunks_fts'").fetchone():
        conn.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(tok, content='', contentless_delete=1)")
    return conn


# ── 抽文字 ────────────────────────────────────────────────────────────────

@dataclass
class Extracted:
    pages: list[tuple[int, str]]                     # (頁碼，從 1 起；非分頁格式為 0)
    ocr_pages: list[int] = field(default_factory=list)
    needs_ocr_pages: list[int] = field(default_factory=list)
    paragraphs: list[tuple[str, str]] = field(default_factory=list)  # docx：(樣式, 文字)


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp950", "big5hkscs"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def docx_paragraphs(path: Path) -> list[tuple[str, str]]:
    """讀 .docx 段落，回傳 (樣式名稱, 文字)。只用標準函式庫。"""
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    out: list[tuple[str, str]] = []
    for p in root.iter(f"{_W}p"):
        style_el = p.find(f"{_W}pPr/{_W}pStyle")
        style = style_el.get(f"{_W}val", "") if style_el is not None else ""
        parts: list[str] = []
        for el in p.iter():
            if el.tag == f"{_W}t":
                parts.append(el.text or "")
            elif el.tag == f"{_W}tab":
                parts.append("\t")
            elif el.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            out.append((style, text))
    return out


def _convert_doc(path: Path, workdir: Path) -> Path | None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    subprocess.run([soffice, "--headless", "--convert-to", "docx", "--outdir", str(workdir), str(path)],
                   capture_output=True, timeout=180, check=False)
    out = workdir / (path.stem + ".docx")
    return out if out.exists() else None


def _pymupdf():
    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return None
    return pymupdf


def _extract_pdf(path: Path, *, ocr: bool) -> Extracted:
    mu = _pymupdf()
    if mu is None:
        if shutil.which("pdftotext"):
            r = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True,
                               timeout=300, check=False)
            pages = r.stdout.decode("utf-8", errors="replace").split("\f")
            ex = Extracted(pages=[(i + 1, t) for i, t in enumerate(pages)])
            ex.needs_ocr_pages = [i for i, t in ex.pages if len(t.strip()) < 20]
            return ex
        raise RuntimeError("讀 PDF 需要 PyMuPDF（pip install pymupdf）或 poppler 的 pdftotext")
    can_ocr = ocr and shutil.which("tesseract") is not None
    ex = Extracted(pages=[])
    with mu.open(str(path)) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text")
            if len(text.strip()) < 20:
                if can_ocr:
                    try:
                        tp = page.get_textpage_ocr(language=OCR_LANG, dpi=300, full=True)
                        text = page.get_text("text", textpage=tp)
                        ex.ocr_pages.append(i)
                    except Exception as exc:  # tesseract 缺語言包等
                        print(f"{path.name} 第 {i} 頁 OCR 失敗：{exc}", file=sys.stderr)
                        ex.needs_ocr_pages.append(i)
                else:
                    ex.needs_ocr_pages.append(i)
            ex.pages.append((i, text))
    return ex


def extract(path: Path, *, ocr: bool = True) -> Extracted:
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        return Extracted(pages=[(0, _read_text_file(path))])
    if suffix == ".docx":
        paras = docx_paragraphs(path)
        return Extracted(pages=[(0, "\n".join(t for _, t in paras))], paragraphs=paras)
    if suffix == ".doc":
        with tempfile.TemporaryDirectory() as tmp:
            converted = _convert_doc(path, Path(tmp))
            if converted is None:
                raise RuntimeError("舊版 .doc 需要 LibreOffice 轉檔（或先另存為 .docx）")
            return extract(converted, ocr=ocr)
    if suffix == ".pdf":
        return _extract_pdf(path, ocr=ocr)
    raise RuntimeError(f"不支援的格式：{suffix}")


# ── 切段 ──────────────────────────────────────────────────────────────────

def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """依段落累積到約 size 字切一段；過長的段落硬切，前後重疊 overlap 字。"""
    text = re.sub(r"[ \t　]+\n", "\n", text.replace("\r\n", "\n"))
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        while len(p) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(p[:size])
            p = p[size - overlap:]
        if len(buf) + len(p) + 1 > size and buf:
            chunks.append(buf)
            buf = buf[-overlap:] + "\n" + p if overlap else p
        else:
            buf = f"{buf}\n{p}" if buf else p
    if buf.strip():
        chunks.append(buf)
    return chunks


# ── 匯入與檢索 ────────────────────────────────────────────────────────────

def iter_files(paths: list[Path]):
    for p in paths:
        if p.is_dir():
            yield from (f for f in sorted(p.rglob("*")) if f.is_file() and f.suffix.lower() in SUPPORTED)
        elif p.suffix.lower() in SUPPORTED:
            yield p


def _title(path: Path, ex: Extracted) -> str:
    for line in (ex.pages[0][1] if ex.pages else "").splitlines():
        line = line.strip()
        if 2 <= len(line) <= 60:
            return line
    return path.stem


def ingest_docs(conn: sqlite3.Connection, paths: list[Path], *, ocr: bool = True) -> dict:
    stats = {"added": 0, "updated": 0, "unchanged": 0, "failed": 0, "chunks": 0,
             "ocr_pages": 0, "needs_ocr": {}}
    for f in iter_files(paths):
        key = str(f.resolve())
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        old = conn.execute("SELECT id, sha256 FROM documents WHERE path = ?", (key,)).fetchone()
        if old and old["sha256"] == digest:
            stats["unchanged"] += 1
            continue
        try:
            ex = extract(f, ocr=ocr)
        except Exception as exc:
            stats["failed"] += 1
            print(f"略過 {f}：{exc}", file=sys.stderr)
            continue
        if old:
            for (cid,) in conn.execute("SELECT id FROM chunks WHERE doc_id = ?", (old["id"],)).fetchall():
                conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
            conn.execute("DELETE FROM documents WHERE id = ?", (old["id"],))
        chars = sum(len(t) for _, t in ex.pages)
        cur = conn.execute(
            """INSERT INTO documents (path, sha256, title, pages, chars, ocr_pages, needs_ocr_pages, added_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (key, digest, _title(f, ex), len(ex.pages), chars,
             ",".join(map(str, ex.ocr_pages)), ",".join(map(str, ex.needs_ocr_pages)),
             time.strftime("%Y-%m-%d %H:%M:%S")),
        )
        seq = 0
        for page, text in ex.pages:
            for piece in chunk_text(text):
                c = conn.execute("INSERT INTO chunks (doc_id, seq, page, text) VALUES (?,?,?,?)",
                                 (cur.lastrowid, seq, page, piece))
                conn.execute("INSERT INTO chunks_fts (rowid, tok) VALUES (?, ?)", (c.lastrowid, tokenize(piece)))
                seq += 1
        stats["updated" if old else "added"] += 1
        stats["chunks"] += seq
        stats["ocr_pages"] += len(ex.ocr_pages)
        if ex.needs_ocr_pages:
            stats["needs_ocr"][str(f)] = ex.needs_ocr_pages
        conn.commit()
    return stats


def search_docs(conn: sqlite3.Connection, query: str, n: int = 5) -> list[dict]:
    terms = query_terms(query)
    ids: list[int] = []
    for mode in ("keyword", "any"):
        match = build_match(query, mode)
        if not match or len(ids) >= n:
            continue
        try:
            rows = conn.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
                (match, n * 2)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        ids += [r[0] for r in rows if r[0] not in ids]
    out = []
    for cid in ids[:n]:
        row = conn.execute(
            """SELECT c.text, c.page, d.title, d.path FROM chunks c JOIN documents d ON d.id = c.doc_id
               WHERE c.id = ?""", (cid,)).fetchone()
        if not row:
            continue
        norm = normalize(row["text"])
        pos = min((norm.find(t) for t in terms if norm.find(t) >= 0), default=0)
        start = max(0, pos - 80)
        out.append({"title": row["title"], "path": row["path"], "page": row["page"],
                    "excerpt": row["text"][start:start + 240].replace("\n", " ")})
    return out
