"""規則式個資遮蔽。純 CPU、零 VRAM，給訓練資料匯出與案卷前處理使用。

只處理格式固定的識別碼；姓名、地址這類需要語意判斷的欄位不在範圍內，
司法院公開裁判書已先行以「○○」遮蔽當事人姓名。
"""

from __future__ import annotations

import re

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("身分證號", re.compile(r"(?<![A-Za-z0-9])[A-Z][1289]\d{8}(?!\d)")),
    ("Email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("手機", re.compile(r"(?<!\d)09\d{2}[-\s]?\d{3}[-\s]?\d{3}(?!\d)")),
    ("市話", re.compile(r"(?<!\d)\(0\d{1,2}\)\s?\d{3,4}[-\s]?\d{4}(?!\d)|(?<!\d)0\d{1,2}-\d{3,4}-?\d{4}(?!\d)")),
    ("信用卡號", re.compile(r"(?<!\d)\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4}(?!\d)")),
    # 銀行帳號沒有固定格式，只遮前後有「帳號/帳戶」字樣的長數字，避免誤傷金額與案號。
    ("帳號", re.compile(r"(?<=帳號)[：:\s]*\d[\d-]{8,18}\d|(?<=帳戶)[：:\s]*\d[\d-]{8,18}\d")),
]


def scrub(text: str) -> tuple[str, dict[str, int]]:
    """回傳遮蔽後文字與各類命中次數。"""
    counts: dict[str, int] = {}
    for label, pattern in _RULES:
        text, n = pattern.subn(f"[{label}]", text)
        if n:
            counts[label] = n
    return text, counts
