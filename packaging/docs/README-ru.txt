Hypixel SkyBlock API Tracker v3.0.0 — Установка (Русский)
=========================================================

1. Установка
   Windows: запустите HypixelTracker-Setup-*.exe или распакуйте zip.
   macOS  : откройте .pkg (устанавливается в /usr/local) либо смонтируйте .dmg и запустите install.sh.
   Linux  : sudo dpkg -i hypixel-tracker_*_amd64.deb
            или sudo rpm -i hypixel-tracker-*.x86_64.rpm

2. Запуск
   tracker-server
   Откройте http://127.0.0.1:8000 (порт меняется через --port 8080).

3. Обновление
   tracker-server --check-update     проверить новую версию
   tracker-server --self-update      скачать и заменить (проверка SHA256, откат при ошибке)
   С AUTO_UPDATE=apply обновление и перезапуск выполняются при старте.

4. Где данные
   Windows: %APPDATA%\HypixelTracker
   macOS  : ~/Library/Application Support/HypixelTracker
   Linux  : ~/.local/share/hypixel-tracker
   Можно изменить переменной TRACKER_HOME.

5. Если что-то пошло не так
   Отчёты о сбоях лежат в data/crash/; список — tracker-server --crash-list.
   Приложите .txt к issue (ключей и cookie там нет).
