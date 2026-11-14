"""Test cho phần chia khung giờ (không gọi mạng)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from worker.scheduler import (
    build_slots,
    coverage_days,
    normalize_windows,
    parse_hhmm,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")


def vn(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=VN)


def test_parse_hhmm():
    assert parse_hhmm("09:30") == parse_hhmm("9:30")
    assert parse_hhmm("20:00").hour == 20


def test_normalize_windows_giai_dung_so_bai():
    assert len(normalize_windows(["09:00", "12:00", "20:00"], 3)) == 3
    # nhiều hơn số bài -> giảm xuống
    out = normalize_windows(["08:00", "10:00", "12:00", "15:00", "20:00"], 3)
    assert len(out) == 3
    # ít hơn số bài -> nội suy
    out = normalize_windows(["09:00", "20:00"], 4)
    assert len(out) == 4
    assert out[0].hour == 9 and out[-1].hour == 20
    # rỗng -> mặc định
    assert len(normalize_windows([], 2)) == 2


def test_mac_dinh_3_bai_dung_khung_gio():
    now = vn(2026, 11, 14, 6, 0)
    slots = build_slots(
        now=now,
        posts_per_day=3,
        time_windows=["09:00", "12:00", "20:00"],
        jitter_minutes=0,
        timezone_name="Asia/Ho_Chi_Minh",
        horizon_days=1,
        taken=[],
        count=3,
        rng=random.Random(1),
    )
    assert len(slots) == 3
    local_hours = sorted(s.astimezone(VN).hour for s in slots)
    assert local_hours == [9, 12, 20]
    # đã chuyển sang UTC
    assert all(s.tzinfo is not None for s in slots)


def test_khong_vuot_qua_posts_per_day_khi_da_co_bai():
    now = vn(2026, 11, 14, 0, 5)
    taken = [vn(2026, 11, 14, 9, 0), vn(2026, 11, 14, 12, 0)]
    slots = build_slots(
        now=now,
        posts_per_day=3,
        time_windows=["09:00", "12:00", "20:00"],
        jitter_minutes=0,
        timezone_name="Asia/Ho_Chi_Minh",
        horizon_days=2,
        taken=taken,
        count=5,
        rng=random.Random(7),
    )
    per_day: dict = {}
    for s in slots:
        d = s.astimezone(VN).date()
        per_day[d] = per_day.get(d, 0) + 1
    # ngày 14 đã có 2 bài -> chỉ thêm 1 (20:00), ngày 15 thêm 3
    assert per_day[vn(2026, 11, 14).date()] == 1
    assert per_day[vn(2026, 11, 15).date()] == 3
    assert len(slots) == 4


def test_bo_qua_gio_da_qua_va_ton_trong_lead_time():
    now = vn(2026, 11, 14, 9, 5)  # 09:00 đã qua
    slots = build_slots(
        now=now,
        posts_per_day=3,
        time_windows=["09:00", "12:00", "20:00"],
        jitter_minutes=0,
        timezone_name="Asia/Ho_Chi_Minh",
        horizon_days=1,
        taken=[],
        count=3,
        rng=random.Random(2),
    )
    # 09:00 bị loại vì < now + 15 phút
    assert all(s > now + timedelta(minutes=10) for s in slots)
    assert len(slots) == 2


def test_gioi_han_horizon_va_gioi_han_facebook():
    now = vn(2026, 11, 14, 0, 0)
    slots = build_slots(
        now=now,
        posts_per_day=3,
        time_windows=["09:00", "12:00", "20:00"],
        jitter_minutes=0,
        timezone_name="Asia/Ho_Chi_Minh",
        horizon_days=3,
        taken=[],
        count=100,
        rng=random.Random(3),
    )
    assert len(slots) == 9  # 3 ngày x 3 bài
    # Facebook: từ 10 phút đến 30 ngày. Ta dùng 15 phút .. 29 ngày
    for s in slots:
        delta = (s - now).total_seconds()
        assert 15 * 60 - 61 <= delta <= 29 * 24 * 3600


def test_jitter_trong_gioi_han_va_tai_lap_duoc():
    now = vn(2026, 11, 14, 0, 0)
    kwargs = dict(
        now=now,
        posts_per_day=2,
        time_windows=["09:00", "20:00"],
        jitter_minutes=10,
        timezone_name="Asia/Ho_Chi_Minh",
        horizon_days=1,
        taken=[],
        count=2,
    )
    a = build_slots(**kwargs, rng=random.Random(42))
    b = build_slots(**kwargs, rng=random.Random(42))
    assert a == b
    for slot, base_hour in zip(a, [9, 20]):
        local = slot.astimezone(VN)
        delta_min = abs((local.hour * 60 + local.minute) - (base_hour * 60))
        assert delta_min <= 10


def test_coverage_days():
    assert coverage_days(approved_count=6, posts_per_day=3, scheduled_count=0, timezone_name="Asia/Ho_Chi_Minh") == 2.0
    assert coverage_days(approved_count=0, posts_per_day=3, scheduled_count=1, timezone_name="Asia/Ho_Chi_Minh") == 0.3
