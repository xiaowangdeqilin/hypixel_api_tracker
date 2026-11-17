Hypixel SkyBlock API-Änderungsmonitor v3.0.0 — Installation (Deutsch)
====================================================================

1. Installieren
   Windows: HypixelTracker-Setup-*.exe ausführen oder das Zip entpacken.
   macOS  : .pkg doppelklicken (installiert nach /usr/local) oder .dmg mounten und install.sh ausführen.
   Linux  : sudo dpkg -i hypixel-tracker_*_amd64.deb
            oder sudo rpm -i hypixel-tracker-*.x86_64.rpm

2. Starten
   tracker-server
   Im Browser http://127.0.0.1:8000 öffnen (Port mit --port 8080 ändern).

3. Aktualisieren
   tracker-server --check-update     nach einer neueren Version suchen
   tracker-server --self-update      herunterladen und ersetzen (SHA256 geprüft, Rollback bei Fehler)
   Mit AUTO_UPDATE=apply wird beim Start automatisch aktualisiert und neu gestartet.

4. Speicherort der Daten
   Windows: %APPDATA%\HypixelTracker
   macOS  : ~/Library/Application Support/HypixelTracker
   Linux  : ~/.local/share/hypixel-tracker
   Über die Umgebungsvariable TRACKER_HOME änderbar.

5. Probleme
   Absturzberichte liegen in data/crash/; anzeigen mit tracker-server --crash-list.
   Bitte die .txt-Datei an ein Issue anhängen (enthält keine Schlüssel oder Cookies).
