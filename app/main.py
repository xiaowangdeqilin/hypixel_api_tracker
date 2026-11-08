from typing import Any, Dict, Optional

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.config import (
    BASE_DIR,
    get_bingo_info,
    get_firesales_info,
    get_version,
    get_version_info,
)
from app.i18n import (
    BINGO_EMPTY_HINT,
    BINGO_HINT,
    BINGO_TIP,
    CHANGES_HINT,
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    FIRESALE_EMPTY_HINT,
    FIRESALE_HINT,
    available_languages,
    is_enabled as i18n_enabled,
    is_rtl,
    js_pack,
    resolve_language,
    translator,
)
from app.retention import RETENTION_DAYS
from app.security import RateLimitMiddleware, SecurityHeadersMiddleware
from app.db import (
    get_snapshot_by_id,
    get_status_summary,
    init_db,
    list_recent_changes,
    list_recent_snapshots,
)
from app.scheduler import start_scheduler

app = FastAPI(title="Hypixel SkyBlock API 变更监控台")

# 安全响应头 + API 限流（详见 app/security.py）
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

init_db()
start_scheduler()


def _i18n_context(request: Request, query_lang: Optional[str] = None) -> Dict[str, Any]:
    """组装模板需要的 i18n 上下文；多语言关闭时全部退回默认值。"""
    code = resolve_language(request, query_lang)
    t = translator(code)
    return {
        "t": t,
        "lang_code": code,
        "lang_dir": "rtl" if is_rtl(code) else "ltr",
        "i18n_enabled": i18n_enabled(),
        "languages": available_languages() if i18n_enabled() else {code: ""},
        "i18n_pack": js_pack(code) if i18n_enabled() else {},
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, lang: Optional[str] = Query(default=None, max_length=16)):
    context = {
        "request": request,
        "version": get_version(),
        "version_info": get_version_info(),
        "bingo": get_bingo_info(),
        "firesales": get_firesales_info(),
        "retention_days": RETENTION_DAYS,
    }
    context.update(_i18n_context(request, lang))
    context.update(
        CHANGES_HINT=CHANGES_HINT,
        FIRESALE_EMPTY_HINT=FIRESALE_EMPTY_HINT,
        FIRESALE_HINT=FIRESALE_HINT,
        BINGO_EMPTY_HINT=BINGO_EMPTY_HINT,
        BINGO_HINT=BINGO_HINT,
        BINGO_TIP=BINGO_TIP,
    )
    response = templates.TemplateResponse("index.html", context)
    if lang is not None and context["lang_code"] == resolve_language(None, lang):
        # 仅当用户通过 ?lang= 显式选择了合法语言时才种 Cookie，
        # 之后由浏览器记住偏好；非法取值不写入。
        response.set_cookie(
            COOKIE_NAME,
            context["lang_code"],
            max_age=COOKIE_MAX_AGE,
            samesite="lax",
            path="/",
        )
    return response


@app.get("/api/status")
async def api_status():
    return {"data": get_status_summary()}


@app.get("/api/history")
async def api_history(
    endpoint: Optional[str] = Query(default=None, max_length=50),
    limit: int = Query(default=10, ge=1, le=100),
):
    return {"data": list_recent_snapshots(endpoint=endpoint, limit=limit)}


@app.get("/api/snapshot/{snapshot_id}")
async def api_snapshot(snapshot_id: int):
    snapshot = get_snapshot_by_id(snapshot_id)
    if snapshot is None:
        return JSONResponse({"error": "未找到该快照"}, status_code=404)
    return snapshot


@app.get("/api/changes")
async def api_changes(limit: int = Query(default=20, ge=1, le=100)):
    return {"data": list_recent_changes(limit=limit)}


@app.get("/health")
async def health():
    return {"status": "ok"}
