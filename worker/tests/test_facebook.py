"""Test phân loại lỗi Facebook (không gọi mạng)."""

from __future__ import annotations

import httpx
import pytest

from worker.facebook import FacebookClient, FacebookError, _parse_error, permalink


def _resp(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("POST", "https://x"))


def test_loi_token_khong_retry():
    err = _parse_error(
        _resp(400, {"error": {"message": "Invalid OAuth access token", "type": "OAuthException", "code": 190}})
    )
    assert err.is_token_error is True
    assert err.is_transient is False


def test_loi_tam_thoi_thi_retry():
    err = _parse_error(_resp(500, {"error": {"message": "Unknown error", "code": 2}}))
    assert err.is_token_error is False
    assert err.is_transient is True


def test_loi_khong_hop_le_van_tao_exception():
    r = httpx.Response(502, text="bad gateway", request=httpx.Request("GET", "https://x"))
    err = _parse_error(r)
    assert "502" in str(err)


def test_permalink():
    assert permalink("123_456").endswith("/123_456")


def test_client_dung_version_trong_url():
    c = FacebookClient("page1", "token", "v26.0", max_retries=1)
    assert c.url == "https://graph.facebook.com/v26.0"


def test_schedule_post_mot_anh_di_duong_photos(monkeypatch):
    c = FacebookClient("page1", "token", "v26.0", max_retries=1)
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"post_id": "1_2"}

    monkeypatch.setattr(c, "_request", fake_request)
    pid = c.schedule_post(images=[b"img"], message="xin chào", scheduled_at=1700000000)

    assert pid == "1_2"
    assert calls[0][1] == "page1/photos"
    assert calls[0][2]["data"]["scheduled_publish_time"] == "1700000000"


def test_schedule_post_nhieu_anh_dung_feed_attached_media(monkeypatch):
    c = FacebookClient("page1", "token", "v26.0", max_retries=1)
    uploaded = {"n": 0}
    feed = {}

    def fake_request(method, path, **kwargs):
        if path.endswith("/photos"):
            uploaded["n"] += 1
            return {"id": f"photo{uploaded['n']}"}
        feed.update(kwargs.get("data") or {})
        return {"id": "page1_999"}

    monkeypatch.setattr(c, "_request", fake_request)
    pid = c.schedule_post(
        images=[b"a", b"b", b"c"], message="album", scheduled_at=1700000000
    )

    assert pid == "page1_999"
    assert uploaded["n"] == 3
    assert feed["published"] == "false"
    assert feed["scheduled_publish_time"] == "1700000000"
    assert feed["attached_media[0]"] == '{"media_fbid": "photo1"}'
    assert feed["attached_media[2]"] == '{"media_fbid": "photo3"}'
