//! 本地监控服务（原 app/main.py + scheduler.py + db.py 的整合）。
//!
//! - 路由：`/`、`/api/status`、`/api/history`、`/api/snapshot/{id}`、
//!   `/api/changes`、`/health`、`/static/*`
//! - 模板：`templates/server.html.j2`（Tera；译文由服务端预先解析后注入，
//!   模板里不再出现 t() 调用）
//! - 安全：`security::SECURITY_HEADERS` + `/api/*` 限流；CSP 禁内联脚本，
//!   前端译文通过 `<script type="application/json">` 注入
//! - 调度：后台线程按各端点间隔抓取（启动即抓一轮），变更写入统一的
//!   `data/changes.json`，快照进入 `data/server/history.json` 环形缓冲

use std::collections::BTreeMap;
use std::sync::Arc;

use serde_json::{json, Value};
use tiny_http::{Header, Response, Server};

use crate::bingo::{goal_progress, requirement_text};
use crate::escape_html;
use crate::fetch;
use crate::i18n;
use crate::state::{self, ServerHistory};
use crate::timeutil::{format_time, to_epoch_ms};

const BIND_ADDR_DEFAULT: &str = "127.0.0.1:8000";

/// 入口：阻塞运行服务。
pub fn run() -> i32 {
    let bind = std::env::var("BIND_ADDR").unwrap_or_else(|_| BIND_ADDR_DEFAULT.into());
    let server = match Server::http(&bind) {
        Ok(s) => Arc::new(s),
        Err(e) => {
            eprintln!("[服务] 监听 {} 失败：{}", bind, e);
            return 1;
        }
    };
    eprintln!("[服务] Hypixel SkyBlock API 变更监控台已启动：http://{}", bind);
    if !i18n::is_enabled() {
        eprintln!("[服务] 多语言未开启（设置 ENABLE_I18N=1 启用 31 种语言与 RTL 适配）");
    }

    // 后台抓取线程：启动即抓一轮，之后按各端点间隔调度
    let _ = std::thread::Builder::new()
        .name("scheduler".into())
        .spawn(scheduler_loop);

    let limiter = Arc::new(crate::security::RateLimiter::new());
    let pool_size = 8;
    let mut workers = Vec::new();
    for _ in 0..pool_size {
        let server = server.clone();
        let limiter = limiter.clone();
        workers.push(
            std::thread::Builder::new()
                .name("http-worker".into())
                .spawn(move || serve_loop(server, limiter)),
        );
    }
    for worker in workers {
        if let Ok(handle) = worker {
            let _ = handle.join();
        }
    }
    0
}

fn serve_loop(server: Arc<Server>, limiter: Arc<crate::security::RateLimiter>) {
    loop {
        let request = match server.recv() {
            Ok(r) => r,
            Err(e) => {
                eprintln!("[服务] 接收请求失败：{}", e);
                continue;
            }
        };
        handle_request(request, &limiter);
    }
}

fn header(name: &str, value: &str) -> Header {
    Header::from_bytes(name.as_bytes(), value.as_bytes()).expect("valid header")
}

fn handle_request(request: tiny_http::Request, limiter: &crate::security::RateLimiter) {
    let method = request.method().clone();
    let url = request.url().to_string();
    let (path, query) = match url.split_once('?') {
        Some((p, q)) => (p.to_string(), q.to_string()),
        None => (url.clone(), String::new()),
    };
    let peer = request.remote_addr().map(|a| a.to_string());
    let ip = crate::security::RateLimiter::client_ip(request.headers(), peer.as_deref());
    let cookie_lang = read_cookie_lang(request.headers());

    let (code, content_type, body, extra_headers) =
        route(&method, &path, &query, limiter, &ip, cookie_lang.as_deref());

    let mut response = Response::from_string(body).with_status_code(code);
    response.add_header(header("Content-Type", content_type));
    for (name, value) in crate::security::SECURITY_HEADERS {
        response.add_header(header(name, value));
    }
    for (name, value) in extra_headers {
        response.add_header(header(&name, &value));
    }
    let _ = request.respond(response);
}

/// 读取 Cookie 中的语言偏好（hypixel-lang）。
fn read_cookie_lang(headers: &[tiny_http::Header]) -> Option<String> {
    for header in headers {
        if header.field.equiv("Cookie") {
            for part in header.value.as_str().split(';') {
                let part = part.trim();
                if let Some((name, value)) = part.split_once('=') {
                    if name.trim() == i18n::COOKIE_NAME {
                        return Some(value.trim().to_string());
                    }
                }
            }
        }
    }
    None
}

/// 极简查询串解析（key=value&...，百分号解码）。
pub fn parse_query(query: &str) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    for pair in query.split('&') {
        if pair.is_empty() {
            continue;
        }
        let (k, v) = match pair.split_once('=') {
            Some((k, v)) => (k, v),
            None => (pair, ""),
        };
        out.insert(percent_decode(k), percent_decode(v));
    }
    out
}

pub fn percent_decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                match u8::from_str_radix(&text[i + 1..i + 3], 16) {
                    Ok(byte) => {
                        out.push(byte);
                        i += 3;
                    }
                    Err(_) => {
                        out.push(b'%');
                        i += 1;
                    }
                }
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b => {
                out.push(b);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

type RouteResult = (u16, &'static str, String, Vec<(String, String)>);

fn route(
    method: &tiny_http::Method,
    path: &str,
    query: &str,
    limiter: &crate::security::RateLimiter,
    ip: &str,
    cookie_lang: Option<&str>,
) -> RouteResult {
    if *method != tiny_http::Method::Get {
        return (
            405,
            "application/json; charset=utf-8",
            r#"{"error":"method not allowed"}"#.into(),
            Vec::new(),
        );
    }

    // 限流：/api/ 读接口（/health 豁免）
    if path.starts_with("/api/") && path != "/api/health" {
        if let Some(retry_after) = limiter.check(ip) {
            let body = json!({"error": "请求过于频繁，请稍后再试"}).to_string();
            return (
                429,
                "application/json; charset=utf-8",
                body,
                vec![("Retry-After".into(), retry_after.to_string())],
            );
        }
    }

    // ?lang= 显式选择合法语言时种 Cookie，之后由浏览器记住偏好；非法取值不写入
    let mut extra_headers = Vec::new();
    if path == "/" && i18n::is_enabled() {
        let params = parse_query(query);
        if let Some(lang) = params.get("lang").and_then(|l| i18n::normalize_language(Some(l))) {
            extra_headers.push((
                "Set-Cookie".into(),
                format!(
                    "{}={}; Max-Age={}; Path=/; SameSite=Lax",
                    i18n::COOKIE_NAME,
                    lang,
                    i18n::COOKIE_MAX_AGE
                ),
            ));
        }
    }

    let (code, ct, body) = match path {
        "/" => (200, "text/html; charset=utf-8", render_index(query, cookie_lang)),
        "/api/status" => (200, "application/json; charset=utf-8", api_status()),
        "/api/history" => (200, "application/json; charset=utf-8", api_history(query)),
        "/api/changes" => (200, "application/json; charset=utf-8", api_changes(query)),
        "/health" => (200, "application/json; charset=utf-8", r#"{"status":"ok"}"#.into()),
        p if p.starts_with("/api/snapshot/") => {
            let id = p.trim_start_matches("/api/snapshot/");
            match id.parse::<u64>() {
                Ok(id) => api_snapshot(id),
                Err(_) => (
                    400,
                    "application/json; charset=utf-8",
                    r#"{"error":"invalid id"}"#.into(),
                ),
            }
        }
        p if p.starts_with("/static/") => serve_static(p),
        _ => (404, "text/plain; charset=utf-8", "404 Not Found".into()),
    };
    (code, ct, body, extra_headers)
}

// ---------------------------------------------------------------------------
// 展示数据装配（原 config.py get_bingo_info / get_firesales_info）
// ---------------------------------------------------------------------------

fn get_bingo_info() -> Value {
    let data = state::load_json(&state::data_dir().join("bingo.json")).unwrap_or(Value::Null);
    let Some(obj) = data.as_object() else { return Value::Null };
    if obj.get("id").map(|v| v.is_null()).unwrap_or(true) {
        return Value::Null;
    }
    let mut view = obj.clone();
    let goals: Vec<Value> = obj
        .get("goals")
        .and_then(|g| g.as_array())
        .map(|arr| {
            arr.iter()
                .filter(|g| g.is_object())
                .map(|goal| {
                    let mut entry = goal.as_object().cloned().unwrap_or_default();
                    let lore_lines: Vec<String> = match entry.get("lore_lines").and_then(|l| l.as_array()) {
                        Some(arr) if !arr.is_empty() => {
                            arr.iter().filter_map(|l| l.as_str().map(String::from)).collect()
                        }
                        _ => entry
                            .get("lore")
                            .and_then(|l| l.as_str())
                            .filter(|l| !l.is_empty())
                            .map(|l| vec![l.to_string()])
                            .unwrap_or_default(),
                    };
                    entry.insert(
                        "lore_html".into(),
                        Value::Array(
                            crate::mctext::render_mc_lines(&lore_lines, Some("#e2e8f0"))
                                .into_iter()
                                .map(Value::String)
                                .collect(),
                        ),
                    );
                    let entry_value = Value::Object(entry.clone());
                    if !entry.contains_key("progress_info") {
                        if let Some(p) = goal_progress(&entry_value) {
                            entry.insert("progress_info".into(), p);
                        }
                    }
                    if !entry.contains_key("requirement_text") {
                        entry.insert(
                            "requirement_text".into(),
                            Value::String(requirement_text(&entry_value)),
                        );
                    }
                    Value::Object(entry)
                })
                .collect()
        })
        .unwrap_or_default();
    view.insert("goals".into(), Value::Array(goals));
    for key in ["published_at", "first_seen", "start", "end"] {
        view.insert(
            key.into(),
            Value::String(format_time(obj.get(key).unwrap_or(&Value::Null))),
        );
    }
    // 兼容早期数据：没有 start_ms/end_ms 时从时间字符串反推
    let start_ms = obj
        .get("start_ms")
        .and_then(to_epoch_ms_opt)
        .or_else(|| obj.get("start").and_then(to_epoch_ms_opt));
    let end_ms = obj
        .get("end_ms")
        .and_then(to_epoch_ms_opt)
        .or_else(|| obj.get("end").and_then(to_epoch_ms_opt));
    view.insert("start_ms".into(), start_ms.map(Value::from).unwrap_or(Value::Null));
    view.insert("end_ms".into(), end_ms.map(Value::from).unwrap_or(Value::Null));
    Value::Object(view)
}

fn to_epoch_ms_opt(value: &Value) -> Option<i64> {
    match value {
        Value::Null => None,
        other => to_epoch_ms(other),
    }
}

fn get_firesales_info() -> Value {
    let data = state::load_json(&state::data_dir().join("firesales.json")).unwrap_or(Value::Null);
    let Some(obj) = data.as_object() else { return Value::Null };
    let empty_sales = obj
        .get("sales")
        .and_then(|s| s.as_array())
        .map(|a| a.is_empty())
        .unwrap_or(true);
    if empty_sales {
        return Value::Null;
    }
    crate::firesale::firesales_view(
        &json!({"sales": obj.get("sales").cloned().unwrap_or(Value::Null)}),
        obj.get("generated_at").and_then(|v| v.as_str()).unwrap_or("-"),
        None,
    )
}

// ---------------------------------------------------------------------------
// 页面渲染
// ---------------------------------------------------------------------------


/// 把模板里的 `t['中文原文']` 替换为 `t.kN`，并返回 kN -> 译文的映射。
fn rewrite_template_keys(template: &str, lang: &str) -> (String, BTreeMap<String, Value>) {
    let mut out = String::with_capacity(template.len());
    let mut mapping: BTreeMap<String, Value> = BTreeMap::new();
    let mut slugs: BTreeMap<String, String> = BTreeMap::new();
    let bytes = template.as_bytes();
    let mut i = 0usize;
    while i < bytes.len() {
        // 查找下一个 t['
        match template[i..].find("t['") {
            Some(rel) => {
                let start = i + rel;
                out.push_str(&template[i..start]);
                let key_start = start + 3;
                let key_end = match template[key_start..].find("']") {
                    Some(end) => key_start + end,
                    None => {
                        out.push_str(&template[start..]);
                        break;
                    }
                };
                let key = &template[key_start..key_end];
                let slug = match slugs.get(key) {
                    Some(existing) => existing.clone(),
                    None => {
                        let name = format!("k{}", slugs.len());
                        mapping.insert(
                            name.clone(),
                            Value::String(i18n::translate(lang, key, None)),
                        );
                        slugs.insert(key.to_string(), name.clone());
                        name
                    }
                };
                out.push_str(&format!("t.{}", slug));
                i = key_end + 2;
            }
            None => {
                out.push_str(&template[i..]);
                break;
            }
        }
    }
    (out, mapping)
}

fn render_index(query: &str, cookie_lang: Option<&str>) -> String {
    let params = parse_query(query);
    let lang = i18n::resolve_language(params.get("lang").map(|s| s.as_str()), cookie_lang);
    let enabled = i18n::is_enabled();
    let languages: Vec<(String, String)> = if enabled {
        i18n::available_languages().into_iter().collect()
    } else {
        vec![(lang.clone(), String::new())]
    };
    let pack = if enabled { i18n::js_pack(&lang) } else { json!({}) };

    // Tera 对非 ASCII 的方括号键（t['中文']）有字节边界 panic 的已知限制，
    // 因此渲染前把模板里的 `t['原文']` 重写为 ASCII 别名 `t.kN`，
    // 译文映射（kN -> 译文）在服务端构建后注入上下文。
    let template_path = state::base_dir().join("templates").join("server.html.j2");
    let raw_template = match std::fs::read_to_string(&template_path) {
        Ok(t) => t,
        Err(e) => return format!("<h1>模板加载失败</h1><pre>{}</pre>", escape_html(&e.to_string())),
    };
    let (rewritten, t) = rewrite_template_keys(&raw_template, &lang);
    let mut tera = tera::Tera::default();
    if let Err(e) = tera.add_raw_template("server.html.j2", &rewritten) {
        return format!("<h1>模板加载失败</h1><pre>{}</pre>", escape_html(&e.to_string()));
    }
    // 序号补零过滤器（bingo 格子、特卖卡片的 01/02… 编号）
    tera.register_filter("pad02", |value: &tera::Value, _: &std::collections::HashMap<String, tera::Value>| {
        Ok(tera::Value::String(format!("{:02}", value.as_u64().unwrap_or(0))))
    });

    let version = state::read_version();
    let version_info = state::version_info_view(&state::load_runtime_compat());
    let bingo = get_bingo_info();
    let firesales = get_firesales_info();
    let retention_days = crate::retention::RETENTION_DAYS;

    // 顶栏同步状态标签
    let sync_status = version_info
        .get("sync_status")
        .and_then(|v| v.as_str())
        .unwrap_or("partial")
        .to_string();
    let sync_label = i18n::translate(&lang, sync_status_key(&sync_status), None);

    // firesales 视图（逐期翻译标签、价格/数量字符串）
    let mut firesales_ctx = firesales.clone();
    if let Some(sales) = firesales_ctx.get_mut("sales").and_then(|s| s.as_array_mut()) {
        for sale in sales.iter_mut() {
            let Some(sobj) = sale.as_object_mut() else { continue };
            let label = sobj
                .get("state_label")
                .and_then(|v| v.as_str())
                .unwrap_or("未知")
                .to_string();
            sobj.insert(
                "state_label_t".into(),
                Value::String(i18n::translate(&lang, &label, None)),
            );
            let price_str = sobj
                .get("price")
                .filter(|v| !v.is_null())
                .map(|v| crate::firesale::format_amount(v))
                .unwrap_or_else(|| "-".into());
            let amount_str = sobj
                .get("amount")
                .filter(|v| !v.is_null())
                .map(|v| crate::firesale::format_amount(v))
                .unwrap_or_else(|| "-".into());
            sobj.insert("price_str".into(), Value::String(price_str));
            sobj.insert("amount_str".into(), Value::String(amount_str));
        }
    }

    // bingo 视图（翻译状态标签、组装格子数据）
    let mut bingo_ctx = bingo.clone();
    if let Some(bobj) = bingo_ctx.as_object_mut() {
        let state_label = bobj
            .get("state_label")
            .and_then(|v| v.as_str())
            .unwrap_or("未知")
            .to_string();
        bobj.insert(
            "state_label_t".into(),
            Value::String(i18n::translate(&lang, &state_label, None)),
        );
        if let Some(goals) = bobj.get_mut("goals").and_then(|g| g.as_array_mut()) {
            for (idx, goal) in goals.iter_mut().enumerate() {
                let Some(gobj) = goal.as_object_mut() else { continue };
                let p = gobj.get("progress_info").cloned();
                let req_text = gobj
                    .get("requirement_text")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let has_tiers = gobj
                    .get("tiers")
                    .map(|t| t.is_array() && !t.as_array().unwrap().is_empty())
                    .unwrap_or(false);
                let prefix = if has_tiers {
                    i18n::translate(&lang, "目标档位：", None)
                } else if !req_text.is_empty() {
                    i18n::translate(&lang, "要求：", None)
                } else {
                    String::new()
                };
                gobj.insert(
                    "requirement_attr".into(),
                    Value::String(format!("{}{}", prefix, req_text)),
                );
                let percent_str = p
                    .as_ref()
                    .and_then(|p| p.get("percent"))
                    .and_then(|v| v.as_f64())
                    .map(trim_num)
                    .unwrap_or_default();
                gobj.insert("percent_str".into(), Value::String(percent_str));
                gobj.insert("index_str".into(), Value::String(format!("{:02}", idx + 1)));
                let is_maxed = p
                    .as_ref()
                    .and_then(|p| p.get("is_maxed"))
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false);
                let progress_attr = match &p {
                    Some(p) if is_maxed => i18n::translate(
                        &lang,
                        "全服 {progress} · 已满级（{reached}/{total} 档）",
                        Some(&param_map([
                            ("progress", p.get("progress_text").and_then(|v| v.as_str()).unwrap_or("")),
                            ("reached", &p.get("reached").map(|v| v.to_string()).unwrap_or_default()),
                            ("total", &p.get("total").map(|v| v.to_string()).unwrap_or_default()),
                        ])),
                    ),
                    Some(p) => i18n::translate(
                        &lang,
                        "全服 {progress} / {top} · 已达成 {reached}/{total} 档",
                        Some(&param_map([
                            ("progress", p.get("progress_text").and_then(|v| v.as_str()).unwrap_or("")),
                            ("top", p.get("top_text").and_then(|v| v.as_str()).unwrap_or("")),
                            ("reached", &p.get("reached").map(|v| v.to_string()).unwrap_or_default()),
                            ("total", &p.get("total").map(|v| v.to_string()).unwrap_or_default()),
                        ])),
                    ),
                    None => String::new(),
                };
                gobj.insert("progress_attr".into(), Value::String(progress_attr));
                gobj.insert(
                    "is_done".into(),
                    Value::Bool(
                        p.as_ref()
                            .and_then(|p| p.get("percent"))
                            .and_then(|v| v.as_f64())
                            .map(|v| v >= 100.0)
                            .unwrap_or(false),
                    ),
                );
                let foot = match &p {
                    // 满级后进度条到 100% 即止，文案也不再显示"超出满级"的数字
                    Some(p) if is_maxed => format!(
                        "{} · {}/{} {}",
                        i18n::translate(&lang, "已满级", None),
                        p.get("reached").map(|v| v.to_string()).unwrap_or_default(),
                        p.get("total").map(|v| v.to_string()).unwrap_or_default(),
                        i18n::translate(&lang, "档", None)
                    ),
                    Some(p) => format!(
                        "{} / {} · {}/{} {}",
                        p.get("progress_text").and_then(|v| v.as_str()).unwrap_or(""),
                        p.get("top_text").and_then(|v| v.as_str()).unwrap_or(""),
                        p.get("reached").map(|v| v.to_string()).unwrap_or_default(),
                        p.get("total").map(|v| v.to_string()).unwrap_or_default(),
                        i18n::translate(&lang, "档", None)
                    ),
                    None if !req_text.is_empty() => i18n::translate(
                        &lang,
                        "要求 {text}",
                        Some(&param_map([("text", &req_text)])),
                    ),
                    None => i18n::translate(&lang, "无目标数值", None),
                };
                gobj.insert("foot_text".into(), Value::String(foot));
                // 社区/个人任务标签（对角线规则，见 bingo_view）
                let kind_label = if gobj
                    .get("is_community")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false)
                {
                    i18n::translate(&lang, "社区任务", None)
                } else {
                    i18n::translate(&lang, "个人任务", None)
                };
                gobj.insert("kind_label_t".into(), Value::String(kind_label));
            }
        }
    }

    let goal_count = bingo
        .get("goals")
        .and_then(|g| g.as_array())
        .map(|a| a.len())
        .unwrap_or(0);
    let firesales_non_empty = !firesales.is_null();
    // 秘密 Bingo：提示数据由 data/secret_bingo.json 人工维护，按时间依次解锁；
    // 没有配置时用占位视图，页面照常预留 3 个任务位（每个任务 3 条提示）。
    let mut secret_view = crate::secret_bingo::current_view();
    if secret_view.get("configured").and_then(|v| v.as_bool()) != Some(true) {
        secret_view = crate::secret_bingo::placeholder_view(3);
    }

    let mut context = tera::Context::new();
    context.insert("lang_code", &lang);
    context.insert("lang_dir", &dir_of(&lang));
    context.insert("i18n_enabled", &enabled);
    context.insert(
        "languages",
        &languages
            .iter()
            .map(|(code, name)| json!({"code": code, "name": name}))
            .collect::<Vec<_>>(),
    );
    context.insert("i18n_pack", &serde_json::to_string(&pack).unwrap_or_default());
    context.insert("t", &t);
    context.insert("sync_status", &sync_status);
    context.insert("sync_label", &sync_label);
    context.insert("version", &version);
    context.insert("version_info", &version_info);
    context.insert("retention_days", &retention_days);
    context.insert("bingo", &bingo_ctx);
    context.insert("goal_count", &goal_count);
    context.insert("firesales", &firesales_ctx);
    context.insert("firesales_non_empty", &firesales_non_empty);
    context.insert("secret_view", &secret_view);

    // 参数化长句
    context.insert(
        "changes_hint",
        &i18n::translate(
            &lang,
            i18n::CHANGES_HINT,
            Some(&param_map([("days", &retention_days.to_string())])),
        ),
    );
    context.insert("firesale_empty_hint", &i18n::translate(&lang, i18n::FIRESALE_EMPTY_HINT, None));
    context.insert("firesale_hint", &i18n::translate(&lang, i18n::FIRESALE_HINT, None));
    context.insert("bingo_empty_hint", &i18n::translate(&lang, i18n::BINGO_EMPTY_HINT, None));
    context.insert("bingo_hint", &i18n::translate(&lang, i18n::BINGO_HINT, None));
    context.insert("bingo_tip", &i18n::translate(&lang, i18n::BINGO_TIP, None));

    if firesales_non_empty {
        let total = firesales.get("total").map(|v| v.to_string()).unwrap_or_else(|| "0".into());
        context.insert(
            "firesales_header",
            &i18n::translate(&lang, "共 {total} 期", Some(&param_map([("total", &total)]))),
        );
        let generated_at_text = firesales
            .get("generated_at")
            .and_then(|v| v.as_str())
            .unwrap_or("-")
            .to_string();
        context.insert(
            "firesale_generated_hint",
            &i18n::translate(
                &lang,
                "数据抓取时间：{time}（北京时间）",
                Some(&param_map([("time", &generated_at_text)])),
            ),
        );
    } else {
        context.insert("firesales_header", &String::new());
        context.insert("firesale_generated_hint", &String::new());
    }
    if !bingo.is_null() {
        let id = bingo["id"].to_string().trim_matches('"').to_string();
        context.insert(
            "bingo_header",
            &i18n::translate(&lang, "第 {id} 期", Some(&param_map([("id", &id)]))),
        );
    } else {
        context.insert("bingo_header", &String::new());
    }
    // 集合视图里当前期次（非已结束）特卖卡（取自已补丁的 firesales_ctx，含译文标签）
    let current_sales: Vec<Value> = firesales_ctx
        .get("sales")
        .and_then(|s| s.as_array())
        .map(|arr| {
            arr.iter()
                .filter(|s| s.get("state").and_then(|v| v.as_str()) != Some("ended"))
                .cloned()
                .collect()
        })
        .unwrap_or_default();
    context.insert("current_sales", &current_sales);

    tera.render("server.html.j2", &context)
        .unwrap_or_else(|e| format!("<h1>渲染失败</h1><pre>{}</pre>", escape_html(&format!("{:?}", e))))
}

fn trim_num(v: f64) -> String {
    let text = format!("{}", v);
    if text.contains('.') {
        text.trim_end_matches('0').trim_end_matches('.').to_string()
    } else {
        text
    }
}

fn param_map<const N: usize>(pairs: [(&str, &str); N]) -> BTreeMap<String, String> {
    pairs
        .into_iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

fn dir_of(lang: &str) -> String {
    if i18n::is_rtl(lang) { "rtl".into() } else { "ltr".into() }
}

fn sync_status_key(status: &str) -> &'static str {
    match status {
        "aligned" => "版本一致",
        "syncing" => "同步中",
        "converged" => "已收敛",
        "partial" => "数据不全",
        _ => "数据不全",
    }
}

// ---------------------------------------------------------------------------
// JSON API（响应形状与旧版 FastAPI 保持一致）
// ---------------------------------------------------------------------------

fn api_status() -> String {
    let history = state::load_server_history();
    let changes = state::load_changes();
    // 每端点最新快照（追加序即最新）
    let mut latest: BTreeMap<String, &Value> = BTreeMap::new();
    for snap in &history.snapshots {
        let ep = snap.get("endpoint").and_then(|v| v.as_str()).unwrap_or("").to_string();
        latest.insert(ep, snap);
    }
    let mut summary: Vec<Value> = Vec::new();
    for (endpoint, snap) in latest {
        let change_count = changes
            .iter()
            .filter(|c| c.get("endpoint").and_then(|v| v.as_str()) == Some(endpoint.as_str()))
            .count();
        summary.push(json!({
            "endpoint": endpoint,
            "fetched_at": snap.get("fetched_at").cloned().unwrap_or(Value::Null),
            "status_code": snap.get("status_code").cloned().unwrap_or(Value::Null),
            "change_count": change_count,
        }));
    }
    json!({"data": summary}).to_string()
}

fn api_history(query: &str) -> String {
    let params = parse_query(query);
    let limit = params
        .get("limit")
        .and_then(|l| l.parse::<usize>().ok())
        .unwrap_or(10)
        .clamp(1, 100);
    let endpoint = params.get("endpoint").filter(|e| e.len() <= 50);
    let history = state::load_server_history();
    let items: Vec<Value> = history
        .snapshots
        .iter()
        .rev()
        .filter(|snap| match endpoint {
            Some(ep) => snap.get("endpoint").and_then(|v| v.as_str()) == Some(ep.as_str()),
            None => true,
        })
        .take(limit)
        .map(|snap| {
            // 历史列表只给元数据：旧版把整个 payload 一并返回，items 端点单条
            // 6MB+，纯浪费带宽；需要完整内容请用 /api/snapshot/{id}
            json!({
                "id": snap.get("id").cloned().unwrap_or(Value::Null),
                "endpoint": snap.get("endpoint").cloned().unwrap_or(Value::Null),
                "fetched_at": snap.get("fetched_at").cloned().unwrap_or(Value::Null),
                "status_code": snap.get("status_code").cloned().unwrap_or(Value::Null),
                "checksum": snap.get("checksum").cloned().unwrap_or(Value::Null),
            })
        })
        .collect();
    json!({"data": items}).to_string()
}

fn api_snapshot(id: u64) -> (u16, &'static str, String) {
    let history = state::load_server_history();
    match history
        .snapshots
        .iter()
        .find(|snap| snap.get("id").and_then(|v| v.as_u64()) == Some(id))
    {
        Some(snap) => (200, "application/json; charset=utf-8", snap.to_string()),
        None => (
            404,
            "application/json; charset=utf-8",
            json!({"error": "未找到该快照"}).to_string(),
        ),
    }
}

fn api_changes(query: &str) -> String {
    let params = parse_query(query);
    // 默认返回 30 天内的全部变更（不限条数）；显式传 limit 时才截断，方便调试。
    let limit = params.get("limit").and_then(|l| l.parse::<usize>().ok());
    // changes.json 新记录在最前（append_changes 的合并顺序保证）；
    // 响应形状与旧版 SQLite change_events 对齐。
    let all = state::load_changes();
    let data: Vec<Value> = all
        .into_iter()
        .take(limit.unwrap_or(usize::MAX))
        .map(|entry| {
            json!({
                "endpoint": entry.get("endpoint").cloned().unwrap_or(Value::Null),
                "change_type": entry.get("type").cloned().unwrap_or(Value::Null),
                "path": entry.get("path").cloned().unwrap_or(Value::Null),
                "old_value": entry.get("old_full").cloned().unwrap_or(Value::Null),
                "new_value": entry.get("new_full").cloned().unwrap_or(Value::Null),
                "created_at": entry.get("time").cloned().unwrap_or(Value::Null),
            })
        })
        .collect();
    json!({"data": data}).to_string()
}

// ---------------------------------------------------------------------------
// 静态文件（含路径穿越防护）
// ---------------------------------------------------------------------------

fn serve_static(path: &str) -> (u16, &'static str, String) {
    let Some(rel) = path.strip_prefix("/static/") else { return not_found() };
    if rel.contains("..") || rel.contains('\\') || rel.starts_with('/') || rel.is_empty() {
        return not_found();
    }
    let full = state::base_dir().join("static").join(rel);
    let Ok(meta) = std::fs::metadata(&full) else { return not_found() };
    if !meta.is_file() {
        return not_found();
    }
    match std::fs::read(&full) {
        Ok(bytes) => (
            200,
            content_type_of(rel),
            String::from_utf8_lossy(&bytes).into_owned(),
        ),
        Err(_) => not_found(),
    }
}

fn not_found() -> (u16, &'static str, String) {
    (404, "text/plain; charset=utf-8", "404 Not Found".into())
}

fn content_type_of(name: &str) -> &'static str {
    let lower = name.to_ascii_lowercase();
    if lower.ends_with(".css") {
        "text/css; charset=utf-8"
    } else if lower.ends_with(".js") {
        "application/javascript; charset=utf-8"
    } else if lower.ends_with(".json") {
        "application/json; charset=utf-8"
    } else if lower.ends_with(".svg") {
        "image/svg+xml"
    } else if lower.ends_with(".png") {
        "image/png"
    } else if lower.ends_with(".ico") {
        "image/x-icon"
    } else {
        "text/plain; charset=utf-8"
    }
}

// ---------------------------------------------------------------------------
// 调度线程：按端点间隔抓取（启动即一轮）
// ---------------------------------------------------------------------------

fn scheduler_loop() {
    use std::time::{Duration, Instant};
    let names: Vec<String> = fetch::endpoints().iter().map(|(n, _, _)| n.to_string()).collect();
    let mut next_run: BTreeMap<String, Instant> =
        names.iter().map(|n| (n.clone(), Instant::now())).collect();
    loop {
        let now = Instant::now();
        for name in &names {
            if next_run.get(name).map(|t| *t <= now).unwrap_or(false) {
                let interval = Duration::from_secs(fetch::endpoint_interval(name) * 60);
                // 无论成功失败都按计划排下一次（失败只打日志，不打乱节奏）
                next_run.insert(name.clone(), Instant::now() + interval);
                match fetch_cycle(name) {
                    Ok(_) => {}
                    Err(e) => eprintln!("[调度] {} 抓取失败：{}", name, e),
                }
            }
        }
        std::thread::sleep(Duration::from_secs(15));
    }
}

/// 抓取单端点 → diff → 落盘（快照进环形缓冲、变更进统一日志）。
fn fetch_cycle(name: &str) -> Result<(), String> {
    let mut history: ServerHistory = state::load_server_history();
    let (status_code, _, snapshot_id, change_count) = fetch::process_endpoint_server(name, &mut history);
    state::save_server_history(&history).map_err(|e| e.to_string())?;
    eprintln!(
        "[调度] {}：status={} snapshot_id={} 变更 {} 条",
        name, status_code, snapshot_id, change_count
    );
    Ok(())
}

/// 读取请求体（保留给未来 POST 扩展使用）。
#[allow(dead_code)]
fn read_request_body(request: &mut tiny_http::Request) -> String {
    let mut body = String::new();
    let _ = request.as_reader().read_to_string(&mut body);
    body
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rewrite_handles_multibyte_keys() {
        let tpl = "<h1>{{ t['Hypixel SkyBlock API 变更监控台'] }}</h1>\
                   <p>{{ t['版本状态'] }}</p>\
                   <span>{{ t['Hypixel SkyBlock API 变更监控台'] }}</span>";
        let (rewritten, mapping) = rewrite_template_keys(tpl, "zh-cn");
        assert!(!rewritten.contains("t['"), "模板中不应再残留 t['...'] 写法");
        assert!(rewritten.contains("{{ t.k0 }}"));
        // 相同原文复用同一别名，共 2 个不同键
        assert_eq!(mapping.len(), 2);
        assert!(rewritten.contains("{{ t.k1 }}"));
    }

    #[test]
    fn rewrite_renders_via_tera() {
        let tpl = "<h1>{{ t['监控台'] }}</h1>";
        let (rewritten, mapping) = rewrite_template_keys(tpl, "zh-cn");
        let mut tera = tera::Tera::default();
        tera.add_raw_template("x", &rewritten).unwrap();
        let mut ctx = tera::Context::new();
        ctx.insert("t", &mapping);
        let html = tera.render("x", &ctx).unwrap();
        assert!(!html.contains("{{"));
        assert!(!html.is_empty());
    }
}
