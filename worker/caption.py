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


# Model vừa lỗi 503/404 trong tiến trình hiện tại -> tạm bỏ qua để không chờ lại
# (Gemini hay trả 503 cho model chính vài chục giây mỗi lần gọi).
_DOWN_MODELS: dict[str, float] = {}
_MODEL_DOWN_TTL = 600.0


def available_models(chain: list[str]) -> list[str]:
    """Bỏ các model đang bị đánh dấu 'down'; nếu bỏ hết thì vẫn thử lại model đầu."""
    now = time.monotonic()
    alive = [m for m in chain if _DOWN_MODELS.get(m, 0.0) <= now]
    return alive or chain[:1]


def mark_model_down(model: str) -> None:
    _DOWN_MODELS[model] = time.monotonic() + _MODEL_DOWN_TTL
    log.warning("Tạm bỏ qua model %s trong %.0f phút", model, _MODEL_DOWN_TTL / 60)


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

SYSTEM_PROMPT = """Bạn là nhân viên viết bài bán hàng cho một Facebook Page thanh lý đồ cũ tại Hà Nội.
Nhiệm vụ: xem (các) ảnh sản phẩm và viết MỘT bài đăng hoàn chỉnh theo đúng bố cục mẫu.

BỐ CỤC BẮT BUỘC — mỗi ý một dòng, dùng ký tự xuống dòng thật:
🔥 <TÊN NHÓM HÀNG> THANH LÝ <THƯƠNG HIỆU nếu thấy rõ> 🔥
💥 <một câu hỏi hoặc câu dẫn khiến khách quan tâm>
✅ <đặc điểm 1>
✅ <đặc điểm 2>
✅ <đặc điểm 3>
✅ <đặc điểm 4>
💼 Địa chỉ: <địa chỉ shop>
📍 Zalo/Hotline: <số hotline shop>
📞 <câu kêu gọi nhắn tin để nhận báo giá / xem thêm ảnh>
👉 <CÂU KẾT IN HOA – LIÊN HỆ NGAY!>

Quy tắc trình bày:
- Dùng đúng emoji đầu dòng: 🔥 cho tiêu đề (đầu và cuối dòng), 💥 cho câu mở,
  ✅ cho các đặc điểm, 💼 cho địa chỉ, 📍 cho hotline, 📞 cho lời mời nhắn tin,
  👉 cho câu kết.
- Mỗi dòng là một dòng riêng (có xuống dòng \\n). Không dùng markdown như **,
  không dùng gạch đầu dòng "-", không đánh số.
- Có 3–6 dòng ✅, mỗi dòng một ý ngắn gọn.
- Không để dòng trống giữa các dòng; dòng cuối viết IN HOA.

Quy tắc nội dung:
- Viết bằng tiếng Việt, giọng: {tone}.
- Bám sát thông tin shop sau (nếu có): {shop_info}
- Học giọng văn từ các bài mẫu (không chép nguyên văn): {sample_posts}
- Phân loại mặt hàng của ảnh này: {category}
- Ghi chú của shop cho loại hàng này (nếu có): {category_hint}
- Kiểu bài đăng: {style_hint}
- Vị trí bài: {part_hint}
- CHỈ mô tả những gì thực sự nhìn thấy trong ảnh hoặc có trong thông tin shop.
- TUYỆT ĐỐI KHÔNG bịa giá, kích cỡ, chất liệu, thương hiệu, số lượng, khuyến mãi,
  tình trạng hàng nếu không có trong ảnh hoặc thông tin shop. Nếu thiếu, dùng câu
  mời liên hệ thay vì đoán.
- Không dùng từ ngữ phóng đại vi phạm quy định quảng cáo ("số 1", "cam kết 100%"...).
- Mỗi bài phải khác cách mở đầu so với các bài gần đây để tránh bị coi là spam.
- Thêm 3-6 hashtag liên quan.

Các caption gần đây (KHÔNG được mở đầu giống hoặc lặp lại):
{recent_captions}
"""


STYLE_HINTS = {
    "album": (
        "Một lô nhiều ảnh của cùng một loại hàng. Viết như một đợt hàng: nêu điểm "
        "chung của cả lô, mời khách xem hết các ảnh; chỉ nêu số lượng khi đếm được "
        "hoặc thấy rõ trong ảnh."
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
    part: int = 0,
    parts: int = 0,
) -> str:
    samples = "\n---\n".join(sample_posts or []) or "(chưa có)"
    recent = "\n---\n".join(recent_captions or []) or "(chưa có)"
    if parts > 1 and part:
        part_hint = (
            f"bài {part}/{parts} của cùng một lô ảnh — chỉ tả các món trong ảnh của "
            "bài này, mở đầu và câu kết phải khác bài trước"
        )
    else:
        part_hint = "một bài duy nhất cho toàn bộ ảnh của loại hàng này"
    return SYSTEM_PROMPT.format(
        tone=tone or "thân thiện, tự nhiên, ngắn gọn",
        shop_info=shop_info or "(chưa có)",
        sample_posts=samples,
        recent_captions=recent,
        category=category_name or "(không rõ — tự nhận diện từ ảnh)",
        category_hint=category_hint or "(không có)",
        style_hint=STYLE_HINTS.get(style, STYLE_HINTS["album"]),
        part_hint=part_hint,
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
    # Chuẩn hoá xuống dòng: mỗi dòng một ý, bỏ dòng trống thừa
    caption = caption.replace("\r\n", "\n").replace("\r", "\n")
    caption = "\n".join(
        line.strip() for line in caption.split("\n") if line.strip()
    )

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
    schema: dict[str, Any] | None = None,
    temperature: float = 1.0,
) -> str:
    """Gọi Gemini với 1 hoặc nhiều ảnh, trả về text thô. Ném RateLimited khi 429.

    `schema` cho phép tái dùng hàm này cho việc khác (ví dụ phân loại ảnh) mà
    vẫn giữ responseMimeType=application/json.
    """
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
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseSchema": schema or CAPTION_SCHEMA,
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
    part: int = 0,
    parts: int = 0,
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
        part=part,
        parts=parts,
    )
    hashtag_defaults = list(default_hashtags or []) + list(category_hashtags or [])

    chain = available_models([model] + [m for m in (fallback_models or []) if m and m != model])
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
                mark_model_down(candidate)
                last_error = exc
                break
            except (CaptionError, httpx.HTTPError) as exc:
                last_error = exc
                log.warning("Gemini lỗi (%s, lần %d/%d): %s", candidate, attempt + 1, max_retries + 1, exc)
                if attempt < max_retries:
                    time.sleep(retry_wait * (attempt + 1))

    raise CaptionError(f"Thất bại với mọi model ({len(chain)}): {last_error}")

