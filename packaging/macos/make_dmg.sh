#!/usr/bin/env bash
# 生成 macOS 安装包：.pkg（原生安装器）+ .dmg（便携，内含 install.sh）
#
# 用法：RELEASE_TAG=v3.0.0 bash packaging/macos/make_dmg.sh aarch64-apple-darwin
#
# 布局（与 Rust 的 asset_dir() 约定一致）：
#   /usr/local/bin/tracker-server
#   /usr/local/share/hypixel-tracker/{templates,static,locales}
# 可执行文件在 /usr/local/bin，asset_dir() 会找 ../share/hypixel-tracker。
set -euo pipefail

TARGET="${1:?用法: make_dmg.sh <target-triple>}"
TAG="${RELEASE_TAG:?缺少 RELEASE_TAG}"
VERSION="${TAG#v}"
OUT="packaging/out"
STAGE="packaging/stage"
mkdir -p "$OUT"

# ---------- 1. pkg ----------
ROOT="packaging/pkgroot"
rm -rf "$ROOT"
mkdir -p "$ROOT/usr/local/bin" "$ROOT/usr/local/share/hypixel-tracker"

cp "$STAGE/tracker-server" "$STAGE/tracker-build" "$ROOT/usr/local/bin/"
cp -R "$STAGE/templates" "$STAGE/static" "$STAGE/locales" "$ROOT/usr/local/share/hypixel-tracker/"
cp "$STAGE"/README-*.txt "$ROOT/usr/local/share/hypixel-tracker/" 2>/dev/null || true
chmod 755 "$ROOT/usr/local/bin/"*

mkdir -p packaging/scripts
cat > packaging/scripts/postinstall <<'POST'
#!/bin/bash
# 安装后提示：按系统语言给一句话用法（8 种语言兜底到英文）
case "${LANG:-en}" in
  zh*) MSG='已安装：终端运行 tracker-server，然后打开 http://127.0.0.1:8000' ;;
  ja*) MSG='インストール完了: tracker-server を実行し http://127.0.0.1:8000 を開いてください' ;;
  de*) MSG='Installiert: tracker-server starten und http://127.0.0.1:8000 öffnen' ;;
  es*) MSG='Instalado: ejecuta tracker-server y abre http://127.0.0.1:8000' ;;
  fr*) MSG='Installé : lancez tracker-server puis ouvrez http://127.0.0.1:8000' ;;
  ru*) MSG='Установлено: запустите tracker-server и откройте http://127.0.0.1:8000' ;;
  pt*) MSG='Instalado: execute tracker-server e abra http://127.0.0.1:8000' ;;
  *)   MSG='Installed: run tracker-server, then open http://127.0.0.1:8000' ;;
esac
echo "$MSG"
exit 0
POST
chmod +x packaging/scripts/postinstall

pkgbuild \
  --root "$ROOT" \
  --identifier "com.xiaowangdeqilin.hypixel-tracker" \
  --version "$VERSION" \
  --scripts packaging/scripts \
  --install-location "/" \
  "$OUT/hypixel-tracker-${TAG}-${TARGET}.pkg"

# ---------- 2. dmg（便携） ----------
DMGROOT="packaging/dmgroot"
rm -rf "$DMGROOT"
mkdir -p "$DMGROOT"
cp "$STAGE/tracker-server" "$STAGE/tracker-build" "$DMGROOT/"
cp -R "$STAGE/templates" "$STAGE/static" "$STAGE/locales" "$DMGROOT/"
cp "$STAGE"/README-*.txt "$DMGROOT/" 2>/dev/null || true
chmod 755 "$DMGROOT/"tracker-*

cat > "$DMGROOT/install.sh" <<'INSTALL'
#!/bin/bash
# 把当前目录的内容装到 /usr/local（需要 sudo）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="/usr/local/share/hypixel-tracker"
sudo mkdir -p "$DEST" /usr/local/bin
sudo cp "$HERE/tracker-server" "$HERE/tracker-build" /usr/local/bin/
sudo chmod 755 /usr/local/bin/tracker-server /usr/local/bin/tracker-build
sudo cp -R "$HERE/templates" "$HERE/static" "$HERE/locales" "$DEST/"
sudo cp "$HERE"/README-*.txt "$DEST/" 2>/dev/null || true
echo "installed: /usr/local/bin/tracker-server"
echo "run: tracker-server  ->  http://127.0.0.1:8000"
INSTALL
chmod +x "$DMGROOT/install.sh"

hdiutil create \
  -volname "Hypixel Tracker ${TAG}" \
  -srcfolder "$DMGROOT" \
  -ov -format UDZO \
  "$OUT/hypixel-tracker-${TAG}-${TARGET}.dmg"

ls -lh "$OUT"
