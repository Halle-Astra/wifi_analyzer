# WiFi 信道监控分析工具

持续采集 WiFi 连接指标并记录到本地日志，用于在网络出问题后回溯分析。

支持 macOS 和 Linux，纯 Python 实现，零第三方依赖。自动检测平台并调用对应的系统接口采集数据。

## 跨平台支持

| 平台 | WiFi 数据来源 | RF 扫描（含隐藏设备） | 蓝牙扫描 |
|------|--------------|----------------------|----------|
| macOS | `system_profiler` | Swift CoreWLAN / WiFiScanner.app | `system_profiler SPBluetoothDataType` |
| Linux | `iw` + nl80211 原生扫描器 | `wifi_scanner`（C, 需编译） | `bluetoothctl scan on` |

运行 `bash build_scanner_app.sh` 会自动检测平台，macOS 编译 Swift App，Linux 编译 C 扫描器并配置权限。

## 工具组成

| 文件 | 用途 |
|------|------|
| `wifi_web.py` | **Web UI 仪表盘** — 包含数据采集 + 浏览器实时监控，一条命令搞定 |
| `wifi_monitor.py` | 命令行监控守护进程 — 纯终端输出 + 日志，不需要浏览器 |
| `wifi_analyzer.py` | 离线分析工具 — 对历史日志进行统计、回溯和可视化 |
| `wifi_platform.py` | 平台抽象层 — 自动检测 macOS/Linux 并调用对应的系统命令 |
| `wifi_scanner.c` | Linux nl80211 原生扫描器源码 — 检测隐藏 SSID / 匿名 RF 设备 |
| `wifi_scanner.swift` | macOS Swift CLI 扫描器源码（CoreWLAN） |
| `native_wifi_scanner_app.swift` | macOS 原生扫描 App 源码（支持定位权限） |
| `build_scanner_app.sh` | 跨平台构建脚本 — 自动检测平台并编译对应的扫描器 |

> **`wifi_web.py` vs `wifi_monitor.py`**：两者都会采集数据并写入日志，区别是 `wifi_web.py` 额外提供浏览器 Web UI。大多数情况下只需要运行 `wifi_web.py` 即可，不需要同时运行 `wifi_monitor.py`。如果不需要 Web UI（如服务器/无头环境），再使用 `wifi_monitor.py`。

## 采集指标

每次采样记录以下信息：

- **连接状态** — 已连接 / 已断开
- **网络信息** — SSID、信道、频段 (2.4G/5G)、带宽 (20/40/80/160MHz)、PHY 模式 (ax/ac/n…)
- **信号质量** — 关联链路 RSSI（当前真实连接质量）、扫描 RSSI（原生扫描器看到的 AP 信号）、噪声底限 (dBm)、信噪比 SNR (dB)
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
| `NOISE_SPIKE` | 噪声底限突升但信号不变，疑似非 WiFi 干扰 |

## 快速开始

### 1. 编译扫描器（推荐）

```bash
bash build_scanner_app.sh
```

脚本会自动检测平台：
- **macOS** — 编译 Swift WiFiScanner.app，首次运行需授权定位权限
- **Linux** — 编译 C nl80211 扫描器，交互式配置 sudoers 免密

不编译也能运行，只是看不到隐藏 SSID / 匿名设备，频谱图信息会少一些。

### 2. 启动监控

**Web UI（推荐，大多数场景用这个就够了）：**

```bash
# 一条命令启动采集 + Web 仪表盘
python3 wifi_web.py

# 同时监控 AP 延迟（强烈推荐）
python3 wifi_web.py --ap 192.168.1.10
```

打开 http://localhost:8800 即可看到实时监控面板。`wifi_web.py` 会同时将数据写入 `logs/` 目录。

> `wifi_web.py` 已经包含了完整的数据采集功能，**不需要**同时运行 `wifi_monitor.py`。

**纯命令行（无头环境/服务器）：**

```bash
# 终端输出 + 日志记录
python3 wifi_monitor.py --ap 192.168.1.10

# 后台静默运行
nohup python3 wifi_monitor.py -q --ap 192.168.1.10 &
```

按 `Ctrl+C` 可优雅停止。

### 3. 分析日志

```bash
python3 wifi_analyzer.py all                           # 全量分析报告
python3 wifi_analyzer.py around "2025-04-13 15:30"     # 回溯某时刻 ±5 分钟
python3 wifi_analyzer.py disconnects                   # 断连事件
python3 wifi_analyzer.py neighbors "2025-04-13 15:30"  # 该时刻各信道上的设备
python3 wifi_analyzer.py channels                      # 信道拥挤度
python3 wifi_analyzer.py signal                        # 信号趋势
```

### macOS 额外步骤

如果希望频谱图中命名网络的功率匹配更准确，建议编译原生 App 并授权定位权限：

```bash
bash build_scanner_app.sh
open WiFiScanner.app
# 前往：系统设置 -> 隐私与安全性 -> 定位服务 -> 允许 WiFi Scanner
```

原生 App 会持续把扫描结果写到 `~/.wifi-monitor/native_scan.json`，`wifi_web.py` 和 `wifi_monitor.py` 会自动优先读取。

### Linux 额外步骤

WiFi 全信道扫描和蓝牙发现需要 root 权限。`build_scanner_app.sh` 会引导你配置免密 sudo，也可以手动执行：

```bash
# 免密 sudo（替换 <user> 为你的用户名）
sudo bash -c 'echo "<user> ALL=(root) NOPASSWD: /usr/sbin/iwlist, /usr/bin/bluetoothctl, /path/to/wifi_scanner" > /etc/sudoers.d/wifi-scan'
```

不配置也能运行，但频谱图只显示当前已连接的 WiFi，蓝牙列表为空。

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

## 关于两个 RSSI 值

你可能会注意到 Dashboard 上显示了两个 RSSI 数值：

- **关联 RSSI**（如 `-44 dBm`）— 来自 `system_profiler`，是驱动层报告的当前连接真实信号强度
- **扫描 RSSI**（如 `扫描 -50`）— 来自原生扫描 App 的主动扫描探测结果

两者相差几 dB 是正常的，因为采样时刻和测量方式不同。关联 RSSI 更能反映当前连接质量，扫描 RSSI 和频谱图里的梯形高度一致。

扫描 RSSI 只在 `WiFiScanner.app` 运行时才有值，App 未运行时只显示关联 RSSI。

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

### macOS
- macOS (使用 `system_profiler SPAirPortDataType` 获取 WiFi 数据)
- Python 3.6+
- 无需安装任何第三方库
- 可选：Xcode Command Line Tools（编译 Swift 扫描器）

### Linux
- Python 3.6+
- `iw`（WiFi 接口信息，`sudo apt install iw`）
- `iwlist`（后备扫描，`sudo apt install wireless-tools`）
- `ping`（通常已预装）
- 可选：`gcc`（编译 nl80211 原生扫描器，`sudo apt install build-essential`）
- 可选：`bluetoothctl`（BLE 设备扫描，`sudo apt install bluez`）
- 无需安装任何 Python 第三方库，无需 libnl

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
