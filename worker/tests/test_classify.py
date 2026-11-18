"""Test cho AI tự phân loại ảnh (không gọi mạng)."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from worker import classify
from worker.caption import CaptionError
from worker.classify import (
    build_prompt,
    classify_photo,
    match_category,
    normalize,
    parse_result,
)

CATS = [
    {"id": "1", "name": "Ghế văn phòng", "active": True, "hint": "ghế xoay, ghế lưới"},
    {"id": "2", "name": "Bàn làm việc", "active": True, "hint": ""},
    {"id": "3", "name": "Tủ & kệ", "active": True, "hint": ""},
    {"id": "4", "name": "Đồ đã tắt", "active": False, "hint": ""},
]


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buf, format="JPEG")
    return buf.getvalue()


def test_normalize_bo_dau_va_ky_tu():
    assert normalize("Ghế Văn Phòng") == "ghe van phong"
    assert normalize("Tủ & kệ") == "tu ke"
    assert normalize(None) == ""


def test_match_chinh_xac_khong_phan_biet_dau():
    assert match_category("ghe van phong", CATS)["id"] == "1"
    assert match_category("TỦ & KỆ", CATS)["id"] == "3"


def test_match_ten_rut_gon_duy_nhat():
    assert match_category("bàn", CATS)["id"] == "2"


def test_match_khong_ro_rang_tra_none():
    assert match_category("", CATS) is None
    assert match_category("tủ lạnh", CATS) is None  # "tủ" khớp cả "Tủ & kệ" lẫn "Đồ đã tắt"? -> không chắc
    assert match_category("xe máy", CATS) is None


def test_match_bo_qua_loai_da_tat():
    assert match_category("Đồ đã tắt", CATS) is None


def test_parse_result_va_gioi_han_confidence():
    assert parse_result('{"category":"Ghế văn phòng","confidence":0.9}') == (
        "Ghế văn phòng",
        0.9,
    )
    assert parse_result('```json\n{"category":"","confidence":2}\n```') == ("", 1.0)
    assert parse_result('{"category":"x","confidence":"abc"}') == ("x", 0.0)
    with pytest.raises(CaptionError):
        parse_result("khong phai json")


def test_build_prompt_co_du_ten_va_goi_y():
    prompt = build_prompt(CATS)
    assert "- Ghế văn phòng: ghế xoay, ghế lưới" in prompt
    assert "Bàn làm việc" in prompt


def test_classify_photo_map_dung_loai(monkeypatch):
    calls: dict = {}

    def fake_call(**kwargs):
        calls.update(kwargs)
        return '{"category":"Ghế văn phòng","confidence":0.82}'

    monkeypatch.setattr(classify, "_call_gemini", fake_call)
    result = classify_photo(
        api_key="k",
        model="gemini-test",
        image_bytes=_jpeg(),
        categories=CATS,
    )
    assert result["name"] == "Ghế văn phòng"
    assert result["category"]["id"] == "1"
    assert result["confidence"] == pytest.approx(0.82)
    assert calls["schema"] is classify.CLASSIFY_SCHEMA
    assert calls["temperature"] == pytest.approx(0.2)


def test_classify_photo_khong_khop_loai(monkeypatch):
    monkeypatch.setattr(
        classify, "_call_gemini", lambda **kw: '{"category":"","confidence":0.1}'
    )
    result = classify_photo(
        api_key="k", model="m", image_bytes=_jpeg(), categories=CATS
    )
    assert result["category"] is None


def test_classify_photo_bao_loi_khi_chua_co_phan_loai():
    with pytest.raises(CaptionError):
        classify_photo(api_key="k", model="m", image_bytes=_jpeg(), categories=[])


def test_model_down_bi_bo_qua_trong_lan_chay(monkeypatch):
    from worker import caption

    monkeypatch.setattr(caption, "_DOWN_MODELS", {})
    assert caption.available_models(["a", "b"]) == ["a", "b"]
    caption.mark_model_down("a")
    assert caption.available_models(["a", "b"]) == ["b"]
    caption.mark_model_down("b")
    # Tất cả model đều lỗi -> vẫn thử lại model đầu thay vì bỏ cuộc
    assert caption.available_models(["a", "b"]) == ["a"]
