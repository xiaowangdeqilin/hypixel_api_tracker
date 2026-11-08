//! HTTP 抓取 / 校验和 / 端点 diff（原 app/fetcher.py 的移植）。

use std::time::Duration;

use serde_json::{Map, Value};

use crate::bingo::{bingo_state, bingo_state_label, normalize_bingo};
use crate::firesale::{diff_sales, normalize_sales};
use crate::jsondiff::{diff_dict, diff_list_by_key, diff_values, Change};
use crate::labels::META_KEYS;
use crate::sha2_digest;

/// 端点配置：名称 -> (url, 本地服务抓取间隔分钟)。
pub fn endpoints() -> Vec<(&'static str, &'static str, u64)> {
    vec![
        ("collections", "https://api.hypixel.net/v2/resources/skyblock/collections", 120),
        ("skills", "https://api.hypixel.net/v2/resources/skyblock/skills", 120),
        ("items", "https://api.hypixel.net/v2/resources/skyblock/items", 360),
        ("bazaar", "https://api.hypixel.net/v2/skyblock/bazaar", 5),
        ("packs", "https://api.hypixel.net/v2/resources/packs", 10),
        ("bingo", "https://api.hypixel.net/v2/resources/skyblock/bingo", 30),
        // 特卖开始前官方可能随时调整时间/数量/价格，用最短间隔抓取。
        ("firesales", "https://api.hypixel.net/v2/skyblock/firesales", 5),
    ]
}

pub fn endpoint_url(name: &str) -> Option<&'static str> {
    endpoints().iter().find(|(n, _, _)| *n == name).map(|(_, u, _)| *u)
}

pub fn endpoint_interval(name: &str) -> u64 {
    endpoints().iter().find(|(n, _, _)| *n == name).map(|(_, _, i)| *i).unwrap_or(60)
}

/// 剥离顶层元数据，版本号变化不再被当成端点内容更新。
pub fn strip_meta(payload: &Value) -> Value {
    match payload {
        Value::Object(obj) => {
            let mut out = Map::new();
            for (key, value) in obj {
                if !META_KEYS.contains(&key.as_str()) {
                    out.insert(key.clone(), value.clone());
                }
            }
            Value::Object(out)
        }
        other => other.clone(),
    }
}

/// 抓取 JSON；失败返回错误信息（已白化，不含控制字符）。
pub fn fetch_json(url: &str, timeout_seconds: u64) -> Result<Value, String> {
    let mut request = ureq::get(url).timeout(Duration::from_secs(timeout_seconds));
    if let Ok(api_key) = std::env::var("HYPIXEL_API_KEY") {
        if !api_key.trim().is_empty() {
            request = request.query("key", api_key.trim());
        }
    }
    match request.call() {
        Ok(response) => response
            .into_json::<Value>()
            .map_err(|e| sanitize_error(&e.to_string())),
        Err(ureq::Error::Status(code, response)) => {
            let body = response.into_string().unwrap_or_default();
            Err(sanitize_error(&format!("HTTP {}: {}", code, body.chars().take(200).collect::<String>())))
        }
        Err(e) => Err(sanitize_error(&e.to_string())),
    }
}

/// 下载二进制（资源包 zip 用），返回字节与 HTTP 状态。
pub fn fetch_bytes(url: &str, timeout_seconds: u64) -> Result<Vec<u8>, String> {
    let response = ureq::get(url)
        .timeout(Duration::from_secs(timeout_seconds))
        .call()
        .map_err(|e| sanitize_error(&e.to_string()))?;
    let mut bytes = Vec::new();
    response
        .into_reader()
        .take(256 * 1024 * 1024) // 256MB 上限，防止异常响应撑爆内存
        .read_to_end(&mut bytes)
        .map_err(|e| sanitize_error(&e.to_string()))?;
    Ok(bytes)
}

use std::io::Read as _;

/// 错误信息白化：去掉控制字符并截断（默认 200 字符），避免异常细节污染存储与页面。
pub fn sanitize_error(text: &str) -> String {
    sanitize_error_limited(text, 200)
}

/// 错误信息白化（自定义上限）。
pub fn sanitize_error_limited(text: &str, limit: usize) -> String {
    let cleaned: String = text
        .chars()
        .map(|c| if c.is_control() || c == '\u{7f}' { ' ' } else { c })
        .collect();
    cleaned.trim().chars().take(limit).collect()
}

/// 只对业务内容计算校验和（canonical JSON：紧凑 + 键排序 + 非 ASCII 原样）。
///
/// Bingo 用归一化结构（进度变化不产生噪音）；firesales 按单期切分。
pub fn checksum_payload(payload: &Value, endpoint_name: &str) -> String {
    let body = if endpoint_name == "bingo" {
        normalize_bingo(payload)
    } else if endpoint_name == "firesales" {
        serde_json::json!({ "sales": normalize_sales(payload) })
    } else {
        strip_meta(payload)
    };
    checksum_value(&body)
}

/// 对任意 JSON 值做 canonical 校验和。
pub fn checksum_value(value: &Value) -> String {
    // serde_json::Value 默认用 BTreeMap 存对象 → 键自动排序；
    // to_string 输出紧凑形式且非 ASCII 不转义，与 Python 版语义一致。
    let raw = serde_json::to_string(value).unwrap_or_default();
    sha2_digest(raw.as_bytes())
}

/// 按端点生成变更（传入的 payload 已是「与上一次快照的原始响应」）。
///
/// 返回统一的 [`Change`] 列表；比较前会剥离元数据并做端点特化整形。
pub fn diff_for_endpoint(endpoint_name: &str, old: &Value, new: &Value) -> Vec<Change> {
    // Bingo 先归一化：剔除全服进度与数据刷新时间，时间转成北京时间字符串。
    if endpoint_name == "bingo" {
        let (old_bingo, new_bingo) = (normalize_bingo(old), normalize_bingo(new));
        let old_id = old_bingo.get("id").map(|v| v.to_string());
        let new_id = new_bingo.get("id").map(|v| v.to_string());
        if let (Some(oid), Some(nid)) = (old_id, new_id) {
            if oid != nid && oid != "null" && nid != "null" {
                // 换期时整批任务都换掉，逐条比对只会刷出几十条无意义增删。
                let old_desc = format!(
                    "第 {} 期 {}",
                    old_bingo["id"], old_bingo["name"].as_str().unwrap_or("")
                );
                let new_desc = format!(
                    "第 {} 期 {}",
                    new_bingo["id"], new_bingo["name"].as_str().unwrap_or("")
                );
                return vec![Change {
                    kind: "modified".into(),
                    path: "id".into(),
                    old: Value::String(old_desc.trim().to_string()),
                    new: Value::String(new_desc.trim().to_string()),
                    old_full: crate::jsondiff::log_value(&old_bingo, crate::labels::MAX_LOG_VALUE_CHARS),
                    new_full: crate::jsondiff::log_value(&new_bingo, crate::labels::MAX_LOG_VALUE_CHARS),
                }];
            }
        }
        return diff_dict(&old_bingo, &new_bingo, "");
    }

    // 特卖按「单期 = 一条记录」切分比对。
    if endpoint_name == "firesales" {
        return diff_sales(&normalize_sales(old), &normalize_sales(new))
            .into_iter()
            .map(|c| Change {
                kind: c.kind,
                path: c.path,
                old: c.old,
                new: c.new,
                old_full: c.old_full,
                new_full: c.new_full,
            })
            .collect();
    }

    let (old_clean, new_clean) = (strip_meta(old), strip_meta(new));

    // 集市只追踪商品增删（价格每秒都在变）。
    if endpoint_name == "bazaar" {
        let old_ids = product_ids(&old_clean);
        let new_ids = product_ids(&new_clean);
        let mut changes = Vec::new();
        let mut added: Vec<String> = new_ids.difference(&old_ids).cloned().collect();
        added.sort();
        let mut removed: Vec<String> = old_ids.difference(&new_ids).cloned().collect();
        removed.sort();
        for pid in added {
            changes.push(Change {
                kind: "added".into(),
                path: format!("products.{}", pid),
                old: Value::Null,
                new: Value::String("新增集市商品".into()),
                old_full: None,
                new_full: Some(pid),
            });
        }
        for pid in removed {
            changes.push(Change {
                kind: "removed".into(),
                path: format!("products.{}", pid),
                old: Value::String("集市商品".into()),
                new: Value::Null,
                old_full: Some(pid),
                new_full: None,
            });
        }
        return changes;
    }

    diff_values_clean(&old_clean, &new_clean)
}

/// 集市商品 id 集合（raw payload 的 products 键）。
fn product_ids(payload: &Value) -> std::collections::BTreeSet<String> {
    payload
        .get("products")
        .and_then(|p| p.as_object())
        .map(|obj| obj.keys().cloned().collect())
        .unwrap_or_default()
}

/// 通用 diff：字典递归、列表按键比对（items 用 id）、其余整体比较。
fn diff_values_clean(old: &Value, new: &Value) -> Vec<Change> {
    // items 端点：按物品 id 比对，增删可精确到单个物品
    if old.get("items").map(|v| v.is_array()).unwrap_or(false)
        && new.get("items").map(|v| v.is_array()).unwrap_or(false)
    {
        return diff_list_by_key(
            old["items"].as_array().unwrap(),
            new["items"].as_array().unwrap(),
            "id",
            "items",
        );
    }
    let changes = diff_dict(old, new, "");
    // 字典以外的整体变化（列表长度变化等）由 diff_dict 兜底为 modified
    let _ = diff_values; // 保留引用避免未用告警
    changes
}

/// 服务端抓取一轮：抓取 → 与上次比对 → 落库（快照 + 变更日志）。
///
/// 返回 (endpoint, status_code, snapshot_id, change_count)。
pub fn process_endpoint_server(
    endpoint_name: &str,
    history: &mut crate::state::ServerHistory,
) -> (i64, i64, u64, usize) {
    let url = match endpoint_url(endpoint_name) {
        Some(u) => u,
        None => return (500, 0, 0, 0),
    };
    let (payload, status_code) = match fetch_json(url, 20) {
        Ok(v) => (v, 200),
        Err(e) => (serde_json::json!({"error": e}), 500),
    };

    // 必须在写入新快照之前拿到上一次快照，否则 diff 永远不会执行。
    let previous = crate::state::latest_server_snapshot(history, endpoint_name).cloned();
    let checksum = checksum_payload(&payload, endpoint_name);

    let mut change_count = 0usize;
    let mut changes_log: Vec<Map<String, Value>> = Vec::new();
    if let Some(prev) = previous {
        let prev_checksum = prev.get("checksum").and_then(|v| v.as_str()).unwrap_or("");
        if prev_checksum != checksum {
            let old_payload = prev.get("payload").cloned().unwrap_or(Value::Null);
            let changes = diff_for_endpoint(endpoint_name, &old_payload, &payload);
            change_count = changes.len();
            let now = crate::timeutil::now_beijing();
            for c in changes {
                changes_log.push(crate::state::make_change_entry(
                    endpoint_name,
                    &c.kind,
                    &c.path,
                    c.old,
                    c.new,
                    c.old_full,
                    c.new_full,
                    &now,
                ));
            }
        }
    }

    let payload_json = serde_json::to_string(&payload).unwrap_or_default();
    let snapshot_id = crate::state::append_server_snapshot(history, endpoint_name, &payload_json, status_code, &checksum);

    if !changes_log.is_empty() {
        let mut all = crate::state::load_changes();
        crate::state::append_changes(&mut all, changes_log);
        let _ = crate::state::save_changes(&all);
    }

    (status_code, 0, snapshot_id, change_count)
}

/// Bingo 卡片用的状态快捷判断（构建摘要）。
pub fn bingo_quick_state(payload: &Value) -> (String, String, String, String) {
    let start = crate::timeutil::from_epoch_ms(payload.get("start").unwrap_or(&Value::Null));
    let end = crate::timeutil::from_epoch_ms(payload.get("end").unwrap_or(&Value::Null));
    let (state, _) = bingo_state(&start, &end, &crate::timeutil::now_beijing());
    let name = payload
        .get("name")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(String::from)
        .unwrap_or_else(|| format!("第 {} 期", payload.get("id").map(|v| v.to_string()).unwrap_or_else(|| "-".into())));
    let label = bingo_state_label(&state).to_string();
    (name, label, start, end)
}
