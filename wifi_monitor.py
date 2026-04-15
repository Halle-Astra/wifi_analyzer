#!/usr/bin/env python3
"""
WiFi Channel Monitor — continuously records WiFi metrics for later analysis.

Data collected every interval:
  - Connection status, SSID, BSSID (if available)
  - Channel, bandwidth, PHY mode
  - Signal strength (RSSI) and noise floor (dBm)
  - SNR (signal-to-noise ratio)
  - TX rate, MCS index
  - Number of neighboring networks and per-channel counts
  - Internet connectivity (ping test)

Output: CSV log + JSON events log, rotated daily.
"""

import argparse
import csv
import datetime
import json
import os
import re
import subprocess
import signal
import sys
import time

DEFAULT_INTERVAL = 10  # seconds
DEFAULT_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
PING_TARGET = "223.5.5.5"  # Alibaba DNS, low latency in China
PING_AP = None  # AP/gateway address, set via --ap
PING_TIMEOUT = 3  # seconds

running = True


def handle_signal(signum, frame):
    global running
    running = False
    print(f"\n[{now()}] Received signal {signum}, stopping gracefully...")


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return datetime.datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def get_wifi_data():
    """Collect WiFi info via system_profiler JSON output."""
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


def parse_signal_noise(raw):
    """Parse '-51 dBm / -94 dBm' into (signal, noise) integers."""
    match = re.findall(r"(-?\d+)\s*dBm", raw or "")
    if len(match) >= 2:
        return int(match[0]), int(match[1])
    return None, None


def parse_channel_info(raw):
    """Parse '157 (5GHz, 40MHz)' into (channel, band, width)."""
    match = re.match(r"(\d+)\s*\((\S+?),\s*(\S+?)\)", raw or "")
    if match:
        return int(match.group(1)), match.group(2), match.group(3)
    return None, None, None


def ping_test(target=PING_TARGET, timeout=PING_TIMEOUT):
    """Return (reachable: bool, latency_ms: float|None)."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout * 1000), target],
            capture_output=True, text=True, timeout=timeout + 2,
        )
        if result.returncode == 0:
            match = re.search(r"time[=<]([\d.]+)\s*ms", result.stdout)
            latency = float(match.group(1)) if match else None
            return True, latency
    except Exception:
        pass
    return False, None


def count_neighbors(iface):
    """Count neighboring networks and group by channel, with full details."""
    networks = iface.get("spairport_airport_other_local_wireless_networks", [])
    channel_counts = {}
    channel_networks = {}
    for net in networks:
        ch_raw = net.get("spairport_network_channel", "")
        ch, band, width = parse_channel_info(ch_raw)
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


SCANNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wifi_scanner")
NATIVE_SCAN_PATH = os.path.expanduser("~/.wifi-monitor/native_scan.json")


def rf_scan_from_native_app():
    """Read scan output from the native app if available and fresh."""
    if not os.path.isfile(NATIVE_SCAN_PATH):
        return None
    try:
        st = os.stat(NATIVE_SCAN_PATH)
        if time.time() - st.st_mtime > 20:
            return None
        with open(NATIVE_SCAN_PATH) as fh:
            data = json.load(fh)
        if data.get("authorized") and "networks" in data:
            return data
    except Exception:
        pass
    return None


def rf_scan():
    """Prefer native app output; otherwise run Swift CLI scanner."""
    native_data = rf_scan_from_native_app()
    if native_data:
        return native_data
    if not os.path.isfile(SCANNER_PATH):
        return None
    try:
        result = subprocess.run(
            [SCANNER_PATH], capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception:
        pass
    return None


BW_NAMES = {0: "20MHz", 1: "20MHz", 2: "40MHz", 3: "80MHz", 4: "160MHz"}
BAND_NAMES = {0: "unknown", 1: "2.4GHz", 2: "5GHz"}


def merge_rf_data(channel_networks, rf_data):
    """Merge RF scan data into channel_networks.

    When the native app provides SSIDs (authorized=True), we use the RF scan
    as the authoritative source for all networks — each RF entry IS the real
    device, with its own SSID (or empty = truly anonymous). We discard the
    system_profiler neighbor list and rebuild channel_networks entirely from
    the RF scan to avoid named/anonymous flip-flop caused by system_profiler's
    inconsistent scan results.

    When the native app is NOT authorized (no SSIDs), we fall back to
    count-based anonymous estimation against system_profiler's named list.
    """
    if not rf_data or "networks" not in rf_data:
        return channel_networks, {}

    has_ssids = rf_data.get("authorized", False) and any(
        n.get("ssid") for n in rf_data["networks"]
    )

    rf_by_channel = {}
    for net in rf_data["networks"]:
        ch = net.get("channel", 0)
        if ch not in rf_by_channel:
            rf_by_channel[ch] = []
        rf_by_channel[ch].append(net)

    rf_channel_summary = {}

    if has_ssids:
        new_channel_networks = {}
        all_channels = set(channel_networks.keys()) | set(rf_by_channel.keys())
        for ch in all_channels:
            rf_list = rf_by_channel.get(ch, [])
            rssi_values = [n["rssi"] for n in rf_list]
            noise_values = [n["noise"] for n in rf_list if n.get("noise", 0) != 0]
            named_count = sum(1 for n in rf_list if n.get("ssid"))
            anon_count = sum(1 for n in rf_list if not n.get("ssid"))

            rf_channel_summary[ch] = {
                "named_count": named_count,
                "rf_total_count": len(rf_list),
                "anonymous_count": anon_count,
                "rssi_values": rssi_values,
                "rssi_min": min(rssi_values) if rssi_values else None,
                "rssi_max": max(rssi_values) if rssi_values else None,
                "rssi_avg": round(sum(rssi_values) / len(rssi_values), 1) if rssi_values else None,
                "noise_values": noise_values,
            }

            entries = []
            for rf in rf_list:
                bw_raw = rf.get("channelWidth", 0)
                band_raw = rf.get("channelBand", 0)
                entries.append({
                    "ssid": rf.get("ssid", ""),
                    "bssid": rf.get("bssid", ""),
                    "channel_raw": "",
                    "band": BAND_NAMES.get(band_raw, ""),
                    "width": BW_NAMES.get(bw_raw, ""),
                    "phy_mode": "",
                    "security": "",
                    "rssi": rf["rssi"],
                    "anonymous": not bool(rf.get("ssid")),
                })
            new_channel_networks[ch] = entries
        return new_channel_networks, rf_channel_summary

    all_channels = set(channel_networks.keys()) | set(rf_by_channel.keys())
    for ch in all_channels:
        named = channel_networks.get(ch, [])
        rf_list = rf_by_channel.get(ch, [])
        rssi_values = [n["rssi"] for n in rf_list]
        noise_values = [n["noise"] for n in rf_list if n.get("noise", 0) != 0]
        unmatched = rf_list[len(named):] if len(rf_list) > len(named) else []
        anonymous_count = len(unmatched)

        rf_channel_summary[ch] = {
            "named_count": len(named),
            "rf_total_count": len(rf_list),
            "anonymous_count": anonymous_count,
            "rssi_values": rssi_values,
            "rssi_min": min(rssi_values) if rssi_values else None,
            "rssi_max": max(rssi_values) if rssi_values else None,
            "rssi_avg": round(sum(rssi_values) / len(rssi_values), 1) if rssi_values else None,
            "noise_values": noise_values,
        }

        for rf in unmatched:
            bw_raw = rf.get("channelWidth", 0)
            band_raw = rf.get("channelBand", 0)
            if ch not in channel_networks:
                channel_networks[ch] = []
            channel_networks[ch].append({
                "ssid": "",
                "bssid": "",
                "channel_raw": "",
                "band": BAND_NAMES.get(band_raw, ""),
                "width": BW_NAMES.get(bw_raw, ""),
                "phy_mode": "",
                "security": "",
                "rssi": rf["rssi"],
                "anonymous": True,
            })

    return channel_networks, rf_channel_summary


def scan_bluetooth():
    """Scan nearby Bluetooth devices via system_profiler."""
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
                            dev = {
                                "name": name,
                                "connected": category_key == "device_connected",
                                "address": info.get("device_address", ""),
                                "type": info.get("device_minorType", ""),
                                "rssi": info.get("device_rssi", ""),
                            }
                            devices.append(dev)

        return devices
    except Exception:
        return []


def current_scan_rssi(rf_data, ssid, channel):
    """Return scan-based RSSI for the current SSID/channel if available."""
    if not rf_data or not ssid or channel is None:
        return None, None
    matches = [
        n for n in rf_data.get("networks", [])
        if n.get("ssid") == ssid and n.get("channel") == channel
    ]
    if not matches:
        return None, None
    # If multiple BSSIDs share the same SSID/channel, use the strongest one.
    best = max(matches, key=lambda n: n.get("rssi", -999))
    return best.get("rssi"), best.get("bssid")


def collect_snapshot():
    """Collect a single snapshot of WiFi state."""
    iface = get_wifi_data()
    if "_error" in iface:
        return {
            "timestamp": now(),
            "status": "error",
            "error": iface["_error"],
        }

    status_raw = iface.get("spairport_status_information", "")
    connected = "connected" in status_raw.lower()

    current = iface.get("spairport_current_network_information", {})
    ssid = current.get("_name")
    channel_raw = current.get("spairport_network_channel")
    channel, band, width = parse_channel_info(channel_raw)
    phy = current.get("spairport_network_phymode")
    signal, noise = parse_signal_noise(current.get("spairport_signal_noise"))
    snr = (signal - noise) if (signal is not None and noise is not None) else None
    tx_rate = current.get("spairport_network_rate")
    mcs = current.get("spairport_network_mcs")
    security = current.get("spairport_security_mode")

    neighbor_count, channel_dist, channel_networks = count_neighbors(iface)

    rf_data = rf_scan()
    channel_networks, rf_channel_summary = merge_rf_data(channel_networks, rf_data)
    scan_rssi_dbm, scan_bssid = current_scan_rssi(rf_data, ssid, channel)

    # Recompute top-level counts from the merged authoritative network list.
    merged_named = sum(
        1 for nets in channel_networks.values() for n in nets if not n.get("anonymous")
    )
    merged_anon = sum(
        1 for nets in channel_networks.values() for n in nets if n.get("anonymous")
    )
    if channel is not None:
        same_channel_neighbors = len(channel_networks.get(channel, []))
    else:
        same_channel_neighbors = 0

    bt_devices = scan_bluetooth()

    reachable, latency = ping_test()

    ap_reachable, ap_latency = (None, None)
    if PING_AP:
        ap_reachable, ap_latency = ping_test(target=PING_AP)

    rf_total = merged_named + merged_anon

    return {
        "timestamp": now(),
        "status": "connected" if connected else "disconnected",
        "ssid": ssid,
        "channel": channel,
        "band": band,
        "width": width,
        "phy_mode": phy,
        "signal_dbm": signal,
        "scan_signal_dbm": scan_rssi_dbm,
        "current_bssid": scan_bssid,
        "noise_dbm": noise,
        "snr_db": snr,
        "tx_rate_mbps": tx_rate,
        "mcs_index": mcs,
        "security": security,
        "neighbor_count": merged_named,
        "same_channel_neighbors": same_channel_neighbors,
        "channel_distribution": channel_dist,
        "channel_networks": channel_networks,
        "rf_total_devices": rf_total,
        "anonymous_devices": merged_anon,
        "rf_channel_summary": rf_channel_summary,
        "bluetooth_devices": bt_devices,
        "bluetooth_device_count": len(bt_devices),
        "ap_ping_target": PING_AP,
        "ap_reachable": ap_reachable,
        "ap_ping_latency_ms": ap_latency,
        "internet_reachable": reachable,
        "ping_latency_ms": latency,
    }


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "timestamp", "status", "ssid", "channel", "band", "width", "phy_mode",
    "signal_dbm", "noise_dbm", "snr_db", "tx_rate_mbps", "mcs_index",
    "security", "neighbor_count", "same_channel_neighbors",
    "rf_total_devices", "anonymous_devices", "bluetooth_device_count",
    "ap_ping_target", "ap_reachable", "ap_ping_latency_ms",
    "internet_reachable", "ping_latency_ms",
]


def ensure_log_dir(log_dir):
    os.makedirs(log_dir, exist_ok=True)


def csv_path(log_dir):
    return os.path.join(log_dir, f"wifi_{today()}.csv")


def json_path(log_dir):
    return os.path.join(log_dir, f"wifi_{today()}.jsonl")


def write_csv_row(snapshot, log_dir):
    path = csv_path(log_dir)
    file_exists = os.path.isfile(path)
    with open(path, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerow(snapshot)


def write_json_line(snapshot, log_dir):
    path = json_path(log_dir)
    with open(path, "a") as fh:
        fh.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    return path


# ---------------------------------------------------------------------------
# Event detection
# ---------------------------------------------------------------------------

def detect_events(prev, curr):
    """Compare two snapshots and return a list of notable events."""
    events = []
    if prev is None:
        return events

    if prev.get("status") == "connected" and curr.get("status") != "connected":
        events.append({"type": "DISCONNECT", "detail": f"Was on {prev.get('ssid')} ch{prev.get('channel')}"})

    if prev.get("status") != "connected" and curr.get("status") == "connected":
        events.append({"type": "RECONNECT", "detail": f"Now on {curr.get('ssid')} ch{curr.get('channel')}"})

    if (prev.get("status") == "connected" and curr.get("status") == "connected"
            and prev.get("channel") and curr.get("channel")
            and prev.get("channel") != curr.get("channel")):
        events.append({
            "type": "CHANNEL_CHANGE",
            "detail": f"{prev.get('channel')} -> {curr.get('channel')}",
        })

    if prev.get("internet_reachable") and not curr.get("internet_reachable"):
        events.append({"type": "INTERNET_LOST", "detail": "Ping failed"})

    if not prev.get("internet_reachable") and curr.get("internet_reachable"):
        events.append({"type": "INTERNET_RESTORED", "detail": f"Latency {curr.get('ping_latency_ms')}ms"})

    prev_snr = prev.get("snr_db")
    curr_snr = curr.get("snr_db")
    if prev_snr is not None and curr_snr is not None and (prev_snr - curr_snr) >= 10:
        events.append({
            "type": "SNR_DROP",
            "detail": f"SNR dropped {prev_snr} -> {curr_snr} dB",
        })

    curr_signal = curr.get("signal_dbm")
    if curr_signal is not None and curr_signal > -30:
        events.append({"type": "SIGNAL_ANOMALY", "detail": f"Unusually strong signal {curr_signal} dBm"})
    elif curr_signal is not None and curr_signal < -75:
        events.append({"type": "WEAK_SIGNAL", "detail": f"Signal {curr_signal} dBm"})

    prev_noise = prev.get("noise_dbm")
    curr_noise = curr.get("noise_dbm")
    if (prev_noise is not None and curr_noise is not None
            and curr_noise - prev_noise >= 3
            and curr_signal is not None and prev.get("signal_dbm") is not None
            and abs(curr_signal - prev.get("signal_dbm")) <= 3):
        events.append({
            "type": "NOISE_SPIKE",
            "detail": f"Noise {prev_noise} -> {curr_noise} dBm, signal stable -> possible non-WiFi interference",
        })

    ap_latency = curr.get("ap_ping_latency_ms")
    if ap_latency is not None and ap_latency > 50:
        events.append({
            "type": "AP_LATENCY_HIGH",
            "detail": f"AP ping {ap_latency:.1f}ms > 50ms threshold",
        })

    return events


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def print_snapshot(snapshot, events):
    """Pretty-print one snapshot to terminal."""
    status = snapshot.get("status", "?")
    ssid = snapshot.get("ssid") or "-"
    ch = snapshot.get("channel") or "-"
    band = snapshot.get("band") or ""
    signal_val = snapshot.get("signal_dbm")
    noise = snapshot.get("noise_dbm")
    snr = snapshot.get("snr_db")
    tx = snapshot.get("tx_rate_mbps")
    ping_ok = snapshot.get("internet_reachable")
    latency = snapshot.get("ping_latency_ms")
    neighbors = snapshot.get("neighbor_count", 0)
    same_ch = snapshot.get("same_channel_neighbors", 0)
    rf_total = snapshot.get("rf_total_devices", 0)
    anon = snapshot.get("anonymous_devices", 0)
    bt_count = snapshot.get("bluetooth_device_count", 0)

    sig_str = f"{signal_val}" if signal_val is not None else "-"
    noise_str = f"{noise}" if noise is not None else "-"
    snr_str = f"{snr}" if snr is not None else "-"
    tx_str = f"{tx}" if tx is not None else "-"
    lat_str = f"{latency:.1f}ms" if latency is not None else "FAIL"
    ping_icon = "OK" if ping_ok else "FAIL"

    ap_lat = snapshot.get("ap_ping_latency_ms")
    ap_ok = snapshot.get("ap_reachable")
    ap_str = f"{ap_lat:.1f}ms" if ap_lat is not None else ("FAIL" if ap_ok is not None else "-")

    line = (
        f"[{snapshot['timestamp']}] "
        f"{status:>12} | {ssid:<16} | ch{ch:>4} {band:>5} | "
        f"sig={sig_str:>4} noise={noise_str:>4} SNR={snr_str:>3} | "
        f"tx={tx_str:>4}Mbps | "
        f"nbr={neighbors:>2}(same_ch={same_ch}) rf={rf_total} anon={anon} bt={bt_count} | "
        f"ap={ap_str} inet={ping_icon} {lat_str}"
    )
    print(line)

    for event in events:
        print(f"  *** [{event['type']}] {event['detail']}")


def main():
    parser = argparse.ArgumentParser(description="WiFi Channel Monitor")
    parser.add_argument("-i", "--interval", type=int, default=DEFAULT_INTERVAL,
                        help=f"Sampling interval in seconds (default: {DEFAULT_INTERVAL})")
    parser.add_argument("-d", "--log-dir", default=DEFAULT_LOG_DIR,
                        help=f"Log output directory (default: {DEFAULT_LOG_DIR})")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress terminal output (log only)")
    parser.add_argument("--ap", default=None,
                        help="AP/gateway IP for local ping analysis, e.g. 192.168.1.10")
    args = parser.parse_args()

    global PING_AP
    PING_AP = args.ap

    native_app_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "WiFiScanner.app")
    native_started = False
    if os.path.isdir(native_app_path):
        try:
            result = subprocess.run(["pgrep", "-f", "wifi_scanner_app"], capture_output=True)
            if result.returncode != 0:
                subprocess.Popen(["open", native_app_path])
                native_started = True
        except Exception:
            pass

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    ensure_log_dir(args.log_dir)

    print(f"[{now()}] WiFi Monitor started")
    print(f"  Interval : {args.interval}s")
    print(f"  Log dir  : {args.log_dir}")
    print(f"  Ping test: {PING_TARGET}")
    print(f"  AP ping  : {PING_AP or 'disabled (use --ap 192.168.1.10)'}")
    scanner_ok = os.path.isfile(SCANNER_PATH)
    print(f"  Native   : {'running' if native_started or os.path.isdir(native_app_path) else 'not found (run: bash build_scanner_app.sh)'}")
    print(f"  RF scan  : {'enabled' if scanner_ok else 'disabled'}")
    print(f"  BT scan  : enabled")
    print("-" * 120)

    prev_snapshot = None

    while running:
        snapshot = collect_snapshot()
        events = detect_events(prev_snapshot, snapshot)
        snapshot["events"] = [e["type"] for e in events]

        write_csv_row(snapshot, args.log_dir)
        write_json_line(snapshot, args.log_dir)

        if not args.quiet:
            print_snapshot(snapshot, events)

        prev_snapshot = snapshot

        for _ in range(args.interval * 10):
            if not running:
                break
            time.sleep(0.1)

    print(f"[{now()}] Monitor stopped. Logs saved to {args.log_dir}/")
    if native_started:
        try:
            subprocess.run(["pkill", "-f", "wifi_scanner_app"], capture_output=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
