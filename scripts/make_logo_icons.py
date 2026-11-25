"""Tạo favicon + bộ icon PWA từ logo shop.

Nguồn: `web/icons/logo-source.jpg` (ảnh logo nền trắng).
Chạy: `python scripts/make_logo_icons.py`

Kết quả ghi vào `web/icons/`:
- icon-180/192/512.png  : icon hiển thị trên màn hình điện thoại (PWA/iOS)
- icon-maskable-512.png : icon Android (chừa vùng an toàn để không bị cắt)
- favicon.ico + favicon-32.png : icon trên tab trình duyệt
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "icons" / "logo-source.jpg"
OUT = ROOT / "web" / "icons"


def square_logo(src: Image.Image, size: int, fill_ratio: float = 0.94) -> Image.Image:
    """Đặt logo vào canvas vuông nền trắng, rộng = fill_ratio * size."""
    target_w = max(1, int(round(size * fill_ratio)))
    ratio = target_w / src.width
    target_h = max(1, int(round(src.height * ratio)))
    logo = src.resize((target_w, target_h), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), "white")
    canvas.paste(logo, ((size - target_w) // 2, (size - target_h) // 2))
    return canvas


def main() -> None:
    src = Image.open(SRC).convert("RGB")

    square_logo(src, 512).save(OUT / "icon-512.png", optimize=True)
    square_logo(src, 192).save(OUT / "icon-192.png", optimize=True)
    square_logo(src, 180).save(OUT / "icon-180.png", optimize=True)

    # Maskable: thu nhỏ logo để nằm trong vùng an toàn của Android
    square_logo(src, 512, fill_ratio=0.76).save(
        OUT / "icon-maskable-512.png", optimize=True
    )

    base = square_logo(src, 256, fill_ratio=0.98)
    base.save(
        OUT / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
    )
    base.resize((32, 32), Image.LANCZOS).save(OUT / "favicon-32.png", optimize=True)

    for name in (
        "icon-512.png",
        "icon-192.png",
        "icon-180.png",
        "icon-maskable-512.png",
        "favicon.ico",
        "favicon-32.png",
    ):
        path = OUT / name
        print(f"{name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
