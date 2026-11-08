//! 静态站（GitHub Pages）HTML 渲染（原 build_dashboard.py 渲染部分的移植）。
//!
//! 页面骨架在 `templates/site.html.j2`（Tera），各面板由本模块渲染成片段后注入；
//! 样式内联自 `templates/site_style.css`，Bingo/特卖/主题的 CSS/JS 原样内联，
//! 保证静态页与本地服务两端行为一致。

use std::fmt::Write as _;

use serde_json::{json, Map, Value};

use crate::bingo::format_amount;
use crate::escape_html;
use crate::firesale::{format_amount as sale_amount, CURRENCY_LABEL};
use crate::labels::{
    endpoint_label, pack_status_label, status_label, summary_label, sync_label, type_label,
};
use crate::packs::diff_text_lines;
use crate::retention::RETENTION_DAYS;
use crate::state::{base_dir, read_asset};
use crate::timeutil::format_time;

/// 历史变更日志里残留的英文摘要 → 中文展示。
fn localize_text(text: Option<&str>) -> String {
    let Some(text) = text else { return "-".into() };
    if text.is_empty() {
        return "-".into();
    }
    if text == "null" {
        return "空".into();
    }
    if text == "new bazaar product" {
        return "新增集市商品".into();
    }
    if text == "bazaar product" {
        return "集市商品".into();
    }
    if let Some(rest) = text.strip_prefix('{').and_then(|t| t.strip_suffix(" keys}")) {
        if rest.bytes().all(|b| b.is_ascii_digit()) {
            return format!("{{{} 个字段}}", rest);
        }
    }
    if let Some(rest) = text.strip_prefix('[').and_then(|t| t.strip_suffix(" items]")) {
        if rest.bytes().all(|b| b.is_ascii_digit()) {
            return format!("[{}] 项", rest);
        }
    }
    text.to_string()
}

const PACK_FILE_LIMIT: usize = 20;

/// 单条 diff 展示行（含可选的「查看完整值」折叠）。
fn diff_line_html(preview: &Value, full: &Value, css: &str, sign: &str) -> String {
    let preview_text = preview.as_str().unwrap_or("-");
    let text = escape_html(&localize_text(Some(preview_text)));
    let mut details = String::new();
    if let Some(full_text) = full.as_str() {
        if full_text != preview_text && full_text.len() > 80 {
            details = format!(
                "<details class=\"value-details\"><summary>查看完整值</summary><pre>{}</pre></details>",
                escape_html(&localize_text(Some(full_text)))
            );
        }
    }
    format!(
        "<div class=\"diff-line {css}\"><span class=\"sign\">{sign}</span><div class=\"diff-code\"><code>{text}</code>{details}</div></div>"
    )
}

/// 把日志里的 JSON 字符串整理成多行文本，便于逐行比对。
fn pretty_json_text(value: &Value) -> Option<String> {
    let text = value.as_str()?;
    if text.is_empty() {
        return None;
    }
    match serde_json::from_str::<Value>(text) {
        Ok(parsed) if parsed.is_object() || parsed.is_array() => {
            serde_json::to_string_pretty(&parsed).ok()
        }
        _ => Some(text.to_string()),
    }
}

/// 字段修改只保留变化片段：原值 / 新值整理成文本后逐行比对。
fn change_value_hunks(change: &Map<String, Value>) -> Vec<crate::textdiff::Hunk> {
    let old_full = change.get("old_full").cloned().unwrap_or(Value::Null);
    let new_full = change.get("new_full").cloned().unwrap_or(Value::Null);
    let old_text = pretty_json_text(&old_full)
        .or_else(|| {
            change
                .get("old")
                .and_then(|v| v.as_str())
                .map(|s| localize_text(Some(s)))
        })
        .unwrap_or_default();
    let new_text = pretty_json_text(&new_full)
        .or_else(|| {
            change
                .get("new")
                .and_then(|v| v.as_str())
                .map(|s| localize_text(Some(s)))
        })
        .unwrap_or_default();
    if old_text.is_empty() && new_text.is_empty() {
        return Vec::new();
    }
    diff_text_lines(&old_text, &new_text)
}

/// 把逐行 diff 片段渲染成代码审核风格：删除红、新增绿，未变化的段不输出。
pub fn render_hunks(hunks: &[crate::textdiff::Hunk], show_header: bool) -> String {
    if hunks.is_empty() {
        return String::new();
    }
    let mut blocks = String::new();
    for hunk in hunks {
        let mut rows = String::new();
        for line in &hunk.lines {
            let (css, sign) = match line.kind.as_str() {
                "del" => ("diff-del", "-"),
                "add" => ("diff-add", "+"),
                _ => ("diff-ctx", " "),
            };
            let _ = write!(
                rows,
                "<div class=\"diff-line {css}\"><span class=\"sign\">{sign}</span><div class=\"diff-code\"><code>{}</code></div></div>",
                escape_html(&line.text)
            );
        }
        if let Some(truncated) = hunk.truncated {
            let _ = write!(rows, "<div class=\"diff-omitted\">… 本段另有 {} 行未显示</div>", truncated);
        }
        let header = if show_header {
            format!(
                "<div class=\"diff-hunk\">@@ 原值第 {} 行 · 新值第 {} 行 @@</div>",
                hunk.old_start, hunk.new_start
            )
        } else {
            String::new()
        };
        blocks.push_str(&header);
        blocks.push_str(&rows);
    }
    format!("<div class=\"diff-hunks\">{}</div>", blocks)
}

/// 把变更渲染成类似 git diff / code review 的区块：新增纯绿、删除纯红。
pub fn render_change_blocks(changes: &[Map<String, Value>]) -> String {
    if changes.is_empty() {
        return "<div class=\"empty\">暂无变更记录。当某个端点返回与上次快照不同的数据时，变更会以代码审核风格显示在这里。</div>".into();
    }
    let mut blocks = String::new();
    // 不限制条数：只按保留期（30 天）清理，记录多少条就完整展示多少条。
    for change in changes.iter() {
        let raw_type = change.get("type").and_then(|v| v.as_str()).unwrap_or("");
        let badge_css = match raw_type {
            "added" => "badge-add",
            "removed" => "badge-del",
            "modified" => "badge-mod",
            "version" => "badge-ver",
            _ => "badge-mod",
        };
        let mut lines = String::new();
        match raw_type {
            "added" => {
                // 新增：只标绿，不显示任何红色删除行
                lines.push_str(&diff_line_html(
                    change.get("new").unwrap_or(&Value::Null),
                    change.get("new_full").unwrap_or(&Value::Null),
                    "diff-add",
                    "+",
                ));
            }
            "removed" => {
                lines.push_str(&diff_line_html(
                    change.get("old").unwrap_or(&Value::Null),
                    change.get("old_full").unwrap_or(&Value::Null),
                    "diff-del",
                    "-",
                ));
            }
            "modified" => {
                let hunks = change_value_hunks(change);
                if !hunks.is_empty() {
                    lines.push_str(&render_hunks(&hunks, true));
                } else {
                    lines.push_str(&diff_line_html(
                        change.get("old").unwrap_or(&Value::Null),
                        change.get("old_full").unwrap_or(&Value::Null),
                        "diff-del",
                        "-",
                    ));
                    lines.push_str(&diff_line_html(
                        change.get("new").unwrap_or(&Value::Null),
                        change.get("new_full").unwrap_or(&Value::Null),
                        "diff-add",
                        "+",
                    ));
                }
            }
            _ => {
                let old_text = change.get("old").and_then(|v| v.as_str()).unwrap_or("-");
                let new_text = change.get("new").and_then(|v| v.as_str()).unwrap_or("-");
                let text = escape_html(&format!("{} → {}", old_text, new_text));
                lines.push_str(&format!(
                    "<div class=\"diff-line diff-mod\"><span class=\"sign\">~</span><div class=\"diff-code\"><code>{text}</code></div></div>"
                ));
            }
        }
        let endpoint = change.get("endpoint").and_then(|v| v.as_str()).unwrap_or("");
        let path = change.get("path").and_then(|v| v.as_str()).unwrap_or("");
        let time = format_time(change.get("time").unwrap_or(&Value::Null));
        let _ = write!(
            blocks,
            "<div class=\"diff-block diff-block-{raw_type}\">\
               <div class=\"diff-header\">\
                 <span class=\"diff-badge {badge_css}\">{}</span>\
                 <span class=\"diff-endpoint\">{}</span>\
                 <code class=\"diff-path\">{}</code>\
                 <span class=\"diff-time\">{}</span>\
               </div>\
               <div class=\"diff-lines\">{lines}</div>\
             </div>",
            escape_html(&type_label(raw_type)),
            escape_html(&endpoint_label(endpoint)),
            escape_html(path),
            escape_html(&time),
        );
    }
    blocks
}

/// 渲染 5×5 方格里的单个任务格。
fn render_bingo_goal(goal: &Value, index: usize) -> String {
    let gobj = goal.as_object().cloned().unwrap_or_default();
    let name = escape_html(gobj.get("name").and_then(|v| v.as_str()).unwrap_or("未命名任务"));
    let gid = escape_html(
        &gobj
            .get("id")
            .map(|v| match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .unwrap_or_else(|| "-".into()),
    );
    let tier_text = gobj
        .get("requirement_text")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let requirement = match gobj.get("tiers").and_then(|t| t.as_array()) {
        Some(tiers) if !tiers.is_empty() => format!(
            "目标档位：{}",
            tiers.iter().map(format_amount).collect::<Vec<_>>().join(" / ")
        ),
        _ if !tier_text.is_empty() => format!("要求：{}", tier_text),
        _ => "无目标数值".into(),
    };

    let lore_lines: Vec<String> = match gobj.get("lore_lines").and_then(|l| l.as_array()) {
        Some(arr) if !arr.is_empty() => {
            arr.iter().filter_map(|l| l.as_str().map(String::from)).collect()
        }
        _ => match gobj.get("fullLore").and_then(|l| l.as_array()) {
            Some(arr) if !arr.is_empty() => {
                arr.iter().filter_map(|l| l.as_str().map(String::from)).collect()
            }
            _ => gobj
                .get("lore")
                .and_then(|l| l.as_str())
                .filter(|l| !l.is_empty())
                .map(|l| vec![l.to_string()])
                .unwrap_or_default(),
        },
    };
    let lore_html: String = if lore_lines.is_empty() {
        "<span class=\"bingo-cell-lore-line\">（该任务没有文案）</span>".into()
    } else {
        lore_lines
            .iter()
            .map(|line| {
                format!(
                    "<span class=\"bingo-cell-lore-line\">{}</span>",
                    crate::mctext::render_mc_text(Some(line), Some("var(--text)"), false)
                )
            })
            .collect()
    };

    let progress = gobj.get("progress_info").cloned().unwrap_or(Value::Null);
    let percent_text = progress
        .get("percent")
        .and_then(|v| v.as_f64())
        .map(crate::bingo::format_percent)
        .unwrap_or_default();
    let badge_attr = if progress
        .get("percent")
        .and_then(|v| v.as_f64())
        .map(|p| p >= 100.0)
        .unwrap_or(false)
    {
        " is-done"
    } else {
        ""
    };

    // 个人任务没有全服进度：不显示百分比徽章，也不画进度条（只保留「要求 x」）
    let (badge_text, progress_attr, foot_html) = if !progress.is_null() {
        let progress_text = progress.get("progress_text").and_then(|v| v.as_str()).unwrap_or("");
        let top_text = progress.get("top_text").and_then(|v| v.as_str()).unwrap_or("");
        let reached = progress.get("reached").map(|v| v.to_string()).unwrap_or_default();
        let total = progress.get("total").map(|v| v.to_string()).unwrap_or_default();
        let is_maxed = progress.get("is_maxed").and_then(|v| v.as_bool()).unwrap_or(false);
        // 满级（5/5 档）后进度条封顶 100%，文案显示「已满级」而不是继续累加的数字
        let foot = if is_maxed {
            format!(
                "<span class=\"bingo-bar\"><span class=\"bingo-bar-fill\" style=\"width:100%\"></span></span>\
                 <span class=\"bingo-cell-progress\">已满级 · {}/{} 档</span>",
                reached, total
            )
        } else {
            format!(
                "<span class=\"bingo-bar\"><span class=\"bingo-bar-fill\" style=\"width:{percent_text}%\"></span></span>\
                 <span class=\"bingo-cell-progress\">{} / {} · {}/{} 档</span>",
                escape_html(progress_text),
                escape_html(top_text),
                reached,
                total
            )
        };
        let attr = if is_maxed {
            format!("全服 {} · 已满级（{}/{} 档）", progress_text, reached, total)
        } else {
            format!("全服 {} / {} · 已达成 {}/{} 档", progress_text, top_text, reached, total)
        };
        (format!("{percent_text}%"), attr, foot)
    } else {
        let foot_text = if tier_text.is_empty() {
            "无目标数值".to_string()
        } else {
            format!("要求 {}", tier_text)
        };
        (
            String::new(),
            String::new(),
            format!(
                "<span class=\"bingo-cell-progress\">{}</span>",
                escape_html(&foot_text)
            ),
        )
    };

    // 社区/个人任务徽章（对角线规则，见 bingo_view；无标记时按档位兜底）
    let is_community = gobj
        .get("is_community")
        .and_then(|v| v.as_bool())
        .unwrap_or_else(|| {
            gobj.get("tiers").and_then(|t| t.as_array()).is_some_and(|t| !t.is_empty())
        });
    let kind_label = if is_community { "社区任务" } else { "个人任务" };
    // 个人任务没有百分比徽章，直接不渲染该元素（避免出现「—」占位）
    let badge_html = if badge_text.is_empty() {
        String::new()
    } else {
        format!("<span class=\"bingo-cell-badge{badge_attr}\">{badge_text}</span>")
    };

    format!(
        "<button type=\"button\" class=\"bingo-cell\" data-bingo-cell data-index=\"{index:02}\" \
         data-goal-id=\"{gid}\" data-kind-label=\"{kind_label}\" data-community=\"{}\" \
         data-requirement=\"{}\" data-progress=\"{}\" data-percent=\"{percent_text}\">\
           <span class=\"bingo-cell-top\"><span class=\"bingo-index\">{index:02}</span>\
           <span class=\"bingo-cell-kind{}\">{kind_label}</span>\
           {badge_html}</span>\
           <span class=\"bingo-cell-name\">{name}</span>\
           <span class=\"bingo-cell-lore\">{lore_html}</span>\
           <span class=\"bingo-cell-foot\">{foot_html}</span>\
         </button>",
        if is_community { "1" } else { "0" },
        escape_html(&requirement),
        escape_html(&progress_attr),
        if is_community { " is-community" } else { "" },
    )
}

/// 渲染「Bingo 任务」面板。
pub fn render_bingo_panel(bingo: &Value, stale: bool) -> String {
    let empty = Map::new();
    let bobj = bingo.as_object().unwrap_or(&empty);
    if bobj.is_empty() || bingo.get("id").map(|v| v.is_null()).unwrap_or(true) {
        return r#"<div class="panel bingo-panel" data-bingo-panel>
      <h2>Bingo 任务</h2>
      <p class="hint">数据来自 <code>/v2/resources/skyblock/bingo</code>。当前还没有抓到 Bingo 数据，
      运行一次构建后即可显示期次时间与全部任务。</p>
      <div class="empty">暂无 Bingo 数据</div>
    </div>"#
            .into();
    }

    let state = bingo.get("state").and_then(|v| v.as_str()).unwrap_or("unknown");
    let goals: Vec<Value> = bingo
        .get("goals")
        .and_then(|g| g.as_array())
        .map(|arr| arr.iter().filter(|g| g.is_object()).cloned().collect())
        .unwrap_or_default();
    let goal_cells: String = if goals.is_empty() {
        "<div class=\"empty\">该期没有任务数据</div>".into()
    } else {
        goals
            .iter()
            .enumerate()
            .map(|(i, goal)| render_bingo_goal(goal, i + 1))
            .collect()
    };

    let start_ms = bingo
        .get("start_ms")
        .and_then(to_epoch_ms_value)
        .or_else(|| bingo.get("start").and_then(to_epoch_ms_value));
    let end_ms = bingo
        .get("end_ms")
        .and_then(to_epoch_ms_value)
        .or_else(|| bingo.get("end").and_then(to_epoch_ms_value));
    let start_attr = start_ms.map(|v| v.to_string()).unwrap_or_default();
    let end_attr = end_ms.map(|v| v.to_string()).unwrap_or_default();

    let stale_html = stale.then(|| "<div class=\"bingo-stale\">本次运行未抓到最新数据，以下为上一次成功抓取的结果。</div>").unwrap_or("");

    let facts = [
        ("发布时间", format_time(bingo.get("published_at").unwrap_or(&Value::Null))),
        ("首次发现", format_time(bingo.get("first_seen").unwrap_or(&Value::Null))),
        ("开始时间", format_time(bingo.get("start").unwrap_or(&Value::Null))),
        ("结束时间", format_time(bingo.get("end").unwrap_or(&Value::Null))),
        ("难度修饰", bingo.get("modifier").and_then(|v| v.as_str()).unwrap_or("-").to_string()),
        ("任务数量", format!("{} 个", goals.len())),
    ];
    let facts_html: String = facts
        .iter()
        .map(|(k, v)| format!("<div><span class=\"k\">{k}</span><span class=\"v\">{}</span></div>", escape_html(v)))
        .collect();

    format!(
        r#"<div class="panel bingo-panel" data-bingo-panel>
      <h2>Bingo 任务<span class="panel-sub">第 {id} 期 · {name}</span></h2>
      <p class="hint">数据来自 <code>/v2/resources/skyblock/bingo</code>。发布时间取自接口的
      <code>lastUpdated</code>，另有「首次发现」记录本仓库第一次抓到这一期的时刻；
      开始与结束时间取自 <code>start</code> / <code>end</code>。任务文案保留 Minecraft
      颜色码（<code>§</code>）并按官方配色渲染。<strong>全服进度（progress）只用于展示，
      不参与变更比对</strong>，否则每个抓取周期都会刷出大量无意义变更。</p>
      {stale_html}
      <div class="bingo-head">
        <div class="bingo-state bingo-state-{state}" data-bingo-state data-state="{state}" data-start-ms="{start_attr}" data-end-ms="{end_attr}">
          <span class="bingo-state-label">{state_label}</span>
          <span class="bingo-countdown" data-bingo-countdown{hidden}>{remaining}</span>
        </div>
        <div class="bingo-info">{facts_html}</div>
      </div>
      <p class="bingo-tip">只有「未开始」和「进行中」显示倒计时，由浏览器按本地时间逐秒计算，到点自动切换状态，不受 Actions 抓取延迟影响；已结束的期次不再计时。点击任意格子查看完整文案与全服进度。</p>
      <div class="bingo-grid-wrap"><div class="bingo-grid">{goal_cells}</div></div>
      <dialog class="bingo-dialog" data-bingo-dialog aria-label="Bingo 任务详情">
        <div class="bingo-dialog-inner">
          <div class="bingo-dialog-head">
            <span class="bingo-dialog-index" data-dialog-index></span>
            <span class="bingo-dialog-name" data-dialog-name></span>
            <button type="button" class="bingo-dialog-close" data-dialog-close aria-label="关闭">×</button>
          </div>
          <div class="bingo-dialog-lore" data-dialog-lore></div>
          <div class="bingo-dialog-facts">
            <div><span class="k">类型</span><span class="v" data-dialog-kind></span></div>
            <div><span class="k">目标</span><span class="v" data-dialog-requirement></span></div>
            <div data-dialog-row="progress"><span class="k">全服进度</span><span class="v" data-dialog-progress></span></div>
            <div data-dialog-row="percent"><span class="k">完成度</span><span class="v" data-dialog-percent></span></div>
          </div>
          <div class="bingo-dialog-id">任务 ID：<code data-dialog-id></code></div>
        </div>
      </dialog>
    </div>"#,
        id = escape_html(&bingo["id"].to_string().trim_matches('"').to_string()),
        name = escape_html(bingo.get("name").and_then(|v| v.as_str()).unwrap_or("-")),
        state_label = escape_html(bingo.get("state_label").and_then(|v| v.as_str()).unwrap_or("-")),
        remaining = escape_html(bingo.get("remaining").and_then(|v| v.as_str()).unwrap_or("-")),
        hidden = if state == "ended" { " hidden" } else { "" },
    )
}

/// 渲染单张「秘密 Bingo」任务卡。
fn render_secret_card(goal: &Value, now: i64, hints_per_goal: usize) -> String {
    let index = goal.get("index").and_then(|v| v.as_u64()).unwrap_or(0);
    let index_str = goal
        .get("index_str")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .unwrap_or_else(|| format!("{:02}", index));
    let has_name = goal.get("has_name").and_then(|v| v.as_bool()).unwrap_or(false);
    let name = if has_name {
        escape_html(goal.get("name").and_then(|v| v.as_str()).unwrap_or(""))
    } else {
        format!("秘密任务 {}", index)
    };
    let unlocked_count = goal.get("unlocked_count").and_then(|v| v.as_u64()).unwrap_or(0);
    let count_class = if unlocked_count as usize >= hints_per_goal { " is-done" } else { "" };

    let mut hints_html = String::new();
    let hints = goal.get("hints").and_then(|h| h.as_array()).cloned().unwrap_or_default();
    for (i, hint) in hints.iter().enumerate() {
        let hint_no = format!("提示 {}", hint.get("index").and_then(|v| v.as_u64()).unwrap_or(i as u64 + 1));
        let populated = hint.get("populated").and_then(|v| v.as_bool()).unwrap_or(false);
        let text = escape_html(hint.get("text").and_then(|v| v.as_str()).unwrap_or(""));
        let reveal_ms = hint.get("reveal_ms").and_then(|v| v.as_i64());
        let reveal_at = escape_html(hint.get("reveal_at").and_then(|v| v.as_str()).unwrap_or("-"));

        if !populated {
            hints_html.push_str(&format!(
                "<li class=\"secret-hint is-empty\" data-secret-hint>\
                   <span class=\"secret-hint-no\">{hint_no}</span>\
                   <span class=\"secret-hint-lock\">待公布</span>\
                 </li>"
            ));
            continue;
        }
        let unlocked = hint.get("unlocked").and_then(|v| v.as_bool()).unwrap_or(true);
        match (unlocked, reveal_ms) {
            (true, _) => hints_html.push_str(&format!(
                "<li class=\"secret-hint is-unlocked\" data-secret-hint data-reveal-ms=\"{}\" \
                     title=\"解锁于 {reveal_at}\">\
                   <span class=\"secret-hint-no\">{hint_no}</span>\
                   <span class=\"secret-hint-text\" data-hint-text>{text}</span>\
                   <span class=\"secret-hint-lock\" data-hint-lock hidden></span>\
                 </li>",
                reveal_ms.unwrap_or(0)
            )),
            (false, Some(ms)) => {
                let remain = crate::bingo::humanize_delta_pub((ms - now) / 1000);
                hints_html.push_str(&format!(
                    "<li class=\"secret-hint is-locked\" data-secret-hint data-reveal-ms=\"{ms}\">\
                       <span class=\"secret-hint-no\">{hint_no}</span>\
                       <span class=\"secret-hint-text\" data-hint-text hidden>{text}</span>\
                       <span class=\"secret-hint-lock\" data-hint-lock>还有 {remain} 解锁</span>\
                     </li>"
                ));
            }
            (false, None) => hints_html.push_str(&format!(
                "<li class=\"secret-hint is-locked\" data-secret-hint>\
                   <span class=\"secret-hint-no\">{hint_no}</span>\
                   <span class=\"secret-hint-text\" data-hint-text hidden>{text}</span>\
                   <span class=\"secret-hint-lock\" data-hint-lock>待公布</span>\
                 </li>"
            )),
        }
    }

    format!(
        "<article class=\"secret-card\" data-secret-goal data-goal-index=\"{index_str}\">\
           <header class=\"secret-card-head\">\
             <span class=\"secret-index\">{index_str}</span>\
             <span class=\"secret-name\">{name}</span>\
             <span class=\"secret-count{count_class}\" data-secret-count>{unlocked_count}/{hints_per_goal}</span>\
           </header>\
           <ol class=\"secret-hints\">{hints_html}</ol>\
         </article>"
    )
}

/// 渲染「秘密 Bingo」面板。
///
/// 提示内容无法从 API 获取，由 `data/secret_bingo.json` 人工维护；
/// 面板按 `reveal_at` 依次解锁，每个任务固定 3 条提示。没有秘密 Bingo 进行时不渲染。
pub fn render_secret_panel(secret: &Value) -> String {
    // 没有秘密 Bingo 进行时整块都不渲染（不显示占位卡、不留空位）
    let visible = secret.get("visible").and_then(|v| v.as_bool()).unwrap_or(false);
    let goals: Vec<Value> = secret
        .get("goals")
        .and_then(|g| g.as_array())
        .cloned()
        .unwrap_or_default();
    if !visible || goals.is_empty() {
        return String::new();
    }

    let now = crate::timeutil::now_ms();
    let hints_per_goal = secret
        .get("hints_per_goal")
        .and_then(|v| v.as_u64())
        .map(|v| v as usize)
        .unwrap_or(crate::secret_bingo::HINTS_PER_GOAL);

    let cards: String = goals
        .iter()
        .map(|goal| render_secret_card(goal, now, hints_per_goal))
        .collect();

    let sub = format!("每个任务 {hints_per_goal} 条提示");

    format!(
        "<div class=\"panel secret-panel\" data-secret-panel>\
           <h2>秘密 Bingo <span class=\"panel-sub\">{sub}</span></h2>\
           <p class=\"secret-note\">秘密任务（Secret Bingo）的提示由官方按时间依次公布，\
             每个任务共 {hints_per_goal} 条提示：到点自动解锁，无需刷新页面；\
             倒计时按浏览器本地时间计算，不受定时构建延迟影响。</p>\
           <div class=\"secret-grid\">{cards}</div>\
         </div>"
    )
}

/// 兼容 JSON 数值/字符串的 to_epoch_ms。
fn to_epoch_ms_value(value: &Value) -> Option<i64> {
    match value {
        Value::Null => None,
        other => crate::timeutil::to_epoch_ms(other),
    }
}

/// 单期特卖卡片。
fn render_firesale_card(sale: &Map<String, Value>, index: usize) -> String {
    let state = sale.get("state").and_then(|v| v.as_str()).unwrap_or("unknown");
    let name = escape_html(sale.get("name").and_then(|v| v.as_str()).unwrap_or("-"));
    let item_id = escape_html(sale.get("item_id").and_then(|v| v.as_str()).unwrap_or("-"));
    let price = escape_html(&sale_amount(sale.get("price").unwrap_or(&Value::Null)));
    let amount = escape_html(&sale_amount(sale.get("amount").unwrap_or(&Value::Null)));
    let start = escape_html(sale.get("start").and_then(|v| v.as_str()).unwrap_or("-"));
    let end = escape_html(sale.get("end").and_then(|v| v.as_str()).unwrap_or("-"));
    format!(
        r#"<div class="firesale-card firesale-card-{state}">
          <div class="firesale-card-head">
            <span class="firesale-index">{index:02}</span>
            <span class="firesale-badge firesale-badge-{state}">{state_label}</span>
          </div>
          <div class="firesale-name">{name}</div>
          <div class="firesale-facts">
            <div><span class="k">价格</span><span class="v firesale-price">{price} {currency}</span></div>
            <div><span class="k">数量</span><span class="v">{amount}</span></div>
            <div><span class="k">开始</span><span class="v">{start}</span></div>
            <div><span class="k">结束</span><span class="v">{end}</span></div>
          </div>
          <div class="firesale-id"><code>{item_id}</code></div>
        </div>"#,
        state_label = escape_html(crate::firesale::sale_state_label(state)),
        currency = CURRENCY_LABEL,
    )
}

/// 「限时特卖」面板。
pub fn render_firesales_panel(firesales: &Value, stale: bool) -> String {
    let empty = Map::new();
    let fobj = firesales.as_object().unwrap_or(&empty);
    let sales: Vec<Map<String, Value>> = firesales
        .get("sales")
        .and_then(|s| s.as_array())
        .map(|arr| arr.iter().filter_map(|s| s.as_object().cloned()).collect())
        .unwrap_or_default();
    if fobj.is_empty() || sales.is_empty() {
        return r#"<div class="panel">
      <h2>限时特卖（Firesales）</h2>
      <p class="hint">数据来自 <code>/v2/skyblock/firesales</code>。当前还没有特卖数据，
      运行一次构建后即可显示每期特卖的价格与数量。</p>
      <div class="empty">暂无限时特卖数据</div>
    </div>"#
            .into();
    }

    let now_sales: Vec<&Map<String, Value>> = sales
        .iter()
        .filter(|s| matches!(s.get("state").and_then(|v| v.as_str()), Some("upcoming") | Some("active")))
        .collect();
    let main_cards: String = if now_sales.is_empty() {
        "<div class=\"empty\">当前没有即将开始或进行中的特卖</div>".into()
    } else {
        now_sales
            .iter()
            .enumerate()
            .map(|(i, sale)| render_firesale_card(sale, i + 1))
            .collect()
    };

    let rows: String = sales
        .iter()
        .enumerate()
        .map(|(i, sale)| {
            format!(
                "<tr><td>{}</td><td>{}</td><td><code>{}</code></td><td>{}</td><td>{}</td><td>{}</td><td class=\"time\">{}</td><td class=\"time\">{}</td></tr>",
                i + 1,
                escape_html(sale.get("name").and_then(|v| v.as_str()).unwrap_or("-")),
                escape_html(sale.get("item_id").and_then(|v| v.as_str()).unwrap_or("-")),
                escape_html(&sale_amount(sale.get("price").unwrap_or(&Value::Null))),
                escape_html(&sale_amount(sale.get("amount").unwrap_or(&Value::Null))),
                escape_html(sale.get("state_label").and_then(|v| v.as_str()).unwrap_or("-")),
                escape_html(sale.get("start").and_then(|v| v.as_str()).unwrap_or("-")),
                escape_html(sale.get("end").and_then(|v| v.as_str()).unwrap_or("-")),
            )
        })
        .collect();
    let raw_json = escape_html(&serde_json::to_string_pretty(&Value::Array(sales.iter().cloned().map(Value::Object).collect())).unwrap_or_default());

    let stale_html = stale.then(|| "<div class=\"bingo-stale\">本次运行未抓到最新数据，以下为上一次成功抓取的结果。</div>").unwrap_or("");

    format!(
        r#"<div class="panel firesale-panel" data-firesale-panel>
      <h2>限时特卖（Firesales）<span class="panel-sub">共 {total} 期 · 即将开始 {upcoming} · 进行中 {active} · 已结束 {ended}</span>
        <button type="button" class="firesale-toggle" data-firesale-toggle aria-expanded="false" aria-controls="firesale-extra">显示额外数据</button></h2>
      <p class="hint">数据来自 <code>/v2/skyblock/firesales</code>，价格以宝石（Gems）结算。
        特卖<strong>开始前官方可能随时调整时间、数量与价格</strong>，因此每次运行都会重新抓取并覆盖，
        不存在「查过就跳过」。一次响应里可能同时有多期特卖，每期独立成卡，按开始时间排序；
        主视图只展示<strong>即将开始与进行中</strong>的期次。</p>
      {stale_html}
      <div class="firesale-grid">{main_cards}</div>
      <div class="firesale-extra" id="firesale-extra" data-firesale-extra hidden>
        <h3>全部期次（含已结束）</h3>
        <div class="firesale-table-wrap">
          <table>
            <thead>
              <tr><th>#</th><th>物品</th><th>item_id</th><th>价格（{currency}）</th>
                  <th>数量</th><th>状态</th><th>开始（北京时间）</th><th>结束（北京时间）</th></tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
        <p class="hint">数据抓取时间：{generated_at}（北京时间）</p>
        <details>
          <summary>原始数据（JSON）</summary>
          <pre class="firesale-raw">{raw_json}</pre>
        </details>
      </div>
    </div>"#,
        total = sales.len(),
        upcoming = fobj.get("upcoming_count").map(|v| v.to_string()).unwrap_or_else(|| "0".into()),
        active = fobj.get("active_count").map(|v| v.to_string()).unwrap_or_else(|| "0".into()),
        ended = fobj.get("ended_count").map(|v| v.to_string()).unwrap_or_else(|| "0".into()),
        generated_at = escape_html(fobj.get("generated_at").and_then(|v| v.as_str()).unwrap_or("-")),
        currency = CURRENCY_LABEL,
    )
}

/// 渲染「版本记录」面板。
pub fn render_version_panel(version_info: &Value) -> String {
    let history: Vec<Value> = version_info
        .get("history")
        .and_then(|h| h.as_array())
        .cloned()
        .unwrap_or_default();
    let rows: String = history
        .iter()
        .take(10)
        .map(|item| {
            format!(
                "<tr><td><code>v{}</code></td><td class=\"time\">{}</td><td>{}</td><td>{}</td></tr>",
                escape_html(item.get("version").map(|v| v.to_string()).unwrap_or_else(|| "-".into()).trim_matches('"')),
                escape_html(&format_time(item.get("time").unwrap_or(&Value::Null))),
                escape_html(item.get("note").and_then(|v| v.as_str()).unwrap_or("-")),
                escape_html(item.get("previous").and_then(|v| v.as_str()).unwrap_or("-")),
            )
        })
        .collect();
    let body = if rows.is_empty() {
        "<tr><td colspan=\"4\" class=\"empty\">暂无版本切换记录。首次运行会记录当前稳定版本。</td></tr>".into()
    } else {
        rows
    };
    let versions = version_info.get("versions").cloned().unwrap_or(json!({}));
    let observed_parts: Vec<String> = versions
        .as_object()
        .map(|obj| {
            obj.iter()
                .map(|(ep, info)| {
                    format!(
                        "{} v{}（首次观察 {}）{}",
                        endpoint_label(ep),
                        escape_html(info.get("version").map(|v| v.to_string()).unwrap_or_else(|| "-".into()).trim_matches('"')),
                        escape_html(&format_time(info.get("observed_at").unwrap_or(&Value::Null))),
                        if info.get("stale").and_then(|v| v.as_bool()).unwrap_or(false) {
                            " · 本次未返回"
                        } else {
                            ""
                        },
                    )
                })
                .collect()
        })
        .unwrap_or_default();
    let observed = if observed_parts.is_empty() {
        "尚未取得版本数据".to_string()
    } else {
        observed_parts.join(" &middot; ")
    };
    format!(
        r#"<div class="panel">
      <h2>版本记录</h2>
      <p class="hint">collections 与 skills 都带 version 字段，但 Hypixel 分批更新时两者不一定同时切换。
      当前版本只在两个接口版本一致时才确认更新；不一致时保持上一次稳定版本并标注「版本同步中」，避免版本号来回跳。</p>
      <div class="pack-meta">当前观测：{observed}</div>
      <table>
        <thead><tr><th>确认版本</th><th>确认时间（北京时间）</th><th>说明</th><th>上一版本</th></tr></thead>
        <tbody>{body}</tbody>
      </table>
    </div>"#
    )
}

/// 新增 / 删除文件：整段单一颜色。
fn pack_paths_details(label: &str, files: &[Value], css: &str, sign: &str) -> String {
    if files.is_empty() {
        return String::new();
    }
    let items: String = files
        .iter()
        .take(PACK_FILE_LIMIT)
        .map(|path| {
            format!(
                "<div class=\"diff-line {css}\"><span class=\"sign\">{sign}</span><div class=\"diff-code\"><code>{}</code></div></div>",
                escape_html(path.as_str().unwrap_or("-"))
            )
        })
        .collect();
    let more = if files.len() > PACK_FILE_LIMIT {
        format!("<div class=\"pack-more\">… 另有 {} 个文件未展开</div>", files.len() - PACK_FILE_LIMIT)
    } else {
        String::new()
    };
    format!(
        "<details class=\"pack-files\"><summary class=\"cnt-{css}\">{label}文件（{count}）</summary><div class=\"diff-lines\">{items}{more}</div></details>",
        count = files.len()
    )
}

/// 修改文件：只显示发生变化的那一段。
fn pack_modified_details(modified: &[Value]) -> String {
    if modified.is_empty() {
        return String::new();
    }
    let mut parts = Vec::new();
    for entry in modified.iter().take(PACK_FILE_LIMIT) {
        let (path, kind, old_hash, new_hash) = match entry {
            Value::String(s) => (s.clone(), "text".to_string(), String::new(), String::new()),
            Value::Object(o) => (
                o.get("path").and_then(|v| v.as_str()).unwrap_or("-").to_string(),
                o.get("kind").and_then(|v| v.as_str()).unwrap_or("text").to_string(),
                o.get("old_hash").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                o.get("new_hash").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            ),
            _ => continue,
        };
        let head = format!("<div class=\"pack-mod-head\"><code>{}</code></div>", escape_html(&path));
        let hunks: Vec<crate::textdiff::Hunk> = entry
            .get("hunks")
            .and_then(|h| serde_json::from_value(h.clone()).ok())
            .unwrap_or_default();
        let body = if !hunks.is_empty() {
            render_hunks(&hunks, false)
        } else if kind == "binary" {
            format!(
                "<div class=\"pack-binary\">二进制文件已修改，仅显示哈希：{} → {}</div>",
                escape_html(if old_hash.is_empty() { "-" } else { &old_hash }),
                escape_html(if new_hash.is_empty() { "-" } else { &new_hash })
            )
        } else {
            format!(
                "<div class=\"diff-line diff-del\"><span class=\"sign\">-</span><div class=\"diff-code\"><code>哈希 {}</code></div></div>\
                 <div class=\"diff-line diff-add\"><span class=\"sign\">+</span><div class=\"diff-code\"><code>哈希 {}</code></div></div>",
                escape_html(if old_hash.is_empty() { "-" } else { &old_hash }),
                escape_html(if new_hash.is_empty() { "-" } else { &new_hash })
            )
        };
        parts.push(format!("<div class=\"pack-mod\">{head}{body}</div>"));
    }
    if modified.len() > PACK_FILE_LIMIT {
        parts.push(format!(
            "<div class=\"pack-more\">… 另有 {} 个修改文件未展开</div>",
            modified.len() - PACK_FILE_LIMIT
        ));
    }
    format!(
        "<details class=\"pack-files\" open><summary class=\"cnt-mod\">修改文件（{}）</summary>{}</details>",
        modified.len(),
        parts.join("")
    )
}

/// 渲染「资源包文件变更」面板。
pub fn render_pack_panel(pack_result: &Value, pack_changes: &[Map<String, Value>]) -> String {
    let status = pack_result.get("status").and_then(|v| v.as_str()).unwrap_or("unavailable");
    let pack = pack_result.get("pack").cloned().unwrap_or(Value::Null);
    let total_files = pack_result.get("total_files").and_then(|v| v.as_u64()).unwrap_or(0);
    let status_text = pack_status_label(status);

    let meta = if let Some(pobj) = pack.as_object() {
        let mut meta = format!(
            "资源包：{} &middot; packFormat {} &middot; 文件总数 {} &middot; 部署 ID <code>{}</code>",
            escape_html(pobj.get("id").map(|v| v.to_string()).unwrap_or_else(|| "-".into()).trim_matches('"')),
            escape_html(pobj.get("pack_format").map(|v| v.to_string()).unwrap_or_else(|| "-".into()).trim_matches('"')),
            total_files,
            escape_html(pobj.get("deploy_id").map(|v| v.to_string()).unwrap_or_else(|| "-".into()).trim_matches('"')),
        );
        let candidates: Vec<Value> = pack_result
            .get("candidates")
            .and_then(|c| c.as_array())
            .cloned()
            .unwrap_or_default();
        if !candidates.is_empty() {
            let joined = candidates
                .iter()
                .map(|c| c.to_string())
                .collect::<Vec<_>>()
                .join(" / ");
            let selected = pobj.get("pack_format").map(|v| v.to_string()).unwrap_or_default();
            meta.push_str(&format!(
                " &middot; <span class=\"pack-formats\">本次候选 packFormat：{} → 自动选取最大值 <strong>{}</strong>（数字随 MC 更新变化，每次调用接口重新取）</span>",
                escape_html(&joined),
                escape_html(selected.trim_matches('"')),
            ));
        }
        meta
    } else {
        "尚未获取到资源包信息".into()
    };

    let err_html = pack_result
        .get("error")
        .and_then(|v| v.as_str())
        .filter(|e| !e.is_empty())
        .map(|e| format!("<p class=\"pack-error\">检查异常：{}</p>", escape_html(e)))
        .unwrap_or_default();

    let rows = if pack_changes.is_empty() {
        "<div class=\"empty\">暂无资源包变更记录。工作流首次运行会建立文件基线，之后资源包更新时会列出新增 / 修改 / 删除的文件。</div>".into()
    } else {
        let parts: String = pack_changes
            .iter()
            .take(5)
            .map(|rec| {
                let added = rec.get("added").and_then(|a| a.as_array()).cloned().unwrap_or_default();
                let modified = rec.get("modified").and_then(|a| a.as_array()).cloned().unwrap_or_default();
                let removed = rec.get("removed").and_then(|a| a.as_array()).cloned().unwrap_or_default();
                let note_html = rec
                    .get("note")
                    .and_then(|v| v.as_str())
                    .filter(|n| !n.is_empty())
                    .map(|n| format!("<div class=\"pack-note\">{}</div>", escape_html(n)))
                    .unwrap_or_default();
                format!(
                    r#"<div class="pack-change">
                  <div class="pack-change-head">
                    <span class="time">{}</span>
                    <span>packFormat {}</span>
                    <span class="cnt cnt-add">新增 {}</span>
                    <span class="cnt cnt-mod">修改 {}</span>
                    <span class="cnt cnt-del">删除 {}</span>
                    <span>共 {} 个文件</span>
                  </div>
                  {note_html}
                  {}
                  {}
                  {}
                </div>"#,
                    escape_html(&format_time(rec.get("time").unwrap_or(&Value::Null))),
                    escape_html(rec.get("pack_format").map(|v| v.to_string()).unwrap_or_else(|| "-".into()).trim_matches('"')),
                    added.len(),
                    modified.len(),
                    removed.len(),
                    escape_html(&rec.get("total_files").map(|v| v.to_string()).unwrap_or_else(|| "-".into())),
                    pack_paths_details("新增", &added, "diff-add", "+"),
                    pack_modified_details(&modified),
                    pack_paths_details("删除", &removed, "diff-del", "-"),
                )
            })
            .collect();
        parts
    };

    format!(
        r#"<div class="panel">
      <h2>资源包文件变更</h2>
      <p class="hint">只展示最近 {RETENTION_DAYS} 天内的文件变更，更早的批次会自动清理。每次调用接口都会从响应里取 <strong>packFormat 数值最大</strong> 的资源包（该数字随 MC 更新变化，不写死）；官方哈希未变化时跳过下载。新增文件只标绿、删除文件只标红；被修改的文本文件<strong>只显示发生变化的那一段</strong>，删除行标红、新增行标绿，未变化的内容不输出。若接口或下载失败，本次运行不会改动任何数据。</p>
      <div class="pack-meta">{status_text} &middot; {meta}</div>
      {err_html}
      {rows}
    </div>"#
    )
}

/// 静态站完整 HTML：骨架走 Tera 模板，片段全部在 Rust 侧渲染。
pub fn build_site_html(
    generated_at: &str,
    endpoints_result: &Value,
    changes: &[Map<String, Value>],
    version_info: &Value,
    dashboard_version: &str,
    build: &str,
    pack_result: &Value,
    pack_changes: &[Map<String, Value>],
    bingo: &Value,
    bingo_stale: bool,
    firesales: &Value,
    secret: &Value,
    skipped_reason: Option<&str>,
) -> Result<String, String> {
    // 顶部版本栏
    let stable = version_info
        .get("stable_version")
        .and_then(|v| v.as_str())
        .unwrap_or("未获取");
    let version_display = if stable == "未获取" { "未获取".to_string() } else { format!("v{}", stable) };
    let sync_status = version_info.get("sync_status").and_then(|v| v.as_str()).unwrap_or("unknown");
    let versions = version_info.get("versions").cloned().unwrap_or(json!({}));
    let observed_text: String = crate::labels::VERSION_ENDPOINTS
        .iter()
        .map(|ep| {
            format!(
                "{} v{}",
                endpoint_label(ep),
                escape_html(
                    versions
                        .get(*ep)
                        .and_then(|v| v.get("version"))
                        .map(|v| v.to_string())
                        .unwrap_or_else(|| "-".into())
                        .trim_matches('"')
                )
            )
        })
        .collect::<Vec<_>>()
        .join(" &middot; ");
    let note_html = matches!(sync_status, "syncing" | "partial")
        .then(|| {
            version_info
                .get("note")
                .and_then(|v| v.as_str())
                .filter(|n| !n.is_empty())
                .map(|n| format!("<div class=\"version-note\">{}</div>", escape_html(n)))
        })
        .flatten()
        .unwrap_or_default();
    let skip_html = skipped_reason
        .map(|reason| {
            format!(
                "<div class=\"skip-banner\">本次运行未获取到完整数据，<strong>仓库数据未做任何改动</strong>，以下展示的是上一次成功抓取的结果。<br>原因：{}</div>",
                escape_html(reason)
            )
        })
        .unwrap_or_default();

    // 端点卡片
    let mut cards = String::new();
    let endpoints_order = ["collections", "skills", "items", "bazaar", "packs", "bingo", "firesales"];
    for name in endpoints_order {
        let ep = endpoints_result.get(name).cloned().unwrap_or(json!({}));
        let status = ep.get("status").and_then(|v| v.as_str()).unwrap_or("error");
        let status_class = if status == "ok" { "ok" } else { "error" };
        let fetched = format_time(ep.get("fetched_at").unwrap_or(&Value::Null));
        let summary_parts: Vec<String> = ep
            .get("summary")
            .and_then(|s| s.as_object())
            .map(|obj| {
                obj.iter()
                    .map(|(k, v)| {
                        format!(
                            "{}：{}",
                            summary_label(k),
                            match v {
                                Value::String(s) => s.clone(),
                                other => other.to_string(),
                            }
                        )
                    })
                    .collect()
            })
            .unwrap_or_default();
        let summary_html = if summary_parts.is_empty() {
            "-".to_string()
        } else {
            escape_html(&summary_parts.join("<br>")).replace("&lt;br&gt;", "<br>")
        };
        cards.push_str(&format!(
            r#"<div class="card">
              <div class="card-header">
                <span class="endpoint-name">{label}<span class="endpoint-id">{name}</span></span>
                <span class="badge badge-{status_class}">{status_text}</span>
              </div>
              <div class="fetched">抓取时间：{fetched}（北京时间）</div>
              <div class="summary">{summary_html}</div>
            </div>"#,
            label = escape_html(&endpoint_label(name)),
            status_text = escape_html(&status_label(status)),
        ));
    }

    let changes_blocks = render_change_blocks(changes);
    let version_panel = render_version_panel(version_info);
    let pack_panel = render_pack_panel(pack_result, pack_changes);
    let firesales_panel = render_firesales_panel(firesales, false);
    let bingo_panel = render_bingo_panel(bingo, bingo_stale);
    let secret_panel = render_secret_panel(secret);

    // 内联样式与脚本（与本地服务两端共用同一份文件）
    let static_dir = base_dir().join("static");
    let bingo_css = read_asset(&static_dir.join("bingo.css"));
    let bingo_js = read_asset(&static_dir.join("bingo.js"));
    let secret_css = read_asset(&static_dir.join("secret.css"));
    let secret_js = read_asset(&static_dir.join("secret.js"));
    let firesale_css = read_asset(&static_dir.join("firesale.css"));
    let firesale_js = read_asset(&static_dir.join("firesale.js"));
    let theme_css = read_asset(&static_dir.join("theme.css"));
    let theme_js = read_asset(&static_dir.join("theme.js"));

    // 主样式：templates/site_style.css（{days} 占位符在读取后替换）
    let mut main_css = read_asset(&base_dir().join("templates").join("site_style.css"));
    main_css = main_css.replace("{days}", &RETENTION_DAYS.to_string());

    let mut tera = tera::Tera::default();
    tera.add_template_file(base_dir().join("templates").join("site.html.j2"), Some("site"))
        .map_err(|e| format!("加载静态站模板失败：{}", e))?;

    let mut context = tera::Context::new();
    context.insert("main_css", &main_css);
    context.insert("bingo_css", &bingo_css);
    context.insert("firesale_css", &firesale_css);
    context.insert("theme_css", &theme_css);
    context.insert("theme_js", &theme_js);
    context.insert("skip_html", &skip_html);
    context.insert("version_display", &version_display);
    context.insert("sync_status", &sync_status);
    context.insert("sync_label", &sync_label(sync_status));
    context.insert("observed_text", &observed_text);
    context.insert("dashboard_version", dashboard_version);
    context.insert("build", build);
    context.insert("retention_days", &RETENTION_DAYS);
    context.insert("generated_at", &format_time(&Value::String(generated_at.to_string())));
    context.insert("cards", &cards);
    context.insert("note_html", &note_html);
    context.insert("version_panel", &version_panel);
    context.insert("pack_panel", &pack_panel);
    context.insert("changes_blocks", &changes_blocks);
    context.insert("firesales_panel", &firesales_panel);
    context.insert("bingo_panel", &bingo_panel);
    context.insert("bingo_js", &bingo_js);
    context.insert("secret_panel", &secret_panel);
    context.insert("secret_css", &secret_css);
    context.insert("secret_js", &secret_js);
    context.insert("firesale_js", &firesale_js);

    tera.render("site", &context).map_err(|e| format!("渲染静态站失败：{:?}", e))
}
