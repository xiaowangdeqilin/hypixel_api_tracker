"""测试夹具：全部使用注入的假数据源，绝不访问真实网络。"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient

from hypixel_tracker_api.app import create_app
from hypixel_tracker_api.config import Settings
from hypixel_tracker_api.data_source import DataUnavailable
from hypixel_tracker_api.languages import is_rtl


def sample_payload() -> Dict[str, Any]:
    endpoints = {}
    summaries = {
        "collections": {"collection_count": 6, "total_items": 90},
        "skills": {"skill_count": 12},
        "items": {"item_count": 5682},
        "bazaar": {"product_count": 2204},
        "packs": {"pack_count": 1, "selected_pack_format": 97},
        "bingo": {"bingo_name": "November 2026", "bingo_goal_count": 25},
        "firesales": {"firesale_active": 0, "firesale_total": 0},
    }
    for name, summary in summaries.items():
        endpoints[name] = {
            "status": "ok",
            "summary": summary,
            "fetched_at": "2026-11-17 20:38:45",
            "change_count": 1,
        }
    changes = [
        {
            "endpoint": "skills",
            "type": "added",
            "path": "skills.FISHING.levels",
            "time": "2026-11-17 06:10:25",
            "old": "",
            "new": "[50 项]",
        },
        {
            "endpoint": "firesales",
            "type": "removed",
            "path": "firesales.sales.0",
            "time": "2026-11-17 05:00:00",
            "old": "{\"id\": 1}",
            "new": "",
        },
        {
            "endpoint": "skills",
            "type": "modified",
            "path": "skills.MINING.levels",
            "time": "2026-11-16 23:59:59",
            "old": "[49 项]",
            "new": "[50 项]",
        },
    ]
    return {
        "bingo": {"name": "November 2026"},
        "build": "80ccc6a",
        "changes": changes,
        "dashboard_version": "3.0.0",
        "data_written": True,
        "endpoints": endpoints,
        "firesales": {"total": 0, "sales": []},
        "generated_at": "2026-11-17 20:38:45",
        "pack": {"pack": {"id": "SkyBlock"}},
        "pack_changes": [],
        "resource_version": "0.27.2.33",
        "skipped_reason": None,
        "version_info": {"confirmed": True, "history": []},
    }


class FakeStore:
    def __init__(
        self,
        payload: Optional[Dict[str, Any]] = None,
        meta: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        self.payload = payload if payload is not None else sample_payload()
        self._meta = meta or {
            "stale": False,
            "age_seconds": 12,
            "source": "network",
            "generated_at": self.payload.get("generated_at"),
        }
        self.error = error
        self.calls = 0

    async def load(self, force: bool = False):
        self.calls += 1
        if self.error:
            raise DataUnavailable(self.error)
        return self.payload, dict(self._meta)


class FakeLocales:
    def __init__(self) -> None:
        self.pack = {"端点": "Endpoints", "最近变更": "Recent changes"}

    async def get_pack(self, lang: str):
        if lang not in ("en", "zh-CN", "fr"):
            return None
        if lang == "zh-CN":
            return {"lang": "zh-CN", "dir": "ltr", "fallback": False, "pack": {}}
        return {
            "lang": lang,
            "dir": "rtl" if is_rtl(lang) else "ltr",
            "fallback": lang != "en",
            "pack": dict(self.pack),
        }


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(cache_dir=tmp_path / "cache", ttl_seconds=300)


@pytest.fixture
def client(settings):
    app = create_app(settings=settings, store=FakeStore(), locales=FakeLocales())
    with TestClient(app) as test_client:
        yield test_client
