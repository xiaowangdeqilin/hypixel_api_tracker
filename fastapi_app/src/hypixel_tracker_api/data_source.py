"""数据抓取与本地缓存。

职责：
- 通过可注入的 :class:`HttpJsonClient` 拉取远端 JSON（10s 超时，失败重试一次）；
- 主源失败时自动切换 jsDelivr 镜像；
- 成功结果写入磁盘缓存，供离线 / 限流时降级；
- 网络与缓存全部不可用时抛出 :class:`DataUnavailable`，由上层转成 503。

``DataStore`` 不直接创建 httpx 客户端，客户端由应用 lifespan 统一管理并复用。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

DataPayload = Dict[str, Any]


class FetchError(RuntimeError):
    """远端请求失败（网络错误、非 2xx 或响应不是合法 JSON）。"""


class DataUnavailable(RuntimeError):
    """网络与磁盘缓存都无法提供数据。"""


class HttpJsonClient:
    """对 httpx.AsyncClient 的薄封装：统一超时、UA 与一次重试。"""

    def __init__(self, client: httpx.AsyncClient, timeout: float) -> None:
        self._client = client
        self._timeout = timeout

    async def get_json(self, url: str) -> Any:
        last_error: Optional[BaseException] = None
        for attempt in (1, 2):
            try:
                response = await self._client.get(
                    url,
                    timeout=self._timeout,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                logger.warning("fetch failed (attempt %s/2) %s: %s", attempt, url, exc)
                if attempt == 1:
                    await asyncio.sleep(0.4)
        raise FetchError(str(last_error) if last_error else "request failed")


def _atomic_write(path: Path, payload: Any) -> None:
    """先写临时文件再原子替换，避免进程中断留下半个 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _read_json(path: Path) -> Optional[Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("cannot read cache %s: %s", path, exc)
        return None


def _valid_payload(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("endpoints"), dict)
        and bool(payload.get("endpoints"))
    )


def clear_cache_files(settings: Settings) -> List[str]:
    """删除数据与语言包缓存文件，返回被删除的路径；崩溃报告不动。"""
    removed: List[str] = []
    targets = [settings.data_cache_path]
    if settings.locale_cache_dir.is_dir():
        targets.extend(sorted(settings.locale_cache_dir.glob("*.json")))
    for path in targets:
        try:
            path.unlink()
            removed.append(str(path))
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("cannot remove %s: %s", path, exc)
    return removed


class DataStore:
    """内存 + 磁盘两级缓存的数据提供者。"""

    def __init__(self, settings: Settings, fetcher: HttpJsonClient) -> None:
        self.settings = settings
        self._fetcher = fetcher
        self._payload: Optional[DataPayload] = None
        self._loaded_at: float = 0.0
        self._source = "none"
        self._stale = True
        self._lock = asyncio.Lock()

    def meta(self) -> Dict[str, Any]:
        age = int(max(0.0, time.time() - self._loaded_at)) if self._loaded_at else None
        return {
            "source": self._source,
            "stale": self._stale,
            "age_seconds": age,
            "generated_at": (self._payload or {}).get("generated_at"),
        }

    async def load(self, force: bool = False) -> Tuple[DataPayload, Dict[str, Any]]:
        """返回 (数据, 元信息)；必要时刷新缓存。"""
        async with self._lock:
            now = time.time()
            if (
                self._payload is not None
                and not force
                and now - self._loaded_at <= self.settings.ttl_seconds
            ):
                return self._payload, self.meta()
            return await self._refresh(now)

    async def _refresh(self, now: float) -> Tuple[DataPayload, Dict[str, Any]]:
        # 便携包模式：HYPIXEL_DATA_FILE 指向随包分发的 data.json，优先于网络
        local = self.settings.data_file
        if local is not None:
            payload = _read_json(local)
            if _valid_payload(payload):
                try:
                    mtime = local.stat().st_mtime
                except OSError:
                    mtime = now
                self._payload = payload
                self._loaded_at = mtime
                self._source = "local-file"
                self._stale = False
                return payload, self.meta()
            if payload is not None:
                logger.warning("ignoring malformed local data file: %s", local)
            else:
                logger.warning("local data file not readable: %s", local)

        urls: List[str] = []
        for url in (self.settings.data_url, self.settings.fallback_url):
            if url and url not in urls:
                urls.append(url)

        last_error: Optional[BaseException] = None
        for url in urls:
            try:
                payload = await self._fetcher.get_json(url)
            except FetchError as exc:
                last_error = exc
                continue
            if not _valid_payload(payload):
                last_error = FetchError("payload missing endpoints: " + url)
                logger.warning("ignoring malformed payload from %s", url)
                continue
            try:
                _atomic_write(self.settings.data_cache_path, payload)
            except OSError as exc:
                logger.warning("cannot write cache: %s", exc)
            self._payload = payload
            self._loaded_at = time.time()
            self._source = "network"
            self._stale = False
            return payload, self.meta()

        payload = _read_json(self.settings.data_cache_path)
        if _valid_payload(payload):
            try:
                mtime = self.settings.data_cache_path.stat().st_mtime
            except OSError:
                mtime = now
            age = max(0.0, now - mtime)
            self._payload = payload
            self._loaded_at = mtime
            self._source = "disk-cache"
            self._stale = age > self.settings.ttl_seconds
            logger.warning(
                "using disk cache (age %.0fs, stale=%s): %s",
                age,
                self._stale,
                last_error,
            )
            return payload, self.meta()

        if self._payload is not None:
            # 磁盘缓存损坏或不可读，但内存里还有上次成功的结果：降级返回旧数据。
            self._source = "memory"
            self._stale = True
            logger.warning("disk cache unusable, serving in-memory data: %s", last_error)
            return self._payload, self.meta()

        hint = str(last_error) if last_error else "no data source reachable"
        raise DataUnavailable(hint)

