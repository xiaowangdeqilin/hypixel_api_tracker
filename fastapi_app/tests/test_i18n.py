"""语言包加载与回退测试。"""

from __future__ import annotations

import pytest

from hypixel_tracker_api.config import Settings
from hypixel_tracker_api.data_source import FetchError
from hypixel_tracker_api.i18n import LocaleStore


class FailingFetcher:
    async def get_json(self, url: str):
        raise FetchError("offline")


@pytest.mark.asyncio
async def test_zh_cn_is_identity_pack(tmp_path):
    store = LocaleStore(Settings(cache_dir=tmp_path), FailingFetcher())
    result = await store.get_pack("zh_CN")
    assert result == {"lang": "zh-CN", "dir": "ltr", "fallback": False, "pack": {}}


@pytest.mark.asyncio
async def test_unknown_language_returns_none(tmp_path):
    store = LocaleStore(Settings(cache_dir=tmp_path), FailingFetcher())
    assert await store.get_pack("klingon") is None


@pytest.mark.asyncio
async def test_locale_falls_back_to_bundled_english(tmp_path):
    store = LocaleStore(Settings(cache_dir=tmp_path), FailingFetcher())
    result = await store.get_pack("fr")

    assert result is not None
    assert result["fallback"] is True
    assert result["lang"] == "fr"
    assert result["dir"] == "ltr"
    assert result["pack"]["端点"] == "Endpoint"


@pytest.mark.asyncio
async def test_rtl_language_flag(tmp_path):
    store = LocaleStore(Settings(cache_dir=tmp_path), FailingFetcher())
    result = await store.get_pack("ar")
    assert result["dir"] == "rtl"
    assert result["pack"]["端点"] == "Endpoint"


def test_template_keys_all_have_english_entries():
    """模板里每个 t("...") 词条都必须能在内置英文包里查到。

    防止改了界面文案却忘了补词条，最终在非中文界面露出中文原文。
    """
    import json
    import re
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "src" / "hypixel_tracker_api"
    html = (package / "templates" / "index.html").read_text(encoding="utf-8")
    keys = set(re.findall(r't\(\s*"([^"]+)"', html))
    assert keys, "模板里没有找到任何 t(...) 词条"
    english = json.loads((package / "locales" / "en.json").read_text(encoding="utf-8"))
    missing = sorted(key for key in keys if key not in english)
    assert not missing, f"面板文案缺少英文词条: {missing}"


@pytest.mark.asyncio
async def test_missing_key_falls_back_to_english_not_chinese(tmp_path):
    """远端语言包比代码旧时（新词条还没发布），缺的键应退回英文而不是中文原文。"""
    store = LocaleStore(Settings(cache_dir=tmp_path), FailingFetcher())
    result = await store.get_pack("de")

    assert result is not None
    pack = result["pack"]
    assert pack["端点"] == "Endpoint"
    assert pack["时区"] == "Time zone"
