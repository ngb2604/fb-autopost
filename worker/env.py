"""Nạp file .env cho chạy thử trên máy (không cần thư viện ngoài).

Trên GitHub Actions, biến môi trường đã được set sẵn nên hàm này không làm gì.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path | str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
