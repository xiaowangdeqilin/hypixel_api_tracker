from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import ENDPOINTS
from app.fetcher import process_endpoint


def start_scheduler():
    """启动定时抓取。

    ``next_run_time`` 设为当前时间，让服务一启动就后台抓取一轮，避免本地
    仪表盘在第一个间隔周期内一直显示「等待首次抓取」。
    """
    scheduler = BackgroundScheduler()
    for name, cfg in ENDPOINTS.items():
        scheduler.add_job(
            process_endpoint,
            "interval",
            minutes=cfg["interval_minutes"],
            args=[name, cfg],
            id=f"fetch_{name}",
            replace_existing=True,
            next_run_time=datetime.now(),
        )

    scheduler.start()
    return scheduler
