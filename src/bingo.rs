//! Bingo 任务的纯数据处理逻辑（原 app/bingo.py 的移植）。
//!
//! 约定：
//! - `start` / `end` / `lastUpdated` 都是 epoch 毫秒，统一转北京时间；
//! - 每个 goal 的 `progress` 是全服累计进度，每次抓取都会增长，**不参与变更比对**；
//! - `lastUpdated` 只表示官方数据刷新时间，页面单独展示，不进 diff。

use chrono::TimeZone;
use serde_json::{json, Map, Value};

use crate::mctext::strip_mc_codes;
use crate::timeutil::{beijing_tz, format_time, from_epoch_ms, parse_beijing, to_epoch_ms};

pub fn bingo_state_label(state: &str) -> &'static str {
    match state {
        "upcoming" => "未开始",
        "active" => "进行中",
        "ended" => "已结束",
        _ => "未知",
    }
}

/// 把目标数值压缩成 `10M` / `72.5K` 这类便于阅读的写法。
pub fn format_amount(value: &Value) -> String {
    let num = match value {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse::<f64>().ok(),
        _ => None,
    };
    let Some(num) = num else {
        return match value {
            Value::String(s) => s.clone(),
            Value::Null => "空".into(),
            other => other.to_string(),
        };
    };
    for (unit, div) in [("B", 1_000_000_000.0), ("M", 1_000_000.0), ("K", 1_000.0)] {
        if num.abs() >= div {
            let scaled = num / div;
            let text = format!("{:.2}", scaled);
            let text = text.trim_end_matches('0').trim_end_matches('.');
            return format!("{}{}", text, unit);
        }
    }
    if num == num.trunc() && num.abs() < 1e15 {
        format_thousands(num as i64)
    } else {
        let text = num.to_string();
        text
    }
}

/// 千位分隔（12 345 -> "12,345"）。
pub fn format_thousands(value: i64) -> String {
    let sign = if value < 0 { "-" } else { "" };
    let digits = value.abs().to_string();
    let mut out = String::new();
    for (i, ch) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(ch);
    }
    format!("{}{}", sign, out)
}

fn humanize_delta(seconds: i64) -> String {
    let seconds = seconds.abs();
    if seconds < 60 {
        return format!("{} 秒", seconds);
    }
    let minutes = seconds / 60;
    if minutes < 60 {
        return format!("{} 分钟", minutes);
    }
    let (hours, minutes) = (minutes / 60, minutes % 60);
    if hours < 24 {
        if minutes > 0 {
            return format!("{} 小时 {} 分钟", hours, minutes);
        }
        return format!("{} 小时", hours);
    }
    let (days, hours) = (hours / 24, hours % 24);
    if hours > 0 {
        format!("{} 天 {} 小时", days, hours)
    } else {
        format!("{} 天", days)
    }
}

/// 判断本期处于未开始 / 进行中 / 已结束，返回 (state, 中文剩余描述)。
pub fn bingo_state(start: &str, end: &str, now: &str) -> (String, String) {
    let (Some(now_dt), Some(start_dt), Some(end_dt)) =
        (parse_beijing(now), parse_beijing(start), parse_beijing(end))
    else {
        return ("unknown".into(), "-".into());
    };
    if now_dt < start_dt {
        let secs = (start_dt - now_dt).num_seconds().max(0);
        return ("upcoming".into(), format!("距开始还有 {}", humanize_delta(secs)));
    }
    if now_dt > end_dt {
        // 已结束的期次不再计时，只保留「已结束」状态徽章。
        return ("ended".into(), "-".into());
    }
    let secs = (end_dt - now_dt).num_seconds().max(0);
    ("active".into(), format!("剩余 {}", humanize_delta(secs)))
}

/// 把接口里的 epoch 毫秒（或已格式化的字符串）统一成北京时间展示串。
fn normalize_time(value: &Value) -> String {
    match value {
        Value::Number(_) => from_epoch_ms(value),
        Value::String(s) if !s.trim().is_empty() => format_time(value),
        _ => "-".into(),
    }
}

/// 构造用于变更比对与快照的稳定结构（剔除 progress 与 lastUpdated）。
pub fn normalize_bingo(payload: &Value) -> Value {
    let empty = Map::new();
    let obj = payload.as_object().unwrap_or(&empty);
    let mut goal_map = Map::new();
    if let Some(goals) = obj.get("goals").and_then(|g| g.as_array()) {
        for goal in goals {
            let Some(gobj) = goal.as_object() else { continue };
            let gid = gobj
                .get("id")
                .map(|v| match v {
                    Value::String(s) => s.clone(),
                    other => other.to_string(),
                })
                .or_else(|| gobj.get("name").map(|v| v.as_str().unwrap_or("unknown").to_string()))
                .unwrap_or_else(|| "unknown".into());
            let mut entry = Map::new();
            entry.insert(
                "name".into(),
                gobj.get("name").cloned().unwrap_or_else(|| Value::String("-".into())),
            );
            entry.insert(
                "lore".into(),
                Value::String(strip_mc_codes(gobj.get("lore").and_then(|v| v.as_str()))),
            );
            if let Some(ra) = gobj.get("requiredAmount") {
                entry.insert("required_amount".into(), ra.clone());
            }
            if let Some(tiers) = gobj.get("tiers").and_then(|t| t.as_array()) {
                if !tiers.is_empty() {
                    let joined = tiers
                        .iter()
                        .map(format_amount)
                        .collect::<Vec<_>>()
                        .join(" / ");
                    entry.insert("tiers".into(), Value::String(joined));
                }
            }
            goal_map.insert(gid, Value::Object(entry));
        }
    }
    json!({
        "id": obj.get("id").cloned().unwrap_or(Value::Null),
        "name": obj.get("name").cloned().unwrap_or_else(|| Value::String("-".into())),
        "modifier": obj.get("modifier").cloned().unwrap_or_else(|| Value::String("-".into())),
        "start": normalize_time(obj.get("start").unwrap_or(&Value::Null)),
        "end": normalize_time(obj.get("end").unwrap_or(&Value::Null)),
        "goals": goal_map,
    })
}

/// 计算单个任务的全服进度；没有 tiers 或 progress 时返回 None。
pub fn goal_progress(goal: &Value) -> Option<Value> {
    let tiers = goal.get("tiers")?.as_array()?;
    if tiers.is_empty() {
        return None;
    }
    let progress = goal.get("progress")?.as_f64()?;
    let numeric: Vec<f64> = tiers.iter().filter_map(|t| t.as_f64()).collect();
    if numeric.is_empty() {
        return None;
    }
    let top = numeric.iter().cloned().fold(f64::MIN, f64::max);
    if top == 0.0 {
        return None;
    }
    // 社区任务最多 5 档（满级）；到达最高档后进度不再增长，
    // 展示值同样封顶，避免出现「30M / 10M」这类超过满级的数字。
    let capped = progress.min(top);
    let ratio = (progress / top).clamp(0.0, 1.0);
    let reached = numeric.iter().filter(|&&t| progress >= t).count();
    let percent = (ratio * 100.0 * 10.0).round() / 10.0;
    Some(json!({
        "ratio": ratio,
        "percent": percent,
        "reached": reached,
        "total": numeric.len(),
        "is_maxed": reached >= numeric.len(),
        "progress": progress,
        "progress_text": format_amount(&Value::from(capped)),
        "top": top,
        "top_text": format_amount(&Value::from(top)),
    }))
}

/// 把 tiers / requiredAmount 转成一行可读的目标描述。
pub fn requirement_text(goal: &Value) -> String {
    if let Some(tiers) = goal.get("tiers").and_then(|t| t.as_array()) {
        if !tiers.is_empty() {
            return tiers
                .iter()
                .map(format_amount)
                .collect::<Vec<_>>()
                .join(" / ");
        }
    }
    match goal.get("requiredAmount") {
        Some(v) if !v.is_null() => format_amount(v),
        _ => String::new(),
    }
}

/// 生成展示用的 Bingo 数据（保留原始 goals、颜色码与全服进度）。
///
/// `first_seen` 记录第一次抓到这一期的时刻；同一期再次抓取时沿用已有记录。
pub fn bingo_view(payload: &Value, generated_at: &str, previous: Option<&Value>) -> Value {
    let empty = Map::new();
    let obj = payload.as_object().unwrap_or(&empty);
    let mut goals: Vec<Value> = obj
        .get("goals")
        .and_then(|g| g.as_array())
        .map(|arr| arr.iter().filter(|g| g.is_object()).cloned().collect())
        .unwrap_or_default();

    let bingo_id = obj.get("id").cloned().unwrap_or(Value::Null);
    let start = from_epoch_ms(obj.get("start").unwrap_or(&Value::Null));
    let end = from_epoch_ms(obj.get("end").unwrap_or(&Value::Null));
    let start_ms = to_epoch_ms(obj.get("start").unwrap_or(&Value::Null));
    let end_ms = to_epoch_ms(obj.get("end").unwrap_or(&Value::Null));
    let (state, remaining) = bingo_state(&start, &end, generated_at);

    let mut first_seen = generated_at.to_string();
    if let Some(prev) = previous {
        let prev_id = prev.get("id").map(|v| v.to_string());
        if prev_id.as_deref() == Some(bingo_id.to_string().as_str()) {
            if let Some(fs) = prev.get("first_seen").and_then(|v| v.as_str()) {
                if !fs.is_empty() {
                    first_seen = fs.to_string();
                }
            }
        }
    }

    // 社区/个人任务识别：5×5 棋盘上只有左上→右下对角线（第 r 行第 r 列）是社区任务，
    // 其余均为个人任务。任务数量不是 25 时退回「带档位（tiers）即为社区任务」的旧规则。
    let board_mode = goals.len() == 25;
    for (idx, goal) in goals.iter_mut().enumerate() {
        // 先做只读计算，再取可变借用（避免借用冲突）
        let progress = goal_progress(goal);
        let lore_lines: Vec<String> = match goal.get("fullLore").and_then(|l| l.as_array()) {
            Some(arr) if !arr.is_empty() => {
                arr.iter().filter_map(|l| l.as_str().map(String::from)).collect()
            }
            _ => goal
                .get("lore")
                .and_then(|l| l.as_str())
                .filter(|l| !l.is_empty())
                .map(|l| vec![l.to_string()])
                .unwrap_or_default(),
        };
        let requirement = requirement_text(goal);
        // 社区/个人任务识别（board_mode 已在循环前计算，避免借用冲突）
        let is_community = if board_mode {
            idx / 5 == idx % 5
        } else {
            goal.get("tiers").and_then(|t| t.as_array()).is_some_and(|t| !t.is_empty())
        };
        let Some(gobj) = goal.as_object_mut() else { continue };
        gobj.insert("is_community".into(), Value::Bool(is_community));
        match progress {
            Some(p) => {
                gobj.insert("progress_info".into(), p);
            }
            None => {
                gobj.remove("progress_info");
            }
        }
        gobj.insert(
            "lore_lines".into(),
            Value::Array(lore_lines.into_iter().map(Value::String).collect()),
        );
        gobj.insert("requirement_text".into(), Value::String(requirement));
    }

    json!({
        "generated_at": generated_at,
        "id": bingo_id,
        "name": obj.get("name").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).unwrap_or("-"),
        "modifier": obj.get("modifier").and_then(|v| v.as_str()).unwrap_or("-"),
        "start": start,
        "end": end,
        "start_ms": start_ms.map(Value::from).unwrap_or(Value::Null),
        "end_ms": end_ms.map(Value::from).unwrap_or(Value::Null),
        "published_at": from_epoch_ms(obj.get("lastUpdated").unwrap_or(&Value::Null)),
        "first_seen": first_seen,
        "state": state,
        "state_label": bingo_state_label(&state),
        "remaining": remaining,
        "goal_count": goals.len(),
        "goals": goals,
    })
}

/// 供测试：时间差人话描述。
pub fn humanize_delta_pub(seconds: i64) -> String {
    humanize_delta(seconds)
}

/// 供测试：确认 chrono 时区可用。
pub fn _tz_check() -> bool {
    beijing_tz().timestamp_opt(0, 0).single().is_some()
}
