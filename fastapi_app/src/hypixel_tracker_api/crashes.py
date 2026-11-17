"""崩溃报告。

职责：
- 把未捕获异常写成 ``crash-YYYYmmdd-HHMMSS.json``（最多保留 20 份）；
- 对外只暴露报告 id，完整 traceback 只落盘 + 日志打一行，不回传给客户端；
- 提供按 id 精确读取的接口，供 ``--print-crash`` 使用（严格校验 id 格式）。
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
import traceback as traceback_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import APP_VERSION

logger = logging.getLogger(__name__)

CRASH_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}(?:-[0-9]{1,3})?$")
MAX_MESSAGE_LEN = 500


class CrashReporter:
    def __init__(self, directory: Path, max_files: int = 20, version: str = APP_VERSION):
        self.directory = directory
        self.max_files = max_files
        self.version = version

    def write(
        self,
        exc: BaseException,
        *,
        path: str,
        method: str,
        request_id: str,
    ) -> str:
        """保存异常并返回报告 id；写盘失败时仍返回 id 以便排查。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        report_id = self._next_id()
        message = str(exc).strip().replace("\n", " ")[:MAX_MESSAGE_LEN] or exc.__class__.__name__
        report = {
            "id": report_id,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "version": self.version,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "path": path,
            "method": method,
            "message": message,
            "traceback": "".join(
                traceback_module.format_exception(type(exc), exc, exc.__traceback__)
            ),
            "request_id": request_id,
        }
        target = self.directory / ("crash-" + report_id + ".json")
        tmp = target.with_name(target.name + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, target)
            self._prune()
        except OSError as write_error:  # 磁盘满 / 只读目录：不能让它掩盖原始异常
            logger.error("cannot write crash report %s: %s", target, write_error)
        logger.error(
            "unhandled error id=%s path=%s method=%s: %s",
            report_id,
            path,
            method,
            message,
        )
        return report_id

    def _next_id(self) -> str:
        base = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        candidate = base
        counter = 1
        while (self.directory / ("crash-" + candidate + ".json")).exists():
            counter += 1
            candidate = base + "-" + str(counter)
        return candidate

    def _prune(self) -> None:
        files = sorted(
            self.directory.glob("crash-*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for stale in files[self.max_files :]:
            try:
                stale.unlink()
            except OSError as exc:
                logger.warning("cannot prune %s: %s", stale, exc)

    def list_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        summary: List[Dict[str, Any]] = []
        files = sorted(
            self.directory.glob("crash-*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for path in files[: max(0, limit)]:
            report = self._read(path)
            if report is None:
                continue
            summary.append(
                {
                    "id": report.get("id", path.stem[len("crash-") :]),
                    "time": report.get("time"),
                    "version": report.get("version"),
                    "path": report.get("path"),
                    "method": report.get("method"),
                    "message": report.get("message"),
                }
            )
        return summary

    def get(self, report_id: str) -> Optional[Dict[str, Any]]:
        if not CRASH_ID_RE.match(report_id or ""):
            return None
        return self._read(self.directory / ("crash-" + report_id + ".json"))

    def _read(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("cannot read crash report %s: %s", path, exc)
            return None
        return data if isinstance(data, dict) else None
