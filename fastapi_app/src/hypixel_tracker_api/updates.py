"""版本检查。

职责：
- 解析 ``v3.0.0`` / ``3.1.0-beta.1`` 形式的标签并做数值比较；
- 查询 GitHub ``releases/latest``，结果在内存里缓存 10 分钟；
- 只报告「有更新」，不下载、不替换已安装文件（pip 管理的文件由用户升级）。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from .config import APP_VERSION
from .data_source import FetchError

logger = logging.getLogger(__name__)

VERSION_RE = re.compile(r"^v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})(?:[-+]([0-9A-Za-z.\-]+))?$")

VersionTuple = Tuple[int, int, int]


def parse_version(value: str) -> Optional[VersionTuple]:
    """解析语义化版本，忽略预发布后缀；非法输入返回 ``None``。"""
    if not isinstance(value, str):
        return None
    match = VERSION_RE.match(value.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_newer(candidate: str, current: str) -> bool:
    """``candidate`` 是否严格新于 ``current``；任一方非法则视为没有更新。"""
    left = parse_version(candidate)
    right = parse_version(current)
    if left is None or right is None:
        return False
    return left > right


def normalize_tag(tag: str) -> str:
    """把 tag 归一成 ``3.0.0``；无法解析时原样返回（去掉前缀 v）。"""
    parsed = parse_version(tag)
    if parsed is None:
        return (tag or "").strip().lstrip("vV")
    return "%d.%d.%d" % parsed


class UpdateChecker:
    """带 TTL 的 Release 查询器；``fetch_json`` 由调用方注入以便测试。"""

    def __init__(
        self,
        release_api_url: str,
        releases_page: str,
        ttl_seconds: int = 600,
        current_version: str = APP_VERSION,
    ) -> None:
        self.release_api_url = release_api_url
        self.releases_page = releases_page
        self.ttl_seconds = ttl_seconds
        self.current_version = current_version
        self._cached: Optional[Dict[str, Any]] = None
        self._cached_at = 0.0

    async def check(self, fetch_json: Callable[[str], Awaitable[Any]]) -> Dict[str, Any]:
        now = time.time()
        if self._cached is not None and now - self._cached_at <= self.ttl_seconds:
            return dict(self._cached, cached=True)

        result: Dict[str, Any] = {
            "current": self.current_version,
            "latest": None,
            "available": False,
            "page": self.releases_page,
        }
        try:
            release = await fetch_json(self.release_api_url)
        except FetchError as exc:
            logger.warning("release check failed: %s", exc)
            result["error"] = "github release lookup failed"
        else:
            tag = release.get("tag_name") if isinstance(release, dict) else None
            if isinstance(tag, str) and parse_version(tag) is not None:
                result["latest"] = normalize_tag(tag)
                result["available"] = is_newer(tag, self.current_version)
                page = release.get("html_url")
                if isinstance(page, str) and page.startswith("https://"):
                    result["page"] = page
            else:
                result["error"] = "release tag missing or invalid"

        self._cached = result
        self._cached_at = now
        return dict(result, cached=False)


def upgrade_command() -> str:
    return "python -m pip install --upgrade hypixel-tracker-api"
