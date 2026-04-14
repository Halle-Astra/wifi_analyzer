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

    def _html_response(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
            self._html_response(DASHBOARD_HTML)

        else:
            self.send_error(404)


# ---------------------------------------------------------------------------
# Dashboard HTML (inline)
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WiFi 实时监控面板</title>
<style>
:root {
  --bg: #0b1020;
  --panel: #121933;
  --panel-2: #182245;
  --text: #e8ecff;
  --muted: #9aa6d1;
  --ok: #21c77a;
  --warn: #ffb020;
  --bad: #ff5d73;
  --line: #2a376b;
  --accent: #68a0ff;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
  background: linear-gradient(180deg, #0b1020, #0f1730);
  color: var(--text);
}
.container { max-width: 1400px; margin: 0 auto; padding: 20px; }
h1 { margin: 0 0 8px; font-size: 28px; }
.sub { color: var(--muted); margin-bottom: 18px; }
.grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: 14px;
}
.card {
  background: rgba(18, 25, 51, 0.95);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 16px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.22);
}
.kpi { grid-column: span 3; }
.wide { grid-column: span 6; }
.full { grid-column: span 12; }
.title { font-size: 13px; color: var(--muted); margin-bottom: 10px; }
.value { font-size: 30px; font-weight: 700; }
.small { font-size: 12px; color: var(--muted); }
.ok { color: var(--ok); }
.warn { color: var(--warn); }
.bad { color: var(--bad); }
.row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 0;
  border-bottom: 1px solid rgba(42, 55, 107, 0.45);
}
.row:last-child { border-bottom: 0; }
.list {
  max-height: 360px;
  overflow: auto;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.02);
}
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.channel {
  border: 1px solid rgba(104, 160, 255, 0.18);
  border-radius: 12px;
  padding: 12px;
  margin-bottom: 12px;
  background: rgba(255, 255, 255, 0.02);
}
.channel.mine { border-color: rgba(33, 199, 122, 0.45); }
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  background: var(--panel-2);
  border: 1px solid var(--line);
  color: var(--muted);
  margin-left: 8px;
}
.dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  margin-right: 6px;
}
.chart {
  width: 100%;
  height: 190px;
  display: block;
  background: rgba(255, 255, 255, 0.02);
  border-radius: 10px;
}
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: end;
  gap: 12px;
  margin-bottom: 16px;
}
@media (max-width: 1100px) {
  .kpi, .wide { grid-column: span 6; }
}
@media (max-width: 760px) {
  .kpi, .wide, .full { grid-column: span 12; }
  .value { font-size: 24px; }
}
</style>
</head>
<body>
<div class="container">
  <div class="header-row">
    <div>
      <h1>WiFi 实时监控面板</h1>
      <div class="sub">实时看连接状态、信道竞争、匿名 RF 设备、蓝牙干扰源</div>
    </div>
    <div class="small">最后更新：<span id="updated">-</span></div>
  </div>

  <div class="grid">
    <div class="card kpi"><div class="title">当前网络</div><div class="value" id="ssid">-</div><div class="small" id="status">-</div></div>
    <div class="card kpi"><div class="title">信道 / 带宽</div><div class="value" id="channel">-</div><div class="small" id="phy">-</div></div>
    <div class="card kpi"><div class="title">信号 / SNR</div><div class="value" id="signal">-</div><div class="small" id="noise">-</div></div>
    <div class="card kpi"><div class="title">网络连通性</div><div class="value" id="internet">-</div><div class="small" id="latency">-</div></div>

    <div class="card wide">
      <div class="title">实时趋势</div>
      <svg id="chart" class="chart" viewBox="0 0 800 190" preserveAspectRatio="none"></svg>
      <div class="small">绿色：RSSI　蓝色：同信道竞争数　红色：匿名设备数</div>
    </div>

    <div class="card wide">
      <div class="title">概览</div>
      <div class="row"><span>可见命名网络</span><strong id="neighborCount">-</strong></div>
      <div class="row"><span>同信道竞争</span><strong id="sameChannel">-</strong></div>
      <div class="row"><span>RF 总设备数</span><strong id="rfTotal">-</strong></div>
      <div class="row"><span>匿名 / 隐藏设备</span><strong id="anonTotal">-</strong></div>
      <div class="row"><span>蓝牙设备数</span><strong id="btTotal">-</strong></div>
      <div class="row"><span>TX 速率</span><strong id="txRate">-</strong></div>
    </div>

    <div class="card wide">
      <div class="title">最近事件</div>
      <div class="list" id="events"></div>
    </div>

    <div class="card wide">
      <div class="title">蓝牙设备（2.4GHz 潜在干扰源）</div>
      <div class="list" id="bluetooth"></div>
    </div>

    <div class="card full">
      <div class="title">各信道设备明细</div>
      <div id="channels"></div>
    </div>
  </div>
</div>

<script>
const els = {
  updated: document.getElementById('updated'),
  ssid: document.getElementById('ssid'),
  status: document.getElementById('status'),
  channel: document.getElementById('channel'),
  phy: document.getElementById('phy'),
  signal: document.getElementById('signal'),
  noise: document.getElementById('noise'),
  internet: document.getElementById('internet'),
  latency: document.getElementById('latency'),
  neighborCount: document.getElementById('neighborCount'),
  sameChannel: document.getElementById('sameChannel'),
  rfTotal: document.getElementById('rfTotal'),
  anonTotal: document.getElementById('anonTotal'),
  btTotal: document.getElementById('btTotal'),
  txRate: document.getElementById('txRate'),
  events: document.getElementById('events'),
  bluetooth: document.getElementById('bluetooth'),
  channels: document.getElementById('channels'),
  chart: document.getElementById('chart'),
};

async function fetchJSON(url) {
  const response = await fetch(url, { cache: 'no-store' });
  return response.json();
}

function statusClass(ok) { return ok ? 'ok' : 'bad'; }
function esc(text) {
  return String(text ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

function renderCurrent(data) {
  els.updated.textContent = data.timestamp || '-';
  els.ssid.textContent = data.ssid || '(未连接)';
  els.status.innerHTML = `<span class="${data.status === 'connected' ? 'ok' : 'bad'}">${esc(data.status || '-')}</span>`;
  els.channel.textContent = data.channel ? `ch ${data.channel}` : '-';
  els.phy.textContent = `${data.band || ''} ${data.width || ''} ${data.phy_mode || ''}`.trim() || '-';
  els.signal.textContent = data.signal_dbm != null ? `${data.signal_dbm} dBm` : '-';
  els.noise.textContent = `noise ${data.noise_dbm ?? '-'} / SNR ${data.snr_db ?? '-'}`;
  els.internet.innerHTML = `<span class="${statusClass(!!data.internet_reachable)}">${data.internet_reachable ? '可达' : '失败'}</span>`;
  els.latency.textContent = data.ping_latency_ms != null ? `${data.ping_latency_ms.toFixed(1)} ms` : '-';
  els.neighborCount.textContent = data.neighbor_count ?? '-';
  els.sameChannel.textContent = data.same_channel_neighbors ?? '-';
  els.rfTotal.textContent = data.rf_total_devices ?? '-';
  els.anonTotal.textContent = data.anonymous_devices ?? '-';
  els.btTotal.textContent = data.bluetooth_device_count ?? '-';
  els.txRate.textContent = data.tx_rate_mbps != null ? `${data.tx_rate_mbps} Mbps` : '-';
}

function renderEvents(items) {
  const recent = items.slice(-20).reverse();
  els.events.innerHTML = recent.length ? recent.map(item =>
    `<div class="row"><span class="mono">${esc(item.timestamp)}</span><strong>${esc(item.type)}</strong><span class="small">${esc(item.detail)}</span></div>`
  ).join('') : '<div class="row"><span>暂无事件</span></div>';
}

function renderBluetooth(devices) {
  els.bluetooth.innerHTML = devices.length ? devices.map(device => {
    const name = esc(device.name || '(unknown)');
    const type = esc(device.type || '');
    const rssi = device.rssi ? ` RSSI=${esc(device.rssi)}` : '';
    const state = device.connected ? '已连接' : '已配对';
    return `<div class="row"><span>${name}</span><span class="small">${state} ${type}${rssi}</span></div>`;
  }).join('') : '<div class="row"><span>未发现蓝牙设备</span></div>';
}

function renderChannels(data) {
  const channelNetworks = data.channel_networks || {};
  const summaries = data.rf_channel_summary || {};
  const myChannel = String(data.channel ?? '');
  const keys = Object.keys(channelNetworks).sort((a, b) => Number(a) - Number(b));
  els.channels.innerHTML = keys.length ? keys.map(key => {
    const networks = channelNetworks[key] || [];
    const summary = summaries[key] || summaries[Number(key)] || {};
    const named = networks.filter(item => !item.anonymous);
    const anonymous = networks.filter(item => item.anonymous);
    const mine = key === myChannel;
    const rssiPart = summary.rssi_min != null ? `RSSI ${summary.rssi_min} ~ ${summary.rssi_max} dBm` : '';
    return `
      <div class="channel ${mine ? 'mine' : ''}">
        <div><strong>信道 ${esc(key)}</strong>${mine ? '<span class="badge">当前信道</span>' : ''}<span class="badge">命名 ${named.length}</span><span class="badge">匿名 ${anonymous.length}</span>${rssiPart ? `<span class="badge">${esc(rssiPart)}</span>` : ''}</div>
        <div class="small" style="margin:10px 0 6px;">${named.concat(anonymous).map(net => {
          const name = net.anonymous ? '(anonymous RF)' : (net.ssid || '(hidden SSID)');
          const meta = [net.band, net.width, net.phy_mode, net.rssi != null ? `RSSI=${net.rssi}` : ''].filter(Boolean).join(' · ');
          return `<div class="row"><span>${esc(name)}</span><span class="small">${esc(meta)}</span></div>`;
        }).join('')}</div>
      </div>`;
  }).join('') : '<div class="row"><span>暂无信道数据</span></div>';
}

function renderChart(history) {
  const svg = els.chart;
  const width = 800, height = 190, pad = 18;
  if (!history.length) {
    svg.innerHTML = '';
    return;
  }
  const xs = history.map((_, index) => pad + (index * (width - pad * 2)) / Math.max(1, history.length - 1));
  const signalVals = history.map(item => item.signal_dbm ?? -100);
  const sameVals = history.map(item => item.same_channel_neighbors ?? 0);
  const anonVals = history.map(item => item.anonymous_devices ?? 0);
  const sigMin = Math.min(...signalVals), sigMax = Math.max(...signalVals);
  const maxCount = Math.max(1, ...sameVals, ...anonVals);
  const scaleSignal = value => height - pad - ((value - sigMin) / Math.max(1, sigMax - sigMin || 1)) * (height - pad * 2);
  const scaleCount = value => height - pad - (value / maxCount) * (height - pad * 2);
  const pathFor = (vals, scaler) => vals.map((v, i) => `${i ? 'L' : 'M'} ${xs[i].toFixed(1)} ${scaler(v).toFixed(1)}`).join(' ');
  const grid = [0.25, 0.5, 0.75].map(r => `<line x1="0" y1="${(height*r).toFixed(1)}" x2="${width}" y2="${(height*r).toFixed(1)}" stroke="rgba(255,255,255,.08)"/>`).join('');
  svg.innerHTML = `${grid}
    <path d="${pathFor(signalVals, scaleSignal)}" fill="none" stroke="#21c77a" stroke-width="3"/>
    <path d="${pathFor(sameVals, scaleCount)}" fill="none" stroke="#68a0ff" stroke-width="2.5"/>
    <path d="${pathFor(anonVals, scaleCount)}" fill="none" stroke="#ff5d73" stroke-width="2.5"/>
  `;
}

async function refresh() {
  try {
    const [current, history, events] = await Promise.all([
      fetchJSON('/api/current'),
      fetchJSON('/api/history'),
      fetchJSON('/api/events'),
    ]);
    renderCurrent(current);
    renderEvents(events);
    renderBluetooth(current.bluetooth_devices || []);
    renderChannels(current);
    renderChart(history);
  } catch (error) {
    console.error(error);
  }
}

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>"""


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
