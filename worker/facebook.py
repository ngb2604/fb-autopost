"""Facebook Graph API: lên lịch / đăng ảnh lên Page.

Tài liệu tham chiếu:
- POST /{page-id}/photos  : https://developers.facebook.com/docs/graph-api/reference/page/photos/
- Lên lịch bài viết      : https://developers.facebook.com/documentation/pages-api/posts
  (scheduled_publish_time phải cách hiện tại từ 10 phút đến 30 ngày)

Nguyên tắc an toàn:
- Chỉ gọi khi ảnh chưa có fb_post_id (worker đã "claim" nguyên tử trong DB).
- Lỗi token (OAuthException / code 190) KHÔNG retry, báo Telegram ngay.
- Lỗi mạng / 5xx: retry tối đa 3 lần với backoff.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com"


class FacebookError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        subcode: int | None = None,
        err_type: str = "",
        fbtrace_id: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.err_type = err_type
        self.fbtrace_id = fbtrace_id

    @property
    def is_token_error(self) -> bool:
        return self.code == 190 or self.err_type == "OAuthException"

    @property
    def is_transient(self) -> bool:
        if self.is_token_error:
            return False
        return self.code in (1, 2, 4, 17, 32, 613) or self.code is None


def _parse_error(r: httpx.Response) -> FacebookError:
    try:
        err = (r.json() or {}).get("error", {})
    except ValueError:
        return FacebookError(f"HTTP {r.status_code}: {r.text[:300]}")

    msg = err.get("message") or f"HTTP {r.status_code}"
    return FacebookError(
        msg,
        code=err.get("code"),
        subcode=err.get("error_subcode"),
        err_type=err.get("type", ""),
        fbtrace_id=err.get("fbtrace_id", ""),
    )


class FacebookClient:
    def __init__(
        self,
        page_id: str,
        token: str,
        version: str,
        *,
        timeout: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        self.page_id = page_id
        self.token = token
        self.version = version
        self.timeout = timeout
        self.max_retries = max_retries
        self.url = f"{GRAPH}/{version}"

    # -- HTTP với retry -----------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    r = client.request(method, f"{self.url}/{path}", **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
                log.warning("Lỗi mạng gọi Facebook (lần %d): %s", attempt + 1, exc)
                time.sleep(2**attempt * 3)
                continue

            if r.status_code < 400:
                return r.json() if r.content else {}

            err = _parse_error(r)
            if err.is_token_error:
                raise err  # không retry
            if r.status_code >= 500 or err.is_transient:
                last = err
                wait = 2**attempt * 5
                log.warning(
                    "Facebook lỗi tạm thời (lần %d/%d): %s — chờ %ss",
                    attempt + 1,
                    self.max_retries,
                    err,
                    wait,
                )
                time.sleep(wait)
                continue
            raise err

        raise FacebookError(f"Thất bại sau {self.max_retries} lần: {last}")

    # -- Kiểm tra token -----------------------------------------------------
    def me(self) -> dict[str, Any]:
        return self._request(
            "GET", "me", params={"fields": "id,name", "access_token": self.token}
        )

    def page_info(self) -> dict[str, Any]:
        return self._request(
            "GET", self.page_id, params={"fields": "id,name,link", "access_token": self.token}
        )

    # -- Đăng / lên lịch ----------------------------------------------------
    def schedule_photo(
        self,
        *,
        image_bytes: bytes,
        message: str,
        scheduled_at: int,
    ) -> str:
        """Lên lịch một ảnh, trả về fb_post_id.

        Cách 1 (chính): POST /{page}/photos với published=false.
        Cách 2 (dự phòng): upload ảnh unpublished rồi POST /{page}/feed với
        attached_media — dùng khi Page/phiên bản API không nhận cách 1.
        """
        try:
            return self._schedule_via_photos(image_bytes, message, scheduled_at)
        except FacebookError as exc:
            if exc.is_token_error:
                raise
            log.warning("Cách 1 thất bại (%s) — thử cách 2 (feed + attached_media)", exc)
            return self._schedule_via_feed(image_bytes, message, scheduled_at)

    def _schedule_via_photos(self, image_bytes: bytes, message: str, scheduled_at: int) -> str:
        data = {
            "message": message,
            "published": "false",
            "scheduled_publish_time": str(scheduled_at),
            "access_token": self.token,
        }
        files = {"source": ("photo.jpg", image_bytes, "image/jpeg")}
        res = self._request("POST", f"{self.page_id}/photos", data=data, files=files)
        post_id = res.get("post_id") or res.get("id")
        if not post_id:
            raise FacebookError(f"Không nhận được id bài đăng: {res}")
        log.info("Đã lên lịch ảnh, post_id=%s", post_id)
        return str(post_id)

    def _schedule_via_feed(self, image_bytes: bytes, message: str, scheduled_at: int) -> str:
        files = {"source": ("photo.jpg", image_bytes, "image/jpeg")}
        uploaded = self._request(
            "POST",
            f"{self.page_id}/photos",
            data={"published": "false", "access_token": self.token},
            files=files,
        )
        photo_id = uploaded.get("id")
        if not photo_id:
            raise FacebookError(f"Upload ảnh thất bại: {uploaded}")
        res = self._request(
            "POST",
            f"{self.page_id}/feed",
            data={
                "message": message,
                "published": "false",
                "scheduled_publish_time": str(scheduled_at),
                "attached_media": f'[{{"media_fbid":"{photo_id}"}}]',
                "access_token": self.token,
            },
        )
        post_id = res.get("id")
        if not post_id:
            raise FacebookError(f"Tạo bài lên lịch thất bại: {res}")
        log.info("Đã lên lịch qua feed, post_id=%s (photo_id=%s)", post_id, photo_id)
        return str(post_id)

    def post_photo_now(self, *, image_bytes: bytes, message: str) -> str:
        """Đăng ngay (dùng cho M1 — thử tay)."""
        files = {"source": ("photo.jpg", image_bytes, "image/jpeg")}
        res = self._request(
            "POST",
            f"{self.page_id}/photos",
            data={"message": message, "access_token": self.token},
            files=files,
        )
        post_id = res.get("post_id") or res.get("id")
        if not post_id:
            raise FacebookError(f"Không nhận được id bài đăng: {res}")
        return str(post_id)

    # -- Khác ---------------------------------------------------------------
    def delete_post(self, post_id: str) -> bool:
        try:
            self._request("DELETE", post_id, params={"access_token": self.token})
            return True
        except FacebookError as exc:
            log.warning("Không xóa được bài %s: %s", post_id, exc)
            return False

    def post_exists(self, post_id: str) -> bool:
        try:
            self._request(
                "GET", post_id, params={"fields": "id", "access_token": self.token}
            )
            return True
        except FacebookError as exc:
            if exc.is_token_error:
                raise
            return False


def permalink(post_id: str) -> str:
    """Link công khai tới bài viết (dạng chuẩn của Graph API)."""
    return f"https://www.facebook.com/{post_id}"
