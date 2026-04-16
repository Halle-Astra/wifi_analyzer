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
    echo "WiFi data is collected via iwlist and iw (pre-installed on most distros)."
    echo ""
    echo "Required tools:"
    echo "  - iwlist    (sudo apt install wireless-tools)"
    echo "  - iw        (sudo apt install iw)"
    echo "  - ping      (usually pre-installed)"
    echo ""
    echo "Optional tools:"
    echo "  - bluetoothctl  (sudo apt install bluez)"
    echo ""

    MISSING=""
    command -v iwlist >/dev/null 2>&1 || MISSING="$MISSING iwlist"
    command -v iw >/dev/null 2>&1 || MISSING="$MISSING iw"
    command -v ping >/dev/null 2>&1 || MISSING="$MISSING ping"

    if [ -n "$MISSING" ]; then
        echo "WARNING: Missing tools:$MISSING"
        echo "Install them before running wifi_monitor.py or wifi_web.py."
    else
        echo "All required tools are available."
    fi

    echo ""
    echo "=================================================="
    echo "  WiFi 扫描和蓝牙发现需要 root 权限。"
    echo "  推荐配置免密 sudo："
    echo "=================================================="
    echo ""
    IWLIST_PATH="$(command -v iwlist 2>/dev/null || echo '/usr/sbin/iwlist')"
    BTCTL_PATH="$(command -v bluetoothctl 2>/dev/null || echo '/usr/bin/bluetoothctl')"
    echo "手动配置（sudoers 免密）："
    echo "  sudo bash -c 'echo \"$(whoami) ALL=(root) NOPASSWD: $IWLIST_PATH, $BTCTL_PATH\" > /etc/sudoers.d/wifi-scan'"
    echo ""
    echo "  - iwlist：发现附近所有 WiFi 网络（含 2.4G/5G 频谱图）"
    echo "  - bluetoothctl：发现附近 BLE 低功耗蓝牙设备"
    echo ""
    echo "如不配置，程序仍可运行，但频谱图只显示已连接 WiFi，蓝牙列表为空。"
    echo ""

    read -rp "是否现在自动配置 sudoers 免密？[y/N] " REPLY
    if [[ "$REPLY" =~ ^[Yy]$ ]]; then
        SUDOERS_LINE="$(whoami) ALL=(root) NOPASSWD: $IWLIST_PATH, $BTCTL_PATH"
        sudo bash -c "echo '$SUDOERS_LINE' > /etc/sudoers.d/wifi-scan && chmod 440 /etc/sudoers.d/wifi-scan"
        if [ $? -eq 0 ]; then
            echo "Done! 已配置免密 sudo iwlist + bluetoothctl。"
        else
            echo "配置失败，请手动执行上述命令。"
        fi
    else
        echo "跳过。你可以稍后手动配置。"
    fi

    echo ""
    echo "Ready to run:"
    echo "  python3 wifi_web.py"
    echo "  python3 wifi_monitor.py"
else
    echo "Unsupported platform: $OS"
    exit 1
fi
