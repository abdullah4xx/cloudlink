#!/usr/bin/env bash
# Builds cloudlink_<version>_all.deb from linux_app/ (core/ included — the old package was missing it).
# Usage: packaging/build_deb.sh [version]      → dist/cloudlink_<version>_all.deb
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${1:-2.0.0}"
PKG=cloudlink
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
ROOT="$STAGE/${PKG}_${VERSION}_all"

install -d "$ROOT/DEBIAN" "$ROOT/usr/lib/cloudlink" "$ROOT/usr/bin" "$ROOT/usr/share/applications" "$ROOT/usr/share/doc/cloudlink"
cp -r linux_app/core "$ROOT/usr/lib/cloudlink/core"
install -m 0644 linux_app/main_cli.py linux_app/main_gtk.py "$ROOT/usr/lib/cloudlink/"
cp protocol/PROTOCOL.md "$ROOT/usr/share/doc/cloudlink/"
find "$ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$ROOT/usr" -type d -exec chmod 0755 {} +
find "$ROOT/usr" -type f -exec chmod 0644 {} +   # the zip may carry 0600 files

cat > "$ROOT/usr/bin/cloudlink" <<'SH'
#!/bin/sh
exec python3 /usr/lib/cloudlink/main_cli.py "$@"
SH
cat > "$ROOT/usr/bin/cloudlink-gtk" <<'SH'
#!/bin/sh
exec python3 /usr/lib/cloudlink/main_gtk.py "$@"
SH
chmod 0755 "$ROOT/usr/bin/cloudlink" "$ROOT/usr/bin/cloudlink-gtk"
chmod 0755 "$ROOT/DEBIAN"
install -m 0644 packaging/cloudlink.desktop "$ROOT/usr/share/applications/cloudlink.desktop"

SIZE=$(du -sk "$ROOT/usr" | cut -f1)
cat > "$ROOT/DEBIAN/control" <<CTL
Package: $PKG
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-cryptography, python3-gi, gir1.2-gtk-4.0
Installed-Size: $SIZE
Maintainer: CloudLink <noreply@example.invalid>
Description: Share files between your Linux PC and Android phone over Wi-Fi
 Direct, end-to-end encrypted transfers on the local network. No internet,
 no account, no relay server. Devices are paired once by comparing a 6-digit code.
 Provides a GTK4 app (cloudlink-gtk) and a command-line tool (cloudlink).
 Note: allow TCP 47616 and UDP 47615 through the firewall (e.g. "ufw allow 47616/tcp; ufw allow 47615/udp").
CTL

mkdir -p dist
if command -v fakeroot >/dev/null; then
  fakeroot dpkg-deb --build "$ROOT" "dist/${PKG}_${VERSION}_all.deb"
else
  dpkg-deb --root-owner-group --build "$ROOT" "dist/${PKG}_${VERSION}_all.deb"
fi
echo "built dist/${PKG}_${VERSION}_all.deb"
