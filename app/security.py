"""安全加固中间件（无外部依赖，可直接复用）。

1. ``SecurityHeadersMiddleware``：为所有响应追加安全响应头。
   - CSP 里 ``script-src 'self'``：页面不允许任何内联 <script>，
     前端译文通过 ``<script type="application/json">``（不可执行）注入，
     数据动态渲染全部走外部 JS 文件并做 HTML 转义。
   - ``style-src 'unsafe-inline'`` 仅为进度条内联宽度等少量样式保留。
2. ``RateLimitMiddleware``：对 ``/api/`` 读接口做按 IP 的滑动窗口限流，
   防止被脚本刷接口拖垮 SQLite；阈值可用环境变量 ``API_RATE_LIMIT``
   （次/分钟，默认 240）调整，``/health`` 不受限流影响。

注意：如果部署在反向代理之后且希望按真实客户端 IP 限流，需要保证
代理正确覆写（而非透传客户端可伪造的）``X-Forwarded-For``。
"""

import os
import threading
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    ),
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按 IP 的滑动窗口限流，只作用于 ``/api/`` 前缀的读接口。"""

    def __init__(self, app, limit: int | None = None, window_seconds: int = 60):
        super().__init__(app)
        raw = os.getenv("API_RATE_LIMIT", "").strip()
        try:
            self.limit = int(raw) if raw else (limit if limit is not None else 240)
        except ValueError:
            self.limit = 240
        self.limit = max(1, min(self.limit, 10000))
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # 只取第一个非空段并做白化处理，避免奇异头值撑爆内存
            first = forwarded.split(",")[0].strip()[:64]
            if first:
                return first
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method != "GET" or not path.startswith("/api/") or path == "/api/health":
            return await call_next(request)

        ip = self._client_ip(request)
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[ip]
            while bucket and now - bucket[0] > self.window:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(1, int(self.window - (now - bucket[0])))
                # 限流命中：顺手清理长期不活跃的 IP，防止字典无限增长
                if len(self._hits) > 5000:
                    stale = [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]
                    for k in stale:
                        self._hits.pop(k, None)
                return JSONResponse(
                    {"error": "请求过于频繁，请稍后再试"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)
        return await call_next(request)


def safe_error_message(text: str, limit: int = 200) -> str:
    """错误信息白化：去掉控制字符并截断，避免异常细节污染存储与页面。"""
    cleaned = "".join(ch for ch in str(text) if ch.isprintable()).strip()
    return cleaned[:limit]
