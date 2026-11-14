"""M2 â€” Cháº¡y thá»­ Gemini vá»›i má»™t áº£nh cá»¥c bá»™, in JSON káº¿t quáº£.

CÃ¡ch dÃ¹ng:
    python -m worker.caption_test anh.jpg
    python -m worker.caption_test anh.jpg --tone "vui váº»" --shop "Shop A, 0912..."

Cáº§n GEMINI_API_KEY (Ä‘áº·t trong .env hoáº·c biáº¿n mÃ´i trÆ°á»ng).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .env import load_dotenv


def main() -> int:
    load_dotenv()
    from .caption import CaptionError, RateLimited, generate_caption, needs_manual_review
    from .config import Config

    parser = argparse.ArgumentParser(description="Thá»­ viáº¿t caption báº±ng Gemini")
    parser.add_argument("image")
    parser.add_argument("--tone", default="thÃ¢n thiá»‡n, tá»± nhiÃªn, ngáº¯n gá»n")
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
            fallback_models=cfg.gemini_fallbacks,
            image_bytes=raw,
            tone=args.tone,
            shop_info=args.shop,
            sample_posts=[],
            recent_captions=[],
            default_hashtags=[],
        )
    except RateLimited as exc:
        print(f"â³ Bá»‹ giá»›i háº¡n tá»‘c Ä‘á»™ (429): {exc}")
        return 2
    except CaptionError as exc:
        print(f"âœ— Lá»—i: {exc}")
        return 1

    manual, reason = needs_manual_review(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nModel: {model}")
    print(f"Báº¯t buá»™c duyá»‡t tay: {manual} {('(' + reason + ')') if reason else ''}")
    print("\n--- BÃ i sáº½ Ä‘Äƒng ---")
    print(result["caption"])
    print(" ".join(result["hashtags"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

