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
from wifi_monitor import (
    collect_snapshot, detect_events, write_csv_row, write_json_line,
    ensure_log_dir, now, SCANNER_PATH,
)

DEFAULT_PORT = 8800
DEFAULT_INTERVAL = 10
DEFAULT_LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
MAX_HISTORY = 360

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

class MonitorState:
    def __init__(self, max_history=MAX_HISTORY):
        self.lock = threading.Lock()
        self.current = None
        self.prev = None
        self.history = collections.deque(maxlen=max_history)
        self.events = collections.deque(maxlen=200)
        self.running = True

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
        write_json_line(snapshot, log_dir)

        with state.lock:
            state.prev = state.current
            state.current = snapshot
            state.history.append({
                "timestamp": snapshot.get("timestamp"),
                "signal_dbm": snapshot.get("signal_dbm"),
                "noise_dbm": snapshot.get("noise_dbm"),
                "snr_db": snapshot.get("snr_db"),
                "tx_rate_mbps": snapshot.get("tx_rate_mbps"),
                "ping_latency_ms": snapshot.get("ping_latency_ms"),
                "internet_reachable": snapshot.get("internet_reachable"),
                "neighbor_count": snapshot.get("neighbor_count"),
                "same_channel_neighbors": snapshot.get("same_channel_neighbors"),
                "rf_total_devices": snapshot.get("rf_total_devices"),
                "anonymous_devices": snapshot.get("anonymous_devices"),
                "bluetooth_device_count": snapshot.get("bluetooth_device_count"),
                "channel": snapshot.get("channel"),
            })
            for ev in events:
                state.events.append({
                    "timestamp": snapshot.get("timestamp"),
                    "type": ev["type"],
                    "detail": ev["detail"],
                })

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
        self.wfile.write(body)

    def _file_response(self, filepath, content_type):
        try:
            with open(filepath, "rb") as fh:
                body = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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

        elif path == "/api/events":
            with state.lock:
                data = list(state.events)
            self._json_response(data)

        elif path == "/" or path == "/index.html":
            self._file_response(
                os.path.join(STATIC_DIR, "index.html"),
                "text/html; charset=utf-8",
            )

        else:
            self.send_error(404)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="WiFi Monitor Web UI")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT,
                        help=f"HTTP port (default: {DEFAULT_PORT})")
    parser.add_argument("-i", "--interval", type=int, default=DEFAULT_INTERVAL,
                        help=f"Sampling interval in seconds (default: {DEFAULT_INTERVAL})")
    parser.add_argument("-d", "--log-dir", default=DEFAULT_LOG_DIR,
                        help=f"Log output directory (default: {DEFAULT_LOG_DIR})")
    args = parser.parse_args()

    def handle_sig(signum, frame):
        state.running = False

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    scanner_ok = os.path.isfile(SCANNER_PATH)
    print(f"[{now()}] WiFi Monitor Web UI starting")
    print(f"  Port     : {args.port}")
    print(f"  Interval : {args.interval}s")
    print(f"  Log dir  : {args.log_dir}")
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
    server.server_close()


if __name__ == "__main__":
    main()
