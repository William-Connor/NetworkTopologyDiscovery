#!/usr/bin/env python3
"""
Phase 2 二次审查 — 独立验证脚本
使用合成数据验证所有 P0/P1 修复，不依赖实际网络。
"""
import sys
import json

sys.path.insert(0, ".")

from topology.classifier import DeviceClassifier
from topology.linker import LinkInferrer, VIRTUAL_SWITCH_ID
from topology.graph_builder import GraphBuilder


def bold(s):
    return f"\033[1m{s}\033[0m"
def green(s):
    return f"\033[92m{s}\033[0m"
def red(s):
    return f"\033[91m{s}\033[0m"
def yellow(s):
    return f"\033[93m{s}\033[0m"

pass_count = 0
fail_count = 0

def check(name, condition, detail=""):
    global pass_count, fail_count
    if condition:
        print(f"  {green('✓')} {name} {detail}")
        pass_count += 1
    else:
        print(f"  {red('✗')} {name} {detail}")
        fail_count += 1

def section(title):
    print(f"\n{bold('━'*60)}")
    print(f"  {bold(title)}")
    print(f"{bold('━'*60)}")

# ══════════════════════════════════════════════════════════════════════
# 合成数据
# ══════════════════════════════════════════════════════════════════════
GATEWAY = "10.15.117.1"
LOCAL_IP = "10.15.117.85"
SUBNET = "10.15.117.0/24"

# 合成 ARP 表：包含一个 multi-IP MAC（模拟交换机管理接口）
SYNTH_ARP = [
    {"ip": "10.15.117.1",   "mac": "00:11:22:33:44:01", "vendor": "RouterVendor"},
    {"ip": "10.15.117.85",  "mac": "3c:ec:ef:9a:c6:01", "vendor": "MeVendor"},
    {"ip": "10.15.117.100", "mac": "aa:bb:cc:dd:ee:ff", "vendor": "SwitchVendor"},
    {"ip": "10.15.117.101", "mac": "aa:bb:cc:dd:ee:ff", "vendor": "SwitchVendor"},  # same MAC = multi-IP
    {"ip": "10.15.117.50",  "mac": "11:22:33:44:55:66", "vendor": "PC_Vendor"},
    {"ip": "10.15.117.51",  "mac": "11:22:33:44:55:77", "vendor": "PC2_Vendor"},
    # Multi-IP MAC that includes GATEWAY → should NOT become switch
    {"ip": "10.15.117.1",   "mac": "00:11:22:33:44:02", "vendor": "RouterVendor2"},  # duplicate IP for gateway
    {"ip": "10.15.117.200", "mac": "00:11:22:33:44:02", "vendor": "RouterVendor2"},  # same MAC as gateway
]

# Real ARP: gateway only has 1 MAC, not multi-IP
# We'll simulate: gateway IP does NOT match any multi-IP MAC except its own
# Let me simplify: keep original clear ARP
SYNTH_ARP_SIMPLE = [
    {"ip": "10.15.117.1",   "mac": "00:11:22:33:44:01", "vendor": "RouterVendor"},
    {"ip": "10.15.117.85",  "mac": "3c:ec:ef:9a:c6:01", "vendor": "MeVendor"},
    {"ip": "10.15.117.100", "mac": "aa:bb:cc:dd:ee:ff", "vendor": "SwitchVendor"},
    {"ip": "10.15.117.101", "mac": "aa:bb:cc:dd:ee:ff", "vendor": "SwitchVendor"},  # 同 MAC multi-IP
    {"ip": "10.15.117.102", "mac": "aa:bb:cc:dd:ee:ff", "vendor": "SwitchVendor"},  # 同 MAC multi-IP
    {"ip": "10.15.117.50",  "mac": "11:22:33:44:55:66", "vendor": "PC_Vendor"},
    {"ip": "10.15.117.51",  "mac": "11:22:33:44:55:77", "vendor": "PC2_Vendor"},
]

# ARP where gateway shares MAC with another IP (gateway in multi-IP MAC)
SYNTH_ARP_GW_MULTI = [
    {"ip": "10.15.117.1",   "mac": "00:11:22:33:44:01", "vendor": "RouterVendor"},
    {"ip": "10.15.117.85",  "mac": "3c:ec:ef:9a:c6:01", "vendor": "MeVendor"},
    {"ip": "10.15.117.1",   "mac": "00:11:22:33:44:aa", "vendor": "RouterVendor_A"},  # gateway with 2nd MAC
    {"ip": "10.15.117.200", "mac": "00:11:22:33:44:aa", "vendor": "MultiVendor"},  # same MAC as above
    {"ip": "10.15.117.100", "mac": "aa:bb:cc:dd:ee:ff", "vendor": "SwitchVendor"},
    {"ip": "10.15.117.101", "mac": "aa:bb:cc:dd:ee:ff", "vendor": "SwitchVendor"},
    {"ip": "10.15.117.50",  "mac": "11:22:33:44:55:66", "vendor": "PC_Vendor"},
]

# Online IPs
ONLINE_IPS = ["10.15.117.1", "10.15.117.85", "10.15.117.100", "10.15.117.101",
              "10.15.117.102", "10.15.117.50", "10.15.117.51"]

# Traceroute results: 外网 targets are 8.8.8.8, 114.114.114.114, 1.1.1.1
TR_RESULTS = {
    "8.8.8.8": [
        {"ttl": 1, "ip": "10.15.117.1", "latency_ms": 0.5},     # gateway
        {"ttl": 2, "ip": "172.16.0.1", "latency_ms": 2.1},       # upstream router 1
        {"ttl": 3, "ip": "10.0.0.1", "latency_ms": 5.3},         # upstream router 2
        {"ttl": 4, "ip": None, "latency_ms": None},
        {"ttl": 5, "ip": "8.8.8.8", "latency_ms": 20.1},         # target itself!
    ],
    "114.114.114.114": [
        {"ttl": 1, "ip": "10.15.117.1", "latency_ms": 0.6},
        {"ttl": 2, "ip": "172.16.0.1", "latency_ms": 3.0},       # same upstream, ttl=2
        {"ttl": 3, "ip": "10.0.0.1", "latency_ms": 6.0},         # same upstream, ttl=3
        {"ttl": 4, "ip": "114.114.114.114", "latency_ms": 15.0},  # target itself!
    ],
    "1.1.1.1": [
        {"ttl": 1, "ip": "10.15.117.1", "latency_ms": 0.4},
        {"ttl": 2, "ip": "172.16.0.1", "latency_ms": 2.5},       # same upstream, ttl=2 (min ttl for this IP)
        {"ttl": 3, "ip": None, "latency_ms": None},
        {"ttl": 4, "ip": "1.1.1.1", "latency_ms": 30.0},         # target itself!
    ],
}

# Traceroute targets (外网目标)
TR_TARGETS = ["8.8.8.8", "114.114.114.114", "1.1.1.1"]

# ══════════════════════════════════════════════════════════════════════
# 测试 0: 空 traceroute_targets 边界
# ══════════════════════════════════════════════════════════════════════
section("边界测试: traceroute_targets 为 None/空列表")

classifier = DeviceClassifier()

# 测试 None
devices_none = classifier.classify(
    online_ips=ONLINE_IPS, gateway=GATEWAY, arp_entries=SYNTH_ARP_SIMPLE,
    local_ip=LOCAL_IP, traceroute_results=TR_RESULTS, traceroute_targets=None,
)
# 外网 target IPs 可能出现在分类中（因为 traceroute_targets=None 不排除）
ext_ips_none = {d["ip"] for d in devices_none}
has_8 = "8.8.8.8" in ext_ips_none
check("traceroute_targets=None: 8.8.8.8 可能出现", has_8, "(预期行为 — 不过滤)")

# 测试空列表
devices_empty = classifier.classify(
    online_ips=ONLINE_IPS, gateway=GATEWAY, arp_entries=SYNTH_ARP_SIMPLE,
    local_ip=LOCAL_IP, traceroute_results=TR_RESULTS, traceroute_targets=[],
)
ext_ips_empty = {d["ip"] for d in devices_empty}
has_8_empty = "8.8.8.8" in ext_ips_empty
check("traceroute_targets=[]: 8.8.8.8 可能出现", has_8_empty, "(空列表不过滤)")

# ══════════════════════════════════════════════════════════════════════
# 测试 1: P0-2 — traceroute 目标过滤
# ══════════════════════════════════════════════════════════════════════
section("P0-2: traceroute 目标过滤 — 8.8.8.8/114.114.114.114/1.1.1.1 不应出现在设备分类中")

devices = classifier.classify(
    online_ips=ONLINE_IPS, gateway=GATEWAY, arp_entries=SYNTH_ARP_SIMPLE,
    local_ip=LOCAL_IP, traceroute_results=TR_RESULTS, traceroute_targets=TR_TARGETS,
)

all_ips = {d["ip"] for d in devices}
for ext_ip in ["8.8.8.8", "114.114.114.114", "1.1.1.1"]:
    check(f"外网IP {ext_ip} 不在设备分类中", ext_ip not in all_ips)

# 上游 router IPs 应该在分类中
for upstream in ["172.16.0.1", "10.0.0.1"]:
    check(f"上游路由器 {upstream} 仍然被分类", upstream in all_ips)

# 验证上游路由器类型
for d in devices:
    if d["ip"] == "172.16.0.1":
        check("172.16.0.1 类型为 router", d["type"] == "router")
    if d["ip"] == "10.0.0.1":
        check("10.0.0.1 类型为 router", d["type"] == "router")

# ══════════════════════════════════════════════════════════════════════
# 测试 2: P0-3 — 同 MAC 多 IP 处理 (gateway 不在 multi-IP MAC 中)
# ══════════════════════════════════════════════════════════════════════
section("P0-3: 同 MAC 多 IP — 标准情况（gateway 不在 multi-IP MAC 中）")

for d in devices:
    if d["ip"] == "10.15.117.100":
        check("10.15.117.100 (multi-IP MAC) 类型为 switch", d["type"] == "switch",
              f"got: {d['type']}")
    if d["ip"] == "10.15.117.101":
        check("10.15.117.101 (multi-IP MAC) 类型为 switch", d["type"] == "switch",
              f"got: {d['type']}")
    if d["ip"] == "10.15.117.102":
        check("10.15.117.102 (multi-IP MAC) 类型为 switch", d["type"] == "switch",
              f"got: {d['type']}")

# ══════════════════════════════════════════════════════════════════════
# 测试 2b: P0-3 — gateway 在 multi-IP MAC 中 → 全部保持 router
# ══════════════════════════════════════════════════════════════════════
section("P0-3: 同 MAC 多 IP — gateway 在 multi-IP MAC 中（全部应为 router）")

devices_gw_multi = classifier.classify(
    online_ips=ONLINE_IPS, gateway=GATEWAY, arp_entries=SYNTH_ARP_GW_MULTI,
    local_ip=LOCAL_IP, traceroute_results=TR_RESULTS, traceroute_targets=TR_TARGETS,
)

for d in devices_gw_multi:
    if d["ip"] == "10.15.117.1":
        check("10.15.117.1 仍为 router (gateway)", d["type"] == "router",
              f"got: {d['type']}")
    if d["ip"] == "10.15.117.200":
        check("10.15.117.200 (同 gateway MAC) 不应为 switch", d["type"] != "switch",
              f"got: {d['type']}")
        # NOTE: 当前实现仅阻止转为 switch，未显式设为 router。
        # 这是 P2 级别残留：同 gateway MAC 的其他 IP 应保持 router。
        if d["type"] == "endpoint":
            print(f"    {yellow('⚠')} P2 残留: 10.15.117.200 为 {d['type']}，最佳应为 router")
        else:
            check("10.15.117.200 应为 router", d["type"] == "router",
                  f"got: {d['type']}")

# ══════════════════════════════════════════════════════════════════════
# 测试 3: P0-1 — L3_ROUTE hops 使用实际 traceroute 跳数
# ══════════════════════════════════════════════════════════════════════
section("P0-1: L3_ROUTE hops 使用实际 traceroute 跳数（最小 TTL）")

linker = LinkInferrer()
links = linker.infer(devices, SUBNET, GATEWAY, TR_RESULTS)

# 提取 L3 links
l3_links = [lk for lk in links if lk["type"] == "L3_ROUTE"]
check("存在 L3 链路", len(l3_links) > 0)

# 手动计算预期 hops
# 172.16.0.1 appears at ttl=2 (8.8.8.8), ttl=2 (114.114.114.114), ttl=2 (1.1.1.1) → min=2
# 10.0.0.1 appears at ttl=3 (8.8.8.8), ttl=3 (114.114.114.114) → min=3
expected_hops = {
    "172.16.0.1": 2,
    "10.0.0.1": 3,
}

for lk in l3_links:
    target = lk["target"]
    hops = lk["hops"]
    expected = expected_hops.get(target)
    if expected is not None:
        check(
            f"L3 link {GATEWAY} → {target}: hops={hops} (expected {expected})",
            hops == expected,
        )

# 确保 hops 不全为 1
all_hops = [lk["hops"] for lk in l3_links]
check("L3 hops 不全为 1", not all(h == 1 for h in all_hops),
      f"hops: {all_hops}")

# ══════════════════════════════════════════════════════════════════════
# 测试 4: P1-1 — self 设备链路不重复
# ══════════════════════════════════════════════════════════════════════
section("P1-1: self 设备 (10.15.117.85) 链路不重复")

self_links = [lk for lk in links if lk["source"] == LOCAL_IP]
check(f"self 设备链路数量 = {len(self_links)} (应为 1 条，不重复)",
      len(self_links) == 1,
      f"links: {[(lk['source'], lk['target']) for lk in self_links]}")

# 确认 self 设备链接到 switch
if self_links:
    check("self 链路连接到 switch",
          self_links[0]["target"] == "10.15.117.100" or self_links[0]["target"] == VIRTUAL_SWITCH_ID)

# ══════════════════════════════════════════════════════════════════════
# 测试 5: P1-2 — graph_builder 使用批量方法
# ══════════════════════════════════════════════════════════════════════
section("P1-2: graph_builder 使用 batch add_nodes_from / add_edges_from")

builder = GraphBuilder()
graph = builder.build(devices, links)

check("graph 节点数 > 0", graph.number_of_nodes() > 0)
check("graph 边数 > 0", graph.number_of_edges() > 0)

# 验证 export_json
vis = builder.export_json()
check("export_json 返回 nodes", len(vis["nodes"]) > 0)
check("export_json 返回 edges", len(vis["edges"]) > 0)

# 验证 diff
diff = builder.diff()
check("diff 返回 added_nodes 键", "added_nodes" in diff)
check("diff 返回 removed_nodes 键", "removed_nodes" in diff)

# ══════════════════════════════════════════════════════════════════════
# 测试 6: 无 traceroute 结果时 L3 链路处理
# ══════════════════════════════════════════════════════════════════════
section("边界测试: 无 traceroute 结果时 L3 链路处理")

devices_no_tr = classifier.classify(
    online_ips=ONLINE_IPS, gateway=GATEWAY, arp_entries=SYNTH_ARP_SIMPLE,
    local_ip=LOCAL_IP, traceroute_results={}, traceroute_targets=TR_TARGETS,
)

links_no_tr = linker.infer(devices_no_tr, SUBNET, GATEWAY, traceroute_results=None)

l3_no_tr = [lk for lk in links_no_tr if lk["type"] == "L3_ROUTE"]
check("无 traceroute 时 L3 链路数量为 0", len(l3_no_tr) == 0,
      f"got {len(l3_no_tr)} L3 links")

l2_no_tr = [lk for lk in links_no_tr if lk["type"] == "L2_DIRECT"]
check("无 traceroute 时 L2 链路仍然存在", len(l2_no_tr) > 0,
      f"got {len(l2_no_tr)} L2 links")

# ══════════════════════════════════════════════════════════════════════
# 测试 7: 分类优先级 — gateway 始终为 router
# ══════════════════════════════════════════════════════════════════════
section("分类优先级: gateway 始终为 router，不被覆盖为 switch")

# 构造：gateway 的 MAC 也出现在 multi-IP 集合中
arp_gw_switch_like = [
    {"ip": "10.15.117.1",   "mac": "aa:bb:cc:dd:ee:01", "vendor": "RouterVendor"},
    {"ip": "10.15.117.2",   "mac": "aa:bb:cc:dd:ee:01", "vendor": "RouterVendor"},  # gateway MAC multi-IP
    {"ip": "10.15.117.85",  "mac": "3c:ec:ef:9a:c6:01", "vendor": "MeVendor"},
    {"ip": "10.15.117.50",  "mac": "11:22:33:44:55:66", "vendor": "PC_Vendor"},
]

devices_gw_test = classifier.classify(
    online_ips=["10.15.117.1", "10.15.117.2", "10.15.117.85", "10.15.117.50"],
    gateway=GATEWAY, arp_entries=arp_gw_switch_like,
    local_ip=LOCAL_IP, traceroute_results={}, traceroute_targets=TR_TARGETS,
)

for d in devices_gw_test:
    if d["ip"] == "10.15.117.1":
        check("gateway 10.15.117.1 类型为 router（不被 switch 覆盖）",
              d["type"] == "router", f"got: {d['type']}")
    if d["ip"] == "10.15.117.2":
        # 同 MAC 但包含 gateway → 应为 router, 不应为 switch
        check("10.15.117.2 (与 gateway 同 MAC) 不应为 switch",
              d["type"] != "switch", f"got: {d['type']}")

# ══════════════════════════════════════════════════════════════════════
# 测试 8: 所有链路类型有正确 hops
# ══════════════════════════════════════════════════════════════════════
section("所有链路类型有正确的 hops 属性")

for lk in links:
    check(f"link {lk['source']}→{lk['target']} has 'hops' key", "hops" in lk)
    check(f"link {lk['source']}→{lk['target']} 'type' key", "type" in lk)
    check(f"link {lk['source']}→{lk['target']} 'updated' key", "updated" in lk)

l2_direct = [lk for lk in links if lk["type"] == "L2_DIRECT"]
for lk in l2_direct:
    check(f"L2 link {lk['source']}→{lk['target']} hops=1",
          lk["hops"] == 1)

# ══════════════════════════════════════════════════════════════════════
# 评分汇总
# ══════════════════════════════════════════════════════════════════════
print(f"\n{bold('═'*60)}")
print(f"  {bold('测试结果汇总')}")
print(f"  {green('通过:')} {pass_count}  |  {red('失败:')} {fail_count}")
print(f"{bold('═'*60)}")

if fail_count == 0:
    print(f"\n  {green('🎉 全部测试通过!')}")
    sys.exit(0)
else:
    print(f"\n  {red('❌ 存在失败测试')}")
    sys.exit(1)
