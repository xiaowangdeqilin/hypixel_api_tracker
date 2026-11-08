//! 秘密 Bingo（Secret Bingo）任务与提示的时间解锁逻辑。
//!
//! 秘密 Bingo 是官方随 Bingo 活动一起推出的隐藏任务：官方按时间逐步放出提示，
//! 每个任务共 3 条提示。提示内容无法从 API 获取，由人工维护在
//! `data/secret_bingo.json`，本项目负责：
//!
//! 1. 把每条提示与它的 `reveal_at`（北京时间）对齐成固定 3 个槽位；
//! 2. 按当前时间算出「已解锁 / 未解锁」，供页面渲染初始状态；
//! 3. 输出 `reveal_ms`，交给浏览器逐秒倒计时并在到点时自动解锁。
//!
//! 数据格式（`data/secret_bingo.json`）：
//!
//! ```json
//! {
//!   "event_id": 59,
//!   "goals": [
//!     {
//!       "id": "secret-1",
//!       "name": "秘密任务一",
//!       "hints": [
//!         { "text": "提示文本", "reveal_at": "2026-01-05 20:00:00" }
//!       ]
//!     }
//!   ]
//! }
//! ```
//!
//! `reveal_at` 支持北京时间串（`YYYY-MM-DD HH:MM:SS`）或 epoch 毫秒；
//! 无法解析时按「已解锁」处理，避免写错日期导致提示永久隐藏。

use std::path::PathBuf;

use serde_json::{json, Value};

use crate::state;
use crate::timeutil::{format_time, now_ms, to_epoch_ms};

/// 每个秘密任务固定 3 条提示。
pub const HINTS_PER_GOAL: usize = 3;

/// `data/secret_bingo.json` 的完整路径。
pub fn data_path() -> PathBuf {
    state::data_dir().join("secret_bingo.json")
}

/// 读取秘密 Bingo 配置；文件缺失、损坏或结构不对时返回空配置（页面显示预留位）。
pub fn load() -> Value {
    let raw = state::load_json(&data_path()).unwrap_or(Value::Null);
    normalize_raw(&raw)
}

/// 把任意输入收敛成 `{event_id, goals: [...]}` 结构。
fn normalize_raw(raw: &Value) -> Value {
    let obj = raw.as_object();
    let goals: Vec<Value> = obj
        .and_then(|o| o.get("goals"))
        .and_then(|g| g.as_array())
        .map(|arr| arr.iter().filter(|g| g.is_object()).cloned().collect())
        .unwrap_or_default();
    json!({
        "event_id": obj.and_then(|o| o.get("event_id")).cloned().unwrap_or(Value::Null),
        "goals": goals,
    })
}

/// 单个提示槽：文本 + 解锁时间。
struct Slot {
    text: String,
    reveal_at: String,
    reveal_ms: Option<i64>,
}

fn read_slot(hint: &Value) -> Slot {
    let text = hint
        .get("text")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let reveal_raw = hint.get("reveal_at").cloned().unwrap_or(Value::Null);
    Slot {
        text,
        reveal_at: format_time(&reveal_raw),
        reveal_ms: to_epoch_ms(&reveal_raw),
    }
}

/// 生成展示视图。
///
/// - 每个任务固定 [`HINTS_PER_GOAL`] 个槽位：配置不足的补空槽，超出的截断；
/// - 槽位按解锁时间升序排列（「依次给出提示」），无时间的排在最后；
/// - `unlocked` 由 `now` 与 `reveal_ms` 比较得出，`reveal_ms` 交给前端倒计时。
pub fn view(raw: &Value, now: i64) -> Value {
    let normalized = normalize_raw(raw);
    let event_id = normalized.get("event_id").cloned().unwrap_or(Value::Null);
    let raw_goals = normalized
        .get("goals")
        .and_then(|g| g.as_array())
        .cloned()
        .unwrap_or_default();

    let mut goals: Vec<Value> = Vec::new();
    for (idx, goal) in raw_goals.iter().enumerate() {
        let gobj = goal.as_object().cloned().unwrap_or_default();
        let name = gobj
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        let gid = gobj
            .get("id")
            .map(|v| match v {
                Value::String(s) => s.clone(),
                other => other.to_string().trim_matches('"').to_string(),
            })
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| format!("secret-{}", idx + 1));

        let mut slots: Vec<Slot> = gobj
            .get("hints")
            .and_then(|h| h.as_array())
            .map(|arr| {
                arr.iter()
                    .filter(|h| h.is_object())
                    .map(read_slot)
                    .filter(|s| !s.text.is_empty() || s.reveal_ms.is_some())
                    .collect()
            })
            .unwrap_or_default();
        // 依次给出：按解锁时间升序，时间未知的排最后（保持原顺序）
        slots.sort_by_key(|s| s.reveal_ms.unwrap_or(i64::MAX));

        let mut hints: Vec<Value> = Vec::new();
        let mut unlocked_count = 0usize;
        let mut next_reveal: Option<i64> = None;
        for slot_idx in 0..HINTS_PER_GOAL {
            let index = slot_idx + 1;
            match slots.get(slot_idx) {
                Some(slot) => {
                    // 没有时间的提示视为立即可见
                    let unlocked = slot.reveal_ms.map(|ms| now >= ms).unwrap_or(true);
                    if unlocked {
                        unlocked_count += 1;
                    } else if let Some(ms) = slot.reveal_ms {
                        next_reveal = Some(next_reveal.map_or(ms, |cur: i64| cur.min(ms)));
                    }
                    hints.push(json!({
                        "index": index,
                        "text": slot.text,
                        "reveal_at": slot.reveal_at,
                        "reveal_ms": slot.reveal_ms,
                        "unlocked": unlocked,
                        "populated": true,
                    }));
                }
                None => hints.push(json!({
                    "index": index,
                    "text": "",
                    "reveal_at": "-",
                    "reveal_ms": Value::Null,
                    "unlocked": false,
                    "populated": false,
                })),
            }
        }

        goals.push(json!({
            "index": idx + 1,
            "index_str": format!("{:02}", idx + 1),
            "id": gid,
            "name": name,
            "has_name": !name.is_empty(),
            "hints": hints,
            "unlocked_count": unlocked_count,
            "total_hints": HINTS_PER_GOAL,
            "configured_hints": slots.len().min(HINTS_PER_GOAL),
            "all_unlocked": unlocked_count >= HINTS_PER_GOAL,
            "next_reveal_ms": next_reveal,
        }));
    }

    let goal_count = goals.len();
    let unlocked_hints: usize = goals
        .iter()
        .map(|g| g.get("unlocked_count").and_then(|v| v.as_u64()).unwrap_or(0) as usize)
        .sum();

    json!({
        "event_id": event_id,
        "configured": goal_count > 0,
        "goal_count": goal_count,
        "hints_per_goal": HINTS_PER_GOAL,
        "total_hints": goal_count * HINTS_PER_GOAL,
        "unlocked_hints": unlocked_hints,
        "goals": goals,
    })
}

/// 便捷入口：读取配置并按当前时间生成视图。
pub fn current_view() -> Value {
    view(&load(), now_ms())
}

/// 一组占位任务（用于「预留位置」的空态展示）。
pub fn placeholder_view(count: usize) -> Value {
    let goals: Vec<Value> = (0..count)
        .map(|i| {
            json!({
                "index": i + 1,
                "index_str": format!("{:02}", i + 1),
                "id": format!("placeholder-{}", i + 1),
                "name": "",
                "has_name": false,
                "hints": (0..HINTS_PER_GOAL).map(|h| json!({
                    "index": h + 1,
                    "text": "",
                    "reveal_at": "-",
                    "reveal_ms": Value::Null,
                    "unlocked": false,
                    "populated": false,
                })).collect::<Vec<_>>(),
                "unlocked_count": 0,
                "total_hints": HINTS_PER_GOAL,
                "configured_hints": 0,
                "all_unlocked": false,
                "next_reveal_ms": Value::Null,
            })
        })
        .collect();
    json!({
        "event_id": Value::Null,
        "configured": false,
        "goal_count": count,
        "hints_per_goal": HINTS_PER_GOAL,
        "total_hints": count * HINTS_PER_GOAL,
        "unlocked_hints": 0,
        "goals": goals,
    })
}
