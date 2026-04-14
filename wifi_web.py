#!/usr/bin/env python3
"""
WiFi Monitor Web UI — real-time WiFi dashboard in your browser.

Reuses data collection from wifi_monitor.py, adds:
  - Background sampling thread
  - Built-in HTTP server (zero dependencies)
  - JSON API endpoints
  - Single-page dashboard served as inline HTML
"""

import argparse
import collections
import json
import os
import signal
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(SCRIPT_DIR, "static")
sys.path.insert(0, SCRIPT_DIR)
import wifi_monitor as monitor_mod
from wifi_monitor import (
    collect_snapshot, detect_events, write_csv_row, write_json_line,
    ensure_log_dir, now, SCANNER_PATH,
)

NATIVE_APP_PATH = os.path.join(SCRIPT_DIR, "WiFiScanner.app")
NATIVE_APP_STARTED = False


def start_native_scanner():
    """Launch WiFiScanner.app if it exists and isn't already running."""
    global NATIVE_APP_STARTED
    if not os.path.isdir(NATIVE_APP_PATH):
        return False
    try:
        import subprocess as _sp
        result = _sp.run(["pgrep", "-f", "wifi_scanner_app"], capture_output=True)
        if result.returncode == 0:
            return True
        _sp.Popen(["open", NATIVE_APP_PATH])
        NATIVE_APP_STARTED = True
        return True
    except Exception:
        return False


def stop_native_scanner():
    """Stop WiFiScanner.app only if we started it."""
    if not NATIVE_APP_STARTED:
        return
    try:
        import subprocess as _sp
        _sp.run(["pkill", "-f", "wifi_scanner_app"], capture_output=True)
    except Exception:
        pass


DEFAULT_PORT = 8800
DEFAULT_INTERVAL = 10
DEFAULT_LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
TWO_DAYS_SECONDS = 2 * 24 * 3600


def safe_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def safe_bool(value):
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    value = str(value).strip().lower()
    if value in ("true", "1", "yes", "ok"):
        return True
    if value in ("false", "0", "no", "fail"):
        return False
    return None


def list_log_dates(log_dir):
    dates = []
    if not os.path.isdir(log_dir):
        return dates
    for name in sorted(os.listdir(log_dir)):
        if name.startswith("wifi_") and name.endswith(".jsonl"):
            dates.append(name[len("wifi_"):-len(".jsonl")])
    return dates


def snapshot_to_history_entry(snapshot):
    return {
        "timestamp": snapshot.get("timestamp"),
        "signal_dbm": safe_float(snapshot.get("signal_dbm")),
        "noise_dbm": safe_float(snapshot.get("noise_dbm")),
        "snr_db": safe_float(snapshot.get("snr_db")),
        "tx_rate_mbps": safe_float(snapshot.get("tx_rate_mbps")),
        "ping_latency_ms": safe_float(snapshot.get("ping_latency_ms")),
        "internet_reachable": safe_bool(snapshot.get("internet_reachable")),
        "neighbor_count": safe_float(snapshot.get("neighbor_count")),
        "same_channel_neighbors": safe_float(snapshot.get("same_channel_neighbors")),
        "rf_total_devices": safe_float(snapshot.get("rf_total_devices")),
        "anonymous_devices": safe_float(snapshot.get("anonymous_devices")),
        "bluetooth_device_count": safe_float(snapshot.get("bluetooth_device_count")),
        "channel": safe_float(snapshot.get("channel")),
        "ap_reachable": safe_bool(snapshot.get("ap_reachable")),
        "ap_ping_latency_ms": safe_float(snapshot.get("ap_ping_latency_ms")),
    }


def build_log_index(log_dir, dates):
    entries = []
    history_entries = []
    events = []
    for date in dates:
        path = os.path.join(log_dir, f"wifi_{date}.jsonl")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as fh:
                while True:
                    offset = fh.tell()
                    line = fh.readline()
                    if not line:
                        break
                    try:
                        obj = json.loads(line.decode("utf-8"))
                    except Exception:
                        continue
                    ts = obj.get("timestamp", "")
                    entries.append((ts, path, offset))
                    history_entries.append(snapshot_to_history_entry(obj))
                    for evt in obj.get("events", []):
                        events.append({
                            "timestamp": ts,
                            "type": evt,
                            "detail": "loaded from historical log",
                        })
        except Exception:
            continue
    entries.sort(key=lambda x: x[0])
    history_entries.sort(key=lambda x: x.get("timestamp", ""))
    events.sort(key=lambda x: x.get("timestamp", ""))
    return entries, history_entries, events


def read_snapshot_at(path, offset):
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            line = fh.readline()
        return json.loads(line.decode("utf-8"))
    except Exception:
        return {}


def trim_history_entries(entries, interval_seconds):
    max_items = max(720, int(TWO_DAYS_SECONDS / max(1, interval_seconds)))
    if len(entries) > max_items:
        return entries[-max_items:]
    return entries

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

class MonitorState:
    def __init__(self, interval=DEFAULT_INTERVAL):
        self.lock = threading.Lock()
        self.current = None
        self.prev = None
        self.interval = interval
        self.history = []
        self.line_index = []
        self.events = []
        self.running = True
        self.log_dir = DEFAULT_LOG_DIR

state = MonitorState()


# ---------------------------------------------------------------------------
# Background sampler
# ---------------------------------------------------------------------------

def sampler_loop(interval, log_dir):
    ensure_log_dir(log_dir)
    while state.running:
        snapshot = collect_snapshot()
        events = detect_events(state.prev, snapshot)
        snapshot["events"] = [e["type"] for e in events]

        write_csv_row(snapshot, log_dir)
        json_path = write_json_line(snapshot, log_dir)

        with state.lock:
            state.prev = state.current
            state.current = snapshot
            state.history.append(snapshot_to_history_entry(snapshot))
            if json_path:
                fsize = os.path.getsize(json_path)
                line_bytes = json.dumps(snapshot, ensure_ascii=False).encode("utf-8")
                offset = max(0, fsize - len(line_bytes) - 1)
                state.line_index.append((snapshot.get("timestamp", ""), json_path, offset))
            for ev in events:
                state.events.append({
                    "timestamp": snapshot.get("timestamp"),
                    "type": ev["type"],
                    "detail": ev["detail"],
                })
            max_items = max(720, int(TWO_DAYS_SECONDS / max(1, interval)))
            if len(state.history) > max_items:
                state.history = state.history[-max_items:]
            if len(state.line_index) > max_items:
                state.line_index = state.line_index[-max_items:]
            if len(state.events) > max_items:
                state.events = state.events[-max_items:]

        for _ in range(interval * 10):
            if not state.running:
                break
            time.sleep(0.1)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass
        except ConnectionResetError:
            pass

    def _file_response(self, filepath, content_type):
        try:
            with open(filepath, "rb") as fh:
                body = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass
            except ConnectionResetError:
                pass
        except FileNotFoundError:
            self.send_error(404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/current":
            with state.lock:
                data = state.current or {}
            self._json_response(data)

        elif path == "/api/history":
            with state.lock:
                data = list(state.history)
            self._json_response(data)

        elif path == "/api/snapshots":
            self._json_response({"deprecated": True, "msg": "use /api/view"})

        elif path == "/api/events":
            with state.lock:
                data = list(state.events)
            self._json_response(data)

        elif path == "/api/log-dates":
            data = list_log_dates(state.log_dir)
            self._json_response(data)

        elif path == "/api/timeline":
            with state.lock:
                data = [{"i": i, "t": e[0]}
                        for i, e in enumerate(state.line_index)]
            self._json_response(data)

        elif path == "/api/view":
            qs = parse_qs(parsed.query)
            center = int(qs.get("center", ["-1"])[0])
            window = int(qs.get("window", ["90"])[0])
            with state.lock:
                total = len(state.line_index)
                if center < 0 or center >= total:
                    center = max(0, total - 1)
                lo = max(0, center - window)
                hi = min(total, center + window + 1)
                index_slice = state.line_index[lo:hi]
                hist = state.history[lo:hi]
                evts = [e for e in state.events
                        if index_slice
                        and index_slice[0][0] <= e.get("timestamp", "") <= index_slice[-1][0]]
            snaps = [read_snapshot_at(path, off) for (_, path, off) in index_slice]
            center_rel = center - lo
            cur_snap = snaps[center_rel] if 0 <= center_rel < len(snaps) else {}
            self._json_response({
                "center": center,
                "lo": lo,
                "hi": hi - 1,
                "total": total,
                "current": cur_snap,
                "history": hist,
                "snapshots": snaps,
                "events": evts,
            })

        elif path == "/" or path == "/index.html":
            self._file_response(
                os.path.join(STATIC_DIR, "index.html"),
                "text/html; charset=utf-8",
            )

        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/debug-log":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            import datetime as _dt
            with open("/tmp/wifi_web_debug.log", "a") as fh:
                fh.write(f"[{_dt.datetime.now().strftime('%H:%M:%S.%f')}] {body}\n")
            self._json_response({"ok": True})

        elif parsed.path == "/api/load-logs":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            try:
                req = json.loads(body)
                dates = req.get("dates", [])
            except Exception:
                self._json_response({"error": "bad request"}, 400)
                return
            new_index, new_hist, new_events = build_log_index(state.log_dir, dates)
            with state.lock:
                existing_ts = {e[0] for e in state.line_index}
                added = 0
                for entry, hist_entry, in zip(new_index, new_hist):
                    if entry[0] not in existing_ts:
                        state.line_index.append(entry)
                        state.history.append(hist_entry)
                        added += 1
                for ev in new_events:
                    state.events.append(ev)
                state.line_index.sort(key=lambda x: x[0])
                state.history.sort(key=lambda x: x.get("timestamp", ""))
                state.events.sort(key=lambda x: x.get("timestamp", ""))
            self._json_response({
                "loaded": added,
                "total_in_memory": len(state.line_index),
            })

        else:
            self.send_error(404)

def main():
    parser = argparse.ArgumentParser(description="WiFi Monitor Web UI")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT,
                        help=f"HTTP port (default: {DEFAULT_PORT})")
    parser.add_argument("-i", "--interval", type=int, default=DEFAULT_INTERVAL,
                        help=f"Sampling interval in seconds (default: {DEFAULT_INTERVAL})")
    parser.add_argument("-d", "--log-dir", default=DEFAULT_LOG_DIR,
                        help=f"Log output directory (default: {DEFAULT_LOG_DIR})")
    parser.add_argument("--ap", default=None,
                        help="AP/gateway IP for local ping analysis, e.g. 192.168.1.10")
    args = parser.parse_args()

    monitor_mod.PING_AP = args.ap
    state.log_dir = args.log_dir

    def handle_sig(signum, frame):
        state.running = False

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    scanner_ok = os.path.isfile(SCANNER_PATH)
    native_ok = start_native_scanner()
    print(f"[{now()}] WiFi Monitor Web UI starting")
    print(f"  Port     : {args.port}")
    print(f"  Interval : {args.interval}s")
    print(f"  Log dir  : {args.log_dir}")
    print(f"  AP ping  : {args.ap or 'disabled (use --ap 192.168.1.10)'}")
    print(f"  Native   : {'running' if native_ok else 'not found (run: bash build_scanner_app.sh)'}")
    print(f"  RF scan  : {'enabled' if scanner_ok else 'disabled'}")
    print(f"  Dashboard: http://localhost:{args.port}")

    sampler_thread = threading.Thread(
        target=sampler_loop, args=(args.interval, args.log_dir), daemon=True
    )
    sampler_thread.start()

    server = HTTPServer(("0.0.0.0", args.port), DashboardHandler)
    server.timeout = 1
    print(f"[{now()}] Server running — open http://localhost:{args.port}")

    while state.running:
        server.handle_request()

    print(f"\n[{now()}] Shutting down.")
    stop_native_scanner()
    server.server_close()


if __name__ == "__main__":
    main()
