"""Lập kế hoạch bài đăng: gom ảnh theo phân loại thành album hoặc bài 1 ảnh.

Toàn bộ file là hàm thuần (không gọi mạng) để test được.

Quy tắc:
- Ảnh được gom theo phân loại; mỗi loại có một "hàng đợi" riêng.
- `rotate=True`: các phân loại được xoay vòng (round-robin) để loại nào cũng có bài.
- `rotate=False`: đăng hết một phân loại rồi mới sang phân loại kế tiếp.
- Kiểu đăng xoay vòng theo `styles` (ví dụ album -> single -> album ...).
- Bài album lấy tối đa `album_size` ảnh; nếu chỉ còn 1 ảnh thì thành bài 1 ảnh.
- Ảnh thuộc phân loại đã tắt sẽ không được đăng.
"""

from __future__ import annotations

from typing import Any

UNCATEGORIZED = "__uncategorized__"
STYLES = ("album", "single")


def _active_categories(categories: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = [c for c in (categories or []) if c.get("active", True)]
    return sorted(
        rows,
        key=lambda c: (int(c.get("sort_order") or 0), str(c.get("name") or "")),
    )


def plan_posts(
    available: list[dict[str, Any]] | None,
    categories: list[dict[str, Any]] | None,
    *,
    album_size: int = 4,
    styles: list[str] | None = None,
    max_posts: int = 6,
    rotate: bool = True,
) -> list[dict[str, Any]]:
    """Trả về danh sách bài cần tạo.

    Mỗi phần tử: `{category_id, category_name, style, photos: [...]}`.
    `available` phải đã được sắp theo thứ tự ưu tiên (ảnh lâu chưa dùng trước).
    """
    album_size = max(1, int(album_size or 1))
    max_posts = max(1, int(max_posts or 1))
    style_cycle = [s for s in (styles or ["album"]) if s in STYLES] or ["album"]

    buckets: dict[str, list[dict[str, Any]]] = {}
    meta: dict[str, dict[str, Any]] = {}

    for cat in _active_categories(categories):
        key = str(cat["id"])
        buckets[key] = []
        meta[key] = {"category_id": key, "category_name": cat.get("name") or ""}

    buckets[UNCATEGORIZED] = []
    meta[UNCATEGORIZED] = {"category_id": None, "category_name": ""}

    for photo in available or []:
        key = str(photo.get("category_id") or UNCATEGORIZED)
        if key not in buckets:
            # Ảnh của phân loại đã tắt -> bỏ qua
            continue
        buckets[key].append(photo)

    plans: list[dict[str, Any]] = []

    def take(key: str) -> None:
        pool = buckets[key]
        style = style_cycle[len(plans) % len(style_cycle)]
        if style == "album":
            take_n = min(album_size, len(pool))
            if take_n < 2:
                style, take_n = "single", 1
        else:
            take_n = 1

        photos = pool[:take_n]
        del pool[:take_n]
        plans.append({**meta[key], "style": style, "photos": photos})

    order = list(buckets.keys())

    if rotate:
        while len(plans) < max_posts:
            progressed = False
            for key in order:
                if len(plans) >= max_posts:
                    break
                if buckets[key]:
                    take(key)
                    progressed = True
            if not progressed:
                break
    else:
        for key in order:
            while buckets[key] and len(plans) < max_posts:
                take(key)

    return plans


def plan_summary(plans: list[dict[str, Any]]) -> str:
    """Chuỗi ngắn mô tả kế hoạch, dùng cho log/Telegram."""
    if not plans:
        return "không có bài nào"
    parts = []
    for p in plans:
        name = p.get("category_name") or "Chưa phân loại"
        parts.append(f"{name} ({len(p['photos'])} ảnh)")
    return ", ".join(parts)
