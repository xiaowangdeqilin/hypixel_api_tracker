Hypixel SkyBlock API Tracker v3.0.0 — Instalação (Português)
============================================================

1. Instalar
   Windows: execute HypixelTracker-Setup-*.exe ou extraia o zip.
   macOS  : dê duplo clique no .pkg (instala em /usr/local) ou monte o .dmg e rode install.sh.
   Linux  : sudo dpkg -i hypixel-tracker_*_amd64.deb
            ou sudo rpm -i hypixel-tracker-*.x86_64.rpm

2. Executar
   tracker-server
   Abra http://127.0.0.1:8000 (use --port 8080 para outra porta).

3. Atualizar
   tracker-server --check-update     procurar versão mais nova
   tracker-server --self-update      baixar e substituir (SHA256 verificado, reverte em caso de falha)
   Com AUTO_UPDATE=apply atualiza e reinicia ao abrir.

4. Onde ficam os dados
   Windows: %APPDATA%\HypixelTracker
   macOS  : ~/Library/Application Support/HypixelTracker
   Linux  : ~/.local/share/hypixel-tracker
   Pode mudar com a variável TRACKER_HOME.

5. Problemas
   Relatórios de falha ficam em data/crash/; liste com tracker-server --crash-list.
   Anexe o .txt ao abrir um issue (não contém chaves nem cookies).
