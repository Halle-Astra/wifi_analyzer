# WiFi 信道监控分析工具

持续采集 WiFi 连接指标并记录到本地日志，用于在网络出问题后回溯分析。

纯 Python 实现，零第三方依赖，仅需 macOS 自带的 `system_profiler` 和 `ping`。可选编译 Swift 扫描器以获取匿名/隐藏设备信息。

## 工具组成

| 文件 | 用途 |
|------|------|
| `wifi_monitor.py` | 监控守护进程 — 按固定间隔采集 WiFi 数据并写入日志 |
| `wifi_web.py` | Web UI 仪表盘 — 浏览器实时查看监控数据 |
| `wifi_analyzer.py` | 分析工具 — 对历史日志进行统计、回溯和可视化 |
| `wifi_scanner.swift` | Swift CLI 扫描器源码 — 检测匿名/隐藏设备及其信号强度 |
| `wifi_scanner` | 编译后的 CLI 扫描器二进制 |
| `native_wifi_scanner_app.swift` | 原生 macOS 扫描 App 源码（支持定位权限） |
| `build_scanner_app.sh` | 构建 `WiFiScanner.app` 的脚本 |
| `Info.plist` | 原生扫描 App 的 bundle 配置和定位权限说明 |

## 采集指标

每次采样记录以下信息：

- **连接状态** — 已连接 / 已断开
- **网络信息** — SSID、信道、频段 (2.4G/5G)、带宽 (20/40/80/160MHz)、PHY 模式 (ax/ac/n…)
- **信号质量** — 信号强度 RSSI (dBm)、噪声底限 (dBm)、信噪比 SNR (dB)
- **传输速率** — TX Rate (Mbps)、MCS Index
- **信道拥挤度** — 周围可见网络总数、同信道竞争网络数
- **信道邻居详情** — 每个信道上具体有哪些网络（SSID、PHY 模式、带宽等）
- **匿名/隐藏设备** — 通过 RF 扫描检测无 SSID 广播的设备，记录其信道和信号强度
- **蓝牙设备** — 扫描周围蓝牙设备（蓝牙使用 2.4GHz 频段，可能造成干扰）
- **互联网连通性** — ping 公网测试结果及延迟 (ms)
- **AP 延迟** — ping AP/网关测试，用于区分无线链路问题和上游网络问题（需 `--ap` 参数）

此外，工具会自动检测以下**事件**：

| 事件类型 | 说明 |
|----------|------|
| `DISCONNECT` | WiFi 断开连接 |
| `RECONNECT` | WiFi 重新连接 |
| `CHANNEL_CHANGE` | 信道发生切换 |
| `INTERNET_LOST` | 互联网不可达 (ping 失败) |
| `INTERNET_RESTORED` | 互联网恢复 |
| `SNR_DROP` | 信噪比骤降 (≥10dB) |
| `WEAK_SIGNAL` | 信号弱于 -75 dBm |
| `SIGNAL_ANOMALY` | 信号异常强 (> -30 dBm) |

## 快速开始

### 编译原生扫描 App（更推荐）

如果你希望尽量拿到 SSID / BSSID，从而让频谱图里的命名网络功率匹配更准确，建议改用原生 App 版本。这样 macOS 才能正常给定位权限：

```bash
bash build_scanner_app.sh
open WiFiScanner.app
```

首次打开后，到：
- `系统设置 -> 隐私与安全性 -> 定位服务`

确认 `WiFi Scanner` 已被允许。

原生 App 会持续把扫描结果写到：

```bash
~/.wifi-monitor/native_scan.json
```

`wifi_web.py` 和 `wifi_monitor.py` 会自动优先读取这个文件。

### 编译 RF 扫描器（推荐）

RF 扫描器能检测到隐藏 SSID 和匿名设备，强烈建议编译：

```bash
swiftc -framework CoreWLAN -framework Foundation wifi_scanner.swift -o wifi_scanner
```

编译后 `wifi_monitor.py` 会自动检测并使用它。不编译也能正常运行，只是看不到匿名设备。

### Web UI 实时仪表盘（推荐）

一条命令启动，浏览器打开即可实时查看所有监控数据：

```bash
# 默认端口 8800，采样间隔 10 秒
python3 wifi_web.py

# 同时监控 AP 延迟（推荐）
python3 wifi_web.py --ap 192.168.1.10

# 自定义端口和采样间隔
python3 wifi_web.py -p 9000 -i 5
```

打开 http://localhost:8800 即可看到：
- 多页面 Web UI：概览 / 信号 / 信道 / 事件
- Dashboard 概览卡片和趋势图
- 带时间横轴和单位纵轴的详细图表，方便分析波动
- AP vs 公网 Ping 对比图（`--ap` 启用时），一眼看出延迟跳变发生在哪一段
- 2.4GHz / 5GHz 频谱梯形图，支持鼠标框选缩放和历史快照对比
- 各信道上的所有设备（命名 + 匿名），当前信道高亮
- 蓝牙设备列表（潜在 2.4GHz 干扰源）
- 事件时间线（断连、信道切换等）
- 历史回溯滑块 + 实时刷新开关，分析历史数据时不被新数据打断

Web UI 同时会将数据写入 `logs/` 目录，关闭后可用 `wifi_analyzer.py` 做离线分析。

### 命令行监控

```bash
# 默认每 10 秒采样，日志写入 ./logs/
python3 wifi_monitor.py

# 同时监控 AP 延迟
python3 wifi_monitor.py --ap 192.168.1.10

# 自定义采样间隔为 5 秒
python3 wifi_monitor.py -i 5

# 指定日志目录
python3 wifi_monitor.py -d ~/wifi-logs

# 静默模式（不输出到终端，只写日志），适合后台长期运行
nohup python3 wifi_monitor.py -q &
```

按 `Ctrl+C` 可优雅停止。

### 分析日志

```bash
# 全量分析报告（推荐首次使用）
python3 wifi_analyzer.py all

# 仅查看断连 / 网络中断事件
python3 wifi_analyzer.py disconnects

# 查看所有检测到的事件时间线
python3 wifi_analyzer.py events

# 查看信道拥挤度分析
python3 wifi_analyzer.py channels

# 查看每个信道上具体有哪些网络（最新一条快照）
python3 wifi_analyzer.py neighbors

# 查看某个时间点附近各信道上都有谁
python3 wifi_analyzer.py neighbors "2025-04-13 15:30" -w 10

# 查看信号强度变化趋势
python3 wifi_analyzer.py signal

# 查看总体统计摘要
python3 wifi_analyzer.py summary
```

### 回溯特定时间点

当网络出问题时，记下大致时间，之后使用 `around` 命令查看该时间前后的详细数据：

```bash
# 查看某个时间点 ±5 分钟的数据
python3 wifi_analyzer.py around "2025-04-13 15:30"

# 扩大窗口到 ±10 分钟
python3 wifi_analyzer.py around "2025-04-13 15:30" -w 10
```

### 按日期范围过滤

```bash
# 只分析某几天的数据
python3 wifi_analyzer.py --from 2025-04-10 --to 2025-04-13 summary

# 组合使用
python3 wifi_analyzer.py --from 2025-04-12 disconnects
```

## 日志格式

日志按天自动轮转，存储在 `logs/` 目录下：

```
logs/
├── wifi_2025-04-13.csv      # 表格数据，可用 Excel / Numbers 打开
└── wifi_2025-04-13.jsonl    # JSON Lines 格式，含完整信道分布详情
```

- **CSV** — 每行一条采样记录，包含所有核心指标，适合导入电子表格做图表
- **JSONL** — 每行一个 JSON 对象，额外包含 `channel_distribution`（各信道上的网络数量）、`channel_networks`（含匿名设备在内的每个网络详细信息）、`rf_channel_summary`（RF 扫描统计）、`bluetooth_devices`（蓝牙设备列表）和 `events` 列表

## 典型排查流程

1. 启动后台监控：`python3 wifi_web.py --ap 192.168.1.10`（或 `nohup python3 wifi_monitor.py --ap 192.168.1.10 -q &`）
2. 正常使用电脑，等问题复现
3. 记录下断网的大致时间
4. 打开信号页查看 AP vs 公网 Ping 对比图，判断延迟跳变发生在哪一段
5. 运行 `python3 wifi_analyzer.py around "断网时间"` 查看当时情况
6. 运行 `python3 wifi_analyzer.py disconnects` 查看所有断连事件的规律
7. 运行 `python3 wifi_analyzer.py neighbors "断网时间"` 查看当时各信道上有哪些网络、匿名设备和蓝牙干扰源
8. 打开信道页频谱梯形图，框选放大干扰区域，结合历史滑块对比不同时间的频谱变化

## 延迟跳变分析

如果你的网络存在周期性延迟跳变（如每隔 15 秒 ping 从 10ms 跳到 100ms+），可以用 `--ap` 参数同时 ping AP 和公网来定位问题所在：

```bash
python3 wifi_web.py --ap 192.168.1.10
```

打开信号页的「AP vs 公网 Ping 对比」图表：

- **两条线同时跳** → 无线链路问题（信道干扰 / AP 负载 / 漫游切换）
- **只有公网跳，AP 平稳** → 上游网络问题（路由器 / 运营商）
- **AP 小幅跳，公网大幅跳** → 无线有轻微问题，上游放大了延迟

结合信道页的频谱梯形图，可以进一步确认是否有同信道干扰源在跳变时段出现。

## 系统要求

- macOS (使用 `system_profiler SPAirPortDataType` 获取 WiFi 数据)
- Python 3.6+
- 无需安装任何第三方库
- 可选：Xcode Command Line Tools（用于编译 Swift RF 扫描器）

## 参数参考

### wifi_web.py

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-p`, `--port` | HTTP 监听端口 | 8800 |
| `-i`, `--interval` | 采样间隔（秒） | 10 |
| `-d`, `--log-dir` | 日志输出目录 | `./logs/` |
| `--ap` | AP/网关 IP，启用本地 ping 对比分析 | 无 |

### wifi_monitor.py

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-i`, `--interval` | 采样间隔（秒） | 10 |
| `-d`, `--log-dir` | 日志输出目录 | `./logs/` |
| `-q`, `--quiet` | 静默模式，不输出到终端 | 关闭 |
| `--ap` | AP/网关 IP，启用本地 ping 对比分析 | 无 |

### wifi_analyzer.py

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-d`, `--log-dir` | 日志目录 | `./logs/` |
| `--from` | 起始日期过滤 (YYYY-MM-DD) | 无 |
| `--to` | 结束日期过滤 (YYYY-MM-DD) | 无 |

**子命令：** `summary` · `events` · `disconnects` · `channels` · `neighbors [时间]` · `signal` · `around <时间>` · `all`
