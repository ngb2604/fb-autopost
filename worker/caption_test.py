"""M2 — Chạy thử Gemini với một ảnh cục bộ, in JSON kết quả.

Cách dùng:
    python -m worker.caption_test anh.jpg
    python -m worker.caption_test anh.jpg --tone "vui vẻ" --shop "Shop A, 0912..."

Cần GEMINI_API_KEY (đặt trong .env hoặc biến môi trường).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .facebook_test import load_dotenv


def main() -> int:
    load_dotenv()
    from .caption import CaptionError, RateLimited, generate_caption, needs_manual_review
    from .config import Config

    parser = argparse.ArgumentParser(description="Thử viết caption bằng Gemini")
    parser.add_argument("image")
    parser.add_argument("--tone", default="thân thiện, tự nhiên, ngắn gọn")
    parser.add_argument("--shop", default="")
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    cfg = Config.from_env()
    cfg.require("gemini_api_key")
    model = args.model or cfg.gemini_model

    raw = Path(args.image).read_bytes()
    try:
        result = generate_caption(
            api_key=cfg.gemini_api_key,
            model=model,
            image_bytes=raw,
            tone=args.tone,
            shop_info=args.shop,
            sample_posts=[],
            recent_captions=[],
            default_hashtags=[],
        )
    except RateLimited as exc:
        print(f"⏳ Bị giới hạn tốc độ (429): {exc}")
        return 2
    except CaptionError as exc:
        print(f"✗ Lỗi: {exc}")
        return 1

    manual, reason = needs_manual_review(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nModel: {model}")
    print(f"Bắt buộc duyệt tay: {manual} {('(' + reason + ')') if reason else ''}")
    print("\n--- Bài sẽ đăng ---")
    print(result["caption"])
    print(" ".join(result["hashtags"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
