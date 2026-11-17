"""HTTP 接口与网页渲染测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hypixel_tracker_api.app import create_app
from hypixel_tracker_api.languages import LANGUAGES

from .conftest import FakeLocales, FakeStore


def test_health_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "3.0.0"
    assert body["stale"] is False
    assert body["data_age_seconds"] == 12
    assert response.headers["cache-control"] == "no-store"


def test_health_reports_unavailable_without_raising(settings):
    app = create_app(settings=settings, store=FakeStore(error="offline"), locales=FakeLocales())
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "unavailable"
        assert body["stale"] is True
        assert body["data_age_seconds"] is None


def test_version_and_summary(client):
    version = client.get("/api/version").json()
    assert version["app"] == "hypixel-tracker-api"
    assert version["dashboard_version"] == "3.0.0"
    assert version["time_basis"] == "Asia/Shanghai"
    assert version["repo"].endswith("/hypixel_api_tracker")

    summary = client.get("/api/summary").json()
    names = [row["endpoint"] for row in summary["endpoints"]]
    assert names == ["collections", "skills", "items", "bazaar", "packs", "bingo", "firesales"]
    assert summary["endpoints"][0]["summary"]["collection_count"] == 6
    assert summary["stale"] is False


def test_changes_limit_clamped_and_filtered(client):
    everything = client.get("/api/changes").json()
    assert len(everything) == 3

    clamped_low = client.get("/api/changes?limit=0").json()
    assert len(clamped_low) == 1

    clamped_high = client.get("/api/changes?limit=99999").json()
    assert len(clamped_high) == 3

    skills = client.get("/api/changes?limit=200&endpoint=skills").json()
    assert len(skills) == 2
    assert {row["endpoint"] for row in skills} == {"skills"}

    unknown = client.get("/api/changes?endpoint=crashes")
    assert unknown.status_code == 400


def test_data_whitelist_and_traversal(client):
    collections = client.get("/api/data/collections")
    assert collections.status_code == 200
    assert collections.json()["status"] == "ok"

    endpoints = client.get("/api/data/endpoints").json()
    assert "skills" in endpoints

    for name in ("unknown", "Collections", "..%2Fetc%2Fpasswd", "passwd"):
        assert client.get("/api/data/" + name).status_code == 404


def test_languages_and_i18n(client):
    languages = client.get("/api/languages").json()
    assert len(languages) == len(LANGUAGES)
    assert languages[0] == {"code": "zh-CN", "name": "简体中文"}
    assert {"code": "ar", "name": "العربية"} in languages

    zh = client.get("/api/i18n/zh-CN").json()
    assert zh == {"lang": "zh-CN", "dir": "ltr", "fallback": False, "pack": {}}

    en = client.get("/api/i18n/en").json()
    assert en["pack"]["端点"] == "Endpoints"

    fr = client.get("/api/i18n/fr").json()
    assert fr["fallback"] is True

    assert client.get("/api/i18n/klingon").status_code == 404


def test_index_renders_without_javascript(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    for label in ("collections", "skills", "items", "bazaar", "packs", "bingo", "firesales"):
        assert label in html
    assert 'data-ts="2026-11-17 20:38:45"' in html
    assert 'value="zh-CN" selected' in html
    assert html.count("<option value=") >= 103
    assert "最近变更" in html
    assert "新增" in html and "删除" in html and "修改" in html


def test_index_uses_language_pack(client):
    html = client.get("/?lang=en").text
    assert "Endpoints" in html
    assert 'lang="en"' in html


def test_index_reports_data_error(settings):
    app = create_app(settings=settings, store=FakeStore(error="offline"), locales=FakeLocales())
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 503
        assert 'class="alert error"' in response.text
        assert "offline" in response.text


def test_data_endpoints_return_503_when_unavailable(settings):
    app = create_app(settings=settings, store=FakeStore(error="offline"), locales=FakeLocales())
    with TestClient(app) as client:
        for path in ("/api/summary", "/api/changes", "/api/data/bingo"):
            response = client.get(path)
            assert response.status_code == 503
            assert response.json()["error"] == "data unavailable"


def test_cors_and_request_id(client):
    response = client.get("/api/health", headers={"Origin": "https://example.com"})
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["x-request-id"]


def test_update_check_endpoint_uses_injected_http(settings):
    app = create_app(settings=settings, store=FakeStore(), locales=FakeLocales())

    class FakeHttp:
        async def get_json(self, url: str):
            return {"tag_name": "v3.1.0", "html_url": "https://example.com/releases/v3.1.0"}

    with TestClient(app) as client:
        app.state.http = FakeHttp()
        body = client.get("/api/update/check").json()

    assert body["current"] == "3.0.0"
    assert body["latest"] == "3.1.0"
    assert body["available"] is True
    assert body["page"].endswith("/v3.1.0")
