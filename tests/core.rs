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

#[test]
fn append_changes_has_no_count_cap() {
    use hypixel_tracker::state::append_changes;
    use serde_json::Map;
    let now = hypixel_tracker::retention::now();
    let make = |path: String| {
        let mut m = Map::new();
        m.insert("time".into(), json!(now));
        m.insert("path".into(), json!(path));
        m
    };
    // 600 条仍在保留期内：超过旧的 500 条上限也应全部保留
    let mut existing: Vec<Map<String, serde_json::Value>> =
        (0..600).map(|i| make(format!("p{}", i))).collect();
    append_changes(&mut existing, vec![make("new".into())]);
    assert_eq!(existing.len(), 601);
    assert_eq!(existing[0]["path"], json!("new"));
}

#[test]
fn bingo_goal_progress_caps_at_max_tier() {
    use hypixel_tracker::bingo::goal_progress;
    // 5 档满级：progress 超出最高档时，展示值封顶、百分比 100%、标记满级
    let goal = json!({"tiers": [10, 20, 30, 40, 50], "progress": 999});
    let p = goal_progress(&goal).unwrap();
    assert_eq!(p["is_maxed"], json!(true));
    assert_eq!(p["percent"], json!(100.0));
    assert_eq!(p["progress_text"], json!("50"));
    assert_eq!(p["reached"], json!(5));
    assert_eq!(p["total"], json!(5));

    // 未满级不受影响
    let goal2 = json!({"tiers": [10, 20, 30], "progress": 15});
    let p2 = goal_progress(&goal2).unwrap();
    assert_eq!(p2["is_maxed"], json!(false));
    assert_eq!(p2["progress_text"], json!("15"));
    assert_eq!(p2["reached"], json!(1));
}

#[test]
fn percent_text_keeps_trailing_zeros() {
    use hypixel_tracker::bingo::format_percent;
    // 回归：100.0 曾被裁零成 "1"，页面显示 1%
    assert_eq!(format_percent(100.0), "100");
    assert_eq!(format_percent(50.0), "50");
    assert_eq!(format_percent(20.0), "20");
    assert_eq!(format_percent(0.0), "0");
    assert_eq!(format_percent(12.5), "12.5");
    assert_eq!(format_percent(6.25), "6.3");
    assert_eq!(format_percent(99.9), "99.9");
}

#[test]
fn bingo_panel_renders_full_percent() {
    use hypixel_tracker::bingo::bingo_view;
    use hypixel_tracker::render_site::render_bingo_panel;
    // 满级任务：页面徽章必须显示 100%，不能变成 1%
    let goals: Vec<serde_json::Value> = (0..25)
        .map(|i| json!({"name": format!("g{}", i), "lore": "x", "tiers": [10, 20, 30, 40, 50], "progress": 999}))
        .collect();
    let raw = json!({"id": "1", "start": 0, "end": 0, "goals": goals});
    let view = bingo_view(&raw, "2026-01-01 00:00:00", None);
    let html = render_bingo_panel(&view, false);
    assert!(html.contains("100%"), "满级任务应显示 100%");
    assert!(!html.contains(">1%<"), "不应出现被裁零后的 1%");

    // 进度一半：50%
    let goals2: Vec<serde_json::Value> = (0..25)
        .map(|i| {
            if i % 6 == 0 {
                json!({"name": format!("g{}", i), "lore": "x", "tiers": [10, 20, 30, 40], "progress": 20})
            } else {
                json!({"name": format!("g{}", i), "lore": "x"})
            }
        })
        .collect();
    let raw2 = json!({"id": "1", "start": 0, "end": 0, "goals": goals2});
    let view2 = bingo_view(&raw2, "2026-01-01 00:00:00", None);
    let html2 = render_bingo_panel(&view2, false);
    assert!(html2.contains("50%"));
}

#[test]
fn secret_bingo_unlocks_hints_by_time() {
    use hypixel_tracker::secret_bingo;
    // 4 条提示打乱顺序给出：按时间排序后取前 3 条（每个任务固定 3 条），
    // now = 2026-01-06 12:00（北京时间）时只有第一条已解锁。
    let raw = json!({
        "event_id": 59,
        "goals": [{
            "id": "s1",
            "name": "测试秘密任务",
            "hints": [
                {"text": "第三条", "reveal_at": "2026-01-07 20:00:00"},
                {"text": "第一条", "reveal_at": "2026-01-05 20:00:00"},
                {"text": "第二条", "reveal_at": "2026-01-06 20:00:00"},
                {"text": "多余的第四条", "reveal_at": "2026-01-08 20:00:00"}
            ]
        }]
    });
    let now = to_epoch_ms(&json!("2026-01-06 12:00:00")).unwrap();
    let view = secret_bingo::view(&raw, now);
    assert_eq!(view["configured"], json!(true));
    assert_eq!(view["hints_per_goal"], json!(3));
    let goals = view["goals"].as_array().unwrap();
    assert_eq!(goals.len(), 1);
    let hints = goals[0]["hints"].as_array().unwrap();
    assert_eq!(hints.len(), 3);
    // 依次给出：按解锁时间升序
    assert_eq!(hints[0]["text"], json!("第一条"));
    assert_eq!(hints[1]["text"], json!("第二条"));
    assert_eq!(hints[2]["text"], json!("第三条"));
    assert_eq!(hints[0]["unlocked"], json!(true));
    assert_eq!(hints[1]["unlocked"], json!(false));
    assert_eq!(hints[2]["unlocked"], json!(false));
    assert_eq!(goals[0]["unlocked_count"], json!(1));
    assert_eq!(goals[0]["total_hints"], json!(3));
    assert!(goals[0]["next_reveal_ms"].as_i64().is_some());
}

#[test]
fn secret_bingo_pads_missing_hints() {
    use hypixel_tracker::secret_bingo;
    // 配置不足 3 条时补空槽；没有 unlock 时间的提示视为立即可见
    let raw = json!({"goals": [{"id": "a", "hints": [{"text": "唯一一条提示"}]}]});
    let view = secret_bingo::view(&raw, 0);
    let goal = &view["goals"][0];
    let hints = goal["hints"].as_array().unwrap();
    assert_eq!(hints.len(), 3);
    assert_eq!(hints[0]["text"], json!("唯一一条提示"));
    assert_eq!(hints[0]["unlocked"], json!(true));
    assert_eq!(hints[1]["populated"], json!(false));
    assert_eq!(goal["configured_hints"], json!(1));

    // 空配置 → configured=false，渲染层显示预留位
    let empty = secret_bingo::view(&json!({"goals": []}), 0);
    assert_eq!(empty["configured"], json!(false));
    assert_eq!(empty["goal_count"], json!(0));
    let placeholder = secret_bingo::placeholder_view(3);
    assert_eq!(placeholder["goals"].as_array().unwrap().len(), 3);
    assert_eq!(placeholder["configured"], json!(false));
}

#[test]
fn personal_goal_has_no_server_progress() {
    use hypixel_tracker::bingo::{bingo_view, goal_progress};
    // 个人任务（无 tiers）不应产出全服进度，页面因此不会渲染进度条与百分比
    let goal = json!({"id": "p1", "name": "个人", "requiredAmount": 5});
    assert!(goal_progress(&goal).is_none());
    let payload = json!({
        "id": 59, "name": "B",
        "start": 1762147200000i64, "end": 1762233600000i64,
        "goals": (0..25).map(|i| {
            if i % 6 == 0 {
                json!({"id": format!("c{}", i), "name": "C", "tiers": [10], "progress": 5})
            } else {
                json!({"id": format!("p{}", i), "name": "P", "requiredAmount": 3})
            }
        }).collect::<Vec<_>>(),
    });
    let view = bingo_view(&payload, "2026-11-08 12:00:00", None);
    for (i, g) in view["goals"].as_array().unwrap().iter().enumerate() {
        let has_progress = g.get("progress_info").is_some();
        assert_eq!(has_progress, i % 6 == 0, "idx {}", i);
    }
}
