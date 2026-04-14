#!/usr/bin/env python3
"""
WiFi Log Analyzer — review collected WiFi monitoring data.

Features:
  - Summary statistics for a date range
  - Disconnect / event timeline
  - Channel congestion analysis
  - Signal quality trend (text-based sparkline)
  - Query around a specific incident time
"""

import argparse
import csv
import datetime
import glob
import json
import os
import sys

DEFAULT_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_csv_files(log_dir, date_from=None, date_to=None):
    """Load and merge CSV logs, optionally filtering by date."""
    pattern = os.path.join(log_dir, "wifi_*.csv")
    files = sorted(glob.glob(pattern))
    rows = []
    for filepath in files:
        basename = os.path.basename(filepath)
        file_date = basename.replace("wifi_", "").replace(".csv", "")
        if date_from and file_date < date_from:
            continue
        if date_to and file_date > date_to:
            continue
        with open(filepath, newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                for key in ["signal_dbm", "noise_dbm", "snr_db", "tx_rate_mbps",
                             "mcs_index", "channel", "neighbor_count",
                             "same_channel_neighbors", "ping_latency_ms",
                             "rf_total_devices", "anonymous_devices",
                             "bluetooth_device_count"]:
                    if row.get(key):
                        try:
                            row[key] = float(row[key])
                        except ValueError:
                            row[key] = None
                    else:
                        row[key] = None
                row["internet_reachable"] = row.get("internet_reachable", "").lower() == "true"
                rows.append(row)
    return rows


def load_jsonl_files(log_dir, date_from=None, date_to=None):
    """Load JSONL event logs."""
    pattern = os.path.join(log_dir, "wifi_*.jsonl")
    files = sorted(glob.glob(pattern))
    entries = []
    for filepath in files:
        basename = os.path.basename(filepath)
        file_date = basename.replace("wifi_", "").replace(".jsonl", "")
        if date_from and file_date < date_from:
            continue
        if date_to and file_date > date_to:
            continue
        with open(filepath) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    return entries


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def print_summary(rows):
    """Print overall summary statistics."""
    if not rows:
        print("No data found.")
        return

    total = len(rows)
    connected = sum(1 for r in rows if r.get("status") == "connected")
    disconnected = sum(1 for r in rows if r.get("status") == "disconnected")
    inet_ok = sum(1 for r in rows if r.get("internet_reachable"))
    inet_fail = sum(1 for r in rows if not r.get("internet_reachable"))

    signals = [r["signal_dbm"] for r in rows if r.get("signal_dbm") is not None]
    noises = [r["noise_dbm"] for r in rows if r.get("noise_dbm") is not None]
    snrs = [r["snr_db"] for r in rows if r.get("snr_db") is not None]
    latencies = [r["ping_latency_ms"] for r in rows if r.get("ping_latency_ms") is not None]
    tx_rates = [r["tx_rate_mbps"] for r in rows if r.get("tx_rate_mbps") is not None]

    time_start = rows[0].get("timestamp", "?")
    time_end = rows[-1].get("timestamp", "?")

    print("=" * 80)
    print("  WiFi Monitoring Summary")
    print("=" * 80)
    print(f"  Time range    : {time_start} ~ {time_end}")
    print(f"  Total samples : {total}")
    print(f"  Connected     : {connected} ({100*connected/total:.1f}%)")
    print(f"  Disconnected  : {disconnected} ({100*disconnected/total:.1f}%)")
    print(f"  Internet OK   : {inet_ok} ({100*inet_ok/total:.1f}%)")
    print(f"  Internet FAIL : {inet_fail} ({100*inet_fail/total:.1f}%)")

    rf_totals = [r["rf_total_devices"] for r in rows if r.get("rf_total_devices") is not None]
    anon_totals = [r["anonymous_devices"] for r in rows if r.get("anonymous_devices") is not None]
    bt_totals = [r["bluetooth_device_count"] for r in rows if r.get("bluetooth_device_count") is not None]
    if rf_totals:
        print(f"  RF devices    : avg={sum(rf_totals)/len(rf_totals):.0f}, max={max(rf_totals):.0f}")
    if anon_totals and max(anon_totals) > 0:
        print(f"  Anonymous dev : avg={sum(anon_totals)/len(anon_totals):.0f}, max={max(anon_totals):.0f}")
    if bt_totals:
        print(f"  BT devices    : avg={sum(bt_totals)/len(bt_totals):.0f}, max={max(bt_totals):.0f}")
    print()

    if signals:
        print(f"  Signal (RSSI) : avg={sum(signals)/len(signals):.1f} dBm, "
              f"min={min(signals):.0f}, max={max(signals):.0f}")
    if noises:
        print(f"  Noise floor   : avg={sum(noises)/len(noises):.1f} dBm, "
              f"min={min(noises):.0f}, max={max(noises):.0f}")
    if snrs:
        print(f"  SNR           : avg={sum(snrs)/len(snrs):.1f} dB, "
              f"min={min(snrs):.0f}, max={max(snrs):.0f}")
    if latencies:
        print(f"  Ping latency  : avg={sum(latencies)/len(latencies):.1f} ms, "
              f"min={min(latencies):.1f}, max={max(latencies):.1f}")
    if tx_rates:
        print(f"  TX rate       : avg={sum(tx_rates)/len(tx_rates):.0f} Mbps, "
              f"min={min(tx_rates):.0f}, max={max(tx_rates):.0f}")
    print()


def print_events(entries):
    """Print event timeline from JSONL data."""
    events_found = []
    for entry in entries:
        ev_list = entry.get("events", [])
        if ev_list:
            events_found.append((entry.get("timestamp", "?"), ev_list, entry))

    if not events_found:
        print("  No events detected in the log period.")
        return

    print("=" * 80)
    print("  Event Timeline")
    print("=" * 80)

    for ts, evts, snapshot in events_found:
        for evt in evts:
            sig = snapshot.get("signal_dbm", "-")
            snr = snapshot.get("snr_db", "-")
            ch = snapshot.get("channel", "-")
            print(f"  [{ts}] {evt:20s} | ch={ch} sig={sig} SNR={snr}")
    print()
    print(f"  Total events: {sum(len(e[1]) for e in events_found)}")
    print()


def print_disconnects(entries):
    """Focused view on disconnect/internet-loss incidents."""
    incidents = []
    for entry in entries:
        ev_list = entry.get("events", [])
        if any(e in ("DISCONNECT", "INTERNET_LOST") for e in ev_list):
            incidents.append(entry)

    print("=" * 80)
    print("  Disconnect / Internet Loss Incidents")
    print("=" * 80)

    if not incidents:
        print("  None found — your WiFi has been stable!")
        print()
        return

    for inc in incidents:
        ts = inc.get("timestamp", "?")
        status = inc.get("status", "?")
        ssid = inc.get("ssid") or "-"
        ch = inc.get("channel", "-")
        sig = inc.get("signal_dbm", "-")
        snr = inc.get("snr_db", "-")
        neighbors = inc.get("neighbor_count", "-")
        same_ch = inc.get("same_channel_neighbors", "-")
        inet = "OK" if inc.get("internet_reachable") else "FAIL"
        evts = ", ".join(inc.get("events", []))

        print(f"  [{ts}] {evts}")
        print(f"    status={status} ssid={ssid} ch={ch} sig={sig} SNR={snr}")
        print(f"    neighbors={neighbors} same_channel={same_ch} internet={inet}")
        print()


def print_around_time(rows, target_time, window_minutes=5):
    """Show data around a specific time for incident investigation."""
    try:
        target = datetime.datetime.strptime(target_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            target = datetime.datetime.strptime(target_time, "%Y-%m-%d %H:%M")
        except ValueError:
            print(f"  Invalid time format: {target_time}")
            print("  Use: YYYY-MM-DD HH:MM:SS or YYYY-MM-DD HH:MM")
            return

    delta = datetime.timedelta(minutes=window_minutes)

    print("=" * 80)
    print(f"  Data around {target_time} (±{window_minutes} min)")
    print("=" * 80)

    found = []
    for row in rows:
        try:
            row_time = datetime.datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, KeyError):
            continue
        if abs((row_time - target).total_seconds()) <= delta.total_seconds():
            found.append(row)

    if not found:
        print("  No data found in this time window.")
        print()
        return

    header = f"  {'Time':>19} {'Status':>12} {'SSID':<16} {'Ch':>4} {'Sig':>5} {'SNR':>4} {'TX':>5} {'Nbr':>4} {'SCh':>4} {'Inet':>5} {'Ping':>8}"
    print(header)
    print("  " + "-" * len(header.strip()))
    for r in found:
        ts = r.get("timestamp", "?")
        st = r.get("status", "?")
        ssid = (r.get("ssid") or "-")[:16]
        ch_raw = r.get("channel")
        ch = f"{int(ch_raw)}" if ch_raw is not None else "-"
        sig = r.get("signal_dbm")
        snr = r.get("snr_db")
        tx = r.get("tx_rate_mbps")
        nbr = r.get("neighbor_count")
        sch = r.get("same_channel_neighbors")
        inet = "OK" if r.get("internet_reachable") else "FAIL"
        lat = r.get("ping_latency_ms")

        sig_s = f"{sig:.0f}" if sig is not None else "-"
        snr_s = f"{snr:.0f}" if snr is not None else "-"
        tx_s = f"{tx:.0f}" if tx is not None else "-"
        nbr_s = f"{nbr:.0f}" if nbr is not None else "-"
        sch_s = f"{sch:.0f}" if sch is not None else "-"
        lat_s = f"{lat:.1f}ms" if lat is not None else "-"

        print(f"  {ts:>19} {st:>12} {ssid:<16} {ch:>4} {sig_s:>5} {snr_s:>4} {tx_s:>5} {nbr_s:>4} {sch_s:>4} {inet:>5} {lat_s:>8}")
    print()


def print_channel_analysis(rows):
    """Analyze channel congestion from collected data."""
    print("=" * 80)
    print("  Channel Congestion Analysis")
    print("=" * 80)

    channels_used = {}
    for r in rows:
        ch = r.get("channel")
        if ch is not None:
            ch = int(ch)
            if ch not in channels_used:
                channels_used[ch] = {"count": 0, "signals": [], "snrs": []}
            channels_used[ch]["count"] += 1
            if r.get("signal_dbm") is not None:
                channels_used[ch]["signals"].append(r["signal_dbm"])
            if r.get("snr_db") is not None:
                channels_used[ch]["snrs"].append(r["snr_db"])

    if not channels_used:
        print("  No channel data available.")
        print()
        return

    print(f"\n  Your WiFi used these channels:")
    for ch in sorted(channels_used.keys()):
        info = channels_used[ch]
        avg_sig = sum(info["signals"]) / len(info["signals"]) if info["signals"] else 0
        avg_snr = sum(info["snrs"]) / len(info["snrs"]) if info["snrs"] else 0
        pct = 100 * info["count"] / len(rows)
        print(f"    Channel {ch:>3}: {info['count']:>5} samples ({pct:5.1f}%) "
              f"| avg signal={avg_sig:.1f} dBm, avg SNR={avg_snr:.1f} dB")
    print()


def print_neighbor_details(entries, target_time=None, window_minutes=5):
    """Show per-channel network details from JSONL data, including anonymous and BT devices."""
    print("=" * 80)

    selected = []
    if target_time:
        try:
            target = datetime.datetime.strptime(target_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                target = datetime.datetime.strptime(target_time, "%Y-%m-%d %H:%M")
            except ValueError:
                print(f"  Invalid time format: {target_time}")
                return
        delta = datetime.timedelta(minutes=window_minutes)
        for entry in entries:
            try:
                entry_time = datetime.datetime.strptime(entry.get("timestamp", ""), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if abs((entry_time - target).total_seconds()) <= delta.total_seconds():
                selected.append(entry)
        print(f"  Neighbor Networks around {target_time} (±{window_minutes} min)")
    else:
        if entries:
            selected = [entries[-1]]
        print("  Neighbor Networks (latest snapshot)")

    print("=" * 80)

    if not selected:
        print("  No data found.")
        print()
        return

    for snapshot in selected:
        ts = snapshot.get("timestamp", "?")
        my_ssid = snapshot.get("ssid") or "-"
        my_ch = snapshot.get("channel", "-")
        ch_nets = snapshot.get("channel_networks", {})
        rf_summary = snapshot.get("rf_channel_summary", {})
        bt_devices = snapshot.get("bluetooth_devices", [])

        if not ch_nets and not bt_devices:
            print(f"\n  [{ts}] No neighbor data in this snapshot.")
            continue

        rf_total = snapshot.get("rf_total_devices", 0)
        anon_total = snapshot.get("anonymous_devices", 0)
        named_total = snapshot.get("neighbor_count", 0)

        print(f"\n  [{ts}] My network: {my_ssid} @ ch{my_ch}")
        print(f"  RF devices: {rf_total} total = {named_total} named + {anon_total} anonymous")
        print(f"  {'─' * 78}")

        for ch_key in sorted(ch_nets.keys(), key=lambda x: int(x) if str(x).isdigit() else 0):
            nets = ch_nets[ch_key]
            is_my_channel = str(ch_key) == str(my_ch)
            marker = " ◀ MY CHANNEL" if is_my_channel else ""

            rf_info = rf_summary.get(str(ch_key), rf_summary.get(int(ch_key) if str(ch_key).isdigit() else ch_key, {}))
            anon_count = rf_info.get("anonymous_count", 0)
            rssi_avg = rf_info.get("rssi_avg")
            rssi_range = ""
            if rf_info.get("rssi_min") is not None:
                rssi_range = f"  [RSSI range: {rf_info['rssi_min']} ~ {rf_info['rssi_max']} dBm, avg={rssi_avg}]"

            named_nets = [n for n in nets if not n.get("anonymous")]
            anon_nets = [n for n in nets if n.get("anonymous")]

            total_on_ch = len(named_nets) + len(anon_nets)
            print(f"\n  Channel {ch_key}{marker}  ({total_on_ch} devices: {len(named_nets)} named, {len(anon_nets)} anonymous){rssi_range}")

            for net in sorted(named_nets, key=lambda n: n.get("ssid", "")):
                ssid = net.get("ssid") or "(hidden SSID)"
                phy = net.get("phy_mode", "")
                width = net.get("width", "")
                band = net.get("band", "")
                rssi = net.get("rssi")
                ch_detail = f"{band}, {width}" if band and width else ""
                rssi_str = f"RSSI={rssi}" if rssi is not None else ""
                print(f"    • {ssid:<28} {phy:<20} {ch_detail:<14} {rssi_str}")

            for idx, net in enumerate(anon_nets):
                rssi = net.get("rssi", "?")
                width = net.get("width", "")
                band = net.get("band", "")
                ch_detail = f"{band}, {width}" if band and width else ""
                print(f"    ◦ (anonymous #{idx+1}){'':<17} {'':20} {ch_detail:<14} RSSI={rssi}")

        total_named = sum(1 for nets in ch_nets.values() for n in nets if not n.get("anonymous"))
        total_anon = sum(1 for nets in ch_nets.values() for n in nets if n.get("anonymous"))
        print(f"\n  {'─' * 78}")
        print(f"  WiFi total: {total_named + total_anon} devices on {len(ch_nets)} channels "
              f"({total_named} named, {total_anon} anonymous)")

        if bt_devices:
            print(f"\n  Bluetooth devices ({len(bt_devices)}) — potential 2.4GHz interference sources:")
            for dev in bt_devices:
                name = dev.get("name", "?")
                dev_type = dev.get("type", "")
                connected = "connected" if dev.get("connected") else "paired"
                rssi = dev.get("rssi", "")
                rssi_str = f" RSSI={rssi}" if rssi else ""
                type_str = f" ({dev_type})" if dev_type else ""
                print(f"    ◆ {name:<28} {connected:<12}{type_str}{rssi_str}")
        elif snapshot.get("bluetooth_device_count", 0) == 0:
            print(f"\n  Bluetooth: no paired/connected devices detected")

    print()


def print_signal_sparkline(rows, width=60):
    """Text-based signal strength trend."""
    signals = [(r.get("timestamp", ""), r.get("signal_dbm")) for r in rows if r.get("signal_dbm") is not None]
    if not signals:
        print("  No signal data for sparkline.")
        return

    print("=" * 80)
    print("  Signal Strength Trend")
    print("=" * 80)

    step = max(1, len(signals) // width)
    sampled = signals[::step][:width]

    min_sig = min(s[1] for s in sampled)
    max_sig = max(s[1] for s in sampled)
    range_sig = max_sig - min_sig if max_sig != min_sig else 1

    blocks = " ▁▂▃▄▅▆▇█"
    sparkline = ""
    for _, sig in sampled:
        idx = int((sig - min_sig) / range_sig * (len(blocks) - 1))
        sparkline += blocks[idx]

    print(f"  {max_sig:.0f} dBm (best)  ┤{sparkline}")
    print(f"  {min_sig:.0f} dBm (worst) ┤{'─' * len(sparkline)}")
    if sampled:
        print(f"  Time: {sampled[0][0]} ~ {sampled[-1][0]}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="WiFi Log Analyzer")
    parser.add_argument("-d", "--log-dir", default=DEFAULT_LOG_DIR,
                        help=f"Log directory (default: {DEFAULT_LOG_DIR})")
    parser.add_argument("--from", dest="date_from", default=None,
                        help="Start date filter (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", default=None,
                        help="End date filter (YYYY-MM-DD)")

    sub = parser.add_subparsers(dest="command", help="Analysis command")

    sub.add_parser("summary", help="Show overall summary")
    sub.add_parser("events", help="Show event timeline")
    sub.add_parser("disconnects", help="Show disconnect incidents")
    sub.add_parser("channels", help="Channel congestion analysis")
    sub.add_parser("signal", help="Signal strength trend")

    around_parser = sub.add_parser("around", help="Show data around a specific time")
    around_parser.add_argument("time", help="Target time (YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS)")
    around_parser.add_argument("-w", "--window", type=int, default=5,
                               help="Window in minutes (default: 5)")

    neighbors_parser = sub.add_parser("neighbors", help="Show per-channel neighbor network details")
    neighbors_parser.add_argument("time", nargs="?", default=None,
                                  help="Optional: target time (YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS)")
    neighbors_parser.add_argument("-w", "--window", type=int, default=5,
                                  help="Window in minutes (default: 5)")

    sub.add_parser("all", help="Run all analyses")

    args = parser.parse_args()
    command = args.command or "all"

    rows = load_csv_files(args.log_dir, args.date_from, args.date_to)
    entries = load_jsonl_files(args.log_dir, args.date_from, args.date_to)

    if not rows and not entries:
        print(f"No log data found in {args.log_dir}/")
        print("Run wifi_monitor.py first to collect data.")
        sys.exit(1)

    if command == "summary":
        print_summary(rows)
    elif command == "events":
        print_events(entries)
    elif command == "disconnects":
        print_disconnects(entries)
    elif command == "channels":
        print_channel_analysis(rows)
    elif command == "signal":
        print_signal_sparkline(rows)
    elif command == "around":
        print_around_time(rows, args.time, args.window)
    elif command == "neighbors":
        print_neighbor_details(entries, args.time, args.window)
    elif command == "all":
        print_summary(rows)
        print_events(entries)
        print_disconnects(entries)
        print_channel_analysis(rows)
        print_neighbor_details(entries)
        print_signal_sparkline(rows)


if __name__ == "__main__":
    main()
