"""Test cho phần parse/validate JSON caption và các quy tắc an toàn."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from worker.caption import (
    CaptionError,
    build_prompt,
    needs_manual_review,
    normalize_hashtags,
    parse_caption_json,
    prepare_image,
)

GOOD = """
{
  "product_type": "áo thun",
  "caption": "Áo thun trắng basic\\nChất vải mềm, dễ phối đồ 😊\\nInbox shop để chọn size nhé!",
  "hashtags": ["ao thun", "#thoi trang", "#ÁO"],
  "confidence": 0.86,
  "warnings": []
}
"""


def test_parse_hop_le():
    r = parse_caption_json(GOOD)
    assert r["product_type"] == "áo thun"
    assert r["confidence"] == pytest.approx(0.86)
    assert r["hashtags"] == ["#aothun", "#thoitrang", "#ao"]


def test_parse_bo_code_fence():
    r = parse_caption_json("```json\n" + GOOD + "\n```")
    assert "Inbox" in r["caption"]


def test_json_hong_bi_tu_choi():
    with pytest.raises(CaptionError):
        parse_caption_json("{khong phai json")
    with pytest.raises(CaptionError):
        parse_caption_json("")
    with pytest.raises(CaptionError):
        parse_caption_json('{"product_type":"x","hashtags":[]}')


def test_confidence_bi_gioi_han():
    r = parse_caption_json(
        '{"caption":"a","hashtags":[],"confidence":9,"warnings":[],"product_type":""}'
    )
    assert r["confidence"] == 1.0
    r = parse_caption_json(
        '{"caption":"a","hashtags":[],"confidence":"x","warnings":[],"product_type":""}'
    )
    assert r["confidence"] == 0.0


def test_hashtag_mac_dinh_duoc_gop_va_gioi_han_6():
    tags = normalize_hashtags(["#a", "b"], defaults=["#c", "#a", "#d", "#e", "#f", "#g"])
    assert len(tags) == 6
    assert tags[0] == "#a"


def test_hashtag_bo_dau_tieng_viet():
    tags = normalize_hashtags(["#ghế văn phòng", "Đồ gỗ cũ", "#thanh-ly"])
    assert tags == ["#ghevanphong", "#dogocu", "#thanhly"]


def test_buoc_duyet_tay_khi_tin_cay_thap():
    assert needs_manual_review({"confidence": 0.3, "warnings": []})[0] is True
    assert needs_manual_review({"confidence": 0.9, "warnings": ["ảnh mờ"]})[0] is True
    ok, reason = needs_manual_review({"confidence": 0.9, "warnings": []})
    assert ok is False and reason == ""


def test_prompt_co_du_thong_tin_shop_va_bai_mau():
    p = build_prompt(
        tone="thân thiện",
        shop_info="Shop A, 0912",
        sample_posts=["Bài mẫu 1"],
        recent_captions=["Caption cũ"],
    )
    assert "Shop A" in p and "Bài mẫu 1" in p and "Caption cũ" in p
    assert "KHÔNG bịa giá" in p


def test_prompt_co_phan_loai_va_kieu_bai():
    p = build_prompt(
        tone="thân thiện",
        shop_info="",
        sample_posts=None,
        recent_captions=None,
        category_name="Ghế văn phòng",
        category_hint="Ghế lưới còn tốt",
        style="single",
    )
    assert "Ghế văn phòng" in p
    assert "Ghế lưới còn tốt" in p
    assert "Bài một ảnh" in p


def test_prompt_mac_dinh_la_album():
    p = build_prompt(
        tone="", shop_info="", sample_posts=None, recent_captions=None
    )
    assert "lô nhiều ảnh" in p


def test_prompt_co_bo_cuc_emoji_va_xuong_dong():
    p = build_prompt(
        tone="", shop_info="", sample_posts=None, recent_captions=None,
        category_name="Ghế văn phòng", style="album",
    )
    for symbol in ("🔥", "💥", "✅", "💼", "📍", "📞", "👉"):
        assert symbol in p
    assert "xuống dòng" in p
    assert "Địa chỉ" in p and "Hotline" in p


def test_prompt_bao_vi_tri_bai_khi_chia_2():
    p = build_prompt(
        tone="", shop_info="", sample_posts=None, recent_captions=None,
        style="album", part=2, parts=2,
    )
    assert "bài 2/2" in p


def test_caption_duoc_chuan_hoa_xuong_dong():
    raw = (
        '{"caption":"Dòng 1\\r\\n\\r\\n  Dòng 2  \\n\\nDòng 3",'
        '"hashtags":[],"confidence":0.9,"warnings":[],"product_type":""}'
    )
    assert parse_caption_json(raw)["caption"] == "Dòng 1\nDòng 2\nDòng 3"


def test_prepare_image_resize_va_xoay():
    img = Image.new("RGB", (3000, 1200), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out = prepare_image(buf.getvalue(), max_edge=1600)
    with Image.open(io.BytesIO(out)) as resized:
        assert resized.format == "JPEG"
        assert max(resized.size) <= 1600
