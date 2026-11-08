//! 数据文件读写与版本状态机（原 db.py + build_dashboard 状态部分的整合）。
//!
//! 存储格式 v2：全部 JSON、无数据库依赖（详见 crate 文档）。
//! 所有写盘都是「临时文件 + 原子重命名」，进程中断不会留下半截文件。

use std::io::Write as _;
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::labels::{sync_label, VERSION_ENDPOINTS};
use crate::timeutil::now_beijing;

/// 项目根目录：优先环境变量 `TRACKER_HOME`，否则用当前工作目录。
pub fn base_dir() -> PathBuf {
    if let Ok(home) = std::env::var("TRACKER_HOME") {
        if !home.trim().is_empty() {
            return PathBuf::from(home);
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

pub fn data_dir() -> PathBuf {
    base_dir().join("data")
}

pub fn snapshot_path(name: &str) -> PathBuf {
    data_dir().join("snapshots").join(format!("{}.json", name))
}

// ---------------------------------------------------------------------------
// 通用 JSON 文件读写（原子写）
// ---------------------------------------------------------------------------

pub fn load_json(path: &Path) -> Option<Value> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

pub fn load_json_or(path: &Path, default: Value) -> Value {
    load_json(path).unwrap_or(default)
}

/// 原子写 JSON：先写 `.tmp` 再重命名，失败时目标是旧内容。
pub fn write_json(path: &Path, data: &Value) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("json.tmp");
    {
        let mut fh = std::fs::File::create(&tmp)?;
        fh.write_all(serde_json::to_string_pretty(data).unwrap_or_default().as_bytes())?;
        fh.write_all(b"\n")?;
        fh.sync_all()?;
    }
    std::fs::rename(&tmp, path)
}

pub fn write_text(path: &Path, text: &str) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("tmp");
    {
        let mut fh = std::fs::File::create(&tmp)?;
        fh.write_all(text.as_bytes())?;
        fh.sync_all()?;
    }
    std::fs::rename(&tmp, path)
}

// ---------------------------------------------------------------------------
// 仪表盘自身版本号
// ---------------------------------------------------------------------------

pub fn read_version() -> String {
    std::fs::read_to_string(base_dir().join("VERSION"))
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| "0.0.0".into())
}

/// 本次构建对应的提交短哈希；Actions 环境下取 GITHUB_SHA。
pub fn current_build() -> String {
    match std::env::var("GITHUB_SHA") {
        Ok(sha) if !sha.trim().is_empty() => sha.trim()[..7.min(sha.trim().len())].to_string(),
        _ => "本地构建".into(),
    }
}

// ---------------------------------------------------------------------------
// 快照
// ---------------------------------------------------------------------------

pub fn load_snapshot(name: &str) -> Option<Value> {
    load_json(&snapshot_path(name))
}

pub fn save_snapshot(name: &str, snapshot: &Value) -> std::io::Result<()> {
    write_json(&snapshot_path(name), snapshot)
}

// ---------------------------------------------------------------------------
// 变更日志（构建与服务端共用一份）
// ---------------------------------------------------------------------------

pub fn load_changes() -> Vec<Map<String, Value>> {
    match load_json(&data_dir().join("changes.json")) {
        Some(Value::Array(items)) => {
            items.into_iter().filter_map(|v| v.as_object().cloned()).collect()
        }
        _ => Vec::new(),
    }
}

pub fn save_changes(changes: &[Map<String, Value>]) -> std::io::Result<()> {
    write_json(
        &data_dir().join("changes.json"),
        &Value::Array(changes.iter().cloned().map(Value::Object).collect()),
    )
}

/// 合并新旧变更，并丢掉超过保留期（30 天）的记录。
///
/// 除保留期外不再限制条数：30 天内有多少条就保留多少条。
pub fn append_changes(
    existing: &mut Vec<Map<String, Value>>,
    new_changes: Vec<Map<String, Value>>,
) {
    let mut merged = new_changes;
    merged.extend(existing.drain(..));
    *existing = crate::retention::filter_recent(merged, "time");
}

/// 构造一条变更记录（changes.json 统一 schema）。
pub fn make_change_entry(
    endpoint: &str,
    kind: &str,
    path: &str,
    old: Value,
    new: Value,
    old_full: Option<String>,
    new_full: Option<String>,
    time: &str,
) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("endpoint".into(), Value::String(endpoint.to_string()));
    m.insert("type".into(), Value::String(kind.to_string()));
    m.insert("path".into(), Value::String(path.to_string()));
    m.insert("old".into(), old);
    m.insert("new".into(), new);
    m.insert(
        "old_full".into(),
        old_full.map(Value::String).unwrap_or(Value::Null),
    );
    m.insert(
        "new_full".into(),
        new_full.map(Value::String).unwrap_or(Value::Null),
    );
    m.insert("time".into(), Value::String(time.to_string()));
    m
}

// ---------------------------------------------------------------------------
// 版本状态机（data/version.json，原 runtime.json）
// ---------------------------------------------------------------------------

pub fn load_runtime() -> Value {
    load_json_or(&data_dir().join("version.json"), json!({}))
}

/// 兼容旧文件名 runtime.json（已由迁移脚本改名，这里兜底读一次）。
pub fn load_runtime_compat() -> Value {
    let v = load_runtime();
    if v.as_object().map(|o| !o.is_empty()).unwrap_or(false) {
        return v;
    }
    load_json_or(&data_dir().join("runtime.json"), json!({}))
}

pub fn save_runtime(data: &Value) -> std::io::Result<()> {
    write_json(&data_dir().join("version.json"), data)
}

/// 把 `0.27.2.22` 转成可比较的数值向量，用于取较高版本。
fn version_key(value: &str) -> Vec<u64> {
    value
        .split(|c: char| c == '.' || c == '-' || c == '+' || c == '_')
        .map(|piece| piece.parse::<u64>().unwrap_or(0))
        .collect()
}

fn max_version(versions: &[String]) -> String {
    versions
        .iter()
        .cloned()
        .max_by_key(|v| version_key(v))
        .unwrap_or_default()
}

/// 双端点版本一致性确认，避免灰度发布期间版本号来回跳。
///
/// 1. 两个接口都返回版本且相同 → `aligned`，该版本成为稳定版本；
/// 2. 都返回但不同 → `syncing`，保持上一次稳定版本不变；
/// 3. 只拿到部分 → `partial`，同样保持上次稳定版本。
pub fn resolve_version_state(
    runtime: &Value,
    observed: &std::collections::BTreeMap<String, String>,
    now: &str,
) -> Value {
    let empty = Map::new();
    let robj = runtime.as_object().unwrap_or(&empty);
    let previous_stable = robj
        .get("stable_version")
        .and_then(|v| v.as_str())
        .or_else(|| robj.get("resource_version").and_then(|v| v.as_str()))
        .unwrap_or("")
        .to_string();
    let prev_versions = robj.get("versions").and_then(|v| v.as_object()).unwrap_or(&empty);

    let mut versions = Map::new();
    for ep in VERSION_ENDPOINTS.iter() {
        let prev = prev_versions.get(*ep).and_then(|v| v.as_object());
        let current = observed.get(*ep);
        if let Some(current) = current {
            let prev_version = prev.and_then(|p| p.get("version")).and_then(|v| v.as_str());
            let first_seen = if prev_version == Some(current.as_str()) {
                prev
                    .and_then(|p| p.get("observed_at"))
                    .and_then(|v| v.as_str())
                    .unwrap_or(now)
                    .to_string()
            } else {
                now.to_string()
            };
            versions.insert(
                ep.to_string(),
                json!({"version": current, "observed_at": first_seen}),
            );
        } else if let Some(prev) = prev {
            if let Some(pv) = prev.get("version").and_then(|v| v.as_str()) {
                versions.insert(
                    ep.to_string(),
                    json!({
                        "version": pv,
                        "observed_at": prev.get("observed_at").and_then(|v| v.as_str()).unwrap_or("-"),
                        "stale": true,
                    }),
                );
            }
        }
    }

    let available: Vec<String> = VERSION_ENDPOINTS
        .iter()
        .filter_map(|ep| versions.get(*ep).and_then(|v| v.get("version")).and_then(|v| v.as_str()).map(String::from))
        .collect();
    let stale = VERSION_ENDPOINTS
        .iter()
        .any(|ep| versions.get(*ep).and_then(|v| v.get("stale")).and_then(|v| v.as_bool()).unwrap_or(false));

    let (status, stable, note) = if available.len() == VERSION_ENDPOINTS.len()
        && available.iter().all(|v| v == &available[0])
        && !stale
    {
        (
            "aligned",
            available[0].clone(),
            "收藏与技能两个接口版本一致，已确认当前版本".to_string(),
        )
    } else if available.len() < VERSION_ENDPOINTS.len() || stale {
        let stable = if previous_stable.is_empty() {
            max_version(&available)
        } else {
            previous_stable.clone()
        };
        (
            "partial",
            stable,
            "部分接口本次未返回版本号，沿用上次已确认的稳定版本".to_string(),
        )
    } else {
        let c = &versions["collections"]["version"];
        let s = &versions["skills"]["version"];
        let mut note = format!(
            "版本同步中：收藏 v{}、技能 v{}。两个接口更新不同步，暂不切换当前版本，待二者一致后再确认。",
            c.as_str().unwrap_or("-"),
            s.as_str().unwrap_or("-")
        );
        if !previous_stable.is_empty() {
            note.push_str(&format!("（当前保持 v{}）", previous_stable));
        }
        let stable = if previous_stable.is_empty() {
            max_version(&available)
        } else {
            previous_stable.clone()
        };
        ("syncing", stable, note)
    };

    let confirmed = status == "aligned" && !stable.is_empty() && stable != previous_stable;
    let mut history: Vec<Value> = robj
        .get("history")
        .and_then(|h| h.as_array())
        .cloned()
        .unwrap_or_default();
    if confirmed {
        history.insert(
            0,
            json!({
                "version": stable,
                "time": now,
                "note": "两个接口版本一致，确认切换当前版本",
                "previous": if previous_stable.is_empty() { "-".to_string() } else { previous_stable.clone() },
            }),
        );
        history.truncate(20);
    }

    json!({
        "stable_version": if stable.is_empty() { "未获取".to_string() } else { stable.clone() },
        "resource_version": if stable.is_empty() { "未获取".to_string() } else { stable.clone() },
        "versions": versions,
        "sync_status": status,
        "note": note,
        "confirmed": confirmed,
        "previous_stable": if previous_stable.is_empty() { Value::Null } else { Value::String(previous_stable) },
        "updated_at": now,
        "history": history,
    })
}

/// 服务端页面使用的版本信息视图（压平 versions、格式化时间）。
pub fn version_info_view(runtime: &Value) -> Value {
    let empty = Map::new();
    let robj = runtime.as_object().unwrap_or(&empty);
    let stable = robj
        .get("stable_version")
        .and_then(|v| v.as_str())
        .or_else(|| robj.get("resource_version").and_then(|v| v.as_str()))
        .unwrap_or("未获取");
    let mut flat = Map::new();
    if let Some(versions) = robj.get("versions").and_then(|v| v.as_object()) {
        for (key, value) in versions {
            let flat_value = match value {
                Value::Object(o) => o
                    .get("version")
                    .map(|v| v.as_str().unwrap_or("-").to_string())
                    .unwrap_or_else(|| "-".into()),
                Value::String(s) => s.clone(),
                other => other.to_string(),
            };
            flat.insert(key.clone(), Value::String(flat_value));
        }
    }
    let history: Vec<Value> = robj
        .get("history")
        .and_then(|h| h.as_array())
        .map(|arr| {
            arr.iter()
                .map(|item| {
                    let mut entry = match item {
                        Value::Object(o) => o.clone(),
                        _ => Map::new(),
                    };
                    entry.insert(
                        "time".into(),
                        Value::String(crate::timeutil::format_time(item.get("time").unwrap_or(&Value::Null))),
                    );
                    Value::Object(entry)
                })
                .collect()
        })
        .unwrap_or_default();

    json!({
        "stable_version": stable,
        "versions": flat,
        "sync_status": robj.get("sync_status").and_then(|v| v.as_str()).unwrap_or("aligned"),
        "note": robj.get("note").and_then(|v| v.as_str()).unwrap_or(""),
        "updated_at": crate::timeutil::format_time(robj.get("updated_at").unwrap_or(&Value::Null)),
        "history": history,
    })
}

pub fn sync_status_label(status: &str) -> String {
    sync_label(status)
}

// ---------------------------------------------------------------------------
// 静态资源读取（静态页内联用），缺失时降级为空串而不是中断构建
// ---------------------------------------------------------------------------

/// 读取需要内联到静态页面的前端资源；缺失时降级为空串而不是中断构建。
pub fn read_asset(path: &Path) -> String {
    std::fs::read_to_string(path).unwrap_or_default()
}

// ---------------------------------------------------------------------------
// 服务端快照环形缓冲（data/server/history.json，.gitignore）
// ---------------------------------------------------------------------------

pub const SERVER_HISTORY_MAX_PER_ENDPOINT: usize = 30;
/// 单条快照 payload 的持久化上限：超过则只存元数据（payload=null），
/// 避免 items（6MB+）把历史文件撑爆。
pub const SERVER_PAYLOAD_MAX_BYTES: usize = 512 * 1024;
/// 服务端快照历史保留天数。
pub const SERVER_HISTORY_DAYS: i64 = 7;

#[derive(serde::Serialize, serde::Deserialize, Default)]
pub struct ServerHistory {
    #[serde(default)]
    pub next_id: u64,
    #[serde(default)]
    pub snapshots: Vec<Value>,
}

pub fn load_server_history() -> ServerHistory {
    load_json(&data_dir().join("server").join("history.json"))
        .and_then(|v| serde_json::from_value(v).ok())
        .unwrap_or_default()
}

pub fn save_server_history(history: &ServerHistory) -> std::io::Result<()> {
    write_json(
        &data_dir().join("server").join("history.json"),
        &serde_json::to_value(history).unwrap_or(Value::Null),
    )
}

/// 追加快照并清理（每端点最多 N 条 + 超龄剔除 + 超大 payload 去除）。
pub fn append_server_snapshot(history: &mut ServerHistory, endpoint: &str, payload_json: &str, status_code: i64, checksum: &str) -> u64 {
    history.next_id += 1;
    let id = history.next_id;
    let oversized = payload_json.len() > SERVER_PAYLOAD_MAX_BYTES;
    let payload_value: Value = if oversized {
        Value::Null
    } else {
        serde_json::from_str(payload_json).unwrap_or(Value::Null)
    };
    history.snapshots.push(json!({
        "id": id,
        "endpoint": endpoint,
        "fetched_at": now_beijing(),
        "status_code": status_code,
        "payload": payload_value,
        "checksum": checksum,
    }));
    prune_server_history(history);
    id
}

fn prune_server_history(history: &mut ServerHistory) {
    let cutoff = (chrono::Utc::now() - chrono::Duration::days(SERVER_HISTORY_DAYS))
        .format("%Y-%m-%d %H:%M:%S")
        .to_string();
    // 快照按时间正序追加（最新的在末尾）：每端点保留最新 N 条
    let total = history.snapshots.len();
    let mut seen: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
    let mut keep = vec![true; total];
    for idx in (0..total).rev() {
        let ep = history.snapshots[idx]
            .get("endpoint")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let count = seen.entry(ep).or_insert(0);
        *count += 1;
        if *count > SERVER_HISTORY_MAX_PER_ENDPOINT {
            keep[idx] = false;
        }
    }
    let mut kept: Vec<Value> = Vec::with_capacity(total);
    for (idx, snap) in history.snapshots.drain(..).enumerate() {
        if !keep[idx] {
            continue;
        }
        // 剔除超龄（fetched_at 是北京时间字符串，可直接比较）；无法解析的按过期处理
        let fresh = snap
            .get("fetched_at")
            .and_then(|v| v.as_str())
            .map(|ts| ts >= cutoff.as_str())
            .unwrap_or(false);
        if fresh {
            kept.push(snap);
        }
    }
    history.snapshots = kept;
}

/// 每端点最新一条快照（用于 diff 前置）。
pub fn latest_server_snapshot<'a>(history: &'a ServerHistory, endpoint: &str) -> Option<&'a Value> {
    history
        .snapshots
        .iter()
        .rev()
        .find(|snap| snap.get("endpoint").and_then(|v| v.as_str()) == Some(endpoint))
}
