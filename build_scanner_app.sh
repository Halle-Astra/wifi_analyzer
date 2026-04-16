#!/bin/bash
set -e
cd "$(dirname "$0")"

OS="$(uname -s)"

if [ "$OS" = "Darwin" ]; then
    APP_NAME="WiFiScanner.app"
    BUNDLE_DIR="$APP_NAME/Contents"
    MACOS_DIR="$BUNDLE_DIR/MacOS"
    EXEC_NAME="wifi_scanner_app"

    echo "Building native WiFi scanner app (macOS)..."

    rm -rf "$APP_NAME"
    mkdir -p "$MACOS_DIR"

    cp Info.plist "$BUNDLE_DIR/Info.plist"

    swiftc \
        -framework AppKit \
        -framework CoreWLAN \
        -framework CoreLocation \
        -framework Foundation \
        native_wifi_scanner_app.swift \
        -o "$MACOS_DIR/$EXEC_NAME"

    echo "Done! Built $APP_NAME"
    echo ""
    echo "First run (grant Location permission):"
    echo "  open $APP_NAME"
    echo ""
    echo "macOS will ask for Location Services permission."
    echo "Go to System Settings -> Privacy & Security -> Location Services"
    echo "and make sure 'WiFi Scanner' is allowed."
    echo ""
    echo "The app writes scan results to:"
    echo "  ~/.wifi-monitor/native_scan.json"
    echo ""
    echo "wifi_web.py and wifi_monitor.py will auto-detect this file."

elif [ "$OS" = "Linux" ]; then
    echo "Linux detected — no native scanner build needed."
    echo ""
    echo "WiFi data is collected via iw and nmcli (pre-installed on most distros)."
    echo ""
    echo "Required tools:"
    echo "  - iw        (sudo apt install iw)"
    echo "  - nmcli     (part of NetworkManager)"
    echo "  - ping      (usually pre-installed)"
    echo ""
    echo "Optional tools:"
    echo "  - bluetoothctl  (sudo apt install bluez)"
    echo ""

    MISSING=""
    command -v iw >/dev/null 2>&1 || MISSING="$MISSING iw"
    command -v nmcli >/dev/null 2>&1 || MISSING="$MISSING nmcli"
    command -v ping >/dev/null 2>&1 || MISSING="$MISSING ping"

    if [ -n "$MISSING" ]; then
        echo "WARNING: Missing tools:$MISSING"
        echo "Install them before running wifi_monitor.py or wifi_web.py."
    else
        echo "All required tools are available. Ready to run:"
        echo "  python3 wifi_web.py"
        echo "  python3 wifi_monitor.py"
    fi
else
    echo "Unsupported platform: $OS"
    exit 1
fi
