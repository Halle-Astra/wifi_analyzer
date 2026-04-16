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
    echo "  WiFi 扫描需要 root 权限才能发现附近所有网络。"
    echo "  推荐配置免密 sudo（二选一）："
    echo "=================================================="
    echo ""
    echo "方式一：sudoers 免密（推荐）"
    IWLIST_PATH="$(command -v iwlist 2>/dev/null || echo '/usr/sbin/iwlist')"
    echo "  sudo bash -c 'echo \"$(whoami) ALL=(root) NOPASSWD: $IWLIST_PATH\" > /etc/sudoers.d/wifi-scan'"
    echo ""
    echo "方式二：给 iwlist 添加 cap_net_admin capability"
    echo "  sudo setcap cap_net_admin+ep $IWLIST_PATH"
    echo ""
    echo "配置后即可免密扫描，频谱图将显示所有 2.4G/5G 网络。"
    echo ""
    echo "如不配置，程序仍可运行，但频谱图只显示当前连接的 WiFi。"
    echo ""

    read -rp "是否现在自动配置 sudoers 免密？[y/N] " REPLY
    if [[ "$REPLY" =~ ^[Yy]$ ]]; then
        IWLIST_PATH="$(command -v iwlist 2>/dev/null || echo '/usr/sbin/iwlist')"
        SUDOERS_LINE="$(whoami) ALL=(root) NOPASSWD: $IWLIST_PATH"
        sudo bash -c "echo '$SUDOERS_LINE' > /etc/sudoers.d/wifi-scan && chmod 440 /etc/sudoers.d/wifi-scan"
        if [ $? -eq 0 ]; then
            echo "Done! 已配置免密 sudo iwlist scan。"
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
