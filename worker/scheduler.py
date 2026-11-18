"""Chia khung giờ đăng bài.

Toàn bộ logic ở file này là hàm thuần (không gọi mạng) để test được.
Mọi tính toán theo múi giờ trong settings (mặc định Asia/Ho_Chi_Minh),
kết quả trả về datetime đã có timezone (UTC) để lưu DB.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import FB_MAX_AHEAD_SECONDS, FB_MIN_LEAD_SECONDS


def parse_hhmm(value: str) -> time:
    hh, mm = value.strip().split(":")
    return time(int(hh), int(mm))


def normalize_windows(windows: list[str] | None, posts_per_day: int) -> list[time]:
    """Chuẩn hóa danh sách khung giờ về đúng `posts_per_day` mốc.

    - Nếu người dùng nhập nhiều hơn số bài/ngày: chọn các mốc rải đều.
    - Nếu nhập ít hơn: nội suy thêm mốc giữa mốc đầu và mốc cuối.
    - Nếu rỗng: dùng khung mặc định 08:00 → 21:00.
    """
    parsed: list[time] = []
    for w in windows or []:
        try:
            parsed.append(parse_hhmm(str(w)))
        except (ValueError, AttributeError):
            continue
    parsed = sorted(set(parsed))

    n = max(1, int(posts_per_day))
    if not parsed:
        parsed = [time(8, 0), time(21, 0)]

    if len(parsed) == n:
        return parsed

    if len(parsed) > n:
        # Rải đều trên danh sách đã có, luôn giữ mốc đầu và cuối
        if n == 1:
            return [parsed[len(parsed) // 2]]
        step = (len(parsed) - 1) / (n - 1)
        return [parsed[round(i * step)] for i in range(n)]

    # len(parsed) < n: nội suy tuyến tính giữa mốc đầu và cuối
    start_min = parsed[0].hour * 60 + parsed[0].minute
    end_min = parsed[-1].hour * 60 + parsed[-1].minute
    if end_min <= start_min:
        end_min = start_min + 60 * (n - 1)
    step = (end_min - start_min) / (n - 1) if n > 1 else 0
    out: list[time] = []
    for i in range(n):
        total = int(round(start_min + i * step)) % (24 * 60)
        out.append(time(total // 60, total % 60))
    return out


def day_key(dt: datetime, tz: ZoneInfo) -> date:
    return dt.astimezone(tz).date()


def build_slots(
    *,
    now: datetime,
    posts_per_day: int,
    time_windows: list[str] | None,
    jitter_minutes: int,
    timezone_name: str,
    horizon_days: int,
    taken: list[datetime],
    count: int,
    rng: random.Random | None = None,
) -> list[datetime]:
    """Trả về tối đa `count` khung giờ trống (UTC), sớm nhất trước.

    Quy tắc:
    - Mỗi ngày (theo múi giờ địa phương) không vượt quá `posts_per_day`,
      tính cả các bài đã `scheduled`/`posted` (tham số `taken`).
    - Khung giờ phải cách hiện tại ít nhất 15 phút và không quá 29 ngày
      (giới hạn của Facebook).
    - Chỉ xếp trong `horizon_days` ngày tới.
    - `rng` cho phép tái lập kết quả trong test.
    """
    rng = rng or random.Random()
    tz = ZoneInfo(timezone_name)
    now = now.astimezone(timezone.utc)
    earliest = now + timedelta(seconds=FB_MIN_LEAD_SECONDS)
    latest = now + timedelta(seconds=FB_MAX_AHEAD_SECONDS)

    windows = normalize_windows(time_windows, posts_per_day)
    jitter = max(0, min(int(jitter_minutes), 120))

    # Đếm số bài đã chiếm theo từng ngày địa phương
    used: dict[date, int] = {}
    for dt in taken:
        used[day_key(dt, tz)] = used.get(day_key(dt, tz), 0) + 1

    slots: list[datetime] = []
    start_day = now.astimezone(tz).date()
    for offset in range(max(1, int(horizon_days))):
        if len(slots) >= count:
            break
        local_day = start_day + timedelta(days=offset)
        for t in windows:
            if len(slots) >= count:
                break
            if used.get(local_day, 0) >= max(1, int(posts_per_day)):
                break

            delta = rng.randint(-jitter, jitter) if jitter else 0
            naive = datetime.combine(local_day, t) + timedelta(minutes=delta)
            slot = naive.replace(tzinfo=tz).astimezone(timezone.utc)

            if slot < earliest or slot > latest:
                continue
            if any(abs((slot - other).total_seconds()) < 300 for other in slots):
                continue  # hai bài sát nhau quá 5 phút -> dời bài sau

            slots.append(slot)
            used[local_day] = used.get(local_day, 0) + 1

    return slots


def slots_needed(
    *,
    posts_per_day: int,
    pending_approved: int,
    horizon_days: int,
    taken: list[datetime],
    now: datetime,
    timezone_name: str,
    time_windows: list[str] | None,
    jitter_minutes: int,
) -> list[datetime]:
    """Tính đúng số khung giờ cần cho các ảnh đã duyệt (tối đa horizon_days)."""
    if pending_approved <= 0:
        return []
    return build_slots(
        now=now,
        posts_per_day=posts_per_day,
        time_windows=time_windows,
        jitter_minutes=jitter_minutes,
        timezone_name=timezone_name,
        horizon_days=horizon_days,
        taken=taken,
        count=pending_approved,
    )


def coverage_days(
    *,
    approved_count: int,
    posts_per_day: int,
    scheduled_count: int,
    timezone_name: str,
) -> float:
    """Số ngày còn đủ ảnh để đăng (dùng để cảnh báo "sắp hết ảnh")."""
    total = max(0, approved_count) + max(0, scheduled_count)
    per_day = max(1, int(posts_per_day))
    return round(total / per_day, 1)


def order_by_category(
    photos: list[dict],
    *,
    enabled: bool = True,
) -> list[dict]:
    """Xen kẽ ảnh theo phân loại (round-robin) để các loại hàng luân phiên nhau.

    - Đầu vào đã sắp theo thứ tự mong muốn (thường là created_at tăng dần).
    - Trong mỗi loại giữ nguyên thứ tự đó.
    - Ảnh chưa gán loại được coi là một nhóm riêng (xếp cuối).
    - `enabled=False` thì trả về nguyên thứ tự ban đầu.
    """
    if not enabled or not photos:
        return list(photos)

    buckets: dict[str, list[dict]] = {}
    order: list[str] = []
    for p in photos:
        key = p.get("category_id") or ""
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(p)

    # Nhóm "chưa phân loại" luôn xếp sau cùng
    order = [k for _, k in sorted(enumerate(order), key=lambda t: (t[1] == "", t[0]))]

    out: list[dict] = []
    while True:
        progressed = False
        for key in order:
            if buckets[key]:
                out.append(buckets[key].pop(0))
                progressed = True
        if not progressed:
            break
    return out

