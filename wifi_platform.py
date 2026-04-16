#!/usr/bin/env python3
"""
Platform abstraction for WiFi data collection.

Detects the current OS and provides unified interfaces for:
  - WiFi interface data (signal, noise, channel, neighbors, etc.)
  - Bluetooth device scanning
  - Ping command differences

Supported platforms: macOS (Darwin), Linux
"""

import json
import os
import platform
import re
import subprocess
import sys

PLATFORM = platform.system()  # "Darwin" or "Linux"


def get_wifi_interface_name():
    """Return the primary WiFi interface name."""
    if PLATFORM == "Darwin":
        return "en0"
    try:
        result = subprocess.run(
            ["iw", "dev"], capture_output=True, text=True, timeout=5,
        )
        match = re.search(r"Interface\s+(\S+)", result.stdout)
        if match:
            return match.group(1)
    except Exception:
        pass
    for candidate in ("wlan0", "wlp2s0", "wlp3s0"):
        if os.path.isdir(f"/sys/class/net/{candidate}/wireless"):
            return candidate
    return "wlan0"


WIFI_IFACE = None


def _iface():
    global WIFI_IFACE
    if WIFI_IFACE is None:
        WIFI_IFACE = get_wifi_interface_name()
    return WIFI_IFACE


def _freq_to_channel(freq_mhz):
    """Convert frequency in MHz to WiFi channel number."""
    freq = int(freq_mhz)
    if 2412 <= freq <= 2484:
        if freq == 2484:
            return 14
        return (freq - 2407) // 5
    if 5170 <= freq <= 5825:
        return (freq - 5000) // 5
    if 5955 <= freq <= 7115:
        return (freq - 5950) // 5
    return None


def _freq_to_band(freq_mhz):
    freq = int(freq_mhz)
    if freq < 3000:
        return "2.4GHz"
    if freq < 6000:
        return "5GHz"
    return "6GHz"


def _channel_width_str(width_mhz):
    """Normalize channel width to string like '20MHz'."""
    if width_mhz is None:
        return None
    w = int(width_mhz)
    return f"{w}MHz"


# ---------------------------------------------------------------------------
# macOS implementation
# ---------------------------------------------------------------------------

def _macos_get_wifi_data():
    """Collect WiFi info via system_profiler JSON output (macOS)."""
    try:
        result = subprocess.run(
            ["system_profiler", "SPAirPortDataType", "-json"],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout)
        iface = data["SPAirPortDataType"][0]["spairport_airport_interfaces"][0]
        return iface
    except Exception as exc:
        return {"_error": str(exc)}


def _macos_parse_signal_noise(raw):
    match = re.findall(r"(-?\d+)\s*dBm", raw or "")
    if len(match) >= 2:
        return int(match[0]), int(match[1])
    return None, None


def _macos_parse_channel_info(raw):
    match = re.match(r"(\d+)\s*\((\S+?),\s*(\S+?)\)", raw or "")
    if match:
        return int(match.group(1)), match.group(2), match.group(3)
    return None, None, None


def _macos_count_neighbors(iface):
    networks = iface.get("spairport_airport_other_local_wireless_networks", [])
    channel_counts = {}
    channel_networks = {}
    for net in networks:
        ch_raw = net.get("spairport_network_channel", "")
        ch, band, width = _macos_parse_channel_info(ch_raw)
        if ch is not None:
            channel_counts[ch] = channel_counts.get(ch, 0) + 1
            if ch not in channel_networks:
                channel_networks[ch] = []
            channel_networks[ch].append({
                "ssid": net.get("_name", ""),
                "channel_raw": ch_raw,
                "band": band,
                "width": width,
                "phy_mode": net.get("spairport_network_phymode", ""),
                "security": net.get("spairport_security_mode", ""),
            })
    return len(networks), channel_counts, channel_networks


def _macos_scan_bluetooth():
    try:
        result = subprocess.run(
            ["system_profiler", "SPBluetoothDataType", "-json"],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(result.stdout)
        bt = data.get("SPBluetoothDataType", [{}])[0]
        devices = []
        for category_key in ["device_connected", "device_not_connected"]:
            category = bt.get(category_key, [])
            if isinstance(category, list):
                for item in category:
                    if isinstance(item, dict):
                        for name, info in item.items():
                            devices.append({
                                "name": name,
                                "connected": category_key == "device_connected",
                                "address": info.get("device_address", ""),
                                "type": info.get("device_minorType", ""),
                                "rssi": info.get("device_rssi", ""),
                            })
        return devices
    except Exception:
        return []


def macos_collect_wifi_info():
    """Return a dict with all WiFi connection info for macOS.

    Keys: connected, ssid, channel, band, width, phy_mode,
          signal_dbm, noise_dbm, snr_db, tx_rate_mbps, mcs_index,
          security, neighbor_count, channel_distribution, channel_networks
    Also returns the raw iface object under '_iface' for RF merge compatibility.
    """
    iface = _macos_get_wifi_data()
    if "_error" in iface:
        return {"_error": iface["_error"]}

    status_raw = iface.get("spairport_status_information", "")
    connected = "connected" in status_raw.lower()

    current = iface.get("spairport_current_network_information", {})
    ssid = current.get("_name")
    channel_raw = current.get("spairport_network_channel")
    channel, band, width = _macos_parse_channel_info(channel_raw)
    phy = current.get("spairport_network_phymode")
    signal, noise = _macos_parse_signal_noise(current.get("spairport_signal_noise"))
    snr = (signal - noise) if (signal is not None and noise is not None) else None
    tx_rate = current.get("spairport_network_rate")
    mcs = current.get("spairport_network_mcs")
    security = current.get("spairport_security_mode")

    neighbor_count, channel_dist, channel_networks = _macos_count_neighbors(iface)

    return {
        "connected": connected,
        "ssid": ssid,
        "channel": channel,
        "band": band,
        "width": width,
        "phy_mode": phy,
        "signal_dbm": signal,
        "noise_dbm": noise,
        "snr_db": snr,
        "tx_rate_mbps": tx_rate,
        "mcs_index": mcs,
        "security": security,
        "neighbor_count": neighbor_count,
        "channel_distribution": channel_dist,
        "channel_networks": channel_networks,
        "_iface": iface,
    }


# ---------------------------------------------------------------------------
# Linux implementation
# ---------------------------------------------------------------------------

def _linux_get_link_info():
    """Parse `iw dev <iface> link` for current connection info."""
    try:
        result = subprocess.run(
            ["iw", "dev", _iface(), "link"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout
    except Exception:
        return ""


def _linux_get_iface_info():
    """Parse `iw dev <iface> info` for channel/width."""
    try:
        result = subprocess.run(
            ["iw", "dev", _iface(), "info"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout
    except Exception:
        return ""


def _linux_get_station_info():
    """Parse `iw dev <iface> station dump` for noise and detailed stats."""
    try:
        result = subprocess.run(
            ["iw", "dev", _iface(), "station", "dump"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout
    except Exception:
        return ""


def _linux_get_survey_info():
    """Parse `iw dev <iface> survey dump` for noise floor."""
    try:
        result = subprocess.run(
            ["iw", "dev", _iface(), "survey", "dump"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout
    except Exception:
        return ""


def _decode_iwlist_essid(raw):
    """Decode iwlist ESSID string containing \\xNN escape sequences.

    iwlist outputs non-ASCII bytes as \\xNN. For example, the Chinese SSID
    '汤圆' appears as '\\xE6\\xB1\\xA4\\xE5\\x9C\\x86'. This function
    converts consecutive \\xNN sequences back to proper UTF-8 text.
    """
    parts = re.split(r'((?:\\x[0-9a-fA-F]{2})+)', raw)
    result = []
    for part in parts:
        if part.startswith("\\x"):
            hex_bytes = re.findall(r'\\x([0-9a-fA-F]{2})', part)
            decoded = bytes(int(h, 16) for h in hex_bytes).decode('utf-8', errors='replace')
            result.append(decoded)
        else:
            result.append(part)
    return "".join(result)


def _parse_iwlist_output(output):
    """Parse iwlist scan output into a list of network dicts."""
    networks = []
    current = None

    for line in output.splitlines():
        line = line.strip()

        cell_match = re.match(r"Cell\s+\d+\s+-\s+Address:\s*(\S+)", line)
        if cell_match:
            if current is not None:
                networks.append(current)
            current = {
                "bssid": cell_match.group(1),
                "ssid": "",
                "channel": 0,
                "freq": 0,
                "rssi": -100,
                "security": "",
                "band": "",
                "width": "20MHz",
                "mode": "",
            }
            continue

        if current is None:
            continue

        if line.startswith("ESSID:"):
            essid_match = re.search(r'ESSID:"(.*)"', line)
            if essid_match:
                raw_ssid = essid_match.group(1)
                current["ssid"] = _decode_iwlist_essid(raw_ssid)

        elif line.startswith("Channel:"):
            ch_match = re.search(r"Channel:\s*(\d+)", line)
            if ch_match:
                current["channel"] = int(ch_match.group(1))

        elif line.startswith("Frequency:"):
            freq_match = re.search(r"Frequency:\s*([\d.]+)\s*GHz", line)
            if freq_match:
                freq_ghz = float(freq_match.group(1))
                current["freq"] = int(freq_ghz * 1000)
                current["band"] = _freq_to_band(current["freq"])
            ch_in_freq = re.search(r"\(Channel\s+(\d+)\)", line)
            if ch_in_freq and current["channel"] == 0:
                current["channel"] = int(ch_in_freq.group(1))

        elif "Signal level" in line:
            sig_match = re.search(r"Signal level[=:]\s*(-?\d+)\s*dBm", line)
            if sig_match:
                current["rssi"] = int(sig_match.group(1))
            else:
                qual_match = re.search(r"Quality[=:](\d+)/(\d+)", line)
                if qual_match:
                    quality = int(qual_match.group(1))
                    max_qual = int(qual_match.group(2))
                    current["rssi"] = int((quality / max_qual) * 70 - 110)

        elif line.startswith("Mode:"):
            mode_match = re.search(r"Mode:\s*(\S+)", line)
            if mode_match:
                current["mode"] = mode_match.group(1)

        elif "Encryption key:on" in line:
            current["security"] = "encrypted"

        elif "WPA2" in line or "IEEE 802.11i/WPA2" in line:
            current["security"] = "WPA2"

        elif "WPA " in line and "WPA2" not in current.get("security", ""):
            current["security"] = "WPA"

    if current is not None:
        networks.append(current)

    return networks


_iwlist_needs_sudo = None  # None = not tested yet, True/False = tested


def _linux_iwlist_scan():
    """Scan WiFi networks via iwlist.

    Without root, iwlist only reads kernel cache (usually just the connected AP).
    With root (sudo or cap_net_admin), it triggers a real scan and discovers all
    nearby networks on both 2.4GHz and 5GHz bands.

    Strategy:
      1. First call: try plain iwlist; if only <=1 result, retry with sudo.
      2. Remember whether sudo was needed so subsequent calls go straight to it.
      3. If sudo fails (no permission / password required), fall back to cache.
    """
    global _iwlist_needs_sudo
    iface = _iface()

    def _run_iwlist(use_sudo=False):
        cmd = ["sudo", "iwlist", iface, "scan"] if use_sudo else ["iwlist", iface, "scan"]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0 and use_sudo:
                return None
            return _parse_iwlist_output(result.stdout)
        except Exception:
            return None

    if _iwlist_needs_sudo is True:
        nets = _run_iwlist(use_sudo=True)
        if nets is not None:
            return nets
        return _run_iwlist(use_sudo=False) or []

    if _iwlist_needs_sudo is False:
        return _run_iwlist(use_sudo=False) or []

    plain = _run_iwlist(use_sudo=False) or []
    if len(plain) > 1:
        _iwlist_needs_sudo = False
        return plain

    sudo_nets = _run_iwlist(use_sudo=True)
    if sudo_nets is not None and len(sudo_nets) > len(plain):
        _iwlist_needs_sudo = True
        return sudo_nets

    _iwlist_needs_sudo = False
    return plain


LINUX_SCANNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wifi_scanner")

BW_NAMES_LINUX = {0: "20MHz", 1: "20MHz", 2: "40MHz", 3: "80MHz", 4: "160MHz"}
BAND_NAMES_LINUX = {0: "unknown", 1: "2.4GHz", 2: "5GHz", 3: "6GHz"}


def _linux_native_scan():
    """Run the compiled nl80211 scanner binary if available.

    Returns a list of network dicts compatible with the channel_networks format,
    or None if the scanner is not available.
    """
    if not os.path.isfile(LINUX_SCANNER_PATH):
        return None
    try:
        use_sudo = os.geteuid() != 0
        cmd = ["sudo", LINUX_SCANNER_PATH] if use_sudo else [LINUX_SCANNER_PATH]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            cmd = [LINUX_SCANNER_PATH, "-n"]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5,
            )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        data = json.loads(result.stdout)
        networks = []
        for net in data.get("networks", []):
            bw_raw = net.get("channelWidth", 0)
            band_raw = net.get("channelBand", 0)
            networks.append({
                "bssid": net.get("bssid", ""),
                "ssid": net.get("ssid", ""),
                "channel": net.get("channel", 0),
                "freq": 0,
                "rssi": net.get("rssi", -100),
                "noise": net.get("noise", 0),
                "security": "",
                "band": BAND_NAMES_LINUX.get(band_raw, ""),
                "width": BW_NAMES_LINUX.get(bw_raw, "20MHz"),
                "hidden": net.get("hidden", False),
                "beacon_interval": net.get("beaconInterval", 0),
            })
        return networks
    except Exception:
        return None


def _linux_scan_neighbors():
    """Scan neighbor networks. Prefer native nl80211 scanner, fall back to iwlist."""
    native = _linux_native_scan()
    if native is not None and len(native) > 0:
        return native
    return _linux_iwlist_scan()


_bt_sudo_tested = None


def _bt_needs_sudo():
    """Check if sudo is available for bluetoothctl (passwordless)."""
    global _bt_sudo_tested
    if _bt_sudo_tested is not None:
        return _bt_sudo_tested

    try:
        result = subprocess.run(
            ["sudo", "-n", "bluetoothctl", "show"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            _bt_sudo_tested = True
            return True
    except Exception:
        pass

    _bt_sudo_tested = False
    return False


def _linux_scan_bluetooth():
    """Scan Bluetooth devices on Linux via bluetoothctl.

    Uses `bluetoothctl --timeout <N> scan on` to discover nearby BLE devices.
    Parses the stream output for device addresses, names, RSSI, and
    manufacturer data. Needs sudo for actual BLE discovery on most systems.
    """
    devices_info = {}
    scan_duration = 4

    use_sudo = _bt_needs_sudo()
    cmd_prefix = ["sudo"] if use_sudo else []

    try:
        result = subprocess.run(
            cmd_prefix + ["timeout", str(scan_duration + 2),
                          "bluetoothctl", "--timeout", str(scan_duration),
                          "scan", "on"],
            capture_output=True, text=True, timeout=scan_duration + 5,
        )
        scan_output = (result.stdout or "") + (result.stderr or "")
    except Exception:
        scan_output = ""

    ansi_re = re.compile(r'\x01?\x1b\[[^m]*m\x02?')
    for line in scan_output.splitlines():
        clean = ansi_re.sub('', line).strip()

        new_match = re.match(
            r'\[NEW\]\s+Device\s+(\S+)\s+(.*)', clean)
        if new_match:
            addr = new_match.group(1)
            name = new_match.group(2).strip()
            if addr not in devices_info:
                devices_info[addr] = {"name": name, "rssi": None, "mfr": None}
            continue

        rssi_match = re.match(
            r'\[CHG\]\s+Device\s+(\S+)\s+RSSI:\s*(-?\d+)', clean)
        if rssi_match:
            addr = rssi_match.group(1)
            rssi_val = int(rssi_match.group(2))
            if addr not in devices_info:
                devices_info[addr] = {"name": "", "rssi": rssi_val, "mfr": None}
            else:
                devices_info[addr]["rssi"] = rssi_val
            continue

        mfr_match = re.match(
            r'\[CHG\]\s+Device\s+(\S+)\s+ManufacturerData Key:\s*(0x[0-9a-fA-F]+)',
            clean)
        if mfr_match:
            addr = mfr_match.group(1)
            mfr_key = mfr_match.group(2).lower()
            if addr in devices_info:
                devices_info[addr]["mfr"] = mfr_key

        name_match = re.match(
            r'\[CHG\]\s+Device\s+(\S+)\s+Name:\s*(.*)', clean)
        if name_match:
            addr = name_match.group(1)
            name = name_match.group(2).strip()
            if addr in devices_info and name:
                devices_info[addr]["name"] = name

    devices = []
    for addr, info in devices_info.items():
        name = info["name"]
        is_anon = (not name) or (name == addr.replace(":", "-"))

        mfr = info.get("mfr") or ""
        dev_type = ""
        if mfr == "0x004c":
            dev_type = "apple-device"
        elif mfr == "0x0006":
            dev_type = "microsoft-device"
        elif mfr == "0x00e0":
            dev_type = "google-device"

        if is_anon:
            display_name = f"(BLE) {addr}"
            if dev_type:
                display_name = f"({dev_type}) {addr}"
        else:
            display_name = name

        devices.append({
            "name": display_name,
            "connected": False,
            "address": addr,
            "type": dev_type,
            "rssi": info["rssi"] if info["rssi"] is not None else "",
        })

    devices.sort(key=lambda d: d.get("rssi") or -999, reverse=True)
    return devices


def _linux_get_noise_floor():
    """Extract noise floor from survey dump for the active channel."""
    survey = _linux_get_survey_info()
    noise = None
    in_use = False
    for line in survey.splitlines():
        line = line.strip()
        if line.startswith("Survey data from"):
            in_use = False
        if "[in use]" in line:
            in_use = True
        if in_use and "noise:" in line.lower():
            match = re.search(r"(-?\d+)\s*dBm", line)
            if match:
                noise = int(match.group(1))
    return noise


def linux_collect_wifi_info():
    """Return a dict with all WiFi connection info for Linux."""
    link_out = _linux_get_link_info()
    info_out = _linux_get_iface_info()

    connected = "Connected to" in link_out

    ssid = None
    signal = None
    tx_rate = None
    bssid = None
    phy = None

    ssid_match = re.search(r"SSID:\s*(.+)", link_out)
    if ssid_match:
        ssid = ssid_match.group(1).strip()

    sig_match = re.search(r"signal:\s*(-?\d+)\s*dBm", link_out)
    if sig_match:
        signal = int(sig_match.group(1))

    tx_match = re.search(r"tx bitrate:\s*([\d.]+)\s*MBit/s\s*(.*)", link_out)
    if tx_match:
        tx_rate = float(tx_match.group(1))
        phy_tail = tx_match.group(2)
        if "HE-" in phy_tail:
            phy = "802.11ax (Wi-Fi 6)"
        elif "VHT" in phy_tail:
            phy = "802.11ac (Wi-Fi 5)"
        elif "HT" in phy_tail:
            phy = "802.11n (Wi-Fi 4)"

    bssid_match = re.search(r"Connected to\s+(\S+)", link_out)
    if bssid_match:
        bssid = bssid_match.group(1)

    channel = None
    band = None
    width = None
    ch_match = re.search(r"channel\s+(\d+)\s+\((\d+)\s*MHz\),\s*width:\s*(\d+)\s*MHz", info_out)
    if ch_match:
        channel = int(ch_match.group(1))
        freq = int(ch_match.group(2))
        width = f"{ch_match.group(3)}MHz"
        band = _freq_to_band(freq)

    noise = _linux_get_noise_floor()
    snr = (signal - noise) if (signal is not None and noise is not None) else None

    mcs = None
    mcs_match = re.search(r"(?:HE-MCS|MCS)\s+(\d+)", link_out)
    if mcs_match:
        mcs = int(mcs_match.group(1))

    security = None
    scanned_nets = _linux_scan_neighbors()

    channel_networks = {}
    channel_counts = {}
    for net in scanned_nets:
        if net["ssid"] == ssid and net.get("bssid", "").lower() == (bssid or "").lower():
            security = net.get("security")
        ch = net["channel"]
        if ch:
            channel_counts[ch] = channel_counts.get(ch, 0) + 1
            if ch not in channel_networks:
                channel_networks[ch] = []
            channel_networks[ch].append({
                "ssid": net["ssid"],
                "bssid": net.get("bssid", ""),
                "channel_raw": f"{ch} ({net['freq']} MHz)" if net.get("freq") else str(ch),
                "band": net.get("band", ""),
                "width": net.get("width", ""),
                "phy_mode": "",
                "security": net.get("security", ""),
                "rssi": net["rssi"],
                "anonymous": not bool(net["ssid"]),
            })

    neighbor_count = sum(len(v) for v in channel_networks.values())

    return {
        "connected": connected,
        "ssid": ssid,
        "channel": channel,
        "band": band,
        "width": width,
        "phy_mode": phy,
        "signal_dbm": signal,
        "noise_dbm": noise,
        "snr_db": snr,
        "tx_rate_mbps": tx_rate,
        "mcs_index": mcs,
        "security": security,
        "neighbor_count": neighbor_count,
        "channel_distribution": channel_counts,
        "channel_networks": channel_networks,
        "_iface": None,
    }


# ---------------------------------------------------------------------------
# Unified public API
# ---------------------------------------------------------------------------

def collect_wifi_info():
    """Collect WiFi info for the current platform."""
    if PLATFORM == "Darwin":
        return macos_collect_wifi_info()
    elif PLATFORM == "Linux":
        return linux_collect_wifi_info()
    else:
        return {"_error": f"Unsupported platform: {PLATFORM}"}


def scan_bluetooth():
    """Scan Bluetooth devices for the current platform."""
    if PLATFORM == "Darwin":
        return _macos_scan_bluetooth()
    elif PLATFORM == "Linux":
        return _linux_scan_bluetooth()
    return []


def ping_command_args(target, timeout_seconds):
    """Return the correct ping command args for the current platform."""
    if PLATFORM == "Darwin":
        return ["ping", "-c", "1", "-W", str(timeout_seconds * 1000), target]
    else:
        return ["ping", "-c", "1", "-W", str(timeout_seconds), target]


def supports_rf_scan():
    """Whether native RF scanning (Swift CoreWLAN) is available."""
    return PLATFORM == "Darwin"


def supports_native_app():
    """Whether the native WiFiScanner.app is supported."""
    return PLATFORM == "Darwin"
