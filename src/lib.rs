//! Hypixel SkyBlock API 变更追踪器（Rust 实现）。
//!
//! 模块划分与旧版 Python 一一对应：
//! - `timeutil`  北京时间统一处理（原 app/timeutil.py）
//! - `mctext`    Minecraft 颜色码渲染（原 app/mctext.py）
//! - `jsondiff`  结构化 diff（原 app/diff.py + build_dashboard 内的 diff_dict）
//! - `textdiff`  逐行 diff 片段（原 pack_inspector.diff_text_lines）
//! - `retention` 30 天保留期（原 app/retention.py）
//! - `bingo`     Bingo 任务整形（原 app/bingo.py）
//! - `firesale`  限时特卖整形（原 app/firesale.py）
//! - `labels`    展示层中文文案（原 build_dashboard 顶部常量）
//! - `fetch`     HTTP 抓取 / 校验和 / 服务端抓取循环（原 app/fetcher.py）
//! - `state`     数据文件读写与版本状态机（原 db.py + build_dashboard 状态部分）
//! - `render_site` 静态站 HTML 片段渲染（原 build_dashboard 渲染部分）
//! - `i18n`      可选多语言（原 app/i18n.py）
//! - `security`  安全响应头 + 限流（原 app/security.py）
//! - `server_app` 本地监控服务（原 app/main.py + scheduler.py）
//!
//! 存储格式（v2，全部 JSON、无数据库依赖）：
//! - `data/snapshots/<endpoint>.json` 每端点最新快照
//! - `data/changes.json` 统一变更日志（构建与服务端共用）
//! - `data/version.json` 版本状态机（原 runtime.json）
//! - `data/bingo.json` / `data/firesales.json` 展示数据
//! - `data/pack_files.json` / `data/pack_texts.json` / `data/pack_changes.json` 资源包
//! - `data/server/history.json` 本地服务专用快照环形缓冲（.gitignore）

pub mod bingo;
pub mod fetch;
pub mod firesale;
pub mod i18n;
pub mod jsondiff;
pub mod labels;
pub mod mctext;
pub mod packs;
pub mod render_site;
pub mod retention;
pub mod security;
pub mod server_app;
pub mod state;
pub mod textdiff;
pub mod timeutil;

pub use timeutil::now_beijing;

/// SHA-256 十六进制摘要（校验和用）。
pub fn sha2_digest(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(data);
    let out = hasher.finalize();
    out.iter().map(|b| format!("{:02x}", b)).collect()
}

/// SHA-1 十六进制摘要（资源包官方哈希校验用）。
pub fn sha1_hex(data: &[u8]) -> String {
    use sha1::{Digest, Sha1};
    let mut hasher = Sha1::new();
    hasher.update(data);
    let out = hasher.finalize();
    out.iter().map(|b| format!("{:02x}", b)).collect()
}

/// HTML 转义（文本节点用；属性额外转义引号）。
pub fn escape_html(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#39;"),
            _ => out.push(ch),
        }
    }
    out
}
