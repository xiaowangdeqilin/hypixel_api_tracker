import json
import os
from pathlib import Path
from typing import Any, Dict

from app.timeutil import format_time

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "hypixel_monitor.db"

# 仪表盘自身版本号统一保存在仓库根目录的 VERSION 文件中，静态 Pages 页面
# 与本地 FastAPI 页面都从这里读取，避免多处维护。
VERSION_FILE = BASE_DIR / "VERSION"
# 资源数据版本状态由 Pages 构建脚本写入此文件（含双接口版本与同步状态）。
RUNTIME_FILE = DATA_DIR / "runtime.json"


def get_version() -> str:
    """读取仪表盘自身版本号。"""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def get_version_info() -> Dict[str, Any]:
    """读取 Hypixel 资源数据版本状态。

    ``runtime.json`` 由 ``scripts/build_dashboard.py`` 维护，结构为::

        {
          "stable_version": "0.27.2.22",
          "versions": {"collections": "0.27.2.22", "skills": "0.27.2.22"},
          "sync_status": "aligned",
          "note": "...",
          "updated_at": "2026-11-03 14:30:00",
          "history": [...]
        }

    兼容旧版只有 ``resource_version`` 的文件。
    """
    empty = {
        "stable_version": "未获取",
        "versions": {},
        "sync_status": "partial",
        "note": "尚未生成版本数据，请先运行 python scripts/build_dashboard.py",
        "updated_at": "-",
        "history": [],
    }
    try:
        data = json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty

    # 兼容旧结构
    stable = data.get("stable_version") or data.get("resource_version") or "未获取"
    versions = data.get("versions") or {}
    if not versions and data.get("resource_version"):
        versions = {"collections": stable, "skills": stable}
    history = []
    for item in data.get("history", []) or []:
        entry = dict(item)
        entry["time"] = format_time(entry.get("time"))
        history.append(entry)
    return {
        "stable_version": stable,
        "versions": versions,
        "sync_status": data.get("sync_status", "aligned"),
        "note": data.get("note", ""),
        "updated_at": format_time(data.get("updated_at", "-")),
        "history": history,
    }


def get_resource_version() -> str:
    """向后兼容：只返回稳定版本号。"""
    return get_version_info()["stable_version"]


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
