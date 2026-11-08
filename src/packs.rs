//! Hypixel 资源包的下载、解压与文件变更比对（原 scripts/pack_inspector.py 的移植）。
//!
//! 1. 从 packs 响应里**动态选出 packFormat 数值最大**的资源包（不写死任何数字）；
//! 2. 官方 sha1 与上次相同时复用清单，跳过下载；
//! 3. 否则下载 zip、校验 sha1、解压，计算每个文件的 sha256（前 16 位），
//!    并为文本文件缓存内容（下次比对做逐行 diff）；
//! 4. 与上次清单比对得到新增 / 修改 / 删除；
//! 5. 结果交给调用方统一落盘（失败安全：任何一步失败都不写盘）。

use std::collections::BTreeMap;
use std::io::Read;

use serde_json::{json, Map, Value};

use crate::retention::filter_recent;
use crate::sha1_hex;
use crate::sha2_digest;
use crate::state::{load_json, write_json};
use crate::timeutil::now_beijing;

const DOWNLOAD_TIMEOUT: u64 = 120;
const HASH_LENGTH: usize = 16;
const MAX_PACK_CHANGE_RECORDS: usize = 50;

const TEXT_EXTENSIONS: [&str; 14] = [
    ".json", ".mcmeta", ".txt", ".lang", ".fsh", ".vsh", ".glsl",
    ".properties", ".yml", ".yaml", ".cfg", ".md", ".toml", ".csv",
];
const MAX_TEXT_FILE_BYTES: usize = 128 * 1024;
const MAX_TOTAL_TEXT_BYTES: usize = 2 * 1024 * 1024;

pub fn parse_pack_format(value: Option<&Value>) -> Option<i64> {
    match value? {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(s) => {
            let text = s.trim();
            if !text.is_empty() && text.bytes().all(|b| b.is_ascii_digit()) {
                text.parse::<i64>().ok()
            } else {
                None
            }
        }
        _ => None,
    }
}

/// 摊平 packs 响应中所有可用的资源包版本（含所属 pack 信息）。
pub fn collect_pack_versions(packs_payload: &Value) -> Vec<Map<String, Value>> {
    let mut versions = Vec::new();
    let Some(packs) = packs_payload.get("packs").and_then(|p| p.as_array()) else {
        return versions;
    };
    for pack in packs {
        let Some(pobj) = pack.as_object() else { continue };
        for version in pobj.get("versions").and_then(|v| v.as_array()).into_iter().flatten() {
            let Some(vobj) = version.as_object() else { continue };
            let Some(fmt) = parse_pack_format(vobj.get("packFormat")) else { continue };
            let mut entry = Map::new();
            entry.insert(
                "id".into(),
                pobj.get("id").cloned().unwrap_or_else(|| Value::String("unknown".into())),
            );
            entry.insert(
                "deploy_id".into(),
                pobj.get("deployId").cloned().unwrap_or_else(|| Value::String(String::new())),
            );
            entry.insert("pack_format".into(), Value::from(fmt));
            entry.insert(
                "hash".into(),
                vobj.get("hash").cloned().unwrap_or_else(|| Value::String(String::new())),
            );
            entry.insert(
                "url".into(),
                vobj.get("url").cloned().unwrap_or_else(|| Value::String(String::new())),
            );
            versions.push(entry);
        }
    }
    versions
}

/// 选出 packFormat 数值最大的资源包版本（附带 candidates 供页面展示选取依据）。
pub fn select_pack(packs_payload: &Value) -> Option<Map<String, Value>> {
    let versions = collect_pack_versions(packs_payload);
    let usable: Vec<&Map<String, Value>> = versions
        .iter()
        .filter(|v| {
            !v.get("url").and_then(|u| u.as_str()).unwrap_or("").is_empty()
                && !v.get("hash").and_then(|h| h.as_str()).unwrap_or("").is_empty()
        })
        .collect();
    let best_ref = usable
        .iter()
        .copied()
        .max_by_key(|v| v.get("pack_format").and_then(|f| f.as_i64()).unwrap_or(i64::MIN))?;
    let mut best = best_ref.clone();
    let mut candidates: Vec<i64> = versions
        .iter()
        .filter_map(|v| v.get("pack_format").and_then(|f| f.as_i64()))
        .collect();
    candidates.sort_unstable();
    candidates.dedup();
    candidates.reverse();
    best.insert(
        "candidates".into(),
        Value::Array(candidates.into_iter().map(Value::from).collect()),
    );
    let pack_count = packs_payload.get("packs").and_then(|p| p.as_array()).map(|a| a.len()).unwrap_or(0);
    best.insert("pack_count".into(), Value::from(pack_count));
    Some(best)
}

fn is_text_file(filename: &str) -> bool {
    let lower = filename.to_ascii_lowercase();
    TEXT_EXTENSIONS.iter().any(|ext| lower.ends_with(ext))
}

/// 解压 zip，返回 (文件哈希清单, 文本文件内容缓存)。
pub fn scan_zip(zip_bytes: &[u8]) -> Result<(BTreeMap<String, String>, BTreeMap<String, String>), String> {
    let mut files = BTreeMap::new();
    let mut texts = BTreeMap::new();
    let mut total_text_bytes = 0usize;
    let reader = std::io::Cursor::new(zip_bytes);
    let mut archive =
        zip::ZipArchive::new(reader).map_err(|e| format!("资源包 zip 解析失败：{}", e))?;
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index).map_err(|e| e.to_string())?;
        if entry.is_dir() {
            continue;
        }
        let name = entry.name().to_string();
        // 防 zip-slip：统一用正斜杠路径名做键，不落盘所以无路径穿越风险
        let name = name.replace('\\', "/");
        let mut content = Vec::new();
        entry
            .read_to_end(&mut content)
            .map_err(|e| format!("读取 zip 内文件失败 {}: {}", name, e))?;
        let hash = sha2_digest(&content)[..HASH_LENGTH].to_string();
        files.insert(name.clone(), hash);
        if !is_text_file(&name) || content.len() > MAX_TEXT_FILE_BYTES {
            continue;
        }
        if total_text_bytes + content.len() > MAX_TOTAL_TEXT_BYTES {
            continue;
        }
        if let Ok(text) = String::from_utf8(content) {
            total_text_bytes += text.len();
            texts.insert(name, text);
        }
    }
    Ok((files, texts))
}

pub use crate::textdiff::diff_text_lines;

/// 为被修改的文件生成展示条目。
///
/// 文本文件只携带变化片段（hunks）；二进制文件只说明哈希变化。
fn build_modified_entries(
    paths: &[String],
    previous_files: &BTreeMap<String, String>,
    current_files: &BTreeMap<String, String>,
    previous_texts: &BTreeMap<String, String>,
    current_texts: &BTreeMap<String, String>,
) -> Vec<Value> {
    let mut entries = Vec::new();
    for path in paths {
        let old_hash = previous_files.get(path).cloned().unwrap_or_default();
        let new_hash = current_files.get(path).cloned().unwrap_or_default();
        let (old_text, new_text) = (previous_texts.get(path), current_texts.get(path));
        if let (Some(old_text), Some(new_text)) = (old_text, new_text) {
            let hunks = diff_text_lines(old_text, new_text);
            if !hunks.is_empty() {
                entries.push(json!({
                    "path": path,
                    "kind": "text",
                    "old_hash": old_hash,
                    "new_hash": new_hash,
                    "hunks": hunks,
                }));
                continue;
            }
        }
        entries.push(json!({
            "path": path,
            "kind": if is_text_file(path) { "text" } else { "binary" },
            "old_hash": old_hash,
            "new_hash": new_hash,
        }));
    }
    entries
}

fn manifest_path(data_dir: &std::path::Path) -> std::path::PathBuf {
    data_dir.join("pack_files.json")
}
fn texts_path(data_dir: &std::path::Path) -> std::path::PathBuf {
    data_dir.join("pack_texts.json")
}
fn pack_changes_path(data_dir: &std::path::Path) -> std::path::PathBuf {
    data_dir.join("pack_changes.json")
}

pub fn load_manifest(data_dir: &std::path::Path) -> Value {
    load_json(&manifest_path(data_dir)).unwrap_or_else(|| json!({}))
}

pub fn load_texts_cache(data_dir: &std::path::Path) -> Value {
    load_json(&texts_path(data_dir)).unwrap_or_else(|| json!({}))
}

pub fn load_pack_changes(data_dir: &std::path::Path) -> Vec<Map<String, Value>> {
    match load_json(&pack_changes_path(data_dir)) {
        Some(Value::Array(items)) => {
            items.into_iter().filter_map(|v| v.as_object().cloned()).collect()
        }
        _ => Vec::new(),
    }
}

pub fn save_pack_changes(data_dir: &std::path::Path, changes: &[Map<String, Value>]) {
    let _ = write_json(
        &pack_changes_path(data_dir),
        &Value::Array(changes.iter().cloned().map(Value::Object).collect()),
    );
}

/// inspect_pack 的返回结果（不落盘，由调用方决定是否 commit）。
pub struct PackResult {
    pub status: String, // baseline | updated | unchanged | error | unavailable | texts_backfilled
    pub pack: Option<Map<String, Value>>,
    pub total_files: usize,
    pub changes: Vec<Map<String, Value>>,
    pub error: Option<String>,
    pub candidates: Vec<Value>,
    /// 需要写入的数据（manifest / texts / changes），None 表示无需写入。
    pub pending: Option<Map<String, Value>>,
}

/// 检查资源包，返回本次检查结果，**但不写入任何文件**。
pub fn inspect_pack(
    packs_payload: &Value,
    data_dir: &std::path::Path,
    force_download: bool,
) -> PackResult {
    let mut result = PackResult {
        status: "unavailable".into(),
        pack: None,
        total_files: 0,
        changes: load_pack_changes(data_dir),
        error: None,
        candidates: Vec::new(),
        pending: None,
    };

    let target = match select_pack(packs_payload) {
        Some(t) => t,
        None => {
            result.error = Some("packs 响应中没有可用的资源包下载地址".into());
            return result;
        }
    };
    let target_hash = target.get("hash").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let target_url = target.get("url").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let target_id = target.get("id").cloned().unwrap_or(Value::Null);
    let target_deploy = target.get("deploy_id").cloned().unwrap_or(Value::Null);
    let target_format = target.get("pack_format").cloned().unwrap_or(Value::Null);
    result.pack = Some(target.clone());
    result.candidates = target
        .get("candidates")
        .and_then(|c| c.as_array())
        .cloned()
        .unwrap_or_default();

    let manifest = load_manifest(data_dir);
    let previous_files: BTreeMap<String, String> = manifest
        .get("files")
        .and_then(|f| f.as_object())
        .map(|obj| {
            obj.iter()
                .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                .collect()
        })
        .unwrap_or_default();
    let texts_cache = load_texts_cache(data_dir);
    let previous_texts: BTreeMap<String, String> = texts_cache
        .get("texts")
        .and_then(|t| t.as_object())
        .map(|obj| {
            obj.iter()
                .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                .collect()
        })
        .unwrap_or_default();
    result.total_files = previous_files.len();

    let hash_matches = manifest.get("pack_hash").and_then(|v| v.as_str()) == Some(target_hash.as_str());
    let texts_missing = previous_texts.is_empty()
        || texts_cache.get("pack_hash").and_then(|v| v.as_str()) != Some(target_hash.as_str());
    let content_only_refresh = hash_matches && !previous_files.is_empty() && !force_download;

    if content_only_refresh && !texts_missing {
        result.status = "unchanged".into();
        return result;
    }

    let (current_files, current_texts) = match crate::fetch::fetch_bytes(&target_url, DOWNLOAD_TIMEOUT) {
        Ok(bytes) => {
            let actual_sha1 = sha1_hex(&bytes);
            if actual_sha1 != target_hash {
                result.status = "error".into();
                result.error = Some(format!(
                    "资源包 sha1 校验失败（官方 {}，实际 {}）",
                    target_hash, actual_sha1
                ));
                return result;
            }
            match scan_zip(&bytes) {
                Ok((files, texts)) if !files.is_empty() => (files, texts),
                Ok(_) => {
                    result.status = "error".into();
                    result.error = Some("资源包解压后没有可比对的文件".into());
                    return result;
                }
                Err(e) => {
                    result.status = "error".into();
                    result.error = Some(e);
                    return result;
                }
            }
        }
        Err(e) => {
            result.status = "error".into();
            result.error = Some(format!("资源包下载失败：{}", e));
            return result;
        }
    };

    if content_only_refresh {
        // 旧基线缺少文本缓存：只补齐缓存，不改动文件清单、不产生变更记录。
        result.status = "texts_backfilled".into();
        result.total_files = current_files.len();
        let mut pending = Map::new();
        pending.insert(
            "texts".into(),
            json!({
                "pack_id": target_id,
                "pack_hash": target_hash,
                "generated_at": now_beijing(),
                "texts": current_texts,
            }),
        );
        result.pending = Some(pending);
        return result;
    }

    let is_baseline = previous_files.is_empty();
    let added: Vec<String> = current_files
        .keys()
        .filter(|k| !previous_files.contains_key(*k))
        .cloned()
        .collect();
    let removed: Vec<String> = previous_files
        .keys()
        .filter(|k| !current_files.contains_key(*k))
        .cloned()
        .collect();
    let modified_paths: Vec<String> = current_files
        .iter()
        .filter(|(k, v)| previous_files.get(*k).map(|ov| ov != *v).unwrap_or(false))
        .map(|(k, _)| k.clone())
        .collect();
    let modified = build_modified_entries(
        &modified_paths,
        &previous_files,
        &current_files,
        &previous_texts,
        &current_texts,
    );

    let record = json!({
        "time": now_beijing(),
        "pack_id": target_id,
        "deploy_id": target_deploy,
        "pack_format": target_format,
        "pack_hash": target_hash,
        "total_files": current_files.len(),
        "added": if is_baseline { json!([]) } else { json!(added) },
        "modified": if is_baseline { json!([]) } else { json!(modified) },
        "removed": if is_baseline { json!([]) } else { json!(removed) },
        "note": if is_baseline { json!("首次建立基线") } else { Value::Null },
    });

    let mut changes = vec![record.as_object().cloned().unwrap_or_default()];
    changes.extend(result.changes.drain(..));
    let kept = filter_recent(changes, "time");
    let kept: Vec<Map<String, Value>> = kept.into_iter().take(MAX_PACK_CHANGE_RECORDS).collect();
    result.changes = kept;
    result.total_files = current_files.len();
    result.status = if is_baseline { "baseline".into() } else { "updated".into() };

    let mut pending = Map::new();
    pending.insert(
        "manifest".into(),
        json!({
            "pack_id": target_id,
            "deploy_id": target_deploy,
            "pack_format": target_format,
            "pack_hash": target_hash,
            "generated_at": now_beijing(),
            "files": current_files,
        }),
    );
    pending.insert(
        "texts".into(),
        json!({
            "pack_id": target_id.clone(),
            "pack_hash": target_hash,
            "generated_at": now_beijing(),
            "texts": current_texts,
        }),
    );
    pending.insert(
        "changes".into(),
        Value::Array(result.changes.iter().cloned().map(Value::Object).collect()),
    );
    result.pending = Some(pending);
    result
}

/// 把 inspect_pack 的结果落盘（按需写，补缓存时只写 texts）。
pub fn commit_pack_result(result: &PackResult, data_dir: &std::path::Path) -> bool {
    let Some(pending) = &result.pending else { return false };
    let mut written = false;
    if let Some(manifest) = pending.get("manifest") {
        let _ = write_json(&manifest_path(data_dir), manifest);
        written = true;
    }
    if let Some(texts) = pending.get("texts") {
        let _ = write_json(&texts_path(data_dir), texts);
        written = true;
    }
    if let Some(changes) = pending.get("changes") {
        let _ = write_json(&pack_changes_path(data_dir), changes);
        written = true;
    }
    written
}
