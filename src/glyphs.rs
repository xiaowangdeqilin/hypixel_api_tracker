//! Minecraft 资源包 bitmap 字体的装饰符号提取。
//!
//! 资源包的 `assets/*/font/*.json` 里，`type: bitmap` 的 provider 把一张 PNG
//! 切成 `chars` 指定的网格：每个字符占一格，格宽 = 图宽 / 列数，格高 = height。
//! 这些字符多是 PUA 码点（如 U+E000），对应 SkyBlock 的图标（stats / skills /
//! mobs / weather 等）。本模块：
//!
//! 1. 从文本缓存里的 font JSON 解析出所有 bitmap provider；
//! 2. 把 `namespace:path` 资源定位符映射到包内实际路径
//!    (`hypixel_skyblock:gui/stats.png` → `assets/hypixel_skyblock/textures/gui/stats.png`)；
//! 3. 从 zip 里取出对应 PNG，读 IHDR 拿宽高，算出切分参数；
//! 4. 产出可直接渲染成 CSS sprite 的清单（不做任何 HTML，交给渲染层）。

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use crate::state::{load_json, write_json};

/// 短哈希（与 packs.rs 的 HASH_LENGTH 保持一致）。
fn sha256_short(data: &[u8]) -> String {
    crate::sha2_digest(data)[..16].to_string()
}

/// 单个装饰符号：码点 + 在 sprite 图里的格子坐标。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Glyph {
    /// 字符码点（十六进制字符串，如 "e000"）；0 表示空占位（不渲染）。
    pub code: String,
    pub row: usize,
    pub col: usize,
}

/// 一张被 font JSON 引用的 sprite 图。
#[derive(Debug, Clone)]
pub struct Sprite {
    /// 资源定位符原文，如 `hypixel_skyblock:gui/stats.png`。
    pub source: String,
    /// 包内实际路径，如 `assets/hypixel_skyblock/textures/gui/stats.png`。
    pub path: String,
    /// 缓存到 data/glyphs 下的文件名（hash + .png）。
    pub file: String,
    /// PNG 原始字节（供落盘；不进入 data.json）。
    pub bytes: Vec<u8>,
    /// 图片尺寸。
    pub width: u32,
    pub height: u32,
    /// 切分参数：cols × rows 个格子，每格 cell_w × cell_h。
    pub cols: usize,
    pub rows: usize,
    pub cell_w: f64,
    pub cell_h: u32,
    /// 所有符号（含空占位），顺序与 chars 展平一致。
    pub glyphs: Vec<Glyph>,
}

/// `namespace:path` → 包内实际路径。
///
/// Minecraft 资源定位符约定：`assets/<namespace>/textures/<path>`；
/// 省略 namespace 时按 `minecraft` 处理。已经带 `assets/` 前缀的直接返回。
pub fn resource_path(locator: &str) -> String {
    let locator = locator.trim();
    if locator.is_empty() {
        return String::new();
    }
    if locator.starts_with("assets/") {
        return locator.to_string();
    }
    let (ns, rest) = match locator.split_once(':') {
        Some((ns, rest)) => (if ns.is_empty() { "minecraft" } else { ns }, rest),
        None => ("minecraft", locator),
    };
    // 已经带 textures/ 的不要重复拼
    if rest.starts_with("textures/") {
        format!("assets/{}/{}", ns, rest)
    } else {
        format!("assets/{}/textures/{}", ns, rest)
    }
}

/// 从 PNG 字节头读 IHDR 的宽高（不解码像素）。
pub fn png_size(bytes: &[u8]) -> Option<(u32, u32)> {
    const SIG: [u8; 8] = [0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a];
    if bytes.len() < 24 || bytes[..8] != SIG {
        return None;
    }
    let w = u32::from_be_bytes(bytes[16..20].try_into().ok()?);
    let h = u32::from_be_bytes(bytes[20..24].try_into().ok()?);
    if w == 0 || h == 0 {
        return None;
    }
    Some((w, h))
}

/// 从 font JSON 文本里解析 bitmap provider，产出 (定位符, height, chars 行数组)。
///
/// 只认 `type == "bitmap"` 且带 `chars` 数组的 provider；其它 provider
/// （空间、ttf、引用）忽略。返回顺序按 provider 在 JSON 里的出现顺序。
pub fn parse_bitmap_providers(json_text: &str) -> Vec<(String, u32, Vec<Vec<String>>)> {
    let Ok(value) = serde_json::from_str::<Value>(json_text) else {
        return Vec::new();
    };
    let Some(providers) = value.get("providers").and_then(|p| p.as_array()) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for provider in providers {
        if provider.get("type").and_then(|v| v.as_str()) != Some("bitmap") {
            continue;
        }
        let Some(file) = provider.get("file").and_then(|v| v.as_str()) else {
            continue;
        };
        let height = provider
            .get("height")
            .and_then(|v| v.as_u64())
            .unwrap_or(0) as u32;
        if height == 0 {
            continue;
        }
        let Some(rows) = provider.get("chars").and_then(|v| v.as_array()) else {
            continue;
        };
        // 每行是字符串，逐字符展开成码点；空串行跳过
        let parsed: Vec<Vec<String>> = rows
            .iter()
            .filter_map(|row| row.as_str())
            .map(|row| {
                row.chars()
                    .map(|c| format!("{:x}", c as u32))
                    .collect()
            })
            .filter(|row: &Vec<String>| !row.is_empty())
            .collect();
        if parsed.is_empty() {
            continue;
        }
        out.push((file.to_string(), height, parsed));
    }
    out
}

/// 从文本缓存（path → 内容）里找出所有 font JSON 并解析 bitmap provider。
///
/// 返回 (定位符, height, chars) 列表，按 font 路径排序保证结果稳定。
pub fn providers_from_texts(texts: &BTreeMap<String, String>) -> Vec<(String, u32, Vec<Vec<String>>)> {
    let mut out = Vec::new();
    let mut font_paths: Vec<&String> = texts
        .keys()
        .filter(|k| k.contains("/font/") && k.to_ascii_lowercase().ends_with(".json"))
        .collect();
    font_paths.sort();
    for path in font_paths {
        let text = &texts[path];
        for provider in parse_bitmap_providers(text) {
            out.push(provider);
        }
    }
    out
}

/// 生成一份 sprite：从 zip 内容映射里取 PNG，算好切分与码点格子。
///
/// `zip_files` 是包内路径 → 字节的映射（只放被 font 引用的 PNG 即可）。
/// 图片尺寸非法、列数为 0、PNG 缺失时返回 None（跳过该 provider，不影响整体）。
pub fn build_sprite(
    locator: &str,
    height: u32,
    chars: &[Vec<String>],
    zip_files: &BTreeMap<String, Vec<u8>>,
) -> Option<Sprite> {
    let path = resource_path(locator);
    let bytes = zip_files.get(&path)?;
    let (width, img_height) = png_size(bytes)?;
    let cols = chars.first().map(|r| r.len())?;
    let rows = chars.len();
    if cols == 0 || rows == 0 {
        return None;
    }
    // 每格宽 = 图宽 / 列数；格子高用 provider 的 height
    let cell_w = width as f64 / cols as f64;
    // 校验：图片高度应等于 rows * height，否则切分会错位
    if img_height != rows as u32 * height {
        return None;
    }
    let hash = sha256_short(bytes);
    let ext = ".png";
    let mut glyphs = Vec::new();
    for (row, line) in chars.iter().enumerate() {
        for (col, code) in line.iter().enumerate() {
            glyphs.push(Glyph {
                code: code.clone(),
                row,
                col,
            });
        }
    }
    Some(Sprite {
        source: locator.to_string(),
        path,
        file: format!("{}{}", hash, ext),
        bytes: bytes.clone(),
        width,
        height: img_height,
        cols,
        rows,
        cell_w,
        cell_h: height,
        glyphs,
    })
}

/// sprite 清单写入 data/pack_glyphs.json（只存元数据，PNG 内容在 data/glyphs/）。
pub fn glyphs_manifest_path(data_dir: &std::path::Path) -> std::path::PathBuf {
    data_dir.join("pack_glyphs.json")
}

pub fn glyphs_dir(data_dir: &std::path::Path) -> std::path::PathBuf {
    data_dir.join("glyphs")
}

pub fn load_pack_glyphs(data_dir: &std::path::Path) -> Value {
    load_json(&glyphs_manifest_path(data_dir)).unwrap_or_else(|| json!({}))
}

/// Sprite → JSON（供 data.json 与页面渲染）。
pub fn sprite_to_value(sprite: &Sprite) -> Value {
    let cells: Vec<Value> = sprite
        .glyphs
        .iter()
        .map(|g| {
            json!({
                "code": g.code,
                "row": g.row,
                "col": g.col,
            })
        })
        .collect();
    json!({
        "source": sprite.source,
        "path": sprite.path,
        "file": sprite.file,
        "width": sprite.width,
        "height": sprite.height,
        "cols": sprite.cols,
        "rows": sprite.rows,
        "cell_w": sprite.cell_w,
        "cell_h": sprite.cell_h,
        "glyphs": cells,
    })
}

/// 落盘 sprite 清单（调用方负责写 PNG 内容）。旧版本 PNG 由调用方按清单清理。
pub fn save_pack_glyphs(data_dir: &std::path::Path, sprites: &[Sprite], pack_hash: &str) {
    let list: Vec<Value> = sprites.iter().map(sprite_to_value).collect();
    let manifest = json!({
        "pack_hash": pack_hash,
        "generated_at": crate::timeutil::now_beijing(),
        "sprites": list,
    });
    let _ = write_json(&glyphs_manifest_path(data_dir), &manifest);
}

/// 从清单 JSON 里读出 sprite 列表（渲染层用）。
pub fn sprites_from_manifest(value: &Value) -> Vec<Map<String, Value>> {
    value
        .get("sprites")
        .and_then(|s| s.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_object().cloned())
                .collect()
        })
        .unwrap_or_default()
}
