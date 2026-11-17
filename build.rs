//! 构建脚本：把目标三元组注入二进制，供自更新挑选对应平台的 release 资产。
fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    if let Ok(target) = std::env::var("TARGET") {
        println!("cargo:rustc-env=BUILD_TARGET={target}");
    }
}
