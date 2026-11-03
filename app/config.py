import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "hypixel_monitor.db"

# 版本号统一保存在仓库根目录的 VERSION 文件中，静态 Pages 页面与本地
# FastAPI 页面都从这里读取，避免多处维护。
VERSION_FILE = BASE_DIR / "VERSION"


def get_version() -> str:
    """读取当前版本号。"""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"

ENDPOINTS = {
    "collections": {
        "url": "https://api.hypixel.net/v2/resources/skyblock/collections",
        "interval_minutes": 120,
    },
    "skills": {
        "url": "https://api.hypixel.net/v2/resources/skyblock/skills",
        "interval_minutes": 120,
    },
    "items": {
        "url": "https://api.hypixel.net/v2/resources/skyblock/items",
        "interval_minutes": 360,
    },
    "bazaar": {
        "url": "https://api.hypixel.net/v2/skyblock/bazaar",
        "interval_minutes": 5,
    },
}

API_KEY = os.getenv("HYPIXEL_API_KEY", "")
REQUEST_TIMEOUT_SECONDS = 20
