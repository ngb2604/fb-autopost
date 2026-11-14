"""M1 — Thử đăng/lên lịch MỘT ảnh lên Page bằng Graph API.

Cách dùng:
    python -m worker.facebook_test anh.jpg "Caption thử nghiệm"            # đăng ngay
    python -m worker.facebook_test anh.jpg "Caption thử" --schedule 60     # lên lịch sau 60 phút

Cần biến môi trường: FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN (và tùy chọn FB_GRAPH_VERSION).
Có thể đặt trong file .env (xem .env.example) — script tự nạp.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    """Nạp .env đơn giản (không cần thư viện ngoài)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    load_dotenv()
    from .config import Config
    from .facebook import FacebookClient, FacebookError, permalink

    parser = argparse.ArgumentParser(description="Đăng thử 1 ảnh lên Facebook Page")
    parser.add_argument("image", help="Đường dẫn file ảnh")
    parser.add_argument("caption", help="Nội dung bài đăng")
    parser.add_argument("--schedule", type=int, default=0, help="Lên lịch sau N phút")
    parser.add_argument("--delete", action="store_true", help="Xóa bài ngay sau khi đăng (để test)")
    args = parser.parse_args()

    cfg = Config.from_env()
    cfg.require("fb_page_id", "fb_page_token")

    fb = FacebookClient(cfg.fb_page_id, cfg.fb_page_token, cfg.fb_graph_version)

    print("→ Kiểm tra Page...")
    info = fb.page_info()
    print(f"  Page: {info.get('name')} ({info.get('id')})")

    raw = Path(args.image).read_bytes()
    try:
        if args.schedule:
            when = datetime.now(timezone.utc) + timedelta(minutes=args.schedule)
            print(f"→ Lên lịch lúc {when.isoformat()} ...")
            post_id = fb.schedule_photo(
                image_bytes=raw, message=args.caption, scheduled_at=int(when.timestamp())
            )
        else:
            print("→ Đăng ngay...")
            post_id = fb.post_photo_now(image_bytes=raw, message=args.caption)
    except FacebookError as exc:
        print(f"✗ Lỗi Facebook: {exc} (code={exc.code}, type={exc.err_type})")
        return 1

    print(f"✓ Thành công: post_id={post_id}")
    print(f"  Link: {permalink(post_id)}")

    if args.delete:
        print("→ Xóa bài test...")
        print("✓ Đã xóa" if fb.delete_post(post_id) else "✗ Không xóa được (xóa tay trên Facebook)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
