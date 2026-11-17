"""运行时配置。

所有可变项都从环境变量读取一次并冻结为不可变的 :class:`Settings`，
测试可以显式构造自己的实例，避免依赖真实网络或用户目录。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

APP_NAME = "hypixel-tracker-api"
APP_VERSION = "3.0.0"
REPO = "xiaowangdeqilin/hypixel_api_tracker"
REPO_BRANCH = "main"
REPO_URL = "https://github.com/" + REPO
RELEASES_PAGE = REPO_URL + "/releases"

DEFAULT_DATA_URL = (
    "https://raw.githubusercontent.com/" + REPO + "/" + REPO_BRANCH + "/site/data.json"
)
FALLBACK_DATA_URL = (
    "https://cdn.jsdelivr.net/gh/" + REPO + "@" + REPO_BRANCH + "/site/data.json"
)
LOCALE_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/" + REPO + "/" + REPO_BRANCH + "/locales/{code}.json"
)
# raw.githubusercontent.com 在部分网络下不可达，语言包也留一条 CDN 兜底
LOCALE_FALLBACK_TEMPLATE = (
    "https://cdn.jsdelivr.net/gh/" + REPO + "@" + REPO_BRANCH + "/locales/{code}.json"
)
RELEASE_API_URL = "https://api.github.com/repos/" + REPO + "/releases/latest"

DEFAULT_TTL_SECONDS = 300
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_LOCALE_TTL_SECONDS = 86400
UPDATE_CACHE_SECONDS = 600
MAX_CRASH_FILES = 20


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_path(name: str) -> Optional[Path]:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else None


def default_cache_dir() -> Path:
    """用户缓存目录，遵循 XDG 之外的简单约定：~/.cache/hypixel-tracker-api。"""
    override = os.environ.get("HYPIXEL_CACHE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CACHE_HOME", "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".cache"
    return root / APP_NAME


@dataclass(frozen=True)
class Settings:
    """服务端配置；字段均有安全默认值，可直接实例化。"""

    data_url: str = DEFAULT_DATA_URL
    fallback_url: str = FALLBACK_DATA_URL
    locale_url_template: str = LOCALE_URL_TEMPLATE
    locale_fallback_template: str = LOCALE_FALLBACK_TEMPLATE
    release_api_url: str = RELEASE_API_URL
    releases_page: str = RELEASES_PAGE
    repo: str = REPO
    cache_dir: Path = field(default_factory=default_cache_dir)
    # 便携包用：直接读本地 data.json，不走网络（HYPIXEL_DATA_FILE）
    data_file: Optional[Path] = None
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    locale_ttl_seconds: int = DEFAULT_LOCALE_TTL_SECONDS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_crash_files: int = MAX_CRASH_FILES
    update_cache_seconds: int = UPDATE_CACHE_SECONDS

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_url=os.environ.get("HYPIXEL_DATA_URL", "").strip() or DEFAULT_DATA_URL,
            fallback_url=os.environ.get("HYPIXEL_FALLBACK_URL", "").strip()
            or FALLBACK_DATA_URL,
            locale_url_template=os.environ.get("HYPIXEL_LOCALE_URL", "").strip()
            or LOCALE_URL_TEMPLATE,
            locale_fallback_template=os.environ.get(
                "HYPIXEL_LOCALE_FALLBACK_URL", ""
            ).strip()
            or LOCALE_FALLBACK_TEMPLATE,
            cache_dir=default_cache_dir(),
            data_file=_env_path("HYPIXEL_DATA_FILE"),
            ttl_seconds=_env_int("HYPIXEL_DATA_TTL", DEFAULT_TTL_SECONDS),
            locale_ttl_seconds=_env_int(
                "HYPIXEL_LOCALE_TTL", DEFAULT_LOCALE_TTL_SECONDS
            ),
            timeout_seconds=_env_float(
                "HYPIXEL_HTTP_TIMEOUT", DEFAULT_TIMEOUT_SECONDS
            ),
        )

    @property
    def data_cache_path(self) -> Path:
        return self.cache_dir / "data.json"

    @property
    def locale_cache_dir(self) -> Path:
        return self.cache_dir / "locales"

    @property
    def crash_dir(self) -> Path:
        return self.cache_dir / "crash"

    def locale_cache_path(self, code: str) -> Path:
        return self.locale_cache_dir / (code + ".json")

    def ensure_dirs(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.locale_cache_dir.mkdir(parents=True, exist_ok=True)
        self.crash_dir.mkdir(parents=True, exist_ok=True)
