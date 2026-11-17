//! 自更新：以本 GitHub 仓库的 Release 为数据源。
//!
//! 流程：查 `releases/latest` → 比较语义版本 → 按当前平台挑资产
//! （`hypixel-tracker-v{ver}-{target}[.exe]`）→ 下载 → 用 `SHA256SUMS.txt`
//! 校验 → 原子替换可执行文件（旧文件留作 `.old` 备份，失败可回滚）。
//!
//! 安全约束：
//! - 没有校验和资产时拒绝更新（避免被中间人替换）；
//! - 下载体积上限 120MB，超限即中止；
//! - 只在版本号严格更高时更新，避免降级/重装死循环；
//! - 替换失败会把备份改回来，保证程序还能启动。

use std::io::Read;
use std::path::{Path, PathBuf};

use serde_json::Value;
use sha2::{Digest, Sha256};

/// 更新源仓库。
pub const REPO: &str = "xiaowangdeqilin/hypixel_api_tracker";
const MAX_DOWNLOAD: u64 = 120 * 1024 * 1024;
const UA: &str = "hypixel-tracker-updater/3";

/// 当前程序的版本（Cargo 包版本，与 `VERSION` 文件保持一致）。
pub fn current_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

/// 当前构建的目标三元组（由 `build.rs` 注入）。
pub fn target_triple() -> &'static str {
    option_env!("BUILD_TARGET").unwrap_or_else(|| {
        match (std::env::consts::OS, std::env::consts::ARCH) {
            ("linux", "x86_64") => "x86_64-unknown-linux-gnu",
            ("linux", "aarch64") => "aarch64-unknown-linux-gnu",
            ("macos", "x86_64") => "x86_64-apple-darwin",
            ("macos", "aarch64") => "aarch64-apple-darwin",
            ("windows", "x86_64") => "x86_64-pc-windows-msvc",
            ("windows", "aarch64") => "aarch64-pc-windows-msvc",
            _ => "unknown",
        }
    })
}

/// 语义版本比较：只比较 `主.次.修订`，忽略预发布后缀与 `v` 前缀。
pub fn parse_version(text: &str) -> Option<(u64, u64, u64)> {
    let core = text
        .trim()
        .trim_start_matches('v')
        .split(['-', '+'])
        .next()?
        .trim();
    let mut parts = core.split('.');
    let major = parts.next()?.parse().ok()?;
    let minor = parts.next().unwrap_or("0").parse().ok()?;
    let patch = parts.next().unwrap_or("0").parse().ok()?;
    Some((major, minor, patch))
}

pub fn is_newer(latest: &str, current: &str) -> bool {
    match (parse_version(latest), parse_version(current)) {
        (Some(l), Some(c)) => l > c,
        _ => false,
    }
}

fn agent() -> ureq::Agent {
    ureq::AgentBuilder::new()
        .timeout_connect(std::time::Duration::from_secs(8))
        .timeout(std::time::Duration::from_secs(60))
        .user_agent(UA)
        .build()
}

fn github_get(url: &str) -> Result<ureq::Response, String> {
    let mut req = agent().get(url).set("Accept", "application/vnd.github+json");
    if let Ok(token) = std::env::var("GITHUB_TOKEN") {
        if !token.trim().is_empty() {
            req = req.set("Authorization", &format!("Bearer {}", token.trim()));
        }
    }
    req.call().map_err(|e| match e {
        ureq::Error::Status(404, _) => "仓库还没有任何 Release".to_string(),
        ureq::Error::Status(403, _) => "GitHub API 限流，请稍后再试或设置 GITHUB_TOKEN".to_string(),
        other => format!("请求 GitHub 失败：{other}"),
    })
}

/// 更新检查结果（直接给 `/api/update/check` 用）。
pub fn check() -> Result<Value, String> {
    let resp = github_get(&format!(
        "https://api.github.com/repos/{REPO}/releases/latest"
    ))?;
    let body: Value = resp.into_json().map_err(|e| format!("解析 Release 失败：{e}"))?;
    let tag = body["tag_name"].as_str().unwrap_or_default().to_string();
    let current = current_version();
    let asset_name = binary_asset_name(&tag);
    let mut has_asset = false;
    let mut download = String::new();
    if let Some(assets) = body["assets"].as_array() {
        for asset in assets {
            let name = asset["name"].as_str().unwrap_or_default();
            if name == asset_name {
                has_asset = true;
                download = asset["browser_download_url"].as_str().unwrap_or_default().to_string();
            }
        }
    }
    Ok(serde_json::json!({
        "repo": REPO,
        "current": current,
        "latest": tag,
        "available": is_newer(&tag, &current),
        "target": target_triple(),
        "asset": asset_name,
        "asset_found": has_asset,
        "download": download,
        "page": body["html_url"].as_str().unwrap_or(""),
        "published": body["published_at"].as_str().unwrap_or(""),
        "notes": body["body"].as_str().unwrap_or("").chars().take(1200).collect::<String>(),
    }))
}

/// `hypixel-tracker-v3.0.0-x86_64-unknown-linux-gnu[.exe]`
fn binary_asset_name(tag: &str) -> String {
    let tag = if tag.starts_with('v') { tag.to_string() } else { format!("v{tag}") };
    let ext = if cfg!(windows) { ".exe" } else { "" };
    format!("hypixel-tracker-{tag}-{}{ext}", target_triple())
}

fn checksums_url(version: &str) -> String {
    let tag = if version.starts_with('v') { version.to_string() } else { format!("v{version}") };
    format!("https://github.com/{REPO}/releases/download/{tag}/SHA256SUMS.txt")
}

pub fn download_url(version: &str, asset: &str) -> String {
    let tag = if version.starts_with('v') { version.to_string() } else { format!("v{version}") };
    format!("https://github.com/{REPO}/releases/download/{tag}/{asset}")
}

fn download_to(url: &str, dest: &Path) -> Result<Vec<u8>, String> {
    let resp = agent()
        .get(url)
        .call()
        .map_err(|e| format!("下载失败（{url}）：{e}"))?;
    let mut buf = Vec::new();
    resp.into_reader()
        .take(MAX_DOWNLOAD + 1)
        .read_to_end(&mut buf)
        .map_err(|e| format!("读取下载内容失败：{e}"))?;
    if buf.len() as u64 > MAX_DOWNLOAD {
        return Err("下载体积超过 120MB，已中止".into());
    }
    std::fs::write(dest, &buf).map_err(|e| format!("写入临时文件失败：{e}"))?;
    Ok(buf)
}

fn expected_sha(version: &str, asset: &str) -> Result<String, String> {
    let text = agent()
        .get(&checksums_url(version))
        .call()
        .map_err(|e| format!("下载校验和失败：{e}"))?
        .into_string()
        .map_err(|e| format!("读取校验和失败：{e}"))?;
    for line in text.lines() {
        let mut it = line.split_whitespace();
        let Some(sum) = it.next() else { continue };
        let name = it.next().unwrap_or("").trim_start_matches('*');
        if name == asset {
            return Ok(sum.to_ascii_lowercase());
        }
    }
    Err(format!("校验和文件里没有 {asset}，已拒绝更新"))
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

/// 备份路径：`tracker-server` → `tracker-server.old`（Windows 保留 `.exe`）。
fn backup_path(exe: &Path) -> PathBuf {
    let mut name = exe.file_name().unwrap_or_default().to_os_string();
    if cfg!(windows) {
        name.push(".old.exe");
    } else {
        name.push(".old");
    }
    exe.with_file_name(name)
}

/// 执行自更新。返回人类可读的结果说明。
pub fn self_update() -> Result<String, String> {
    let info = check()?;
    let latest = info["latest"].as_str().unwrap_or_default().to_string();
    if !info["available"].as_bool().unwrap_or(false) {
        return Ok(format!("已是最新版本 v{}", current_version()));
    }
    if !info["asset_found"].as_bool().unwrap_or(false) {
        return Err(format!(
            "Release {latest} 里没有当前平台（{}）的可执行文件",
            target_triple()
        ));
    }
    let asset = info["asset"].as_str().unwrap_or_default().to_string();
    let url = download_url(&latest, &asset);
    let exe = std::env::current_exe().map_err(|e| format!("定位自身可执行文件失败：{e}"))?;
    let tmp = exe.with_file_name(format!("{}.download", asset.replace('/', "_")));
    let bytes = download_to(&url, &tmp)?;
    let expected = expected_sha(&latest, &asset)?;
    let actual = sha256_hex(&bytes);
    if actual != expected {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("校验和不匹配（期望 {expected}，实际 {actual}），已中止"));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o755));
    }
    let backup = backup_path(&exe);
    if let Err(e) = std::fs::rename(&exe, &backup) {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("备份当前版本失败（可能没有写权限，试试 sudo）：{e}"));
    }
    if let Err(e) = std::fs::rename(&tmp, &exe) {
        // 回滚：把备份改回原名，保证程序还能跑
        let _ = std::fs::rename(&backup, &exe);
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("替换可执行文件失败，已回滚：{e}"));
    }
    Ok(format!(
        "已从 v{} 更新到 {latest}；旧版本保留为 {}，重启后生效",
        current_version(),
        backup.display()
    ))
}

/// 启动时自动更新（`AUTO_UPDATE=apply`）：更新成功则拉起新进程并退出。
///
/// 必须在绑定端口之前调用，否则新旧两个进程会抢同一个端口。
pub fn auto_update_at_startup() {
    if std::env::var("AUTO_UPDATE").unwrap_or_default() != "apply" {
        return;
    }
    match self_update() {
        Ok(msg) if msg.contains("更新到") => {
            eprintln!("[更新] {msg}");
            let exe = match std::env::current_exe() {
                Ok(p) => p,
                Err(_) => return,
            };
            let mut cmd = std::process::Command::new(exe);
            cmd.args(std::env::args().skip(1))
                .env("HYPIXEL_START_DELAY_MS", "600")
                .env("AUTO_UPDATE", "check");
            match cmd.spawn() {
                Ok(_) => std::process::exit(0),
                Err(e) => eprintln!("[更新] 启动新版本失败，请手动重启：{e}"),
            }
        }
        Ok(_) => {}
        Err(e) => eprintln!("[更新] 自动更新未完成：{e}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_compare_handles_prefix_and_suffix() {
        assert!(is_newer("v3.0.0", "2.9.9"));
        assert!(is_newer("3.0.1", "3.0.0"));
        assert!(!is_newer("v3.0.0", "3.0.0"));
        assert!(!is_newer("3.0.0-beta.1", "3.0.0"));
        assert!(parse_version("v10.20.30-rc1").is_some());
        assert_eq!(parse_version("v3.0"), Some((3, 0, 0)));
        assert_eq!(parse_version("垃圾"), None);
    }

    #[test]
    fn asset_name_matches_release_convention() {
        let name = binary_asset_name("v3.0.0");
        assert!(name.starts_with("hypixel-tracker-v3.0.0-"));
        assert_eq!(name, binary_asset_name("3.0.0"));
    }

    #[test]
    fn sha256_matches_known_vector() {
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
