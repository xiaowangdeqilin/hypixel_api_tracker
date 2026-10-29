import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "hypixel_monitor.db"

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
