"""In-process daily-briefing scheduler.

Fits this app's deployment: Cloud Run runs exactly one always-on instance
(`--min-instances=1 --max-instances=1 --no-cpu-throttling`) because Socket Mode
needs a persistent connection. That single instance makes an in-process
scheduler safe — no double-firing across replicas, no scale-to-zero.

Every minute we scan opted-in users and DM the briefing to any whose *local*
time now matches their chosen `daily_briefing_time`. A per-user "already sent
today" guard makes a missed/duplicated tick harmless.
"""

from __future__ import annotations

from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from app.db.engine import session_scope
from app.db.users import list_daily_briefing_subscribers
from app.slack_app.commands.goodmorning import post_briefing
from app.utils.timezone import now_in

# Guards against sending the same user more than once per local day, even if the
# minute-tick fires twice or overlaps. Keyed by user id -> local date sent.
_last_sent: dict[int, date] = {}


async def _run_due_briefings(client) -> None:
    """Scan subscribers and DM anyone whose local time == their chosen time."""
    async with session_scope() as session:
        subscribers = await list_daily_briefing_subscribers(session)
        # Snapshot the fields we need so we don't touch ORM objects post-session.
        targets = [
            {
                "user_id": u.id,
                "slack_team_id": u.slack_team_id,
                "slack_user_id": u.slack_user_id,
                "real_name": u.real_name,
                "tz": u.tz or "UTC",
                "workday_start": u.workday_start,
                "workday_end": u.workday_end,
                "email": u.email,
                "time": (u.daily_briefing_time or "08:00"),
            }
            for u in subscribers
        ]

    for t in targets:
        local_now = now_in(t["tz"])
        if local_now.strftime("%H:%M") != t["time"]:
            continue
        if _last_sent.get(t["user_id"]) == local_now.date():
            continue
        _last_sent[t["user_id"]] = local_now.date()
        try:
            await post_briefing(
                client,
                user_id=t["user_id"],
                slack_user_id=t["slack_user_id"],
                slack_team_id=t["slack_team_id"],
                real_name=t["real_name"],
                tz_name=t["tz"],
                workday_start=t["workday_start"],
                workday_end=t["workday_end"],
                email=t["email"],
                briefing_enabled=True,
                briefing_time=t["time"],
                source="scheduled",
            )
            logger.info(f"sent scheduled briefing to user {t['user_id']} at {t['time']} {t['tz']}")
        except Exception:
            logger.exception(f"scheduled briefing failed for user {t['user_id']}")


def start_briefing_scheduler(client) -> AsyncIOScheduler:
    """Start the minute-granularity scheduler on the running event loop."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_due_briefings,
        trigger=IntervalTrigger(seconds=60),
        args=[client],
        id="daily-briefings",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )
    scheduler.start()
    logger.info("Daily-briefing scheduler started (1-minute scan)")
    return scheduler
