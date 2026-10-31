"""
app/scheduler.py

Background scheduler for MedMate Layer 4:
- Recurring escalation tick every SCHEDULER_TICK_SECONDS.
- Dose generation job running from yesterday for 4 days (every 6 hours).
- Non-blocking BackgroundScheduler using fresh database sessions per run.
- Max instances 1, coalesce True to prevent overlapping runs.
- Privacy-safe logging (exception class names only, no raw text).
"""
import logging
from datetime import timedelta
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from app.db import SessionLocal
from app.config import settings
from app.services.clock import utc_now
from app.services.escalation import process_tick
from app.services.dose_engine import generate_dose_events
from app.notifiers import get_notifier

logger = logging.getLogger(__name__)

scheduler: Optional[BackgroundScheduler] = None


def run_tick_job() -> None:
    """Execute escalation process_tick in worker thread with fresh session."""
    db = SessionLocal()
    try:
        notifier = get_notifier()
        process_tick(db=db, clock=utc_now, notifier=notifier)
    except Exception as exc:
        logger.error("Job %s failed with exception: %s", "tick_job", exc.__class__.__name__)
    finally:
        db.close()


def run_generation_job() -> None:
    """Execute dose generation in worker thread with fresh session."""
    db = SessionLocal()
    try:
        now = utc_now()
        yesterday = (now - timedelta(days=1)).date()
        generate_dose_events(
            db=db,
            start_date=yesterday,
            days=4,
            clock=utc_now,
            skip_past=True,
        )
    except Exception as exc:
        logger.error("Job %s failed with exception: %s", "generation_job", exc.__class__.__name__)
    finally:
        db.close()


def start_scheduler() -> Optional[BackgroundScheduler]:
    """Start the background scheduler if SCHEDULER_ENABLED is True."""
    global scheduler

    if not settings.SCHEDULER_ENABLED:
        logger.info("Scheduler is disabled (SCHEDULER_ENABLED=false).")
        return None

    notifier = get_notifier()
    channel = getattr(notifier, "channel", "unknown")

    logger.info("Starting MedMate scheduler (SCHEDULER_ENABLED=true)")
    logger.info("Scheduler tick interval: %s seconds", settings.SCHEDULER_TICK_SECONDS)
    logger.info("Active notifier channel: %s", channel)

    if channel == "mock":
        logger.warning(
            "*** WARNING: Active notifier channel is 'mock' - no real SMS or WhatsApp messages will be delivered! ***"
        )

    if scheduler is None or not scheduler.running:
        scheduler = BackgroundScheduler(timezone="UTC")

        # 1. Escalation tick job
        scheduler.add_job(
            run_tick_job,
            "interval",
            seconds=settings.SCHEDULER_TICK_SECONDS,
            id="escalation_tick",
            max_instances=1,
            coalesce=True,
        )

        # 2. Dose generation job (every 6 hours)
        scheduler.add_job(
            run_generation_job,
            "interval",
            hours=6,
            id="dose_generation",
            max_instances=1,
            coalesce=True,
        )

        # Run generation once on startup
        run_generation_job()

        scheduler.start()
        logger.info("MedMate scheduler started successfully.")

    return scheduler


def stop_scheduler() -> None:
    """Shut down the background scheduler if running."""
    global scheduler
    if scheduler is not None and scheduler.running:
        logger.info("Stopping MedMate scheduler...")
        scheduler.shutdown(wait=False)
        scheduler = None
        logger.info("MedMate scheduler stopped.")
