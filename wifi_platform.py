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


def _linux_nmcli_scan():
    """Use nmcli to list visible WiFi networks (no root required)."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f",
             "BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY,MODE,RATE",
             "dev", "wifi", "list", "--rescan", "no"],
            capture_output=True, text=True, timeout=10,
        )
        networks = []
        for line in result.stdout.strip().splitlines():
            unescaped = line.replace("\\:", "\x00")
            parts = unescaped.split(":")
            parts = [p.replace("\x00", ":") for p in parts]
            if len(parts) < 8:
                continue
            bssid_raw = parts[0]
            ssid = parts[1]
            try:
                chan = int(parts[2])
            except ValueError:
                chan = 0
            freq_match = re.search(r"(\d+)", parts[3])
            freq = int(freq_match.group(1)) if freq_match else 0
            try:
                signal_pct = int(parts[4])
            except ValueError:
                signal_pct = 0
            rssi_est = (signal_pct / 2) - 100 if signal_pct else -100
            security = parts[5]
            rate_match = re.search(r"([\d.]+)", parts[7])
            rate = float(rate_match.group(1)) if rate_match else None
            band = _freq_to_band(freq) if freq else ""
            width_guess = "20MHz"
            if rate and rate > 300:
                width_guess = "80MHz"
            elif rate and rate > 150:
                width_guess = "40MHz"

            networks.append({
                "bssid": bssid_raw,
                "ssid": ssid,
                "channel": chan,
                "freq": freq,
                "signal_pct": signal_pct,
                "rssi_est": rssi_est,
                "security": security,
                "band": band,
                "width": width_guess,
                "rate": rate,
            })
        return networks
    except Exception:
        return []


def _linux_scan_bluetooth():
    """Scan Bluetooth devices on Linux via bluetoothctl."""
    devices = []
    try:
        result = subprocess.run(
            ["bluetoothctl", "devices"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().splitlines():
            match = re.match(r"Device\s+(\S+)\s+(.*)", line)
            if match:
                addr = match.group(1)
                name = match.group(2)
                connected = False
                try:
                    info = subprocess.run(
                        ["bluetoothctl", "info", addr],
                        capture_output=True, text=True, timeout=3,
                    )
                    if "Connected: yes" in info.stdout:
                        connected = True
                    type_match = re.search(r"Icon:\s*(\S+)", info.stdout)
                    dev_type = type_match.group(1) if type_match else ""
                except Exception:
                    dev_type = ""
                devices.append({
                    "name": name,
                    "connected": connected,
                    "address": addr,
                    "type": dev_type,
                    "rssi": "",
                })
    except Exception:
        pass
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
    nmcli_nets = _linux_nmcli_scan()

    channel_networks = {}
    channel_counts = {}
    for net in nmcli_nets:
        if net["ssid"] == ssid and net.get("bssid") == bssid:
            security = net.get("security")
        ch = net["channel"]
        if ch:
            channel_counts[ch] = channel_counts.get(ch, 0) + 1
            if ch not in channel_networks:
                channel_networks[ch] = []
            channel_networks[ch].append({
                "ssid": net["ssid"],
                "bssid": net["bssid"],
                "channel_raw": f"{ch} ({net['freq']} MHz)" if net.get("freq") else str(ch),
                "band": net.get("band", ""),
                "width": net.get("width", ""),
                "phy_mode": "",
                "security": net.get("security", ""),
                "rssi": net["rssi_est"],
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
