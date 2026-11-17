"""语言包加载。

职责：
- 校验语言代码（仅接受 :mod:`languages` 中登记或别名归一后的代码）；
- 从 GitHub 拉取 ``locales/<code>.json``，并缓存到磁盘（默认 1 天）；
- 网络失败时依次退到磁盘缓存、包内 ``en.json`` / ``zh-CN.json``；
- ``zh-CN`` 是原文语言，直接返回空包，调用方按原文显示。

语言包的键是简体中文原文，值是对应译文；缺键时前端回退原文。
"""

from __future__ import annotations

import json
import logging
import time
from importlib import resources
from typing import Any, Dict, Optional, Tuple

from .config import Settings
from .data_source import FetchError, HttpJsonClient, _atomic_write, _read_json
from .languages import DEFAULT_LANGUAGE, is_rtl, normalize

logger = logging.getLogger(__name__)

Pack = Dict[str, str]


def _bundled_pack(name: str) -> Pack:
    """读取包内兜底语言包；缺失时返回空字典。"""
    try:
        resource = resources.files(__package__).joinpath("locales/" + name + ".json")
        with resource.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning("bundled locale %s unavailable: %s", name, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


class LocaleStore:
    def __init__(self, settings: Settings, fetcher: HttpJsonClient) -> None:
        self.settings = settings
        self._fetcher = fetcher
        self._memory: Dict[str, Tuple[Pack, float, bool]] = {}
        self._bundled_en = _bundled_pack("en")
        self._bundled_zh = _bundled_pack(DEFAULT_LANGUAGE)

    def _bundled(self, code: str) -> Pack:
        if code == DEFAULT_LANGUAGE:
            return dict(self._bundled_zh)
        if code == "en":
            return dict(self._bundled_en)
        return {}

    async def get_pack(self, lang: str) -> Optional[Dict[str, Any]]:
        """返回 ``{lang, dir, fallback, pack}``；未知语言返回 ``None``。"""
        code = normalize(lang)
        if code is None:
            return None

        if code == DEFAULT_LANGUAGE:
            return {"lang": code, "dir": "ltr", "fallback": False, "pack": {}}

        cached = self._memory.get(code)
        if cached:
            # 回退英文时只缓存 60 秒，网络恢复后能尽快拿到真正的译文。
            ttl = 60 if cached[2] else self.settings.locale_ttl_seconds
            if time.time() - cached[1] <= ttl:
                pack, _, fallback = cached
                return self._payload(code, pack, fallback)

        pack, fallback = await self._load(code)
        self._memory[code] = (pack, time.time(), fallback)
        return self._payload(code, pack, fallback)

    def _payload(self, code: str, pack: Pack, fallback: bool) -> Dict[str, Any]:
        return {
            "lang": code,
            "dir": "rtl" if is_rtl(code) else "ltr",
            "fallback": fallback,
            "pack": pack,
        }

    async def _load(self, code: str) -> Tuple[Pack, bool]:
        cached_pack = _read_json(self.settings.locale_cache_path(code))
        if isinstance(cached_pack, dict):
            try:
                fresh = (
                    time.time() - self.settings.locale_cache_path(code).stat().st_mtime
                    <= self.settings.locale_ttl_seconds
                )
            except OSError:
                fresh = False
            if fresh:
                return self._merge(code, cached_pack), False

        for template in self._url_templates():
            url = template.format(code=code)
            try:
                remote = await self._fetcher.get_json(url)
            except FetchError as exc:
                logger.warning("locale %s fetch failed: %s", code, exc)
                continue
            if isinstance(remote, dict):
                try:
                    _atomic_write(self.settings.locale_cache_path(code), remote)
                except OSError as exc:
                    logger.warning("cannot cache locale %s: %s", code, exc)
                return self._merge(code, remote), False
            logger.warning("locale %s is not an object, fallback to built-in", code)

        if isinstance(cached_pack, dict):
            return self._merge(code, cached_pack), False

        return self._fallback_pack(code), True

    def _url_templates(self) -> Tuple[str, ...]:
        """先走主源，再走 CDN 兜底；两者相同时只留一个。"""
        primary = self.settings.locale_url_template
        fallback = self.settings.locale_fallback_template
        if not fallback or fallback == primary:
            return (primary,)
        return (primary, fallback)

    def _merge(self, code: str, pack: Dict[str, Any]) -> Pack:
        """英文内置包作基线，再叠加本语言内置条目与远端译文。

        远端语言包可能是旧版本（新词条还没发布），这时缺的键会退回英文，
        而不是把简体中文原文露给非中文用户。
        """
        merged: Pack = dict(self._bundled_en)
        merged.update(self._bundled(code))
        for key, value in pack.items():
            if isinstance(value, str):
                merged[str(key)] = value
        return merged

    def _fallback_pack(self, code: str) -> Pack:
        if code == "en":
            return dict(self._bundled_en)
        # 其他语言缺包时退回英文，保证页面至少可读。
        return dict(self._bundled_en)
