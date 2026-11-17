"""Điểm vào của worker: chạy bởi GitHub Actions (cron hoặc workflow_dispatch).

Luồng (mục 7.2 của đặc tả):
1. Đọc settings; nếu paused -> thoát.
2. Xử lý ảnh `pending`: Gemini viết caption -> `captioned` (nếu duyệt tay)
   hoặc `approved`.
3. Xếp lịch ảnh `approved`: tính khung giờ trống -> gọi Facebook
   (`published=false`, `scheduled_publish_time`) -> `scheduled`.
4. Đồng bộ: ảnh `scheduled` đã qua giờ -> `posted`.
5. Dọn ảnh cũ trong Storage theo `retention_days`.
6. Gửi Telegram tổng kết + cảnh báo khi sắp hết ảnh.

Chạy thử:  python -m worker.main --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from . import db as database
from .env import load_dotenv
from . import notify
from .caption import CaptionError, RateLimited, generate_caption, needs_manual_review
from .config import Config
from .facebook import FacebookClient, FacebookError
from .scheduler import build_slots, coverage_days

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("worker")


def build_message(caption: str, hashtags: list[str] | None) -> str:
    tags = " ".join(hashtags or [])
    return f"{caption.strip()}\n\n{tags}".strip()


def recent_captions(db: database.Supabase, limit: int = 5) -> list[str]:
    rows = db.select(
        "photos",
        {
            "select": "caption",
            "caption": "not.is.null",
            "order": "created_at.desc",
            "limit": str(limit),
        },
    )
    return [r["caption"] for r in rows if r.get("caption")]


# ---------------------------------------------------------------------------
# Bước 2: viết caption cho ảnh pending
# ---------------------------------------------------------------------------

def process_pending(
    db: database.Supabase,
    cfg: Config,
    settings: dict[str, Any],
    stats: dict[str, int],
    notes: list[str],
) -> None:
    limit = max(1, cfg.max_photos_per_run)
    photos = database.list_photos(db, ["pending"], limit=limit)
    if not photos:
        log.info("Không có ảnh pending.")
        return

    log.info("Xử lý %d ảnh pending...", len(photos))
    history = recent_captions(db)
    require_approval = bool(settings.get("require_approval", True))

    for photo in photos:
        pid = photo["id"]
        try:
            raw = db.download("photos", photo["storage_path"])
        except Exception as exc:
            database.update_photo(db, pid, status="failed", error_message=f"Không tải được ảnh: {exc}")
            database.log_event(db, "error", "Không tải được ảnh từ Storage", photo_id=pid)
            stats["failed"] += 1
            continue

        try:
            result = generate_caption(
                api_key=cfg.gemini_api_key,
                model=cfg.gemini_model,
                fallback_models=cfg.gemini_fallbacks,
                image_bytes=raw,
                tone=settings.get("tone", ""),
                shop_info=settings.get("shop_info", ""),
                sample_posts=settings.get("sample_posts") or [],
                recent_captions=history,
                default_hashtags=settings.get("default_hashtags") or [],
            )
        except RateLimited as exc:
            log.warning("Gemini 429 — để ảnh %s lại cho lần chạy sau (%s)", pid, exc)
            notes.append("⏳ Gemini bị giới hạn tốc độ, một số ảnh sẽ xử lý ở lần chạy sau.")
            break
        except CaptionError as exc:
            message = f"AI không tạo được caption: {exc}"
            database.update_photo(
                db,
                pid,
                status="captioned",
                caption="",
                error_message=message,
            )
            database.log_event(db, "error", message, photo_id=pid)
            notes.append(f"⚠️ 1 ảnh cần viết caption tay ({database_short(pid)})")
            stats["failed"] += 1
            continue

        manual, reason = needs_manual_review(result)
        status = "captioned" if (require_approval or manual) else "approved"
        database.update_photo(
            db,
            pid,
            status=status,
            caption=result["caption"],
            hashtags=result["hashtags"],
            ai_meta={
                "product_type": result["product_type"],
                "confidence": result["confidence"],
                "warnings": result["warnings"],
                "manual_review_reason": reason,
                "model": cfg.gemini_model,
            },
            error_message=None,
        )
        history.insert(0, result["caption"])
        history = history[:5]
        stats["captioned"] += 1
        database.log_event(
            db,
            "info",
            f"Đã tạo caption (tin cậy {result['confidence']:.2f})",
            photo_id=pid,
            detail={"warnings": result["warnings"], "status": status},
        )
        log.info("Caption xong cho %s -> %s", database_short(pid), status)


def database_short(pid: str) -> str:
    return pid[:8]


# ---------------------------------------------------------------------------
# Bước 3: xếp lịch ảnh approved
# ---------------------------------------------------------------------------

def schedule_approved(
    db: database.Supabase,
    cfg: Config,
    settings: dict[str, Any],
    stats: dict[str, int],
    *,
    dry_run: bool,
) -> None:
    approved = database.list_photos(
        db, ["approved"], order="created_at.asc", limit=max(1, cfg.max_photos_per_run * 3)
    )
    if not approved:
        log.info("Không có ảnh approved cần xếp lịch.")
        return

    now = datetime.now(timezone.utc)
    horizon = int(settings.get("horizon_days", 7))

    # Các bài đã chiếm chỗ trong khoảng thời gian tới
    taken: list[datetime] = []
    busy = db.select(
        "photos",
        {
            "select": "scheduled_at",
            "status": "in.(scheduled,posted)",
            "scheduled_at": f"gte.{now.isoformat()}",
        },
    )
    for row in busy:
        if row.get("scheduled_at"):
            taken.append(datetime.fromisoformat(row["scheduled_at"].replace("Z", "+00:00")))

    slots = build_slots(
        now=now,
        posts_per_day=int(settings.get("posts_per_day", 3)),
        time_windows=settings.get("time_windows") or [],
        jitter_minutes=int(settings.get("jitter_minutes", 10)),
        timezone_name=settings.get("timezone", "Asia/Ho_Chi_Minh"),
        horizon_days=horizon,
        taken=taken,
        count=len(approved),
    )

    if not slots:
        log.warning("Không còn khung giờ trống trong %d ngày tới.", horizon)
        notify.send(
            cfg.telegram_token,
            cfg.telegram_chat_id,
            "⚠️ Không còn khung giờ trống để xếp lịch. Hãy tăng số bài/ngày hoặc giảm ảnh trong kho.",
            silent=True,
        )
        return

    fb = FacebookClient(cfg.fb_page_id, cfg.fb_page_token, cfg.fb_graph_version)

    for photo, slot in zip(approved, slots):
        pid = photo["id"]
        message = build_message(
            photo.get("caption") or "", photo.get("hashtags") or []
        )
        if not message.strip():
            database.update_photo(db, pid, status="captioned", error_message="Caption rỗng")
            continue

        if dry_run:
            print(
                f"[DRY-RUN] Sẽ lên lịch {database_short(pid)} lúc "
                f"{slot.astimezone().isoformat()} | {message.splitlines()[0][:60]}"
            )
            stats["scheduled"] += 1
            continue

        # Chống trùng: claim nguyên tử trước khi gọi Facebook
        if not database.claim_for_scheduling(db, pid):
            log.info("Ảnh %s đã được xử lý bởi tiến trình khác, bỏ qua.", database_short(pid))
            continue

        try:
            raw = db.download("photos", photo["storage_path"])
            fb_post_id = fb.schedule_photo(
                image_bytes=raw,
                message=message,
                scheduled_at=int(slot.timestamp()),
            )
        except FacebookError as exc:
            message_err = f"Facebook: {exc} (code={exc.code}, subcode={exc.subcode})"
            database.update_photo(
                db, pid, status="failed", error_message=message_err[:900]
            )
            database.log_event(db, "error", message_err, photo_id=pid)
            stats["failed"] += 1
            if exc.is_token_error:
                notify.send(
                    cfg.telegram_token,
                    cfg.telegram_chat_id,
                    "🚨 <b>Token Facebook hết hạn hoặc bị thu hồi.</b>\n"
                    "Đã dừng xếp lịch. Hãy lấy Page Access Token mới và cập nhật "
                    "secret <code>FB_PAGE_ACCESS_TOKEN</code> trên GitHub.",
                )
                break
            continue
        except Exception as exc:  # lỗi Storage, mạng... không làm hỏng cả lần chạy
            database.update_photo(db, pid, status="failed", error_message=str(exc)[:900])
            database.log_event(db, "error", f"Lỗi khi lên lịch: {exc}", photo_id=pid)
            stats["failed"] += 1
            continue

        database.update_photo(
            db,
            pid,
            status="scheduled",
            scheduled_at=slot.isoformat(),
            fb_post_id=fb_post_id,
            error_message=None,
        )
        database.log_event(
            db,
            "info",
            f"Đã lên lịch lúc {slot.isoformat()}",
            photo_id=pid,
            detail={"fb_post_id": fb_post_id},
        )
        stats["scheduled"] += 1
        log.info("Lên lịch %s -> %s", database_short(pid), slot.isoformat())


# ---------------------------------------------------------------------------
# Bước 4 + 5: đồng bộ trạng thái & dọn ảnh
# ---------------------------------------------------------------------------

def sync_posted(db: database.Supabase, cfg: Config, stats: dict[str, int], *, dry_run: bool) -> None:
    now = datetime.now(timezone.utc)
    rows = db.select(
        "photos",
        {
            "select": "id,scheduled_at,fb_post_id",
            "status": "eq.scheduled",
            "scheduled_at": f"lt.{now.isoformat()}",
            "limit": "100",
        },
    )
    if not rows:
        return
    if dry_run:
        print(f"[DRY-RUN] Sẽ đánh dấu {len(rows)} bài là đã đăng.")
        return

    fb = None
    for row in rows:
        pid = row["id"]
        # Kiểm tra bài có thực sự tồn tại trước khi đánh dấu (tùy chọn, an toàn)
        if row.get("fb_post_id") and cfg.fb_page_token:
            fb = fb or FacebookClient(cfg.fb_page_id, cfg.fb_page_token, cfg.fb_graph_version)
            try:
                if not fb.post_exists(row["fb_post_id"]):
                    database.update_photo(
                        db,
                        pid,
                        status="failed",
                        error_message="Bài không còn tồn tại trên Facebook (có thể đã bị xóa).",
                    )
                    database.log_event(db, "warn", "Bài đã bị xóa trên Facebook", photo_id=pid)
                    stats["failed"] += 1
                    continue
            except FacebookError as exc:
                log.warning("Không kiểm tra được bài %s: %s", row.get("fb_post_id"), exc)

        database.update_photo(
            db, pid, status="posted", posted_at=row.get("scheduled_at") or now.isoformat()
        )
        database.log_event(db, "info", "Đã đăng thành công (đồng bộ)", photo_id=pid)
        stats["posted"] += 1


def cleanup_storage(
    db: database.Supabase, settings: dict[str, Any], *, dry_run: bool, stats: dict[str, int]
) -> None:
    days = int(settings.get("retention_days", 7))
    if days <= 0:
        return
    cutoff_dt = datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)
    rows = db.select(
        "photos",
        {
            "select": "id,storage_path,posted_at",
            "status": "eq.posted",
            "storage_path": "not.is.null",
            "posted_at": f"lt.{cutoff_dt.isoformat()}",
            "limit": "50",
        },
    )
    removed = 0
    for row in rows:
        if dry_run:
            print(f"[DRY-RUN] Sẽ xóa file {row['storage_path']}")
        else:
            try:
                db.remove("photos", row["storage_path"])
            except Exception as exc:
                log.warning("Không xóa được file %s: %s", row["storage_path"], exc)
                continue
        removed += 1
    stats["cleaned"] = removed


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Worker đăng bài Facebook Page")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ in kế hoạch, không gọi Facebook")
    parser.add_argument("--limit", type=int, default=0, help="Giới hạn số ảnh xử lý lần này")
    args = parser.parse_args(argv)

    load_dotenv()
    cfg = Config.from_env(dry_run=args.dry_run)
    if args.limit:
        cfg.max_photos_per_run = args.limit

    cfg.require("supabase_url", "supabase_service_key")
    if not cfg.dry_run:
        cfg.require("gemini_api_key")

    db = database.Supabase(cfg.supabase_url, cfg.supabase_service_key)
    stats = {"captioned": 0, "scheduled": 0, "posted": 0, "failed": 0, "cleaned": 0}
    notes: list[str] = []
    run_id = None

    try:
        settings = database.get_settings(db)
        if not settings:
            log.warning("Chưa có settings — hãy đăng nhập PWA một lần để khởi tạo.")
            return 0

        if settings.get("paused"):
            log.info("Đang tạm dừng (paused=true) — thoát.")
            return 0

        # Chưa cấu hình Facebook: chưa có gì để đăng thì bỏ qua (không báo lỗi),
        # chỉ báo lỗi khi trong kho thật sự có ảnh đang chờ.
        if not cfg.dry_run and not (cfg.fb_page_id and cfg.fb_page_token):
            waiting = db.select(
                "photos",
                {"select": "id", "status": "in.(pending,captioned,approved,scheduled)"},
            )
            if not waiting:
                log.warning(
                    "Chưa cấu hình Facebook (FB_PAGE_ID / FB_PAGE_ACCESS_TOKEN) "
                    "và kho chưa có ảnh — bỏ qua lần chạy này."
                )
                return 0
            message = (
                f"⚠️ <b>Chưa cấu hình Facebook</b>\n"
                f"Đang có {len(waiting)} ảnh trong kho nhưng thiếu secret "
                f"<code>FB_PAGE_ID</code> / <code>FB_PAGE_ACCESS_TOKEN</code>.\n"
                "Bài sẽ không được lên lịch cho tới khi thêm đủ 2 secret này."
            )
            log.error(message.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""))
            notify.send(cfg.telegram_token, cfg.telegram_chat_id, message)
            return 1

        if not cfg.dry_run:
            run_id = database.acquire_run_lock(db)
            if run_id is None:
                log.info("Một lần chạy khác đang hoạt động — thoát.")
                return 0

        if cfg.dry_run:
            print("=== DRY RUN — không gọi Facebook, không ghi DB ===")
            print(f"Model AI: {cfg.gemini_model} | Graph API: {cfg.fb_graph_version}")
            print(
                f"Cấu hình: {settings.get('posts_per_day')} bài/ngày | "
                f"khung giờ {settings.get('time_windows')} | "
                f"duyệt tay: {settings.get('require_approval')}"
            )

        if not cfg.dry_run:
            process_pending(db, cfg, settings, stats, notes)

        schedule_approved(db, cfg, settings, stats, dry_run=cfg.dry_run)

        if not cfg.dry_run:
            sync_posted(db, cfg, stats, dry_run=False)
            cleanup_storage(db, settings, dry_run=False, stats=stats)
            database.log_event(
                db, "info", "Worker kết thúc", detail={**stats, "notes": notes}
            )

        # Thống kê còn lại
        remaining_rows = db.select(
            "photos",
            {"select": "id", "status": "in.(pending,captioned,approved)"},
        )
        scheduled_rows = db.select("photos", {"select": "id", "status": "eq.scheduled"})
        coverage = coverage_days(
            approved_count=len(remaining_rows),
            posts_per_day=int(settings.get("posts_per_day", 3)),
            scheduled_count=len(scheduled_rows),
            timezone_name=settings.get("timezone", "Asia/Ho_Chi_Minh"),
        )

        msg = notify.summary_message(
            captioned=stats["captioned"],
            scheduled=stats["scheduled"],
            posted=stats["posted"],
            failed=stats["failed"],
            remaining=len(remaining_rows),
            posts_per_day=int(settings.get("posts_per_day", 3)),
            coverage=coverage,
            dry_run=cfg.dry_run,
            notes=notes,
        )
        print(msg.replace("<b>", "").replace("</b>", ""))
        if not cfg.dry_run:
            notify.send(cfg.telegram_token, cfg.telegram_chat_id, msg)

        if run_id is not None:
            database.finish_run_lock(db, run_id, {**stats, "coverage": coverage})
        return 0

    except Exception as exc:  # lỗi toàn cục: báo Telegram rồi thoát mã lỗi
        log.exception("Worker lỗi")
        if not cfg.dry_run:
            notify.send(
                cfg.telegram_token,
                cfg.telegram_chat_id,
                f"🚨 <b>Worker lỗi</b>\n<code>{notify.esc(str(exc))[:800]}</code>",
            )
            database.finish_run_lock(db, run_id, {"error": str(exc)[:500], **stats})
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

