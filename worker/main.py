"""Äiá»ƒm vÃ o cá»§a worker: cháº¡y bá»Ÿi GitHub Actions (cron hoáº·c workflow_dispatch).

Luá»“ng (má»¥c 7.2 cá»§a Ä‘áº·c táº£):
1. Äá»c settings; náº¿u paused -> thoÃ¡t.
2. Xá»­ lÃ½ áº£nh `pending`: Gemini viáº¿t caption -> `captioned` (náº¿u duyá»‡t tay)
   hoáº·c `approved`.
3. Xáº¿p lá»‹ch áº£nh `approved`: tÃ­nh khung giá» trá»‘ng -> gá»i Facebook
   (`published=false`, `scheduled_publish_time`) -> `scheduled`.
4. Äá»“ng bá»™: áº£nh `scheduled` Ä‘Ã£ qua giá» -> `posted`.
5. Dá»n áº£nh cÅ© trong Storage theo `retention_days`.
6. Gá»­i Telegram tá»•ng káº¿t + cáº£nh bÃ¡o khi sáº¯p háº¿t áº£nh.

Cháº¡y thá»­:  python -m worker.main --dry-run
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
# BÆ°á»›c 2: viáº¿t caption cho áº£nh pending
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
        log.info("KhÃ´ng cÃ³ áº£nh pending.")
        return

    log.info("Xá»­ lÃ½ %d áº£nh pending...", len(photos))
    history = recent_captions(db)
    require_approval = bool(settings.get("require_approval", True))

    for photo in photos:
        pid = photo["id"]
        try:
            raw = db.download("photos", photo["storage_path"])
        except Exception as exc:
            database.update_photo(db, pid, status="failed", error_message=f"KhÃ´ng táº£i Ä‘Æ°á»£c áº£nh: {exc}")
            database.log_event(db, "error", "KhÃ´ng táº£i Ä‘Æ°á»£c áº£nh tá»« Storage", photo_id=pid)
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
            log.warning("Gemini 429 â€” Ä‘á»ƒ áº£nh %s láº¡i cho láº§n cháº¡y sau (%s)", pid, exc)
            notes.append("â³ Gemini bá»‹ giá»›i háº¡n tá»‘c Ä‘á»™, má»™t sá»‘ áº£nh sáº½ xá»­ lÃ½ á»Ÿ láº§n cháº¡y sau.")
            break
        except CaptionError as exc:
            message = f"AI khÃ´ng táº¡o Ä‘Æ°á»£c caption: {exc}"
            database.update_photo(
                db,
                pid,
                status="captioned",
                caption="",
                error_message=message,
            )
            database.log_event(db, "error", message, photo_id=pid)
            notes.append(f"âš ï¸ 1 áº£nh cáº§n viáº¿t caption tay ({database_short(pid)})")
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
            f"ÄÃ£ táº¡o caption (tin cáº­y {result['confidence']:.2f})",
            photo_id=pid,
            detail={"warnings": result["warnings"], "status": status},
        )
        log.info("Caption xong cho %s -> %s", database_short(pid), status)


def database_short(pid: str) -> str:
    return pid[:8]


# ---------------------------------------------------------------------------
# BÆ°á»›c 3: xáº¿p lá»‹ch áº£nh approved
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
        log.info("KhÃ´ng cÃ³ áº£nh approved cáº§n xáº¿p lá»‹ch.")
        return

    now = datetime.now(timezone.utc)
    horizon = int(settings.get("horizon_days", 7))

    # CÃ¡c bÃ i Ä‘Ã£ chiáº¿m chá»— trong khoáº£ng thá»i gian tá»›i
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
        log.warning("KhÃ´ng cÃ²n khung giá» trá»‘ng trong %d ngÃ y tá»›i.", horizon)
        notify.send(
            cfg.telegram_token,
            cfg.telegram_chat_id,
            "âš ï¸ KhÃ´ng cÃ²n khung giá» trá»‘ng Ä‘á»ƒ xáº¿p lá»‹ch. HÃ£y tÄƒng sá»‘ bÃ i/ngÃ y hoáº·c giáº£m áº£nh trong kho.",
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
            database.update_photo(db, pid, status="captioned", error_message="Caption rá»—ng")
            continue

        if dry_run:
            print(
                f"[DRY-RUN] Sáº½ lÃªn lá»‹ch {database_short(pid)} lÃºc "
                f"{slot.astimezone().isoformat()} | {message.splitlines()[0][:60]}"
            )
            stats["scheduled"] += 1
            continue

        # Chá»‘ng trÃ¹ng: claim nguyÃªn tá»­ trÆ°á»›c khi gá»i Facebook
        if not database.claim_for_scheduling(db, pid):
            log.info("áº¢nh %s Ä‘Ã£ Ä‘Æ°á»£c xá»­ lÃ½ bá»Ÿi tiáº¿n trÃ¬nh khÃ¡c, bá» qua.", database_short(pid))
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
                    "ðŸš¨ <b>Token Facebook háº¿t háº¡n hoáº·c bá»‹ thu há»“i.</b>\n"
                    "ÄÃ£ dá»«ng xáº¿p lá»‹ch. HÃ£y láº¥y Page Access Token má»›i vÃ  cáº­p nháº­t "
                    "secret <code>FB_PAGE_ACCESS_TOKEN</code> trÃªn GitHub.",
                )
                break
            continue
        except Exception as exc:  # lá»—i Storage, máº¡ng... khÃ´ng lÃ m há»ng cáº£ láº§n cháº¡y
            database.update_photo(db, pid, status="failed", error_message=str(exc)[:900])
            database.log_event(db, "error", f"Lá»—i khi lÃªn lá»‹ch: {exc}", photo_id=pid)
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
            f"ÄÃ£ lÃªn lá»‹ch lÃºc {slot.isoformat()}",
            photo_id=pid,
            detail={"fb_post_id": fb_post_id},
        )
        stats["scheduled"] += 1
        log.info("LÃªn lá»‹ch %s -> %s", database_short(pid), slot.isoformat())


# ---------------------------------------------------------------------------
# BÆ°á»›c 4 + 5: Ä‘á»“ng bá»™ tráº¡ng thÃ¡i & dá»n áº£nh
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
        print(f"[DRY-RUN] Sáº½ Ä‘Ã¡nh dáº¥u {len(rows)} bÃ i lÃ  Ä‘Ã£ Ä‘Äƒng.")
        return

    fb = None
    for row in rows:
        pid = row["id"]
        # Kiá»ƒm tra bÃ i cÃ³ thá»±c sá»± tá»“n táº¡i trÆ°á»›c khi Ä‘Ã¡nh dáº¥u (tÃ¹y chá»n, an toÃ n)
        if row.get("fb_post_id") and cfg.fb_page_token:
            fb = fb or FacebookClient(cfg.fb_page_id, cfg.fb_page_token, cfg.fb_graph_version)
            try:
                if not fb.post_exists(row["fb_post_id"]):
                    database.update_photo(
                        db,
                        pid,
                        status="failed",
                        error_message="BÃ i khÃ´ng cÃ²n tá»“n táº¡i trÃªn Facebook (cÃ³ thá»ƒ Ä‘Ã£ bá»‹ xÃ³a).",
                    )
                    database.log_event(db, "warn", "BÃ i Ä‘Ã£ bá»‹ xÃ³a trÃªn Facebook", photo_id=pid)
                    stats["failed"] += 1
                    continue
            except FacebookError as exc:
                log.warning("KhÃ´ng kiá»ƒm tra Ä‘Æ°á»£c bÃ i %s: %s", row.get("fb_post_id"), exc)

        database.update_photo(
            db, pid, status="posted", posted_at=row.get("scheduled_at") or now.isoformat()
        )
        database.log_event(db, "info", "ÄÃ£ Ä‘Äƒng thÃ nh cÃ´ng (Ä‘á»“ng bá»™)", photo_id=pid)
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
            print(f"[DRY-RUN] Sáº½ xÃ³a file {row['storage_path']}")
        else:
            try:
                db.remove("photos", row["storage_path"])
            except Exception as exc:
                log.warning("KhÃ´ng xÃ³a Ä‘Æ°á»£c file %s: %s", row["storage_path"], exc)
                continue
        removed += 1
    stats["cleaned"] = removed


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Worker Ä‘Äƒng bÃ i Facebook Page")
    parser.add_argument("--dry-run", action="store_true", help="Chá»‰ in káº¿ hoáº¡ch, khÃ´ng gá»i Facebook")
    parser.add_argument("--limit", type=int, default=0, help="Giá»›i háº¡n sá»‘ áº£nh xá»­ lÃ½ láº§n nÃ y")
    args = parser.parse_args(argv)

    load_dotenv()
    cfg = Config.from_env(dry_run=args.dry_run)
    if args.limit:
        cfg.max_photos_per_run = args.limit

    cfg.require("supabase_url", "supabase_service_key")
    if not cfg.dry_run:
        cfg.require("gemini_api_key", "fb_page_id", "fb_page_token")

    db = database.Supabase(cfg.supabase_url, cfg.supabase_service_key)
    stats = {"captioned": 0, "scheduled": 0, "posted": 0, "failed": 0, "cleaned": 0}
    notes: list[str] = []
    run_id = None

    try:
        settings = database.get_settings(db)
        if not settings:
            log.warning("ChÆ°a cÃ³ settings â€” hÃ£y Ä‘Äƒng nháº­p PWA má»™t láº§n Ä‘á»ƒ khá»Ÿi táº¡o.")
            return 0

        if settings.get("paused"):
            log.info("Äang táº¡m dá»«ng (paused=true) â€” thoÃ¡t.")
            return 0

        if not cfg.dry_run:
            run_id = database.acquire_run_lock(db)
            if run_id is None:
                log.info("Má»™t láº§n cháº¡y khÃ¡c Ä‘ang hoáº¡t Ä‘á»™ng â€” thoÃ¡t.")
                return 0

        if cfg.dry_run:
            print("=== DRY RUN â€” khÃ´ng gá»i Facebook, khÃ´ng ghi DB ===")
            print(f"Model AI: {cfg.gemini_model} | Graph API: {cfg.fb_graph_version}")
            print(
                f"Cáº¥u hÃ¬nh: {settings.get('posts_per_day')} bÃ i/ngÃ y | "
                f"khung giá» {settings.get('time_windows')} | "
                f"duyá»‡t tay: {settings.get('require_approval')}"
            )

        if not cfg.dry_run:
            process_pending(db, cfg, settings, stats, notes)

        schedule_approved(db, cfg, settings, stats, dry_run=cfg.dry_run)

        if not cfg.dry_run:
            sync_posted(db, cfg, stats, dry_run=False)
            cleanup_storage(db, settings, dry_run=False, stats=stats)
            database.log_event(
                db, "info", "Worker káº¿t thÃºc", detail={**stats, "notes": notes}
            )

        # Thá»‘ng kÃª cÃ²n láº¡i
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

    except Exception as exc:  # lá»—i toÃ n cá»¥c: bÃ¡o Telegram rá»“i thoÃ¡t mÃ£ lá»—i
        log.exception("Worker lá»—i")
        if not cfg.dry_run:
            notify.send(
                cfg.telegram_token,
                cfg.telegram_chat_id,
                f"ðŸš¨ <b>Worker lá»—i</b>\n<code>{notify.esc(str(exc))[:800]}</code>",
            )
            database.finish_run_lock(db, run_id, {"error": str(exc)[:500], **stats})
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

