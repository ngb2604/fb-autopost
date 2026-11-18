"""Đổi User Access Token ngắn hạn thành token dài hạn (60 ngày) rồi nạp Page token vào GitHub Secrets.

Cách dùng (chạy trên máy, đã đăng nhập `gh`):

    python scripts/fb_token.py --token "EAAG..." --app-secret "abc123..."

Hoặc đặt FB_APP_SECRET trong .env rồi chỉ cần --token.

Script sẽ:
1. Đổi token ngắn hạn -> token dài hạn bằng `fb_exchange_token`.
2. Gọi /me/accounts để lấy Page Access Token.
3. Kiểm tra hạn dùng của cả hai token (in ra ngày hết hạn).
4. Nạp secret FB_PAGE_ID, FB_PAGE_ACCESS_TOKEN và FB_USER_TOKEN lên GitHub.

Token KHÔNG được ghi ra file nào.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone

import httpx

GRAPH = "https://graph.facebook.com"


def call(path: str, token: str, version: str, **params) -> dict:
    params["access_token"] = token
    r = httpx.get(f"{GRAPH}/{version}/{path}", params=params, timeout=30)
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        raise SystemExit(f"Graph API lỗi: {json.dumps(data.get('error', data), ensure_ascii=False)}")
    return data


def exchange(app_id: str, app_secret: str, short_token: str, version: str) -> dict:
    r = httpx.get(
        f"{GRAPH}/{version}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=30,
    )
    data = r.json()
    if r.status_code >= 400 or "error" in data:
        raise SystemExit(
            "Không đổi được token dài hạn: "
            + json.dumps(data.get("error", data), ensure_ascii=False)
        )
    return data


def debug(token: str, app_token: str, version: str) -> dict:
    data = call(
        "debug_token", app_token, version, input_token=token
    )
    return data.get("data") or {}


def fmt_expiry(ts: int) -> str:
    if not ts:
        return "không hết hạn"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


def read_env_secret(name: str) -> str:
    """Đọc giá trị từ biến môi trường hoặc file .env ở thư mục gốc repo."""
    if os.environ.get(name):
        return os.environ[name]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def set_secret(name: str, value: str) -> None:
    print(f"Đang nạp secret {name}...")
    proc = subprocess.run(["gh", "secret", "set", name], input=value, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr.strip()[:300])
        raise SystemExit(f"Không nạp được secret {name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="User access token ngắn hạn (Graph API Explorer)")
    ap.add_argument("--app-secret", default="", help="App Secret (hoặc đặt FB_APP_SECRET trong .env)")
    ap.add_argument("--app-id", default="", help="App ID (mặc định đọc APP_ID trong .env)")
    ap.add_argument("--page-id", default="", help="Chọn Page cụ thể nếu có nhiều Page")
    ap.add_argument("--version", default="v27.0")
    ap.add_argument("--dry-run", action="store_true", help="Chỉ in ra, không nạp secret")
    args = ap.parse_args()

    app_secret = args.app_secret or read_env_secret("FB_APP_SECRET")
    app_id = args.app_id or read_env_secret("FB_APP_ID")

    user_token = args.token.strip()
    if app_secret and app_id:
        print("Đang đổi sang token dài hạn...")
        data = exchange(app_id, app_secret, user_token, args.version)
        user_token = data.get("access_token", user_token)
        if data.get("expires_in"):
            print(f"Token dài hạn: {data['expires_in']} giây (~{data['expires_in'] // 86400} ngày)")
    else:
        print("⚠️ Không có app id/secret — dùng token như đang có (có thể chỉ sống ~1 giờ).")

    me = call("me", user_token, args.version, fields="id,name")
    print(f"Tài khoản: {me.get('name')} (id {me.get('id')})")

    accounts = call("me/accounts", user_token, args.version, fields="id,name,access_token,tasks", limit=100)
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

    if app_secret:
        app_token = f"{app_id}|{app_secret}"
        u = debug(user_token, app_token, args.version)
        p = debug(page_token, app_token, args.version)
        print(f"  User token hết hạn: {fmt_expiry(int(u.get('expires_at') or 0))}")
        print(f"  Page token hết hạn: {fmt_expiry(int(p.get('expires_at') or 0))}")

    if args.dry_run:
        print("\n(dry-run) Không nạp secret.")
        return 0

    set_secret("FB_PAGE_ID", page_id)
    set_secret("FB_PAGE_ACCESS_TOKEN", page_token)
    if app_secret:
        set_secret("FB_USER_TOKEN", user_token)
        set_secret("FB_APP_SECRET", app_secret)
        set_secret("FB_APP_ID", app_id)

    print("\n✅ Đã nạp secret Facebook lên GitHub.")
    print('Chạy thử: gh workflow run "Worker đăng bài" -f dry_run=true')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
