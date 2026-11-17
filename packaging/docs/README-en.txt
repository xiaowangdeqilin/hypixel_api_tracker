Hypixel SkyBlock API Tracker v3.0.0 — Installation (English)
============================================================

1. Install
   Windows: run HypixelTracker-Setup-*.exe, or just unzip the portable zip.
   macOS  : double-click the .pkg (installs to /usr/local), or mount the .dmg and run install.sh.
   Linux  : sudo dpkg -i hypixel-tracker_*_amd64.deb
            or sudo rpm -i hypixel-tracker-*.x86_64.rpm

2. Run
   tracker-server
   Open http://127.0.0.1:8000 — use --port 8080 to change the port.

3. Update
   tracker-server --check-update     ask GitHub for a newer release
   tracker-server --self-update      download and replace (SHA256 verified, rolls back on failure)
   Set AUTO_UPDATE=apply to update and restart automatically at launch.

4. Where data lives
   Windows: %APPDATA%\HypixelTracker
   macOS  : ~/Library/Application Support/HypixelTracker
   Linux  : ~/.local/share/hypixel-tracker
   Override with the TRACKER_HOME environment variable.

5. Troubleshooting
   Crash reports are written to data/crash/; list them with tracker-server --crash-list.
   Attach the .txt report when filing an issue (it contains no keys or cookies).
