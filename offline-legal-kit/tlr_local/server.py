"""TLR 相容的本機 HTTP 服務（只用標準函式庫）。

把 twlegalrag 指向這裡即可離線使用：
    export TWLEGALRAG_TLR_BASE_URL=http://127.0.0.1:8787
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db, service


class _Handler(BaseHTTPRequestHandler):
    conn = None  # 由 make_server 設定
    lock = threading.Lock()

    def log_message(self, fmt, *args):  # 只記錄到 stderr，不寫查詢內容以外的東西
        sys.stderr.write("tlr-local %s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/health":
            with self.lock:
                self._send(200, service.health(self.conn))
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"detail": "invalid JSON body"})
            return
        path = self.path.rstrip("/")
        c = self.conn
        try:
            with self.lock:
                if path == "/v1/search":
                    if not (req.get("query") or "").strip():
                        self._send(422, {"detail": "query is required"})
                        return
                    out = service.search(c, req["query"], req.get("search_type", "hybrid"),
                                         req.get("max_results", 5))
                elif path == "/v1/fulltext":
                    out = service.fulltext(c, req.get("doc_id", ""), req.get("excerpt_offset", 0))
                    if out is None:
                        self._send(404, {"detail": f"doc_id not found: {req.get('doc_id')}"})
                        return
                elif path == "/v1/law_article":
                    out = service.law_article(c, req.get("law_name", ""), str(req.get("article_no", "")))
                elif path == "/v1/legal_reference":
                    out = service.legal_reference(c, req.get("serial", ""), req.get("authority"))
                elif path == "/v1/legal_references/search":
                    out = service.search_legal_references(
                        c, req.get("query", ""), req.get("authority"),
                        req.get("source_kind"), req.get("max_results", 5))
                else:
                    self._send(404, {"detail": "not found"})
                    return
        except Exception as exc:  # 單一請求失敗不要讓服務掛掉
            self._send(500, {"detail": f"{type(exc).__name__}: {exc}"})
            return
        self._send(200, out)


def make_server(db_path: str, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    _Handler.conn = db.connect(db_path)
    return ThreadingHTTPServer((host, port), _Handler)


def serve(db_path: str, host: str, port: int) -> None:
    srv = make_server(db_path, host, port)
    print(f"tlr-local 服務啟動：http://{host}:{port}（資料庫 {db_path}）", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
