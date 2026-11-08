//! 可选的多语言（i18n）支持（原 app/i18n.py 的移植）。
//!
//! 设计原则：
//! - **默认关闭**：不设置环境变量 `ENABLE_I18N` 时，页面与旧版完全一致；
//! - 开启方式：启动前设置 `ENABLE_I18N=1`（也接受 true/yes/on）；
//! - 译文存放在 `locales/<语言代码>.json`，**键为简体中文原文，值为译文**；
//! - 缺失或未翻译的条目自动回退为简体中文原文；
//! - RTL 语言（ar/he/fa/ur）会把 `<html dir>` 设为 `rtl`；
//! - 语言解析顺序：URL `?lang=` > Cookie `hypixel-lang` > 简体中文。
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Mutex;
use std::sync::OnceLock;

pub const DEFAULT_LANGUAGE: &str = "zh-CN";
pub const COOKIE_NAME: &str = "hypixel-lang";
pub const COOKIE_MAX_AGE: i64 = 365 * 24 * 60 * 60;
pub const RTL_LANGUAGES: [&str; 4] = ["ar", "he", "fa", "ur"];

/// 语言代码 -> 本语言自称（用于语言切换器展示，一律用母语书写）。
pub fn language_names() -> &'static BTreeMap<&'static str, &'static str> {
    static NAMES: OnceLock<BTreeMap<&'static str, &'static str>> = OnceLock::new();
    NAMES.get_or_init(|| {
        BTreeMap::from([
            ("zh-CN", "简体中文"),
            ("zh-TW", "繁體中文"),
            ("en", "English"),
            ("ja", "日本語"),
            ("ko", "한국어"),
            ("vi", "Tiếng Việt"),
            ("th", "ไทย"),
            ("id", "Bahasa Indonesia"),
            ("ms", "Bahasa Melayu"),
            ("fil", "Filipino"),
            ("hi", "हिन्दी"),
            ("bn", "বাংলা"),
            ("ur", "اردو"),
            ("ar", "العربية"),
            ("he", "עברית"),
            ("fa", "فارسی"),
            ("tr", "Türkçe"),
            ("fr", "Français"),
            ("de", "Deutsch"),
            ("es", "Español"),
            ("it", "Italiano"),
            ("pt", "Português"),
            ("nl", "Nederlands"),
            ("ru", "Русский"),
            ("uk", "Українська"),
            ("pl", "Polski"),
            ("cs", "Čeština"),
            ("hu", "Magyar"),
            ("ro", "Română"),
            ("el", "Ελληνικά"),
            ("sv", "Svenska"),
        ])
    })
}

/// 小写别名 -> 登记代码（大小写不敏感的归一化表）。
fn lowercase_aliases() -> &'static BTreeMap<&'static str, &'static str> {
    static ALIASES: OnceLock<BTreeMap<&'static str, &'static str>> = OnceLock::new();
    ALIASES.get_or_init(|| {
        BTreeMap::from([
            ("zh-hans", "zh-CN"),
            ("zh-hans-cn", "zh-CN"),
            ("zh-hant", "zh-TW"),
            ("zh-hant-tw", "zh-TW"),
            ("no", "sv"), // 挪威语归并到北欧语言（回退链相同）
            ("nb", "sv"),
            ("nn", "sv"),
            ("tl", "fil"),
            ("in", "id"),
            ("iw", "he"),
            ("pt-br", "pt"),
            ("pt-pt", "pt"),
        ])
    })
}

fn locales_dir() -> PathBuf {
    crate::state::base_dir().join("locales")
}

static PACK_CACHE: OnceLock<Mutex<BTreeMap<String, std::sync::Arc<BTreeMap<String, String>>>>> =
    OnceLock::new();

fn pack_cache() -> &'static Mutex<BTreeMap<String, std::sync::Arc<BTreeMap<String, String>>>> {
    PACK_CACHE.get_or_init(|| Mutex::new(BTreeMap::new()))
}

/// 多语言开关：环境变量 `ENABLE_I18N`，默认关闭。
pub fn is_enabled() -> bool {
    static ENABLED: OnceLock<bool> = OnceLock::new();
    *ENABLED.get_or_init(|| {
        let raw = std::env::var("ENABLE_I18N").unwrap_or_default();
        matches!(raw.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes" | "on")
    })
}

/// 返回可展示在切换器里的语言：{代码: 母语名称}，简体中文固定在最前。
pub fn available_languages() -> BTreeMap<String, String> {
    let mut langs = BTreeMap::new();
    langs.insert(
        DEFAULT_LANGUAGE.to_string(),
        language_names()[DEFAULT_LANGUAGE].to_string(),
    );
    for (code, name) in language_names() {
        if *code != DEFAULT_LANGUAGE && locales_dir().join(format!("{}.json", code)).exists() {
            langs.insert(code.to_string(), name.to_string());
        }
    }
    langs
}

/// 白名单校验：只接受登记过的语言代码（大小写、下划线不敏感）。
pub fn normalize_language(raw: Option<&str>) -> Option<String> {
    let raw = raw?;
    if raw.is_empty() || raw.len() > 16 {
        return None;
    }
    let mut candidate = raw.trim().replace('_', "-").to_ascii_lowercase();
    if let Some(mapped) = lowercase_aliases().get(candidate.as_str()) {
        candidate = mapped.to_string();
    }
    // 常见区域变体截断为主代码，如 "fr-CA" -> "fr"
    // （登记名本身含连字符的 zh-CN / zh-TW / pt-* 已在上面处理）
    if !language_names().contains_key(candidate.as_str()) {
        if let Some((main, _)) = candidate.split_once('-') {
            candidate = main.to_string();
        }
    }
    if language_names().contains_key(candidate.as_str())
        && available_languages().contains_key(&candidate)
    {
        return Some(candidate);
    }
    None
}

pub fn is_rtl(code: &str) -> bool {
    RTL_LANGUAGES.contains(&code)
}

/// 加载语言包；损坏的 JSON 一律视为空包（回退中文），绝不让页面 500。
pub fn load_pack(code: &str) -> std::sync::Arc<BTreeMap<String, String>> {
    if code == DEFAULT_LANGUAGE {
        return std::sync::Arc::new(BTreeMap::new());
    }
    let mut cache = pack_cache().lock().unwrap();
    if let Some(cached) = cache.get(code) {
        return cached.clone();
    }
    let mut pack = BTreeMap::new();
    let path = locales_dir().join(format!("{}.json", code));
    if let Ok(text) = std::fs::read_to_string(&path) {
        if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(obj) = parsed.as_object() {
                for (k, v) in obj {
                    if let Some(v) = v.as_str() {
                        pack.insert(k.clone(), v.to_string());
                    }
                }
            }
        }
    }
    let pack = std::sync::Arc::new(pack);
    cache.insert(code.to_string(), pack.clone());
    pack
}

/// 翻译函数：文案缺失 -> 回退简体中文原文；params 填充 {name} 占位符。
pub fn translate(code: &str, key: &str, params: Option<&BTreeMap<String, String>>) -> String {
    let pack = load_pack(code);
    let template = pack.get(key).cloned().unwrap_or_else(|| key.to_string());
    let Some(params) = params else {
        return template;
    };
    let mut out = template.clone();
    for (name, value) in params {
        out = out.replace(&format!("{{{}}}", name), value);
    }
    if pack.get(key).is_none() && out == template {
        // 译文缺失：对原文做占位符填充
        let mut fallback = key.to_string();
        for (name, value) in params {
            fallback = fallback.replace(&format!("{{{}}}", name), value);
        }
        return fallback;
    }
    out
}

/// 前端脚本使用的译文集合（直接复用整包，体量很小）。
pub fn js_pack(code: &str) -> serde_json::Value {
    serde_json::to_value(load_pack(code).as_ref()).unwrap_or(serde_json::json!({}))
}

/// 决定本次请求使用的语言：query > cookie > 默认。
pub fn resolve_language(query_lang: Option<&str>, cookie_lang: Option<&str>) -> String {
    if !is_enabled() {
        return DEFAULT_LANGUAGE.into();
    }
    normalize_language(query_lang)
        .or_else(|| normalize_language(cookie_lang))
        .unwrap_or_else(|| DEFAULT_LANGUAGE.into())
}

// ---------------------------------------------------------------------------
// 含 HTML 标记 / 占位符的长句：作为翻译键统一在这里定义，模板与语言包共用。
// ---------------------------------------------------------------------------

pub const CHANGES_HINT: &str = concat!(
    r#"按代码审核（diff）风格展示：<span class="legend-add">新增只标绿 +</span>、"#,
    r#"<span class="legend-del">删除只标红 -</span>；修改<strong>只显示发生变化的那一段</strong>"#,
    r#"（删除行标红、新增行标绿，未变化内容不输出）。端点 diff 已排除 version / lastUpdated 等"#,
    r#"元数据字段。<strong>只展示最近 {days} 天的增删改，更早的记录会自动清理。</strong>"#
);

pub const FIRESALE_EMPTY_HINT: &str = concat!(
    r#"数据来自 <code>/v2/skyblock/firesales</code>。当前还没有特卖数据，先运行 "#,
    r#"<code>cargo run --release --bin tracker-build</code> 生成一次即可。"#
);

pub const FIRESALE_HINT: &str = concat!(
    r#"价格以宝石（Gems）结算。特卖<strong>开始前官方可能随时调整时间、数量与价格</strong>，"#,
    r#"因此每次运行都会重新抓取并覆盖，不存在「查过就跳过」。一次响应里可能同时有多期特卖，"#,
    r#"每期独立成卡，按开始时间排序；主视图只展示<strong>即将开始与进行中</strong>的期次。"#
);

pub const BINGO_EMPTY_HINT: &str = concat!(
    r#"数据来自 <code>/v2/resources/skyblock/bingo</code>。当前还没有 Bingo 数据，先运行 "#,
    r#"<code>cargo run --release --bin tracker-build</code> 生成一次即可。"#
);

pub const BINGO_HINT: &str = concat!(
    r#"发布时间取自接口的 <code>lastUpdated</code>，「首次发现」是本仓库第一次抓到这一期的时刻；"#,
    r#"开始与结束时间取自 <code>start</code> / <code>end</code>。任务文案保留 Minecraft 颜色码"#,
    r#"并按官方配色渲染。<strong>全服进度只用于展示，不参与变更比对</strong>。"#
);

pub const BINGO_TIP: &str = concat!(
    r#"棋盘上从左上到右下的对角线是社区任务（有全服共享进度），其余均为个人任务；"#,
    r#"只有「未开始」和「进行中」显示倒计时，到点自动切换，已结束不再计时。点击任意格子查看完整详情。"#
);
