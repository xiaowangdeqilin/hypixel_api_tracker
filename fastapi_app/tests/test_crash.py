"""崩溃报告写入、读取与保留策略测试。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from hypixel_tracker_api.app import create_app
from hypixel_tracker_api.crashes import CrashReporter

from .conftest import FakeLocales, FakeStore


def test_unhandled_error_returns_500_and_writes_report(settings):
    app = create_app(settings=settings, store=FakeStore(), locales=FakeLocales())

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal server error"
    assert body["request_id"]
    assert body["crash_report"] == body["crash_report"].strip()

    path = settings.crash_dir / ("crash-" + body["crash_report"] + ".json")
    assert path.exists()
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["message"] == "kaboom"
    assert report["path"] == "/boom"
    assert report["method"] == "GET"
    assert "Traceback" in report["traceback"]
    assert report["request_id"] == body["request_id"]
    assert "Traceback" not in response.text


def test_crash_recent_endpoint_and_id_lookup(settings):
    reporter = CrashReporter(settings.crash_dir, max_files=20)
    report_id = reporter.write(
        ValueError("bad input"), path="/api/x", method="POST", request_id="req-1"
    )

    app = create_app(settings=settings, store=FakeStore(), locales=FakeLocales())
    with TestClient(app) as client:
        body = client.get("/api/crash/recent?limit=5").json()

    assert body["count"] == 1
    assert body["reports"][0]["id"] == report_id
    assert body["reports"][0]["message"] == "bad input"
    assert "ValueError: bad input" in reporter.get(report_id)["traceback"]
    assert reporter.get("../../etc/passwd") is None
    assert reporter.get("20260101-000000") is None


def test_crash_reports_are_capped(settings):
    reporter = CrashReporter(settings.crash_dir, max_files=3)
    for index in range(6):
        reporter.write(
            RuntimeError("failure " + str(index)),
            path="/boom",
            method="GET",
            request_id="req-" + str(index),
        )

    files = list(settings.crash_dir.glob("crash-*.json"))
    assert len(files) == 3
    assert len(reporter.list_recent(10)) == 3
