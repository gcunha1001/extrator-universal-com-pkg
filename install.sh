#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BIN_DIR="$HOME/.local/bin"
DATA_DIR="$HOME/.local/share/extrair-universal"
DESKTOP_DIR="$HOME/.local/share/applications"

mkdir -p "$BIN_DIR" "$DATA_DIR" "$DESKTOP_DIR"

cp "$SCRIPT_DIR/app.py" "$SCRIPT_DIR/core.py" "$DATA_DIR/"
cp "$SCRIPT_DIR/extrair-tudo" "$BIN_DIR/extrair-tudo"
chmod +x "$BIN_DIR/extrair-tudo"

sed \
    -e "s|^Exec=extrair-tudo$|Exec=$BIN_DIR/extrair-tudo|" \
    -e "s|^TryExec=extrair-tudo$|TryExec=$BIN_DIR/extrair-tudo|" \
    "$SCRIPT_DIR/extrair-tudo.desktop" > "$DESKTOP_DIR/extrair-tudo.desktop"

update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo "Código instalado em $DATA_DIR"
echo "Comando disponível: $BIN_DIR/extrair-tudo"
echo "Atalho criado: $DESKTOP_DIR/extrair-tudo.desktop"
