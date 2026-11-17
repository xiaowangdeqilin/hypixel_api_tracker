"""版本解析与 Release 检查测试。"""

from __future__ import annotations

import pytest

from hypixel_tracker_api.data_source import FetchError
from hypixel_tracker_api.updates import UpdateChecker, is_newer, normalize_tag, parse_version


def test_parse_and_compare_versions():
    assert parse_version("v3.0.0") == (3, 0, 0)
    assert parse_version("3.10.2") == (3, 10, 2)
    assert parse_version("v3.1.0-beta.1") == (3, 1, 0)
    assert parse_version("nightly") is None
    assert is_newer("v3.1.0", "3.0.0") is True
    assert is_newer("v3.0.0", "3.0.0") is False
    assert is_newer("v2.9.9", "3.0.0") is False
    assert is_newer("garbage", "3.0.0") is False
    assert normalize_tag("v3.2.1") == "3.2.1"
    assert normalize_tag("nightly") == "nightly"


class FakeFetch:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def __call__(self, url: str):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_update_checker_reports_newer_release_and_caches():
    checker = UpdateChecker("https://api.example/latest", "https://example/releases", ttl_seconds=600)
    fetch = FakeFetch({"tag_name": "v3.2.0", "html_url": "https://example/releases/v3.2.0"})
    result = await checker.check(fetch)

    assert result["current"] == "3.0.0"
    assert result["latest"] == "3.2.0"
    assert result["available"] is True
    assert result["page"] == "https://example/releases/v3.2.0"
    assert result["cached"] is False

    again = await checker.check(fetch)
    assert again["cached"] is True
    assert fetch.calls == 1


@pytest.mark.asyncio
async def test_update_checker_handles_offline_and_same_version():
    checker = UpdateChecker("https://api.example/latest", "https://example/releases")
    offline = await checker.check(FakeFetch(FetchError("no network")))
    assert offline["available"] is False
    assert offline["latest"] is None
    assert offline["error"]

    checker = UpdateChecker("https://api.example/latest", "https://example/releases")
    same = await checker.check(FakeFetch({"tag_name": "v3.0.0"}))
    assert same["available"] is False
    assert same["latest"] == "3.0.0"
