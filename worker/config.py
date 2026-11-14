"""Cấu hình worker: đọc biến môi trường, có giá trị mặc định an toàn."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# --- Hằng số nghiệp vụ -------------------------------------------------------
# Facebook: scheduled_publish_time phải nằm trong khoảng 10 phút .. 30 ngày
# (https://developers.facebook.com/documentation/pages-api/posts — "Notes about
# scheduled posts"). Ta chừa biên an toàn: tối thiểu 15 phút, tối đa 29 ngày.
FB_MIN_LEAD_SECONDS = 15 * 60
FB_MAX_AHEAD_SECONDS = 29 * 24 * 3600

# Facebook Graph API: phiên bản mới nhất đã xác nhận trong changelog chính thức
# (v26.0, 29/07/2026). Có thể nâng bằng biến môi trường khi Meta ra bản mới.
DEFAULT_FB_VERSION = "v26.0"

# Gemini: model Flash ổn định, còn thuộc gói miễn phí tại thời điểm viết code
# (xem https://ai.google.dev/gemini-api/docs/pricing).
# 3.8-flash thường bị 503 "high demand" nên mặc định dùng 3.6-flash,
# tự chuyển sang các model dự phòng khi cần.
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_GEMINI_FALLBACKS = "gemini-3.5-flash-lite,gemini-3.8-flash"


@dataclass
class Config:
    supabase_url: str
    supabase_service_key: str
    gemini_api_key: str
    gemini_model: str
    gemini_fallbacks: list[str]
    fb_page_id: str
    fb_page_token: str
    fb_graph_version: str
    telegram_token: str
    telegram_chat_id: str
    max_photos_per_run: int
    dry_run: bool

    @classmethod
    def from_env(cls, dry_run: bool = False) -> "Config":
        return cls(
            supabase_url=_env("SUPABASE_URL").rstrip("/"),
            supabase_service_key=_env("SUPABASE_SERVICE_ROLE_KEY"),
            gemini_api_key=_env("GEMINI_API_KEY"),
            gemini_model=_env("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
            gemini_fallbacks=[
                m.strip()
                for m in _env("GEMINI_MODEL_FALLBACKS", DEFAULT_GEMINI_FALLBACKS).split(",")
                if m.strip()
            ],
            fb_page_id=_env("FB_PAGE_ID"),
            fb_page_token=_env("FB_PAGE_ACCESS_TOKEN"),
            fb_graph_version=_env("FB_GRAPH_VERSION", DEFAULT_FB_VERSION),
            telegram_token=_env("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
            max_photos_per_run=_env_int("MAX_PHOTOS_PER_RUN", 5),
            dry_run=dry_run,
        )

    def require(self, *names: str) -> None:
        """Báo lỗi rõ ràng nếu thiếu secret bắt buộc (không in giá trị ra log)."""
        missing = [n for n in names if not getattr(self, n.lower(), "")]
        if missing:
            raise SystemExit(
                "Thiếu secret bắt buộc: " + ", ".join(missing) + ". "
                "Xem docs/SETUP_USER.md mục 'Dán secret vào GitHub'."
            )
