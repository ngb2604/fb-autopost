"""Test lập kế hoạch bài đăng (không gọi mạng)."""

from __future__ import annotations

from worker.posts import UNCATEGORIZED, plan_posts, plan_summary

CATS = [
    {"id": "ghe", "name": "Ghế văn phòng", "active": True, "sort_order": 1},
    {"id": "ban", "name": "Bàn làm việc", "active": True, "sort_order": 2},
    {"id": "tu", "name": "Tủ & kệ", "active": True, "sort_order": 3},
]


def photos(category_id: str | None, count: int, start: int = 0) -> list[dict]:
    return [
        {"id": f"{category_id or 'none'}-{i}", "category_id": category_id}
        for i in range(start, start + count)
    ]


def test_gom_album_va_xoay_vong_phan_loai():
    available = photos("ghe", 10) + photos("ban", 20) + photos("tu", 70)
    plans = plan_posts(available, CATS, album_size=4, styles=["album"], max_posts=6)

    assert len(plans) == 6
    assert [p["category_name"] for p in plans] == [
        "Ghế văn phòng",
        "Bàn làm việc",
        "Tủ & kệ",
        "Ghế văn phòng",
        "Bàn làm việc",
        "Tủ & kệ",
    ]
    assert all(p["style"] == "album" for p in plans)
    assert all(len(p["photos"]) == 4 for p in plans)
    # Không có ảnh nào bị dùng 2 lần
    ids = [ph["id"] for p in plans for ph in p["photos"]]
    assert len(ids) == len(set(ids)) == 24


def test_xoay_vong_kieu_dang_album_va_single():
    available = photos("ghe", 10) + photos("ban", 20) + photos("tu", 70)
    plans = plan_posts(
        available, CATS, album_size=4, styles=["album", "single"], max_posts=4
    )

    assert [p["style"] for p in plans] == ["album", "single", "album", "single"]
    assert [len(p["photos"]) for p in plans] == [4, 1, 4, 1]


def test_con_1_anh_thi_thanh_bai_1_anh():
    plans = plan_posts(photos("ghe", 1), CATS, album_size=4, styles=["album"], max_posts=3)
    assert len(plans) == 1
    assert plans[0]["style"] == "single"
    assert len(plans[0]["photos"]) == 1


def test_khong_xoay_vong_thi_dang_het_mot_loai():
    available = photos("ghe", 5) + photos("ban", 5)
    plans = plan_posts(
        available, CATS, album_size=4, styles=["album"], max_posts=10, rotate=False
    )
    assert [p["category_name"] for p in plans] == [
        "Ghế văn phòng",
        "Ghế văn phòng",
        "Bàn làm việc",
        "Bàn làm việc",
    ]
    # 5 ảnh -> 1 album 4 ảnh + 1 bài 1 ảnh
    assert [len(p["photos"]) for p in plans] == [4, 1, 4, 1]


def test_bo_qua_phan_loai_da_tat():
    cats = [
        {"id": "ghe", "name": "Ghế", "active": False, "sort_order": 1},
        {"id": "ban", "name": "Bàn", "active": True, "sort_order": 2},
    ]
    plans = plan_posts(
        photos("ghe", 10) + photos("ban", 10), cats, album_size=4, styles=["album"]
    )
    assert {p["category_name"] for p in plans} == {"Bàn"}


def test_anh_chua_phan_loai_xep_cuoi():
    plans = plan_posts(
        photos(None, 5) + photos("ghe", 5),
        CATS,
        album_size=4,
        styles=["album"],
        max_posts=10,
    )
    assert plans[0]["category_name"] == "Ghế văn phòng"
    assert plans[-1]["category_id"] is None


def test_gioi_han_so_bai_moi_lan_chay():
    plans = plan_posts(photos("ghe", 100), CATS, album_size=4, styles=["album"], max_posts=2)
    assert len(plans) == 2


def test_khong_co_anh():
    assert plan_posts([], CATS) == []
    assert plan_summary([]) == "không có bài nào"
