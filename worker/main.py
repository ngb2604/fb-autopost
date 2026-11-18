"""Điểm vào của worker: chạy bởi GitHub Actions (cron hoặc workflow_dispatch).

Mô hình mới: 1 BÀI = 1 NHÓM ẢNH cùng phân loại (album) hoặc 1 ảnh.

Luồng:
1. Đọc settings; nếu paused -> thoát.
2. AI tự phân loại các ảnh chưa có phân loại (Gemini, tối đa `classify_per_run`).
3. Lấy ảnh đang dùng được (chưa dùng, hoặc đã quá `reuse_days`).
4. Lập kế hoạch bài: gom ảnh theo phân loại, xoay vòng loại + kiểu đăng.
5. Với mỗi bài: tạo post + gắn ảnh, gọi Gemini viết caption từ TẤT CẢ ảnh.
6. Xếp lịch bài `ready`: tính khung giờ trống -> đăng album/ảnh lên Facebook
   (`published=false` + `scheduled_publish_time`) -> `scheduled`.
7. Đồng bộ bài đã tới giờ -> `posted` (ảnh vẫn dùng lại được sau `reuse_days`).
8. Gửi Telegram tổng kết + cảnh báo khi sắp hết ảnh.

Chạy thử:  python -m worker.main --dry-run
Chỉ phân loại ảnh (không tạo bài): python -m worker.main --classify-only
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db as database
from . import notify
from .caption import CaptionError, RateLimited, generate_caption
from .classify import MIN_CONFIDENCE, classify_photo
from .config import Config
from .env import load_dotenv
from .facebook import FacebookClient, FacebookError
from .posts import plan_posts, plan_summary
from .scheduler import build_slots

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("worker")

# Không log URL request của httpx (chứa ?key=<GEMINI_API_KEY>)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def build_message(caption: str, hashtags: list[str] | None) -> str:
    tags = " ".join(hashtags or [])
    return f"{caption.strip()}\n\n{tags}".strip()


def short(ident: str) -> str:
    return str(ident)[:8]


def recent_captions(db: database.Supabase, limit: int = 5) -> list[str]:
    rows = db.select(
        "posts",
        {
            "select": "caption",
            "caption": "not.is.null",
            "order": "created_at.desc",
            "limit": str(limit),
        },
    )
    return [r["caption"] for r in rows if r.get("caption")]


def category_lookup(categories: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(c["id"]): c for c in categories}


# ---------------------------------------------------------------------------
# Bước 1b: AI tự phân loại ảnh chưa có phân loại
# ---------------------------------------------------------------------------

def auto_classify(
    db: database.Supabase,
    cfg: Config,
    settings: dict[str, Any],
    categories: list[dict[str, Any]],
    stats: dict[str, int],
    notes: list[str],
    *,
    dry_run: bool = False,
) -> None:
    """Gán phân loại cho ảnh chưa chọn loại, bằng Gemini.

    Mỗi ảnh chỉ được AI thử một lần (đánh dấu `classified_at`), trừ khi gọi
    Gemini lỗi/429 — khi đó để lần chạy sau thử lại. Giới hạn số ảnh mỗi lần
    chạy để tiết kiệm quota free tier.
    """
    if not bool(settings.get("auto_classify", True)):
        log.info("Tự phân loại ảnh đang tắt (auto_classify=false).")
        return

    active = [c for c in categories if c.get("active", True) and c.get("name")]
    if not active:
        log.info("Chưa có phân loại nào đang bật — bỏ qua tự phân loại.")
        return

    per_run = max(1, int(settings.get("classify_per_run", 10) or 10))

    if dry_run:
        pending = database.count_unclassified_photos(db)
        if pending:
            print(
                f"[DRY-RUN] Có {pending} ảnh chưa phân loại; "
                f"AI sẽ xử lý tối đa {per_run} ảnh mỗi lần chạy."
            )
            stats["classified"] = min(pending, per_run)
        return

    rows = database.list_unclassified_photos(db, limit=per_run)
    if not rows:
        log.info("Không có ảnh nào cần AI phân loại.")
        return

    log.info("AI phân loại %d ảnh (tối đa %d/lần chạy)…", len(rows), per_run)
    unmatched = 0

    for row in rows:
        try:
            raw = db.download("photos", row["storage_path"])
        except Exception as exc:
            log.warning("Không tải được ảnh %s để phân loại: %s", short(row["id"]), exc)
            continue

        try:
            result = classify_photo(
                api_key=cfg.gemini_api_key,
                model=cfg.gemini_model,
                image_bytes=raw,
                categories=active,
                fallback_models=cfg.gemini_fallbacks,
            )
        except RateLimited as exc:
            log.warning("Gemini 429 khi phân loại — để lần chạy sau (%s)", exc)
            notes.append("⏳ Gemini bị giới hạn tốc độ — tạm dừng phân loại ảnh lần này.")
            break
        except CaptionError as exc:
            log.warning("AI phân loại lỗi — để lần chạy sau: %s", exc)
            notes.append("⚠️ AI phân loại ảnh bị lỗi — sẽ thử lại lần chạy sau.")
            break

        match = result.get("category")
        confidence = float(result.get("confidence") or 0)
        now = datetime.now(timezone.utc).isoformat()

        if match and confidence >= MIN_CONFIDENCE:
            database.update_photo(
                db, row["id"], category_id=match["id"], classified_at=now
            )
            stats["classified"] += 1
            log.info(
                " Ảnh %s -> %s (tin cậy %.2f)", short(row["id"]), match["name"], confidence
            )
            database.log_event(
                db,
                "info",
                f"AI phân loại ảnh vào “{match['name']}” (tin cậy {confidence:.2f})",
                photo_id=row["id"],
            )
        else:
            # Đánh dấu đã thử để không tốn quota gọi lại ảnh này
            database.update_photo(db, row["id"], classified_at=now)
            unmatched += 1
            log.info(
                " Ảnh %s: AI chưa khớp loại (tên=%r, tin cậy %.2f)",
                short(row["id"]),
                result.get("name"),
                confidence,
            )

    if unmatched:
        notes.append(
            f"🤖 {unmatched} ảnh AI chưa khớp phân loại nào — vẫn đăng ở nhóm “Chưa phân loại”."
        )


# ---------------------------------------------------------------------------
# Bước 2-4: tạo bài từ ảnh trong kho
# ---------------------------------------------------------------------------

def create_posts(
    db: database.Supabase,
    cfg: Config,
    settings: dict[str, Any],
    categories: list[dict[str, Any]],
    stats: dict[str, int],
    notes: list[str],
    *,
    dry_run: bool = False,
) -> None:
    reuse_days = int(settings.get("reuse_days", 14))
    album_size = int(settings.get("album_size", 4))
    max_posts = int(settings.get("max_posts_per_run", 6))
    styles = settings.get("post_styles") or ["album", "single"]
    rotate = bool(settings.get("category_rotation", True))
    require_approval = bool(settings.get("require_approval", False))

    available = database.available_photos(db, reuse_days=reuse_days, limit=300)
    if not available:
        log.info("Không còn ảnh nào dùng được.")
        return

    plans = plan_posts(
        available,
        categories,
        album_size=album_size,
        styles=list(styles),
        max_posts=max_posts,
        rotate=rotate,
    )
    if not plans:
        log.info("Không lập được bài nào từ %d ảnh.", len(available))
        return

    log.info("Kế hoạch: %s", plan_summary(plans))

    if dry_run:
        print(f"[DRY-RUN] Có {len(available)} ảnh dùng được. Kế hoạch {len(plans)} bài:")
        for p in plans:
            print(
                f"  - {p['category_name'] or 'Chưa phân loại'} | {p['style']} | "
                f"{len(p['photos'])} ảnh: "
                + ", ".join(short(ph["id"]) for ph in p["photos"])
            )
        stats["created_posts"] = len(plans)
        stats["photos_used"] = sum(len(p["photos"]) for p in plans)
        return

    cats = category_lookup(categories)
    history = recent_captions(db)

    for plan in plans:
        cat = cats.get(str(plan.get("category_id") or "")) or {}
        photos = plan["photos"]
        photo_ids = [p["id"] for p in photos]

        post = database.create_post(
            db,
            category_id=plan.get("category_id"),
            style=plan["style"],
            photo_ids=photo_ids,
        )
        post_id = post["id"]

        # Tải ảnh về để gửi Gemini (album: gửi tất cả ảnh trong 1 lượt)
        images: list[bytes] = []
        bad_photo = None
        for photo in photos:
            try:
                images.append(db.download("photos", photo["storage_path"]))
            except Exception as exc:
                bad_photo = photo["id"]
                log.warning("Không tải được ảnh %s: %s", short(bad_photo), exc)
                break

        if bad_photo or not images:
            database.update_post(
                db, post_id, status="failed", error_message="Không tải được ảnh từ Storage"
            )
            database.release_photos(db, photo_ids)
            if bad_photo:
                database.update_photo(
                    db, bad_photo, status="failed", error_message="Không tải được ảnh"
                )
            stats["failed"] += 1
            notes.append(f"⚠️ 1 ảnh bị lỗi đọc file ({short(bad_photo or '')})")
            continue

        try:
            result = generate_caption(
                api_key=cfg.gemini_api_key,
                model=cfg.gemini_model,
                fallback_models=cfg.gemini_fallbacks,
                image_bytes=images,
                tone=settings.get("tone", ""),
                shop_info=settings.get("shop_info", ""),
                sample_posts=settings.get("sample_posts") or [],
                recent_captions=history,
                default_hashtags=settings.get("default_hashtags") or [],
                category_name=cat.get("name", ""),
                category_hint=cat.get("hint", ""),
                category_hashtags=cat.get("hashtags") or [],
                style=plan["style"],
            )
        except RateLimited as exc:
            log.warning("Gemini 429 — hoãn tạo bài (%s)", exc)
            database.delete_post(db, post_id)
            database.release_photos(db, photo_ids)
            notes.append("⏳ Gemini bị giới hạn tốc độ, các bài còn lại để lần chạy sau.")
            break
        except CaptionError as exc:
            message = f"AI không tạo được caption: {exc}"
            database.update_post(db, post_id, status="failed", error_message=message[:900])
            database.release_photos(db, photo_ids)
            # Ảnh đầu tiên bị lỗi -> đánh dấu để không lặp lại mỗi lần chạy
            database.update_photo(db, photo_ids[0], status="failed", error_message=message[:900])
            database.log_event(db, "error", message, detail={"post_id": post_id})
            notes.append(f"⚠️ 1 bài lỗi caption ({short(post_id)})")
            stats["failed"] += 1
            continue

        status = "draft" if require_approval else "ready"
        database.update_post(
            db,
            post_id,
            status=status,
            caption=result["caption"],
            hashtags=result["hashtags"],
            ai_meta={
                "product_type": result["product_type"],
                "confidence": result["confidence"],
                "warnings": result["warnings"],
                "model": cfg.gemini_model,
                "photo_count": len(images),
            },
            error_message=None,
        )
        history.insert(0, result["caption"])
        history = history[:5]
        stats["created_posts"] += 1
        stats["photos_used"] += len(images)
        database.log_event(
            db,
            "info",
            f"Đã tạo bài {plan['style']} ({len(images)} ảnh) — tin cậy {result['confidence']:.2f}",
            detail={"post_id": post_id, "category": cat.get("name", "")},
        )
        log.info(
            " Bài %s: %s, %d ảnh -> %s",
            short(post_id),
            cat.get("name") or "Chưa phân loại",
            len(images),
            status,
        )


# ---------------------------------------------------------------------------
# Bước 5: xếp lịch các bài đã có caption
# ---------------------------------------------------------------------------

def _future_slots_taken(db: database.Supabase, now: datetime) -> list[datetime]:
    rows = db.select(
        "posts",
        {
            "select": "scheduled_at",
            "status": "in.(scheduled,posted)",
            "scheduled_at": f"gte.{now.isoformat()}",
        },
    )
    out = []
    for row in rows:
        if row.get("scheduled_at"):
            out.append(datetime.fromisoformat(row["scheduled_at"].replace("Z", "+00:00")))
    return out


def schedule_ready(
    db: database.Supabase,
    cfg: Config,
    settings: dict[str, Any],
    stats: dict[str, int],
    *,
    dry_run: bool,
) -> None:
    ready = database.list_posts(db, ["ready"], order="created_at.asc", limit=50)
    if not ready:
        log.info("Không có bài nào chờ xếp lịch.")
        return

    now = datetime.now(timezone.utc)
    horizon = int(settings.get("horizon_days", 7))
    slots = build_slots(
        now=now,
        posts_per_day=int(settings.get("posts_per_day", 3)),
        time_windows=settings.get("time_windows") or [],
        jitter_minutes=int(settings.get("jitter_minutes", 10)),
        timezone_name=settings.get("timezone", "Asia/Ho_Chi_Minh"),
        horizon_days=horizon,
        taken=_future_slots_taken(db, now),
        count=len(ready),
    )

    if not slots:
        log.warning("Không còn khung giờ trống trong %d ngày tới.", horizon)
        notify.send(
            cfg.telegram_token,
            cfg.telegram_chat_id,
            "⚠️ Không còn khung giờ trống để xếp lịch. Hãy tăng số bài/ngày.",
            silent=True,
        )
        return

    by_post = database.posts_with_photos(db, [p["id"] for p in ready])
    fb = None

    for post, slot in zip(ready, slots):
        pid = post["id"]
        photos = by_post.get(pid, [])
        photo_ids = [p["id"] for p in photos]
        message = build_message(post.get("caption") or "", post.get("hashtags") or [])

        if not message.strip() or not photos:
            database.update_post(
                db, pid, status="failed", error_message="Thiếu caption hoặc ảnh"
            )
            database.release_photos(db, photo_ids)
            stats["failed"] += 1
            continue

        if dry_run:
            print(
                f"[DRY-RUN] Bài {short(pid)} ({len(photos)} ảnh) lúc "
                f"{slot.astimezone().isoformat()} | {message.splitlines()[0][:60]}"
            )
            stats["scheduled"] += 1
            continue

        # Giữ chỗ nguyên tử trước khi gọi Facebook
        if not database.claim_post(db, pid):
            log.info("Bài %s đã được xử lý bởi tiến trình khác.", short(pid))
            continue

        fb = fb or FacebookClient(cfg.fb_page_id, cfg.fb_page_token, cfg.fb_graph_version)
        try:
            images = [db.download("photos", p["storage_path"]) for p in photos]
            fb_post_id = fb.schedule_post(
                images=images,
                message=message,
                scheduled_at=int(slot.timestamp()),
            )
        except FacebookError as exc:
            err = f"Facebook: {exc} (code={exc.code}, subcode={exc.subcode})"
            database.update_post(db, pid, status="failed", error_message=err[:900])
            database.release_photos(db, photo_ids)
            database.log_event(db, "error", err, detail={"post_id": pid})
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
        except Exception as exc:
            database.update_post(db, pid, status="failed", error_message=str(exc)[:900])
            database.release_photos(db, photo_ids)
            database.log_event(db, "error", f"Lỗi khi lên lịch: {exc}", detail={"post_id": pid})
            stats["failed"] += 1
            continue

        database.update_post(
            db,
            pid,
            status="scheduled",
            scheduled_at=slot.isoformat(),
            fb_post_id=fb_post_id,
            error_message=None,
        )
        database.mark_photos_used(db, photo_ids)
        database.log_event(
            db,
            "info",
            f"Đã lên lịch {len(photos)} ảnh lúc {slot.isoformat()}",
            detail={"post_id": pid, "fb_post_id": fb_post_id},
        )
        stats["scheduled"] += 1
        log.info("Lên lịch bài %s (%d ảnh) -> %s", short(pid), len(photos), slot.isoformat())


# ---------------------------------------------------------------------------
# Bước 6: đồng bộ trạng thái & dọn ảnh
# ---------------------------------------------------------------------------

def sync_posted(db: database.Supabase, cfg: Config, stats: dict[str, int], *, dry_run: bool) -> None:
    now = datetime.now(timezone.utc)
    rows = db.select(
        "posts",
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

    ids = [r["id"] for r in rows]
    by_post = database.posts_with_photos(db, ids)
    fb = None

    for row in rows:
        pid = row["id"]
        photo_ids = [p["id"] for p in by_post.get(pid, [])]
        if row.get("fb_post_id") and cfg.fb_page_token:
            fb = fb or FacebookClient(cfg.fb_page_id, cfg.fb_page_token, cfg.fb_graph_version)
            try:
                if not fb.post_exists(row["fb_post_id"]):
                    database.update_post(
                        db,
                        pid,
                        status="failed",
                        error_message="Bài không còn tồn tại trên Facebook (có thể đã bị xóa).",
                    )
                    database.release_photos(db, photo_ids)
                    database.log_event(db, "warn", "Bài đã bị xóa trên Facebook", detail={"post_id": pid})
                    stats["failed"] += 1
                    continue
            except FacebookError as exc:
                log.warning("Không kiểm tra được bài %s: %s", row.get("fb_post_id"), exc)

        database.update_post(
            db,
            pid,
            status="posted",
            posted_at=row.get("scheduled_at") or now.isoformat(),
        )
        database.mark_photos_posted(db, photo_ids)
        database.log_event(db, "info", "Đã đăng thành công (đồng bộ)", detail={"post_id": pid})
        stats["posted"] += 1


def recover_stuck(db: database.Supabase) -> None:
    """Trả lại các bài bị kẹt ở 'scheduling' do lần chạy trước bị ngắt."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    rows = db.select(
        "posts",
        {
            "select": "id",
            "status": "eq.scheduling",
            "updated_at": f"lt.{cutoff}",
            "limit": "20",
        },
    )
    for row in rows:
        by_post = database.posts_with_photos(db, [row["id"]])
        photo_ids = [p["id"] for p in by_post.get(row["id"], [])]
        database.update_post(
            db, row["id"], status="ready", error_message="Tự khôi phục sau lần chạy bị ngắt"
        )
        database.release_photos(db, photo_ids)
        log.warning("Khôi phục bài kẹt %s", short(row["id"]))


def cleanup_storage(
    db: database.Supabase, settings: dict[str, Any], *, dry_run: bool, stats: dict[str, int]
) -> None:
    """Dọn file của ảnh đã bị bỏ qua (ảnh đã đăng được giữ lại để dùng lại)."""
    days = int(settings.get("retention_days", 7))
    if days <= 0:
        return
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.select(
        "photos",
        {
            "select": "id,storage_path",
            "status": "eq.skipped",
            "storage_path": "not.is.null",
            "created_at": f"lt.{cutoff_dt.isoformat()}",
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
    parser.add_argument("--limit", type=int, default=0, help="Giới hạn số bài tạo lần này")
    parser.add_argument(
        "--classify-only",
        action="store_true",
        help="Chỉ phân loại ảnh bằng AI, không tạo/xếp lịch bài",
    )
    args = parser.parse_args(argv)

    load_dotenv()
    cfg = Config.from_env(dry_run=args.dry_run)
    cfg.require("supabase_url", "supabase_service_key")
    if not cfg.dry_run:
        cfg.require("gemini_api_key")

    db = database.Supabase(cfg.supabase_url, cfg.supabase_service_key)
    stats = {
        "created_posts": 0,
        "photos_used": 0,
        "classified": 0,
        "scheduled": 0,
        "posted": 0,
        "failed": 0,
        "cleaned": 0,
    }
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

        if args.limit:
            settings = {**settings, "max_posts_per_run": max(1, args.limit)}

        fb_ready = bool(cfg.fb_page_id and cfg.fb_page_token)
        if not cfg.dry_run and not fb_ready:
            log.warning(
                "Chưa cấu hình Facebook (FB_PAGE_ID / FB_PAGE_ACCESS_TOKEN) — "
                "vẫn tạo caption, nhưng bỏ qua bước lên lịch."
            )

        if not cfg.dry_run:
            run_id = database.acquire_run_lock(db)
            if run_id is None:
                log.info("Một lần chạy khác đang hoạt động — thoát.")
                return 0
            recover_stuck(db)

        categories = database.list_categories(db)

        if cfg.dry_run:
            print("=== DRY RUN — không gọi Facebook, không ghi DB ===")
            print(f"Model AI: {cfg.gemini_model} | Graph API: {cfg.fb_graph_version}")
            print(
                f"Cấu hình: {settings.get('posts_per_day')} bài/ngày | "
                f"{settings.get('album_size', 4)} ảnh/bài | "
                f"dùng lại sau {settings.get('reuse_days', 14)} ngày | "
                f"luân phiên: {settings.get('category_rotation', True)}"
            )

        # Ảnh chưa có phân loại -> nhờ AI gán loại trước khi gom bài
        auto_classify(db, cfg, settings, categories, stats, notes, dry_run=cfg.dry_run)

        if args.classify_only:
            log.info("--classify-only: đã phân loại %d ảnh.", stats["classified"])
            if not cfg.dry_run:
                database.log_event(
                    db,
                    "info",
                    f"Chỉ phân loại ảnh: {stats['classified']} ảnh",
                    detail=stats,
                )
                database.finish_run_lock(db, run_id, {**stats, "classify_only": True})
            print(f"Đã phân loại {stats['classified']} ảnh.")
            return 0

        if not cfg.dry_run:
            create_posts(db, cfg, settings, categories, stats, notes, dry_run=False)
        else:
            create_posts(db, cfg, settings, categories, stats, notes, dry_run=True)

        if cfg.dry_run or fb_ready:
            schedule_ready(db, cfg, settings, stats, dry_run=cfg.dry_run)

        if not cfg.dry_run:
            sync_posted(db, cfg, stats, dry_run=False)
            cleanup_storage(db, settings, dry_run=False, stats=stats)
            database.log_event(db, "info", "Worker kết thúc", detail={**stats, "notes": notes})

        # Thống kê ảnh còn dùng được
        reuse_days = int(settings.get("reuse_days", 14))
        available = database.available_photos(db, reuse_days=reuse_days, limit=1000)
        album_size = max(1, int(settings.get("album_size", 4)))
        posts_per_day = max(1, int(settings.get("posts_per_day", 3)))
        coverage = round(len(available) / album_size / posts_per_day, 1)

        if not cfg.dry_run and not fb_ready and available:
            notes.append(
                f"⚠️ {len(available)} ảnh đang chờ nhưng chưa có token Facebook "
                "→ chưa lên lịch được."
            )

        msg = notify.summary_message(
            created_posts=stats["created_posts"],
            photos_used=stats["photos_used"],
            classified=stats["classified"],
            scheduled=stats["scheduled"],
            posted=stats["posted"],
            failed=stats["failed"],
            remaining_photos=len(available),
            posts_per_day=posts_per_day,
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
