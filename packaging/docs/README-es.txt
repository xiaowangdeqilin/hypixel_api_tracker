Hypixel SkyBlock API Tracker v3.0.0 — Instalación (Español)
===========================================================

1. Instalar
   Windows: ejecuta HypixelTracker-Setup-*.exe o descomprime el zip.
   macOS  : doble clic en el .pkg (instala en /usr/local) o monta el .dmg y ejecuta install.sh.
   Linux  : sudo dpkg -i hypixel-tracker_*_amd64.deb
            o sudo rpm -i hypixel-tracker-*.x86_64.rpm

2. Ejecutar
   tracker-server
   Abre http://127.0.0.1:8000 (cambia el puerto con --port 8080).

3. Actualizar
   tracker-server --check-update     buscar una versión nueva
   tracker-server --self-update      descargar y sustituir (SHA256 verificado, revierte si falla)
   Con AUTO_UPDATE=apply se actualiza y reinicia al arrancar.

4. Dónde están los datos
   Windows: %APPDATA%\HypixelTracker
   macOS  : ~/Library/Application Support/HypixelTracker
   Linux  : ~/.local/share/hypixel-tracker
   Se puede cambiar con la variable TRACKER_HOME.

5. Problemas
   Los informes de fallo se guardan en data/crash/; lístalos con tracker-server --crash-list.
   Adjunta el .txt al abrir una incidencia (no contiene claves ni cookies).
