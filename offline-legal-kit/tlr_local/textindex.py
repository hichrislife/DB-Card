"""中文全文檢索用的正規化與 bigram 斷詞。

SQLite FTS5 內建的 unicode61 不會切中文，trigram 又查不到兩字詞（「酌減」「過高」），
所以寫入索引前先把文字轉成以空白分隔的 token：

* 中文連續字串 → 相鄰兩字一組（bigram）；只有一個字時保留單字。
* 英數字串 → 整段一個 token（案號、條號、年份）。

查詢時把每個詞轉成連續 bigram 的 FTS5 phrase，效果等同子字串比對。
"""

from __future__ import annotations

import re
import unicodedata

_RUN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]+|[0-9a-z]+")
_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def normalize(text: str) -> str:
    """全形轉半形、小寫、「臺」統一為「台」。索引與查詢必須用同一套規則。"""
    text = unicodedata.normalize("NFKC", text or "")
    return text.replace("臺", "台").lower()


def _run_tokens(run: str, *, for_query: bool) -> list[str]:
    if not _CJK_RE.match(run):
        return [run]
    if len(run) == 1:
        # 索引端保留單字；查詢端丟掉，因為較長字串裡的單字不會被索引成 unigram。
        return [] if for_query else [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]


def tokenize(text: str) -> str:
    """把文字轉成 FTS5 要存的 token 字串。"""
    out: list[str] = []
    for run in _RUN_RE.findall(normalize(text)):
        out.extend(_run_tokens(run, for_query=False))
    return " ".join(out)


def term_phrase(term: str) -> str | None:
    """單一查詢詞 → FTS5 phrase；沒有可用 token 時回傳 None。"""
    toks: list[str] = []
    for run in _RUN_RE.findall(normalize(term)):
        toks.extend(_run_tokens(run, for_query=True))
    if not toks:
        return None
    return '"' + " ".join(toks) + '"'


def build_match(query: str, mode: str) -> str | None:
    """依模式組 FTS5 MATCH 字串。

    keyword：所有詞都要出現（AND）
    phrase ：整句當一個片語
    any    ：任一詞出現即可（OR），hybrid 補足結果時使用
    """
    if mode == "phrase":
        return term_phrase(query.replace(" ", "").replace("　", ""))
    phrases = [p for p in (term_phrase(t) for t in query.split()) if p]
    if not phrases:
        return None
    return (" OR " if mode == "any" else " AND ").join(phrases)


def query_terms(query: str) -> list[str]:
    """供擷取命中片段用的正規化查詢詞。"""
    return [normalize(t) for t in query.split() if t.strip()]
