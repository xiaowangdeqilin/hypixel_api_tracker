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
fn append_changes_dedupes_identical_transitions() {
    use hypixel_tracker::state::append_changes;
    use serde_json::Map;
    let now = hypixel_tracker::retention::now();
    let make = |path: &str, new_val: &str| {
        let mut m = Map::new();
        m.insert("endpoint".into(), json!("items"));
        m.insert("path".into(), json!(path));
        m.insert("type".into(), json!("modified"));
        m.insert("old".into(), json!("A"));
        m.insert("new".into(), json!(new_val));
        m.insert("time".into(), json!(now));
        m
    };

    // 两次构建用旧快照做基线，会把同一条 diff 原样再报一遍：只保留一条
    let mut existing: Vec<Map<String, serde_json::Value>> = vec![make("a.b", "B")];
    append_changes(&mut existing, vec![make("a.b", "B"), make("c.d", "D")]);
    assert_eq!(existing.len(), 2);
    assert_eq!(existing[0]["path"], json!("c.d"));

    // 值真的来回跳（B -> A -> B）：新旧值不同，不算重复，照常保留
    let mut existing: Vec<Map<String, serde_json::Value>> = vec![make("a.b", "B")];
    let mut back = make("a.b", "B");
    back.insert("old".into(), json!("B"));
    back.insert("new".into(), json!("A"));
    append_changes(&mut existing, vec![back]);
    assert_eq!(existing.len(), 2);
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

    // 空配置 → 未配置时 visible=false，页面整块不渲染（不显示占位卡）
    let empty = secret_bingo::view(&json!({"goals": []}), 0);
    assert_eq!(empty["configured"], json!(false));
    assert_eq!(empty["visible"], json!(false));
    assert_eq!(empty["goal_count"], json!(0));
    assert_eq!(
        hypixel_tracker::render_site::render_secret_panel(&empty),
        ""
    );
}

#[test]
fn bazaar_diff_detects_added_products_in_snapshot_format() {
    use hypixel_tracker::fetch::diff_for_endpoint;
    // 回归：build.rs 传给 diff 的两侧都是快照 payload（压缩成 product_ids 数组），
    // 此前 product_ids() 只认 products 对象，导致商品增删永远检不出来。
    let old = json!({"product_ids": ["COAL", "LOG", "WHEAT"]});
    let new = json!({"product_ids": ["COAL", "LOG", "WHEAT", "ENCHANTMENT_LOOTING_6"]});
    let changes = diff_for_endpoint("bazaar", &old, &new);
    assert_eq!(changes.len(), 1);
    assert_eq!(changes[0].kind, "added");
    assert_eq!(changes[0].path, "products.ENCHANTMENT_LOOTING_6");

    // 删除同样要能检出
    let changes2 = diff_for_endpoint("bazaar", &new, &old);
    assert_eq!(changes2.len(), 1);
    assert_eq!(changes2[0].kind, "removed");

    // 混合形态：旧快照含完整 products 对象、新快照是压缩列表，也应可比对
    let old_full = json!({"products": {"COAL": {"a": 1}, "LOG": {"a": 1}, "WHEAT": {"a": 1}}});
    let changes3 = diff_for_endpoint("bazaar", &old_full, &new);
    assert_eq!(changes3.len(), 1);
    assert_eq!(changes3[0].kind, "added");
}

#[test]
fn secret_bingo_hides_outside_event_window() {
    use hypixel_tracker::render_site::render_secret_panel;
    use hypixel_tracker::secret_bingo::{view, HINTS_PER_GOAL};
    // 配置了任务但活动还没开始 → 不显示面板（便于提前把提示写进配置）
    let raw = json!({
        "event_id": 60,
        "start": "2026-12-01 12:00:00",
        "end": "2026-12-08 12:00:00",
        "goals": [{"id": "s1", "name": "秘密一", "hints": [{"text": "h1", "reveal_at": "2026-12-01 20:00:00"}]}],
    });
    let before = view(&raw, 1_790_000_000_000); // 2026-09 左右，早于 start
    assert_eq!(before["configured"], json!(true));
    assert_eq!(before["visible"], json!(false));
    assert_eq!(render_secret_panel(&before), "");

    // 窗口内 → 显示
    let start_ms = hypixel_tracker::timeutil::to_epoch_ms(&json!("2026-12-02 12:00:00")).unwrap();
    let during = view(&raw, start_ms);
    assert_eq!(during["visible"], json!(true));
    let html = render_secret_panel(&during);
    assert!(html.contains("data-secret-panel"));
    assert!(html.contains("秘密一"));
    assert_eq!(during["hints_per_goal"], json!(HINTS_PER_GOAL));

    // 结束后 → 也隐藏
    let end_ms = hypixel_tracker::timeutil::to_epoch_ms(&json!("2026-12-09 12:00:00")).unwrap();
    let after = view(&raw, end_ms);
    assert_eq!(after["visible"], json!(false));
    assert_eq!(render_secret_panel(&after), "");
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

#[test]
fn version_history_prunes_older_than_30_days_and_caps_at_20() {
    use chrono::Duration;
    use hypixel_tracker::state::resolve_version_state;
    let now = hypixel_tracker::timeutil::now_beijing();
    let base = hypixel_tracker::timeutil::parse_beijing(&now).unwrap();
    let stamp = |days_ago: i64| (base - Duration::days(days_ago)).format("%Y-%m-%d %H:%M:%S").to_string();

    // 1 条 40 天前（应被删除）+ 25 条 1 天前（应截断到 20 条）
    let mut history: Vec<serde_json::Value> = vec![serde_json::json!({
        "version": "0.0.1", "time": stamp(40), "note": "过期", "previous": "-"
    })];
    for i in 0..25 {
        history.push(serde_json::json!({
            "version": format!("0.0.{}", i + 2), "time": stamp(1), "note": "近期", "previous": "-"
        }));
    }
    let runtime = serde_json::json!({
        "stable_version": "0.27.2.25",
        "versions": {"collections": {"version": "0.27.2.25"}, "skills": {"version": "0.27.2.25"}},
        "history": history,
    });
    let observed: std::collections::BTreeMap<String, String> = std::collections::BTreeMap::new();
    let out = resolve_version_state(&runtime, &observed, &now);
    let kept = out["history"].as_array().unwrap();
    assert_eq!(kept.len(), hypixel_tracker::state::MAX_VERSION_HISTORY);
    assert!(
        kept.iter().all(|h| h["version"] != serde_json::json!("0.0.1")),
        "超过 30 天的记录应被删除"
    );
}

#[test]
fn static_site_has_language_and_timezone_switchers() {
    use hypixel_tracker::render_site::build_site_html;
    let html = build_site_html(
        "2026-11-10 12:00:00",
        &serde_json::json!({}),
        &[],
        &serde_json::json!({}),
        "0",
        "test",
        &serde_json::json!({}),
        &[],
        &serde_json::json!({}),
        false,
        &serde_json::Value::Null,
        &serde_json::json!({}),
        None,
        &[],
    )
    .expect("静态站应能渲染");
    // 语言切换：103 种语言（默认 zh-CN）
    assert!(html.contains("data-site-lang"), "应有语言切换器");
    let options = html.matches("<option value=\"").count();
    assert!(options >= 100, "语言选项应 >= 100，实际 {}", options);
    // 词条包与时区配置（静态页默认北京时间，可切换）
    assert!(html.contains("id=\"i18n-packs\""), "应内嵌词条包");
    assert!(html.contains("data-tz-switch"), "应有时区切换器");
    assert!(
        html.contains("\"follow_browser\":false"),
        "静态页默认应为北京时间"
    );
    // 文案应说明可切换，而不是「固定不可修改」
    assert!(!html.contains("固定不可修改"), "不应再声称时区不可修改");
    assert!(html.contains("data-k="), "应有构建时词条标注");
    // 端点卡片标题（含「限时特卖」这种与面板标题不同的短词条）也要被标注
    assert!(
        html.contains("data-k=\"限时特卖\""),
        "特卖卡片标题应被标注为可翻译"
    );
    // 主题切换按钮的文案由前端脚本按 window.I18N 重写
    assert!(
        html.contains("THEME_REFRESH"),
        "静态页应支持语言切换后刷新主题按钮文案"
    );
}

#[test]
fn every_language_pack_is_complete_and_safe() {
    // 静态页把 packs 整包内联进 HTML，任何一条缺失/残留中文都会直接显示在页面上
    let path = hypixel_tracker::state::base_dir()
        .join("i18n-src")
        .join("translations.json");
    let text = std::fs::read_to_string(&path).expect("应能读到 i18n-src/translations.json");
    let value: serde_json::Value = serde_json::from_str(&text).expect("词条文件应为合法 JSON");
    let packs = value["packs"].as_object().expect("应有 packs");
    let keys: Vec<String> = packs["en"]
        .as_object()
        .expect("en 包")
        .keys()
        .cloned()
        .collect();
    assert!(keys.len() >= 160, "词条数应 >= 160，实际 {}", keys.len());
    assert!(packs.len() >= 100, "语言数应 >= 100，实际 {}", packs.len());

    // 「个 / 档」是量词，英文等语言用空串表示「不需要这个成分」
    let may_be_empty = ["个", "档"];
    let cjk = |s: &str| s.chars().any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c));

    for (code, pack) in packs {
        let obj = pack.as_object().unwrap_or_else(|| panic!("{} 应是对象", code));
        for key in &keys {
            let val = obj
                .get(key)
                .unwrap_or_else(|| panic!("{} 缺少词条：{}", code, key))
                .as_str()
                .unwrap_or_else(|| panic!("{} 的词条不是字符串：{}", code, key));
            if val.is_empty() {
                assert!(
                    may_be_empty.contains(&key.as_str()),
                    "{} 的词条为空：{}",
                    code,
                    key
                );
                continue;
            }
            // 日文、繁体中文本身使用汉字；其余语言不应残留简体中文原文
            if code != "ja" && code != "zh-TW" && val != key.as_str() {
                assert!(
                    !cjk(val),
                    "{} 的译文残留中文：{} -> {}",
                    code,
                    key,
                    val
                );
            }
            // 译文会被直接插进 HTML：只拦截真正的注入片段。
            // 正文里出现「JavaScript: 已禁用」这类专有名词是合法的，
            // 危险的是 `href="javascript:…"` 这种属性上下文，所以要看前一个字符。
            let lower = val.to_ascii_lowercase();
            let has_tag = ["<script", "<iframe", "<object", "<embed", "onerror=", "onload="]
                .iter()
                .any(|needle| lower.contains(needle));
            let has_scheme = lower.match_indices("javascript:").any(|(idx, _)| {
                lower[..idx]
                    .chars()
                    .next_back()
                    .is_some_and(|prev| matches!(prev, '"' | '\'' | '=' | '('))
            });
            assert!(
                !has_tag && !has_scheme,
                "{} 的译文不得包含可执行内容：{}",
                code,
                key
            );
        }
    }
}

#[test]
fn scan_zip_extracts_font_blobs_and_skips_limits() {
    use hypixel_tracker::packs::{is_font_file, scan_zip};

    // 内存里构造一个含字体 + 文本 + 其它二进制的 zip
    let ttf: Vec<u8> = (0..2048).map(|i| (i % 251) as u8).collect();
    let otf: Vec<u8> = vec![7u8; 512];
    let mut buf = std::io::Cursor::new(Vec::new());
    {
        let mut w = zip::ZipWriter::new(&mut buf);
        let opts = zip::write::FileOptions::default();
        w.start_file("assets/minecraft/font/demo.ttf", opts).unwrap();
        std::io::Write::write_all(&mut w, &ttf).unwrap();
        w.start_file("assets/minecraft/font/mono.otf", opts).unwrap();
        std::io::Write::write_all(&mut w, &otf).unwrap();
        w.start_file("assets/minecraft/lang/en_us.json", opts).unwrap();
        std::io::Write::write_all(&mut w, b"{\"a\":1}").unwrap();
        w.start_file("assets/minecraft/textures/block.png", opts).unwrap();
        std::io::Write::write_all(&mut w, &[1u8; 64]).unwrap();
        w.finish().unwrap();
    }
    let bytes = buf.into_inner();

    assert!(is_font_file("font/a.ttf"));
    assert!(is_font_file("font/A.WOFF2"));
    assert!(!is_font_file("textures/block.png"));

    let (files, texts, fonts) = scan_zip(&bytes).expect("zip 应能解析");
    assert_eq!(fonts.len(), 2, "应提取出 2 个字体文件");
    let ttf_blob = fonts.iter().find(|f| f.path.ends_with(".ttf")).unwrap();
    assert_eq!(ttf_blob.bytes.len(), ttf.len());
    assert_eq!(ttf_blob.ext(), ".ttf");
    // 哈希应与文件清单一致
    assert_eq!(files.get("assets/minecraft/font/demo.ttf").unwrap(), &ttf_blob.hash);
    // 文本缓存不受影响
    assert!(texts.contains_key("assets/minecraft/lang/en_us.json"));
    // png 不进字体也不进文本
    assert!(fonts.iter().all(|f| !f.path.ends_with(".png")));
}

#[test]
fn pack_fonts_section_empty_when_no_fonts() {
    use hypixel_tracker::render_site::render_pack_panel;
    // 无字体清单时面板不应出现字体小节
    let html = render_pack_panel(
        &serde_json::json!({"status": "unchanged", "pack": null, "total_files": 0}),
        &[],
        &[],
    );
    assert!(!html.contains("pack-fonts"), "无字体时不应渲染字体小节");
    // 有字体清单时应渲染小节与示例
    let fonts = vec![serde_json::Map::from_iter([
        ("path".to_string(), serde_json::json!("assets/minecraft/font/demo.ttf")),
        ("family".to_string(), serde_json::json!("minecraft-font-demo")),
        ("file".to_string(), serde_json::json!("assets/fonts/abc.ttf")),
    ])];
    let html = render_pack_panel(
        &serde_json::json!({"status": "unchanged", "pack": null, "total_files": 0}),
        &[],
        &fonts,
    );
    assert!(html.contains("pack-fonts-list"), "有字体时应渲染字体小节");
    assert!(html.contains("assets/fonts/abc.ttf"), "应包含字体下载链接");
}
