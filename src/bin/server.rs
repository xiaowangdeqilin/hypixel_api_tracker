//! tracker-server：本地监控服务入口。

fn main() {
    std::process::exit(hypixel_tracker::server_app::run());
}
