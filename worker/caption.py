"""Gọi Gemini để đọc ảnh và viết caption tiếng Việt (JSON).

Thiết kế:
- Ảnh được resize (cạnh dài ≤ 1600px) + xoay theo EXIF trước khi gửi.
- Yêu cầu Gemini trả JSON theo schema cố định (responseMimeType + responseSchema).
- Retry 2 lần khi JSON hỏng; retry có backoff khi 429/5xx.
- Nếu độ tin cậy thấp hoặc có cảnh báo nghiêm trọng -> buộc duyệt tay.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
import unicodedata
from typing import Any

import httpx
from PIL import Image, ImageOps

log = logging.getLogger(__name__)

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_EDGE = 1600
JPEG_QUALITY = 82

CAPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "product_type": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["product_type", "caption", "hashtags", "confidence", "warnings"],
    "propertyOrdering": ["product_type", "caption", "hashtags", "confidence", "warnings"],
}


class CaptionError(Exception):
    """Lỗi không thể phục hồi khi tạo caption."""


class RateLimited(Exception):
    """Gemini trả 429 — nên chờ và thử lại sau."""


class ModelUnavailable(Exception):
    """Model đang quá tải (503) hoặc không tồn tại (404) — chuyển model khác."""



# ---------------------------------------------------------------------------
# Ảnh
# ---------------------------------------------------------------------------

def prepare_image(raw: bytes, max_edge: int = MAX_EDGE) -> bytes:
    """Resize + xoay đúng hướng + chuyển JPEG. Trả về bytes JPEG."""
    with Image.open(io.BytesIO(raw)) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((max_edge, max_edge), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Bạn là người viết bài bán hàng cho một Facebook Page tại Việt Nam.
Nhiệm vụ: nhìn ảnh sản phẩm và viết MỘT bài đăng.

Quy tắc:
- Viết bằng tiếng Việt, giọng: {tone}.
- Bám sát thông tin shop sau (nếu có): {shop_info}
- Học giọng văn từ các bài mẫu (không chép nguyên văn): {sample_posts}
- Phân loại mặt hàng của ảnh này: {category}
- Ghi chú của shop cho loại hàng này (nếu có): {category_hint}
- Kiểu bài đăng: {style_hint}
- CHỈ mô tả những gì thực sự nhìn thấy trong ảnh hoặc có trong thông tin shop.
- TUYỆT ĐỐI KHÔNG bịa giá, kích cỡ, chất liệu, thương hiệu, khuyến mãi, tình trạng hàng
  nếu không có trong ảnh hoặc thông tin shop. Nếu thiếu, dùng câu mời liên hệ thay vì đoán.
- Không dùng từ ngữ phóng đại vi phạm quy định quảng cáo ("số 1", "cam kết 100%"...).
- Mỗi bài phải khác cách mở đầu so với các bài gần đây để tránh bị coi là spam.
- Độ dài: 3-6 dòng, có emoji vừa phải, có lời kêu gọi hành động ở cuối.
- Thêm 3-6 hashtag liên quan.

Các caption gần đây (KHÔNG được mở đầu giống hoặc lặp lại):
{recent_captions}
"""


STYLE_HINTS = {
    "album": (
        "Album nhiều ảnh của cùng một loại hàng. Viết như một đợt hàng/collection: "
        "nêu điểm chung của cả lô, mời khách xem hết các ảnh, gợi ý số lượng/độ đa dạng "
        "nếu thấy rõ trong ảnh."
    ),
    "single": (
        "Bài một ảnh. Tập trung mô tả đúng món đồ trong ảnh, ngắn gọn, dễ đọc."
    ),
}


def build_prompt(
    *,
    tone: str,
    shop_info: str,
    sample_posts: list[str] | None,
    recent_captions: list[str] | None,
    category_name: str = "",
    category_hint: str = "",
    style: str = "",
) -> str:
    samples = "\n---\n".join(sample_posts or []) or "(chưa có)"
    recent = "\n---\n".join(recent_captions or []) or "(chưa có)"
    return SYSTEM_PROMPT.format(
        tone=tone or "thân thiện, tự nhiên, ngắn gọn",
        shop_info=shop_info or "(chưa có)",
        sample_posts=samples,
        recent_captions=recent,
        category=category_name or "(không rõ — tự nhận diện từ ảnh)",
        category_hint=category_hint or "(không có)",
        style_hint=STYLE_HINTS.get(style, STYLE_HINTS["album"]),
    )


# ---------------------------------------------------------------------------
# Parse / kiểm tra JSON
# ---------------------------------------------------------------------------

def _strip_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt: 'ghế' -> 'ghe', 'đồ' -> 'do'."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def normalize_hashtags(tags: Any, defaults: list[str] | None = None, limit: int = 6) -> list[str]:
    """Chuẩn hoá hashtag: bỏ dấu, bỏ khoảng trắng/ký tự lạ, thêm '#'.

    Hashtag có dấu (ví dụ '#ghếvănphòng') không hoạt động trên Facebook,
    nên luôn chuyển về dạng không dấu.
    """
    out: list[str] = []
    for tag in list(tags or []) + list(defaults or []):
        if not isinstance(tag, str):
            continue
        t = _strip_diacritics(tag.strip()).lower()
        t = re.sub(r"[^0-9a-z_]", "", t)
        if not t:
            continue
        tag_str = "#" + t
        if tag_str not in out:
            out.append(tag_str)
    return out[:limit]


def parse_caption_json(text: str, defaults: list[str] | None = None) -> dict[str, Any]:
    """Parse + validate JSON từ Gemini. Ném CaptionError nếu không hợp lệ."""
    if not text or not text.strip():
        raise CaptionError("Gemini trả về rỗng")

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise CaptionError(f"JSON không hợp lệ: {exc}") from exc

    if not isinstance(data, dict):
        raise CaptionError("JSON gốc không phải object")

    caption = str(data.get("caption") or "").strip()
    if not caption:
        raise CaptionError("Thiếu trường caption")

    try:
        confidence = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    warnings = [str(w) for w in (data.get("warnings") or []) if str(w).strip()]

    return {
        "product_type": str(data.get("product_type") or "").strip(),
        "caption": caption,
        "hashtags": normalize_hashtags(data.get("hashtags"), defaults),
        "confidence": confidence,
        "warnings": warnings,
    }


SERIOUS_WARNING_KEYWORDS = ("mờ", "không rõ", "che khuất", "nghi ngờ", "nhiều sản phẩm", "không thấy ảnh")


def needs_manual_review(result: dict[str, Any]) -> tuple[bool, str]:
    """Quyết định bắt buộc duyệt tay (mục 8 của đặc tả)."""
    if result.get("confidence", 0) < 0.5:
        return True, f"độ tin cậy thấp ({result.get('confidence')})"
    for w in result.get("warnings", []):
        low = w.lower()
        if any(k in low for k in SERIOUS_WARNING_KEYWORDS):
            return True, f"cảnh báo AI: {w}"
    return False, ""


# ---------------------------------------------------------------------------
# Gọi API
# ---------------------------------------------------------------------------

def _call_gemini(
    *,
    api_key: str,
    model: str,
    prompt: str,
    images: list[bytes],
    timeout: float = 120.0,
) -> str:
    """Gọi Gemini với 1 hoặc nhiều ảnh, trả về text thô. Ném RateLimited khi 429."""
    parts: list[dict[str, Any]] = [{"text": prompt}]
    for img in images:
        parts.append(
            {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(img).decode(),
                }
            }
        )

    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 1.0,
            "responseMimeType": "application/json",
            "responseSchema": CAPTION_SCHEMA,
        },
    }
    url = GEMINI_ENDPOINT.format(model=model)
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, params={"key": api_key}, json=body)

    if r.status_code == 429:
        raise RateLimited(r.text[:300])
    if r.status_code in (404, 503):
        # 404 = model không còn hỗ trợ, 503 = quá tải -> thử model khác
        raise ModelUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
    if r.status_code >= 400:
        raise CaptionError(f"HTTP {r.status_code}: {r.text[:400]}")

    data = r.json()
    candidates = data.get("candidates") or []
    if not candidates:
        fb = (data.get("promptFeedback") or {}).get("blockReason")
        raise CaptionError(f"Không có candidates (blockReason={fb})")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        finish = candidates[0].get("finishReason")
        raise CaptionError(f"Phản hồi rỗng (finishReason={finish})")
    return text


def generate_caption(
    *,
    api_key: str,
    model: str,
    image_bytes: bytes | list[bytes],
    tone: str,
    shop_info: str,
    sample_posts: list[str] | None = None,
    recent_captions: list[str] | None = None,
    default_hashtags: list[str] | None = None,
    category_name: str = "",
    category_hint: str = "",
    category_hashtags: list[str] | None = None,
    style: str = "album",
    fallback_models: list[str] | None = None,
    max_retries: int = 2,
    retry_wait: float = 8.0,
) -> dict[str, Any]:
    """Trả về dict đã validate. Ném CaptionError / RateLimited.

    `image_bytes` có thể là 1 ảnh hoặc danh sách ảnh (album). Gemini nhận tất cả
    các ảnh trong cùng một lượt để viết một caption chung.

    Nếu model chính quá tải (503) hoặc đã bị gỡ (404), tự thử các model trong
    `fallback_models` — giúp bot không chết khi Google đổi model/quá tải.
    """
    raw_images = image_bytes if isinstance(image_bytes, list) else [image_bytes]
    if not raw_images:
        raise CaptionError("Không có ảnh để viết caption")
    images = [prepare_image(img) for img in raw_images]
    prompt = build_prompt(
        tone=tone,
        shop_info=shop_info,
        sample_posts=sample_posts,
        recent_captions=recent_captions,
        category_name=category_name,
        category_hint=category_hint,
        style=style,
    )
    hashtag_defaults = list(default_hashtags or []) + list(category_hashtags or [])

    chain = [model] + [m for m in (fallback_models or []) if m and m != model]
    last_error: Exception | None = None

    for candidate in chain:
        for attempt in range(max_retries + 1):
            try:
                text = _call_gemini(api_key=api_key, model=candidate, prompt=prompt, images=images)
                result = parse_caption_json(text, defaults=hashtag_defaults)
                if candidate != model:
                    log.warning("Đã dùng model dự phòng: %s", candidate)
                return result
            except RateLimited:
                raise
            except ModelUnavailable as exc:
                # Không tốn thời gian retry: sang model kế tiếp
                log.warning("Model %s không dùng được (%s) — thử model khác", candidate, exc)
                last_error = exc
                break
            except (CaptionError, httpx.HTTPError) as exc:
                last_error = exc
                log.warning("Gemini lỗi (%s, lần %d/%d): %s", candidate, attempt + 1, max_retries + 1, exc)
                if attempt < max_retries:
                    time.sleep(retry_wait * (attempt + 1))

    raise CaptionError(f"Thất bại với mọi model ({len(chain)}): {last_error}")

