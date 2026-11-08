//! 限时特卖（Firesales）的归一化、状态与变更比对（原 app/firesale.py 的移植）。
//!
//! 一次响应里可能同时包含多期特卖，因此按「单期 = 一条记录」切分，
//! 以 `item_id + start` 作为唯一键。特卖开始前官方随时可能调整时间/数量/价格，
//! 构建与服务端每次运行都重新抓取，不存在「查过就跳过」。

use serde_json::{json, Map, Value};

use crate::timeutil::{from_epoch_ms, now_ms, to_epoch_ms};

pub fn sale_state_label(state: &str) -> &'static str {
    match state {
        "upcoming" => "即将开始",
        "active" => "进行中",
        "ended" => "已结束",
        _ => "未知",
    }
}

pub const CURRENCY_LABEL: &str = "宝石";

/// `WITHER_GOGGLES_LAGOON_SPECTRE` -> "Wither Goggles Lagoon Spectre"。
pub fn prettify_item_id(item_id: &Value) -> String {
    let text = match item_id {
        Value::String(s) => s.trim().to_string(),
        Value::Null => String::new(),
        other => other.to_string(),
    };
    if text.is_empty() {
        return "未知道具".into();
    }
    text.split('_')
        .filter(|p| !p.is_empty())
        .map(|p| {
            let mut chars = p.chars();
            match chars.next() {
                Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// 950 -> "950"；大数加千位分隔符，非法值返回 "-"。
pub fn format_amount(value: &Value) -> String {
    match value {
        Value::Number(n) => {
            let as_i64 = n.as_i64().or_else(|| n.as_f64().map(|f| f as i64));
            match as_i64 {
                Some(v) => crate::bingo::format_thousands(v),
                None => "-".into(),
            }
        }
        _ => "-".into(),
    }
}

fn number_or_none(value: Option<&Value>) -> Option<Value> {
    match value {
        Some(Value::Number(n)) => Some(Value::Number(n.clone())),
        _ => None,
    }
}

/// 按毫秒时间戳判断单期特卖状态，返回 (state, 中文标签)。
pub fn sale_state(start_ms: Option<i64>, end_ms: Option<i64>, now: i64) -> (String, String) {
    let (Some(start), Some(end)) = (start_ms, end_ms) else {
        return ("unknown".into(), sale_state_label("unknown").into());
    };
    if now < start {
        return ("upcoming".into(), sale_state_label("upcoming").into());
    }
    if now > end {
        return ("ended".into(), sale_state_label("ended").into());
    }
    ("active".into(), sale_state_label("active").into())
}

/// 把接口响应整理成稳定的销售列表（按开始时间、物品 id 排序）。
pub fn normalize_sales(payload: &Value) -> Vec<Map<String, Value>> {
    let raw = payload.get("sales").and_then(|s| s.as_array());
    let mut sales: Vec<Map<String, Value>> = Vec::new();
    for entry in raw.into_iter().flatten() {
        let Some(obj) = entry.as_object() else { continue };
        let start_ms = to_epoch_ms(obj.get("start").unwrap_or(&Value::Null));
        let end_ms = to_epoch_ms(obj.get("end").unwrap_or(&Value::Null));
        let item_id = obj
            .get("item_id")
            .and_then(|v| v.as_str())
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "未知道具".into());

        let mut item = Map::new();
        item.insert("item_id".into(), Value::String(item_id));
        item.insert(
            "start_ms".into(),
            start_ms.map(Value::from).unwrap_or(Value::Null),
        );
        item.insert(
            "end_ms".into(),
            end_ms.map(Value::from).unwrap_or(Value::Null),
        );
        item.insert(
            "start".into(),
            Value::String(from_epoch_ms(obj.get("start").unwrap_or(&Value::Null))),
        );
        item.insert(
            "end".into(),
            Value::String(from_epoch_ms(obj.get("end").unwrap_or(&Value::Null))),
        );
        item.insert("amount".into(), number_or_none(obj.get("amount")).unwrap_or(Value::Null));
        item.insert("price".into(), number_or_none(obj.get("price")).unwrap_or(Value::Null));
        // 持久化数据里已解析过可读名称，原样保留。
        if let Some(name) = obj.get("name").and_then(|v| v.as_str()) {
            item.insert("name".into(), Value::String(name.to_string()));
        }
        sales.push(item);
    }
    sales.sort_by(|a, b| {
        let sa = a.get("start_ms").and_then(|v| v.as_i64()).unwrap_or(0);
        let sb = b.get("start_ms").and_then(|v| v.as_i64()).unwrap_or(0);
        sa.cmp(&sb).then_with(|| {
            let ia = a.get("item_id").and_then(|v| v.as_str()).unwrap_or("");
            let ib = b.get("item_id").and_then(|v| v.as_str()).unwrap_or("");
            ia.cmp(ib)
        })
    });
    sales
}

fn sale_get_str<'a>(sale: &'a Map<String, Value>, key: &str) -> &'a str {
    sale.get(key).and_then(|v| v.as_str()).unwrap_or("")
}

/// 单期特卖的唯一键：同一物品可能分期上架，start 参与区分。
pub fn sale_key(sale: &Map<String, Value>) -> String {
    let start = sale.get("start_ms").and_then(|v| v.as_i64()).unwrap_or(0);
    format!("{}@{}", sale_get_str(sale, "item_id"), start)
}

/// 从 items 端点快照构造 id -> name 映射。
pub fn build_item_names(items_payload: Option<&Value>) -> Map<String, Value> {
    let mut names = Map::new();
    let Some(items) = items_payload.and_then(|p| p.get("items")).and_then(|i| i.as_array()) else {
        return names;
    };
    for item in items {
        if let (Some(id), Some(name)) =
            (item.get("id").and_then(|v| v.as_str()), item.get("name").and_then(|v| v.as_str()))
        {
            names.insert(id.to_string(), Value::String(name.to_string()));
        }
    }
    names
}

/// 一行可读摘要，用于变更日志：物品 ×数量 @价格（时间窗口）。
pub fn describe_sale(sale: &Map<String, Value>) -> String {
    let name = sale
        .get("name")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(String::from)
        .unwrap_or_else(|| prettify_item_id(sale.get("item_id").unwrap_or(&Value::Null)));
    let price = format_amount(sale.get("price").unwrap_or(&Value::Null));
    let amount = format_amount(sale.get("amount").unwrap_or(&Value::Null));
    let start = sale.get("start").and_then(|v| v.as_str()).unwrap_or("-");
    let end = sale.get("end").and_then(|v| v.as_str()).unwrap_or("-");
    format!(
        "{} ×{} @ {} {}（{} → {}）",
        name, amount, price, CURRENCY_LABEL, start, end
    )
}

/// 单条特卖变更（与端点 diff 相同结构的条目）。
pub struct SaleChange {
    pub kind: String,
    pub path: String,
    pub old: Value,
    pub new: Value,
    pub old_full: Option<String>,
    pub new_full: Option<String>,
}

fn to_json_str(v: &Value) -> String {
    serde_json::to_string(v).unwrap_or_default()
}

/// 按 sale_key 比对两期销售列表。
///
/// 开始时间参与 key，官方推迟开卖体现为「旧期删除 + 新期新增」。
pub fn diff_sales(
    old: &[Map<String, Value>],
    new: &[Map<String, Value>],
) -> Vec<SaleChange> {
    let to_map = |list: &[Map<String, Value>]| -> std::collections::BTreeMap<String, Map<String, Value>> {
        list.iter()
            .map(|s| (sale_key(s), s.clone()))
            .collect()
    };
    let old_map = to_map(old);
    let new_map = to_map(new);
    let mut changes = Vec::new();

    for key in new_map.keys().filter(|k| !old_map.contains_key(*k)) {
        let mut sale = new_map[key].clone();
        sale.insert(
            "name".into(),
            Value::String(prettify_item_id(sale.get("item_id").unwrap_or(&Value::Null))),
        );
        changes.push(SaleChange {
            kind: "added".into(),
            path: format!("sales.{}", key),
            old: Value::Null,
            new: Value::String(format!("新增特卖：{}", describe_sale(&sale))),
            old_full: None,
            new_full: Some(to_json_str(&Value::Object(sale))),
        });
    }
    for key in old_map.keys().filter(|k| !new_map.contains_key(*k)) {
        let mut sale = old_map[key].clone();
        sale.insert(
            "name".into(),
            Value::String(prettify_item_id(sale.get("item_id").unwrap_or(&Value::Null))),
        );
        changes.push(SaleChange {
            kind: "removed".into(),
            path: format!("sales.{}", key),
            old: Value::String(format!("移除特卖：{}", describe_sale(&sale))),
            new: Value::Null,
            old_full: Some(to_json_str(&Value::Object(sale))),
            new_full: None,
        });
    }
    for key in old_map.keys().filter(|k| new_map.contains_key(*k)) {
        let (old_sale, new_sale) = (&old_map[key], &new_map[key]);
        for (field, label) in [("price", "价格"), ("amount", "数量"), ("end_ms", "结束时间")] {
            if old_sale.get(field) != new_sale.get(field) {
                let item_label =
                    prettify_item_id(old_sale.get("item_id").unwrap_or(&Value::Null));
                let (old_value, new_value) = if field == "end_ms" {
                    (
                        from_epoch_ms(old_sale.get(field).unwrap_or(&Value::Null)),
                        from_epoch_ms(new_sale.get(field).unwrap_or(&Value::Null)),
                    )
                } else {
                    (
                        format_amount(old_sale.get(field).unwrap_or(&Value::Null)),
                        format_amount(new_sale.get(field).unwrap_or(&Value::Null)),
                    )
                };
                changes.push(SaleChange {
                    kind: "modified".into(),
                    path: format!("sales.{}.{}", key, field),
                    old: Value::String(format!("{} {}：{}", item_label, label, old_value)),
                    new: Value::String(format!("{} {}：{}", item_label, label, new_value)),
                    old_full: old_sale.get(field).map(to_json_str),
                    new_full: new_sale.get(field).map(to_json_str),
                });
            }
        }
    }
    changes
}

/// 生成展示用的特卖数据（含状态、可读名称与统计）。
pub fn firesales_view(payload: &Value, generated_at: &str, item_names: Option<&Map<String, Value>>) -> Value {
    let now = now_ms();
    let names = item_names;
    let mut sales: Vec<Value> = Vec::new();
    for sale in normalize_sales(payload) {
        let mut entry = sale.clone();
        let item_id = sale_get_str(&sale, "item_id").to_string();
        let resolved = names
            .and_then(|n| n.get(&item_id))
            .and_then(|v| v.as_str())
            .map(String::from)
            .or_else(|| {
                sale.get("name")
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty())
                    .map(String::from)
            })
            .unwrap_or_else(|| prettify_item_id(&Value::String(item_id.clone())));
        entry.insert("name".into(), Value::String(resolved));
        let (state, state_label) = sale_state(
            sale.get("start_ms").and_then(|v| v.as_i64()),
            sale.get("end_ms").and_then(|v| v.as_i64()),
            now,
        );
        entry.insert("state".into(), Value::String(state.clone()));
        entry.insert("state_label".into(), Value::String(state_label));
        entry.insert("key".into(), Value::String(sale_key(&sale)));
        sales.push(Value::Object(entry));
    }

    let total = sales.len();
    let upcoming = sales.iter().filter(|s| s["state"] == "upcoming").count();
    let active = sales.iter().filter(|s| s["state"] == "active").count();
    let ended = sales.iter().filter(|s| s["state"] == "ended").count();

    json!({
        "generated_at": generated_at,
        "sales": sales,
        "total": total,
        "upcoming_count": upcoming,
        "active_count": active,
        "ended_count": ended,
    })
}

/// 快速统计（用于卡片摘要）：返回 (total, upcoming, active)。
pub fn quick_counts(payload: &Value) -> (usize, usize, usize) {
    let now = now_ms();
    let mut total = 0;
    let mut upcoming = 0;
    let mut active = 0;
    for sale in normalize_sales(payload) {
        total += 1;
        let (state, _) = sale_state(
            sale.get("start_ms").and_then(|v| v.as_i64()),
            sale.get("end_ms").and_then(|v| v.as_i64()),
            now,
        );
        if state == "upcoming" {
            upcoming += 1;
        } else if state == "active" {
            active += 1;
        }
    }
    (total, upcoming, active)
}
