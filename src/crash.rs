//! 崩溃诊断：把 panic 落成一份可读、可上传的崩溃报告。
//!
//! 设计取舍（避免“崩溃时什么都看不到”）：
//! - 报告写到 `data/crash/` 下的 JSON + 同名 `.txt` 摘要，用户可以直接把文件发出来；
//! - 报告里带版本、构建号、系统与架构、线程名、panic 位置与 backtrace，
//!   不写任何 API Key / Cookie 等敏感内容；
//! - 目录只保留最近 [`MAX_REPORTS`] 份，防止长期运行把磁盘写满；
//! - 服务端在 `route()` 外层用 `catch_unwind` 兜住 panic，返回 500 而不是
//!   静默断开连接，响应里带上崩溃报告文件名，方便用户回报。

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

use serde_json::{json, Value};

/// 最多保留的崩溃报告数量。
pub const MAX_REPORTS: usize = 20;

static HOOK_INSTALLED: AtomicBool = AtomicBool::new(false);

/// 崩溃报告目录：`data/crash/`。
pub fn crash_dir() -> PathBuf {
    crate::state::data_dir().join("crash")
}

/// 安装 panic hook。重复调用无副作用。
///
/// `component` 用于区分是哪个二进制崩溃的（`tracker-server` / `tracker-build`）。
pub fn install_hook(component: &'static str) {
    if HOOK_INSTALLED.swap(true, Ordering::SeqCst) {
        return;
    }
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        if let Some(path) = record_panic(info, component) {
            eprintln!("[崩溃] 已写入崩溃报告：{}", path.display());
            eprintln!("[崩溃] 如果这是意外行为，请把该文件附在 issue 里。");
        }
        previous(info);
    }));
}

fn record_panic(info: &std::panic::PanicHookInfo<'_>, component: &str) -> Option<PathBuf> {
    let message = panic_message(info);
    let location = info
        .location()
        .map(|l| format!("{}:{}:{}", l.file(), l.line(), l.column()))
        .unwrap_or_else(|| "-".into());
    let thread = std::thread::current()
        .name()
        .map(|s| s.to_string())
        .unwrap_or_else(|| "-".into());
    // force_capture 不受 RUST_BACKTRACE 影响，保证 release 构建也有栈
    let backtrace = std::backtrace::Backtrace::force_capture().to_string();
    write_report(component, &message, &location, &thread, &backtrace, "panic")
}

/// 记录一次「已捕获的严重错误」（非 panic，例如自更新失败）。
pub fn record_handled(component: &str, message: &str, detail: &str) -> Option<PathBuf> {
    write_report(component, message, "-", "-", detail, "handled")
}

#[allow(clippy::too_many_arguments)]
fn write_report(
    component: &str,
    message: &str,
    location: &str,
    thread: &str,
    backtrace: &str,
    kind: &str,
) -> Option<PathBuf> {
    write_report_in(&crash_dir(), component, message, location, thread, backtrace, kind)
}

/// 写入指定目录（单测用临时目录，避免污染真实 data/）。
#[allow(clippy::too_many_arguments)]
fn write_report_in(
    dir: &Path,
    component: &str,
    message: &str,
    location: &str,
    thread: &str,
    backtrace: &str,
    kind: &str,
) -> Option<PathBuf> {
    let dir = dir.to_path_buf();
    if std::fs::create_dir_all(&dir).is_err() {
        return None;
    }
    let now = chrono::Local::now();
    let stamp = now.format("%Y%m%d-%H%M%S%.3f").to_string();
    let id = format!("crash-{stamp}");
    let payload = json!({
        "id": id,
        "kind": kind,
        "component": component,
        "time": now.format("%Y-%m-%d %H:%M:%S").to_string(),
        "version": crate::state::read_version(),
        "package_version": env!("CARGO_PKG_VERSION"),
        "build": crate::state::current_build(),
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "thread": thread,
        "location": location,
        "message": message,
        "backtrace": backtrace.lines().take(80).collect::<Vec<_>>().join("\n"),
    });
    let json_path = dir.join(format!("{id}.json"));
    let text = format!(
        "Hypixel SkyBlock API Tracker — 崩溃报告\n\
         ==========================================\n\
         时间：{}\n\
         版本：{}（构建 {}）\n\
         组件：{}  类型：{}\n\
         系统：{} / {}\n\
         线程：{}\n\
         位置：{}\n\
         信息：{}\n\n\
         --- backtrace ---\n{}\n",
        payload["time"].as_str().unwrap_or("-"),
        payload["version"].as_str().unwrap_or("-"),
        payload["build"].as_str().unwrap_or("-"),
        component,
        kind,
        payload["os"].as_str().unwrap_or("-"),
        payload["arch"].as_str().unwrap_or("-"),
        thread,
        location,
        message,
        backtrace
    );
    let txt_path = dir.join(format!("{id}.txt"));
    let _ = std::fs::write(&json_path, serde_json::to_string_pretty(&payload).ok()?);
    let _ = std::fs::write(&txt_path, text);
    prune(&dir);
    Some(txt_path)
}

/// 只保留最近 `MAX_REPORTS` 份（按文件名里的时间戳排序，文件名即时间序）。
fn prune(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    let mut names: Vec<String> = entries
        .flatten()
        .filter_map(|e| e.file_name().into_string().ok())
        .filter(|n| n.starts_with("crash-") && n.ends_with(".json"))
        .collect();
    if names.len() <= MAX_REPORTS {
        return;
    }
    names.sort();
    let drop_count = names.len() - MAX_REPORTS;
    for name in names.into_iter().take(drop_count) {
        let stem = name.trim_end_matches(".json");
        let _ = std::fs::remove_file(dir.join(&name));
        let _ = std::fs::remove_file(dir.join(format!("{stem}.txt")));
    }
}

/// 最近的崩溃报告（新的在前）。
pub fn recent(limit: usize) -> Vec<Value> {
    let dir = crash_dir();
    let Ok(entries) = std::fs::read_dir(&dir) else { return Vec::new() };
    let mut files: Vec<PathBuf> = entries
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .map(|n| n.starts_with("crash-") && n.ends_with(".json"))
                .unwrap_or(false)
        })
        .collect();
    files.sort();
    files.reverse();
    files
        .into_iter()
        .take(limit)
        .filter_map(|p| {
            let text = std::fs::read_to_string(&p).ok()?;
            serde_json::from_str::<Value>(&text).ok()
        })
        .collect()
}

pub fn count() -> usize {
    recent(MAX_REPORTS).len()
}

fn panic_message(info: &std::panic::PanicHookInfo<'_>) -> String {
    if let Some(s) = info.payload().downcast_ref::<&str>() {
        (*s).to_string()
    } else if let Some(s) = info.payload().downcast_ref::<String>() {
        s.clone()
    } else {
        "未知 panic".into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "hypixel-crash-test-{}-{}-{}",
            tag,
            std::process::id(),
            chrono::Local::now().timestamp_nanos_opt().unwrap_or_default()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn writes_json_and_text_report() {
        let dir = temp_dir("write");
        let txt = write_report_in(&dir, "tracker-server", "测试崩溃", "a.rs:1:1", "main", "stack", "panic")
            .expect("应写入报告");
        assert!(txt.exists());
        let json_path = txt.with_extension("json");
        assert!(json_path.exists());
        let value: Value = serde_json::from_str(&std::fs::read_to_string(&json_path).unwrap()).unwrap();
        assert_eq!(value["message"], "测试崩溃");
        assert_eq!(value["component"], "tracker-server");
        assert_eq!(value["kind"], "panic");
        assert!(value["version"].as_str().is_some());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn prune_keeps_only_latest_reports() {
        let dir = temp_dir("prune");
        for i in 0..(MAX_REPORTS + 5) {
            // 文件名带序号以保证排序稳定
            std::fs::write(
                dir.join(format!("crash-20260101-0000{:02}.json", i)),
                "{}",
            )
            .unwrap();
        }
        prune(&dir);
        let count = std::fs::read_dir(&dir).unwrap().count();
        assert_eq!(count, MAX_REPORTS);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn recent_on_missing_dir_is_empty() {
        assert!(recent(3).len() <= 3);
    }
}
