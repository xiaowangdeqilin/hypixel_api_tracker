"""网页视图模型。

职责：把 ``site/data.json`` 的原始结构压成模板直接可用的小结构
（端点卡片、精简变更行、时区列表），避免模板里塞业务判断，
也避免把几 MB 的 ``new_full`` 原样吐到页面上。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

# 端点展示顺序与中文标签（作为 i18n 语言包的键）。
# 全部使用主语言包里已经存在的词条（键 = 简体中文原文），静态站与这里共用一份译文
ENDPOINT_LABELS = [
    ("collections", "收藏"),
    ("skills", "技能"),
    ("items", "物品"),
    ("bazaar", "集市"),
    ("packs", "资源包"),
    ("bingo", "Bingo 任务"),
    ("firesales", "限时特卖"),
]

# 摘要行复用主站「标签：数值」整句词条，避免为每种语言再造一套裸标签
SUMMARY_PATTERNS = {
    "collection_count": "收藏分类数：{count}",
    "total_items": "收藏物品总数：{count}",
    "skill_count": "技能数：{count}",
    "item_count": "物品数：{count}",
    "product_count": "集市商品数：{count}",
    "deploy_id": "部署 ID：{id}",
    "pack_count": "资源包数：{count}",
    "pack_format_count": "格式版本数：{count}",
    "selected_pack_format": "选取格式（最大）：{value}",
    "bingo_goal_count": "任务数：{count}",
    "bingo_name": "期次：{name}",
    "bingo_state": "状态：{status}",
    "firesale_total": "特卖期数：{count}",
    "firesale_upcoming": "即将开始：{count}",
    "firesale_active": "进行中：{count}",
}

CHANGE_TYPE_LABELS = {
    "added": "新增",
    "removed": "删除",
    "modified": "修改",
}

# 时区用 IANA 标识 + 偏移展示：不随界面语言变化，也不需要为每种语言准备城市名
TIMEZONES = [
    ("local", "🌐 Auto"),
    ("UTC", "UTC · UTC+0"),
    ("Asia/Shanghai", "Asia/Shanghai · UTC+8"),
    ("Asia/Tokyo", "Asia/Tokyo · UTC+9"),
    ("Asia/Seoul", "Asia/Seoul · UTC+9"),
    ("Asia/Singapore", "Asia/Singapore · UTC+8"),
    ("Asia/Kolkata", "Asia/Kolkata · UTC+5:30"),
    ("Asia/Dubai", "Asia/Dubai · UTC+4"),
    ("Europe/Moscow", "Europe/Moscow · UTC+3"),
    ("Europe/Berlin", "Europe/Berlin · UTC+1"),
    ("Europe/Paris", "Europe/Paris · UTC+1"),
    ("Europe/London", "Europe/London · UTC+0"),
    ("Europe/Madrid", "Europe/Madrid · UTC+1"),
    ("Europe/Rome", "Europe/Rome · UTC+1"),
    ("America/Sao_Paulo", "America/Sao_Paulo · UTC-3"),
    ("America/New_York", "America/New_York · UTC-5"),
    ("America/Chicago", "America/Chicago · UTC-6"),
    ("America/Denver", "America/Denver · UTC-7"),
    ("America/Los_Angeles", "America/Los_Angeles · UTC-8"),
    ("Pacific/Honolulu", "Pacific/Honolulu · UTC-10"),
    ("Australia/Sydney", "Australia/Sydney · UTC+11"),
    ("Pacific/Auckland", "Pacific/Auckland · UTC+13"),
]

MAX_VALUE_CHARS = 240
MAX_PATH_CHARS = 160


def _compact(value: Any, limit: int = MAX_VALUE_CHARS) -> str:
    """把任意 JSON 值压成单行可读文本并截断。"""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def endpoint_cards(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    endpoints = data.get("endpoints")
    if not isinstance(endpoints, dict):
        return []
    cards: List[Dict[str, Any]] = []
    for name, label in ENDPOINT_LABELS:
        entry = endpoints.get(name)
        if not isinstance(entry, dict):
            continue
        summary = entry.get("summary")
        rows = []
        if isinstance(summary, dict):
            for key, value in summary.items():
                pattern = SUMMARY_PATTERNS.get(key)
                if pattern:
                    param = "id" if key == "deploy_id" else "value" if key == "selected_pack_format" else "name" if key == "bingo_name" else "status" if key == "bingo_state" else "count"
                    rows.append(
                        {
                            "key": key,
                            "pattern": pattern,
                            "params": {param: _format_value(value)},
                            "label": key.replace("_", " "),
                        }
                    )
                else:
                    # 未知字段：直接显示字段名，不猜中文
                    rows.append(
                        {"key": key, "pattern": "", "params": {}, "label": key.replace("_", " ")}
                    )
        cards.append(
            {
                "name": name,
                "label": label,
                "status": str(entry.get("status") or "unknown"),
                "fetched_at": entry.get("fetched_at"),
                "change_count": entry.get("change_count"),
                "rows": rows,
            }
        )
    return cards


def recent_changes(data: Dict[str, Any], limit: int = 60) -> List[Dict[str, Any]]:
    changes = data.get("changes")
    if not isinstance(changes, list):
        return []
    rows: List[Dict[str, Any]] = []
    for item in changes[:limit]:
        if not isinstance(item, dict):
            continue
        change_type = str(item.get("type") or "modified")
        row = {
            "type": change_type,
            "type_label": CHANGE_TYPE_LABELS.get(change_type, change_type),
            "endpoint": str(item.get("endpoint") or ""),
            "path": _compact(item.get("path"), MAX_PATH_CHARS),
            "time": str(item.get("time") or ""),
            "old": _compact(item.get("old")),
            "new": _compact(item.get("new")),
        }
        rows.append(row)
    return rows


def total_changes(data: Dict[str, Any]) -> int:
    changes = data.get("changes")
    return len(changes) if isinstance(changes, list) else 0


