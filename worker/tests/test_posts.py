"""Test lập kế hoạch bài đăng (không gọi mạng)."""

from __future__ import annotations

from worker.posts import UNCATEGORIZED, plan_posts, plan_summary, split_sizes

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


def test_chia_deu_khong_vuot_gioi_han():
    assert split_sizes(11, 2, 20) == [6, 5]
    assert split_sizes(21, 2, 10) == [10, 10]
    assert split_sizes(5, 2, 20) == [3, 2]
    assert split_sizes(0, 2, 20) == [0, 0]


def test_11_anh_gop_1_bai():
    plans = plan_posts(photos("ghe", 11), CATS)

    assert len(plans) == 1
    assert plans[0]["category_name"] == "Ghế văn phòng"
    assert plans[0]["style"] == "album"
    assert len(plans[0]["photos"]) == 11
    assert plans[0]["part"] == 1 and plans[0]["parts"] == 1


def test_qua_gioi_han_thi_chia_2_bai_deu():
    plans = plan_posts(photos("ghe", 25), CATS, max_photos_per_post=20)

    assert len(plans) == 2
    assert [len(p["photos"]) for p in plans] == [13, 12]
    assert [p["part"] for p in plans] == [1, 2]
    assert all(p["parts"] == 2 for p in plans)
    # Không ảnh nào bị dùng 2 lần
    ids = [ph["id"] for p in plans for ph in p["photos"]]
    assert len(ids) == len(set(ids)) == 25


def test_qua_2_bai_thi_de_lai_cho_lan_sau():
    plans = plan_posts(photos("ghe", 45), CATS, max_photos_per_post=20)

    assert len(plans) == 2
    assert [len(p["photos"]) for p in plans] == [20, 20]
    ids = [ph["id"] for p in plans for ph in p["photos"]]
    assert len(ids) == 40
    assert "none-44" not in ids  # 5 ảnh còn lại chờ lần chạy sau


def test_moi_phan_loai_toi_da_2_bai():
    available = photos("ghe", 11) + photos("ban", 3) + photos("tu", 2)
    plans = plan_posts(available, CATS, max_posts_per_run=10)

    by_cat = {}
    for p in plans:
        by_cat.setdefault(p["category_name"], []).append(p)

    assert len(by_cat["Ghế văn phòng"]) == 1
    assert len(by_cat["Bàn làm việc"]) == 1
    assert len(by_cat["Tủ & kệ"]) == 1
    assert [len(p["photos"]) for p in by_cat["Ghế văn phòng"]] == [11]


def test_giu_thu_tu_phan_loai_va_anh_chua_phan_loai_xep_cuoi():
    plans = plan_posts(
        photos(None, 5) + photos("ghe", 5),
        CATS,
        max_posts_per_run=10,
    )
    assert plans[0]["category_name"] == "Ghế văn phòng"
    assert plans[-1]["category_id"] is None
    assert plans[-1]["category_name"] == ""


def test_gioi_han_so_bai_moi_lan_chay():
    available = photos("ghe", 30) + photos("ban", 30) + photos("tu", 30)
    plans = plan_posts(available, CATS, max_posts_per_run=2)

    assert len(plans) == 2
    assert all(p["category_name"] == "Ghế văn phòng" for p in plans)


def test_bo_qua_phan_loai_da_tat():
    cats = [
        {"id": "ghe", "name": "Ghế", "active": False, "sort_order": 1},
        {"id": "ban", "name": "Bàn", "active": True, "sort_order": 2},
    ]
    plans = plan_posts(photos("ghe", 10) + photos("ban", 10), cats)
    assert {p["category_name"] for p in plans} == {"Bàn"}


def test_mot_anh_la_bai_single():
    plans = plan_posts(photos("ghe", 1), CATS)
    assert len(plans) == 1
    assert plans[0]["style"] == "single"
    assert len(plans[0]["photos"]) == 1


def test_khong_co_anh():
    assert plan_posts([], CATS) == []
    assert plan_posts(None, CATS) == []
    assert plan_summary([]) == "không có bài nào"


def test_tom_tat_ke_hoach():
    plans = plan_posts(photos("ghe", 6), CATS)
    assert "Ghế văn phòng (6 ảnh)" in plan_summary(plans)
    assert UNCATEGORIZED
