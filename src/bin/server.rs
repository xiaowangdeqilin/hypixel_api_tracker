//! tracker-server：本地监控服务入口。
//!
//! 命令行：
//! - 默认（无参数）：启动 HTTP 服务
//! - `--port N` / `--bind ADDR`：监听地址
//! - `--check-update`：问 GitHub 有没有新版本（JSON 输出，便于脚本消费）
//! - `--self-update`：下载并替换当前可执行文件（校验 SHA256，失败回滚）
//! - `--crash-list`：列出最近的崩溃报告
//! - `--version` / `--help`

use hypixel_tracker::{crash, server_app, state, updater};

/// 帮助文本：覆盖 8 种最常见的系统语言，其余回退英文。
fn help(lang: &str) -> &'static str {
    match lang {
        "zh" => "Hypixel SkyBlock API 变更监控台（本地服务）

用法：
  tracker-server [选项]

选项：
  --port <端口>      监听端口（默认 8000）
  --bind <地址>      监听地址（默认 127.0.0.1）
  --check-update     检查 GitHub 上是否有新版本
  --self-update      下载并替换为最新版本（校验 SHA256，失败回滚）
  --crash-list       列出最近的崩溃报告
  --version          打印版本与目标平台
  --help             显示本帮助

环境变量：
  BIND_ADDR          监听地址，等价于 --bind
  AUTO_UPDATE        设为 apply 时启动即自动更新并重启
  GITHUB_TOKEN       提高 GitHub API 限额
  ENABLE_I18N=1      开启服务端页面多语言
",
        "ja" => "Hypixel SkyBlock API 変更モニター（ローカルサービス）

使い方:
  tracker-server [オプション]

オプション:
  --port <番号>      待ち受けポート（既定 8000）
  --bind <アドレス>  待ち受けアドレス（既定 127.0.0.1）
  --check-update     GitHub の最新リリースを確認
  --self-update      最新版をダウンロードして置き換え（SHA256 検証、失敗時は巻き戻し）
  --crash-list       最近のクラッシュレポートを表示
  --version          バージョンと対象プラットフォームを表示
  --help             このヘルプを表示

環境変数:
  BIND_ADDR, AUTO_UPDATE=apply, GITHUB_TOKEN, ENABLE_I18N=1
",
        "de" => "Hypixel SkyBlock API-Änderungsmonitor (lokaler Dienst)

Verwendung:
  tracker-server [Optionen]

Optionen:
  --port <Port>      Port (Standard 8000)
  --bind <Adresse>   Adresse (Standard 127.0.0.1)
  --check-update     Auf neue Version auf GitHub prüfen
  --self-update      Neueste Version herunterladen und ersetzen (SHA256, Rollback bei Fehler)
  --crash-list       Letzte Absturzberichte anzeigen
  --version          Version und Zielplattform ausgeben
  --help             Diese Hilfe anzeigen

Umgebung: BIND_ADDR, AUTO_UPDATE=apply, GITHUB_TOKEN, ENABLE_I18N=1
",
        "es" => "Monitor de cambios de Hypixel SkyBlock API (servicio local)

Uso:
  tracker-server [opciones]

Opciones:
  --port <puerto>    Puerto de escucha (por defecto 8000)
  --bind <dirección> Dirección de escucha (por defecto 127.0.0.1)
  --check-update     Comprobar si hay una versión nueva en GitHub
  --self-update      Descargar y sustituir por la última versión (SHA256, revierte si falla)
  --crash-list       Mostrar los informes de fallo recientes
  --version          Mostrar versión y plataforma
  --help             Mostrar esta ayuda

Entorno: BIND_ADDR, AUTO_UPDATE=apply, GITHUB_TOKEN, ENABLE_I18N=1
",
        "fr" => "Moniteur de changements Hypixel SkyBlock API (service local)

Utilisation :
  tracker-server [options]

Options :
  --port <port>      Port d'écoute (8000 par défaut)
  --bind <adresse>   Adresse d'écoute (127.0.0.1 par défaut)
  --check-update     Vérifier les nouvelles versions sur GitHub
  --self-update      Télécharger et remplacer par la dernière version (SHA256, retour arrière en cas d'échec)
  --crash-list       Afficher les derniers rapports de plantage
  --version          Afficher la version et la plateforme
  --help             Afficher cette aide

Environnement : BIND_ADDR, AUTO_UPDATE=apply, GITHUB_TOKEN, ENABLE_I18N=1
",
        "ru" => "Монитор изменений Hypixel SkyBlock API (локальный сервис)

Использование:
  tracker-server [параметры]

Параметры:
  --port <порт>      Порт (по умолчанию 8000)
  --bind <адрес>     Адрес (по умолчанию 127.0.0.1)
  --check-update     Проверить наличие новой версии на GitHub
  --self-update      Скачать и заменить на новую версию (SHA256, откат при ошибке)
  --crash-list       Показать последние отчёты о сбоях
  --version          Показать версию и платформу
  --help             Показать эту справку

Переменные: BIND_ADDR, AUTO_UPDATE=apply, GITHUB_TOKEN, ENABLE_I18N=1
",
        "pt" => "Monitor de alterações da Hypixel SkyBlock API (serviço local)

Uso:
  tracker-server [opções]

Opções:
  --port <porta>     Porta (padrão 8000)
  --bind <endereço>  Endereço (padrão 127.0.0.1)
  --check-update     Verificar nova versão no GitHub
  --self-update      Baixar e substituir pela versão mais recente (SHA256, reverte em caso de falha)
  --crash-list       Listar os relatórios de falha recentes
  --version          Mostrar versão e plataforma
  --help             Mostrar esta ajuda

Ambiente: BIND_ADDR, AUTO_UPDATE=apply, GITHUB_TOKEN, ENABLE_I18N=1
",
        _ => "Hypixel SkyBlock API Tracker (local server)

Usage:
  tracker-server [options]

Options:
  --port <port>      listen port (default 8000)
  --bind <addr>      listen address (default 127.0.0.1)
  --check-update     check GitHub for a newer release
  --self-update      download and replace this binary (SHA256 verified, rolls back on failure)
  --crash-list       list recent crash reports
  --version          print version and target platform
  --help             show this help

Environment: BIND_ADDR, AUTO_UPDATE=apply, GITHUB_TOKEN, ENABLE_I18N=1
",
    }
}

/// 按 `LANG` / `LC_ALL` 猜系统语言。
fn detect_lang() -> String {
    let raw = std::env::var("LC_ALL")
        .or_else(|_| std::env::var("LANG"))
        .unwrap_or_default()
        .to_lowercase();
    let lang = raw.split(['_', '.', '-']).next().unwrap_or("").to_string();
    match lang.as_str() {
        "zh" | "ja" | "de" | "es" | "fr" | "ru" | "pt" => lang,
        _ => "en".into(),
    }
}

fn msg(lang: &str, zh: &'static str, en: &'static str) -> &'static str {
    if lang == "zh" {
        zh
    } else {
        en
    }
}

fn main() {
    crash::install_hook("tracker-server");
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() {
        std::process::exit(server_app::run());
    }
    let lang = detect_lang();
    let has = |flag: &str| args.iter().any(|a| a == flag);

    if has("--help") || has("-h") {
        print!("{}", help(&lang));
        return;
    }
    if has("--version") || has("-V") {
        println!(
            "hypixel-tracker {} ({} / {})",
            updater::current_version(),
            updater::target_triple(),
            state::current_build()
        );
        return;
    }
    if has("--check-update") {
        match updater::check() {
            Ok(info) => {
                println!("{}", serde_json::to_string_pretty(&info).unwrap_or_default());
                if info["available"].as_bool().unwrap_or(false) {
                    eprintln!(
                        "{}",
                        msg(
                            &lang,
                            "有新版本可用：运行 tracker-server --self-update 更新",
                            "A newer release is available: run `tracker-server --self-update`",
                        )
                    );
                }
            }
            Err(e) => {
                eprintln!("[更新] {e}");
                std::process::exit(2);
            }
        }
        return;
    }
    if has("--self-update") {
        match updater::self_update() {
            Ok(message) => {
                println!("{message}");
                eprintln!(
                    "{}",
                    msg(&lang, "重启服务后生效", "restart the service to apply")
                );
            }
            Err(e) => {
                crash::record_handled("tracker-server", "自更新失败", &e);
                eprintln!("[更新] {e}");
                std::process::exit(2);
            }
        }
        return;
    }
    if has("--crash-list") {
        let list = crash::recent(10);
        if list.is_empty() {
            println!("{}", msg(&lang, "没有崩溃报告", "no crash reports"));
        }
        for item in list {
            println!(
                "{}  {}  {}  {}",
                item["id"].as_str().unwrap_or("-"),
                item["time"].as_str().unwrap_or("-"),
                item["component"].as_str().unwrap_or("-"),
                item["message"].as_str().unwrap_or("-")
            );
        }
        return;
    }

    // --port / --bind 落到 BIND_ADDR，交给 server_app 统一处理
    let mut bind = std::env::var("BIND_ADDR").unwrap_or_else(|_| "127.0.0.1:8000".into());
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--port" => {
                if let Some(port) = iter.next() {
                    if port.parse::<u16>().is_ok() {
                        let host = bind.split(':').next().unwrap_or("127.0.0.1").to_string();
                        bind = format!("{host}:{port}");
                    } else {
                        eprintln!("[服务] 端口不合法：{port}");
                        std::process::exit(2);
                    }
                }
            }
            "--bind" => {
                if let Some(addr) = iter.next() {
                    bind = addr.clone();
                }
            }
            other if other.starts_with('-') => {
                eprintln!(
                    "{}",
                    msg(&lang, "未知参数，见 --help", "unknown option, see --help")
                );
                std::process::exit(2);
            }
            _ => {}
        }
    }
    std::env::set_var("BIND_ADDR", bind);
    std::process::exit(server_app::run());
}
