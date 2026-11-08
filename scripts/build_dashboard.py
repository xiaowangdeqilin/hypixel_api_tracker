#!/usr/bin/env python3
"""生成 Hypixel SkyBlock API 变更追踪的静态 GitHub Pages 仪表盘。

在定时 GitHub Actions 工作流中运行：抓取六个端点的数据（含资源包 packs 与
Bingo 任务），与上一次提交的快照做比对，记录结构性变更；下载并解压资源包
zip，比对包内文件的新增 / 修改 / 删除；最后渲染一个自包含的中文 HTML
仪表盘以及 data.json 数据文件。

关键设计：

* **版本一致性确认**：collections 与 skills 两个资源接口都带 ``version``，
  但 Hypixel 灰度更新时二者不一定同时切换。页面主版本只在两个接口版本
  一致时才确认更新；不一致时保持上一次稳定版本并标注「版本同步中」，
  避免版本号来回跳（打架）。详见 ``resolve_version_state()``。
* **元数据不参与 diff**：``version`` / ``lastUpdated`` / ``success`` 在快照与
  比对前统一剥离，版本号变化不会被当成端点内容更新。
* **时间统一北京时间**：所有写入与展示的时间均由 ``app.timeutil`` 处理。
* **资源包取最大 packFormat**：``packFormat`` 会随 Minecraft / Hypixel 更新而变大，
  每次调用接口都从响应里重新取数值最大的版本，不缓存也不写死任何数字。
* **失败安全**：抓取与比对过程完全不落盘；只有「六个接口全部拿到数据」且
  「资源包检查成功」时，才在阶段 3 一次性写入 ``data/``。任一环节失败则整次
  运行不做任何变化，页面沿用上次结果并标注原因。
* **只显示变化片段**：修改类变更（端点字段与资源包文本文件）经逐行比对后
  只输出发生变化的段落，删除行标红、新增行标绿；新增项只标绿，不出现红色行。
* **Bingo 任务**：``/v2/resources/skyblock/bingo`` 的 ``start`` / ``end`` /
  ``lastUpdated`` 均为 epoch 毫秒，统一转北京时间展示；``goals`` 里的
  ``progress`` 是全服累计进度、每次抓取都在变，只用于展示，不参与变更比对，
  否则每个周期都会刷出几十条无意义变更。任务文案里的 Minecraft 颜色码
  （``§7`` 等）由 ``app.mctext`` 渲染成等价的 HTML 样式。
* 快照保存在 ``data/snapshots/``，滚动变更日志 ``data/changes.json``，
  版本状态 ``data/runtime.json``，资源包清单 ``data/pack_files.json`` /
  文本缓存 ``data/pack_texts.json`` / 变更记录 ``data/pack_changes.json``，
  Bingo 展示数据 ``data/bingo.json``。
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

from app.bingo import (  # noqa: E402
    BINGO_STATE_LABELS,
    bingo_state,
    bingo_view,
    format_amount,
    goal_progress,
    normalize_bingo,
    requirement_text,
)
from app.firesale import (  # noqa: E402
    CURRENCY_LABEL,
    build_item_names,
    diff_sales,
    firesales_view,
    normalize_sales,
    sale_key,
)
from app.mctext import render_mc_lines  # noqa: E402
from app.retention import RETENTION_DAYS, filter_recent, prune_list  # noqa: E402
from app.timeutil import (  # noqa: E402
    format_time,
    from_epoch_ms,
    now_beijing,
    to_epoch_ms,
)

try:  # 兼容 ``python scripts/build_dashboard.py`` 与模块导入两种方式
    from pack_inspector import (
        collect_pack_versions,
        commit_pack_result,
        diff_text_lines,
        inspect_pack,
        load_manifest,
        load_pack_changes,
        parse_pack_format,
        save_pack_changes,
    )
except ImportError:  # pragma: no cover
    from scripts.pack_inspector import (
        collect_pack_versions,
        commit_pack_result,
        diff_text_lines,
        inspect_pack,
        load_manifest,
        load_pack_changes,
        parse_pack_format,
        save_pack_changes,
    )

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #

DATA_DIR = BASE_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
CHANGES_FILE = DATA_DIR / "changes.json"
RUNTIME_FILE = DATA_DIR / "runtime.json"
BINGO_FILE = DATA_DIR / "bingo.json"
# 限时特卖展示数据：每次运行都会重新抓取并覆盖（开始前可能被官方调整）。
FIRESALES_FILE = DATA_DIR / "firesales.json"
SITE_DIR = BASE_DIR / "site"
# Bingo 面板的样式与交互脚本：本地页面用 <link>/<script> 引用，
# 静态页面由构建脚本原样内联，保证两端行为完全一致。
BINGO_CSS_FILE = BASE_DIR / "static" / "bingo.css"
BINGO_JS_FILE = BASE_DIR / "static" / "bingo.js"
# 主题变量与切换脚本同样两端共用
FIRESALE_CSS_FILE = BASE_DIR / "static" / "firesale.css"
FIRESALE_JS_FILE = BASE_DIR / "static" / "firesale.js"
THEME_CSS_FILE = BASE_DIR / "static" / "theme.css"
THEME_JS_FILE = BASE_DIR / "static" / "theme.js"

ENDPOINTS: Dict[str, str] = {
    "collections": "https://api.hypixel.net/v2/resources/skyblock/collections",
    "skills": "https://api.hypixel.net/v2/resources/skyblock/skills",
    "items": "https://api.hypixel.net/v2/resources/skyblock/items",
    "bazaar": "https://api.hypixel.net/v2/skyblock/bazaar",
    "packs": "https://api.hypixel.net/v2/resources/packs",
    "bingo": "https://api.hypixel.net/v2/resources/skyblock/bingo",
    "firesales": "https://api.hypixel.net/v2/skyblock/firesales",
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
    "bingo": "Bingo 任务",
    "firesales": "限时特卖",
}
SUMMARY_LABELS = {
    "collection_count": "收藏分类数",
    "total_items": "收藏物品总数",
    "skill_count": "技能数",
    "item_count": "物品数",
    "product_count": "集市商品数",
    "pack_count": "资源包数",
    "pack_format_count": "格式版本数",
    "selected_pack_format": "选取格式（最大）",
    "deploy_id": "部署 ID",
    "bingo_name": "期次",
    "bingo_goal_count": "任务数",
    "bingo_state": "状态",
    "bingo_window": "活动时间",
    "firesale_total": "特卖期数",
    "firesale_upcoming": "即将开始",
    "firesale_active": "进行中",
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
    "texts_backfilled": "已补齐文本缓存（包内容无变化）",
}
FIRESALE_STATE_LABELS = {
    "upcoming": "即将开始",
    "active": "进行中",
    "ended": "已结束",
    "unknown": "未知",
}
BINGO_STATE_LABELS = {
    "upcoming": "未开始",
    "active": "进行中",
    "ended": "已结束",
    "unknown": "未知",
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
# Bingo 任务
# --------------------------------------------------------------------------- #


def load_bingo() -> Dict[str, Any]:
    if not BINGO_FILE.exists():
        return {}
    try:
        data = json.loads(BINGO_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def load_firesales() -> Dict[str, Any]:
    if not FIRESALES_FILE.exists():
        return {}
    try:
        data = json.loads(FIRESALES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_firesales(view: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FIRESALES_FILE.write_text(
        json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def read_asset(path: Path) -> str:
    """读取需要内联到静态页面的前端资源；缺失时降级为空串而不是中断构建。"""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def save_bingo(view: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BINGO_FILE.write_text(
        json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def percent_text(value: Any) -> str:
    """把 0–100 的百分比压成 ``100`` / ``33.3``，避免卡片上出现多余小数。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{num:g}"


def render_bingo_goal(goal: Dict[str, Any], index: int) -> str:
    """渲染 5×5 方格里的单个任务格（文案里的 MC 颜色码会还原成对应颜色）。"""
    name = html.escape(str(goal.get("name") or "未命名任务"))
    gid = html.escape(str(goal.get("id") or "-"))
    tier_text = goal.get("requirement_text") or requirement_text(goal)
    tiers = goal.get("tiers")
    if isinstance(tiers, list) and tiers:
        requirement = "目标档位：" + " / ".join(format_amount(t) for t in tiers)
    elif tier_text:
        requirement = "要求：" + tier_text
    else:
        requirement = "无目标数值"

    lore_lines = goal.get("lore_lines")
    if not lore_lines:
        lore_lines = goal.get("fullLore") or ([goal.get("lore")] if goal.get("lore") else [])
    lore_html = "".join(
        f'<span class="bingo-cell-lore-line">{line}</span>'
        for line in render_mc_lines([str(x) for x in lore_lines], default_color="var(--text)")
    ) or '<span class="bingo-cell-lore-line">（该任务没有文案）</span>'

    progress = goal.get("progress_info") or goal_progress(goal)
    percent = progress.get("percent") if progress else None
    percent_attr = percent_text(percent) if progress else ""
    badge_attr = " is-done" if progress and percent >= 100 else ""

    if progress:
        badge_text = f"{percent_attr}%"
        progress_attr = (
            f"全服 {progress['progress_text']} / {progress['top_text']} · "
            f"已达成 {progress['reached']}/{progress['total']} 档"
        )
        foot_html = f"""
                <span class="bingo-bar"><span class="bingo-bar-fill" style="width:{percent_attr}%"></span></span>
                <span class="bingo-cell-progress">{html.escape(progress['progress_text'])} / {html.escape(progress['top_text'])}
                  · {progress['reached']}/{progress['total']} 档</span>"""
    else:
        badge_text = "—"
        progress_attr = ""
        foot_text = f"要求 {tier_text}" if tier_text else "无目标数值"
        foot_html = f"""
                <span class="bingo-cell-progress">{html.escape(foot_text)}</span>"""

    return f"""
            <button type="button" class="bingo-cell" data-bingo-cell
                    data-index="{index:02d}"
                    data-goal-id="{gid}"
                    data-requirement="{html.escape(requirement)}"
                    data-progress="{html.escape(progress_attr)}"
                    data-percent="{percent_attr}">
              <span class="bingo-cell-top">
                <span class="bingo-index">{index:02d}</span>
                <span class="bingo-cell-badge{badge_attr}">{badge_text}</span>
              </span>
              <span class="bingo-cell-name">{name}</span>
              <span class="bingo-cell-lore">{lore_html}</span>
              <span class="bingo-cell-foot">{foot_html}
              </span>
            </button>
    """


def render_firesale_card(sale: Dict[str, Any], index: int) -> str:
    """单期特卖卡片：一期一张，多期数据互不混排。"""
    state = str(sale.get("state") or "unknown")
    name = html.escape(str(sale.get("name") or "-"))
    item_id = html.escape(str(sale.get("item_id") or "-"))
    price = html.escape(format_amount(sale.get("price")))
    amount = html.escape(format_amount(sale.get("amount")))
    start = html.escape(str(sale.get("start") or "-"))
    end = html.escape(str(sale.get("end") or "-"))
    return f"""
        <div class="firesale-card firesale-card-{html.escape(state)}">
          <div class="firesale-card-head">
            <span class="firesale-index">{index:02d}</span>
            <span class="firesale-badge firesale-badge-{html.escape(state)}">{html.escape(FIRESALE_STATE_LABELS.get(state, state))}</span>
          </div>
          <div class="firesale-name">{name}</div>
          <div class="firesale-facts">
            <div><span class="k">价格</span><span class="v firesale-price">{price} {CURRENCY_LABEL}</span></div>
            <div><span class="k">数量</span><span class="v">{amount}</span></div>
            <div><span class="k">开始</span><span class="v">{start}</span></div>
            <div><span class="k">结束</span><span class="v">{end}</span></div>
          </div>
          <div class="firesale-id"><code>{item_id}</code></div>
        </div>
    """


def render_firesales_panel(firesales: Dict[str, Any], *, stale: bool = False) -> str:
    """「限时特卖」面板：主视图展示即将开始与进行中的特卖，
    「显示额外数据」按钮展开全部期次（含已结束）与原始数据。"""
    if not firesales or not firesales.get("sales"):
        return """
    <div class="panel">
      <h2>限时特卖（Firesales）</h2>
      <p class="hint">数据来自 <code>/v2/skyblock/firesales</code>。当前还没有特卖数据，
      运行一次构建后即可显示每期特卖的价格与数量。</p>
      <div class="empty">暂无限时特卖数据</div>
    </div>
    """

    sales = [s for s in (firesales.get("sales") or []) if isinstance(s, dict)]
    now_sales = [s for s in sales if s.get("state") in ("upcoming", "active")]
    main_cards = "".join(
        render_firesale_card(sale, i + 1) for i, sale in enumerate(now_sales)
    ) or '<div class="empty">当前没有即将开始或进行中的特卖</div>'

    rows = "".join(
        f"""
          <tr>
            <td>{i + 1}</td>
            <td>{html.escape(str(sale.get('name') or '-'))}</td>
            <td><code>{html.escape(str(sale.get('item_id') or '-'))}</code></td>
            <td>{html.escape(format_amount(sale.get('price')))}</td>
            <td>{html.escape(format_amount(sale.get('amount')))}</td>
            <td>{html.escape(str(sale.get('state_label') or '-'))}</td>
            <td class="time">{html.escape(str(sale.get('start') or '-'))}</td>
            <td class="time">{html.escape(str(sale.get('end') or '-'))}</td>
          </tr>"""
        for i, sale in enumerate(sales)
    )
    raw_json = html.escape(
        json.dumps(sales, ensure_ascii=False, indent=2)
    )

    stale_html = (
        '<div class="bingo-stale">本次运行未抓到最新数据，以下为上一次成功抓取的结果。</div>'
        if stale else ""
    )
    return f"""
    <div class="panel firesale-panel" data-firesale-panel>
      <h2>限时特卖（Firesales）<span class="panel-sub">共 {len(sales)} 期 ·
        即将开始 {firesales.get('upcoming_count', 0)} · 进行中 {firesales.get('active_count', 0)} ·
        已结束 {firesales.get('ended_count', 0)}</span>
        <button type="button" class="firesale-toggle" data-firesale-toggle
                aria-expanded="false" aria-controls="firesale-extra">显示额外数据</button></h2>
      <p class="hint">数据来自 <code>/v2/skyblock/firesales</code>，价格以宝石（Gems）结算。
        特卖<strong>开始前官方可能随时调整时间、数量与价格</strong>，因此每次运行都会重新抓取并覆盖，
        不存在「查过就跳过」。一次响应里可能同时有多期特卖，每期独立成卡，按开始时间排序；
        主视图只展示<strong>即将开始与进行中</strong>的期次。</p>
      {stale_html}
      <div class="firesale-grid">{main_cards}</div>
      <div class="firesale-extra" id="firesale-extra" data-firesale-extra hidden>
        <h3>全部期次（含已结束）</h3>
        <div class="firesale-table-wrap">
          <table>
            <thead>
              <tr><th>#</th><th>物品</th><th>item_id</th><th>价格（{CURRENCY_LABEL}）</th>
                  <th>数量</th><th>状态</th><th>开始（北京时间）</th><th>结束（北京时间）</th></tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        <p class="hint">数据抓取时间：{html.escape(str(firesales.get('generated_at') or '-'))}（北京时间）</p>
        <details>
          <summary>原始数据（JSON）</summary>
          <pre class="firesale-raw">{raw_json}</pre>
        </details>
      </div>
    </div>
    """


def render_bingo_panel(bingo: Dict[str, Any], *, stale: bool = False) -> str:
    """渲染页面最底部的「Bingo 任务」面板。

    25 个任务按接口顺序放进 5×5 方格（行优先：从左到右、从上到下）；
    倒计时不在构建时写死，而是把 ``start`` / ``end`` 的 epoch 毫秒交给浏览器
    本地逐秒计算，避免 Actions 抓取延迟让「剩余时间」越走越慢。
    """
    if not bingo or not bingo.get("id"):
        return """
    <div class="panel bingo-panel" data-bingo-panel>
      <h2>Bingo 任务</h2>
      <p class="hint">数据来自 <code>/v2/resources/skyblock/bingo</code>。当前还没有抓到 Bingo 数据，
      运行一次构建后即可显示期次时间与全部任务。</p>
      <div class="empty">暂无 Bingo 数据</div>
    </div>
    """

    state = bingo.get("state", "unknown")
    goals = [g for g in (bingo.get("goals") or []) if isinstance(g, dict)]
    goal_cells = "".join(
        render_bingo_goal(goal, i + 1) for i, goal in enumerate(goals)
    ) or '<div class="empty">该期没有任务数据</div>'

    start_ms = bingo.get("start_ms") or to_epoch_ms(bingo.get("start"))
    end_ms = bingo.get("end_ms") or to_epoch_ms(bingo.get("end"))
    start_attr = str(start_ms) if start_ms else ""
    end_attr = str(end_ms) if end_ms else ""

    stale_html = (
        '<div class="bingo-stale">本次运行未抓到最新数据，以下为上一次成功抓取的结果。</div>'
        if stale else ""
    )
    return f"""
    <div class="panel bingo-panel" data-bingo-panel>
      <h2>Bingo 任务<span class="panel-sub">第 {html.escape(str(bingo.get('id', '-')))} 期 ·
        {html.escape(str(bingo.get('name', '-')))}</span></h2>
      <p class="hint">数据来自 <code>/v2/resources/skyblock/bingo</code>。发布时间取自接口的
      <code>lastUpdated</code>，另有「首次发现」记录本仓库第一次抓到这一期的时刻；
      开始与结束时间取自 <code>start</code> / <code>end</code>。任务文案保留 Minecraft
      颜色码（<code>§</code>）并按官方配色渲染。<strong>全服进度（progress）只用于展示，
      不参与变更比对</strong>，否则每个抓取周期都会刷出大量无意义变更。</p>
      {stale_html}
      <div class="bingo-head">
        <div class="bingo-state bingo-state-{html.escape(state)}" data-bingo-state
             data-state="{html.escape(state)}" data-start-ms="{start_attr}" data-end-ms="{end_attr}">
          <span class="bingo-state-label">{html.escape(str(bingo.get('state_label', '-'))) }</span>
          <span class="bingo-countdown" data-bingo-countdown{' hidden' if state == 'ended' else ''}>{html.escape(str(bingo.get('remaining', '-'))) }</span>
        </div>
        <div class="bingo-info">
          <div><span class="k">发布时间</span><span class="v">{html.escape(format_time(bingo.get('published_at')))}</span></div>
          <div><span class="k">首次发现</span><span class="v">{html.escape(format_time(bingo.get('first_seen')))}</span></div>
          <div><span class="k">开始时间</span><span class="v">{html.escape(format_time(bingo.get('start')))}</span></div>
          <div><span class="k">结束时间</span><span class="v">{html.escape(format_time(bingo.get('end')))}</span></div>
          <div><span class="k">难度修饰</span><span class="v">{html.escape(str(bingo.get('modifier', '-')))}</span></div>
          <div><span class="k">任务数量</span><span class="v">{len(goals)} 个</span></div>
        </div>
      </div>
      <p class="bingo-tip">只有「未开始」和「进行中」显示倒计时，由浏览器按本地时间逐秒计算，到点自动切换状态，不受 Actions 抓取延迟影响；已结束的期次不再计时。点击任意格子查看完整文案与全服进度。</p>
      <div class="bingo-grid-wrap">
        <div class="bingo-grid">{goal_cells}</div>
      </div>

      <dialog class="bingo-dialog" data-bingo-dialog aria-label="Bingo 任务详情">
        <div class="bingo-dialog-inner">
          <div class="bingo-dialog-head">
            <span class="bingo-dialog-index" data-dialog-index></span>
            <span class="bingo-dialog-name" data-dialog-name></span>
            <button type="button" class="bingo-dialog-close" data-dialog-close aria-label="关闭">×</button>
          </div>
          <div class="bingo-dialog-lore" data-dialog-lore></div>
          <div class="bingo-dialog-facts">
            <div><span class="k">目标</span><span class="v" data-dialog-requirement></span></div>
            <div><span class="k">全服进度</span><span class="v" data-dialog-progress></span></div>
            <div><span class="k">完成度</span><span class="v" data-dialog-percent></span></div>
          </div>
          <div class="bingo-dialog-id">任务 ID：<code data-dialog-id></code></div>
        </div>
      </dialog>
    </div>
    """


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


def diff_bingo(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Bingo 快照已在生成阶段规范化：goals 是按任务 id 索引的字典。

    两种情况分开处理：

    * **同一期任务改动** → 字典比对，可精确到 ``goals.<id>.lore`` 这样的字段；
    * **换期**（``id`` 变化）→ 整期任务全部换掉，逐条比对会刷出几十条无意义
      的增删记录，因此合并成一条「期次更新」，必要时补上时间窗口变化。

    全服进度与数据刷新时间已在 ``normalize_bingo()`` 中剔除，不会产生噪音。
    """
    old_id, new_id = old.get("id"), new.get("id")
    if old_id is not None and new_id is not None and str(old_id) != str(new_id):
        changes: List[Dict[str, Any]] = [
            {
                "type": "modified", "path": "id",
                "old": f"第 {old_id} 期 {old.get('name', '')}".strip(),
                "old_full": _log_value(old),
                "new": f"第 {new_id} 期 {new.get('name', '')}".strip(),
                "new_full": _log_value(new),
            }
        ]
        for field in ("start", "end", "modifier"):
            if old.get(field) != new.get(field):
                changes.append(
                    {
                        "type": "modified", "path": field,
                        "old": _summarize(old.get(field)), "old_full": _log_value(old.get(field)),
                        "new": _summarize(new.get(field)), "new_full": _log_value(new.get(field)),
                    }
                )
        return changes
    return _enrich_bingo_changes(diff_dict(old, new), old, new)


def _enrich_bingo_changes(
    changes: List[Dict[str, Any]], old: Dict[str, Any], new: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """把「新增/删除整条任务」的摘要换成任务名，日志里读得懂。"""
    old_goals = old.get("goals") or {}
    new_goals = new.get("goals") or {}
    if not isinstance(old_goals, dict) or not isinstance(new_goals, dict):
        return changes
    for change in changes:
        parts = str(change.get("path", "")).split(".")
        if len(parts) != 2 or parts[0] != "goals":
            continue
        entry = new_goals.get(parts[1]) or old_goals.get(parts[1]) or {}
        name = entry.get("name") or parts[1]
        if change["type"] == "added":
            change["new"] = f"新增任务「{name}」"
        elif change["type"] == "removed":
            change["old"] = f"删除任务「{name}」"
    return changes


def diff_packs(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    """只比对每个资源包中 **packFormat 数值最大** 的那个版本。

    packFormat 会随 Minecraft / Hypixel 更新而变大，如果把格式号写进路径，
    一次格式升级就会被误报成「旧格式全部删除 + 新格式全部新增」。这里改用
    固定键，格式号变化只体现为 ``packFormat`` 字段的一条修改记录。
    """

    def flatten(payload: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for pack in payload.get("packs") or []:
            if not isinstance(pack, dict):
                continue
            pid = str(pack.get("id", "unknown"))
            out[f"{pid}.deployId"] = pack.get("deployId")
            versions = [
                v for v in (pack.get("versions") or [])
                if isinstance(v, dict) and parse_pack_format(v.get("packFormat")) is not None
            ]
            if not versions:
                continue
            best = max(versions, key=lambda v: parse_pack_format(v.get("packFormat")) or -1)
            out[f"{pid}.packFormat"] = parse_pack_format(best.get("packFormat"))
            out[f"{pid}.hash"] = best.get("hash")
            out[f"{pid}.url"] = best.get("url")
        return out

    return diff_dict(flatten(old), flatten(new))


def diff_firesales(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    """特卖快照已规范化为 {"sales": [...]}，直接按单期比对。"""
    return diff_sales(old.get("sales") or [], new.get("sales") or [])


DIFF_FUNCS = {
    "collections": diff_collections,
    "skills": diff_skills,
    "items": diff_items,
    "bazaar": diff_bazaar,
    "packs": diff_packs,
    "bingo": diff_bingo,
    "firesales": diff_firesales,
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


def _sale_state_quick(sale: Dict[str, Any], now_ms: int) -> Tuple[str, str]:
    start = sale.get("start_ms") or 0
    end = sale.get("end_ms") or 0
    if not start or not end:
        return "unknown", "-"
    if now_ms < start:
        return "upcoming", "-"
    if now_ms > end:
        return "ended", "-"
    return "active", "-"


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
        versions = collect_pack_versions(payload)
        first = packs[0] if packs and isinstance(packs[0], dict) else {}
        # 每次都从响应里重新取最大值，格式号随 MC 更新而变化，不做缓存。
        selected = max((v["pack_format"] for v in versions), default=None)
        return {
            "pack_count": len(packs),
            "pack_format_count": len(versions),
            "selected_pack_format": selected if selected is not None else "-",
            "deploy_id": first.get("deployId", "-"),
        }
    if name == "firesales":
        sales = payload.get("sales") or []
        import time as _time

        now_ms = int(_time.time() * 1000)
        upcoming = active = 0
        for sale in normalize_sales(payload):
            state, _ = _sale_state_quick(sale, now_ms)
            if state == "upcoming":
                upcoming += 1
            elif state == "active":
                active += 1
        return {
            "firesale_total": len(sales) if isinstance(sales, list) else 0,
            "firesale_upcoming": upcoming,
            "firesale_active": active,
        }
    if name == "bingo":
        goals = payload.get("goals") or []
        start = from_epoch_ms(payload.get("start"))
        end = from_epoch_ms(payload.get("end"))
        state, _ = bingo_state(start, end, now_beijing())
        return {
            "bingo_name": payload.get("name") or f"第 {payload.get('id', '-')} 期",
            "bingo_goal_count": len(goals) if isinstance(goals, list) else 0,
            "bingo_state": BINGO_STATE_LABELS.get(state, state),
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
    elif name == "bingo":
        # Bingo 用规范化结构比对：剔除全服进度与数据刷新时间，
        # 时间统一转成北京时间字符串，变更日志才读得懂。
        bingo_norm = normalize_bingo(payload)
        snap["payload"] = bingo_norm
        snap["checksum"] = checksum(bingo_norm)
    elif name == "firesales":
        # 特卖同样用规范化结构：按 item_id+start 切分成单期记录，
        # 时间转成北京时间字符串，多期数据比对时互不干扰。
        sales_norm = {"sales": normalize_sales(payload)}
        snap["payload"] = sales_norm
        snap["checksum"] = checksum(sales_norm)
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
    """合并新旧变更，并丢掉超过保留期（默认 30 天）的记录。

    变更日志同时受两个上限约束：时间窗口（30 天）与条数上限（500 条）。
    先按时间过滤再截断，避免一堆过期记录把新记录挤掉。
    """
    merged = new_changes + load_changes()
    merged = filter_recent(merged, key="time")
    return merged[:MAX_CHANGE_LOG]


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


def _pretty_json_text(value: Optional[str]) -> Optional[str]:
    """把日志里的 JSON 字符串整理成多行文本，便于逐行比对。"""
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return str(value)
    if isinstance(parsed, (dict, list)):
        return json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True)
    return str(value)


def change_value_hunks(change: Dict[str, Any]) -> List[Dict[str, Any]]:
    """字段修改只保留变化片段：原值 / 新值整理成文本后逐行比对。"""
    old_text = _pretty_json_text(change.get("old_full")) or localize_text(
        change.get("old")
    )
    new_text = _pretty_json_text(change.get("new_full")) or localize_text(
        change.get("new")
    )
    if not old_text and not new_text:
        return []
    return diff_text_lines(str(old_text or ""), str(new_text or ""))


def render_hunks(hunks: List[Dict[str, Any]], *, show_header: bool = True) -> str:
    """把逐行 diff 片段渲染成代码审核风格：删除红、新增绿，未变化的段不输出。"""
    if not hunks:
        return ""
    blocks = []
    for hunk in hunks:
        rows = []
        for line in hunk.get("lines", []):
            kind = line.get("kind", "ctx")
            css = {"del": "diff-del", "add": "diff-add", "ctx": "diff-ctx"}.get(
                kind, "diff-ctx"
            )
            sign = {"del": "-", "add": "+", "ctx": " "}.get(kind, " ")
            text = html.escape(str(line.get("text", "")))
            rows.append(
                f'<div class="diff-line {css}"><span class="sign">{sign}</span>'
                f'<div class="diff-code"><code>{text}</code></div></div>'
            )
        truncated = hunk.get("truncated")
        if truncated:
            rows.append(
                f'<div class="diff-omitted">… 本段另有 {truncated} 行未显示</div>'
            )
        header = ""
        if show_header:
            header = (
                '<div class="diff-hunk">@@ 原值第 '
                f'{html.escape(str(hunk.get("old_start", "?")))} 行 · 新值第 '
                f'{html.escape(str(hunk.get("new_start", "?")))} 行 @@</div>'
            )
        blocks.append(header + "".join(rows))
    return f'<div class="diff-hunks">{"".join(blocks)}</div>'


def render_change_blocks(changes: List[Dict[str, Any]]) -> str:
    """把变更渲染成类似 git diff / code review 的区块：新增纯绿、删除纯红。

    修改类变更只显示「发生变化的那一段」，未变化的部分完全不输出。
    """
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
            # 新增：只标绿，不显示任何红色删除行
            lines.append(_diff_line(c.get("new"), c.get("new_full"), "diff-add", "+"))
        elif raw_type == "removed":
            lines.append(_diff_line(c.get("old"), c.get("old_full"), "diff-del", "-"))
        elif raw_type == "modified":
            hunks = change_value_hunks(c)
            if hunks:
                lines.append(render_hunks(hunks))
            else:
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


PACK_FILE_LIMIT = 20


def _pack_paths_details(label: str, files: List[str], css: str, sign: str) -> str:
    """新增 / 删除文件：整段单一颜色，新增前面不出现红色行。"""
    if not files:
        return ""
    items = "".join(
        f'<div class="diff-line {css}"><span class="sign">{sign}</span>'
        f'<div class="diff-code"><code>{html.escape(str(path))}</code></div></div>'
        for path in files[:PACK_FILE_LIMIT]
    )
    more = (
        f'<div class="pack-more">… 另有 {len(files) - PACK_FILE_LIMIT} 个文件未展开</div>'
        if len(files) > PACK_FILE_LIMIT else ""
    )
    return (
        f'<details class="pack-files"><summary class="cnt-{css}">'
        f'{label}文件（{len(files)}）</summary><div class="diff-lines">{items}{more}</div></details>'
    )


def _pack_modified_details(modified: List[Any]) -> str:
    """修改文件：只显示发生变化的那一段，删除行标红、新增行标绿。"""
    if not modified:
        return ""
    parts = []
    for entry in modified[:PACK_FILE_LIMIT]:
        if isinstance(entry, str):
            # 兼容旧格式记录（只有文件路径）
            path, hunks, kind = entry, [], "text"
            old_hash = new_hash = ""
        else:
            path = entry.get("path", "-")
            hunks = entry.get("hunks") or []
            kind = entry.get("kind", "text")
            old_hash = str(entry.get("old_hash", "") or "")
            new_hash = str(entry.get("new_hash", "") or "")
        head = (
            f'<div class="pack-mod-head"><code>{html.escape(str(path))}</code></div>'
        )
        if hunks:
            body = render_hunks(hunks, show_header=False)
        elif kind == "binary":
            body = (
                '<div class="pack-binary">二进制文件已修改，仅显示哈希：'
                f'{html.escape(old_hash or "-")} → {html.escape(new_hash or "-")}</div>'
            )
        else:
            body = (
                '<div class="diff-line diff-del"><span class="sign">-</span>'
                f'<div class="diff-code"><code>哈希 {html.escape(old_hash or "-")}</code></div></div>'
                '<div class="diff-line diff-add"><span class="sign">+</span>'
                f'<div class="diff-code"><code>哈希 {html.escape(new_hash or "-")}</code></div></div>'
            )
        parts.append(f'<div class="pack-mod">{head}{body}</div>')
    if len(modified) > PACK_FILE_LIMIT:
        parts.append(
            f'<div class="pack-more">… 另有 {len(modified) - PACK_FILE_LIMIT} '
            '个修改文件未展开</div>'
        )
    return (
        f'<details class="pack-files" open><summary class="cnt-mod">'
        f'修改文件（{len(modified)}）</summary>{"".join(parts)}</details>'
    )


def _pack_candidates_text(pack_result: Dict[str, Any]) -> str:
    """说明本次从哪些格式里取了最大值。"""
    candidates = pack_result.get("candidates") or []
    pack = pack_result.get("pack") or {}
    selected = pack.get("pack_format")
    if not candidates:
        return ""
    joined = " / ".join(str(x) for x in candidates)
    return (
        f'<span class="pack-formats">本次候选 packFormat：{html.escape(joined)}'
        f' → 自动选取最大值 <strong>{html.escape(str(selected))}</strong>'
        '（数字随 MC 更新变化，每次调用接口重新取）</span>'
    )


def render_pack_panel(pack_result: Dict[str, Any], pack_changes: List[Dict[str, Any]]) -> str:
    """渲染「资源包文件变更」面板（同样使用 diff 配色）。"""
    status = pack_result.get("status", "unavailable")
    pack = pack_result.get("pack") or {}
    total_files = pack_result.get("total_files", 0)
    status_text = PACK_STATUS_LABELS.get(status, status)

    candidates_html = _pack_candidates_text(pack_result)
    if pack:
        meta = (
            f'资源包：{html.escape(str(pack.get("id", "-")))} &middot; '
            f'packFormat {html.escape(str(pack.get("pack_format", "-")))} &middot; '
            f'文件总数 {total_files} &middot; '
            f'部署 ID <code>{html.escape(str(pack.get("deploy_id", "-")))}</code>'
            + (" &middot; " + candidates_html if candidates_html else "")
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
                  {_pack_paths_details('新增', added, 'diff-add', '+')}
                  {_pack_modified_details(modified)}
                  {_pack_paths_details('删除', removed, 'diff-del', '-')}
                </div>
                """
            )
        rows = "".join(parts)

    return f"""
    <div class="panel">
      <h2>资源包文件变更</h2>
      <p class="hint">只展示最近 {RETENTION_DAYS} 天内的文件变更，更早的批次会自动清理。每次调用接口都会从响应里取 <strong>packFormat 数值最大</strong> 的资源包（该数字随 MC 更新变化，不写死）；官方哈希未变化时跳过下载。新增文件只标绿、删除文件只标红；被修改的文本文件<strong>只显示发生变化的那一段</strong>，删除行标红、新增行标绿，未变化的内容不输出。若接口或下载失败，本次运行不会改动任何数据。</p>
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
    bingo: Optional[Dict[str, Any]] = None,
    bingo_stale: bool = False,
    firesales: Optional[Dict[str, Any]] = None,
    skipped_reason: Optional[str] = None,
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
    skip_html = ""
    if skipped_reason:
        skip_html = (
            '<div class="skip-banner">本次运行未获取到完整数据，'
            '<strong>仓库数据未做任何改动</strong>，以下展示的是上一次成功抓取的结果。'
            f'<br>原因：{html.escape(skipped_reason)}</div>'
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

    # Bingo 面板样式与脚本与本地页面共用同一份文件，这里原样内联进静态页。
    bingo_css = read_asset(BINGO_CSS_FILE)
    bingo_js = read_asset(BINGO_JS_FILE)
    firesale_css = read_asset(FIRESALE_CSS_FILE)
    firesale_js = read_asset(FIRESALE_JS_FILE)
    theme_css = read_asset(THEME_CSS_FILE)
    theme_js = read_asset(THEME_JS_FILE)

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
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 24px;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    .version-bar {{
      display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
      gap: 8px; padding: 10px 16px; margin-bottom: 12px;
      background: linear-gradient(90deg, #1d4ed8, #0ea5e9);
      border: 1px solid var(--accent); border-radius: 10px;
    }}
    .version-main {{ display: flex; align-items: center; gap: 10px; }}
    .version-tag {{ font-size: 15px; font-weight: 700; color: var(--text-strong); letter-spacing: .5px; }}
    .sync-badge {{
      font-size: 11px; font-weight: 700; padding: 2px 10px; border-radius: 999px;
      background: rgba(255,255,255,.22); color: #fff;
    }}
    .sync-syncing, .sync-partial {{ background: var(--warn-line); color: #1f2937; }}
    .version-meta {{ font-size: 12px; color: rgba(255, 255, 255, .9); }}
    .version-note {{
      background: var(--warn-bg-soft); border: 1px solid var(--warn-line); color: var(--warn-text-soft);
      border-radius: 8px; padding: 8px 14px; font-size: 12px; margin-bottom: 20px;
    }}
    h1 {{ margin: 0 0 4px 0; font-size: 28px; }}
    .subtitle {{ color: var(--muted); margin-bottom: 28px; font-size: 14px; }}
    .cards {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px; margin-bottom: 32px;
    }}
    .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 18px; }}
    .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
    .endpoint-name {{ font-weight: 700; font-size: 16px; }}
    .endpoint-id {{ display: block; font-size: 11px; font-weight: 400; color: var(--muted-2); margin-top: 2px; }}
    .badge {{ padding: 2px 10px; border-radius: 999px; font-size: 11px; font-weight: 700; }}
    .badge-ok {{ background: var(--ok-bg); color: var(--ok-text); }}
    .badge-error {{ background: var(--bad-bg); color: var(--bad-text); }}
    .fetched {{ font-size: 12px; color: var(--muted); margin-bottom: 10px; }}
    .summary {{ font-size: 13px; color: var(--text-soft); line-height: 1.6; word-break: break-all; }}
    h2 {{ font-size: 18px; margin: 0 0 16px 0; }}
    .panel {{
      background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
      padding: 20px; margin-bottom: 24px; overflow-x: auto;
    }}
    .hint {{ color: var(--muted); font-size: 12px; line-height: 1.7; margin: -8px 0 14px 0; }}
    .pack-meta {{ font-size: 13px; color: var(--text-soft); margin-bottom: 14px; word-break: break-all; }}
    .pack-meta code {{ color: var(--code); }}
    .pack-error {{ color: var(--bad-text); font-size: 12px; }}
    .pack-change {{ background: var(--panel-soft); border: 1px solid var(--line); border-radius: 8px; padding: 12px; margin-bottom: 10px; }}
    .pack-change-head {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; font-size: 12px; color: var(--muted); }}
    .cnt {{ padding: 2px 8px; border-radius: 4px; font-weight: 700; }}
    .cnt-add {{ background: var(--ok-bg); color: var(--ok-text); }}
    .cnt-mod {{ background: var(--warn-bg); color: var(--warn-text); }}
    .cnt-del {{ background: var(--bad-bg); color: var(--bad-text); }}
    .pack-note {{ font-size: 12px; color: var(--muted-2); margin-top: 8px; }}
    .pack-files {{ margin-top: 8px; }}
    .pack-files summary {{ cursor: pointer; font-size: 12px; user-select: none; }}
    .pack-more {{ color: var(--muted-2); font-size: 11px; padding: 6px 12px; }}

    /* ---- 代码审核 / git diff 风格 ---- */
    .diff-block {{
      background: var(--code-bg); border: 1px solid var(--line); border-radius: 8px;
      margin-bottom: 12px; overflow: hidden;
    }}
    .diff-header {{
      display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
      padding: 8px 12px; background: var(--panel-2); border-bottom: 1px solid var(--line);
      font-size: 12px;
    }}
    .diff-badge {{ padding: 2px 9px; border-radius: 4px; font-size: 11px; font-weight: 700; }}
    .badge-add {{ background: var(--ok-bg-strong); color: var(--ok-text); }}
    .badge-del {{ background: var(--bad-bg); color: var(--bad-text); }}
    .badge-mod {{ background: var(--warn-bg); color: var(--warn-text); }}
    .badge-ver {{ background: var(--info-bg); color: var(--info-text); }}
    .diff-endpoint {{ color: var(--purple); font-weight: 600; }}
    .diff-path {{ color: var(--code); word-break: break-all; }}
    .diff-time {{ color: var(--muted); margin-left: auto; white-space: nowrap; }}
    .diff-lines {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }}
    .diff-line {{ display: flex; align-items: flex-start; gap: 10px; padding: 6px 12px; font-size: 12px; line-height: 1.6; }}
    .diff-line .sign {{ flex: 0 0 14px; font-weight: 700; text-align: center; }}
    .diff-line code {{ white-space: pre-wrap; word-break: break-all; }}
    .diff-code {{ min-width: 0; flex: 1; }}
    .diff-add {{ background: var(--diff-add-bg); border-left: 3px solid var(--ok-line); color: var(--diff-add-text); }}
    .diff-add .sign {{ color: var(--diff-add-sign); }}
    .diff-del {{ background: var(--diff-del-bg); border-left: 3px solid var(--bad-line); color: var(--diff-del-text); }}
    .diff-del .sign {{ color: var(--diff-del-sign); }}
    .diff-mod {{ background: var(--diff-mod-bg); border-left: 3px solid var(--warn-line); color: var(--warn-text-soft); }}
    .diff-mod .sign {{ color: var(--warn-text); }}
    .value-details {{ margin-top: 6px; font-family: inherit; }}
    .value-details summary {{ cursor: pointer; font-size: 11px; color: var(--accent); user-select: none; }}
    .value-details summary:hover {{ text-decoration: underline; }}
    .value-details pre {{
      margin: 6px 0 0 0; padding: 8px; background: var(--code-bg); border: 1px solid var(--line);
      border-radius: 6px; font-size: 11px; line-height: 1.5; color: var(--text);
      white-space: pre-wrap; word-break: break-all; max-height: 260px; overflow: auto;
    }}
    .pack-files .diff-line {{ font-size: 11px; }}
    .pack-files code {{ color: inherit; }}
    .pack-mod {{ border-top: 1px dashed var(--line); padding: 6px 0; }}
    .pack-mod-head {{ font-size: 11px; color: var(--warn-text); padding: 2px 12px; word-break: break-all; }}
    .pack-binary {{ color: var(--muted); font-size: 11px; padding: 4px 12px; }}
    .pack-formats {{ color: var(--code); }}

    /* ---- 只显示变化片段 ---- */
    .diff-hunks {{ border: 1px solid var(--line); border-radius: 6px; margin: 6px 0; overflow-x: auto; }}
    .diff-hunk {{ background: var(--panel-2); color: var(--muted); font-size: 11px; padding: 3px 12px; }}
    .diff-omitted {{ color: var(--muted-2); font-size: 11px; padding: 4px 12px; }}
    .diff-ctx {{ border-left: 3px solid var(--line-strong); color: var(--muted); }}
    .diff-ctx .sign {{ color: var(--line-strong); }}
    .skip-banner {{
      background: var(--warn-bg-soft); border: 1px solid var(--warn-line); color: var(--warn-text-soft);
      border-radius: 8px; padding: 10px 14px; font-size: 12px; margin-bottom: 12px;
    }}

    /* 混淆码 §k：MC 里表现为随机跳动的字符，这里用轻微抖动示意 */
    .mc-obf {{ display: inline-block; animation: mc-obf-jitter .45s steps(2) infinite; }}
    @keyframes mc-obf-jitter {{
      0% {{ opacity: .55; letter-spacing: .6px; }}
      50% {{ opacity: 1; letter-spacing: 0; }}
      100% {{ opacity: .55; letter-spacing: .6px; }}
    }}

    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
    th {{ background: var(--panel-soft); color: var(--muted); font-weight: 600; position: sticky; top: 0; }}
    tr:hover td {{ background: var(--hover); }}
    .time {{ color: var(--muted); white-space: nowrap; }}
    .empty {{ text-align: center; color: var(--muted-2); padding: 24px; }}
    a {{ color: var(--accent); }}
  </style>
  <style>{bingo_css}</style>
  <style>{firesale_css}</style>
  <style>{theme_css}</style>
  <script>{theme_js}</script>
</head>
<body>
  <div class="container">
    {skip_html}
    <div class="version-bar">
      <div class="version-main">
        <span class="version-tag">当前版本 {version_display}</span>
        <span class="sync-badge sync-{html.escape(sync_status)}">{html.escape(sync_label)}</span>
      </div>
      <span class="version-meta">{observed_text} &middot; 仪表盘 v{html.escape(dashboard_version)} &middot; 构建 {html.escape(build)} &middot; 生成于 {html.escape(format_time(generated_at))}（北京时间）</span>
    </div>
    {note_html}

    <h1>Hypixel SkyBlock API 变更监控台</h1>
    <div class="subtitle">监控收藏、技能、物品、集市、资源包、限时特卖与 Bingo 任务七个端点，并解压比对资源包内文件变化。数据由 GitHub Actions 定时抓取，所有时间均为北京时间（UTC+8）。页面最下方以 5×5 方格汇总当前 Bingo 的全部任务，倒计时由浏览器本地计算。</div>

    <div class="cards">{''.join(cards)}</div>

    {render_version_panel(version_info)}

    {render_pack_panel(pack_result, pack_changes)}

    <div class="panel">
      <h2>最近变更</h2>
      <p class="hint">按代码审核（diff）风格展示：<span style="color:var(--ok-text)">新增只标绿 +</span>、<span style="color:var(--bad-text)">删除只标红 -</span>；修改<strong>只显示发生变化的那一段</strong>（删除行标红、新增行标绿，未变化内容不输出）。端点比对已排除 <code>version</code> / <code>lastUpdated</code> / <code>success</code>，版本号变化不会被当成内容更新。<strong>只展示最近 {RETENTION_DAYS} 天的增删改，更早的记录会自动从页面和数据中清理。</strong></p>
      {render_change_blocks(changes)}
    </div>

    <div class="panel">
      <h2>关于本页</h2>
      <p style="color:var(--muted); font-size:13px; line-height:1.8; margin:0;">
        本仪表盘由 GitHub Actions 定时工作流自动生成。它定期抓取 Hypixel SkyBlock 公共 API，
        并把每次响应与上一次快照做比对，新增 / 删除 / 修改的字段会以代码审核风格记录在上方的变更日志中。
        页面顶部的「当前版本」来自 <strong>collections 与 skills 两个资源接口</strong>的 version 字段：
        由于 Hypixel 分批更新时两个接口不一定同时切换，只有两者版本一致才会确认新版本；不一致时保持上次稳定版本并显示「版本同步中」，
        避免版本号来回跳。版本切换单独记录在「版本记录」面板，不混入端点内容变更日志。
        集市（bazaar）的价格波动已被过滤，仅追踪商品的结构性增删。
        资源包（packs）端点每次都会取 packFormat 数值最大的版本（该数字随 MC 更新变化），
        在官方哈希变化时下载 zip 并解压，按文件内容哈希比对包内文件的增删改；
        被修改的文本文件只展示变化片段并标红标绿，新增文件只标绿。
        Bingo 端点（bingo）记录每期任务的发布时间、开始与结束时间，并以 5×5 方格
        列出全部任务（从左到右、从上到下，点击格子查看完整文案）；
        倒计时把 <code>start</code> / <code>end</code> 的 epoch 毫秒交给浏览器逐秒计算，
        不受 Actions 抓取延迟影响；
        任务文案里的 Minecraft 颜色码（<code>§</code>）会按官方配色还原，
        全服进度只用于展示、不参与变更比对。
        限时特卖（firesales）端点每次运行都会重新抓取（特卖开始前官方可能调整时间/数量/价格），
        每期特卖独立成卡展示价格与数量，「显示额外数据」按钮可展开全部期次与原始 JSON。
        若任一接口或资源包下载失败，本次运行不会改动仓库里的任何数据。
      </p>
    </div>

    {render_firesales_panel(firesales or {})}
    {render_bingo_panel(bingo or {}, stale=bingo_stale)}
  </div>
  <script>{bingo_js}</script>
  <script>{firesale_js}</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def _write_github_output(key: str, value: str) -> None:
    """在 Actions 里把结果传给后续步骤；本地运行时是空操作。"""
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def _skipped_view() -> Dict[str, Dict[str, Any]]:
    """跳过模式下从磁盘上的旧快照构造页面卡片（不产生任何新数据）。"""
    view: Dict[str, Dict[str, Any]] = {}
    for name in ENDPOINTS:
        snap = load_snapshot(name)
        if snap:
            view[name] = {
                "status": "ok",
                "fetched_at": snap.get("fetched_at", "-"),
                "summary": snap.get("summary", {}),
            }
        else:
            view[name] = {"status": "error", "fetched_at": "-", "summary": {}}
    return view


def _pack_result_from_disk() -> Dict[str, Any]:
    """跳过模式下从磁盘清单还原资源包状态，保证页面与仓库数据一致。"""
    manifest = load_manifest(DATA_DIR)
    files = manifest.get("files") or {}
    return {
        "status": "unchanged",
        "pack": {
            "id": manifest.get("pack_id", "-"),
            "pack_format": manifest.get("pack_format", "-"),
            "deploy_id": manifest.get("deploy_id", "-"),
        } if files else None,
        "total_files": len(files),
        "changes": [],
        "error": None,
        "candidates": [],
    }


def main() -> int:
    SITE_DIR.mkdir(parents=True, exist_ok=True)

    dashboard_version = read_version()
    build = current_build()
    generated_at = now_beijing()
    runtime = load_runtime()

    # ---- 阶段 1：只抓取，不落盘 ------------------------------------------
    payloads: Dict[str, Dict[str, Any]] = {}
    fetch_errors: Dict[str, str] = {}
    for name, url in ENDPOINTS.items():
        payload, error = fetch_endpoint(name, url)
        if payload is None:
            fetch_errors[name] = error or "未知错误"
        else:
            payloads[name] = payload

    # ---- 阶段 2：只计算，不落盘 ------------------------------------------
    endpoints_result: Dict[str, Dict[str, Any]] = {}
    new_changes: List[Dict[str, Any]] = []
    observed_versions: Dict[str, str] = {}
    pending_snapshots: Dict[str, Dict[str, Any]] = {}

    for name, payload in payloads.items():
        # 版本观测：collections 与 skills 顶层带 version；items 没有，不参与。
        if name in VERSION_ENDPOINTS and isinstance(payload, dict):
            version_value = payload.get("version")
            if isinstance(version_value, str) and version_value:
                observed_versions[name] = version_value

        snapshot = make_snapshot(name, payload)
        previous = load_snapshot(name)
        if previous is None:
            endpoints_result[name] = {
                "status": "ok", "fetched_at": snapshot["fetched_at"],
                "summary": snapshot["summary"], "note": "first snapshot",
            }
        else:
            try:
                # 历史快照可能仍含元数据，比对前统一剥离，保证规则一致。
                old_payload = strip_meta(previous.get("payload", {}))
                changes = DIFF_FUNCS[name](old_payload, snapshot["payload"])
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
        pending_snapshots[name] = snapshot

    version_info = resolve_version_state(runtime, observed_versions, generated_at)

    # Bingo 展示数据：保留原始 goals（含颜色码与全服进度）。
    # 同一期的 first_seen 沿用上次记录，避免每次运行都把首发时间刷新掉。
    bingo_data: Dict[str, Any] = load_bingo()
    if "bingo" in payloads:
        bingo_data = bingo_view(
            payloads["bingo"], generated_at=generated_at, previous=bingo_data
        )

    # 限时特卖展示数据：每次成功抓取都会整体重建（开始前可能被官方调整，
    # 不做「内容没变就跳过」的省略），物品名称从 items 端点解析。
    firesales_data: Dict[str, Any] = {}
    if "firesales" in payloads:
        items_payload = payloads.get("items")
        if items_payload is None:
            items_snap = load_snapshot("items")
            items_payload = (items_snap or {}).get("payload")
        firesales_data = firesales_view(
            payloads["firesales"],
            generated_at=generated_at,
            item_names=build_item_names(items_payload),
        )

    # 资源包检查：只在 packs 接口成功时运行；检查器本身不写盘。
    pack_result: Dict[str, Any] = {
        "status": "unavailable", "pack": None, "total_files": 0,
        "changes": [], "error": None, "candidates": [],
    }
    if "packs" in payloads:
        try:
            pack_result = inspect_pack(payloads["packs"], DATA_DIR)
        except Exception as exc:  # noqa: BLE001
            pack_result = {
                "status": "error", "pack": None, "total_files": 0,
                "changes": [], "error": str(exc), "candidates": [],
            }

    # ---- 阶段 3：判定是否允许落盘（失败即整次不做任何变化）----------------
    reasons: List[str] = []
    if fetch_errors:
        failed = "、".join(ENDPOINT_LABELS.get(k, k) for k in fetch_errors)
        reasons.append(f"{failed} 接口未获取到内容")
    if pack_result.get("status") in ("error", "unavailable"):
        detail = pack_result.get("error") or "无法取得可用的资源包下载地址"
        reasons.append(f"资源包检查未完成（{detail}）")
    skipped_reason = "；".join(reasons) if reasons else None

    if skipped_reason is None:
        # 数据齐备，一次性事务式落盘。
        for name, snapshot in pending_snapshots.items():
            save_snapshot(name, snapshot)
        all_changes = append_changes(new_changes)
        save_changes(all_changes)
        save_runtime(version_info)
        commit_pack_result(pack_result, DATA_DIR)
        if bingo_data:
            save_bingo(bingo_data)
        if firesales_data:
            save_firesales(firesales_data)
        pack_changes = load_pack_changes(DATA_DIR)
        pack_changes, expired_pack_records = prune_list(pack_changes, key="time")
        if expired_pack_records:
            # 资源包没更新时 commit_pack_result 不会写盘，这里单独清理过期记录。
            save_pack_changes(DATA_DIR, pack_changes)
        data_written = True
        bingo_stale = False
    else:
        # 任何失败都不改动 data/ 下的一个字节，页面沿用上次结果。
        data_written = False
        # 页面只展示保留期内的记录，旧数据即使还在磁盘上也不再显示。
        all_changes = filter_recent(load_changes(), key="time")
        saved_runtime = load_runtime()
        if saved_runtime:
            version_info = saved_runtime
        pack_changes = filter_recent(load_pack_changes(DATA_DIR), key="time")
        pack_result = _pack_result_from_disk()
        endpoints_result = _skipped_view()
        # Bingo 同样回退到磁盘上的上一次结果，并标注为非最新数据。
        bingo_data = load_bingo()
        bingo_stale = bool(bingo_data)
        firesales_data = load_firesales()

    # ---- 阶段 4：生成站点产物（artifact，不属于仓库持久化数据）------------
    data_payload = {
        "version_info": version_info,
        "resource_version": version_info.get("stable_version", "未获取"),
        "dashboard_version": dashboard_version,
        "build": build,
        "generated_at": generated_at,
        "endpoints": endpoints_result,
        "changes": all_changes,
        "pack": {k: v for k, v in pack_result.items() if k != "changes"},
        "pack_changes": pack_changes,
        "bingo": bingo_data,
        "firesales": firesales_data,
        "data_written": data_written,
        "skipped_reason": skipped_reason,
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
            bingo=bingo_data,
            bingo_stale=bingo_stale,
            firesales=firesales_data,
            skipped_reason=skipped_reason,
        ),
        encoding="utf-8",
    )
    _write_github_output("data_written", "true" if data_written else "false")

    print(
        f"[构建] 当前版本 v{version_info.get('stable_version', '未获取')} "
        f"({SYNC_LABELS.get(version_info.get('sync_status', 'unknown'), '未知')}) "
        f"· 仪表盘 v{dashboard_version} · 构建 {build} · {generated_at}（北京时间）"
    )
    if data_written:
        if version_info.get("confirmed"):
            print(
                f"[构建] 版本已确认切换：{version_info.get('previous_stable') or '-'} "
                f"→ {version_info['stable_version']}"
            )
        for name, ep in endpoints_result.items():
            cc = ep.get("change_count", 0)
            print(
                f"[构建] {ENDPOINT_LABELS.get(name, name)}："
                f"状态={STATUS_LABELS.get(ep['status'], ep['status'])}，变更 {cc} 条"
            )
        print(
            f"[构建] 资源包检查："
            f"{PACK_STATUS_LABELS.get(pack_result['status'], pack_result['status'])}，"
            f"文件 {pack_result.get('total_files', 0)} 个"
        )
        if bingo_data:
            print(
                f"[构建] Bingo：第 {bingo_data.get('id', '-')} 期 "
                f"{bingo_data.get('name', '-')}（{bingo_data.get('state_label', '-')}），"
                f"{len(bingo_data.get('goals') or [])} 个任务，"
                f"{bingo_data.get('start', '-')} → {bingo_data.get('end', '-')}（北京时间）"
            )
        if firesales_data:
            print(
                f"[构建] 限时特卖：共 {firesales_data.get('total', 0)} 期"
                f"（即将开始 {firesales_data.get('upcoming_count', 0)} ·"
                f" 进行中 {firesales_data.get('active_count', 0)}），已刷新数据"
            )
        print(f"[构建] 本次新增变更 {len(new_changes)} 条")
    else:
        print(f"[跳过] {skipped_reason}；本次未改动任何仓库数据")
    print(f"[构建] 页面已写入 {SITE_DIR}，data_written={str(data_written).lower()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
