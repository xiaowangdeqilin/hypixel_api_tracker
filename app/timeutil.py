"""统一时间处理：项目内所有对外展示与写入的时间一律为北京时间（UTC+8）。

历史数据里既存在 ``2026-11-02T21:57:06Z`` 这类 UTC 时间戳，也存在
``2026-11-03T06:27:24`` 这类无时区后缀的 UTC 字符串。``format_time()`` 会把它们
统一转换成 ``YYYY-MM-DD HH:MM:SS`` 的北京时间；新写入的数据一律由
``now_beijing()`` 生成（不带时区后缀），避免再次产生歧义。

本模块只依赖标准库，静态 Pages 构建脚本与本地 FastAPI 服务共用同一份实现。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

BEIJING_TZ = timezone(timedelta(hours=8))

# 新写入的北京时间字符串格式（空格分隔、无时区后缀）
_BEIJING_PLAIN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def now_beijing() -> str:
    """返回当前北京时间的展示字符串，例如 ``2026-11-03 14:30:00``。"""
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def from_epoch_ms(value: object) -> str:
    """把 Hypixel 常用的毫秒时间戳转换成北京时间展示字符串。

    接口里的 ``start`` / ``end`` / ``lastUpdated`` 都是 epoch 毫秒。无效值
    统一返回 ``-``，避免页面出现 ``1970-01-01`` 这类误导性时间。
    """
    try:
        ms = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "-"
    if ms <= 0:
        return "-"
    try:
        return datetime.fromtimestamp(ms / 1000, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "-"


def parse_beijing(value: Optional[str]) -> Optional[datetime]:
    """把北京时间展示字符串解析回带时区的 ``datetime``。

    用于计算倒计时等相对时间；无法解析时返回 ``None``。
    """
    if not value:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING_TZ)
    return dt.astimezone(BEIJING_TZ)


def format_time(value: Optional[str]) -> str:
    """把任意历史时间字符串转换成北京时间展示格式。

    规则：

    * 已是 ``YYYY-MM-DD HH:MM:SS`` 的新数据 → 原样返回（本身就是北京时间）；
    * 以 ``Z`` 结尾或带显式偏移 → 按该时区解析后换算到 UTC+8；
    * 无时区后缀但含 ``T`` 的旧数据 → 按 UTC 解析后换算到 UTC+8；
    * 无法解析 → 原样返回，避免丢信息。
    """
    if value is None:
        return "-"
    text = str(value).strip()
    if not text:
        return "-"
    if _BEIJING_PLAIN.match(text):
        return text
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return text
