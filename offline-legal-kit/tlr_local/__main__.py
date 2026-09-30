"""python -m tlr_local <指令>"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import db, export, ingest, server

DEFAULT_DB = os.environ.get("TLR_LOCAL_DB", "tlr_local.sqlite")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tlr_local", description="twlegalrag 相容的本機離線檢索服務")
    p.add_argument("--db", default=DEFAULT_DB, help=f"SQLite 路徑（預設 {DEFAULT_DB}，或設 TLR_LOCAL_DB）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="啟動 HTTP 服務")
    s.add_argument("--host", default="127.0.0.1", help="預設只綁本機；非必要不要改成 0.0.0.0")
    s.add_argument("--port", type=int, default=8787)

    j = sub.add_parser("ingest-judgments", help="匯入司法院裁判書開放資料（JSON 資料夾或 zip）")
    j.add_argument("paths", nargs="+", type=Path)
    j.add_argument("--index-chars", type=int, default=0,
                   help="每篇只索引前 N 字以節省空間（0 = 全文）")

    law = sub.add_parser("ingest-laws", help="匯入全國法規資料庫 ChLaw.json / ChOrder.json（或 zip）")
    law.add_argument("paths", nargs="+", type=Path)

    r = sub.add_parser("ingest-refs", help="匯入函釋 JSONL")
    r.add_argument("paths", nargs="+", type=Path)

    e = sub.add_parser("export-training", help="匯出 DeepForge 訓練語料 JSONL（已遮蔽個資）")
    e.add_argument("out", type=Path)
    e.add_argument("--category", help="民事 / 刑事 / 行政 …")
    e.add_argument("--court", help="法院名稱關鍵字，例如 最高法院")
    e.add_argument("--year-from", type=int, help="民國年，例如 105")
    e.add_argument("--min-chars", type=int, default=500)
    e.add_argument("--max-chars", type=int, default=16_000)
    e.add_argument("--full", action="store_true", help="輸出全文而非只輸出理由段")
    e.add_argument("--limit", type=int, default=0)

    sub.add_parser("stats", help="顯示資料庫筆數")

    a = p.parse_args(argv)
    if a.cmd == "serve":
        server.serve(a.db, a.host, a.port)
        return 0
    conn = db.connect(a.db)
    if a.cmd == "ingest-judgments":
        ok, bad = ingest.ingest_judgments(conn, a.paths, index_chars=a.index_chars)
        print(f"裁判書：匯入 {ok} 筆，略過 {bad} 筆")
    elif a.cmd == "ingest-laws":
        n, arts = ingest.ingest_laws(conn, a.paths)
        print(f"法規：{n} 部，條文 {arts} 條")
    elif a.cmd == "ingest-refs":
        ok, bad = ingest.ingest_refs(conn, a.paths)
        print(f"函釋：匯入 {ok} 筆，略過 {bad} 筆")
    elif a.cmd == "export-training":
        stats = export.export_training(
            conn, a.out, category=a.category, court=a.court, year_from=a.year_from,
            min_chars=a.min_chars, max_chars=a.max_chars, reasoning_only=not a.full, limit=a.limit)
        print(json.dumps(stats, ensure_ascii=False))
    elif a.cmd == "stats":
        from .service import health
        print(health(conn)["retrieval"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
