//! tracker-build：CI 数据管道（原 scripts/build_dashboard.py 的移植）。
//!
//! 四个阶段：
//! 1. 只抓取，不落盘；
//! 2. 只计算（快照、diff、版本状态机、Bingo/特卖视图、资源包检查），不落盘；
//! 3. 判定是否允许落盘——任一接口失败或资源包检查失败 → 整次零改动；
//! 4. 生成站点产物（site/index.html + site/data.json）并输出 data_written。

use std::collections::BTreeMap;
use std::io::Write as _;

use serde_json::{json, Map, Value};

use hypixel_tracker::bingo::bingo_view;
use hypixel_tracker::fetch::{checksum_value, diff_for_endpoint, fetch_json, strip_meta};
use hypixel_tracker::firesale::{build_item_names, firesales_view, normalize_sales};
use hypixel_tracker::labels::{
    endpoint_label, pack_status_label, status_label, sync_label, VERSION_ENDPOINTS,
};
use hypixel_tracker::packs::{
    commit_pack_result, inspect_pack, load_manifest, load_pack_changes, save_pack_changes,
    PackResult,
};
use hypixel_tracker::render_site::build_site_html;
use hypixel_tracker::retention::filter_recent;
use hypixel_tracker::state::{
    self, append_changes, current_build, load_changes, load_runtime_compat, load_snapshot,
    make_change_entry, read_version, resolve_version_state, save_changes, save_runtime,
    save_snapshot, write_json,
};
use hypixel_tracker::timeutil::{from_epoch_ms, now_beijing};

const REQUEST_TIMEOUT: u64 = 30;
fn main() {
    let code = run();
    std::process::exit(code);
}

fn run() -> i32 {
    let site_dir = state::base_dir().join("site");
    let _ = std::fs::create_dir_all(&site_dir);

    let dashboard_version = read_version();
    let build = current_build();
    let generated_at = now_beijing();
    let runtime = load_runtime_compat();
    let data_dir = state::data_dir();

    // ---- 阶段 1：只抓取，不落盘 ------------------------------------------
    let mut payloads: BTreeMap<String, Value> = BTreeMap::new();
    let mut fetch_errors: BTreeMap<String, String> = BTreeMap::new();
    for (name, url, _) in hypixel_tracker::fetch::endpoints() {
        match fetch_json(url, REQUEST_TIMEOUT) {
            Ok(payload) => {
                payloads.insert(name.to_string(), payload);
            }
            Err(err) => {
                fetch_errors.insert(name.to_string(), err);
            }
        }
    }

    // ---- 阶段 2：只计算，不落盘 ------------------------------------------
    let mut endpoints_result: Map<String, Value> = Map::new();
    let mut new_changes: Vec<Map<String, Value>> = Vec::new();
    let mut observed_versions: std::collections::BTreeMap<String, String> = std::collections::BTreeMap::new();
    let mut pending_snapshots: BTreeMap<String, Value> = BTreeMap::new();

    for (name, payload) in &payloads {
        // 版本观测：collections 与 skills 顶层带 version；items 没有，不参与。
        if VERSION_ENDPOINTS.contains(&name.as_str()) {
            if let Some(v) = payload.get("version").and_then(|v| v.as_str()) {
                if !v.is_empty() {
                    observed_versions.insert(name.clone(), v.to_string());
                }
            }
        }

        let snapshot = make_snapshot(name, payload, &generated_at);
        let previous = load_snapshot(name);
        let change_count = match &previous {
            None => {
                endpoints_result.insert(
                    name.clone(),
                    json!({
                        "status": "ok",
                        "fetched_at": snapshot["fetched_at"],
                        "summary": snapshot["summary"],
                        "note": "first snapshot",
                    }),
                );
                None
            }
            Some(prev) => {
                // 历史快照可能仍含元数据，比对前统一剥离，保证规则一致。
                let old_payload = strip_meta(prev.get("payload").unwrap_or(&Value::Null));
                let changes = diff_for_endpoint(name, &old_payload, &snapshot["payload"]);
                let now = generated_at.clone();
                for c in &changes {
                    new_changes.push(make_change_entry(
                        name,
                        &c.kind,
                        &c.path,
                        c.old.clone(),
                        c.new.clone(),
                        c.old_full.clone(),
                        c.new_full.clone(),
                        &now,
                    ));
                }
                endpoints_result.insert(
                    name.clone(),
                    json!({
                        "status": "ok",
                        "fetched_at": snapshot["fetched_at"],
                        "summary": snapshot["summary"],
                        "change_count": changes.len(),
                    }),
                );
                Some(changes.len())
            }
        };
        let _ = change_count;
        pending_snapshots.insert(name.clone(), snapshot);
    }

    let version_info = resolve_version_state(&runtime, &observed_versions, &generated_at);

    // Bingo 展示数据：保留原始 goals（含颜色码与全服进度）；
    // 同一期的 first_seen 沿用上次记录，避免每次运行都把首发时间刷新掉。
    let previous_bingo =
        state::load_json(&data_dir.join("bingo.json")).filter(|v| v.is_object());
    let bingo_data = payloads
        .get("bingo")
        .map(|payload| bingo_view(payload, &generated_at, previous_bingo.as_ref()))
        .unwrap_or(Value::Null);

    // 限时特卖展示数据：每次成功抓取都整体重建（开始前可能被官方调整，
    // 不做「内容没变就跳过」的省略），物品名称从 items 端点解析。
    let mut firesales_data: Value = payloads
        .get("firesales")
        .map(|payload| {
            let items_payload = match payloads.get("items") {
                Some(p) => Some(p.clone()),
                None => load_snapshot("items")
                    .map(|snap| snap.get("payload").cloned().unwrap_or(Value::Null)),
            };
            let names = build_item_names(items_payload.as_ref());
            firesales_view(payload, &generated_at, Some(&names))
        })
        .unwrap_or(Value::Null);

    // 资源包检查：只在 packs 接口成功时运行；检查器本身不写盘。
    let mut pack_result = PackResult {
        status: "unavailable".into(),
        pack: None,
        total_files: 0,
        changes: Vec::new(),
        error: None,
        candidates: Vec::new(),
        pending: None,
    };
    if let Some(packs_payload) = payloads.get("packs") {
        pack_result = std::panic::catch_unwind(|| inspect_pack(packs_payload, &data_dir, false))
            .unwrap_or(PackResult {
                status: "error".into(),
                pack: None,
                total_files: 0,
                changes: Vec::new(),
                error: Some("资源包检查发生内部错误".into()),
                candidates: Vec::new(),
                pending: None,
            });
    }

    // ---- 阶段 3：判定是否允许落盘（失败即整次不做任何变化）----------------
    let mut reasons: Vec<String> = Vec::new();
    if !fetch_errors.is_empty() {
        let failed: Vec<String> = fetch_errors
            .keys()
            .map(|k| endpoint_label(k))
            .collect();
        reasons.push(format!("{} 接口未获取到内容", failed.join("、")));
    }
    if matches!(pack_result.status.as_str(), "error" | "unavailable") {
        let detail = pack_result
            .error
            .clone()
            .unwrap_or_else(|| "无法取得可用的资源包下载地址".into());
        reasons.push(format!("资源包检查未完成（{}）", detail));
    }
    let skipped_reason = if reasons.is_empty() { None } else { Some(reasons.join("；")) };

    let data_written;
    let mut bingo_stale = false;
    let mut all_changes: Vec<Map<String, Value>>;
    let mut pack_changes: Vec<Map<String, Value>>;
    let version_info_written;

    match &skipped_reason {
        None => {
            // 数据齐备，一次性事务式落盘。
            for (name, snapshot) in &pending_snapshots {
                if let Err(e) = save_snapshot(name, snapshot) {
                    eprintln!("[构建] 写快照失败 {}: {}", name, e);
                }
            }
            all_changes = load_changes();
            append_changes(&mut all_changes, new_changes.clone());
            let _ = save_changes(&all_changes);
            let _ = save_runtime(&version_info);
            commit_pack_result(&pack_result, &data_dir);
            if !bingo_data.is_null() {
                let _ = write_json(&data_dir.join("bingo.json"), &bingo_data);
            }
            if !firesales_data.is_null() {
                let _ = write_json(&data_dir.join("firesales.json"), &firesales_data);
            }
            pack_changes = load_pack_changes(&data_dir);
            let (kept, expired) =
                hypixel_tracker::retention::prune_list(pack_changes.clone(), "time");
            if expired > 0 {
                save_pack_changes(&data_dir, &kept);
            }
            pack_changes = kept;
            data_written = true;
            version_info_written = version_info.clone();
        }
        Some(reason) => {
            // 任何失败都不改动 data/ 下的一个字节，页面沿用上次结果。
            data_written = false;
            all_changes = filter_recent(load_changes(), "time");
            let saved_runtime = load_runtime_compat();
            version_info_written = if saved_runtime.as_object().map(|o| !o.is_empty()).unwrap_or(false) {
                saved_runtime
            } else {
                version_info.clone()
            };
            pack_changes = filter_recent(load_pack_changes(&data_dir), "time");
            pack_result = pack_result_from_disk(&data_dir);
            endpoints_result = skipped_view(&pending_snapshots.keys().cloned().collect::<Vec<_>>());
            let saved_bingo = state::load_json(&data_dir.join("bingo.json")).unwrap_or(Value::Null);
            bingo_stale = !saved_bingo.is_null();
            firesales_data = state::load_json(&data_dir.join("firesales.json")).unwrap_or(Value::Null);
            let _ = reason;
        }
    }

    // ---- 阶段 4：生成站点产物（artifact，不属于仓库持久化数据）------------
    let pack_summary = pack_result_to_value(&pack_result);
    let data_payload = json!({
        "version_info": version_info_written,
        "resource_version": version_info_written.get("stable_version").cloned().unwrap_or(json!("未获取")),
        "dashboard_version": dashboard_version,
        "build": build,
        "generated_at": generated_at,
        "endpoints": endpoints_result,
        "changes": all_changes.iter().cloned().map(Value::Object).collect::<Vec<_>>(),
        "pack": pack_summary,
        "pack_changes": pack_changes.iter().cloned().map(Value::Object).collect::<Vec<_>>(),
        "bingo": bingo_data,
        "firesales": firesales_data,
        "data_written": data_written,
        "skipped_reason": skipped_reason.clone().map(Value::String).unwrap_or(Value::Null),
    });
    if let Err(e) = write_json(&site_dir.join("data.json"), &data_payload) {
        eprintln!("[构建] 写 data.json 失败：{}", e);
    }

    // 静态站页面数据视图（跳过模式下与 data.json 保持一致）
    let endpoints_view = if data_written {
        Value::Object(endpoints_result.clone())
    } else {
        serde_json::to_value(&data_payload["endpoints"]).unwrap_or(Value::Null)
    };
    let html = build_site_html(
        &generated_at,
        &endpoints_view,
        &all_changes,
        &version_info_written,
        &dashboard_version,
        &build,
        &pack_summary,
        &pack_changes,
        &bingo_data,
        bingo_stale,
        &firesales_data,
        &hypixel_tracker::secret_bingo::current_view(),
        skipped_reason.as_deref(),
    );
    match html {
        Ok(html) => {
            if let Err(e) = state::write_text(&site_dir.join("index.html"), &html) {
                eprintln!("[构建] 写 index.html 失败：{}", e);
            }
        }
        Err(e) => {
            eprintln!("[构建] 渲染静态站失败：{}", e);
            return 1;
        }
    }

    write_github_output("data_written", if data_written { "true" } else { "false" });

    // ---- 汇总日志 ---------------------------------------------------------
    println!(
        "[构建] 当前版本 v{} ({}) · 仪表盘 v{} · 构建 {} · {}（北京时间）",
        version_info_written.get("stable_version").and_then(|v| v.as_str()).unwrap_or("未获取"),
        sync_label(version_info_written.get("sync_status").and_then(|v| v.as_str()).unwrap_or("unknown")),
        dashboard_version,
        build,
        generated_at
    );
    if data_written {
        if version_info_written.get("confirmed").and_then(|v| v.as_bool()).unwrap_or(false) {
            println!(
                "[构建] 版本已确认切换：{} → {}",
                version_info_written.get("previous_stable").map(|v| v.to_string()).unwrap_or_else(|| "-".into()),
                version_info_written.get("stable_version").and_then(|v| v.as_str()).unwrap_or("-")
            );
        }
        for (name, ep) in &endpoints_result {
            let cc = ep.get("change_count").and_then(|v| v.as_u64()).unwrap_or(0);
            println!(
                "[构建] {}：状态={}，变更 {} 条",
                endpoint_label(name),
                status_label(ep.get("status").and_then(|v| v.as_str()).unwrap_or("error")),
                cc
            );
        }
        println!(
            "[构建] 资源包检查：{}，文件 {} 个",
            pack_status_label(&pack_result.status),
            pack_result.total_files
        );
        if !bingo_data.is_null() {
            println!(
                "[构建] Bingo：第 {} 期 {}（{}），{} 个任务，{} → {}（北京时间）",
                bingo_data.get("id").map(|v| v.to_string()).unwrap_or_else(|| "-".into()),
                bingo_data.get("name").and_then(|v| v.as_str()).unwrap_or("-"),
                bingo_data.get("state_label").and_then(|v| v.as_str()).unwrap_or("-"),
                bingo_data.get("goals").and_then(|g| g.as_array()).map(|a| a.len()).unwrap_or(0),
                bingo_data.get("start").and_then(|v| v.as_str()).unwrap_or("-"),
                bingo_data.get("end").and_then(|v| v.as_str()).unwrap_or("-"),
            );
        }
        if !firesales_data.is_null() {
            println!(
                "[构建] 限时特卖：共 {} 期（即将开始 {} · 进行中 {}），已刷新数据",
                firesales_data.get("total").and_then(|v| v.as_u64()).unwrap_or(0),
                firesales_data.get("upcoming_count").and_then(|v| v.as_u64()).unwrap_or(0),
                firesales_data.get("active_count").and_then(|v| v.as_u64()).unwrap_or(0),
            );
        }
        println!("[构建] 本次新增变更 {} 条", new_changes.len());
    } else {
        println!("[跳过] {}；本次未改动任何仓库数据", skipped_reason.unwrap_or_default());
    }
    println!("[构建] 页面已写入 {}，data_written={}", site_dir.display(), data_written);
    0
}

/// 构造持久化到磁盘的快照字典（已剥离顶层元数据）。
fn make_snapshot(name: &str, payload: &Value, now: &str) -> Value {
    let clean = strip_meta(payload);
    let mut snap = json!({
        "endpoint": name,
        "fetched_at": now,
        "status_code": 200,
        "checksum": checksum_value(&clean),
        "summary": summarize_payload(name, payload),
        "payload": clean,
    });
    if name == "bazaar" {
        // 集市只追踪结构性变化，快照里只保存排序后的商品 id 列表。
        let product_ids: Vec<Value> = payload
            .get("products")
            .and_then(|p| p.as_object())
            .map(|obj| {
                let mut ids: Vec<String> = obj.keys().cloned().collect();
                ids.sort();
                ids.into_iter().map(Value::String).collect()
            })
            .unwrap_or_default();
        snap["payload"] = json!({"product_ids": product_ids});
        snap["checksum"] = Value::String(checksum_value(&snap["payload"]));
    } else if name == "bingo" {
        // Bingo 用规范化结构比对：剔除全服进度与数据刷新时间。
        let bingo_norm = hypixel_tracker::bingo::normalize_bingo(payload);
        snap["payload"] = bingo_norm.clone();
        snap["checksum"] = Value::String(checksum_value(&bingo_norm));
    } else if name == "firesales" {
        // 特卖按 item_id+start 切分成单期记录，时间转成北京时间字符串。
        let sales_norm = json!({"sales": normalize_sales(payload).into_iter().map(Value::Object).collect::<Vec<_>>()});
        snap["payload"] = sales_norm.clone();
        snap["checksum"] = Value::String(checksum_value(&sales_norm));
    }
    snap
}

/// 生成用于仪表盘卡片的简要统计。
fn summarize_payload(name: &str, payload: &Value) -> Value {
    match name {
        "collections" => {
            let coll = payload.get("collections").and_then(|c| c.as_object());
            let count = coll.map(|c| c.len()).unwrap_or(0);
            let total_items = coll
                .map(|c| {
                    c.values()
                        .filter_map(|v| v.get("items").and_then(|i| i.as_object()))
                        .map(|i| i.len())
                        .sum::<usize>()
                })
                .unwrap_or(0);
            json!({"collection_count": count, "total_items": total_items})
        }
        "skills" => json!({
            "skill_count": payload.get("skills").and_then(|s| s.as_object()).map(|s| s.len()).unwrap_or(0)
        }),
        "items" => json!({
            "item_count": payload.get("items").and_then(|i| i.as_array()).map(|a| a.len()).unwrap_or(0)
        }),
        "bazaar" => json!({
            "product_count": payload.get("products").and_then(|p| p.as_object()).map(|p| p.len()).unwrap_or(0)
        }),
        "packs" => {
            let versions = hypixel_tracker::packs::collect_pack_versions(payload);
            let selected = versions
                .iter()
                .filter_map(|v| v.get("pack_format").and_then(|f| f.as_i64()))
                .max();
            let packs = payload.get("packs").and_then(|p| p.as_array()).map(|a| a.len()).unwrap_or(0);
            let first = payload
                .get("packs")
                .and_then(|p| p.as_array())
                .and_then(|a| a.first())
                .and_then(|p| p.get("deployId"))
                .cloned()
                .unwrap_or(json!("-"));
            json!({
                "pack_count": packs,
                "pack_format_count": versions.len(),
                "selected_pack_format": selected.map(Value::from).unwrap_or(json!("-")),
                "deploy_id": first,
            })
        }
        "firesales" => {
            let (total, upcoming, active) = hypixel_tracker::firesale::quick_counts(payload);
            json!({
                "firesale_total": total,
                "firesale_upcoming": upcoming,
                "firesale_active": active,
            })
        }
        "bingo" => {
            let goals = payload.get("goals").and_then(|g| g.as_array()).map(|a| a.len()).unwrap_or(0);
            let start = from_epoch_ms(payload.get("start").unwrap_or(&Value::Null));
            let end = from_epoch_ms(payload.get("end").unwrap_or(&Value::Null));
            let (state, _) = hypixel_tracker::bingo::bingo_state(&start, &end, &now_beijing());
            let name = payload
                .get("name")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .map(String::from)
                .unwrap_or_else(|| format!("第 {} 期", payload.get("id").map(|v| v.to_string()).unwrap_or_else(|| "-".into())));
            json!({
                "bingo_name": name,
                "bingo_goal_count": goals,
                "bingo_state": hypixel_tracker::bingo::bingo_state_label(&state),
            })
        }
        _ => json!({}),
    }
}

/// 跳过模式下从磁盘上的旧快照构造页面卡片（不产生任何新数据）。
fn skipped_view(names: &[String]) -> Map<String, Value> {
    let mut view = Map::new();
    for name in names {
        match load_snapshot(name) {
            Some(snap) => {
                view.insert(
                    name.clone(),
                    json!({
                        "status": "ok",
                        "fetched_at": snap.get("fetched_at").cloned().unwrap_or(json!("-")),
                        "summary": snap.get("summary").cloned().unwrap_or(json!({})),
                    }),
                );
            }
            None => {
                view.insert(
                    name.clone(),
                    json!({"status": "error", "fetched_at": "-", "summary": {}}),
                );
            }
        }
    }
    view
}

/// 跳过模式下从磁盘清单还原资源包状态，保证页面与仓库数据一致。
fn pack_result_from_disk(data_dir: &std::path::Path) -> PackResult {
    let manifest = load_manifest(data_dir);
    let files = manifest.get("files").and_then(|f| f.as_object()).cloned().unwrap_or_default();
    let pack = if files.is_empty() {
        None
    } else {
        let mut p = Map::new();
        p.insert("id".into(), manifest.get("pack_id").cloned().unwrap_or(json!("-")));
        p.insert("pack_format".into(), manifest.get("pack_format").cloned().unwrap_or(json!("-")));
        p.insert("deploy_id".into(), manifest.get("deploy_id").cloned().unwrap_or(json!("-")));
        Some(p)
    };
    PackResult {
        status: "unchanged".into(),
        pack,
        total_files: files.len(),
        changes: Vec::new(),
        error: None,
        candidates: Vec::new(),
        pending: None,
    }
}

/// PackResult → JSON（页面与 data.json 使用）。
fn pack_result_to_value(result: &PackResult) -> Value {
    json!({
        "status": result.status,
        "pack": result.pack.clone().map(Value::Object).unwrap_or(Value::Null),
        "total_files": result.total_files,
        "error": result.error.clone().map(Value::String).unwrap_or(Value::Null),
        "candidates": result.candidates,
    })
}

fn write_github_output(key: &str, value: &str) {
    // 在 Actions 里把结果传给后续步骤；本地运行时是空操作。
    if let Ok(path) = std::env::var("GITHUB_OUTPUT") {
        if let Ok(mut fh) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
            let _ = writeln!(fh, "{}={}", key, value);
        }
    }
}

