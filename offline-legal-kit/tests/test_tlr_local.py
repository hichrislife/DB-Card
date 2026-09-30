"""python -m unittest discover -s tests（在 offline-legal-kit/ 目錄下執行）。

fixtures 內的裁判書為測試用合成資料，非真實案件。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tlr_local import db, export, ingest, service  # noqa: E402
from tlr_local.pii import scrub  # noqa: E402
from tlr_local.server import make_server  # noqa: E402
from tlr_local.textindex import build_match, tokenize  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def _load(conn):
    ingest.ingest_judgments(conn, [FIX / "judgments"])
    ingest.ingest_laws(conn, [FIX / "ChLaw.json"])
    ingest.ingest_refs(conn, [FIX / "refs.jsonl"])


class TextIndexTest(unittest.TestCase):
    def test_bigrams_and_normalization(self):
        self.assertEqual(tokenize("違約金"), "違約 約金")
        self.assertEqual(tokenize("臺上１２３"), "台上 123")
        self.assertEqual(build_match("違約金 過高", "keyword"), '"違約 約金" AND "過高"')
        self.assertIsNone(build_match("的", "keyword"))


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "t.sqlite")
        _load(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_parsed_fields(self):
        row = self.conn.execute("SELECT * FROM judgments WHERE jid LIKE 'TCHM%'").fetchone()
        self.assertEqual(row["court"], "臺灣高等法院臺中分院")
        self.assertEqual(row["category"], "刑事")
        self.assertEqual(row["citation_text"], "臺灣高等法院臺中分院 110 年度上易字第 89 號刑事判決")
        self.assertIn("刑法第339條", json.loads(row["cited_articles"]))

    def test_keyword_search_two_char_terms(self):
        out = service.search(self.conn, "違約金 過高 酌減", "keyword", 5)
        self.assertEqual([r["doc_id"] for r in out["results"]], ["TPSV,112,台上,1234,20230830,1"])
        self.assertIn("過高", out["results"][0]["hit_excerpt"])

    def test_hybrid_falls_back_to_or(self):
        out = service.search(self.conn, "違約金 詐術", "hybrid", 5)
        self.assertEqual(len(out["results"]), 2)

    def test_docket_lookup_normalizes_tai(self):
        out = service.search(self.conn, "最高法院112年度臺上字第1234號", "hybrid", 5)
        self.assertEqual(out["results"][0]["doc_id"], "TPSV,112,台上,1234,20230830,1")
        missing = service.search(self.conn, "112年度台上字第9999號", "hybrid", 5)
        self.assertIn("查無", missing["note"])

    def test_fulltext_paging(self):
        service.WINDOW_CHARS, saved = 50, service.WINDOW_CHARS
        try:
            first = service.fulltext(self.conn, "TPDV,111,訴,567,20221115,2", 0)
            self.assertTrue(first["fulltext_truncated"])
            header, body = first["text_excerpt"].split("\n\n", 1)
            self.assertNotIn("\n\n", header)
            self.assertEqual(len(body), 50)
        finally:
            service.WINDOW_CHARS = saved

    def test_law_article(self):
        self.assertTrue(service.law_article(self.conn, "民法", "184")["found"])
        self.assertTrue(service.law_article(self.conn, "民法", "第191條之2")["found"])
        self.assertTrue(service.law_article(self.conn, "民法", "第二百五十二條")["found"])
        miss = service.law_article(self.conn, "民訴", "277")
        self.assertFalse(miss["found"])
        self.assertEqual(service.law_article(self.conn, "民事訴訟", "1")["law_candidates"][0]["law_name"], "民事訴訟法")

    def test_refs(self):
        r = service.legal_reference(self.conn, "臺財稅第 881945861 號", None)
        self.assertTrue(r["found"])
        self.assertEqual(r["matches"][0]["status"], "active")
        s = service.search_legal_references(self.conn, "管理維護 印花稅", None, None, 5)
        self.assertEqual(s["results"][0]["serial_no"], "台財稅第881945861號")

    def test_reingest_replaces(self):
        ingest.ingest_judgments(self.conn, [FIX / "judgments"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM judgments").fetchone()[0], 3)
        self.assertEqual(len(service.search(self.conn, "違約金", "keyword", 10)["results"]), 1)

    def test_export_scrubs_pii(self):
        out = Path(self.tmp.name) / "train.jsonl"
        stats = export.export_training(self.conn, out, min_chars=10)
        text = out.read_text(encoding="utf-8")
        self.assertEqual(stats["written"], 3)
        for leaked in ("A123456789", "0912-345-678", "012-345678901"):
            self.assertNotIn(leaked, text)
        self.assertNotIn("上列當事人間", text)  # 只輸出理由段


class PiiTest(unittest.TestCase):
    def test_does_not_touch_money_or_docket(self):
        s, hits = scrub("新臺幣30萬元，112年度台上字第1234號，電話(02)2311-1234")
        self.assertIn("112年度台上字第1234號", s)
        self.assertEqual(hits, {"市話": 1})


class HttpCompatTest(unittest.TestCase):
    """用真正的 twlegalrag client 打本機服務；沒裝 twlegalrag 時跳過。"""

    @classmethod
    def setUpClass(cls):
        cls.exe = shutil.which("twlegalrag")
        if not cls.exe:
            raise unittest.SkipTest("twlegalrag 未安裝")
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "t.sqlite"
        _load(db.connect(path))
        cls.srv = make_server(str(path), "127.0.0.1", 0)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def run_cli(self, *args):
        env = {**os.environ, "TWLEGALRAG_TLR_BASE_URL": self.url, "TWLEGALRAG_HOME": self.tmp.name,
               "COLUMNS": "200", "NO_COLOR": "1"}
        return subprocess.run([self.exe, *args], capture_output=True, text=True, env=env,
                              cwd=self.tmp.name, timeout=60)

    def test_health(self):
        with urllib.request.urlopen(self.url + "/v1/health") as r:
            self.assertEqual(json.load(r)["status"], "ok")

    def test_pack_then_check(self):
        bundle = Path(self.tmp.name) / "bundle.json"
        r = self.run_cli("pack", "違約金 過高 酌減", "-o", str(bundle), "-n", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(bundle.read_text(encoding="utf-8"))
        self.assertEqual(data["judgments"][0]["doc_id"], "TPSV,112,台上,1234,20230830,1")
        self.assertIn("民法第252條", data["judgments"][0]["cited_articles"])
        self.assertIn("法院得減至相當之數額", data["judgments"][0]["fulltext_excerpt"])

        good = Path(self.tmp.name) / "good.txt"
        good.write_text("依最高法院112年度台上字第1234號民事判決意旨，違約金過高得酌減。", encoding="utf-8")
        # twlegalrag check 一律回傳 0，結果看報表文字（Legal-Pleading-Suite 的 check.py 才轉成退出碼）。
        r = self.run_cli("check", str(bundle), str(good))
        self.assertIn("整體: 通過", r.stdout)
        bad = Path(self.tmp.name) / "bad.txt"
        bad.write_text("另參最高法院108年度台上字第4321號民事判決。", encoding="utf-8")
        r = self.run_cli("check", str(bundle), str(bad))
        self.assertIn("整體: 不在bundle/錯誤", r.stdout)

    def test_law_and_ref(self):
        r = self.run_cli("law", "民法", "252")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("約定之違約金額過高者", r.stdout)
        r = self.run_cli("ref", "台財稅第881945861號", "--full")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("active", r.stdout)
        r = self.run_cli("ref-search", "印花稅")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("881945861", r.stdout)


if __name__ == "__main__":
    unittest.main()
