#!/usr/bin/env bash
# 生成 Linux 安装包：.deb（dpkg-deb，无额外依赖）+ .rpm（有 rpmbuild 时才构建）
#
# 用法：RELEASE_TAG=v3.0.0 bash packaging/linux/make_packages.sh x86_64-unknown-linux-gnu
#
# 布局（与 Rust 的 asset_dir() 约定一致）：
#   /usr/lib/hypixel-tracker/{tracker-server,tracker-build,templates,static,locales}
#   /usr/bin/tracker-server -> ../lib/hypixel-tracker/tracker-server
set -euo pipefail

TARGET="${1:?用法: make_packages.sh <target-triple>}"
TAG="${RELEASE_TAG:?缺少 RELEASE_TAG}"
VERSION="${TAG#v}"
OUT="packaging/out"
STAGE="packaging/stage"
mkdir -p "$OUT"

case "$TARGET" in
  x86_64-*) DEB_ARCH="amd64"; RPM_ARCH="x86_64" ;;
  aarch64-*) DEB_ARCH="arm64"; RPM_ARCH="aarch64" ;;
  *) echo "不支持的架构：$TARGET"; exit 1 ;;
esac

ROOT="packaging/debroot"
rm -rf "$ROOT"
LIBDIR="$ROOT/usr/lib/hypixel-tracker"
mkdir -p "$LIBDIR" "$ROOT/usr/bin" "$ROOT/DEBIAN" "$ROOT/usr/share/applications" "$ROOT/usr/share/doc/hypixel-tracker"

cp "$STAGE/tracker-server" "$STAGE/tracker-build" "$LIBDIR/"
cp -R "$STAGE/templates" "$STAGE/static" "$STAGE/locales" "$LIBDIR/"
chmod 755 "$LIBDIR/tracker-server" "$LIBDIR/tracker-build"
ln -sf ../lib/hypixel-tracker/tracker-server "$ROOT/usr/bin/tracker-server"
ln -sf ../lib/hypixel-tracker/tracker-build "$ROOT/usr/bin/tracker-build"

for lang in zh en ja de es fr ru pt; do
  [ -f "$STAGE/README-$lang.txt" ] && cp "$STAGE/README-$lang.txt" "$ROOT/usr/share/doc/hypixel-tracker/"
done

# 桌面入口：文件名与注释都做本地化，这是 deb 里真正会显示多语言的地方
cat > "$ROOT/usr/share/applications/hypixel-tracker.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Terminal=true
Exec=tracker-server
Icon=utilities-system-monitor
Categories=Development;Utility;
Name=Hypixel SkyBlock API Tracker
Name[zh_CN]=Hypixel SkyBlock API 变更监控台
Name[zh_TW]=Hypixel SkyBlock API 變更監控台
Name[ja]=Hypixel SkyBlock API 変更モニター
Name[de]=Hypixel SkyBlock API-Änderungsmonitor
Name[es]=Monitor de cambios de Hypixel SkyBlock API
Name[fr]=Moniteur de changements Hypixel SkyBlock API
Name[ru]=Монитор изменений Hypixel SkyBlock API
Name[pt]=Monitor de alterações da Hypixel SkyBlock API
Comment=Track Hypixel SkyBlock API changes locally
Comment[zh_CN]=本地追踪 Hypixel SkyBlock API 变更
Comment[zh_TW]=本機追蹤 Hypixel SkyBlock API 變更
Comment[ja]=Hypixel SkyBlock API の変更をローカルで追跡
Comment[de]=Hypixel SkyBlock API-Änderungen lokal verfolgen
Comment[es]=Sigue los cambios de la API de Hypixel SkyBlock en local
Comment[fr]=Suivre les changements de l'API Hypixel SkyBlock en local
Comment[ru]=Локальное отслеживание изменений Hypixel SkyBlock API
Comment[pt]=Acompanhe as alterações da API do Hypixel SkyBlock localmente
DESKTOP

cat > "$ROOT/DEBIAN/control" <<CONTROL
Package: hypixel-tracker
Version: $VERSION
Section: utils
Priority: optional
Architecture: $DEB_ARCH
Maintainer: xiaowangdeqilin <https://github.com/xiaowangdeqilin/hypixel_api_tracker>
Depends: ca-certificates
Recommends: xdg-utils
Installed-Size: $(du -sk "$ROOT" | cut -f1)
Description: Hypixel SkyBlock API change tracker
 Tracks the public Hypixel SkyBlock API, records what changed between
 snapshots and serves a local dashboard (103 languages, timezone aware).
 .
 Includes tracker-server (HTTP dashboard) and tracker-build (static site).
CONTROL

cat > "$ROOT/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
case "${LANG:-en}" in
  zh*) MSG='已安装：运行 tracker-server，浏览器打开 http://127.0.0.1:8000' ;;
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
POSTINST
chmod 755 "$ROOT/DEBIAN/postinst"

cat > "$ROOT/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
# 卸载时保留用户数据（~/.local/share/hypixel-tracker），只清理系统文件
exit 0
POSTRM
chmod 755 "$ROOT/DEBIAN/postrm"

dpkg-deb --build --root-owner-group "$ROOT" "$OUT/hypixel-tracker_${VERSION}_${DEB_ARCH}.deb"

# ---------- rpm（可选） ----------
if command -v rpmbuild >/dev/null 2>&1; then
  TOP="packaging/rpmbuild"
  rm -rf "$TOP"
  mkdir -p "$TOP"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
  tar -C "$ROOT" -czf "$TOP/SOURCES/hypixel-tracker-${VERSION}.tar.gz" usr
  cat > "$TOP/SPECS/hypixel-tracker.spec" <<SPEC
Name:           hypixel-tracker
Version:        $VERSION
Release:        1
Summary:        Hypixel SkyBlock API change tracker
License:        MIT
BuildArch:      $RPM_ARCH
Requires:       ca-certificates

%description
Tracks the public Hypixel SkyBlock API and serves a local dashboard.

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}
tar -C %{buildroot} -xzf %{_sourcedir}/hypixel-tracker-${VERSION}.tar.gz

%files
/usr/lib/hypixel-tracker
/usr/bin/tracker-server
/usr/bin/tracker-build
/usr/share/applications/hypixel-tracker.desktop
/usr/share/doc/hypixel-tracker

%changelog
* $(date +"%a %b %d %Y") xiaowangdeqilin - $VERSION-1
- Release $TAG
SPEC
  rpmbuild --define "_topdir $(pwd)/$TOP" -bb "$TOP/SPECS/hypixel-tracker.spec"
  find "$TOP/RPMS" -name '*.rpm' -exec cp {} "$OUT/" \;
else
  echo "未安装 rpmbuild，跳过 rpm（deb 已生成）"
fi

ls -lh "$OUT"
