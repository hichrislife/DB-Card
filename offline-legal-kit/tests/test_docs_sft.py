"""使用者自備文件匯入、團隊隔離與書狀訓練樣本匯出。文件內容皆為測試用合成資料。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tlr_local import docs, sft  # noqa: E402
from tlr_local.__main__ import main as cli  # noqa: E402

PLEADING = [
    ("", "民事起訴狀"),
    ("", "原告 甲○○ 身分證統一編號：A123456789"),
    ("", "被告 乙○○股份有限公司"),
    ("", "壹、訴之聲明"),
    ("", "一、被告應給付原告新臺幣五十萬元，及自起訴狀繕本送達翌日起至清償日止，按週年利率百分之五計算之利息。"),
    ("", "二、訴訟費用由被告負擔。"),
    ("", "貳、事實及理由"),
    ("", "一、兩造於民國一一二年間簽訂買賣契約，約定被告應於同年十月交付系爭設備，惟被告遲未交付，"
         "經原告以手機0912-345-678多次催告仍置之不理，原告爰依民法第二百五十四條解除契約並請求返還價金。"),
    ("", "二、被告遲延給付之事實，有買賣契約書及催告存證信函可證，被告應負返還價金及遲延利息之責。"),
    ("", "參、證據"),
    ("", "原證一：買賣契約書影本。原證二：存證信函影本。以上證據足證原告主張為真實，懇請鈞院鑒核。"),
]


def write_docx(path: Path, paragraphs: list[tuple[str, str]]) -> None:
    body = "".join(
        "<w:p>" + (f'<w:pPr><w:pStyle w:val="{st}"/></w:pPr>' if st else "")
        + f"<w:r><w:t>{escape(t)}</w:t></w:r></w:p>"
        for st, t in paragraphs
    )
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)


class DocsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.root = self.dir / "teams"
        write_docx(self.dir / "起訴狀.docx", PLEADING)
        (self.dir / "memo.txt").write_text("違約金過高之酌減，應審酌債務人履行之程度。", encoding="cp950")

    def tearDown(self):
        self.tmp.cleanup()

    def test_team_name_rejects_path_traversal(self):
        for bad in ("../x", "a/b", "", "a" * 41):
            with self.assertRaises(ValueError):
                docs.team_db_path(self.root, bad)

    def test_ingest_search_and_isolation(self):
        a = docs.connect_team(self.root, "team-a")
        stats = docs.ingest_docs(a, [self.dir])
        self.assertEqual(stats["added"], 2)
        hits = docs.search_docs(a, "解除契約 返還價金")
        self.assertEqual(hits[0]["title"], "民事起訴狀")
        self.assertIn("解除契約", hits[0]["excerpt"])
        self.assertEqual(len(docs.search_docs(a, "違約金 酌減")), 1)  # cp950 文字檔也讀得到

        b = docs.connect_team(self.root, "team-b")
        self.assertEqual(docs.search_docs(b, "解除契約"), [])
        self.assertTrue((self.root / "team-a.sqlite").exists())

    def test_reingest_skips_unchanged_and_replaces_changed(self):
        a = docs.connect_team(self.root, "t")
        docs.ingest_docs(a, [self.dir])
        self.assertEqual(docs.ingest_docs(a, [self.dir])["unchanged"], 2)
        (self.dir / "memo.txt").write_text("改寫後內容：定金之返還。", encoding="utf-8")
        self.assertEqual(docs.ingest_docs(a, [self.dir])["updated"], 1)
        self.assertEqual(docs.search_docs(a, "違約金 酌減"), [])
        self.assertEqual(len(docs.search_docs(a, "定金")), 1)

    def test_chunking_overlap_and_long_paragraph(self):
        pieces = docs.chunk_text("甲" * 2000, size=800, overlap=100)
        self.assertTrue(all(len(p) <= 800 for p in pieces))
        self.assertGreaterEqual(sum(len(p) for p in pieces), 2000)

    def test_pdf_text_and_scanned_pages(self):
        mu = docs._pymupdf()
        if mu is None:
            self.skipTest("PyMuPDF 未安裝")
        pdf = self.dir / "卷證.pdf"
        doc = mu.open()
        page = doc.new_page()
        page.insert_text((72, 72), "本件爭點為消滅時效是否完成之問題，被告抗辯請求權已罹於時效。", fontname="china-t", fontsize=12)
        doc.new_page()  # 空白頁，模擬沒有文字層的掃描頁
        doc.save(str(pdf))
        doc.close()
        ex = docs.extract(pdf, ocr=False)
        self.assertIn("消滅時效", ex.pages[0][1])
        self.assertEqual(ex.needs_ocr_pages, [2])
        a = docs.connect_team(self.root, "t")
        stats = docs.ingest_docs(a, [pdf], ocr=False)
        self.assertEqual(list(stats["needs_ocr"].values()), [[2]])
        self.assertEqual(docs.search_docs(a, "消滅時效")[0]["page"], 1)


class SftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        write_docx(self.dir / "起訴狀.docx", PLEADING)

    def tearDown(self):
        self.tmp.cleanup()

    def test_split_sections(self):
        preamble, sections = sft.split_sections(PLEADING)
        self.assertEqual([h for h, _ in sections], ["壹、訴之聲明", "貳、事實及理由", "參、證據"])
        self.assertEqual(preamble[0], "民事起訴狀")
        self.assertIn("二、訴訟費用", sections[0][1])

    def test_heading_style_fallback(self):
        paras = [("Heading1", "事實"), ("", "內容一" * 30), ("Heading1", "理由"), ("", "內容二" * 30)]
        self.assertEqual([h for h, _ in sft.split_sections(paras)[1]], ["事實", "理由"])

    def test_export_scrubs_and_chains_context(self):
        out = self.dir / "a.jsonl"
        stats = sft.export_sft([self.dir], out, team="team-a", holdout=0)
        recs = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(stats["train"], 3)
        self.assertEqual(recs[0]["team"], "team-a")
        self.assertIn("書狀類型：民事起訴狀", recs[0]["messages"][1]["content"])
        self.assertIn("本段為第一段", recs[0]["messages"][1]["content"])
        self.assertIn("壹、訴之聲明", recs[1]["messages"][1]["content"])  # 第二段帶前文
        dumped = out.read_text(encoding="utf-8")
        self.assertNotIn("0912-345-678", dumped)
        # 同一支電話出現在本段內文與下一段的前文摘錄，兩處都要遮蔽。
        self.assertEqual(stats["pii_hits"].get("手機"), 2)

    def test_holdout_is_deterministic(self):
        out = self.dir / "a.jsonl"
        sft.export_sft([self.dir], out, team="t", holdout=1.0)
        self.assertEqual(out.read_text(encoding="utf-8"), "")
        self.assertEqual(len((self.dir / "a.holdout.jsonl").read_text(encoding="utf-8").splitlines()), 3)

    def test_cli(self):
        out = self.dir / "cli.jsonl"
        self.assertEqual(cli(["export-sft", "--team", "t", str(self.dir), "--out", str(out), "--holdout", "0"]), 0)
        self.assertEqual(len(out.read_text(encoding="utf-8").splitlines()), 3)
        root = self.dir / "teams"
        self.assertEqual(cli(["docs-ingest", "--team", "t", "--docs-root", str(root), str(self.dir)]), 0)


if __name__ == "__main__":
    unittest.main()
