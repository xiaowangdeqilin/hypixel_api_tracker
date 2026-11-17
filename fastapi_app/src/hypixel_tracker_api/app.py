"""FastAPI 应用装配。

职责：
- lifespan 内创建并复用 httpx 客户端，装配 DataStore / LocaleStore；
- 提供网页与只读 JSON API；
- 请求级 request id、崩溃兜底、CORS 与缓存头。

数据刷新与降级逻辑都在 :mod:`data_source`，这里只负责协议层。
"""

from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.templating import Jinja2Templates

from .config import APP_NAME, APP_VERSION, RELEASES_PAGE, REPO_URL, Settings
from .crashes import CrashReporter
from .data_source import DataStore, DataUnavailable, HttpJsonClient
from .i18n import LocaleStore
from .languages import DEFAULT_LANGUAGE, language_list, normalize
from .updates import UpdateChecker
from .views import ENDPOINT_LABELS, TIMEZONES, endpoint_cards, recent_changes, total_changes

logger = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE_ROOT / "templates"))

DATA_NAMES = (
    "collections",
    "skills",
    "items",
    "bazaar",
    "packs",
    "bingo",
    "firesales",
    "pack",
    "pack_changes",
    "version_info",
    "endpoints",
)
DATA_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ENDPOINT_KEYS = tuple(name for name, _ in ENDPOINT_LABELS)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def _no_store(payload: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        content=payload,
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def create_app(
    settings: Optional[Settings] = None,
    store: Optional[DataStore] = None,
    locales: Optional[LocaleStore] = None,
) -> FastAPI:
    """应用工厂；测试可注入 store / locales 以避免真实网络。"""
    config = settings or Settings.from_env()
    reporter = CrashReporter(config.crash_dir, config.max_crash_files)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        config.ensure_dirs()
        client = httpx.AsyncClient(
            follow_redirects=True,
            headers={"User-Agent": APP_NAME + "/" + APP_VERSION},
        )
        fetcher = HttpJsonClient(client, config.timeout_seconds)
        application.state.http = fetcher
        if application.state.store is None:
            application.state.store = DataStore(config, fetcher)
        if application.state.locales is None:
            application.state.locales = LocaleStore(config, fetcher)
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(
        title="hypixel-tracker-api",
        version=APP_VERSION,
        description="Read-only API for Hypixel SkyBlock tracker data",
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.crash = reporter
    app.state.store = store
    app.state.locales = locales
    app.state.updates = UpdateChecker(
        config.release_api_url,
        config.releases_page,
        config.update_cache_seconds,
    )

    @app.middleware("http")
    async def request_guard(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 - 这里是最后一道用户可见的兜底
            report_id = reporter.write(
                exc,
                path=request.url.path,
                method=request.method,
                request_id=request_id,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "error": "internal server error",
                    "crash_report": report_id,
                    "request_id": request_id,
                },
                headers={"Cache-Control": "no-store", "X-Request-ID": request_id},
            )
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # CORS 最后加入 => 位于最外层，异常响应也带跨域头。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.exception_handler(DataUnavailable)
    async def data_unavailable_handler(request: Request, exc: DataUnavailable):
        logger.warning("data unavailable: %s", exc)
        return _no_store(
            {
                "error": "data unavailable",
                "detail": str(exc),
                "hint": "Retry later or check HYPIXEL_DATA_URL / network access.",
            },
            status_code=503,
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "")
        report_id = reporter.write(
            exc,
            path=request.url.path,
            method=request.method,
            request_id=request_id,
        )
        return _no_store(
            {
                "error": "internal server error",
                "crash_report": report_id,
                "request_id": request_id,
            },
            status_code=500,
        )

    async def load_payload(request: Request) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        return await request.app.state.store.load()

    @app.get("/", response_class=Response)
    async def index(request: Request, lang: str = DEFAULT_LANGUAGE):
        code = normalize(lang) or DEFAULT_LANGUAGE
        locale = await request.app.state.locales.get_pack(code)
        pack = locale["pack"] if locale else {}
        direction = locale["dir"] if locale else "ltr"
        fallback = bool(locale and locale.get("fallback"))

        data: Optional[Dict[str, Any]] = None
        meta: Dict[str, Any] = {"stale": True, "age_seconds": None, "source": "none"}
        data_error: Optional[str] = None
        status_code = 200
        try:
            data, meta = await load_payload(request)
        except DataUnavailable as exc:
            data_error = str(exc)
            status_code = 503

        payload = data or {}

        def translate(text: str, **params: Any) -> str:
            template = pack.get(text, text)
            if not params:
                return template

            def swap(match: "re.Match[str]") -> str:
                value = params.get(match.group(1))
                return match.group(0) if value is None else str(value)

            return re.sub(r"\{(\w+)\}", swap, template)

        context = {
            "request": request,
            "t": translate,
            "lang": code,
            "dir": direction,
            "fallback": fallback,
            "languages": language_list(),
            "timezones": TIMEZONES,
            "app_version": APP_VERSION,
            "repo_url": REPO_URL,
            "releases_page": RELEASES_PAGE,
            "dashboard_version": payload.get("dashboard_version") or "—",
            "build": payload.get("build") or "—",
            "generated_at": payload.get("generated_at") or "",
            "resource_version": payload.get("resource_version") or "—",
            "stale": bool(meta.get("stale")),
            "age_seconds": meta.get("age_seconds"),
            "source": meta.get("source"),
            "endpoints": endpoint_cards(payload),
            "changes": recent_changes(payload),
            "changes_total": total_changes(payload),
            "data_error": data_error,
            "time_basis": "Asia/Shanghai",
        }
        response = TEMPLATES.TemplateResponse(request, "index.html", context)
        response.status_code = status_code
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Content-Language"] = code
        return response

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    @app.get("/api/health")
    async def api_health(request: Request):
        payload: Dict[str, Any] = {
            "status": "unavailable",
            "version": APP_VERSION,
            "data_age_seconds": None,
            "stale": True,
        }
        try:
            _, meta = await load_payload(request)
        except DataUnavailable:
            return JSONResponse(payload, headers={"Cache-Control": "no-store"})
        payload["status"] = "stale" if meta.get("stale") else "ok"
        payload["data_age_seconds"] = meta.get("age_seconds")
        payload["stale"] = bool(meta.get("stale"))
        payload["generated_at"] = meta.get("generated_at")
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @app.get("/api/version")
    async def api_version(request: Request):
        dashboard_version = None
        build = None
        generated_at = None
        try:
            data, _ = await load_payload(request)
        except DataUnavailable:
            data = {}
        dashboard_version = data.get("dashboard_version")
        build = data.get("build")
        generated_at = data.get("generated_at")
        return _no_store(
            {
                "app": APP_NAME,
                "version": APP_VERSION,
                "dashboard_version": dashboard_version,
                "build": build,
                "generated_at": generated_at,
                "time_basis": "Asia/Shanghai",
                "repo": REPO_URL,
            }
        )

    @app.get("/api/summary")
    async def api_summary(request: Request):
        data, meta = await load_payload(request)
        endpoints = data.get("endpoints") or {}
        rows: List[Dict[str, Any]] = []
        for name in ENDPOINT_KEYS:
            entry = endpoints.get(name)
            if not isinstance(entry, dict):
                continue
            rows.append(
                {
                    "endpoint": name,
                    "status": entry.get("status"),
                    "summary": entry.get("summary") or {},
                    "fetched_at": entry.get("fetched_at"),
                }
            )
        return _no_store(
            {
                "endpoints": rows,
                "generated_at": data.get("generated_at"),
                "stale": bool(meta.get("stale")),
            }
        )

    @app.get("/api/changes")
    async def api_changes(request: Request, limit: int = 200, endpoint: Optional[str] = None):
        data, _ = await load_payload(request)
        size = _clamp(limit, 1, 2000)
        changes = data.get("changes")
        if not isinstance(changes, list):
            changes = []

        selected = None
        if endpoint:
            selected = endpoint.strip().lower()
            if selected not in ENDPOINT_KEYS:
                return _no_store(
                    {
                        "error": "unknown endpoint",
                        "detail": "available: " + ", ".join(ENDPOINT_KEYS),
                    },
                    status_code=400,
                )
            changes = [
                item
                for item in changes
                if isinstance(item, dict) and item.get("endpoint") == selected
            ]
        return _no_store(changes[:size])

    @app.get("/api/data/{name}")
    async def api_data(request: Request, name: str):
        if name not in DATA_NAMES or not DATA_NAME_RE.match(name):
            return _no_store({"error": "not found", "detail": "unknown dataset " + name}, 404)
        data, _ = await load_payload(request)
        endpoints = data.get("endpoints")
        if isinstance(endpoints, dict) and name in endpoints:
            return _no_store(endpoints[name])
        if name in data:
            return _no_store(data[name])
        return _no_store({"error": "not found", "detail": "dataset missing in payload"}, 404)

    @app.get("/api/languages")
    async def api_languages():
        return _no_store(language_list())

    @app.get("/api/i18n/{lang}")
    async def api_i18n(request: Request, lang: str):
        if len(lang) > 24:
            return _no_store({"error": "not found", "detail": "unknown language"}, 404)
        locale = await request.app.state.locales.get_pack(lang)
        if locale is None:
            return _no_store({"error": "not found", "detail": "unknown language " + lang}, 404)
        return _no_store(locale)

    @app.get("/api/crash/recent")
    async def api_crash_recent(limit: int = 10):
        size = _clamp(limit, 1, 50)
        reports = reporter.list_recent(size)
        return _no_store({"count": len(reports), "reports": reports})

    @app.get("/api/update/check")
    async def api_update_check(request: Request):
        checker: UpdateChecker = request.app.state.updates
        result = await checker.check(request.app.state.http.get_json)
        return _no_store(result)

    return app


app = create_app()
