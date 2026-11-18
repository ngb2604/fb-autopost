"""Gửi thông báo qua Telegram Bot API.

Không bao giờ log token. Tin nhắn dài được chia nhỏ (< 4096 ký tự).
"""

from __future__ import annotations

import html
import logging

import httpx

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 3800


def _chunks(text: str, size: int = MAX_LEN) -> list[str]:
    if len(text) <= size:
        return [text]
    parts, buf = [], ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > size:
            parts.append(buf)
            buf = ""
        buf += line
    if buf:
        parts.append(buf)
    return parts


def send(token: str, chat_id: str, text: str, *, silent: bool = False) -> bool:
    """Gửi tin nhắn (HTML). Trả về True nếu gửi thành công."""
    if not token or not chat_id:
        log.info("Bỏ qua Telegram: chưa cấu hình token/chat_id")
        return False

    ok = True
    for chunk in _chunks(text):
        try:
            with httpx.Client(timeout=20) as client:
                r = client.post(
                    API.format(token=token),
                    json={
                        "chat_id": chat_id,
                        "text": chunk,
                        "parse_mode": "HTML",
                        "disable_notification": silent,
                        "disable_web_page_preview": True,
                    },
                )
            if r.status_code >= 400:
                log.error("Telegram lỗi HTTP %s: %s", r.status_code, r.text[:300])
                ok = False
        except httpx.HTTPError as exc:
            log.error("Không gửi được Telegram: %s", exc)
            ok = False
    return ok


def esc(text: str) -> str:
    return html.escape(str(text), quote=False)


def summary_message(
    *,
    created_posts: int,
    photos_used: int,
    scheduled: int,
    posted: int,
    failed: int,
    remaining_photos: int,
    posts_per_day: int,
    coverage: float,
    dry_run: bool,
    notes: list[str] | None = None,
) -> str:
    icon = "🧪" if dry_run else "🤖"
    head = f"{icon} <b>Worker đã chạy</b>" + (" (dry-run)" if dry_run else "")
    lines = [
        head,
        f"• Bài mới tạo: <b>{created_posts}</b> ({photos_used} ảnh)",
        f"• Đã lên lịch: <b>{scheduled}</b>",
        f"• Đã đăng (đồng bộ): <b>{posted}</b>",
        f"• Lỗi: <b>{failed}</b>",
        f"• Ảnh còn dùng được: <b>{remaining_photos}</b>",
    ]
    if coverage < 2:
        lines.append(
            f"⚠️ Chỉ còn đủ ảnh cho khoảng <b>{coverage}</b> ngày "
            f"(mức {posts_per_day} bài/ngày). Hãy thêm ảnh!"
        )
    for note in notes or []:
        lines.append(note)
    return "\n".join(lines)
