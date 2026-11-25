"""Lập kế hoạch bài đăng: gộp TOÀN BỘ ảnh của một phân loại vào 1 bài.

Mô hình mới (thay cho kiểu chia đều `album_size` cũ):
- Mỗi phân loại có ảnh -> gom hết ảnh của loại đó vào **một bài**.
- Nếu số ảnh vượt giới hạn an toàn của một bài, chia thành **tối đa 2 bài**
  (chia đều, ví dụ 11 ảnh -> 6 + 5) và mỗi bài có caption riêng.
- Ảnh dư (khi một loại có quá nhiều ảnh, vượt 2 bài) để lần chạy sau.
- Tất cả phân loại đang bật đều được xử lý trong cùng một lượt, dừng khi chạm
  `max_posts_per_run`.

Toàn bộ file là hàm thuần (không gọi mạng) để test được.
"""

from __future__ import annotations

from typing import Any

UNCATEGORIZED = "__uncategorized__"
STYLES = ("album", "single")

# Facebook nhận nhiều ảnh trong một bài nhưng payload quá lớn dễ lỗi 500,
# nên giữ một ngưỡng an toàn. Vượt ngưỡng -> chia tối đa 2 bài.
MAX_PHOTOS_PER_POST = 20
MAX_POSTS_PER_CATEGORY = 2


def _active_categories(categories: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = [c for c in (categories or []) if c.get("active", True)]
    return sorted(
        rows,
        key=lambda c: (int(c.get("sort_order") or 0), str(c.get("name") or "")),
    )


def split_sizes(total: int, parts: int, max_per_post: int) -> list[int]:
    """Chia `total` ảnh thành `parts` phần gần bằng nhau, mỗi phần ≤ max_per_post.

    Ví dụ: (11, 2, 20) -> [6, 5]; (21, 2, 10) -> [10, 10].
    """
    total = max(0, int(total))
    parts = max(1, int(parts))
    max_per_post = max(1, int(max_per_post))
    take = min(total, parts * max_per_post)
    base, extra = divmod(take, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def plan_posts(
    available: list[dict[str, Any]] | None,
    categories: list[dict[str, Any]] | None,
    *,
    max_posts_per_run: int = 6,
    max_photos_per_post: int = MAX_PHOTOS_PER_POST,
    max_posts_per_category: int = MAX_POSTS_PER_CATEGORY,
) -> list[dict[str, Any]]:
    """Trả về danh sách bài cần tạo.

    Mỗi phần tử: `{category_id, category_name, style, photos, part, parts}`.
    `available` phải đã sắp theo thứ tự ưu tiên (ảnh lâu chưa dùng trước).
    """
    max_posts_per_run = max(1, int(max_posts_per_run or 1))
    max_photos_per_post = max(1, int(max_photos_per_post or 1))
    max_posts_per_category = max(1, int(max_posts_per_category or 1))

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

    for key in buckets:
        pool = buckets[key]
        if not pool:
            continue

        total = len(pool)
        # 1 bài nếu vừa; nếu không thì chia tối đa `max_posts_per_category` bài
        parts = 1 if total <= max_photos_per_post else max_posts_per_category
        parts = min(parts, max_posts_per_category)
        sizes = split_sizes(total, parts, max_photos_per_post)

        offset = 0
        for index, size in enumerate(s for s in sizes if s > 0):
            if len(plans) >= max_posts_per_run:
                return plans
            photos = pool[offset : offset + size]
            offset += size
            plans.append(
                {
                    **meta[key],
                    "style": "album" if len(photos) > 1 else "single",
                    "photos": photos,
                    "part": index + 1,
                    "parts": sum(1 for s in sizes if s > 0),
                }
            )

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
