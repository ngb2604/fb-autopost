"""AI tự phân loại ảnh: Gemini chọn một phân loại có sẵn cho mỗi ảnh.

Dùng cho ảnh khách/shop tải lên mà chưa chọn phân loại. Chạy trong worker
(GitHub Actions) để API key không bao giờ lộ ra PWA.

Quy tắc:
- Chỉ chọn trong danh sách `categories` đang bật; không tự tạo loại mới.
- Trả về tên loại + độ tin cậy; worker quyết định có gán hay không.
- Ảnh không khớp loại nào -> trả về tên rỗng, worker đánh dấu đã thử
  (`classified_at`) để lần chạy sau không gọi AI lại.
"""

from __future__ import annotations

import json
import logging
import time
import unicodedata
from typing import Any

import httpx

from .caption import (
    CaptionError,
    ModelUnavailable,
    RateLimited,
    _call_gemini,
    available_models,
    mark_model_down,
    prepare_image,
)

log = logging.getLogger(__name__)

CLASSIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["category", "confidence"],
    "propertyOrdering": ["category", "confidence"],
}

MIN_CONFIDENCE = 0.45

PROMPT = """Bạn là nhân viên kho của một shop đồ cũ tại Việt Nam.
Nhiệm vụ: nhìn ảnh và chọn ĐÚNG MỘT phân loại phù hợp nhất cho món đồ chính.

Danh sách phân loại (trả về đúng nguyên văn một trong các tên này):
{catalog}

Quy tắc:
- Chỉ chọn tên có trong danh sách trên, không tự đặt tên mới.
- Nếu ảnh không khớp loại nào (hoặc không nhìn rõ món đồ), trả về chuỗi rỗng "".
- `confidence` từ 0 đến 1: mức chắc chắn về lựa chọn.
- Không mô tả thêm, không giải thích.
"""


def normalize(text: str | None) -> str:
    """Bỏ dấu tiếng Việt, chữ thường, gộp khoảng trắng — để so khớp tên loại."""
    if not text:
        return ""
    lowered = str(text).lower().replace("đ", "d")
    stripped = unicodedata.normalize("NFD", lowered)
    no_marks = "".join(ch for ch in stripped if unicodedata.category(ch) != "Mn")
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in no_marks)
    return " ".join(cleaned.split())


def match_category(
    name: str | None, categories: list[dict[str, Any]] | None
) -> dict[str, Any] | None:
    """Tìm phân loại ứng với tên AI trả về (không phân biệt dấu/hoa thường)."""
    key = normalize(name)
    if not key:
        return None
    rows = [c for c in (categories or []) if c.get("active", True) and c.get("name")]
    for cat in rows:
        if normalize(cat["name"]) == key:
            return cat
    # AI có thể trả tên rút gọn ("ghế") hoặc kèm từ thừa ("ghế văn phòng xoay")
    partial = [
        cat
        for cat in rows
        if key in normalize(cat["name"]) or normalize(cat["name"]) in key
    ]
    return partial[0] if len(partial) == 1 else None


def build_prompt(categories: list[dict[str, Any]]) -> str:
    lines = []
    for cat in categories:
        hint = (cat.get("hint") or "").strip()
        lines.append(f"- {cat['name']}" + (f": {hint}" if hint else ""))
    return PROMPT.format(catalog="\n".join(lines))


def parse_result(text: str) -> tuple[str, float]:
    """Đọc JSON Gemini trả về; chịu được rào ```json ... ```."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CaptionError(f"JSON phân loại hỏng: {exc}") from exc
    name = str(data.get("category") or "").strip()
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return name, max(0.0, min(1.0, confidence))


def classify_photo(
    *,
    api_key: str,
    model: str,
    image_bytes: bytes,
    categories: list[dict[str, Any]],
    fallback_models: list[str] | None = None,
    max_retries: int = 1,
    retry_wait: float = 4.0,
) -> dict[str, Any]:
    """Phân loại 1 ảnh.

    Trả về `{"name": <tên AI chọn>, "category": <dict|None>, "confidence": float}`.
    Ném RateLimited khi Gemini 429, CaptionError khi mọi model đều lỗi.
    """
    active = [c for c in (categories or []) if c.get("active", True) and c.get("name")]
    if not active:
        raise CaptionError("Chưa có phân loại nào đang bật")

    image = prepare_image(image_bytes)
    prompt = build_prompt(active)
    chain = available_models([model] + [m for m in (fallback_models or []) if m and m != model])
    last_error: Exception | None = None

    for candidate in chain:
        for attempt in range(max_retries + 1):
            try:
                text = _call_gemini(
                    api_key=api_key,
                    model=candidate,
                    prompt=prompt,
                    images=[image],
                    timeout=60.0,
                    schema=CLASSIFY_SCHEMA,
                    temperature=0.2,
                )
                name, confidence = parse_result(text)
                if candidate != model:
                    log.warning("Phân loại dùng model dự phòng: %s", candidate)
                return {
                    "name": name,
                    "category": match_category(name, active),
                    "confidence": confidence,
                }
            except RateLimited:
                raise
            except ModelUnavailable as exc:
                log.warning("Model %s không dùng được (%s) — thử model khác", candidate, exc)
                mark_model_down(candidate)
                last_error = exc
                break
            except (CaptionError, httpx.HTTPError) as exc:
                last_error = exc
                log.warning(
                    "Gemini phân loại lỗi (%s, lần %d/%d): %s",
                    candidate,
                    attempt + 1,
                    max_retries + 1,
                    exc,
                )
                if attempt < max_retries:
                    time.sleep(retry_wait * (attempt + 1))

    raise CaptionError(f"Thất bại với mọi model ({len(chain)}): {last_error}")
