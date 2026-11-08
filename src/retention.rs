//! 变更记录的保留期限：只保留最近 30 天（原 app/retention.py 的移植）。
//!
//! 数据里的时间字段混用过两种写法（北京时间 `2026-11-05 12:54:54` 与
//! UTC ISO `2026-11-03T06:27:26Z`），统一交给 `timeutil` 归一后再比较。

use chrono::{Duration, TimeZone};
use serde_json::{Map, Value};

use crate::timeutil::{beijing_tz, format_time, now_beijing, parse_beijing};

pub const RETENTION_DAYS: i64 = 30;

/// 返回保留窗口的起点（北京时间字符串），可直接与数据里的时间串比较。
pub fn cutoff_beijing() -> String {
    let cutoff = chrono::Utc::now().with_timezone(&beijing_tz()) - Duration::days(RETENTION_DAYS);
    cutoff.format("%Y-%m-%d %H:%M:%S").to_string()
}

/// 判断一条记录的时间是否还在保留窗口内。
///
/// 时间缺失或无法解析的记录按「过期」处理。
pub fn is_within_days(value: &Value) -> bool {
    let text = match value {
        Value::String(s) if !s.trim().is_empty() => s.trim(),
        _ => return false,
    };
    let normalized = format_time(&Value::String(text.to_string()));
    match parse_beijing(&normalized) {
        Some(dt) => {
            let cutoff = chrono::Utc::now().with_timezone(&beijing_tz()) - Duration::days(RETENTION_DAYS);
            dt >= cutoff
        }
        None => false,
    }
}

/// 过滤出一组仍需要展示的变更记录（保持原有顺序）。
pub fn filter_recent(records: Vec<Map<String, Value>>, key: &str) -> Vec<Map<String, Value>> {
    records
        .into_iter()
        .filter(|record| record.get(key).map(is_within_days).unwrap_or(false))
        .collect()
}

/// 返回 (保留后的列表, 被移除的条数)。
pub fn prune_list(records: Vec<Map<String, Value>>, key: &str) -> (Vec<Map<String, Value>>, usize) {
    let total = records.len();
    let kept = filter_recent(records, key);
    let removed = total - kept.len();
    (kept, removed)
}

/// 给页面提示文案用的中文描述。
pub fn describe_window() -> String {
    format!("仅保留最近 {} 天", RETENTION_DAYS)
}

/// 当前北京时间（re-export 方便调用方使用）。
pub fn now() -> String {
    now_beijing()
}

/// 判断时间字符串是否在窗口内（字符串版本，供非 JSON 场景使用）。
pub fn within_window_text(value: Option<&str>) -> bool {
    match value {
        Some(v) => is_within_days(&Value::String(v.to_string())),
        None => false,
    }
}

/// 供测试：把字符串时间转成可比较的北京时间字符串。
pub fn normalize_time_text(value: &str) -> Option<String> {
    parse_beijing(&format_time(&Value::String(value.to_string())))
        .map(|dt| beijing_tz().from_utc_datetime(&dt.naive_utc()).format("%Y-%m-%d %H:%M:%S").to_string())
}
