Hypixel SkyBlock API 変更モニター v3.0.0 — インストール手順（日本語）
==============================================================

1. インストール
   Windows: HypixelTracker-Setup-*.exe を実行。zip を解凍するだけでも使えます。
   macOS  : .pkg をダブルクリック（/usr/local に導入）。.dmg の install.sh でも可。
   Linux  : sudo dpkg -i hypixel-tracker_*_amd64.deb
            または sudo rpm -i hypixel-tracker-*.x86_64.rpm

2. 起動
   tracker-server
   ブラウザで http://127.0.0.1:8000 を開く（--port 8080 で変更可）。

3. 更新
   tracker-server --check-update     新しいリリースを確認
   tracker-server --self-update      ダウンロードして置き換え（SHA256 検証、失敗時は巻き戻し）
   起動時に AUTO_UPDATE=apply を設定すると自動更新して再起動します。

4. データの場所
   Windows: %APPDATA%\HypixelTracker
   macOS  : ~/Library/Application Support/HypixelTracker
   Linux  : ~/.local/share/hypixel-tracker
   環境変数 TRACKER_HOME で変更できます。

5. 困ったとき
   クラッシュレポートは data/crash/ に出力されます。tracker-server --crash-list で一覧表示。
   issue には .txt を添付してください（API キーや Cookie は含まれません）。
