//! locales-gen：语言包生成器（原 scripts/build_locales.py 的移植）。
//!
//! 单一事实来源是 `i18n-src/translations.json`：
//! - `packs` 下每个键是语言代码，值是「简体中文原文 -> 译文」的映射；
//! - 值为 `""` 表示该语言不需要这个成分（如中文量词）。
//!
//! 运行后把每个语言写为 `locales/<语言代码>.json`，并做三项校验：
//! 1. 全部语言键集合一致；
//! 2. 含 `{占位符}` 的键在每个语言里占位符集合一致；
//! 3. 含 HTML 标签的键在每个语言里标签集合一致（防止译文破坏版式）。

use std::io::Write as _;

use serde_json::Value;

/// 嵌入的翻译数据（编译期打包，运行时不依赖外部文件）。
const TRANSLATIONS: &str = include_str!("../../i18n-src/translations.json");

fn main() {
    let code = run();
    std::process::exit(code);
}

fn run() -> i32 {
    let data: Value = match serde_json::from_str(TRANSLATIONS) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("[语言包] 解析 i18n-src/translations.json 失败：{}", e);
            return 1;
        }
    };
    let Some(packs) = data.get("packs").and_then(|p| p.as_object()) else {
        eprintln!("[语言包] 缺少 packs 字段");
        return 1;
    };

    // 基准键集（英语）
    let base_pack = match packs.get("en").and_then(|p| p.as_object()) {
        Some(p) => p,
        None => {
            eprintln!("[语言包] 缺少基准语言 en");
            return 1;
        }
    };
    let base_keys: Vec<&String> = base_pack.keys().collect();

    let locales_dir = hypixel_tracker::state::base_dir().join("locales");
    if let Err(e) = std::fs::create_dir_all(&locales_dir) {
        eprintln!("[语言包] 创建目录失败：{}", e);
        return 1;
    }

    let mut errors = 0usize;
    for (code, pack_value) in packs {
        let Some(pack) = pack_value.as_object() else {
            eprintln!("[语言包] {} 不是对象", code);
            errors += 1;
            continue;
        };

        // 校验 1：键集合一致
        let missing: Vec<String> = base_keys
            .iter()
            .filter(|k| !pack.contains_key(**k))
            .map(|k| k.to_string())
            .collect();
        let extra: Vec<String> = pack
            .keys()
            .filter(|k| !base_pack.contains_key(*k))
            .cloned()
            .collect();
        if !missing.is_empty() || !extra.is_empty() {
            eprintln!(
                "[语言包] {} 键集合不一致：缺 {} 个 {:?}；多 {} 个 {:?}",
                code,
                missing.len(),
                &missing[..missing.len().min(3)],
                extra.len(),
                &extra[..extra.len().min(3)],
            );
            errors += 1;
        }

        // 校验 2：占位符一致
        let placeholder = |text: &str| -> Vec<String> {
            let mut out = Vec::new();
            let chars: Vec<char> = text.chars().collect();
            let mut i = 0;
            while i < chars.len() {
                if chars[i] == '{' {
                    if let Some(end) = chars[i..].iter().position(|c| *c == '}') {
                        let name: String = chars[i + 1..i + end].iter().collect();
                        if !name.is_empty()
                            && name.chars().all(|c| c.is_alphanumeric() || c == '_')
                        {
                            out.push(name);
                        }
                        i += end + 1;
                        continue;
                    }
                }
                i += 1;
            }
            out.sort();
            out.dedup();
            out
        };
        for key in &base_keys {
            if !placeholder(key).is_empty() {
                let translated = pack.get(*key).and_then(|v| v.as_str()).unwrap_or("");
                if placeholder(key) != placeholder(translated) {
                    eprintln!("[语言包] {} 占位符不一致：{}", code, &key[..key.len().min(24)]);
                    errors += 1;
                }
            }
        }

        // 校验 3：HTML 标签一致
        let tags = |text: &str| -> Vec<String> {
            let mut out = Vec::new();
            let mut rest = text;
            while let Some(start) = rest.find('<') {
                if let Some(end) = rest[start..].find('>') {
                    let tag = &rest[start + 1..start + end];
                    let name: String = tag
                        .trim_start_matches('/')
                        .chars()
                        .take_while(|c| c.is_ascii_alphanumeric())
                        .collect();
                    if !name.is_empty() {
                        out.push(if tag.starts_with('/') { format!("/{}", name) } else { name });
                    }
                    rest = &rest[start + end + 1..];
                } else {
                    break;
                }
            }
            out.sort();
            out
        };
        for key in &base_keys {
            if !tags(key).is_empty() {
                let translated = pack.get(*key).and_then(|v| v.as_str()).unwrap_or("");
                if tags(key) != tags(translated) {
                    eprintln!("[语言包] {} HTML 标签不一致：{}", code, &key[..key.len().min(24)]);
                    errors += 1;
                }
            }
        }

        // 写盘
        let path = locales_dir.join(format!("{}.json", code));
        match std::fs::File::create(&path) {
            Ok(mut fh) => {
                let body = serde_json::to_string_pretty(pack_value).unwrap_or_default();
                let _ = fh.write_all(body.as_bytes());
                let _ = fh.write_all(b"\n");
                println!("[语言包] 已生成 {}: {} 条", code, pack.len());
            }
            Err(e) => {
                eprintln!("[语言包] 写入 {} 失败：{}", code, e);
                errors += 1;
            }
        }
    }

    if errors > 0 {
        eprintln!("[语言包] 完成，但有 {} 处校验问题", errors);
        return 1;
    }
    println!("[语言包] 共 {} 种语言 -> {}", packs.len(), locales_dir.display());
    0
}
