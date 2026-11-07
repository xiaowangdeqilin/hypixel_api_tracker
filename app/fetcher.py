import hashlib
import json
from typing import Any, Dict, List, Tuple

import requests

from app.bingo import normalize_bingo
from app.config import API_KEY, REQUEST_TIMEOUT_SECONDS
from app.db import get_latest_snapshot, insert_change_event, insert_snapshot
from app.diff import diff_values

# 这些顶层字段只反映「资源版本号 / 抓取时间 / 请求状态」，不属于业务内容。
# 版本号由页面顶部的版本监测单独负责，端点 diff 必须忽略它们，避免把版本
# 变化误判成内容更新；lastUpdated 每次抓取都会变，同样需要排除。
META_KEYS = ("version", "lastUpdated", "success")


def strip_meta(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    return {key: value for key, value in payload.items() if key not in META_KEYS}


def fetch_json(url: str) -> Dict[str, Any]:
    params = {}
    if API_KEY:
        params["key"] = API_KEY

    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def checksum_payload(payload: Dict[str, Any], endpoint_name: str = "") -> str:
    """只对业务内容计算校验和，元数据变化不会触发 diff。

    Bingo 的每个任务都带一个全服累计进度 ``progress``，它会不断增长；
    若参与校验和，每次抓取都会触发一次「有变更」的 diff。这里对 bingo
    改用归一化后的结构，进度变化不再产生噪音。
    """
    body = normalize_bingo(payload) if endpoint_name == "bingo" else strip_meta(payload)
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def diff_for_endpoint(
    endpoint_name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> List[Tuple[str, str, Any, Any]]:
    """按端点生成变更，统一剔除 version / lastUpdated / success。"""
    # Bingo 先归一化：剔除全服进度与数据刷新时间，时间转成北京时间字符串。
    if endpoint_name == "bingo":
        old_bingo, new_bingo = normalize_bingo(old), normalize_bingo(new)
        old_id, new_id = old_bingo.get("id"), new_bingo.get("id")
        if old_id is not None and new_id is not None and str(old_id) != str(new_id):
            # 换期时整批任务都换掉，逐条比对只会刷出几十条无意义增删。
            return [
                (
                    "id",
                    "value_changed",
                    f"第 {old_id} 期 {old_bingo.get('name', '')}".strip(),
                    f"第 {new_id} 期 {new_bingo.get('name', '')}".strip(),
                )
            ]
        return diff_values(old_bingo, new_bingo, path="root")

    old_clean, new_clean = strip_meta(old), strip_meta(new)

    # 集市价格每秒都在变动，本地实时仪表盘同样只追踪商品增删。
    if endpoint_name == "bazaar":
        old_products = old_clean.get("products") or {}
        new_products = new_clean.get("products") or {}
        if not isinstance(old_products, dict) or not isinstance(new_products, dict):
            return []
        old_ids, new_ids = set(old_products), set(new_products)
        changes: List[Tuple[str, str, Any, Any]] = []
        for pid in sorted(new_ids - old_ids):
            changes.append((f"products.{pid}", "added", None, "新增集市商品"))
        for pid in sorted(old_ids - new_ids):
            changes.append((f"products.{pid}", "removed", "集市商品", None))
        return changes

    return diff_values(old_clean, new_clean, path="root")


def process_endpoint(endpoint_name: str, endpoint_cfg: Dict[str, Any]):
    url = endpoint_cfg["url"]
    try:
        payload = fetch_json(url)
        status_code = 200
    except Exception as exc:  # pragma: no cover - 网络异常在运行时处理
        payload = {"error": str(exc)}
        status_code = 500

    # 关键：必须在写入新快照之前拿到上一次快照，否则 get_latest_snapshot()
    # 会返回刚插入的这条，diff 永远不会执行。
    previous = get_latest_snapshot(endpoint_name)

    checksum = checksum_payload(payload, endpoint_name)
    snapshot_id = insert_snapshot(endpoint_name, status_code, payload, checksum)

    if previous and previous["checksum"] != checksum:
        changes = diff_for_endpoint(endpoint_name, previous["payload"], payload)
        for path, change_type, old_value, new_value in changes:
            insert_change_event(
                endpoint=endpoint_name,
                snapshot_id=snapshot_id,
                previous_snapshot_id=previous["id"],
                change_type=change_type,
                path=path,
                old_value=old_value,
                new_value=new_value,
            )

    return {
        "endpoint": endpoint_name,
        "status_code": status_code,
        "snapshot_id": snapshot_id,
        "checksum": checksum,
    }
