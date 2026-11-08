"""限时特卖（Firesales）的归一化、状态与变更比对。

数据来自 ``/v2/skyblock/firesales``，一次响应里可能同时包含多期特卖
（不同物品、不同时间窗口），因此所有展示与比对都按「单期 = 一条记录」
切分，以 ``item_id + start`` 作为唯一键。

特卖在开始前官方随时可能调整时间 / 数量 / 价格，所以构建脚本与本地服务
每次运行都会重新抓取并覆盖 ``data/firesales.json``，不存在「查过就跳过」。
"""

from typing import Any, Dict, List, Optional, Tuple

from app.timeutil import from_epoch_ms, to_epoch_ms

SALE_STATE_LABELS = {
    "upcoming": "即将开始",
    "active": "进行中",
    "ended": "已结束",
    "unknown": "未知",
}

# 特卖用宝石（Gems）结算
CURRENCY_LABEL = "宝石"


def prettify_item_id(item_id: Any) -> str:
    """WITHER_GOGGLES_LAGOON_SPECTRE -> "Wither Goggles Lagoon Spectre"。"""
    text = str(item_id or "").strip()
    if not text:
        return "未知道具"
    return " ".join(part.capitalize() for part in text.split("_") if part)


def format_amount(value: Any) -> str:
    """950 -> "950"；大数加千位分隔符，非法值返回 "-"。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "-"
    return f"{int(value):,}"


def sale_state(start_ms: Any, end_ms: Any, now_ms: int) -> Tuple[str, str]:
    """按毫秒时间戳判断单期特卖状态。"""
    start = to_epoch_ms(start_ms)
    end = to_epoch_ms(end_ms)
    if not start or not end:
        return "unknown", SALE_STATE_LABELS["unknown"]
    if now_ms < start:
        return "upcoming", SALE_STATE_LABELS["upcoming"]
    if now_ms > end:
        return "ended", SALE_STATE_LABELS["ended"]
    return "active", SALE_STATE_LABELS["active"]


def normalize_sales(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把接口响应整理成稳定的销售列表（按开始时间、物品 id 排序）。

    结果只包含比对与展示需要的字段；每期同时保留 epoch 毫秒（给浏览器）
    与北京时间字符串（给快照和变更日志），比对方可读。
    """
    raw = payload.get("sales") if isinstance(payload, dict) else None
    sales: List[Dict[str, Any]] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        start_ms = to_epoch_ms(entry.get("start"))
        end_ms = to_epoch_ms(entry.get("end"))
        item = {
            "item_id": str(entry.get("item_id") or "").strip() or "未知道具",
            "start_ms": start_ms,
            "end_ms": end_ms,
            "start": from_epoch_ms(entry.get("start")),
            "end": from_epoch_ms(entry.get("end")),
            "amount": entry.get("amount") if isinstance(entry.get("amount"), (int, float)) and not isinstance(entry.get("amount"), bool) else None,
            "price": entry.get("price") if isinstance(entry.get("price"), (int, float)) and not isinstance(entry.get("price"), bool) else None,
        }
        # 持久化数据里已解析过可读名称，原样保留，避免本地页面再解析一次。
        if entry.get("name"):
            item["name"] = str(entry["name"])
        sales.append(item)
    sales.sort(key=lambda s: (s["start_ms"] or 0, s["item_id"]))
    return sales


def sale_key(sale: Dict[str, Any]) -> str:
    """单期特卖的唯一键：同一物品可能分期上架，start 参与区分。"""
    return f"{sale.get('item_id', '-')}@{sale.get('start_ms') or 0}"


def build_item_names(items_payload: Any) -> Dict[str, str]:
    """从 items 端点快照构造 id -> name 映射，用于把特卖物品翻译成可读名称。"""
    names: Dict[str, str] = {}
    items: Any = None
    if isinstance(items_payload, dict):
        items = items_payload.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("id") and item.get("name"):
                names[str(item["id"])] = str(item["name"])
    return names


def describe_sale(sale: Dict[str, Any]) -> str:
    """一行可读摘要，用于变更日志：物品 ×数量 @价格（时间窗口）。"""
    name = sale.get("name") or prettify_item_id(sale.get("item_id"))
    price = format_amount(sale.get("price"))
    amount = format_amount(sale.get("amount"))
    return f"{name} ×{amount} @ {price} {CURRENCY_LABEL}（{sale.get('start', '-')} → {sale.get('end', '-')}）"


def diff_sales(old: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 sale_key 比对两期销售列表，输出与端点 diff 相同结构的变更记录。

    * 新出现的 key → added；
    * 消失的 key → removed；
    * 同 key 的字段变化（价格 / 数量 / 结束时间）→ 逐字段 modified。
      开始时间参与 key，官方推迟开卖会体现为「旧期删除 + 新期新增」，
      语义上更直观。
    """
    old_map = {sale_key(s): s for s in old}
    new_map = {sale_key(s): s for s in new}
    changes: List[Dict[str, Any]] = []

    for key in sorted(new_map.keys() - old_map.keys()):
        sale = dict(new_map[key])
        sale["name"] = prettify_item_id(sale["item_id"])
        changes.append(
            {
                "type": "added", "path": f"sales.{key}",
                "old": None, "old_full": None,
                "new": f"新增特卖：{describe_sale(sale)}", "new_full": sale,
            }
        )
    for key in sorted(old_map.keys() - new_map.keys()):
        sale = dict(old_map[key])
        sale["name"] = prettify_item_id(sale["item_id"])
        changes.append(
            {
                "type": "removed", "path": f"sales.{key}",
                "old": f"移除特卖：{describe_sale(sale)}", "old_full": sale,
                "new": None, "new_full": None,
            }
        )
    for key in sorted(old_map.keys() & new_map.keys()):
        old_sale, new_sale = old_map[key], new_map[key]
        for field, label in (("price", "价格"), ("amount", "数量"), ("end_ms", "结束时间")):
            if old_sale.get(field) != new_sale.get(field):
                item_label = prettify_item_id(old_sale.get("item_id"))
                if field == "end_ms":
                    old_value = from_epoch_ms(old_sale.get(field))
                    new_value = from_epoch_ms(new_sale.get(field))
                else:
                    old_value = format_amount(old_sale.get(field))
                    new_value = format_amount(new_sale.get(field))
                changes.append(
                    {
                        "type": "modified", "path": f"sales.{key}.{field}",
                        "old": f"{item_label} {label}：{old_value}",
                        "old_full": old_sale.get(field),
                        "new": f"{item_label} {label}：{new_value}",
                        "new_full": new_sale.get(field),
                    }
                )
    return changes


def firesales_view(
    payload: Dict[str, Any],
    generated_at: str,
    item_names: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """生成展示用的特卖数据（含状态、可读名称与统计）。

    每次抓取都会整体重建并覆盖持久化文件——特卖开始前可能被官方调整，
    不能因为内容暂时没变就跳过刷新。
    """
    import time as _time

    now_ms = int(_time.time() * 1000)
    names = item_names or {}
    sales: List[Dict[str, Any]] = []
    for sale in normalize_sales(payload):
        entry = dict(sale)
        entry["name"] = (
            names.get(entry["item_id"])
            or sale.get("name")
            or prettify_item_id(entry["item_id"])
        )
        entry["state"], entry["state_label"] = sale_state(entry["start_ms"], entry["end_ms"], now_ms)
        entry["key"] = sale_key(sale)
        sales.append(entry)

    counts = {"upcoming": 0, "active": 0, "ended": 0}
    for sale in sales:
        if sale["state"] in counts:
            counts[sale["state"]] += 1

    return {
        "generated_at": generated_at,
        "sales": sales,
        "total": len(sales),
        "upcoming_count": counts["upcoming"],
        "active_count": counts["active"],
        "ended_count": counts["ended"],
    }
