//! 展示层中文文案（原 build_dashboard.py 顶部常量集中处）。
//!
//! 数据与日志里保留原始英文标识，仅在展示层翻译成中文。

pub fn endpoint_label(name: &str) -> String {
    match name {
        "collections" => "收藏".into(),
        "skills" => "技能".into(),
        "items" => "物品".into(),
        "bazaar" => "集市".into(),
        "packs" => "资源包".into(),
        "bingo" => "Bingo 任务".into(),
        "firesales" => "限时特卖".into(),
        other => other.to_string(),
    }
}

pub fn type_label(kind: &str) -> String {
    match kind {
        "added" => "新增".into(),
        "removed" => "删除".into(),
        "modified" => "修改".into(),
        "value_changed" => "修改".into(),
        "list_length_changed" => "列表长度变化".into(),
        "version" => "版本更新".into(),
        other => other.to_string(),
    }
}

pub fn status_label(status: &str) -> String {
    match status {
        "ok" => "正常".into(),
        "error" => "异常".into(),
        other => other.to_string(),
    }
}

pub fn sync_label(status: &str) -> String {
    match status {
        "aligned" => "版本一致".into(),
        "syncing" => "版本同步中".into(),
        "partial" => "部分接口未返回版本".into(),
        "unknown" => "尚未取得版本".into(),
        other => other.to_string(),
    }
}

pub fn pack_status_label(status: &str) -> String {
    match status {
        "unchanged" => "资源包未更新，沿用上次清单".into(),
        "updated" => "资源包已更新并完成比对".into(),
        "baseline" => "已建立资源包文件基线".into(),
        "error" => "资源包检查失败".into(),
        "unavailable" => "暂无可用资源包".into(),
        "texts_backfilled" => "已补齐文本缓存（包内容无变化）".into(),
        other => other.to_string(),
    }
}

/// 摘要字段名 -> 中文（build_dashboard.SUMMARY_LABELS）。
pub fn summary_label(key: &str) -> String {
    match key {
        "collection_count" => "收藏分类数".into(),
        "total_items" => "收藏物品总数".into(),
        "skill_count" => "技能数".into(),
        "item_count" => "物品数".into(),
        "product_count" => "集市商品数".into(),
        "pack_count" => "资源包数".into(),
        "pack_format_count" => "格式版本数".into(),
        "selected_pack_format" => "选取格式（最大）".into(),
        "deploy_id" => "部署 ID".into(),
        "bingo_name" => "期次".into(),
        "bingo_goal_count" => "任务数".into(),
        "bingo_state" => "状态".into(),
        "bingo_window" => "活动时间".into(),
        "firesale_total" => "特卖期数".into(),
        "firesale_upcoming" => "即将开始".into(),
        "firesale_active" => "进行中".into(),
        other => other.to_string(),
    }
}

pub const VERSION_ENDPOINTS: [&str; 2] = ["collections", "skills"];
pub const META_KEYS: [&str; 3] = ["version", "lastUpdated", "success"];
pub const MAX_CHANGE_LOG: usize = 500;
pub const MAX_LOG_VALUE_CHARS: usize = 1500;
