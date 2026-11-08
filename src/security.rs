//! 安全加固（原 app/security.py 的移植）：安全响应头 + 按 IP 滑动窗口限流。
//!
//! CSP 里 `script-src 'self'`：页面不允许任何内联 <script>，前端译文通过
//! `<script type="application/json">`（不可执行）注入；`style-src
//! 'unsafe-inline'` 仅为进度条内联宽度等少量样式保留。

use std::collections::{HashMap, VecDeque};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const SECURITY_HEADERS: [(&str, &str); 6] = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
    (
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; \
         img-src 'self' data:; connect-src 'self'; font-src 'self'; \
         base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    ),
    ("X-Permitted-Cross-Domain-Policies", "none"),
];

/// 限流配置：默认 240 次/分钟，环境变量 `API_RATE_LIMIT` 可调（1..=10000）。
pub fn rate_limit_config() -> usize {
    let raw = std::env::var("API_RATE_LIMIT").unwrap_or_default();
    let parsed = raw.trim().parse::<usize>().ok().unwrap_or(240);
    parsed.clamp(1, 10000)
}

/// 按 IP 的滑动窗口限流器，只作用于 `/api/` 前缀的 GET 读接口。
pub struct RateLimiter {
    limit: usize,
    window: Duration,
    hits: Mutex<HashMap<String, VecDeque<Instant>>>,
}

impl RateLimiter {
    pub fn new() -> Self {
        RateLimiter {
            limit: rate_limit_config(),
            window: Duration::from_secs(60),
            hits: Mutex::new(HashMap::new()),
        }
    }

    /// 从请求头提取客户端 IP（x-forwarded-for 第一段，限长 64）。
    pub fn client_ip(headers: &[tiny_http::Header], peer: Option<&str>) -> String {
        for header in headers {
            if header.field.equiv("X-Forwarded-For") {
                let first = header.value.as_str().to_string();
                let first = first.split(',').next().unwrap_or("").trim().to_string();
                if !first.is_empty() {
                    return first.chars().take(64).collect();
                }
            }
        }
        peer.unwrap_or("unknown").chars().take(64).collect()
    }

    /// 返回 Some(retry_after_seconds) 表示被限流。
    pub fn check(&self, ip: &str) -> Option<u64> {
        let now = Instant::now();
        let mut hits = self.hits.lock().unwrap();
        let bucket = hits.entry(ip.to_string()).or_default();
        while let Some(front) = bucket.front() {
            if now.duration_since(*front) > self.window {
                bucket.pop_front();
            } else {
                break;
            }
        }
        if bucket.len() >= self.limit {
            let retry_after = self
                .window
                .checked_sub(now.duration_since(*bucket.front().unwrap()))
                .map(|d| d.as_secs().max(1))
                .unwrap_or(1);
            // 限流命中：顺手清理长期不活跃的 IP，防止字典无限增长
            if hits.len() > 5000 {
                let stale: Vec<String> = hits
                    .iter()
                    .filter(|(_, v)| {
                        v.back()
                            .map(|t| now.duration_since(*t) > self.window)
                            .unwrap_or(true)
                    })
                    .map(|(k, _)| k.clone())
                    .collect();
                for k in stale {
                    hits.remove(&k);
                }
            }
            return Some(retry_after);
        }
        bucket.push_back(now);
        None
    }
}

impl Default for RateLimiter {
    fn default() -> Self {
        Self::new()
    }
}
