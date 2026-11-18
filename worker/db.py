"""Truy cập Supabase (Postgres + Storage) qua REST API bằng service role key.

Chỉ dùng httpx (nhẹ). Mọi hàm ở đây chạy với quyền service role — chỉ được gọi
từ GitHub Actions, không bao giờ từ PWA.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(30.0, connect=15.0)


class Supabase:
    def __init__(self, url: str, service_key: str) -> None:
        self.url = url.rstrip("/")
        self.key = service_key
        self.http = httpx.Client(
            timeout=TIMEOUT,
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
            },
        )

    # -- PostgREST ----------------------------------------------------------
    def select(
        self,
        table: str,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        r = self.http.get(f"{self.url}/rest/v1/{table}", params=params or {})
        self._raise(r, f"select {table}")
        return r.json()

    def insert(self, table: str, rows: dict | list[dict]) -> list[dict[str, Any]]:
        r = self.http.post(
            f"{self.url}/rest/v1/{table}",
            json=rows,
            headers={"Prefer": "return=representation"},
        )
        self._raise(r, f"insert {table}")
        return r.json() if r.content else []

    def update(
        self, table: str, match: dict[str, str], values: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """match dùng cú pháp PostgREST, ví dụ {"id": "eq.<uuid>"}."""
        r = self.http.patch(
            f"{self.url}/rest/v1/{table}",
            params=match,
            json=values,
            headers={"Prefer": "return=representation"},
        )
        self._raise(r, f"update {table}")
        return r.json() if r.content else []

    def rpc(self, fn: str, args: dict[str, Any] | None = None) -> Any:
        r = self.http.post(f"{self.url}/rest/v1/rpc/{fn}", json=args or {})
        self._raise(r, f"rpc {fn}")
        return r.json() if r.content else None

    # -- Storage ------------------------------------------------------------
    def download(self, bucket: str, path: str) -> bytes:
        r = self.http.get(f"{self.url}/storage/v1/object/{bucket}/{path}")
        self._raise(r, f"download {path}")
        return r.content

    def remove(self, bucket: str, path: str) -> None:
        r = self.http.delete(f"{self.url}/storage/v1/object/{bucket}/{path}")
        # 404 = file đã bị xóa trước đó, coi như thành công
        if r.status_code == 404:
            return
        self._raise(r, f"remove {path}")

    # -- Tiện ích -----------------------------------------------------------
    @staticmethod
    def _raise(r: httpx.Response, what: str) -> None:
        if r.status_code < 400:
            return
        # Không in header (chứa key) — chỉ in nội dung lỗi đã cắt ngắn
        raise RuntimeError(f"{what} -> HTTP {r.status_code}: {r.text[:500]}")

    def close(self) -> None:
        self.http.close()


# ---------------------------------------------------------------------------
# Nghiệp vụ mức cao
# ---------------------------------------------------------------------------

PHOTO_FIELDS = "*"


def get_settings(db: Supabase) -> dict[str, Any] | None:
    rows = db.select("settings", {"select": "*", "limit": "1"})
    return rows[0] if rows else None


def list_categories(db: Supabase) -> list[dict[str, Any]]:
    """Danh sách phân loại mặt hàng (kèm cả loại đã tắt để hiển thị tên)."""
    try:
        return db.select(
            "categories",
            {"select": "id,name,hint,hashtags,active,sort_order", "order": "sort_order.asc,name.asc"},
        )
    except Exception as exc:  # DB cũ chưa có bảng -> bot vẫn chạy bình thường
        log.warning("Không đọc được bảng categories: %s", exc)
        return []


def ensure_settings(db: Supabase) -> dict[str, Any]:
    """App 1 người dùng: lấy settings duy nhất, tạo mới nếu chưa có."""
    s = get_settings(db)
    if s:
        return s
    users = db.rpc("get_or_create_settings", {"p_owner": "00000000-0000-0000-0000-000000000000"})
    return users


def list_photos(
    db: Supabase,
    statuses: list[str],
    *,
    order: str = "created_at.asc",
    limit: int = 50,
) -> list[dict[str, Any]]:
    params = {
        "select": PHOTO_FIELDS,
        "status": "in.(" + ",".join(statuses) + ")",
        "order": order,
        "limit": str(limit),
    }
    return db.select("photos", params)


# ---------------------------------------------------------------------------
# Bài đăng (posts) + ảnh của bài (post_photos)
# ---------------------------------------------------------------------------

POST_FIELDS = "id,category_id,style,status,caption,hashtags,scheduled_at,fb_post_id,error_message,created_at"


def available_photos(
    db: Supabase, reuse_days: int = 14, limit: int = 200
) -> list[dict[str, Any]]:
    """Ảnh đang dùng được: chưa dùng bao giờ, hoặc đã quá hạn dùng lại."""
    rows = db.rpc(
        "available_photos",
        {"p_reuse_days": int(reuse_days), "p_limit": int(limit)},
    )
    return rows or []


def list_posts(
    db: Supabase,
    statuses: list[str],
    *,
    order: str = "created_at.asc",
    limit: int = 50,
) -> list[dict[str, Any]]:
    return db.select(
        "posts",
        {
            "select": POST_FIELDS,
            "status": "in.(" + ",".join(statuses) + ")",
            "order": order,
            "limit": str(limit),
        },
    )


def update_post(db: Supabase, post_id: str, **values: Any) -> None:
    db.update("posts", {"id": f"eq.{post_id}"}, values)


def create_post(
    db: Supabase,
    *,
    category_id: str | None,
    style: str,
    photo_ids: list[str],
) -> dict[str, Any]:
    """Tạo bài + gắn ảnh + giữ chỗ ảnh (status='scheduled')."""
    rows = db.insert(
        "posts",
        {"category_id": category_id, "style": style, "status": "draft"},
    )
    post = rows[0]
    db.insert(
        "post_photos",
        [
            {"post_id": post["id"], "photo_id": pid, "position": i}
            for i, pid in enumerate(photo_ids)
        ],
    )
    if photo_ids:
        db.rpc("reserve_photos", {"p_ids": photo_ids})
    return post


def posts_with_photos(
    db: Supabase, post_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Trả về {post_id: [{id, storage_path, status}, ...]} theo đúng thứ tự."""
    if not post_ids:
        return {}
    links = db.select(
        "post_photos",
        {
            "select": "post_id,photo_id,position",
            "post_id": "in.(" + ",".join(post_ids) + ")",
            "order": "position.asc",
        },
    )
    if not links:
        return {}
    photo_ids = sorted({r["photo_id"] for r in links})
    photos = db.select(
        "photos",
        {
            "select": "id,storage_path,status,used_at,use_count",
            "id": "in.(" + ",".join(photo_ids) + ")",
        },
    )
    by_id = {p["id"]: p for p in photos}
    out: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        photo = by_id.get(link["photo_id"])
        if photo:
            out.setdefault(link["post_id"], []).append(photo)
    return out


def claim_post(db: Supabase, post_id: str) -> bool:
    return bool(db.rpc("claim_post", {"p_id": post_id}))


def mark_photos_used(db: Supabase, photo_ids: list[str]) -> None:
    if photo_ids:
        db.rpc("mark_photos_used", {"p_ids": photo_ids})


def mark_photos_posted(db: Supabase, photo_ids: list[str]) -> None:
    if photo_ids:
        db.rpc("mark_photos_posted", {"p_ids": photo_ids})


def release_photos(db: Supabase, photo_ids: list[str]) -> None:
    if photo_ids:
        db.rpc("release_photos", {"p_ids": photo_ids})


def delete_post(db: Supabase, post_id: str) -> None:
    db.http.delete(f"{db.url}/rest/v1/posts", params={"id": f"eq.{post_id}"})


def update_photo(db: Supabase, photo_id: str, **values: Any) -> None:
    db.update("photos", {"id": f"eq.{photo_id}"}, values)


def claim_for_scheduling(db: Supabase, photo_id: str) -> bool:
    """Đánh dấu "đang xử lý" một cách nguyên tử để tránh đăng trùng.

    Hàm SQL `claim_photo` chỉ thành công khi ảnh còn status='approved',
    chưa có fb_post_id; đồng thời tăng attempts lên 1. Nhờ vậy hai lần chạy
    chồng nhau không thể cùng lấy một ảnh.
    """
    return bool(db.rpc("claim_photo", {"p_id": photo_id}))



def log_event(
    db: Supabase,
    level: str,
    message: str,
    *,
    photo_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    try:
        db.insert(
            "post_logs",
            {
                "photo_id": photo_id,
                "level": level,
                "message": message[:2000],
                "detail": detail,
            },
        )
    except Exception as exc:  # log lỗi không được làm hỏng cả lần chạy
        log.warning("Không ghi được post_logs: %s", exc)


def acquire_run_lock(db: Supabase, max_minutes: int = 45) -> int | None:
    """Khóa mềm: nếu có lần chạy khác chưa kết thúc trong 45 phút thì bỏ qua."""
    rows = db.select(
        "worker_runs",
        {
            "select": "id,started_at",
            "finished_at": "is.null",
            "order": "started_at.desc",
            "limit": "1",
        },
    )
    if rows:
        started = datetime.fromisoformat(rows[0]["started_at"].replace("Z", "+00:00"))
        age_min = (datetime.now(timezone.utc) - started).total_seconds() / 60
        if age_min < max_minutes:
            log.warning("Bỏ qua: lần chạy #%s mới bắt đầu %.0f phút trước", rows[0]["id"], age_min)
            return None
        # Lần chạy cũ bị treo -> đóng lại
        db.update(
            "worker_runs",
            {"id": f"eq.{rows[0]['id']}"},
            {"finished_at": datetime.now(timezone.utc).isoformat()},
        )

    created = db.insert("worker_runs", {})
    return created[0]["id"] if created else None


def finish_run_lock(db: Supabase, run_id: int | None, summary: dict[str, Any]) -> None:
    if run_id is None:
        return
    db.update(
        "worker_runs",
        {"id": f"eq.{run_id}"},
        {"finished_at": datetime.now(timezone.utc).isoformat(), "summary": summary},
    )
