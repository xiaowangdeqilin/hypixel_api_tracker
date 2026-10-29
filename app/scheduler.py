from apscheduler.schedulers.background import BackgroundScheduler

from app.config import ENDPOINTS
from app.fetcher import process_endpoint


def start_scheduler():
    scheduler = BackgroundScheduler()
    for name, cfg in ENDPOINTS.items():
        scheduler.add_job(
            process_endpoint,
            "interval",
            minutes=cfg["interval_minutes"],
            args=[name, cfg],
            id=f"fetch_{name}",
            replace_existing=True,
        )

    scheduler.start()
    return scheduler
