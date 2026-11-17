Hypixel SkyBlock API 变更监控台 v3.0.0 —— 安装说明（简体中文）
=========================================================

1. 安装
   Windows：双击 HypixelTracker-Setup-*.exe 按提示安装；也可直接解压 zip 使用。
   macOS  ：双击 .pkg 安装到 /usr/local；或挂载 .dmg 后运行 install.sh。
   Linux  ：sudo dpkg -i hypixel-tracker_*_amd64.deb
            或 sudo rpm -i hypixel-tracker-*.x86_64.rpm

2. 启动
   终端执行：tracker-server
   浏览器打开：http://127.0.0.1:8000
   换端口：tracker-server --port 8080

3. 更新
   tracker-server --check-update     查看有没有新版本
   tracker-server --self-update      下载并替换（SHA256 校验，失败自动回滚）
   也可启动时设 AUTO_UPDATE=apply 自动更新并重启。

4. 数据放在哪
   Windows：%APPDATA%\HypixelTracker
   macOS  ：~/Library/Application Support/HypixelTracker
   Linux  ：~/.local/share/hypixel-tracker
   想改位置就设环境变量 TRACKER_HOME。

5. 出问题了
   崩溃报告在 data/crash/ 下，用 tracker-server --crash-list 查看最近的。
   反馈时请附上那份 .txt（不含密钥、Cookie 等隐私）。
