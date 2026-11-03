#!/usr/bin/env python3
"""
生成 Hypixel SkyBlock API 变更追踪的静态 GitHub Pages 仪表盘。

本脚本在定时 GitHub Actions 工作流中运行：抓取四个端点的数据，与上一次
提交的快照做比对，记录结构性变更，并渲染一个自包含的中文 HTML 仪表盘
以及 data.json 数据文件。

快照保存在 ``data/snapshots/`` 并随仓库提交，供下一次运行做 diff；滚动
变更日志保存在 ``data/changes.json``。
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# --------------------------------------------------------------------------- #
# 配置
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
# 单条变更记录里 old/new 值最多保留的字符数。
MAX_LOG_VALUE_CHARS = 1500

# 版本号统一保存在仓库根目录的 VERSION 文件中：Pages 页面与本地 FastAPI
# 页面都从这里读取，只需要维护一处。
VERSION_FILE = BASE_DIR / "VERSION"

# 数据与日志里保留原始英文标识，仅在展示层翻译成中文。
ENDPOINT_LABELS = {
    "collections": "收藏",
    "skills": "技能",
    "items": "物品",
    "bazaar": "集市",
}
SUMMARY_LABELS = {
    "collection_count": "收藏分类数",
    "total_items": "收藏物品总数",
    "skill_count": "技能数",
    "item_count": "物品数",
    "product_count": "集市商品数",
}
TYPE_LABELS = {"added": "新增", "removed": "删除", "modified": "修改"}
STATUS_LABELS = {"ok": "正常", "error": "异常"}

# 集市价格每隔几秒就会变动；变更日志里我们只关心结构性变化（商品增删）。
# 快照里仍保留一份精简摘要，供仪表盘展示当前数量。
BAZAAR_STRUCTURAL_ONLY = True


# --------------------------------------------------------------------------- #
# 版本与文案
# --------------------------------------------------------------------------- #


def read_version() -> str:
    """读取仓库根目录 VERSION 文件中的当前版本号。"""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def current_build() -> str:
    """返回本次构建对应的提交短哈希；Actions 环境下取 GITHUB_SHA。"""
    sha = os.getenv("GITHUB_SHA", "").strip()
    return sha[:7] if sha else "本地构建"


_LEGACY_PATTERNS = [
    (re.compile(r"^\{(\d+) keys\}$"), r"{\1} 个字段"),
    (re.compile(r"^\[(\d+) items\]$"), r"[\1] 项"),
    (re.compile(r"^null$"), "空"),
    (re.compile(r"^new bazaar product$"), "新增集市商品"),
    (re.compile(r"^bazaar product$"), "集市商品"),
]


def localize_text(text: Optional[str]) -> str:
    """把历史变更日志里残留的英文摘要兼容地翻译成中文展示。"""
    if not text:
        return "-"
    for pattern, repl in _LEGACY_PATTERNS:
        if pattern.match(text):
            return pattern.sub(repl, text)
    return text


# --------------------------------------------------------------------------- #
# 快照辅助函数
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
# 差异比对引擎
# --------------------------------------------------------------------------- #


def diff_dict(old: Dict[str, Any], new: Dict[str, Any], prefix: str = "") -> List[Dict[str, Any]]:
    """返回两个字典之间的结构性变更列表。"""
    changes: List[Dict[str, Any]] = []
    old_keys = set(old.keys())
    new_keys = set(new.keys())

    for key in sorted(new_keys - old_keys):
        path = f"{prefix}.{key}" if prefix else key
        changes.append(
            {
                "type": "added",
                "path": path,
                "old": None,
                "old_full": None,
                "new": _summarize(new[key]),
                "new_full": _log_value(new[key]),
            }
        )

    for key in sorted(old_keys - new_keys):
        path = f"{prefix}.{key}" if prefix else key
        changes.append(
            {
                "type": "removed",
                "path": path,
                "old": _summarize(old[key]),
                "old_full": _log_value(old[key]),
                "new": None,
                "new_full": None,
            }
        )

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
                    "old_full": _log_value(ov),
                    "new": _summarize(nv),
                    "new_full": _log_value(nv),
                }
            )
    return changes


def diff_list_by_key(
    old: List[Dict[str, Any]],
    new: List[Dict[str, Any]],
    key_field: str,
    prefix: str = "",
) -> List[Dict[str, Any]]:
    """使用稳定键字段（如物品 id）对字典列表做比对。"""
    old_map = {str(item.get(key_field)): item for item in old if isinstance(item, dict)}
    new_map = {str(item.get(key_field)): item for item in new if isinstance(item, dict)}
    return diff_dict(old_map, new_map, prefix)


def _summarize(value: Any) -> str:
    """生成用于变更日志的紧凑、易读的值摘要。"""
    if value is None:
        return "空"
    if isinstance(value, dict):
        return f"{{{len(value)} 个字段}}"
    if isinstance(value, list):
        return f"[{len(value)} 项]"
    text = json.dumps(value, ensure_ascii=False)
    if len(text) > 120:
        text = text[:117] + "..."
    return text


def _log_value(value: Any, max_chars: int = MAX_LOG_VALUE_CHARS) -> Optional[str]:
    """生成完整（但限长）的 JSON 表示，让页面能展开查看变更后的真实值。"""
    if value is None:
        return None
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) > max_chars:
        return text[:max_chars] + f"…（已截断，共 {len(text)} 字符）"
    return text


# --------------------------------------------------------------------------- #
# 各端点的差异比对
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
    """仅保留结构性变更：商品新增或移除。

    快照里存的是 ``product_ids``（排序后的列表）而不是完整商品表，
    因此直接比对这个列表。
    """
    old_ids = set(old.get("product_ids", []))
    new_ids = set(new.get("product_ids", []))

    changes: List[Dict[str, Any]] = []
    for pid in sorted(new_ids - old_ids):
        changes.append(
            {
                "type": "added",
                "path": f"products.{pid}",
                "old": None,
                "old_full": None,
                "new": "新增集市商品",
                "new_full": pid,
            }
        )
    for pid in sorted(old_ids - new_ids):
        changes.append(
            {
                "type": "removed",
                "path": f"products.{pid}",
                "old": "集市商品",
                "old_full": pid,
                "new": None,
                "new_full": None,
            }
        )
    return changes


DIFF_FUNCS = {
    "collections": diff_collections,
    "skills": diff_skills,
    "items": diff_items,
    "bazaar": diff_bazaar,
}


# --------------------------------------------------------------------------- #
# 抓取
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
    """生成用于仪表盘卡片的简要统计。"""
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
    """构造持久化到磁盘的快照字典。"""
    snap = {
        "endpoint": name,
        "fetched_at": _now_iso(),
        "status_code": 200,
        "checksum": checksum(payload),
        "summary": summarize_payload(name, payload),
        "payload": payload,
    }
    if name == "bazaar" and BAZAAR_STRUCTURAL_ONLY:
        # 集市只追踪结构性变化（商品增删），因此快照里只保存排序后的商品 id
        # 列表，避免价格波动导致快照频繁变化。
        products = payload.get("products", {})
        product_ids = sorted(products.keys()) if isinstance(products, dict) else []
        snap["payload"] = {"success": payload.get("success"), "product_ids": product_ids}
        snap["checksum"] = checksum(product_ids)
    return snap


# --------------------------------------------------------------------------- #
# 变更日志
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
# HTML 渲染
# --------------------------------------------------------------------------- #


def _value_cell(preview: Optional[str], full: Optional[str]) -> str:
    """渲染变更值：紧凑摘要，加上可展开查看完整值的 <details> 区块。"""
    preview_text = html.escape(localize_text(preview))
    if not full:
        return f'<span class="val">{preview_text}</span>'

    full_text = html.escape(localize_text(full))
    if len(full) <= 80 and full == preview:
        return f'<span class="val">{full_text}</span>'

    return (
        f'<span class="val">{preview_text}</span>'
        f'<details class="value-details"><summary>查看完整值</summary>'
        f'<pre>{full_text}</pre></details>'
    )


def render_html(
    *,
    generated_at: str,
    endpoints: Dict[str, Dict[str, Any]],
    changes: List[Dict[str, Any]],
    version: str,
    build: str,
) -> str:
    cards = []
    for name in ENDPOINTS:
        ep = endpoints.get(name, {})
        status = ep.get("status", "error")
        status_class = "ok" if status == "ok" else "error"
        status_text = STATUS_LABELS.get(status, status)
        fetched = ep.get("fetched_at", "-")
        summary = ep.get("summary", {})
        summary_parts = [f"{SUMMARY_LABELS.get(k, k)}：{v}" for k, v in summary.items()]
        summary_html = "<br>".join(html.escape(s) for s in summary_parts) if summary_parts else "-"
        cards.append(
            f"""
            <div class="card">
              <div class="card-header">
                <span class="endpoint-name">{html.escape(ENDPOINT_LABELS.get(name, name))}<span class="endpoint-id">{html.escape(name)}</span></span>
                <span class="badge badge-{status_class}">{html.escape(status_text)}</span>
              </div>
              <div class="fetched">抓取时间：{html.escape(fetched)}</div>
              <div class="summary">{summary_html}</div>
            </div>
            """
        )

    change_rows = []
    if changes:
        for c in changes[:100]:
            raw_type = c.get("type", "")
            raw_endpoint = c.get("endpoint", "")
            change_rows.append(
                f"""
                <tr>
                  <td><span class="type type-{html.escape(raw_type)}">{html.escape(TYPE_LABELS.get(raw_type, raw_type))}</span></td>
                  <td class="endpoint">{html.escape(ENDPOINT_LABELS.get(raw_endpoint, raw_endpoint))}</td>
                  <td class="path"><code>{html.escape(c.get('path',''))}</code></td>
                  <td class="old">{_value_cell(c.get('old'), c.get('old_full'))}</td>
                  <td class="new">{_value_cell(c.get('new'), c.get('new_full'))}</td>
                  <td class="time">{html.escape(c.get('time',''))}</td>
                </tr>
                """
            )
    else:
        change_rows.append(
            '<tr><td colspan="6" class="empty">暂无变更记录。当某个端点返回与上次快照不同的数据时，变更会显示在这里。</td></tr>'
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Hypixel SkyBlock API 变更监控台</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #e2e8f0;
      margin: 0;
      padding: 24px;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    .version-bar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 10px 16px;
      margin-bottom: 20px;
      background: linear-gradient(90deg, #1d4ed8, #0ea5e9);
      border: 1px solid #38bdf8;
      border-radius: 10px;
    }}
    .version-tag {{ font-size: 15px; font-weight: 700; color: #f8fafc; letter-spacing: .5px; }}
    .version-meta {{ font-size: 12px; color: #e0f2fe; }}
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
    .endpoint-name {{ font-weight: 700; font-size: 16px; }}
    .endpoint-id {{ display: block; font-size: 11px; font-weight: 400; color: #64748b; margin-top: 2px; }}
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
    .type {{ padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }}
    .type-added {{ background: #065f46; color: #6ee7b7; }}
    .type-removed {{ background: #7f1d1d; color: #fca5a5; }}
    .type-modified {{ background: #78350f; color: #fcd34d; }}
    .path code {{ color: #7dd3fc; word-break: break-all; }}
    .old, .new {{ max-width: 240px; word-break: break-word; color: #cbd5e1; }}
    .val {{ display: block; }}
    .value-details {{ margin-top: 6px; }}
    .value-details summary {{
      cursor: pointer;
      font-size: 11px;
      color: #38bdf8;
      user-select: none;
    }}
    .value-details summary:hover {{ text-decoration: underline; }}
    .value-details pre {{
      margin: 6px 0 0 0;
      padding: 8px;
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 6px;
      font-size: 11px;
      line-height: 1.5;
      color: #e2e8f0;
      white-space: pre-wrap;
      word-break: break-all;
      max-height: 260px;
      overflow: auto;
    }}
    .time {{ color: #94a3b8; white-space: nowrap; }}
    .endpoint {{ color: #c4b5fd; }}
    .empty {{ text-align: center; color: #64748b; padding: 24px; }}
    a {{ color: #38bdf8; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="version-bar">
      <span class="version-tag">当前版本 v{html.escape(version)}</span>
      <span class="version-meta">构建 {html.escape(build)} &middot; 生成于 {html.escape(generated_at)} UTC</span>
    </div>

    <h1>Hypixel SkyBlock API 变更监控台</h1>
    <div class="subtitle">监控收藏、技能、物品与集市四个端点的结构性变更，数据由 GitHub Actions 定时抓取。</div>

    <div class="cards">{''.join(cards)}</div>

    <div class="panel">
      <h2>最近变更</h2>
      <table>
        <thead>
          <tr>
            <th>类型</th>
            <th>端点</th>
            <th>字段路径</th>
            <th>原值</th>
            <th>新值</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          {''.join(change_rows)}
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>关于本页</h2>
      <p style="color:#94a3b8; font-size:13px; line-height:1.7; margin:0;">
        本仪表盘由 GitHub Actions 定时工作流自动生成。它定期抓取 Hypixel SkyBlock 公共 API，
        并把每次响应与上一次快照做比对，新增 / 删除 / 修改的字段会记录在上方的变更日志中。
        集市（bazaar）的价格波动已被过滤，仅追踪商品的结构性增删。
      </p>
    </div>
  </div>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def main() -> int:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    version = read_version()
    build = current_build()
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
                print(f"[警告] {name} 比对失败：{exc}", file=sys.stderr)

            for c in changes:
                new_changes.append(
                    {
                        "endpoint": name,
                        "type": c["type"],
                        "path": c["path"],
                        "old": c["old"],
                        "new": c["new"],
                        "old_full": c.get("old_full"),
                        "new_full": c.get("new_full"),
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

    # 持久化变更日志
    all_changes = append_changes(new_changes)
    save_changes(all_changes)

    # 渲染站点
    data_payload = {
        "version": version,
        "build": build,
        "generated_at": generated_at,
        "endpoints": endpoints_result,
        "changes": all_changes,
    }
    (SITE_DIR / "data.json").write_text(
        json.dumps(data_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (SITE_DIR / "index.html").write_text(
        render_html(
            generated_at=generated_at,
            endpoints=endpoints_result,
            changes=all_changes,
            version=version,
            build=build,
        ),
        encoding="utf-8",
    )

    print(f"[构建] 版本 v{version} · 构建 {build} · 生成时间 {generated_at}")
    for name, ep in endpoints_result.items():
        cc = ep.get("change_count", 0)
        print(f"[构建] {ENDPOINT_LABELS.get(name, name)}：状态={STATUS_LABELS.get(ep['status'], ep['status'])}，变更 {cc} 条")
    print(f"[构建] 本次新增变更 {len(new_changes)} 条")
    print(f"[构建] 页面已写入 {SITE_DIR}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
