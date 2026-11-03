#!/usr/bin/env python3
"""生成 Hypixel SkyBlock API 变更追踪的静态 GitHub Pages 仪表盘。

在定时 GitHub Actions 工作流中运行：抓取五个端点的数据（含资源包 packs），
与上一次提交的快照做比对，记录结构性变更；下载并解压资源包 zip，比对包内
文件的新增 / 修改 / 删除；最后渲染一个自包含的中文 HTML 仪表盘以及
data.json 数据文件。

关键设计：

* **版本一致性确认**：collections 与 skills 两个资源接口都带 ``version``，
  但 Hypixel 灰度更新时二者不一定同时切换。页面主版本只在两个接口版本
  一致时才确认更新；不一致时保持上一次稳定版本并标注「版本同步中」，
  避免版本号来回跳（打架）。详见 ``resolve_version_state()``。
* **元数据不参与 diff**：``version`` / ``lastUpdated`` / ``success`` 在快照与
  比对前统一剥离，版本号变化不会被当成端点内容更新。
* **时间统一北京时间**：所有写入与展示的时间均由 ``app.timeutil`` 处理。
* 快照保存在 ``data/snapshots/``，滚动变更日志 ``data/changes.json``，
  版本状态 ``data/runtime.json``，资源包清单 ``data/pack_files.json`` /
  ``data/pack_changes.json``。
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.timeutil import format_time, now_beijing  # noqa: E402

try:  # 兼容 ``python scripts/build_dashboard.py`` 与模块导入两种方式
    from pack_inspector import inspect_pack, load_pack_changes
except ImportError:  # pragma: no cover
    from scripts.pack_inspector import inspect_pack, load_pack_changes

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

DATA_DIR = BASE_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
CHANGES_FILE = DATA_DIR / "changes.json"
RUNTIME_FILE = DATA_DIR / "runtime.json"
SITE_DIR = BASE_DIR / "site"

ENDPOINTS: Dict[str, str] = {
    "collections": "https://api.hypixel.net/v2/resources/skyblock/collections",
    "skills": "https://api.hypixel.net/v2/resources/skyblock/skills",
    "items": "https://api.hypixel.net/v2/resources/skyblock/items",
    "bazaar": "https://api.hypixel.net/v2/skyblock/bazaar",
    "packs": "https://api.hypixel.net/v2/resources/packs",
}

# 只有这两个资源接口带 version 字段，且可能分批更新。
VERSION_ENDPOINTS = ("collections", "skills")
# 顶层元数据字段：只反映版本号 / 抓取时刻 / 请求状态，不属于业务内容。
# 端点 diff 必须忽略它们，否则版本号一变就会被误判为内容更新。
META_KEYS = ("version", "lastUpdated", "success")

REQUEST_TIMEOUT = 30
MAX_CHANGE_LOG = 500
MAX_LOG_VALUE_CHARS = 1500

# 仪表盘自身版本号，统一保存在仓库根目录的 VERSION 文件中（次要信息）。
VERSION_FILE = BASE_DIR / "VERSION"

# 数据与日志里保留原始英文标识，仅在展示层翻译成中文。
ENDPOINT_LABELS = {
    "collections": "收藏",
    "skills": "技能",
    "items": "物品",
    "bazaar": "集市",
    "packs": "资源包",
}
SUMMARY_LABELS = {
    "collection_count": "收藏分类数",
    "total_items": "收藏物品总数",
    "skill_count": "技能数",
    "item_count": "物品数",
    "product_count": "集市商品数",
    "pack_count": "资源包数",
    "pack_format_count": "格式版本数",
    "deploy_id": "部署 ID",
}
TYPE_LABELS = {
    "added": "新增",
    "removed": "删除",
    "modified": "修改",
    "version": "版本更新",
}
STATUS_LABELS = {"ok": "正常", "error": "异常"}
SYNC_LABELS = {
    "aligned": "版本一致",
    "syncing": "版本同步中",
    "partial": "部分接口未返回版本",
    "unknown": "尚未取得版本",
}
PACK_STATUS_LABELS = {
    "unchanged": "资源包未更新，沿用上次清单",
    "updated": "资源包已更新并完成比对",
    "baseline": "已建立资源包文件基线",
    "error": "资源包检查失败",
    "unavailable": "暂无可用资源包",
}

BAZAAR_STRUCTURAL_ONLY = True


# --------------------------------------------------------------------------- #
# 版本与文案
# --------------------------------------------------------------------------- #


def read_version() -> str:
    """读取仪表盘自身版本号。"""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def current_build() -> str:
    """返回本次构建对应的提交短哈希；Actions 环境下取 GITHUB_SHA。"""
    sha = os.getenv("GITHUB_SHA", "").strip()
    return sha[:7] if sha else "本地构建"


def load_runtime() -> Dict[str, Any]:
    if not RUNTIME_FILE.exists():
        return {}
    try:
        return json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_runtime(data: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def version_key(value: str) -> Tuple[int, ...]:
    """把 ``0.27.2.22`` 转成可比较的数值元组，用于取较高版本。"""
    parts = []
    for piece in re.split(r"[.\-+_]", value):
        parts.append(int(piece) if piece.isdigit() else 0)
    return tuple(parts)


def resolve_version_state(
    runtime: Dict[str, Any], observed: Dict[str, str], now: str
) -> Dict[str, Any]:
    """双端点版本一致性确认，避免灰度发布期间版本号来回跳。

    ``observed`` 只包含本次成功拿到 ``version`` 的端点。规则：

    1. 两个接口都返回版本且相同 → ``aligned``，该版本成为稳定版本；
    2. 两个接口都返回版本但不同 → ``syncing``，保持上一次稳定版本不变，
       页面分别列出各自版本，不确认切换、不写版本变更记录；
    3. 只拿到部分接口 → ``partial``，同样保持上次稳定版本。

    稳定版本只有从「一致」状态切换到另一个「一致」版本时才更新，
    因此分批更新过程中页面不会在两个版本号之间抖动。
    """
    previous_stable = (
        runtime.get("stable_version") or runtime.get("resource_version") or ""
    )
    prev_versions = runtime.get("versions") or {}

    versions: Dict[str, Dict[str, Any]] = {}
    for ep in VERSION_ENDPOINTS:
        prev = prev_versions.get(ep) if isinstance(prev_versions.get(ep), dict) else {}
        current = observed.get(ep)
        if current:
            first_seen = prev.get("observed_at") if prev.get("version") == current else now
            versions[ep] = {"version": current, "observed_at": first_seen or now}
        elif prev.get("version"):
            versions[ep] = {
                "version": prev["version"],
                "observed_at": prev.get("observed_at", "-"),
                "stale": True,
            }

    available = [versions[ep]["version"] for ep in VERSION_ENDPOINTS if ep in versions]
    stale = any(versions.get(ep, {}).get("stale") for ep in VERSION_ENDPOINTS)

    if len(available) == len(VERSION_ENDPOINTS) and len(set(available)) == 1 and not stale:
        status = "aligned"
        stable = available[0]
        note = "收藏与技能两个接口版本一致，已确认当前版本"
    elif len(available) < len(VERSION_ENDPOINTS) or stale:
        status = "partial"
        stable = previous_stable or (max(available, key=version_key) if available else "")
        note = "部分接口本次未返回版本号，沿用上次已确认的稳定版本"
    else:
        status = "syncing"
        stable = previous_stable or max(available, key=version_key)
        c = versions["collections"]["version"]
        s = versions["skills"]["version"]
        note = (
            f"版本同步中：收藏 v{c}、技能 v{s}。两个接口更新不同步，"
            "暂不切换当前版本，待二者一致后再确认。"
        )
        if previous_stable:
            note += f"（当前保持 v{previous_stable}）"

    confirmed = status == "aligned" and bool(stable) and stable != previous_stable
    history = list(runtime.get("history") or [])
    if confirmed:
        history.insert(
            0,
            {
                "version": stable,
                "time": now,
                "note": "两个接口版本一致，确认切换当前版本",
                "previous": previous_stable or "-",
            },
        )
        history = history[:20]

    return {
        "stable_version": stable or "未获取",
        # 兼容旧版只认 resource_version 的读取方
        "resource_version": stable or "未获取",
        "versions": versions,
        "sync_status": status,
        "note": note,
        "confirmed": confirmed,
        "previous_stable": previous_stable,
        "updated_at": now,
        "history": history,
    }


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


def strip_meta(payload: Any) -> Any:
    """剥离顶层元数据，版本号变化不再被当成端点内容更新。"""
    if not isinstance(payload, dict):
        return payload
    return {key: value for key, value in payload.items() if key not in META_KEYS}


# --------------------------------------------------------------------------- #
# 快照辅助函数
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    """兼容旧调用名：返回当前北京时间字符串。"""
    return now_beijing()


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
                "type": "added", "path": path, "old": None, "old_full": None,
                "new": _summarize(new[key]), "new_full": _log_value(new[key]),
            }
        )

    for key in sorted(old_keys - new_keys):
        path = f"{prefix}.{key}" if prefix else key
        changes.append(
            {
                "type": "removed", "path": path, "old": _summarize(old[key]),
                "old_full": _log_value(old[key]), "new": None, "new_full": None,
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
                    "type": "modified", "path": path, "old": _summarize(ov),
                    "old_full": _log_value(ov), "new": _summarize(nv),
                    "new_full": _log_value(nv),
                }
            )
    return changes


def diff_list_by_key(
    old: List[Dict[str, Any]], new: List[Dict[str, Any]],
    key_field: str, prefix: str = "",
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
# 各端点的差异比对（传入的 payload 已剥离顶层元数据）
# --------------------------------------------------------------------------- #


def diff_collections(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    return diff_dict(old.get("collections", {}), new.get("collections", {}), "collections")


def diff_skills(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    return diff_dict(old.get("skills", {}), new.get("skills", {}), "skills")


def diff_items(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    old_items, new_items = old.get("items", []), new.get("items", [])
    if not isinstance(old_items, list) or not isinstance(new_items, list):
        return diff_dict({"items": old_items}, {"items": new_items}, "root")
    return diff_list_by_key(old_items, new_items, "id", "items")


def diff_bazaar(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    """仅保留结构性变更：商品新增或移除。"""
    old_ids = set(old.get("product_ids", []))
    new_ids = set(new.get("product_ids", []))
    changes: List[Dict[str, Any]] = []
    for pid in sorted(new_ids - old_ids):
        changes.append(
            {"type": "added", "path": f"products.{pid}", "old": None, "old_full": None,
             "new": "新增集市商品", "new_full": pid}
        )
    for pid in sorted(old_ids - new_ids):
        changes.append(
            {"type": "removed", "path": f"products.{pid}", "old": "集市商品",
             "old_full": pid, "new": None, "new_full": None}
        )
    return changes


def diff_packs(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把 packs 响应摊平成路径->值的映射后比对，可精确到每个 packFormat 的哈希。"""

    def flatten(payload: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for pack in payload.get("packs") or []:
            if not isinstance(pack, dict):
                continue
            pid = str(pack.get("id", "unknown"))
            out[f"{pid}.deployId"] = pack.get("deployId")
            out[f"{pid}.lastUpdated"] = pack.get("lastUpdated")
            for version in pack.get("versions") or []:
                if not isinstance(version, dict):
                    continue
                fmt = version.get("packFormat")
                out[f"{pid}.format{fmt}.hash"] = version.get("hash")
                out[f"{pid}.format{fmt}.url"] = version.get("url")
        return out

    return diff_dict(flatten(old), flatten(new))


DIFF_FUNCS = {
    "collections": diff_collections,
    "skills": diff_skills,
    "items": diff_items,
    "bazaar": diff_bazaar,
    "packs": diff_packs,
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
            if isinstance(coll, dict) else 0,
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
    if name == "packs":
        packs = payload.get("packs") or []
        versions = [
            v for p in packs if isinstance(p, dict) for v in (p.get("versions") or [])
        ]
        first = packs[0] if packs and isinstance(packs[0], dict) else {}
        return {
            "pack_count": len(packs),
            "pack_format_count": len(versions),
            "deploy_id": first.get("deployId", "-"),
        }
    return {}


def make_snapshot(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """构造持久化到磁盘的快照字典（已剥离顶层元数据）。"""
    clean = strip_meta(payload)
    snap = {
        "endpoint": name,
        "fetched_at": now_beijing(),
        "status_code": 200,
        "checksum": checksum(clean),
        "summary": summarize_payload(name, payload),
        "payload": clean,
    }
    if name == "bazaar" and BAZAAR_STRUCTURAL_ONLY:
        # 集市只追踪结构性变化，快照里只保存排序后的商品 id 列表。
        products = payload.get("products", {})
        product_ids = sorted(products.keys()) if isinstance(products, dict) else []
        snap["payload"] = {"product_ids": product_ids}
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
        json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def append_changes(new_changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return (new_changes + load_changes())[:MAX_CHANGE_LOG]


# --------------------------------------------------------------------------- #
# HTML 渲染：代码审核（diff）风格
# --------------------------------------------------------------------------- #


def _diff_line(preview: Optional[str], full: Optional[str], css: str, sign: str) -> str:
    text = html.escape(localize_text(preview))
    details = ""
    if full and full != preview and len(full) > 80:
        details = (
            '<details class="value-details"><summary>查看完整值</summary>'
            f'<pre>{html.escape(localize_text(full))}</pre></details>'
        )
    return (
        f'<div class="diff-line {css}"><span class="sign">{sign}</span>'
        f'<div class="diff-code"><code>{text}</code>{details}</div></div>'
    )


def render_change_blocks(changes: List[Dict[str, Any]]) -> str:
    """把变更渲染成类似 git diff / code review 的区块：新增绿、删除红。"""
    if not changes:
        return (
            '<div class="empty">暂无变更记录。当某个端点返回与上次快照不同的数据时，'
            '变更会以代码审核风格显示在这里。</div>'
        )
    blocks = []
    for c in changes[:100]:
        raw_type = c.get("type", "")
        badge_css = {
            "added": "badge-add", "removed": "badge-del",
            "modified": "badge-mod", "version": "badge-ver",
        }.get(raw_type, "badge-mod")
        lines = []
        if raw_type == "added":
            lines.append(_diff_line(c.get("new"), c.get("new_full"), "diff-add", "+"))
        elif raw_type == "removed":
            lines.append(_diff_line(c.get("old"), c.get("old_full"), "diff-del", "-"))
        elif raw_type == "modified":
            lines.append(_diff_line(c.get("old"), c.get("old_full"), "diff-del", "-"))
            lines.append(_diff_line(c.get("new"), c.get("new_full"), "diff-add", "+"))
        else:
            lines.append(
                _diff_line(
                    f"{c.get('old', '-')} → {c.get('new', '-')}", None, "diff-mod", "~"
                )
            )
        blocks.append(
            f"""
            <div class="diff-block diff-block-{html.escape(raw_type)}">
              <div class="diff-header">
                <span class="diff-badge {badge_css}">{html.escape(TYPE_LABELS.get(raw_type, raw_type))}</span>
                <span class="diff-endpoint">{html.escape(ENDPOINT_LABELS.get(c.get('endpoint', ''), c.get('endpoint', '')))}</span>
                <code class="diff-path">{html.escape(c.get('path', ''))}</code>
                <span class="diff-time">{html.escape(format_time(c.get('time')))}</span>
              </div>
              <div class="diff-lines">{''.join(lines)}</div>
            </div>
            """
        )
    return "".join(blocks)


def _pack_file_details(label: str, files: List[str], css: str, sign: str) -> str:
    if not files:
        return ""
    limit = 20
    items = "".join(
        f'<div class="diff-line {css}"><span class="sign">{sign}</span>'
        f'<div class="diff-code"><code>{html.escape(str(path))}</code></div></div>'
        for path in files[:limit]
    )
    more = (
        f'<div class="pack-more">… 另有 {len(files) - limit} 个文件未展开</div>'
        if len(files) > limit else ""
    )
    return (
        f'<details class="pack-files"><summary class="cnt-{css}">'
        f'{label}文件（{len(files)}）</summary><div class="diff-lines">{items}{more}</div></details>'
    )


def render_pack_panel(pack_result: Dict[str, Any], pack_changes: List[Dict[str, Any]]) -> str:
    """渲染「资源包文件变更」面板（同样使用 diff 配色）。"""
    status = pack_result.get("status", "unavailable")
    pack = pack_result.get("pack") or {}
    total_files = pack_result.get("total_files", 0)
    status_text = PACK_STATUS_LABELS.get(status, status)

    if pack:
        meta = (
            f'资源包：{html.escape(str(pack.get("id", "-")))} &middot; '
            f'packFormat {html.escape(str(pack.get("pack_format", "-")))} &middot; '
            f'文件总数 {total_files} &middot; '
            f'部署 ID <code>{html.escape(str(pack.get("deploy_id", "-")))}</code>'
        )
    else:
        meta = "尚未获取到资源包信息"
    err = pack_result.get("error")
    err_html = (
        f'<p class="pack-error">检查异常：{html.escape(str(err))}</p>' if err else ""
    )

    if not pack_changes:
        rows = (
            '<div class="empty">暂无资源包变更记录。工作流首次运行会建立文件基线，'
            '之后资源包更新时会列出新增 / 修改 / 删除的文件。</div>'
        )
    else:
        parts = []
        for rec in pack_changes[:5]:
            added, modified, removed = (
                rec.get("added") or [], rec.get("modified") or [], rec.get("removed") or []
            )
            note = rec.get("note")
            note_html = (
                f'<div class="pack-note">{html.escape(str(note))}</div>' if note else ""
            )
            parts.append(
                f"""
                <div class="pack-change">
                  <div class="pack-change-head">
                    <span class="time">{html.escape(format_time(rec.get('time')))}</span>
                    <span>packFormat {html.escape(str(rec.get('pack_format', '-')))}</span>
                    <span class="cnt cnt-add">新增 {len(added)}</span>
                    <span class="cnt cnt-mod">修改 {len(modified)}</span>
                    <span class="cnt cnt-del">删除 {len(removed)}</span>
                    <span>共 {html.escape(str(rec.get('total_files', '-')))} 个文件</span>
                  </div>
                  {note_html}
                  {_pack_file_details('新增', added, 'diff-add', '+')}
                  {_pack_file_details('修改', modified, 'diff-mod', '~')}
                  {_pack_file_details('删除', removed, 'diff-del', '-')}
                </div>
                """
            )
        rows = "".join(parts)

    return f"""
    <div class="panel">
      <h2>资源包文件变更</h2>
      <p class="hint">工作流会下载 Hypixel 资源包 zip 并解压，按文件内容哈希比对，列出包内文件的新增 / 修改 / 删除。官方哈希未变化时自动跳过下载。</p>
      <div class="pack-meta">{status_text} &middot; {meta}</div>
      {err_html}
      {rows}
    </div>
    """


def render_version_panel(version_info: Dict[str, Any]) -> str:
    """渲染「版本记录」面板，说明双端点一致性确认规则。"""
    history = version_info.get("history") or []
    versions = version_info.get("versions") or {}
    rows = []
    for item in history[:10]:
        rows.append(
            f"""
            <tr>
              <td><code>v{html.escape(str(item.get('version', '-')))}</code></td>
              <td class="time">{html.escape(format_time(item.get('time')))}</td>
              <td>{html.escape(str(item.get('note', '-')))}</td>
              <td>{html.escape(str(item.get('previous', '-')))}</td>
            </tr>
            """
        )
    body = "".join(rows) if rows else (
        '<tr><td colspan="4" class="empty">暂无版本切换记录。'
        '首次运行会记录当前稳定版本。</td></tr>'
    )
    observed = " &middot; ".join(
        f"{ENDPOINT_LABELS.get(ep, ep)} v{html.escape(str(info.get('version', '-')))}"
        f"（首次观察 {html.escape(format_time(info.get('observed_at')))}）"
        f"{' · 本次未返回' if info.get('stale') else ''}"
        for ep, info in versions.items()
    ) or "尚未取得版本数据"
    return f"""
    <div class="panel">
      <h2>版本记录</h2>
      <p class="hint">collections 与 skills 都带 version 字段，但 Hypixel 分批更新时两者不一定同时切换。
      当前版本只在两个接口版本一致时才确认更新；不一致时保持上一次稳定版本并标注「版本同步中」，避免版本号来回跳。</p>
      <div class="pack-meta">当前观测：{observed}</div>
      <table>
        <thead><tr><th>确认版本</th><th>确认时间（北京时间）</th><th>说明</th><th>上一版本</th></tr></thead>
        <tbody>{body}</tbody>
      </table>
    </div>
    """


def render_html(
    *,
    generated_at: str,
    endpoints: Dict[str, Dict[str, Any]],
    changes: List[Dict[str, Any]],
    version_info: Dict[str, Any],
    dashboard_version: str,
    build: str,
    pack_result: Dict[str, Any],
    pack_changes: List[Dict[str, Any]],
) -> str:
    stable = version_info.get("stable_version") or "未获取"
    version_display = f"v{stable}" if stable != "未获取" else "未获取"
    sync_status = version_info.get("sync_status", "unknown")
    sync_label = SYNC_LABELS.get(sync_status, sync_status)
    versions = version_info.get("versions") or {}
    observed_text = " &middot; ".join(
        f"{ENDPOINT_LABELS.get(ep, ep)} v{html.escape(str(versions.get(ep, {}).get('version', '-')))}"
        for ep in VERSION_ENDPOINTS
    )
    note_html = (
        f'<div class="version-note">{html.escape(version_info.get("note", ""))}</div>'
        if sync_status in ("syncing", "partial") and version_info.get("note") else ""
    )

    cards = []
    for name in ENDPOINTS:
        ep = endpoints.get(name, {})
        status = ep.get("status", "error")
        status_class = "ok" if status == "ok" else "error"
        status_text = STATUS_LABELS.get(status, status)
        fetched = format_time(ep.get("fetched_at", "-"))
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
              <div class="fetched">抓取时间：{html.escape(fetched)}（北京时间）</div>
              <div class="summary">{summary_html}</div>
            </div>
            """
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
      display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
      gap: 8px; padding: 10px 16px; margin-bottom: 12px;
      background: linear-gradient(90deg, #1d4ed8, #0ea5e9);
      border: 1px solid #38bdf8; border-radius: 10px;
    }}
    .version-main {{ display: flex; align-items: center; gap: 10px; }}
    .version-tag {{ font-size: 15px; font-weight: 700; color: #f8fafc; letter-spacing: .5px; }}
    .sync-badge {{
      font-size: 11px; font-weight: 700; padding: 2px 10px; border-radius: 999px;
      background: rgba(255,255,255,.22); color: #fff;
    }}
    .sync-syncing, .sync-partial {{ background: #f59e0b; color: #1f2937; }}
    .version-meta {{ font-size: 12px; color: #e0f2fe; }}
    .version-note {{
      background: #451a03; border: 1px solid #f59e0b; color: #fde68a;
      border-radius: 8px; padding: 8px 14px; font-size: 12px; margin-bottom: 20px;
    }}
    h1 {{ margin: 0 0 4px 0; font-size: 28px; }}
    .subtitle {{ color: #94a3b8; margin-bottom: 28px; font-size: 14px; }}
    .cards {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px; margin-bottom: 32px;
    }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 18px; }}
    .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
    .endpoint-name {{ font-weight: 700; font-size: 16px; }}
    .endpoint-id {{ display: block; font-size: 11px; font-weight: 400; color: #64748b; margin-top: 2px; }}
    .badge {{ padding: 2px 10px; border-radius: 999px; font-size: 11px; font-weight: 700; }}
    .badge-ok {{ background: #065f46; color: #6ee7b7; }}
    .badge-error {{ background: #7f1d1d; color: #fca5a5; }}
    .fetched {{ font-size: 12px; color: #94a3b8; margin-bottom: 10px; }}
    .summary {{ font-size: 13px; color: #cbd5e1; line-height: 1.6; word-break: break-all; }}
    h2 {{ font-size: 18px; margin: 0 0 16px 0; }}
    .panel {{
      background: #1e293b; border: 1px solid #334155; border-radius: 12px;
      padding: 20px; margin-bottom: 24px; overflow-x: auto;
    }}
    .hint {{ color: #94a3b8; font-size: 12px; line-height: 1.7; margin: -8px 0 14px 0; }}
    .pack-meta {{ font-size: 13px; color: #cbd5e1; margin-bottom: 14px; word-break: break-all; }}
    .pack-meta code {{ color: #7dd3fc; }}
    .pack-error {{ color: #fca5a5; font-size: 12px; }}
    .pack-change {{ background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 12px; margin-bottom: 10px; }}
    .pack-change-head {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; font-size: 12px; color: #94a3b8; }}
    .cnt {{ padding: 2px 8px; border-radius: 4px; font-weight: 700; }}
    .cnt-add {{ background: #065f46; color: #6ee7b7; }}
    .cnt-mod {{ background: #78350f; color: #fcd34d; }}
    .cnt-del {{ background: #7f1d1d; color: #fca5a5; }}
    .pack-note {{ font-size: 12px; color: #64748b; margin-top: 8px; }}
    .pack-files {{ margin-top: 8px; }}
    .pack-files summary {{ cursor: pointer; font-size: 12px; user-select: none; }}
    .pack-more {{ color: #64748b; font-size: 11px; padding: 6px 12px; }}

    /* ---- 代码审核 / git diff 风格 ---- */
    .diff-block {{
      background: #0b1220; border: 1px solid #334155; border-radius: 8px;
      margin-bottom: 12px; overflow: hidden;
    }}
    .diff-header {{
      display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
      padding: 8px 12px; background: #16213a; border-bottom: 1px solid #334155;
      font-size: 12px;
    }}
    .diff-badge {{ padding: 2px 9px; border-radius: 4px; font-size: 11px; font-weight: 700; }}
    .badge-add {{ background: #064e3b; color: #6ee7b7; }}
    .badge-del {{ background: #7f1d1d; color: #fca5a5; }}
    .badge-mod {{ background: #78350f; color: #fcd34d; }}
    .badge-ver {{ background: #1e40af; color: #bfdbfe; }}
    .diff-endpoint {{ color: #c4b5fd; font-weight: 600; }}
    .diff-path {{ color: #7dd3fc; word-break: break-all; }}
    .diff-time {{ color: #94a3b8; margin-left: auto; white-space: nowrap; }}
    .diff-lines {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }}
    .diff-line {{ display: flex; align-items: flex-start; gap: 10px; padding: 6px 12px; font-size: 12px; line-height: 1.6; }}
    .diff-line .sign {{ flex: 0 0 14px; font-weight: 700; text-align: center; }}
    .diff-line code {{ white-space: pre-wrap; word-break: break-all; }}
    .diff-code {{ min-width: 0; flex: 1; }}
    .diff-add {{ background: rgba(34,197,94,.12); border-left: 3px solid #22c55e; color: #bbf7d0; }}
    .diff-add .sign {{ color: #4ade80; }}
    .diff-del {{ background: rgba(239,68,68,.12); border-left: 3px solid #ef4444; color: #fecaca; }}
    .diff-del .sign {{ color: #f87171; }}
    .diff-mod {{ background: rgba(245,158,11,.12); border-left: 3px solid #f59e0b; color: #fde68a; }}
    .diff-mod .sign {{ color: #fbbf24; }}
    .value-details {{ margin-top: 6px; font-family: inherit; }}
    .value-details summary {{ cursor: pointer; font-size: 11px; color: #38bdf8; user-select: none; }}
    .value-details summary:hover {{ text-decoration: underline; }}
    .value-details pre {{
      margin: 6px 0 0 0; padding: 8px; background: #060b16; border: 1px solid #334155;
      border-radius: 6px; font-size: 11px; line-height: 1.5; color: #e2e8f0;
      white-space: pre-wrap; word-break: break-all; max-height: 260px; overflow: auto;
    }}
    .pack-files .diff-line {{ font-size: 11px; }}
    .pack-files code {{ color: inherit; }}

    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #334155; vertical-align: top; }}
    th {{ background: #0f172a; color: #94a3b8; font-weight: 600; position: sticky; top: 0; }}
    tr:hover td {{ background: #283548; }}
    .time {{ color: #94a3b8; white-space: nowrap; }}
    .empty {{ text-align: center; color: #64748b; padding: 24px; }}
    a {{ color: #38bdf8; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="version-bar">
      <div class="version-main">
        <span class="version-tag">当前版本 {version_display}</span>
        <span class="sync-badge sync-{html.escape(sync_status)}">{html.escape(sync_label)}</span>
      </div>
      <span class="version-meta">{observed_text} &middot; 仪表盘 v{html.escape(dashboard_version)} &middot; 构建 {html.escape(build)} &middot; 生成于 {html.escape(format_time(generated_at))}（北京时间）</span>
    </div>
    {note_html}

    <h1>Hypixel SkyBlock API 变更监控台</h1>
    <div class="subtitle">监控收藏、技能、物品、集市与资源包五个端点，并解压比对资源包内文件变化。数据由 GitHub Actions 定时抓取，所有时间均为北京时间（UTC+8）。</div>

    <div class="cards">{''.join(cards)}</div>

    {render_version_panel(version_info)}

    {render_pack_panel(pack_result, pack_changes)}

    <div class="panel">
      <h2>最近变更</h2>
      <p class="hint">按代码审核（diff）风格展示：<span style="color:#6ee7b7">绿色 + 为新增</span>、<span style="color:#fca5a5">红色 - 为删除</span>、修改同时显示原值与新值。端点比对已排除 <code>version</code> / <code>lastUpdated</code> / <code>success</code>，版本号变化不会被当成内容更新。</p>
      {render_change_blocks(changes)}
    </div>

    <div class="panel">
      <h2>关于本页</h2>
      <p style="color:#94a3b8; font-size:13px; line-height:1.8; margin:0;">
        本仪表盘由 GitHub Actions 定时工作流自动生成。它定期抓取 Hypixel SkyBlock 公共 API，
        并把每次响应与上一次快照做比对，新增 / 删除 / 修改的字段会以代码审核风格记录在上方的变更日志中。
        页面顶部的「当前版本」来自 <strong>collections 与 skills 两个资源接口</strong>的 version 字段：
        由于 Hypixel 分批更新时两个接口不一定同时切换，只有两者版本一致才会确认新版本；不一致时保持上次稳定版本并显示「版本同步中」，
        避免版本号来回跳。版本切换单独记录在「版本记录」面板，不混入端点内容变更日志。
        集市（bazaar）的价格波动已被过滤，仅追踪商品的结构性增删。
        资源包（packs）端点会在官方哈希变化时下载 zip 并解压，按文件内容哈希比对包内文件的增删改。
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

    dashboard_version = read_version()
    build = current_build()
    generated_at = now_beijing()
    runtime = load_runtime()
    endpoints_result: Dict[str, Dict[str, Any]] = {}
    new_changes: List[Dict[str, Any]] = []
    observed_versions: Dict[str, str] = {}
    packs_payload: Optional[Dict[str, Any]] = None

    for name, url in ENDPOINTS.items():
        payload, error = fetch_endpoint(name, url)
        if payload is None:
            endpoints_result[name] = {
                "status": "error", "fetched_at": generated_at,
                "error": error, "summary": {},
            }
            continue

        # 版本观测：collections 与 skills 顶层带 version；items 没有，不参与。
        if name in VERSION_ENDPOINTS and isinstance(payload, dict):
            version_value = payload.get("version")
            if isinstance(version_value, str) and version_value:
                observed_versions[name] = version_value

        if name == "packs":
            packs_payload = payload

        snapshot = make_snapshot(name, payload)
        previous = load_snapshot(name)

        if previous is None:
            endpoints_result[name] = {
                "status": "ok", "fetched_at": snapshot["fetched_at"],
                "summary": snapshot["summary"], "note": "first snapshot",
            }
        else:
            diff_func = DIFF_FUNCS[name]
            try:
                # 历史快照可能仍含元数据，比对前统一剥离，保证规则一致。
                old_payload = strip_meta(previous.get("payload", {}))
                changes = diff_func(old_payload, snapshot["payload"])
            except Exception as exc:  # noqa: BLE001
                changes = []
                print(f"[警告] {name} 比对失败：{exc}", file=sys.stderr)

            for c in changes:
                new_changes.append(
                    {
                        "endpoint": name, "type": c["type"], "path": c["path"],
                        "old": c["old"], "new": c["new"],
                        "old_full": c.get("old_full"), "new_full": c.get("new_full"),
                        "time": generated_at,
                    }
                )

            endpoints_result[name] = {
                "status": "ok", "fetched_at": snapshot["fetched_at"],
                "summary": snapshot["summary"], "change_count": len(changes),
            }

        save_snapshot(name, snapshot)

    # 双端点版本一致性确认（不再因为版本号变化写入端点变更日志）
    version_info = resolve_version_state(runtime, observed_versions, generated_at)
    save_runtime(version_info)

    # 资源包下载 / 解压 / 文件变更比对（官方哈希不变时自动跳过下载）。
    pack_result: Dict[str, Any] = {
        "status": "unavailable", "pack": None, "total_files": 0,
        "changes": [], "error": "packs 端点本次未返回数据",
    }
    if packs_payload is not None:
        try:
            pack_result = inspect_pack(packs_payload, DATA_DIR)
        except Exception as exc:  # noqa: BLE001
            pack_result = {
                "status": "error", "pack": None, "total_files": 0,
                "changes": [], "error": str(exc),
            }
    pack_changes = load_pack_changes(DATA_DIR)

    # 持久化变更日志
    all_changes = append_changes(new_changes)
    save_changes(all_changes)

    # 渲染站点
    data_payload = {
        "version_info": version_info,
        "resource_version": version_info["stable_version"],
        "dashboard_version": dashboard_version,
        "build": build,
        "generated_at": generated_at,
        "endpoints": endpoints_result,
        "changes": all_changes,
        "pack": {k: v for k, v in pack_result.items() if k != "changes"},
        "pack_changes": pack_changes,
    }
    (SITE_DIR / "data.json").write_text(
        json.dumps(data_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (SITE_DIR / "index.html").write_text(
        render_html(
            generated_at=generated_at,
            endpoints=endpoints_result,
            changes=all_changes,
            version_info=version_info,
            dashboard_version=dashboard_version,
            build=build,
            pack_result=pack_result,
            pack_changes=pack_changes,
        ),
        encoding="utf-8",
    )

    print(
        f"[构建] 当前版本 v{version_info['stable_version']} "
        f"({SYNC_LABELS.get(version_info['sync_status'], version_info['sync_status'])}) "
        f"· 仪表盘 v{dashboard_version} · 构建 {build} · {generated_at}（北京时间）"
    )
    if version_info["confirmed"]:
        print(f"[构建] 版本已确认切换：{version_info.get('previous_stable') or '-'} → {version_info['stable_version']}")
    for name, ep in endpoints_result.items():
        cc = ep.get("change_count", 0)
        print(f"[构建] {ENDPOINT_LABELS.get(name, name)}：状态={STATUS_LABELS.get(ep['status'], ep['status'])}，变更 {cc} 条")
    print(f"[构建] 资源包检查：{PACK_STATUS_LABELS.get(pack_result['status'], pack_result['status'])}，文件 {pack_result.get('total_files', 0)} 个")
    print(f"[构建] 本次新增变更 {len(new_changes)} 条")
    print(f"[构建] 页面已写入 {SITE_DIR}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
