# 🔍 Network Topology Discovery

[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![vis-network](https://img.shields.io/badge/vis--network-9.x-FF6B6B.svg)](https://visjs.github.io/vis-network/)

局域网二层 + 三层拓扑发现与可视化工具。通过 **ARP**、**ICMP Ping**、**UDP Traceroute** 协议探测子网内所有设备，推断终端-交换机-网关链路关系，提供交互式 Web 界面。

> ✅ **所有功能已实现** — 零特权运行（no root），后台周期扫描，实时 WebSocket 推送。

## ✨ 特性

- 🖥️ **主机发现** — ARP 广播触发 + ICMP Ping 多线程并发扫描，默认 200 并发
- 🛤️ **路由追踪** — 纯 Python UDP Traceroute（`IP_RECVERR` + `MSG_ERRQUEUE`），无需 root
- 🏷️ **MAC 厂商识别** — manuf 库 + 自动缓存 Wireshark OUI 数据库（30 天刷新）
- 🔗 **链路推断** — 终端 → 交换机 → 网关三段链路自动推理（L2_DIRECT / L3_ROUTE）
- 🌐 **交互式 Web** — vis-network 拓扑图，支持拖拽、缩放、双击高亮关联链路
- 📡 **三级扫描深度** — 仅本子网 / 手动指定子网 / 自动发现 traceroute 路由子网
- 📊 **实时仪表盘** — Chart.js 甜甜圈图，设备类型分布 + 在线/离线统计
- 🎨 **双主题** — Grafana 暗色 / UniFi 亮色一键切换（localStorage 记忆）
- 📥 **数据导出** — PNG 拓扑图（1920×1080）+ Excel 双 Sheet 报告（链路清单 + 设备清单）
- 🔄 **后台周期扫描** — 60s 周期，N=5 离线检测阈值，设备状态自动追踪
- 📝 **事件日志** — JSON Lines 格式，WebSocket 实时推送设备上线/下线/拓扑变更
- 🐧 **零特权** — 无需 sudo/root，纯用户态 Linux 运行
- 🔌 **前端直接控制** — 扫描暂停/恢复、立即刷新、深度/子网配置均可在 Web 界面调整

## 🖼️ 界面预览

```
┌────────────────────────────────────────────────────────────────┐
│  🔍 Network Topology Discovery    深度:[▼]  📸PNG 📊XLSX ⏸️ ☀️  │
├──────────┬─────────────────────────┬───────────────────────────┤
│ Dashboard│     Topology Graph      │  Device Details           │
│          │                         │                           │
│ 🟢 42    │   🔷 Gateway            │  IP:    10.15.117.1       │
│ Online   │     │                    │  MAC:   00:11:22:33:44:01 │
│          │   🔶 Switch              │  Vendor:RouterVendor      │
│ 🔴 3     │   ┌──┴──┐                │  Type:  Router            │
│ Offline  │ 🖥️  🖥️   🖥️               │  Status:Online             │
│          │ PC1 PC2 PC3              │  Links: 5 link(s)         │
│ 🔷 1     │                         │                           │
│ Routers  │                         │                           │
│          ├─────────────────────────┴───────────────────────────┤
│          │ 📜 12:00 Device 10.15.117.49 online                  │
└──────────┴──────────────────────────────────────────────────────┘
```

## 🚀 快速开始

### 环境要求

- Linux（已测试 Ubuntu 24.04）
- Python ≥ 3.12
- Conda / Miniconda

### 安装

```bash
# 克隆仓库
git clone https://github.com/William-Connor/NetworkTopologyDiscovery.git
cd NetworkTopologyDiscovery

# 创建 conda 环境
conda create -n netdiscover python=3.12 -y
conda activate netdiscover

# 安装依赖
pip install -r requirements.txt
```

### 运行

```bash
# 启动 Web 服务（首次运行自动检测网卡/子网/网关）
python main.py

# 浏览器访问
# 本机：http://127.0.0.1:8080
# 局域网：http://<your-ip>:8080
```

## ⚙️ 配置

编辑 `config.yaml`（首次运行自动创建默认配置）：

```yaml
network:
  interface: auto          # 自动检测，或手动指定 eno2 等
  ping_timeout: 1.0        # Ping 超时（秒）
  ping_concurrency: 200    # 并发 Ping 进程数
  scan_depth: 1            # 扫描深度：1=本子网  2=手动指定  3=自动发现
  additional_subnets: []   # depth≥2 时额外的目标子网
  max_subnet_count: 5      # depth=3 时最多自动发现的子网数

traceroute:
  max_hops: 30
  timeout: 3.0
  targets:
    - 8.8.8.8
    - 114.114.114.114
    - 1.1.1.1

scheduler:
  interval: 60             # 扫描间隔（秒）
  offline_threshold: 5     # 连续不可达次数阈值

server:
  host: "0.0.0.0"
  port: 8080

theme:
  default: dark            # dark / light

export:
  png_dpi: 150
  png_width: 1920
  png_height: 1080
```

### 扫描深度说明

| 深度 | 行为 | traceroute 目标 | 适用场景 |
|------|------|:---:|---------|
| **1** | 仅扫描本子网 | 3 个 | 单子网环境（默认） |
| **2** | 本子网 + 手动指定子网 | 3 个 | 已知的相邻网段 |
| **3** | 本子网 + traceroute 自动发现 | 8 个 | 未知网络拓扑探索 |

深度和额外子网可在 Web 界面右上角下拉菜单中动态调整，无需重启。

## 📁 项目结构

```
NetworkTopologyDiscovery/
├── main.py                 # FastAPI 入口 + WebSocket
├── config.yaml             # 配置文件
├── requirements.txt        # Python 依赖
├── scanner/                # 扫描模块
│   ├── network_detect.py   #   网卡/IP/子网/网关自动检测
│   ├── ping.py             #   多线程 ICMP Ping 并发扫描
│   ├── traceroute.py       #   UDP Traceroute（MSG_ERRQUEUE）
│   ├── arp.py              #   ARP 缓存读取 + manuf 厂商查询
│   └── scheduler.py        #   周期调度 + 离线检测 + 事件日志
├── topology/               # 拓扑推理模块
│   ├── classifier.py       #   设备分类（终端/交换机/路由器/本机）
│   ├── linker.py           #   链路推断（L2_DIRECT / L3_ROUTE）
│   └── graph_builder.py    #   networkx 图构建 + vis-network JSON + diff
├── export/                 # 导出模块
│   ├── png.py              #   matplotlib 高清拓扑图
│   └── excel.py            #   openpyxl 双 Sheet Excel 报告
├── web/                    # 前端
│   ├── static/
│   │   ├── style.css       #   Grafana/UniFi 双主题 CSS
│   │   └── app.js          #   vis-network 交互 + WebSocket + Chart.js
│   └── templates/
│       └── index.html      #   主页面
├── docs/
│   └── REQUIREMENTS.md     # 需求对齐文档（含架构图、类图、流程图）
├── test_phase2_review.py   # P0/P1 修复的合成数据回归测试
├── data/                   # 运行时缓存（manuf.txt 自动下载）
├── logs/                   # 运行时事件日志
└── output/                 # 导出文件
```

## 🔬 工作原理

### 协议分工

| 协议 | OSI 层 | 用途 | 实现方式 |
|------|--------|------|---------|
| **ARP** | L2 数据链路层 | IP → MAC 映射，厂商识别 | 系统 `ping` 触发内核 ARP → 读 `/proc/net/arp` |
| **ICMP Ping** | L3 网络层 | 主机在线检测 | `subprocess` 调用系统 `ping`，ThreadPoolExecutor 并发 |
| **UDP Traceroute** | L3 网络层 | 路由路径发现 | UDP socket + `IP_RECVERR` + `MSG_ERRQUEUE` |

### 设备分类逻辑

| 类型 | 图标 | 颜色 | 判断依据 |
|------|:---:|------|---------|
| **本机** | ⭐ 星形 | `#4D96FF` 蓝 | 匹配本地 IP |
| **路由器/网关** | 🔷 菱形 | `#FF6B6B` 红 | 默认网关 OR traceroute 中间节点 |
| **交换机** | 🔶 正方形 | `#FFD93D` 黄 | ARP 表中同一 MAC 对应多个 IP；否则插入虚拟交换机 |
| **终端** | 🟢 圆点 | `#6BCB77` 绿 | 其余活跃 IP |
| **远程终端** | 🟣 圆点 | `#C9B1FF` 紫 | 其他子网发现的终端 |

### 链路推断规则

```
[终端] ──(L2_DIRECT)──→ [交换机] ──(L2_DIRECT)──→ [网关/路由器] ──(L3_ROUTE)──→ [上游路由]
                                                                   ──(L3_ROUTE)──→ [远程终端]
```

- 同子网终端与交换机之间为 L2 直连
- 交换机与网关之间为 L2 直连
- 网关到上游路由 / 其他子网终端为 L3 路由

## 📡 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 仪表盘页面 |
| `/api/status` | GET | 扫描器状态 + 图统计 |
| `/api/topology` | GET | vis-network 格式的拓扑 JSON |
| `/api/export/png` | GET | 下载 PNG 拓扑图 |
| `/api/export/xlsx` | GET | 下载 Excel 链路清单 |
| `/ws` | WebSocket | 实时拓扑推送 + 控制指令 |

### WebSocket 指令

| action | 说明 |
|--------|------|
| `pause` / `resume` | 暂停/恢复周期扫描 |
| `scan_now` | 立即触发一次扫描 |
| `get_topology` | 获取当前拓扑 |
| `update_config` | 动态更新扫描深度 / 额外子网 |

## 📦 技术栈

| 组件 | 技术 | 用途 |
|------|------|------|
| 后端 | FastAPI + Uvicorn | Web 服务 + WebSocket |
| 扫描 | Python socket / subprocess | ARP, ICMP, UDP Traceroute |
| 拓扑 | networkx + matplotlib | 图构建 + PNG 渲染 |
| 前端 | vis-network + Chart.js | 交互式拓扑图 + 仪表盘 |
| 导出 | openpyxl | Excel 双 Sheet 报告 |
| 厂商识别 | manuf | MAC OUI 查询 |
| 模板 | Jinja2 | HTML 渲染 |

## 🧪 测试

```bash
# 合成数据回归测试（无需真实网络）
python test_phase2_review.py

# 各模块独立自测
python scanner/ping.py
python scanner/traceroute.py
python scanner/arp.py
python scanner/network_detect.py
python topology/classifier.py
python topology/linker.py
python topology/graph_builder.py
python export/png.py
python export/excel.py
```

## 📝 许可证

MIT © 2026 William Connor
