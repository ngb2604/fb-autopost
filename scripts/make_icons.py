"""Tạo icon PNG cho PWA (180/192/512) — chạy 1 lần, không cần mạng.

    python scripts/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "web" / "icons"
SIZES = (180, 192, 512)


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Nền gradient dọc đơn giản (vẽ từng dải)
    top = (37, 99, 235)
    bottom = (79, 70, 229)
    for y in range(size):
        t = y / max(1, size - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
        d.line([(0, y), (size, y)], fill=color)

    # Bo góc
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255
    )
    img.putalpha(mask)

    # Hình "khung ảnh + mũi tên tăng" tượng trưng cho đăng bài tự động
    d = ImageDraw.Draw(img)
    pad = int(size * 0.26)
    box = [pad, pad + int(size * 0.04), size - pad, size - int(size * 0.14)]
    d.rounded_rectangle(box, radius=int(size * 0.07), outline=(255, 255, 255, 235), width=max(3, size // 48))
    # Núi + mặt trời
    d.ellipse(
        [box[0] + int(size * 0.06), box[1] + int(size * 0.06),
         box[0] + int(size * 0.16), box[1] + int(size * 0.16)],
        fill=(255, 255, 255, 235),
    )
    h = box[3] - box[1]
    d.polygon(
        [
            (box[0] + int(size * 0.04), box[3] - int(h * 0.12)),
            (box[0] + int(size * 0.17), box[3] - int(h * 0.52)),
            (box[0] + int(size * 0.30), box[3] - int(h * 0.12)),
            (box[0] + int(size * 0.38), box[3] - int(h * 0.32)),
            (box[2] - int(size * 0.04), box[3]),
            (box[0] + int(size * 0.04), box[3]),
        ],
        fill=(255, 255, 255, 235),
    )
    # Mũi tên đi lên
    cx = size // 2
    d.line(
        [(cx, int(size * 0.90)), (cx, int(size * 0.70))],
        fill=(255, 255, 255, 255),
        width=max(4, size // 36),
    )
    a = int(size * 0.055)
    d.polygon(
        [(cx, int(size * 0.66)), (cx - a, int(size * 0.75)), (cx + a, int(size * 0.75))],
        fill=(255, 255, 255, 255),
    )
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        path = OUT / f"icon-{size}.png"
        draw_icon(size).save(path)
        print("created", path)


if __name__ == "__main__":
    main()
