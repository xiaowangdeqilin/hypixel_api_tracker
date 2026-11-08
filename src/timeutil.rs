//! 统一时间处理：项目内所有对外展示与写入的时间一律为北京时间（UTC+8）。
//!
//! 兼容三类历史输入：
//! - `2026-11-02T21:57:06Z`（UTC ISO + Z 后缀）
//! - `2026-11-03T06:27:24`（无时区后缀的 UTC 字符串）
//! - `2026-11-03 14:30:00`（本项目新写入的北京时间字符串）
//!
//! 新写入的数据一律由 [`now_beijing`] 生成（空格分隔、无时区后缀）。

use chrono::{DateTime, FixedOffset, TimeZone};
use serde_json::Value;

/// 北京时间固定偏移（UTC+8）。
pub fn beijing_tz() -> FixedOffset {
    FixedOffset::east_opt(8 * 3600).expect("UTC+8 is a valid offset")
}

/// 当前北京时间展示串，例如 `2026-11-03 14:30:00`。
pub fn now_beijing() -> String {
    chrono::Utc::now()
        .with_timezone(&beijing_tz())
        .format("%Y-%m-%d %H:%M:%S")
        .to_string()
}

/// 当前毫秒时间戳。
pub fn now_ms() -> i64 {
    chrono::Utc::now().timestamp_millis()
}

fn format_dt(dt: &DateTime<FixedOffset>) -> String {
    dt.format("%Y-%m-%d %H:%M:%S").to_string()
}

/// 把毫秒时间戳转成北京时间展示串；无效值返回 `-`。
pub fn from_epoch_ms(value: &Value) -> String {
    let ms = match value {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(s) => s.trim().parse::<i64>().ok(),
        _ => None,
    };
    match ms {
        Some(ms) if ms > 0 => match beijing_tz().timestamp_opt(ms / 1000, 0) {
            chrono::LocalResult::Single(dt) => format_dt(&dt),
            _ => "-".into(),
        },
        _ => "-".into(),
    }
}

/// 把毫秒时间戳（JSON 值或北京/ISO 字符串）统一成 epoch 毫秒。
pub fn to_epoch_ms(value: &Value) -> Option<i64> {
    match value {
        Value::Number(n) => {
            let ms = n.as_i64().or_else(|| n.as_f64().map(|f| f as i64))?;
            (ms > 0).then_some(ms)
        }
        Value::String(s) => {
            let text = s.trim();
            if let Ok(ms) = text.parse::<i64>() {
                return (ms > 0).then_some(ms);
            }
            parse_beijing(text).map(|dt| dt.timestamp_millis())
        }
        _ => None,
    }
}

/// 把北京时间 / ISO 时间字符串解析成带时区的 `DateTime`。
///
/// - `YYYY-MM-DD HH:MM:SS`（本项目新写入格式）→ 北京时间；
/// - ISO 8601 带 `Z` / 显式偏移 → 按所带时区换算；
/// - 无时区后缀但含 `T` 的旧数据 → 按 UTC 解释。
pub fn parse_beijing(value: &str) -> Option<DateTime<FixedOffset>> {
    let text = value.trim();
    if text.is_empty() || text == "-" {
        return None;
    }
    // 展示格式：YYYY-MM-DD HH:MM:SS（北京时间）
    if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(text, "%Y-%m-%d %H:%M:%S") {
        // 该格式即北京时间墙上时刻，直接打 +08:00 标签（不做时区换算）
        return match beijing_tz().from_local_datetime(&dt) {
            chrono::LocalResult::Single(dt) => Some(dt),
            _ => None,
        };
    }
    // ISO 8601：带 Z 或显式偏移
    let normalized = text.strip_suffix('Z').unwrap_or(text);
    if let Ok(dt) = DateTime::parse_from_rfc3339(&format!("{}Z", normalized.trim_end_matches('Z'))) {
        return Some(dt.with_timezone(&beijing_tz()));
    }
    // 无时区后缀但含 T 的旧数据 → 按 UTC 解析
    if let Ok(naive) = chrono::NaiveDateTime::parse_from_str(text, "%Y-%m-%dT%H:%M:%S") {
        // 旧数据约定：无时区后缀但含 T 的字符串是 UTC
        return Some(beijing_tz().from_utc_datetime(&naive));
    }
    if let Ok(date) = chrono::NaiveDate::parse_from_str(text, "%Y-%m-%d") {
        let naive = date.and_hms_opt(0, 0, 0)?;
        return Some(beijing_tz().from_utc_datetime(&naive));
    }
    None
}

/// 把任意历史时间字符串转换成北京时间展示格式（无法解析时原样返回）。
pub fn format_time(value: &Value) -> String {
    let text = match value {
        Value::String(s) => s.trim().to_string(),
        // 纯数字按毫秒时间戳处理（Hypixel 惯例）
        Value::Number(_) => return from_epoch_ms(value),
        _ => return "-".into(),
    };
    if text.is_empty() {
        return "-".into();
    }
    // 已是北京时间展示格式 → 原样返回
    if text.len() == 19
        && text.as_bytes().get(4) == Some(&b'-')
        && text.as_bytes().get(10) == Some(&b' ')
    {
        return text.to_string();
    }
    match parse_beijing(&text) {
        Some(dt) => format_dt(&dt),
        None => text,
    }
}

/// 供展示层使用：任意 JSON 时间值 → 北京时间字符串（容错）。
pub fn format_time_str(value: Option<&str>) -> String {
    match value {
        Some(s) => format_time(&Value::String(s.to_string())),
        None => "-".into(),
    }
}
