import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "hypixel_monitor.db"

# 仪表盘自身版本号统一保存在仓库根目录的 VERSION 文件中，静态 Pages 页面
# 与本地 FastAPI 页面都从这里读取，避免多处维护。
VERSION_FILE = BASE_DIR / "VERSION"
# 页面顶部主版本来自 Hypixel 资源 API，由 Pages 构建脚本写入此文件。
RUNTIME_FILE = DATA_DIR / "runtime.json"


def get_version() -> str:
    """读取仪表盘自身版本号。"""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def get_resource_version() -> str:
    """读取 Hypixel 资源数据版本（取自 skills / items 的 version 字段）。"""
    try:
        data = json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
        return data.get("resource_version") or "未获取"
    except (json.JSONDecodeError, OSError):
        return "未获取"

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
    "packs": {
        "url": "https://api.hypixel.net/v2/resources/packs",
        "interval_minutes": 10,
    },
}

API_KEY = os.getenv("HYPIXEL_API_KEY", "")
REQUEST_TIMEOUT_SECONDS = 20
