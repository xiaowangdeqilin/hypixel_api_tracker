//! 静态站（GitHub Pages）的多语言支持。
//!
//! 背景：静态站由 `render_site.rs` 直接拼中文 HTML，模板里的文案也不是 `t[...]` 词条。
//! 为了让静态页也能切换语言而不必逐条改写渲染代码，这里在**构建时**做一次自动标注：
//!
//! 1. 遍历生成的 HTML，拿到每个元素的 innerHTML；
//! 2. 用 `i18n-src/translations.json` 里的中文词条（含 `{占位符}` 与内联标签）去匹配，
//!    `{name}` 会被当成捕获组，例如 `数据抓取时间：{time}（北京时间）` 能匹配
//!    `数据抓取时间：2026-11-10 12:00:00（北京时间）`；
//! 3. 命中就给该元素加上 `data-k="<中文词条>"`（带占位符时再加 `data-p='{...}'`），
//!    前端 `static/site-i18n.js` 按 `data-k` 把 innerHTML 换成当前语言的译文。
//!
//! 这样静态站与本地服务页共用同一套 103 种语言词条，不再维护第二份文案。

use serde_json::{json, Map, Value};
use std::collections::HashMap;

/// 需要跳过的元素：内容不是界面文案。
const SKIP_TAGS: [&str; 6] = ["script", "style", "code", "pre", "textarea", "kbd"];
/// 自闭合 / 空元素：不参与容器栈。
const VOID_TAGS: [&str; 14] = [
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
    "track", "wbr",
];

fn is_cjk(text: &str) -> bool {
    text.chars().any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c))
}

/// 把连续空白压成单个空格并去掉首尾空白，便于把「模板缩进」与词条对齐。
fn normalize(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut pending_space = false;
    for ch in text.chars() {
        if ch.is_whitespace() {
            if !out.is_empty() {
                pending_space = true;
            }
            continue;
        }
        if pending_space {
            out.push(' ');
            pending_space = false;
        }
        out.push(ch);
    }
    out
}

/// 一条可翻译词条：`{name}` 处为可变部分。
struct Pattern {
    key: String,
    literals: Vec<String>,
    names: Vec<String>,
}

fn compile(key: &str) -> Option<Pattern> {
    if !is_cjk(key) {
        return None;
    }
    let mut literals: Vec<String> = Vec::new();
    let mut names: Vec<String> = Vec::new();
    let mut buf = String::new();
    let mut chars = key.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch == '{' {
            let mut name = String::new();
            let mut closed = false;
            for ch in chars.by_ref() {
                if ch == '}' {
                    closed = true;
                    break;
                }
                name.push(ch);
            }
            if closed && !name.is_empty() && name.chars().all(|c| c.is_alphanumeric() || c == '_') {
                literals.push(std::mem::take(&mut buf));
                names.push(name);
                continue;
            }
            buf.push('{');
            buf.push_str(&name);
            buf.push('}');
            continue;
        }
        buf.push(ch);
    }
    literals.push(buf);
    if names.is_empty() && normalize(key).chars().count() < 2 {
        return None;
    }
    Some(Pattern { key: key.to_string(), literals, names })
}

/// 用一条词条匹配规范化后的 innerHTML：`{name}` 处捕获原文本。
fn match_pattern(text: &str, pat: &Pattern) -> Option<Map<String, Value>> {
    let first = pat.literals.first()?;
    if !text.starts_with(first.as_str()) {
        return None;
    }
    let mut cursor = first.len();
    let mut params = Map::new();
    for i in 0..pat.names.len() {
        let next = pat.literals.get(i + 1)?;
        if next.is_empty() {
            // 结尾占位符：把剩余文本整体作为参数
            if i + 1 != pat.names.len() {
                return None;
            }
            let rest = &text[cursor..];
            if rest.is_empty() || rest.contains('<') || rest.contains('>') {
                return None;
            }
            params.insert(pat.names[i].clone(), Value::String(rest.to_string()));
            cursor = text.len();
            break;
        }
        let idx = text[cursor..].find(next.as_str())?;
        if idx == 0 {
            return None; // 占位符必须至少匹配一个字符
        }
        let captured = &text[cursor..cursor + idx];
        if captured.contains('<') || captured.contains('>') {
            return None; // 参数只能是纯文本，不能吞掉标签（如 <br> / <span>）
        }
        params.insert(pat.names[i].clone(), Value::String(captured.to_string()));
        cursor += idx + next.len();
    }
    // 必须正好收尾，避免只匹配到一半的元素
    if cursor != text.len() {
        return None;
    }
    Some(params)
}

fn escape_attr(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('"', "&quot;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// 给命中词条的元素生成要插入到开始标签里的属性。
fn attr_html(key: &str, params: &Map<String, Value>) -> String {
    let mut attr = format!(" data-k=\"{}\"", escape_attr(key));
    if !params.is_empty() {
        let json = serde_json::to_string(params).unwrap_or_default();
        attr.push_str(&format!(" data-p=\"{}\"", escape_attr(&json)));
    }
    attr
}

struct Frame {
    tag: String,
    start: usize,
    content_start: usize,
    child_annotated: bool,
    skip: bool,
}

/// 给静态站 HTML 打上 `data-k` 标注。
pub fn annotate(html: &str, keys: &[String]) -> String {
    let mut exact: HashMap<String, &str> = HashMap::new();
    let mut patterns: Vec<Pattern> = Vec::new();
    for key in keys {
        match compile(key) {
            Some(pat) if pat.names.is_empty() => {
                exact.entry(normalize(key)).or_insert(key.as_str());
            }
            Some(pat) => patterns.push(pat),
            None => {}
        }
    }
    // 长的先匹配，避免短词条抢占整句
    patterns.sort_by_key(|p| std::cmp::Reverse(p.literals.iter().map(|l| l.len()).sum::<usize>()));
    let max_key_len = keys.iter().map(|k| k.chars().count()).max().unwrap_or(0);

    let bytes = html.as_bytes();
    let mut stack: Vec<Frame> = Vec::new();
    let mut inserts: Vec<(usize, String)> = Vec::new();
    let mut i = 0usize;
    while i < html.len() {
        if bytes[i] != b'<' {
            i += 1;
            continue;
        }
        let rest = &html[i..];
        if rest.starts_with("<!--") {
            i += rest.find("-->").map(|e| e + 3).unwrap_or(rest.len());
            continue;
        }
        if rest.starts_with("<!") {
            i += rest.find('>').map(|e| e + 1).unwrap_or(rest.len());
            continue;
        }
        let Some(offset) = rest.find('>') else { break };
        let tag_end = i + offset + 1;
        if rest.starts_with("</") {
            let name = rest[2..offset].trim().to_ascii_lowercase();
            while let Some(frame) = stack.pop() {
                if frame.tag == name {
                    if !frame.skip && !frame.child_annotated {
                        let inner = normalize(&html[frame.content_start..i]);
                        if !inner.is_empty() && inner.chars().count() <= max_key_len + 64 {
                            let hit = exact
                                .get(&inner)
                                .map(|k| (k.to_string(), Map::new()))
                                .or_else(|| {
                                    patterns
                                        .iter()
                                        .find_map(|p| match_pattern(&inner, p).map(|v| (p.key.clone(), v)))
                                });
                            if let Some((key, params)) = hit {
                                inserts.push((frame.start, attr_html(&key, &params)));
                                if let Some(parent) = stack.last_mut() {
                                    parent.child_annotated = true;
                                }
                            }
                        }
                    }
                    break;
                }
            }
            i = tag_end;
            continue;
        }
        let inner_tag = &rest[1..offset];
        let self_closing = inner_tag.ends_with('/');
        let name: String = inner_tag
            .split(|c: char| c.is_whitespace() || c == '/')
            .next()
            .unwrap_or("")
            .to_ascii_lowercase();
        // 已手工标注（如标题里的 <span data-k>）或代码块，整体跳过
        let skip = SKIP_TAGS.contains(&name.as_str()) || inner_tag.contains("data-k=");
        if !self_closing && !VOID_TAGS.contains(&name.as_str()) && !name.is_empty() {
            stack.push(Frame {
                tag: name,
                start: i,
                content_start: tag_end,
                child_annotated: false,
                skip,
            });
        }
        i = tag_end;
    }

    if inserts.is_empty() {
        return html.to_string();
    }
    inserts.sort_by_key(|(pos, _)| *pos);
    let mut out = String::with_capacity(html.len() + inserts.len() * 40);
    let mut last = 0usize;
    for (pos, attr) in inserts {
        out.push_str(&html[last..pos]);
        // 插到标签名之后：<div class=.. → <div data-k=.. class=..
        let open = pos + 1;
        let name_end = html[open..]
            .find(|c: char| c.is_whitespace() || c == '>' || c == '/')
            .map(|e| open + e)
            .unwrap_or(open);
        out.push_str(&html[pos..name_end]);
        out.push_str(&attr);
        last = name_end;
    }
    out.push_str(&html[last..]);
    out
}

/// 读取词条清单（`i18n-src/translations.json` 里 `packs.en` 的键）。
pub fn key_list() -> Vec<String> {
    let path = crate::state::base_dir()
        .join("i18n-src")
        .join("translations.json");
    let Ok(text) = std::fs::read_to_string(&path) else {
        return Vec::new();
    };
    let Ok(value) = serde_json::from_str::<Value>(&text) else {
        return Vec::new();
    };
    value
        .get("packs")
        .and_then(|p| p.get("en"))
        .and_then(|p| p.as_object())
        .map(|obj| obj.keys().cloned().collect())
        .unwrap_or_default()
}

/// 打包所有语言的词条，供静态页内联（`{keys: [...], packs: {en: [...]}}`）。
///
/// 只嵌一份中文键列表 + 每语言一个等长数组，避免 103 份中文键重复占体积。
pub fn packs_bundle() -> Value {
    let path = crate::state::base_dir()
        .join("i18n-src")
        .join("translations.json");
    let Ok(text) = std::fs::read_to_string(&path) else {
        return json!({ "keys": [], "packs": {} });
    };
    let Ok(value) = serde_json::from_str::<Value>(&text) else {
        return json!({ "keys": [], "packs": {} });
    };
    let Some(packs) = value.get("packs").and_then(|p| p.as_object()) else {
        return json!({ "keys": [], "packs": {} });
    };
    let keys: Vec<String> = packs
        .get("en")
        .and_then(|p| p.as_object())
        .map(|obj| obj.keys().cloned().collect())
        .unwrap_or_default();
    let mut out = Map::new();
    for (code, pack) in packs {
        // 简体中文就是原文，无需内嵌
        if code == "zh-CN" {
            continue;
        }
        let Some(obj) = pack.as_object() else { continue };
        let values: Vec<Value> = keys
            .iter()
            .map(|k| obj.get(k).cloned().unwrap_or(Value::Null))
            .collect();
        out.insert(code.clone(), Value::Array(values));
    }
    json!({ "keys": keys, "packs": out })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn annotate_marks_inner_html_with_params() {
        let keys = vec![
            "最近变更".to_string(),
            "数据抓取时间：{time}（北京时间）".to_string(),
        ];
        let html = "<div class=\"x\"><h2>最近变更</h2><p>数据抓取时间：2026-11-10 12:00:00（北京时间）</p></div>";
        let out = annotate(html, &keys);
        assert!(out.contains("<h2 data-k=\"最近变更\">最近变更</h2>"), "{}", out);
        assert!(
            out.contains("data-k=\"数据抓取时间：{time}（北京时间）\""),
            "{}",
            out
        );
        assert!(out.contains("data-p=\"{&quot;time&quot;:&quot;2026-11-10 12:00:00&quot;}\""), "{}", out);
    }

    #[test]
    fn annotate_skips_code_and_unknown_text() {
        let keys = vec!["最近变更".to_string()];
        let html = "<div><code>最近变更</code><span>别的文字</span></div>";
        let out = annotate(html, &keys);
        assert_eq!(out, html);
    }
}
