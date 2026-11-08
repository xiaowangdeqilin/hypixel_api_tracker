//! 结构化 JSON diff（原 app/diff.py + build_dashboard.diff_dict 的合并移植）。
//!
//! 产出与旧版 dashboard 一致的变更条目：
//! `{type, path, old, new, old_full, new_full}`，
//! 其中 `old` / `new` 是展示摘要，`old_full` / `new_full` 是限长的 JSON 字符串。

use serde_json::{Map, Value};

/// 单条结构化变更。
#[derive(Clone, Debug)]
pub struct Change {
    pub kind: String, // added | removed | modified
    pub path: String,
    pub old: Value,      // 展示摘要（None 序列化为 null）
    pub new: Value,
    pub old_full: Option<String>, // 完整 JSON 字符串（限长）
    pub new_full: Option<String>,
}

impl Change {
    fn to_entry(&self) -> Map<String, Value> {
        let mut m = Map::new();
        m.insert("type".into(), Value::String(self.kind.clone()));
        m.insert("path".into(), Value::String(self.path.clone()));
        m.insert("old".into(), self.old.clone());
        m.insert("new".into(), self.new.clone());
        m.insert(
            "old_full".into(),
            self.old_full.clone().map(Value::String).unwrap_or(Value::Null),
        );
        m.insert(
            "new_full".into(),
            self.new_full.clone().map(Value::String).unwrap_or(Value::Null),
        );
        m
    }
}

/// 变更条目 → JSON 对象（写入 changes.json 前调用方补充 endpoint/time）。
pub fn change_to_value(change: &Change) -> Value {
    Value::Object(change.to_entry())
}

/// 生成用于变更日志的紧凑、易读的值摘要。
pub fn summarize(value: &Value) -> Value {
    match value {
        Value::Null => Value::String("空".into()),
        Value::Object(o) => Value::String(format!("{{{:?}}}", o.len()).replace("'", "").replace("{", "{").replace("}", "}")),
        Value::Array(a) => Value::String(format!("[{:?} 项]", a.len()).replace(" ", " ")),
        other => {
            let text = serde_json::to_string(other).unwrap_or_default();
            if text.chars().count() > 120 {
                Value::String(format!("{}...", truncate_chars(&text, 117)))
            } else {
                Value::String(text)
            }
        }
    }
}

/// 按字符截断（避免切断多字节字符）。
fn truncate_chars(text: &str, max: usize) -> String {
    text.chars().take(max).collect()
}

/// 生成完整（但限长）的 JSON 表示，让页面能展开查看真实值。
pub fn log_value(value: &Value, max_chars: usize) -> Option<String> {
    if value.is_null() {
        return None;
    }
    let text = serde_json::to_string(value).unwrap_or_default();
    if text.chars().count() > max_chars {
        let total = text.chars().count();
        Some(format!(
            "{}…（已截断，共 {} 字符）",
            truncate_chars(&text, max_chars),
            total
        ))
    } else {
        Some(text)
    }
}

/// 两个 JSON 值之间的结构性变更（字典递归、列表整体比较、标量直接比较）。
pub fn diff_dict(old: &Value, new: &Value, prefix: &str) -> Vec<Change> {
    let mut changes = Vec::new();
    let (old_map, new_map) = match (old, new) {
        (Value::Object(o), Value::Object(n)) => (o, n),
        _ => {
            if old != new {
                changes.push(Change {
                    kind: "modified".into(),
                    path: prefix_to_path(prefix),
                    old: summarize(old),
                    new: summarize(new),
                    old_full: log_value(old, 1500),
                    new_full: log_value(new, 1500),
                });
            }
            return changes;
        }
    };

    let mut keys: Vec<&String> = old_map.keys().chain(new_map.keys()).collect();
    keys.sort();
    keys.dedup();

    for key in keys {
        let path = if prefix.is_empty() {
            key.clone()
        } else {
            format!("{}.{}", prefix, key)
        };
        match (old_map.get(key), new_map.get(key)) {
            (Some(ov), None) => changes.push(Change {
                kind: "removed".into(),
                path,
                old: summarize(ov),
                new: Value::Null,
                old_full: log_value(ov, 1500),
                new_full: None,
            }),
            (None, Some(nv)) => changes.push(Change {
                kind: "added".into(),
                path,
                old: Value::Null,
                new: summarize(nv),
                old_full: None,
                new_full: log_value(nv, 1500),
            }),
            (Some(ov), Some(nv)) => {
                if ov.is_object() && nv.is_object() {
                    changes.extend(diff_dict(ov, nv, &path));
                } else if ov != nv {
                    changes.push(Change {
                        kind: "modified".into(),
                        path,
                        old: summarize(ov),
                        new: summarize(nv),
                        old_full: log_value(ov, 1500),
                        new_full: log_value(nv, 1500),
                    });
                }
            }
            (None, None) => unreachable!(),
        }
    }
    changes
}

fn prefix_to_path(prefix: &str) -> String {
    if prefix.is_empty() {
        "root".into()
    } else {
        prefix.to_string()
    }
}

/// 使用稳定键字段（如物品 id）对字典列表做比对。
pub fn diff_list_by_key(old: &[Value], new: &[Value], key_field: &str, prefix: &str) -> Vec<Change> {
    let to_map = |items: &[Value]| -> Map<String, Value> {
        let mut m = Map::new();
        for item in items {
            if let Value::Object(o) = item {
                let key = match o.get(key_field) {
                    Some(v) => match v {
                        Value::String(s) => s.clone(),
                        other => other.to_string(),
                    },
                    None => continue,
                };
                m.insert(key, Value::Object(o.clone()));
            }
        }
        m
    };
    diff_dict(&Value::Object(to_map(old)), &Value::Object(to_map(new)), prefix)
}

/// 服务端（旧 diff.py 语义）的简单递归 diff：返回 (path, kind, old, new) 列表。
/// kind 为 added / removed / modified（旧版 value_changed / list_length_changed
/// 已统一收敛为 modified，展示层标签兼容）。
pub fn diff_values(old: &Value, new: &Value, path: &str) -> Vec<(String, String, Value, Value)> {
    let mut out = Vec::new();
    match (old, new) {
        (Value::Object(o), Value::Object(n)) => {
            let mut keys: Vec<&String> = o.keys().chain(n.keys()).collect();
            keys.sort();
            keys.dedup();
            for key in keys {
                let sub = format!("{}.{}", path, key);
                let sub = if path == "root" { key.clone() } else { sub };
                match (o.get(key), n.get(key)) {
                    (None, Some(nv)) => out.push((sub, "added".into(), Value::Null, nv.clone())),
                    (Some(ov), None) => out.push((sub, "removed".into(), ov.clone(), Value::Null)),
                    (Some(ov), Some(nv)) => out.extend(diff_values(ov, nv, &sub)),
                    (None, None) => unreachable!(),
                }
            }
        }
        (Value::Array(a), Value::Array(b)) => {
            if a.len() != b.len() {
                out.push((path.into(), "modified".into(), old.clone(), new.clone()));
            } else {
                for (i, (av, bv)) in a.iter().zip(b.iter()).enumerate() {
                    out.extend(diff_values(av, bv, &format!("{}[{}]", path, i)));
                }
            }
        }
        _ => {
            if old != new {
                out.push((path.into(), "modified".into(), old.clone(), new.clone()));
            }
        }
    }
    out
}
