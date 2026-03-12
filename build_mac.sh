#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  Pulse Terminal — Mac Release Builder
#  Run from the pulse/ project root:
#      chmod +x build_mac.sh && ./build_mac.sh
# ─────────────────────────────────────────────────────────────

set -e
cd "$(dirname "$0")"

# Exact Python with PySide6 + paramiko + pyinstaller
PYTHON=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3

APP_NAME="Pulse Terminal"
VERSION="1.0.0"
BUNDLE_ID="com.mointhedev.pulse"
DMG_NAME="Pulse-Terminal-${VERSION}.dmg"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Pulse Terminal — Mac Build"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "→ Using: $PYTHON ($("$PYTHON" --version))"

# ── 1. Verify dependencies ────────────────────────────────────
for pkg in PySide6 paramiko PyInstaller; do
    if ! "$PYTHON" -c "import $pkg" 2>/dev/null; then
        echo "ERROR: $pkg not found. Run: $PYTHON -m pip install $pkg"
        exit 1
    fi
done
echo "→ Dependencies OK"

# ── 2. Clean previous build ──────────────────────────────────
echo "→ Cleaning previous build..."
rm -rf build dist __pycache__ *.spec 2>/dev/null || true

# ── 3. Convert icon ───────────────────────────────────────────
ICON_FLAG=""
if [ -f "assets/icon.png" ]; then
    echo "→ Converting icon to .icns..."
    mkdir -p assets/icon.iconset
    sips -z 16   16   assets/icon.png --out assets/icon.iconset/icon_16x16.png      > /dev/null
    sips -z 32   32   assets/icon.png --out assets/icon.iconset/icon_16x16@2x.png   > /dev/null
    sips -z 32   32   assets/icon.png --out assets/icon.iconset/icon_32x32.png      > /dev/null
    sips -z 64   64   assets/icon.png --out assets/icon.iconset/icon_32x32@2x.png   > /dev/null
    sips -z 128  128  assets/icon.png --out assets/icon.iconset/icon_128x128.png    > /dev/null
    sips -z 256  256  assets/icon.png --out assets/icon.iconset/icon_128x128@2x.png > /dev/null
    sips -z 256  256  assets/icon.png --out assets/icon.iconset/icon_256x256.png    > /dev/null
    sips -z 512  512  assets/icon.png --out assets/icon.iconset/icon_256x256@2x.png > /dev/null
    sips -z 512  512  assets/icon.png --out assets/icon.iconset/icon_512x512.png    > /dev/null
    sips -z 1024 1024 assets/icon.png --out assets/icon.iconset/icon_512x512@2x.png > /dev/null
    iconutil -c icns assets/icon.iconset -o assets/icon.icns
    rm -rf assets/icon.iconset
    ICON_FLAG="--icon=assets/icon.icns"
    echo "   icon.icns created."
else
    echo "   No assets/icon.png — skipping icon. Add 1024x1024 PNG to include one."
fi

# ── 4. Build .app ─────────────────────────────────────────────
echo "→ Building .app bundle..."
"$PYTHON" -m PyInstaller \
    --name "$APP_NAME" \
    --windowed \
    --onedir \
    --noconfirm \
    --clean \
    $ICON_FLAG \
    --osx-bundle-identifier "$BUNDLE_ID" \
    --add-data "assets:assets" \
    --paths src \
    src/main.py
echo "   Built: dist/${APP_NAME}.app"

# ── 5. Package .dmg ───────────────────────────────────────────
echo "→ Creating .dmg..."
if command -v create-dmg &>/dev/null; then
    ICON_VOL_FLAG=""
    [ -f "assets/icon.icns" ] && ICON_VOL_FLAG="--volicon assets/icon.icns"
    create-dmg \
        --volname "$APP_NAME" \
        $ICON_VOL_FLAG \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "$APP_NAME.app" 175 190 \
        --hide-extension "$APP_NAME.app" \
        --app-drop-link 425 190 \
        "$DMG_NAME" \
        "dist/$APP_NAME.app"
else
    echo "   create-dmg not found — using hdiutil. (brew install create-dmg for prettier DMG)"
    STAGING="dist/dmg_staging"
    mkdir -p "$STAGING"
    cp -r "dist/${APP_NAME}.app" "$STAGING/"
    ln -sf /Applications "$STAGING/Applications"
    hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING" -ov -format UDZO "$DMG_NAME"
    rm -rf "$STAGING"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Done! → ${DMG_NAME}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
