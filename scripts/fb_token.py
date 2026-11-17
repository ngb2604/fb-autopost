"""Lấy Page Access Token từ User Access Token và nạp vào GitHub Secrets.

Cách dùng (chạy trên máy, đã đăng nhập `gh`):

    python scripts/fb_token.py --token "EAAG..."

Script sẽ:
1. Gọi /me/accounts để liệt kê các Page bạn quản lý.
2. Chọn Page (tự động nếu chỉ có 1 Page, hoặc dùng --page-id).
3. Nạp secret FB_PAGE_ID và FB_PAGE_ACCESS_TOKEN lên GitHub.
4. Gọi thử Graph API để xác nhận token dùng được.

Token KHÔNG được ghi ra file nào.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

import httpx

GRAPH = "https://graph.facebook.com"


def call(path: str, token: str, version: str, **params) -> dict:
    params["access_token"] = token
    r = httpx.get(f"{GRAPH}/{version}/{path}", params=params, timeout=30)
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        raise SystemExit(f"Graph API lỗi: {json.dumps(data.get('error', data), ensure_ascii=False)}")
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="User access token (từ Graph API Explorer)")
    ap.add_argument("--page-id", default="", help="Chọn Page cụ thể nếu có nhiều Page")
    ap.add_argument("--version", default="v26.0")
    ap.add_argument("--dry-run", action="store_true", help="Chỉ in ra, không nạp secret")
    args = ap.parse_args()

    me = call("me", args.token, args.version, fields="id,name")
    print(f"Tài khoản: {me.get('name')} (id {me.get('id')})")

    accounts = call("me/accounts", args.token, args.version, fields="id,name,access_token,tasks", limit=100)
    pages = accounts.get("data") or []
    if not pages:
        raise SystemExit(
            "Không thấy Page nào. Kiểm tra: bạn là admin của Page và token có quyền pages_show_list."
        )

    print(f"\nTìm thấy {len(pages)} Page:")
    for p in pages:
        print(f"  - {p['name']}  (id {p['id']})")

    if args.page_id:
        page = next((p for p in pages if p["id"] == args.page_id), None)
        if page is None:
            raise SystemExit(f"Không tìm thấy Page id {args.page_id}")
    elif len(pages) == 1:
        page = pages[0]
    else:
        raise SystemExit("Có nhiều Page — chạy lại với --page-id <id>.")

    page_id = page["id"]
    page_token = page.get("access_token") or ""

    if not page_token:
        print("\n⚠️ Không nhận được page access_token. Hãy cấp quyền pages_manage_posts rồi thử lại.")
        return 1

    print(f"\nChọn Page: {page['name']} ({page_id})")

    info = call(page_id, page_token, args.version, fields="id,name,fan_count")
    print(f"Kiểm tra token Page: OK — {info.get('name')} | {info.get('fan_count', '?')} người theo dõi")

    if args.dry_run:
        print("\n(dry-run) Không nạp secret. Page token dài:", len(page_token), "ký tự")
        return 0

    for name, value in (("FB_PAGE_ID", page_id), ("FB_PAGE_ACCESS_TOKEN", page_token)):
        print(f"Đang nạp secret {name}...")
        proc = subprocess.run(
            ["gh", "secret", "set", name],
            input=value,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(proc.stderr.strip()[:300])
            raise SystemExit(f"Không nạp được secret {name}")

    print("\n✅ Đã nạp FB_PAGE_ID và FB_PAGE_ACCESS_TOKEN lên GitHub Secrets.")
    print("Chạy thử: gh workflow run \"Worker đăng bài\" -f dry_run=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
