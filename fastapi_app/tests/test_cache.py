"""数据缓存与降级测试（不访问真实网络）。"""

from __future__ import annotations

import json
import os
import time

import pytest

from hypixel_tracker_api.config import Settings
from hypixel_tracker_api.data_source import DataStore, DataUnavailable, FetchError

from .conftest import sample_payload


class FailingFetcher:
    def __init__(self) -> None:
        self.urls = []

    async def get_json(self, url: str):
        self.urls.append(url)
        raise FetchError("offline")


class SequenceFetcher:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    async def get_json(self, url: str):
        self.urls.append(url)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.asyncio
async def test_stale_disk_cache_is_used_when_network_fails(tmp_path):
    settings = Settings(cache_dir=tmp_path, ttl_seconds=60)
    settings.data_cache_path.parent.mkdir(parents=True, exist_ok=True)
    settings.data_cache_path.write_text(json.dumps(sample_payload()), encoding="utf-8")
    old = time.time() - 3600
    os.utime(settings.data_cache_path, (old, old))

    store = DataStore(settings, FailingFetcher())
    payload, meta = await store.load()

    assert payload["build"] == "80ccc6a"
    assert meta["source"] == "disk-cache"
    assert meta["stale"] is True
    assert meta["age_seconds"] >= 3000


@pytest.mark.asyncio
async def test_missing_cache_raises_data_unavailable(tmp_path):
    settings = Settings(cache_dir=tmp_path, ttl_seconds=60)
    store = DataStore(settings, FailingFetcher())
    with pytest.raises(DataUnavailable):
        await store.load()


@pytest.mark.asyncio
async def test_network_payload_is_cached_and_served_offline(tmp_path):
    settings = Settings(cache_dir=tmp_path, ttl_seconds=300)
    fetcher = SequenceFetcher([sample_payload()])
    store = DataStore(settings, fetcher)
    payload, meta = await store.load()

    assert payload["resource_version"] == "0.27.2.33"
    assert meta["stale"] is False
    assert settings.data_cache_path.exists()

    offline = DataStore(settings, FailingFetcher())
    _, cached_meta = await offline.load()
    assert cached_meta["source"] == "disk-cache"
    assert cached_meta["stale"] is False


@pytest.mark.asyncio
async def test_malformed_primary_falls_back_to_mirror(tmp_path):
    settings = Settings(
        cache_dir=tmp_path,
        data_url="https://primary.example/data.json",
        fallback_url="https://mirror.example/data.json",
    )
    fetcher = SequenceFetcher([{"not": "valid"}, sample_payload()])
    store = DataStore(settings, fetcher)
    _, meta = await store.load()

    assert meta["source"] == "network"
    assert fetcher.urls == [
        "https://primary.example/data.json",
        "https://mirror.example/data.json",
    ]


@pytest.mark.asyncio
async def test_memory_payload_survives_lost_disk_cache(tmp_path):
    settings = Settings(cache_dir=tmp_path, ttl_seconds=60)
    store = DataStore(settings, SequenceFetcher([sample_payload()]))
    await store.load()
    settings.data_cache_path.unlink()

    store._fetcher = FailingFetcher()
    payload, meta = await store.load(force=True)

    assert payload["build"] == "80ccc6a"
    assert meta["source"] == "memory"
    assert meta["stale"] is True


@pytest.mark.asyncio
async def test_local_data_file_wins_over_network(tmp_path):
    data_file = tmp_path / "portable-data.json"
    data_file.write_text(json.dumps(sample_payload()), encoding="utf-8")
    settings = Settings(
        cache_dir=tmp_path / "cache",
        data_file=data_file,
        data_url="https://unreachable.example/data.json",
    )
    store = DataStore(settings, FailingFetcher())
    payload, meta = await store.load()

    assert payload["build"] == "80ccc6a"
    assert meta["source"] == "local-file"
    assert meta["stale"] is False
