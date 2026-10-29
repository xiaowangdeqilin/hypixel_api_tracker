#!/usr/bin/env python3
"""
Build a static GitHub Pages dashboard for the Hypixel SkyBlock API tracker.

This script is designed to run inside a scheduled GitHub Actions workflow.
It fetches the four configured endpoints, compares each payload against the
previously committed snapshot, records structural changes, and renders a
self-contained HTML dashboard plus a data.json file.

Snapshots are stored under ``data/snapshots/`` so they are committed back to
the repository and available for the next run's diff.  A rolling change log
is kept in ``data/changes.json``.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
CHANGES_FILE = DATA_DIR / "changes.json"
SITE_DIR = BASE_DIR / "site"

ENDPOINTS: Dict[str, str] = {
    "collections": "https://api.hypixel.net/v2/resources/skyblock/collections",
    "skills": "https://api.hypixel.net/v2/resources/skyblock/skills",
    "items": "https://api.hypixel.net/v2/resources/skyblock/items",
    "bazaar": "https://api.hypixel.net/v2/skyblock/bazaar",
}

REQUEST_TIMEOUT = 30
MAX_CHANGE_LOG = 500

# Bazaar price data changes every few seconds; we only care about structural
# changes (products added / removed) for the change log.  We still snapshot a
# small summary so the dashboard can show current counts.
BAZAAR_STRUCTURAL_ONLY = True


# --------------------------------------------------------------------------- #
# Snapshot helpers
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def checksum(obj: Any) -> str:
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def snapshot_path(name: str) -> Path:
    return SNAPSHOT_DIR / f"{name}.json"


def load_snapshot(name: str) -> Optional[Dict[str, Any]]:
    path = snapshot_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_snapshot(name: str, snapshot: Dict[str, Any]) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path(name).write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# Diff engine
# --------------------------------------------------------------------------- #


def diff_dict(old: Dict[str, Any], new: Dict[str, Any], prefix: str = "") -> List[Dict[str, Any]]:
    """Return a list of structural changes between two dicts."""
    changes: List[Dict[str, Any]] = []
    old_keys = set(old.keys())
    new_keys = set(new.keys())

    for key in sorted(new_keys - old_keys):
        path = f"{prefix}.{key}" if prefix else key
        changes.append({"type": "added", "path": path, "old": None, "new": _summarize(new[key])})

    for key in sorted(old_keys - new_keys):
        path = f"{prefix}.{key}" if prefix else key
        changes.append({"type": "removed", "path": path, "old": _summarize(old[key]), "new": None})

    for key in sorted(old_keys & new_keys):
        path = f"{prefix}.{key}" if prefix else key
        ov, nv = old[key], new[key]
        if isinstance(ov, dict) and isinstance(nv, dict):
            changes.extend(diff_dict(ov, nv, path))
        elif ov != nv:
            changes.append(
                {
                    "type": "modified",
                    "path": path,
                    "old": _summarize(ov),
                    "new": _summarize(nv),
                }
            )
    return changes


def diff_list_by_key(
    old: List[Dict[str, Any]],
    new: List[Dict[str, Any]],
    key_field: str,
    prefix: str = "",
) -> List[Dict[str, Any]]:
    """Diff a list of dicts using a stable key field (e.g. item id)."""
    old_map = {str(item.get(key_field)): item for item in old if isinstance(item, dict)}
    new_map = {str(item.get(key_field)): item for item in new if isinstance(item, dict)}
    return diff_dict(old_map, new_map, prefix)


def _summarize(value: Any) -> str:
    """Compact, human-friendly representation of a value for the change log."""
    if value is None:
        return "null"
    if isinstance(value, dict):
        return f"{{{len(value)} keys}}"
    if isinstance(value, list):
        return f"[{len(value)} items]"
    text = json.dumps(value, ensure_ascii=False)
    if len(text) > 120:
        text = text[:117] + "..."
    return text


# --------------------------------------------------------------------------- #
# Endpoint-specific diffs
# --------------------------------------------------------------------------- #


def diff_collections(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    old_c = old.get("collections", {})
    new_c = new.get("collections", {})
    return diff_dict(old_c, new_c, "collections")


def diff_skills(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    old_s = old.get("skills", {})
    new_s = new.get("skills", {})
    return diff_dict(old_s, new_s, "skills")


def diff_items(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    old_items = old.get("items", [])
    new_items = new.get("items", [])
    if not isinstance(old_items, list) or not isinstance(new_items, list):
        return diff_dict({"items": old_items}, {"items": new_items}, "root")
    return diff_list_by_key(old_items, new_items, "id", "items")


def diff_bazaar(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Only structural changes: products added or removed.

    Snapshots store ``product_ids`` (a sorted list) instead of the full
    products map, so we diff that list.
    """
    old_ids = set(old.get("product_ids", []))
    new_ids = set(new.get("product_ids", []))

    changes: List[Dict[str, Any]] = []
    for pid in sorted(new_ids - old_ids):
        changes.append({"type": "added", "path": f"products.{pid}", "old": None, "new": "new bazaar product"})
    for pid in sorted(old_ids - new_ids):
        changes.append({"type": "removed", "path": f"products.{pid}", "old": "bazaar product", "new": None})
    return changes


DIFF_FUNCS = {
    "collections": diff_collections,
    "skills": diff_skills,
    "items": diff_items,
    "bazaar": diff_bazaar,
}


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #


def fetch_endpoint(name: str, url: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    api_key = os.getenv("HYPIXEL_API_KEY", "")
    params = {"key": api_key} if api_key else None
    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json(), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def summarize_payload(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Produce a small summary of the payload for the dashboard cards."""
    if name == "collections":
        coll = payload.get("collections", {})
        return {
            "collection_count": len(coll) if isinstance(coll, dict) else 0,
            "total_items": sum(len(v.get("items", {})) for v in coll.values() if isinstance(v, dict))
            if isinstance(coll, dict)
            else 0,
        }
    if name == "skills":
        skills = payload.get("skills", {})
        return {"skill_count": len(skills) if isinstance(skills, dict) else 0}
    if name == "items":
        items = payload.get("items", [])
        return {"item_count": len(items) if isinstance(items, list) else 0}
    if name == "bazaar":
        products = payload.get("products", {})
        return {"product_count": len(products) if isinstance(products, dict) else 0}
    return {}


def make_snapshot(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build the snapshot dict that gets persisted to disk."""
    snap = {
        "endpoint": name,
        "fetched_at": _now_iso(),
        "status_code": 200,
        "checksum": checksum(payload),
        "summary": summarize_payload(name, payload),
        "payload": payload,
    }
    if name == "bazaar" and BAZAAR_STRUCTURAL_ONLY:
        # We only track structural changes (products added/removed) for bazaar,
        # so store just the sorted list of product ids to keep the snapshot tiny
        # and stable across price fluctuations.
        products = payload.get("products", {})
        product_ids = sorted(products.keys()) if isinstance(products, dict) else []
        slim = {"product_ids": product_ids}
        snap["payload"] = {"success": payload.get("success"), "product_ids": product_ids}
        snap["checksum"] = checksum(product_ids)
    return snap


# --------------------------------------------------------------------------- #
# Change log
# --------------------------------------------------------------------------- #


def load_changes() -> List[Dict[str, Any]]:
    if not CHANGES_FILE.exists():
        return []
    try:
        return json.loads(CHANGES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_changes(changes: List[Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CHANGES_FILE.write_text(
        json.dumps(changes, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_changes(new_changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    existing = load_changes()
    merged = new_changes + existing
    return merged[:MAX_CHANGE_LOG]


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #


def render_html(
    *,
    generated_at: str,
    endpoints: Dict[str, Dict[str, Any]],
    changes: List[Dict[str, Any]],
) -> str:
    cards = []
    for name in ENDPOINTS:
        ep = endpoints.get(name, {})
        status = ep.get("status", "error")
        status_class = "ok" if status == "ok" else "error"
        fetched = ep.get("fetched_at", "-")
        summary = ep.get("summary", {})
        summary_parts = [f"{k}: {v}" for k, v in summary.items()]
        summary_html = "<br>".join(html.escape(s) for s in summary_parts) if summary_parts else "-"
        cards.append(
            f"""
            <div class="card">
              <div class="card-header">
                <span class="endpoint-name">{html.escape(name)}</span>
                <span class="badge badge-{status_class}">{html.escape(status.upper())}</span>
              </div>
              <div class="fetched">{html.escape(fetched)}</div>
              <div class="summary">{summary_html}</div>
            </div>
            """
        )

    change_rows = []
    if changes:
        for c in changes[:100]:
            change_rows.append(
                f"""
                <tr>
                  <td><span class="type type-{html.escape(c.get('type',''))}">{html.escape(c.get('type',''))}</span></td>
                  <td class="endpoint">{html.escape(c.get('endpoint',''))}</td>
                  <td class="path"><code>{html.escape(c.get('path',''))}</code></td>
                  <td class="old">{html.escape(c.get('old') or '-')}</td>
                  <td class="new">{html.escape(c.get('new') or '-')}</td>
                  <td class="time">{html.escape(c.get('time',''))}</td>
                </tr>
                """
            )
    else:
        change_rows.append(
            '<tr><td colspan="6" class="empty">No changes recorded yet. Changes will appear here once an endpoint returns different data.</td></tr>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Hypixel SkyBlock API Tracker</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #e2e8f0;
      margin: 0;
      padding: 24px;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    h1 {{ margin: 0 0 4px 0; font-size: 28px; }}
    .subtitle {{ color: #94a3b8; margin-bottom: 28px; font-size: 14px; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
      margin-bottom: 32px;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 12px;
      padding: 18px;
    }}
    .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
    .endpoint-name {{ font-weight: 700; font-size: 16px; text-transform: capitalize; }}
    .badge {{ padding: 2px 10px; border-radius: 999px; font-size: 11px; font-weight: 700; }}
    .badge-ok {{ background: #065f46; color: #6ee7b7; }}
    .badge-error {{ background: #7f1d1d; color: #fca5a5; }}
    .fetched {{ font-size: 12px; color: #94a3b8; margin-bottom: 10px; }}
    .summary {{ font-size: 13px; color: #cbd5e1; line-height: 1.6; }}
    h2 {{ font-size: 18px; margin: 0 0 16px 0; }}
    .panel {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 24px;
      overflow-x: auto;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #334155; vertical-align: top; }}
    th {{ background: #0f172a; color: #94a3b8; font-weight: 600; position: sticky; top: 0; }}
    tr:hover td {{ background: #283548; }}
    .type {{ padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; text-transform: uppercase; }}
    .type-added {{ background: #065f46; color: #6ee7b7; }}
    .type-removed {{ background: #7f1d1d; color: #fca5a5; }}
    .type-modified {{ background: #78350f; color: #fcd34d; }}
    .path code {{ color: #7dd3fc; word-break: break-all; }}
    .old, .new {{ max-width: 240px; word-break: break-word; color: #cbd5e1; }}
    .time {{ color: #94a3b8; white-space: nowrap; }}
    .endpoint {{ color: #c4b5fd; text-transform: capitalize; }}
    .empty {{ text-align: center; color: #64748b; padding: 24px; }}
    a {{ color: #38bdf8; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Hypixel SkyBlock API Tracker</h1>
    <div class="subtitle">Generated at {html.escape(generated_at)} UTC &middot; Tracks collections, skills, items &amp; bazaar</div>

    <div class="cards">{''.join(cards)}</div>

    <div class="panel">
      <h2>Recent Changes</h2>
      <table>
        <thead>
          <tr>
            <th>Type</th>
            <th>Endpoint</th>
            <th>Path</th>
            <th>Old</th>
            <th>New</th>
            <th>Time</th>
          </tr>
        </thead>
        <tbody>
          {''.join(change_rows)}
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>About</h2>
      <p style="color:#94a3b8; font-size:13px; line-height:1.7; margin:0;">
        This dashboard is built automatically by a scheduled GitHub Actions workflow.
        It polls the Hypixel SkyBlock public API endpoints and compares each response
        against the previous snapshot. Added / removed / modified fields are recorded
        in the change log above. Bazaar price fluctuations are filtered out; only
        structural product changes are tracked.
      </p>
    </div>
  </div>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> int:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    generated_at = _now_iso()
    endpoints_result: Dict[str, Dict[str, Any]] = {}
    new_changes: List[Dict[str, Any]] = []

    for name, url in ENDPOINTS.items():
        payload, error = fetch_endpoint(name, url)
        if payload is None:
            endpoints_result[name] = {
                "status": "error",
                "fetched_at": generated_at,
                "error": error,
                "summary": {},
            }
            continue

        snapshot = make_snapshot(name, payload)
        previous = load_snapshot(name)

        if previous is None:
            endpoints_result[name] = {
                "status": "ok",
                "fetched_at": snapshot["fetched_at"],
                "summary": snapshot["summary"],
                "note": "first snapshot",
            }
        else:
            diff_func = DIFF_FUNCS[name]
            try:
                changes = diff_func(previous.get("payload", {}), snapshot["payload"])
            except Exception as exc:  # noqa: BLE001
                changes = []
                print(f"[warn] diff failed for {name}: {exc}", file=sys.stderr)

            for c in changes:
                new_changes.append(
                    {
                        "endpoint": name,
                        "type": c["type"],
                        "path": c["path"],
                        "old": c["old"],
                        "new": c["new"],
                        "time": generated_at,
                    }
                )

            endpoints_result[name] = {
                "status": "ok",
                "fetched_at": snapshot["fetched_at"],
                "summary": snapshot["summary"],
                "change_count": len(changes),
            }

        save_snapshot(name, snapshot)

    # Persist change log
    all_changes = append_changes(new_changes)
    save_changes(all_changes)

    # Render site
    data_payload = {
        "generated_at": generated_at,
        "endpoints": endpoints_result,
        "changes": all_changes,
    }
    (SITE_DIR / "data.json").write_text(
        json.dumps(data_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (SITE_DIR / "index.html").write_text(
        render_html(generated_at=generated_at, endpoints=endpoints_result, changes=all_changes),
        encoding="utf-8",
    )

    print(f"[build] generated_at={generated_at}")
    for name, ep in endpoints_result.items():
        cc = ep.get("change_count", 0)
        print(f"[build] {name}: status={ep['status']} changes={cc}")
    print(f"[build] total new changes this run: {len(new_changes)}")
    print(f"[build] site written to {SITE_DIR}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
