"""变更记录的保留期限：只保留最近 30 天。

页面上的「最近变更」「资源包文件变更」都属于增删改日志，历史越久价值越低，
而且会把页面拉得很长。这里把期限集中成一处常量，静态 Pages 构建、本地
FastAPI 服务与 SQLite 清理共用同一套口径，避免两边对「30 天」理解不一致。

数据里的时间字段混用过两种写法（北京时间 ``2026-11-05 12:54:54`` 与
UTC ISO ``2026-11-03T06:27:26Z``），统一交给 ``app.timeutil`` 归一后再比较。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from app.timeutil import BEIJING_TZ, format_time, parse_beijing

# 变更记录的保留天数：超过这个天数的记录不再展示，并从数据文件中清理。
RETENTION_DAYS = 30


def cutoff_beijing(days: int = RETENTION_DAYS) -> str:
    """返回保留窗口的起点（北京时间字符串）。

    SQLite 里 ``created_at`` 存的就是同格式字符串，可以直接做字符串比较。
    """
    return (datetime.now(BEIJING_TZ) - timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def is_within_days(value: Any, days: int = RETENTION_DAYS) -> bool:
    """判断一条记录的时间是否还在保留窗口内。

    时间缺失或无法解析的记录按「过期」处理：这类记录无法证明是新的，
    留在页面上只会让变更列表无限增长。
    """
    if value is None:
        return False
    parsed = parse_beijing(format_time(str(value)))
    if parsed is None:
        return False
    return parsed >= datetime.now(BEIJING_TZ) - timedelta(days=days)


def filter_recent(
    records: Iterable[Dict[str, Any]],
    key: str = "time",
    days: int = RETENTION_DAYS,
) -> List[Dict[str, Any]]:
    """过滤出一组仍需要展示的变更记录（保持原有顺序）。"""
    result: List[Dict[str, Any]] = []
    for record in records:
        if isinstance(record, dict) and is_within_days(record.get(key), days):
            result.append(record)
    return result


def prune_list(
    records: List[Dict[str, Any]],
    key: str = "time",
    days: int = RETENTION_DAYS,
) -> tuple[List[Dict[str, Any]], int]:
    """返回 (保留后的列表, 被移除的条数)，便于调用方决定是否需要写盘。"""
    kept = filter_recent(records, key=key, days=days)
    return kept, len(records) - len(kept)


def describe_window(days: int = RETENTION_DAYS) -> str:
    """给页面提示文案用的中文描述。"""
    return f"仅保留最近 {days} 天"
