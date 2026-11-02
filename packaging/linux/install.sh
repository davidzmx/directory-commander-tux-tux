#!/bin/sh
# Instala Directory Commander Tux Tux para el usuario actual, sin root. Sirve igual en KDE y GNOME.
# No lo registra como gestor de archivos predeterminado.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
VENV="$DATA/directory-commander-tux-tux/venv"
BIN="$HOME/.local/bin"

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "Se necesita Python 3.12 o posterior")'
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet "$ROOT"

mkdir -p "$BIN" "$DATA/applications" "$DATA/icons/hicolor/scalable/apps"
ln -sf "$VENV/bin/directory-commander-tux-tux" "$BIN/directory-commander-tux-tux"
sed "s|^Exec=directory-commander-tux-tux|Exec=$BIN/directory-commander-tux-tux|" "$ROOT/packaging/linux/directory-commander-tux-tux.desktop" \
    > "$DATA/applications/directory-commander-tux-tux.desktop"
cp "$ROOT/directory_commander_tux_tux/resources/directory-commander-tux-tux.svg" "$DATA/icons/hicolor/scalable/apps/directory-commander-tux-tux.svg"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" || true

echo "Instalado. Ejecuta: $BIN/directory-commander-tux-tux"
echo "Si Qt no arranca en X11 falta libxcb-cursor0 (Debian/Ubuntu) o xcb-util-cursor (Fedora/Arch)."
