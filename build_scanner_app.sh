#!/bin/bash
set -e
cd "$(dirname "$0")"

APP_NAME="WiFiScanner.app"
BUNDLE_DIR="$APP_NAME/Contents"
MACOS_DIR="$BUNDLE_DIR/MacOS"
EXEC_NAME="wifi_scanner_app"

echo "Building native WiFi scanner app..."

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
