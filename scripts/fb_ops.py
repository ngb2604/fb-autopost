"""Kiểm tra / dọn bài đã lên lịch trên Facebook Page.

Chạy trong GitHub Actions (nơi có secret FB_PAGE_ACCESS_TOKEN), không chạy
trên máy cá nhân để tránh lộ token.

Ví dụ:
    python scripts/fb_ops.py --action list
    python scripts/fb_ops.py --action delete --post-id 779128505447030_123456
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

GRAPH = "https://graph.facebook.com"
VN = timezone(timedelta(hours=7))


def _get(path: str, params: dict, token: str) -> dict:
    params = {**params, "access_token": token}
    with httpx.Client(timeout=60) as client:
        r = client.get(f"{GRAPH}/{path}", params=params)
    data = r.json() if r.content else {}
    if r.status_code >= 400:
        raise SystemExit(f"Graph API lỗi {r.status_code}: {data}")
    return data


def _delete(path: str, token: str) -> dict:
    with httpx.Client(timeout=60) as client:
        r = client.delete(f"{GRAPH}/{path}", params={"access_token": token})
    data = r.json() if r.content else {}
    if r.status_code >= 400:
        raise SystemExit(f"Graph API lỗi {r.status_code}: {data}")
    return data


def photo_count(post: dict) -> int:
    total = 0
    for att in (post.get("attachments") or {}).get("data") or []:
        subs = (att.get("subattachments") or {}).get("data") or []
        total += len(subs) or 1
    return total


def fmt_ts(value) -> str:
    if not value:
        return "(không rõ)"
    try:
        dt = datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError):
        return str(value)
    return dt.astimezone(VN).strftime("%d/%m/%Y %H:%M")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["list", "delete"], default="list")
    parser.add_argument("--post-id", default="")
    args = parser.parse_args()

    page_id = (os.getenv("FB_PAGE_ID") or "").strip()
    token = (os.getenv("FB_PAGE_ACCESS_TOKEN") or "").strip()
    version = (os.getenv("FB_GRAPH_VERSION") or "v26.0").strip()
    if not page_id or not token:
        raise SystemExit("Thiếu FB_PAGE_ID / FB_PAGE_ACCESS_TOKEN")

    if args.action == "delete":
        if not args.post_id:
            raise SystemExit("Thiếu --post-id")
        res = _delete(f"{version}/{args.post_id}", token)
        print(f"Đã xóa {args.post_id}: {res}")
        return 0

    data = _get(
        f"{version}/{page_id}/scheduled_posts",
        {
            "fields": "id,message,scheduled_publish_time,created_time,attachments{subattachments}",
            "limit": 25,
        },
        token,
    )
    posts = data.get("data") or []
    print(f"Bài đang hẹn giờ trên Page: {len(posts)}")
    for post in posts:
        first_line = (post.get("message") or "").strip().splitlines()[:1]
        print(
            f"- {post.get('id')} | {fmt_ts(post.get('scheduled_publish_time'))} | "
            f"{photo_count(post)} ảnh | {first_line[0][:70] if first_line else ''}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
