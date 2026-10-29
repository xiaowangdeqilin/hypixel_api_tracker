from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.config import BASE_DIR
from app.db import (
    get_snapshot_by_id,
    get_status_summary,
    init_db,
    list_recent_changes,
    list_recent_snapshots,
)
from app.scheduler import start_scheduler

app = FastAPI(title="Hypixel API Tracker")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

init_db()
start_scheduler()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/status")
async def api_status():
    return {"data": get_status_summary()}


@app.get("/api/history")
async def api_history(endpoint: str | None = None, limit: int = 10):
    return {"data": list_recent_snapshots(endpoint=endpoint, limit=limit)}


@app.get("/api/snapshot/{snapshot_id}")
async def api_snapshot(snapshot_id: int):
    snapshot = get_snapshot_by_id(snapshot_id)
    if snapshot is None:
        return {"error": "snapshot not found"}
    return snapshot


@app.get("/api/changes")
async def api_changes(limit: int = 20):
    return {"data": list_recent_changes(limit=limit)}


@app.get("/health")
async def health():
    return {"status": "ok"}
