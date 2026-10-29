import hashlib
import json
from typing import Any, Dict

import requests

from app.config import API_KEY, ENDPOINTS, REQUEST_TIMEOUT_SECONDS
from app.db import get_latest_snapshot, insert_change_event, insert_snapshot
from app.diff import diff_values


def fetch_json(url: str) -> Dict[str, Any]:
    params = {}
    if API_KEY:
        params["key"] = API_KEY

    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def checksum_payload(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def process_endpoint(endpoint_name: str, endpoint_cfg: Dict[str, Any]):
    url = endpoint_cfg["url"]
    try:
        payload = fetch_json(url)
        status_code = 200
    except Exception as exc:  # pragma: no cover - network failures handled at runtime
        payload = {"error": str(exc)}
        status_code = 500

    checksum = checksum_payload(payload)
    snapshot_id = insert_snapshot(endpoint_name, status_code, payload, checksum)

    previous = get_latest_snapshot(endpoint_name)
    if previous and previous["id"] != snapshot_id and previous["payload"] != payload:
        changes = diff_values(previous["payload"], payload, path="root")
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
