//! Minecraft 颜色代码渲染（原 app/mctext.py 的移植）。
//!
//! `§` 代码是「状态」而不是「包裹」：`§e` 之后的所有文字都是黄色，直到遇到
//! 下一个颜色码或 `§r` 重置，因此必须顺序扫描并维护当前样式状态。

use std::fmt::Write as _;

/// Minecraft 官方 16 色（Java 版 §0-§f）。
fn mc_color(code: u8) -> Option<&'static str> {
    Some(match code {
        b'0' => "#000000",
        b'1' => "#0000AA",
        b'2' => "#00AA00",
        b'3' => "#00AAAA",
        b'4' => "#AA0000",
        b'5' => "#AA00AA",
        b'6' => "#FFAA00",
        b'7' => "#AAAAAA",
        b'8' => "#555555",
        b'9' => "#5555FF",
        b'a' => "#55FF55",
        b'b' => "#55FFFF",
        b'c' => "#FF5555",
        b'd' => "#FF55FF",
        b'e' => "#FFFF55",
        b'f' => "#FFFFFF",
        _ => return None,
    })
}

/// 纯黑在深色主题背景上完全不可见，单独提亮一档。
fn color_override(code: u8) -> Option<&'static str> {
    (code == b'0').then_some("#404040")
}

#[derive(Clone, Debug, PartialEq)]
struct Style {
    color: Option<String>,
    bold: bool,
    italic: bool,
    underline: bool,
    strike: bool,
    obf: bool,
}

impl Style {
    fn initial(default_color: Option<&str>) -> Self {
        Style {
            color: default_color.map(|c| c.to_string()),
            bold: false,
            italic: false,
            underline: false,
            strike: false,
            obf: false,
        }
    }
}

fn escape_html_basic(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
}

fn emit(chunk: &str, style: &Style) -> String {
    if chunk.is_empty() {
        return String::new();
    }
    let body = escape_html_basic(chunk);
    let mut css = String::new();
    if let Some(color) = &style.color {
        let _ = write!(css, "color:{}", color);
    }
    if style.bold {
        if !css.is_empty() {
            css.push(';');
        }
        css.push_str("font-weight:700");
    }
    if style.italic {
        if !css.is_empty() {
            css.push(';');
        }
        css.push_str("font-style:italic");
    }
    let mut decorations = Vec::new();
    if style.underline {
        decorations.push("underline");
    }
    if style.strike {
        decorations.push("line-through");
    }
    if !decorations.is_empty() {
        if !css.is_empty() {
            css.push(';');
        }
        let _ = write!(css, "text-decoration:{}", decorations.join(" "));
    }

    let mut classes = String::new();
    if style.obf {
        classes.push_str(" class=\"mc-obf\"");
    }
    if css.is_empty() && classes.is_empty() {
        return body;
    }
    let style_attr = if css.is_empty() {
        String::new()
    } else {
        format!(" style=\"{}\"", escape_html_basic(&css))
    };
    format!("<span{}{}>{}</span>", classes, style_attr, body)
}

/// 把带 Minecraft 颜色码的文本渲染成安全的 HTML 片段。
///
/// `default_color` 给没有颜色码的文本指定默认颜色（如 `#e2e8f0`）；
/// `allow_ampersand` 打开后同时识别 `&7` 写法。
pub fn render_mc_text(text: Option<&str>, default_color: Option<&str>, allow_ampersand: bool) -> String {
    let raw = match text {
        Some(t) if !t.is_empty() => t,
        _ => return String::new(),
    };
    let chars: Vec<char> = raw.chars().collect();
    let mut state = Style::initial(default_color);
    let mut out = String::new();
    let mut buf = String::new();
    let mut i = 0usize;
    let len = chars.len();

    while i < len {
        let ch = chars[i];
        let is_prefix = ch == '\u{00a7}' || (allow_ampersand && ch == '&');
        if is_prefix && i + 1 < len {
            let code = chars[i + 1].to_ascii_lowercase();

            // 十六进制写法：§x§R§R§G§G§B§B
            if code == 'x' {
                if let Some(hex) = parse_hex_sequence(&chars[i + 1..]) {
                    out.push_str(&emit(&buf, &state));
                    buf.clear();
                    state = Style::initial(Some(&format!("#{}", hex)));
                    // 1（x）+ 6×2（§R）个字符
                    i += 1 + 12;
                    continue;
                }
            }

            if let Some(color) = mc_color(code as u8) {
                out.push_str(&emit(&buf, &state));
                buf.clear();
                // 颜色码会重置全部格式（与 MC 行为一致）
                let color = color_override(code as u8).unwrap_or(color);
                state = Style::initial(Some(color));
                i += 2;
                continue;
            }

            if code == 'r' {
                out.push_str(&emit(&buf, &state));
                buf.clear();
                state = Style::initial(None);
                i += 2;
                continue;
            }

            let fmt = match code {
                'l' => Some((0, "bold")),
                'o' => Some((1, "italic")),
                'n' => Some((2, "underline")),
                'm' => Some((3, "strike")),
                'k' => Some((4, "obf")),
                _ => None,
            };
            if let Some((idx, _)) = fmt {
                out.push_str(&emit(&buf, &state));
                buf.clear();
                match idx {
                    0 => state.bold = true,
                    1 => state.italic = true,
                    2 => state.underline = true,
                    3 => state.strike = true,
                    _ => state.obf = true,
                }
                i += 2;
                continue;
            }
        }

        buf.push(ch);
        i += 1;
    }

    out.push_str(&emit(&buf, &state));
    out
}

/// 解析 `x§R§R§G§G§B§B` 序列，返回 6 位十六进制数字符串。
fn parse_hex_sequence(rest: &[char]) -> Option<String> {
    // rest[0] == 'x'，随后需要 6 组（§ 或 & + 十六进制位）
    if rest.len() < 13 {
        return None;
    }
    let mut digits = String::new();
    let mut idx = 1usize;
    for _ in 0..6 {
        let prefix = rest[idx];
        let digit = rest[idx + 1];
        if prefix != '\u{00a7}' && prefix != '&' {
            return None;
        }
        if !digit.is_ascii_hexdigit() {
            return None;
        }
        digits.push(digit.to_ascii_lowercase());
        idx += 2;
    }
    Some(digits)
}

/// 去掉颜色码，返回纯文本。
pub fn strip_mc_codes(text: Option<&str>) -> String {
    let raw = match text {
        Some(t) => t,
        None => return String::new(),
    };
    let chars: Vec<char> = raw.chars().collect();
    let mut out = String::new();
    let mut i = 0usize;
    while i < chars.len() {
        let ch = chars[i];
        if ch == '\u{00a7}' && i + 1 < chars.len() {
            let code = chars[i + 1].to_ascii_lowercase();
            if mc_color(code as u8).is_some()
                || matches!(code, 'l' | 'o' | 'n' | 'm' | 'k' | 'r' | 'x')
            {
                i += 2;
                continue;
            }
        }
        out.push(ch);
        i += 1;
    }
    out
}

/// 把多行文本逐行渲染成 HTML 片段。
pub fn render_mc_lines(lines: &[String], default_color: Option<&str>) -> Vec<String> {
    lines
        .iter()
        .map(|l| render_mc_text(Some(l), default_color, false))
        .collect()
}

/// 把 `fullLore` 这类多行文本批量转成纯文本。
pub fn plain_lines(lines: &[String]) -> Vec<String> {
    lines.iter().map(|l| strip_mc_codes(Some(l))).collect()
}
