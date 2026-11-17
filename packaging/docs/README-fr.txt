Hypixel SkyBlock API Tracker v3.0.0 — Installation (Français)
=============================================================

1. Installer
   Windows : lancez HypixelTracker-Setup-*.exe ou décompressez le zip.
   macOS   : double-cliquez sur le .pkg (installation dans /usr/local) ou montez le .dmg et lancez install.sh.
   Linux   : sudo dpkg -i hypixel-tracker_*_amd64.deb
             ou sudo rpm -i hypixel-tracker-*.x86_64.rpm

2. Lancer
   tracker-server
   Ouvrez http://127.0.0.1:8000 (port modifiable avec --port 8080).

3. Mettre à jour
   tracker-server --check-update     vérifier les nouvelles versions
   tracker-server --self-update      télécharger et remplacer (SHA256 vérifié, retour arrière en cas d'échec)
   Avec AUTO_UPDATE=apply, la mise à jour et le redémarrage sont automatiques au lancement.

4. Emplacement des données
   Windows : %APPDATA%\HypixelTracker
   macOS   : ~/Library/Application Support/HypixelTracker
   Linux   : ~/.local/share/hypixel-tracker
   Modifiable via la variable TRACKER_HOME.

5. En cas de problème
   Les rapports de plantage sont dans data/crash/ ; listez-les avec tracker-server --crash-list.
   Joignez le fichier .txt à votre ticket (il ne contient ni clé ni cookie).
