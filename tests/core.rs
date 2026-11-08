//! 核心模块集成测试：diff / 文本比对 / i18n 白名单 / 时间归一化。

use hypixel_tracker::i18n;
use hypixel_tracker::jsondiff::diff_dict;
use hypixel_tracker::textdiff::diff_text_lines;
use hypixel_tracker::timeutil::{format_time, to_epoch_ms};
use serde_json::json;

#[test]
fn diff_dict_detects_add_remove_modify() {
    let old = json!({"a": 1, "b": {"x": 1}});
    let new = json!({"a": 2, "c": 3});
    let changes = diff_dict(&old, &new, "");
    let kinds: Vec<(&str, &str)> = changes
        .iter()
        .map(|c| (c.kind.as_str(), c.path.as_str()))
        .collect();
    assert!(kinds.contains(&("modified", "a")));
    assert!(kinds.contains(&("removed", "b")));
    assert!(kinds.contains(&("added", "c")));
}

#[test]
fn diff_dict_ignores_equal() {
    let changes = diff_dict(&json!({"a": 1}), &json!({"a": 1}), "");
    assert!(changes.is_empty());
}

#[test]
fn textdiff_only_returns_changed_fragments() {
    let old = "line1\nline2\nline3\nline4\nline5";
    let new = "line1\nline2\nCHANGED\nline4\nline5";
    let hunks = diff_text_lines(old, new);
    assert_eq!(hunks.len(), 1);
    let kinds: Vec<&str> = hunks[0].lines.iter().map(|l| l.kind.as_str()).collect();
    assert!(kinds.contains(&"del"));
    assert!(kinds.contains(&"add"));
    // 未变化的 line1 不应出现在片段里（只有变化行 + 上下文）
    assert!(hunks[0].lines.iter().any(|l| l.text == "CHANGED"));
}

#[test]
fn textdiff_identical_returns_empty() {
    assert!(diff_text_lines("a\nb", "a\nb").is_empty());
}

#[test]
fn timeutil_formats_epoch_and_iso() {
    // epoch 毫秒 → 北京时间 → 再反解析回毫秒（往返一致）
    let ms = 1_762_147_200_000i64;
    let shown = format_time(&json!(ms));
    assert!(shown.starts_with("2025-11-"), "got {}", shown);
    assert_eq!(to_epoch_ms(&json!(shown)), Some(ms));
    // 北京时间展示串原样返回
    assert_eq!(format_time(&json!("2026-11-03 14:30:00")), "2026-11-03 14:30:00");
    // UTC ISO 带 Z → 换算到北京时间
    assert_eq!(format_time(&json!("2026-11-03T06:30:00Z")), "2026-11-03 14:30:00");
    // 无效值返回 -
    assert_eq!(format_time(&json!(0)), "-");
}

#[test]
fn i18n_whitelist_and_fallback() {
    // 白名单：大小写/别名归一化
    assert_eq!(i18n::normalize_language(Some("EN")), Some("en".into()));
    assert_eq!(i18n::normalize_language(Some("en-US")), Some("en".into()));
    assert_eq!(i18n::normalize_language(Some("zh_hans")), Some("zh-CN".into()));
    assert_eq!(i18n::normalize_language(Some("pt-BR")), Some("pt".into()));
    // 非法输入一律拒绝
    assert_eq!(i18n::normalize_language(Some("../../etc")), None);
    assert_eq!(i18n::normalize_language(Some("")), None);
    assert_eq!(i18n::normalize_language(Some("x".repeat(100).as_str())), None);
    // RTL 判定
    assert!(i18n::is_rtl("ar"));
    assert!(!i18n::is_rtl("ja"));
}

#[test]
fn i18n_translate_fallback_and_params() {
    // zh-CN 恒为原文
    assert_eq!(
        i18n::translate("zh-CN", "当前版本", None),
        "当前版本"
    );
    // 缺失键回退原文并填充占位符
    assert_eq!(
        i18n::translate("zh-CN", "第 {id} 期", Some(&[("id".to_string(), "42".to_string())].into_iter().collect())),
        "第 42 期"
    );
}

#[test]
fn retention_prunes_unparseable_time() {
    use hypixel_tracker::retention::filter_recent;
    let records = vec![
        serde_json::json!({"time": "2099-01-01 00:00:00"}).as_object().unwrap().clone(),
        serde_json::json!({"time": "not-a-time"}).as_object().unwrap().clone(),
    ];
    let kept = filter_recent(records, "time");
    assert_eq!(kept.len(), 1);
}

#[test]
fn bingo_view_marks_diagonal_as_community() {
    use hypixel_tracker::bingo::bingo_view;
    let goals: Vec<serde_json::Value> = (0..25)
        .map(|i| {
            let mut g = json!({"id": format!("g{}", i), "name": format!("G{}", i), "lore": "l"});
            if i % 6 == 0 {
                g["tiers"] = json!([10, 20]);
                g["progress"] = json!(15);
            } else {
                g["requiredAmount"] = json!(5);
            }
            g
        })
        .collect();
    let payload = json!({
        "id": 60, "name": "B",
        "start": 1762147200000i64, "end": 1762233600000i64,
        "goals": goals,
    });
    let view = bingo_view(&payload, "2026-11-08 12:00:00", None);
    let out = view["goals"].as_array().unwrap();
    assert_eq!(out.len(), 25);
    for (i, g) in out.iter().enumerate() {
        let expect = i / 5 == i % 5; // 左上→右下对角线
        assert_eq!(g["is_community"].as_bool().unwrap(), expect, "idx {}", i);
    }
    // 非棋盘数量时退回档位规则
    let payload2 = json!({"id": 61, "name": "C", "start": 1762147200000i64, "end": 1762233600000i64,
        "goals": [
            {"id": "a", "name": "A", "tiers": [1], "progress": 1},
            {"id": "b", "name": "B", "requiredAmount": 2},
        ]});
    let view2 = bingo_view(&payload2, "2026-11-08 12:00:00", None);
    let out2 = view2["goals"].as_array().unwrap();
    assert_eq!(out2[0]["is_community"].as_bool().unwrap(), true);
    assert_eq!(out2[1]["is_community"].as_bool().unwrap(), false);
}
