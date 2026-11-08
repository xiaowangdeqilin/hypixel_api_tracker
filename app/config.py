import json
import os
from pathlib import Path
from typing import Any, Dict

from app.bingo import goal_progress, requirement_text
from app.firesale import firesales_view
from app.mctext import render_mc_text
from app.timeutil import format_time, to_epoch_ms

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "hypixel_monitor.db"

# 仪表盘自身版本号统一保存在仓库根目录的 VERSION 文件中，静态 Pages 页面
# 与本地 FastAPI 页面都从这里读取，避免多处维护。
VERSION_FILE = BASE_DIR / "VERSION"
# 资源数据版本状态由 Pages 构建脚本写入此文件（含双接口版本与同步状态）。
RUNTIME_FILE = DATA_DIR / "runtime.json"
# Bingo 展示数据同一来源：构建脚本把当前期次与全部任务写在这里。
BINGO_FILE = DATA_DIR / "bingo.json"
# 限时特卖展示数据：构建脚本每次运行都会重新抓取并覆盖（开始前可能被官方调整）。
FIRESALES_FILE = DATA_DIR / "firesales.json"


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
    # runtime.json 里可能存 {"version": ..., "observed_at": ...} 结构，
    # 展示层只需要版本号字符串，这里统一压平。
    flat_versions: Dict[str, str] = {}
    for key, value in versions.items():
        if isinstance(value, dict):
            flat_versions[key] = str(value.get("version") or "-")
        else:
            flat_versions[key] = str(value)
    history = []
    for item in data.get("history", []) or []:
        entry = dict(item)
        entry["time"] = format_time(entry.get("time"))
        history.append(entry)
    return {
        "stable_version": stable,
        "versions": flat_versions,
        "sync_status": data.get("sync_status", "aligned"),
        "note": data.get("note", ""),
        "updated_at": format_time(data.get("updated_at", "-")),
        "history": history,
    }


def get_resource_version() -> str:
    """向后兼容：只返回稳定版本号。"""
    return get_version_info()["stable_version"]


def get_bingo_info() -> Dict[str, Any]:
    """读取当前 Bingo 展示数据，并把任务文案预渲染成带颜色的 HTML。

    数据由 ``scripts/build_dashboard.py`` 写入 ``data/bingo.json``；本地服务
    第一次启动时如果该文件还不存在，页面会提示先运行一次构建脚本。

    颜色码在这里统一渲染：Minecraft 的 ``§`` 代码是「状态」语义，必须顺序
    解析，因此模板只负责把结果原样输出（``|safe``）。
    """
    try:
        data = json.loads(BINGO_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict) or not data.get("id"):
        return {}

    goals = []
    for goal in data.get("goals") or []:
        if not isinstance(goal, dict):
            continue
        entry = dict(goal)
        lines = entry.get("lore_lines") or (
            [entry.get("lore")] if entry.get("lore") else []
        )
        entry["lore_html"] = [
            render_mc_text(str(line), default_color="#e2e8f0") for line in lines
        ]
        entry["progress_info"] = entry.get("progress_info") or goal_progress(entry)
        entry["requirement_text"] = entry.get("requirement_text") or requirement_text(entry)
        goals.append(entry)

    view = dict(data)
    view["goals"] = goals
    view["published_at"] = format_time(data.get("published_at"))
    view["first_seen"] = format_time(data.get("first_seen"))
    view["start"] = format_time(data.get("start"))
    view["end"] = format_time(data.get("end"))
    # 兼容早期写下的 bingo.json：没有 start_ms/end_ms 时从北京时间字符串反推。
    view["start_ms"] = to_epoch_ms(data.get("start_ms")) or to_epoch_ms(data.get("start"))
    view["end_ms"] = to_epoch_ms(data.get("end_ms")) or to_epoch_ms(data.get("end"))
    return view


def get_firesales_info() -> Dict[str, Any]:
    """读取限时特卖展示数据（``data/firesales.json``）。

    数据由 ``scripts/build_dashboard.py`` 每次运行重新抓取写入；
    文件不存在或为空时返回空 dict，页面显示引导提示。
    """
    try:
        data = json.loads(FIRESALES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict) or not data.get("sales"):
        return {}
    return firesales_view(
        {"sales": data.get("sales")},
        str(data.get("generated_at") or "-"),
    )


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
    "bingo": {
        "url": "https://api.hypixel.net/v2/resources/skyblock/bingo",
        "interval_minutes": 30,
    },
    # 特卖开始前官方可能随时调整时间/数量/价格，本地服务同样用最短间隔抓取。
    "firesales": {
        "url": "https://api.hypixel.net/v2/skyblock/firesales",
        "interval_minutes": 5,
    },
}

API_KEY = os.getenv("HYPIXEL_API_KEY", "")
REQUEST_TIMEOUT_SECONDS = 20
