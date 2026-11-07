"""Bingo 任务的纯数据处理逻辑。

Hypixel Skyblock 的 ``/v2/resources/skyblock/bingo`` 返回当前这一期 Bingo 的
时间窗口与全部任务。本模块只负责数据整形，不生成任何 HTML——静态 Pages
页面（``scripts/build_dashboard.py``）与本地 FastAPI 页面共用同一份逻辑，
避免两边对「进度」「状态」「变更范围」的判断出现分歧。

几个关键约定：

* ``start`` / ``end`` / ``lastUpdated`` 都是 epoch 毫秒，统一转北京时间。
* 每个 goal 的 ``progress`` 是全服累计进度，每次抓取都会增长，**不参与变更
  比对**，只用于页面上的进度条。
* ``lastUpdated`` 同理只表示官方数据刷新时间，页面单独展示，不进 diff。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.mctext import strip_mc_codes
from app.timeutil import format_time, from_epoch_ms, parse_beijing, to_epoch_ms

BINGO_STATE_LABELS = {
    "upcoming": "未开始",
    "active": "进行中",
    "ended": "已结束",
    "unknown": "未知",
}

# 需要从「任务内容」中剔除、但仍要展示的字段
VOLATILE_GOAL_KEYS = ("progress",)


def format_amount(value: Any) -> str:
    """把目标数值压缩成 ``10M`` / ``72.5K`` 这类便于阅读的写法。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    for unit, div in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if abs(num) >= div:
            scaled = num / div
            text = f"{scaled:.2f}".rstrip("0").rstrip(".")
            return f"{text}{unit}"
    if num == int(num):
        return f"{int(num):,}"
    return str(num)


def humanize_delta(delta: timedelta) -> str:
    """把时间差转成「3 天 4 小时」这种中文描述。"""
    seconds = int(abs(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} 小时 {minutes} 分钟" if minutes else f"{hours} 小时"
    days, hours = divmod(hours, 24)
    return f"{days} 天 {hours} 小时" if hours else f"{days} 天"


def bingo_state(start: str, end: str, now: str) -> Tuple[str, str]:
    """判断本期处于未开始 / 进行中 / 已结束，并给出剩余或已过的时间。"""
    now_dt = parse_beijing(now)
    start_dt = parse_beijing(start)
    end_dt = parse_beijing(end)
    if now_dt is None or start_dt is None or end_dt is None:
        return "unknown", "-"
    if now_dt < start_dt:
        return "upcoming", f"距开始还有 {humanize_delta(start_dt - now_dt)}"
    if now_dt > end_dt:
        return "ended", f"已于 {humanize_delta(now_dt - end_dt)}前结束"
    return "active", f"剩余 {humanize_delta(end_dt - now_dt)}"


def normalize_time(value: Any) -> str:
    """把接口里的 epoch 毫秒（或已格式化的字符串）统一成北京时间展示串。"""
    if isinstance(value, bool):
        return "-"
    if isinstance(value, (int, float)):
        return from_epoch_ms(value)
    text = str(value or "").strip()
    if not text:
        return "-"
    return format_time(text)


def normalize_bingo(payload: Dict[str, Any]) -> Dict[str, Any]:
    """构造用于变更比对与快照的稳定结构。

    剔除两类每次抓取都可能变化、但不属于「任务内容」的字段：
    ``lastUpdated``（官方数据刷新时间）与每个 goal 的 ``progress``（全服累计
    进度）。同时把 ``start`` / ``end`` 转成北京时间、``lore`` 去掉颜色码，
    让变更日志里出现的是人能直接读懂的文本。
    """
    goals = payload.get("goals") or []
    goal_map: Dict[str, Dict[str, Any]] = {}
    for goal in goals:
        if not isinstance(goal, dict):
            continue
        gid = str(goal.get("id") or goal.get("name") or "unknown")
        entry: Dict[str, Any] = {"name": goal.get("name", "-")}
        entry["lore"] = strip_mc_codes(goal.get("lore") or "")
        if "requiredAmount" in goal:
            entry["required_amount"] = goal["requiredAmount"]
        tiers = goal.get("tiers")
        if isinstance(tiers, list) and tiers:
            entry["tiers"] = " / ".join(format_amount(tier) for tier in tiers)
        goal_map[gid] = entry
    return {
        "id": payload.get("id"),
        "name": payload.get("name", "-"),
        "modifier": payload.get("modifier", "-"),
        "start": normalize_time(payload.get("start")),
        "end": normalize_time(payload.get("end")),
        "goals": goal_map,
    }


def bingo_view(
    payload: Dict[str, Any],
    *,
    generated_at: str,
    previous: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """生成展示用的 Bingo 数据（保留原始 goals、颜色码与全服进度）。

    ``first_seen`` 记录本仓库第一次抓到这一期的时刻：接口的 ``lastUpdated``
    只表示数据刷新时间，回答不了「这期任务是何时发布的」。同一期再次抓取时
    沿用已有记录，不会被刷新掉。
    """
    goals: List[Dict[str, Any]] = [
        dict(g) for g in (payload.get("goals") or []) if isinstance(g, dict)
    ]
    bingo_id = payload.get("id")
    start = from_epoch_ms(payload.get("start"))
    end = from_epoch_ms(payload.get("end"))
    start_ms = to_epoch_ms(payload.get("start"))
    end_ms = to_epoch_ms(payload.get("end"))
    state, remaining = bingo_state(start, end, generated_at)

    first_seen = generated_at
    if previous and str(previous.get("id")) == str(bingo_id) and previous.get("first_seen"):
        first_seen = str(previous["first_seen"])

    for goal in goals:
        goal["progress_info"] = goal_progress(goal)
        goal["lore_lines"] = [str(x) for x in (goal.get("fullLore") or [])] or (
            [str(goal.get("lore"))] if goal.get("lore") else []
        )
        goal["requirement_text"] = requirement_text(goal)

    return {
        "generated_at": generated_at,
        "id": bingo_id,
        "name": payload.get("name") or "-",
        "modifier": payload.get("modifier") or "-",
        "start": start,
        "end": end,
        # 绝对时间点（epoch 毫秒）：页面倒计时交给浏览器本地计算，
        # 不用构建时刻的 remaining 文本，避免 Actions 延迟导致倒计时偏慢。
        "start_ms": start_ms,
        "end_ms": end_ms,
        "published_at": from_epoch_ms(payload.get("lastUpdated")),
        "first_seen": first_seen,
        "state": state,
        "state_label": BINGO_STATE_LABELS.get(state, state),
        "remaining": remaining,
        "goal_count": len(goals),
        "goals": goals,
    }


def requirement_text(goal: Dict[str, Any]) -> str:
    """把 tiers / requiredAmount 转成一行可读的目标描述。"""
    tiers = goal.get("tiers")
    if isinstance(tiers, list) and tiers:
        return " / ".join(format_amount(t) for t in tiers)
    required = goal.get("requiredAmount")
    if required is not None:
        return format_amount(required)
    return ""


def goal_progress(goal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """计算单个任务的全服进度；没有 tiers 或 progress 时返回 ``None``。"""
    tiers = goal.get("tiers")
    progress = goal.get("progress")
    if not isinstance(tiers, list) or not tiers:
        return None
    if not isinstance(progress, (int, float)):
        return None
    numeric = [t for t in tiers if isinstance(t, (int, float))]
    if not numeric:
        return None
    top = max(numeric)
    if not top:
        return None
    ratio = max(0.0, min(1.0, float(progress) / float(top)))
    reached = sum(1 for t in numeric if progress >= t)
    return {
        "ratio": ratio,
        "percent": round(ratio * 100, 1),
        "reached": reached,
        "total": len(numeric),
        "progress": progress,
        "progress_text": format_amount(progress),
        "top": top,
        "top_text": format_amount(top),
    }
