"""Sửa lỗi double-encoding (UTF-8 bị giải mã nhầm bằng cp1252/latin-1 rồi ghi lại).

Có 2 kiểu rác:
- ký tự Latin-1 (U+00A0..U+00FF) ứng với đúng byte gốc
- ký tự đặc biệt của Windows-1252 (€ ‚ ƒ … ‹ ’ “ ” • – — ™ › œ ž Ÿ)
- ký tự điều khiển C1 (U+0080..U+009F) ứng với byte gốc
"""

from __future__ import annotations

import sys
from pathlib import Path

CP1252_REVERSE = {
    "€": 0x80, "‚": 0x82, "ƒ": 0x83, "„": 0x84, "…": 0x85, "†": 0x86,
    "‡": 0x87, "ˆ": 0x88, "‰": 0x89, "Š": 0x8A, "‹": 0x8B, "Œ": 0x8C,
    "Ž": 0x8E, "‘": 0x91, "’": 0x92, "“": 0x93, "”": 0x94, "•": 0x95,
    "–": 0x96, "—": 0x97, "˜": 0x98, "™": 0x99, "š": 0x9A, "›": 0x9B,
    "œ": 0x9C, "ž": 0x9E, "Ÿ": 0x9F,
}

MARKERS = ("Ã", "á»", "Ä‘", "â€", "áº", "Æ°")


def score(line: str) -> int:
    return sum(line.count(m) for m in MARKERS)


def to_bytes(line: str) -> bytes | None:
    out = bytearray()
    for ch in line:
        o = ord(ch)
        if o < 0x100:
            out.append(o)
        elif ch in CP1252_REVERSE:
            out.append(CP1252_REVERSE[ch])
        else:
            return None
    return bytes(out)


def repair(line: str, max_passes: int = 4) -> str:
    cur = line
    for _ in range(max_passes):
        if score(cur) == 0:
            break
        raw = to_bytes(cur)
        if raw is None:
            break
        try:
            fixed = raw.decode("utf-8")
        except UnicodeDecodeError:
            break
        if score(fixed) >= score(cur):
            break
        cur = fixed
    return cur


def main(paths: list[str]) -> int:
    total = 0
    for p in paths:
        path = Path(p)
        text = path.read_text(encoding="utf-8")
        out, changed = [], 0
        for line in text.splitlines(keepends=True):
            if score(line) > 0:
                fixed = repair(line)
                if fixed != line:
                    out.append(fixed)
                    changed += 1
                    continue
            out.append(line)
        if changed:
            path.write_text("".join(out), encoding="utf-8", newline="")
            print(f"SỬA {changed:3d} dòng  {p}")
            total += changed
        else:
            print(f"OK             {p}")
    print(f"Tổng: {total} dòng")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
