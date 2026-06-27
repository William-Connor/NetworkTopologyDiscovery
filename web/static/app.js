/* ================================================================
   Network Topology Discovery — Frontend Application
   Pure JavaScript, no framework. vis-network + Chart.js + WebSocket.
   ================================================================ */

(function () {
    "use strict";

    // ── Global State ────────────────────────────────────────────────
    const state = {
        nodes: new vis.DataSet([]),
        edges: new vis.DataSet([]),
        connected: false,
        scanning: true,
        stats: { online: 0, offline: 0, routers: 0, switches: 0, endpoints: 0 },
        ws: null,
        reconnectTimer: null,
        reconnectDelay: 1000,
        maxReconnectDelay: 30000,
        logLines: 0,
        maxLogLines: 50,
        activeScanStart: null,
        selectedNodeId: null,
        highlightActive: false,
        isInitialLoad: true,
        scanDepth: 1,
        additionalSubnets: [],
    };

    // ── vis-network Configuration ───────────────────────────────────
    const networkOptions = {
        physics: {
            enabled: true,
            stabilization: {
                iterations: 200,
                updateInterval: 25,
            },
            solver: "forceAtlas2Based",
            forceAtlas2Based: {
                gravitationalConstant: -40,
                centralGravity: 0.005,
                springLength: 200,
                springConstant: 0.08,
                damping: 0.4,
            },
        },
        interaction: {
            dragNodes: true,
            dragView: true,
            zoomView: true,
            hover: true,
            tooltipDelay: 200,
        },
        nodes: {
            font: {
                color: "#E4E7EB",
                size: 13,
                face: "Inter, sans-serif",
                strokeWidth: 2,
                strokeColor: "rgba(11, 15, 25, 0.8)",
            },
            borderWidth: 2,
            borderWidthSelected: 3,
            shadow: {
                enabled: true,
                color: "rgba(0,0,0,0.3)",
                size: 8,
            },
            shapeProperties: {
                interpolation: false,
            },
            scaling: {
                label: { enabled: true, min: 10, max: 16 },
            },
        },
        edges: {
            smooth: {
                type: "continuous",
                roundness: 0.5,
            },
            width: 2,
            selectionWidth: 3,
            hoverWidth: 1,
            font: {
                size: 10,
                color: "#9CA3AF",
                align: "top",
            },
            arrows: {
                to: { scaleFactor: 0.8 },
            },
        },
        layout: {
            improvedLayout: true,
            clusterThreshold: 150,
        },
        groups: {
            self: {
                shape: "star",
                color: { background: "#4D96FF", border: "#3A7BE0" },
                borderWidth: 3,
                size: 30,
            },
            router: {
                shape: "diamond",
                color: { background: "#FF6B6B", border: "#E05555" },
                size: 28,
            },
            switch: {
                shape: "square",
                color: { background: "#FFD93D", border: "#E5C235" },
                size: 26,
            },
            endpoint: {
                shape: "dot",
                color: { background: "#6BCB77", border: "#5AAD65" },
                size: 22,
            },
        },
    };

    // ── DOM References ───────────────────────────────────────────────
    const $ = (sel) => document.querySelector(sel);
    const el = {
        // Progress
        progressBar: $("#progress-bar-fill"),
        // Top bar
        connectionDot: $("#connection-dot"),
        scanBadge: $("#scan-badge"),
        btnToggleScan: $("#btn-toggle-scan"),
        btnScanIcon: $("#btn-scan-icon"),
        btnScanLabel: $("#btn-scan-label"),
        btnRefreshNow: $("#btn-refresh-now"),
        btnToggleTheme: $("#btn-toggle-theme"),
        themeIcon: $("#theme-icon"),
        btnExportPng: $("#btn-export-png"),
        btnExportXlsx: $("#btn-export-xlsx"),
        // Stats
        statOnline: $("#stat-online"),
        statOffline: $("#stat-offline"),
        statRouters: $("#stat-routers"),
        statSwitches: $("#stat-switches"),
        statEndpoints: $("#stat-endpoints"),
        // Graph
        graphPanel: $("#network-graph"),
        graphEmpty: $("#graph-empty"),
        // Device detail
        deviceEmpty: $("#device-detail-empty"),
        deviceContent: $("#device-detail-content"),
        detIp: $("#det-ip"),
        detMac: $("#det-mac"),
        detVendor: $("#det-vendor"),
        detType: $("#det-type"),
        detStatus: $("#det-status"),
        detVirtual: $("#det-virtual"),
        detLinks: $("#det-links"),
        detLastSeen: $("#det-last-seen"),
        // Log
        logStream: $("#log-stream"),
        btnClearLog: $("#btn-clear-log"),
        // Download
        downloadFrame: $("#download-frame"),
        // Scan depth
        scanDepth: $("#scanDepth"),
        subnetInputArea: $("#subnetInput"),
        subnetInputField: $("#subnetInputField"),
        addSubnet: $("#addSubnet"),
        subnetList: $("#subnetList"),
    };

    // ── Create vis-network Instance ──────────────────────────────────
    const container = el.graphPanel;
    const networkData = { nodes: state.nodes, edges: state.edges };
    const network = new vis.Network(container, networkData, networkOptions);

    // ── Initialize Chart.js ──────────────────────────────────────────
    let typeChart = null;
    function initChart() {
        try {
            const ctx = $("#type-chart");
            if (!ctx) return;
            const isDark = document.documentElement.getAttribute("data-theme") === "dark";
            typeChart = new Chart(ctx, {
            type: "doughnut",
            data: {
                labels: ["Routers", "Switches", "Endpoints", "Self"],
                datasets: [
                    {
                        data: [0, 0, 0, 0],
                        backgroundColor: [
                            "#FF6B6B",
                            "#FFD93D",
                            "#6BCB77",
                            "#4D96FF",
                        ],
                        borderColor: isDark ? "#1A1F2E" : "#FFFFFF",
                        borderWidth: 3,
                        hoverBorderColor: isDark ? "#232A3B" : "#F0F0F0",
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                cutout: "65%",
                plugins: {
                    legend: {
                        position: "bottom",
                        labels: {
                            padding: 12,
                            usePointStyle: true,
                            pointStyleWidth: 8,
                            font: { family: "Inter, sans-serif", size: 11 },
                            color: isDark ? "#9CA3AF" : "#6B7280",
                        },
                    },
                    tooltip: {
                        backgroundColor: isDark ? "#1A1F2E" : "#FFFFFF",
                        titleColor: isDark ? "#E4E7EB" : "#1F2937",
                        bodyColor: isDark ? "#9CA3AF" : "#4B5563",
                        borderColor: isDark ? "rgba(255,255,255,0.1)" : "rgba(0,0,0,0.1)",
                        borderWidth: 1,
                        cornerRadius: 6,
                    },
                },
                },
            });
        } catch (err) {
            console.error("Chart.js init failed:", err);
        }
    }

    // ── Theme ────────────────────────────────────────────────────────
    function initTheme() {
        let saved = "dark";
        try {
            saved = localStorage.getItem("ntd-theme") || "dark";
        } catch (e) {
            // localStorage unavailable (private browsing, etc.)
        }
        applyTheme(saved);
    }

    function applyTheme(theme) {
        document.documentElement.setAttribute("data-theme", theme);
        try {
            localStorage.setItem("ntd-theme", theme);
        } catch (e) {
            // localStorage unavailable
        }
        el.themeIcon.textContent = theme === "dark" ? "☀️" : "🌙";
        // Update vis-network font color
        const fontColor = theme === "dark" ? "#E4E7EB" : "#1F2937";
        state.nodes.forEach((n) => {
            state.nodes.update({ id: n.id, font: { color: fontColor } });
        });
        // Update chart colors if it exists
        if (typeChart) {
            const isDark = theme === "dark";
            typeChart.options.plugins.legend.labels.color = isDark ? "#9CA3AF" : "#6B7280";
            typeChart.options.plugins.tooltip.backgroundColor = isDark ? "#1A1F2E" : "#FFFFFF";
            typeChart.options.plugins.tooltip.titleColor = isDark ? "#E4E7EB" : "#1F2937";
            typeChart.options.plugins.tooltip.bodyColor = isDark ? "#9CA3AF" : "#4B5563";
            typeChart.data.datasets[0].borderColor = isDark ? "#1A1F2E" : "#FFFFFF";
            typeChart.update();
        }
    }

    function toggleTheme() {
        const current = document.documentElement.getAttribute("data-theme");
        applyTheme(current === "dark" ? "light" : "dark");
    }

    // ── WebSocket ────────────────────────────────────────────────────
    function connectWS() {
        if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        try {
            state.ws = new WebSocket(wsUrl);
        } catch (err) {
            console.error("WebSocket creation failed:", err);
            scheduleReconnect();
            return;
        }

        state.ws.onopen = () => {
            state.connected = true;
            state.reconnectDelay = 1000;
            el.connectionDot.classList.remove("disconnected");
            el.connectionDot.classList.add("connected");
            addLogEntry({ level: "success", message: "WebSocket connected" });
            // Request current topology
            state.ws.send(JSON.stringify({ action: "get_topology" }));
        };

        state.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleMessage(data);
            } catch (err) {
                console.error("Failed to parse WS message:", err);
            }
        };

        state.ws.onclose = (event) => {
            state.connected = false;
            el.connectionDot.classList.remove("connected");
            el.connectionDot.classList.add("disconnected");
            if (!event.wasClean) {
                addLogEntry({ level: "warn", message: `WebSocket disconnected (code: ${event.code})` });
            }
            scheduleReconnect();
        };

        state.ws.onerror = (err) => {
            console.error("WebSocket error:", err);
        };
    }

    function scheduleReconnect() {
        if (state.reconnectTimer) return;
        state.reconnectTimer = setTimeout(() => {
            state.reconnectTimer = null;
            state.reconnectDelay = Math.min(state.reconnectDelay * 1.5, state.maxReconnectDelay);
            addLogEntry({ level: "info", message: "Reconnecting..." });
            connectWS();
        }, state.reconnectDelay);
    }

    function sendControl(action) {
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({ action }));
        }
    }

    // ── Message Handler ──────────────────────────────────────────────
    function handleMessage(data) {
        const msgType = data.type;

        if (msgType === "topology_update") {
            handleTopologyUpdate(data);
            el.graphEmpty.style.display = "none";
        } else if (msgType === "config") {
            // Server-sent current config
            const cfg = data.data || data;
            if (cfg.scan_depth !== undefined) {
                state.scanDepth = cfg.scan_depth;
                if (el.scanDepth) el.scanDepth.value = String(cfg.scan_depth);
                showSubnetInput(cfg.scan_depth >= 2);
            }
            if (cfg.additional_subnets !== undefined) {
                state.additionalSubnets = cfg.additional_subnets;
                renderSubnetList();
            }
            saveScanConfig();
        } else if (msgType === "device_online") {
            setNodeStatus(data.ip, "online");
            addLogEntry({ level: "device_online", message: `Device ${data.ip} online` });
            updateStatsFromGraph();
        } else if (msgType === "device_offline") {
            setNodeStatus(data.ip, "offline");
            addLogEntry({ level: "device_offline", message: `Device ${data.ip} offline (missed ${data.consecutive_failures || "N"} times)` });
            updateStatsFromGraph();
        } else if (msgType === "device_discovered") {
            addLogEntry({ level: "device_discovered", message: `New device discovered: ${data.ip}` });
        } else if (msgType === "topology_changed") {
            addLogEntry({ level: "info", message: "Topology changed: " + JSON.stringify(data.changes || data) });
        } else if (msgType === "progress") {
            handleProgress(data);
        } else if (msgType === "scan_complete") {
            handleScanComplete(data);
        } else if (msgType === "scan_start") {
            handleScanStart(data);
        } else {
            // Generic log
            addLogEntry({ level: data.level || "info", message: data.event || data.type || JSON.stringify(data) });
        }
    }

    // ── Topology Update ──────────────────────────────────────────────
    function handleTopologyUpdate(msg) {
        // Server wraps topology in {type, data: {nodes, edges}}
        const data = msg.data || msg;
        const { nodes: newNodeList, edges: newEdgeList } = data;

        if (newNodeList) {
            // Merge: update existing, add new, remove stale
            const incomingIds = new Set(newNodeList.map((n) => String(n.id)));

            // Remove nodes no longer present
            const toRemove = [];
            state.nodes.forEach((n) => {
                if (!incomingIds.has(String(n.id))) {
                    toRemove.push(n.id);
                }
            });
            if (toRemove.length > 0) state.nodes.remove(toRemove);

            // Upsert incoming nodes
            const fontColor = document.documentElement.getAttribute("data-theme") === "dark" ? "#E4E7EB" : "#1F2937";
            newNodeList.forEach((n) => {
                const id = String(n.id);
                const existing = state.nodes.get(id);
                const nodeObj = {
                    id,
                    label: n.label || id,
                    group: n.group || "endpoint",
                    shape: n.shape || "dot",
                    ip: n.ip || id,
                    mac: n.mac || "",
                    vendor: n.vendor || "Unknown",
                    isVirtual: n.is_virtual || false,
                    status: n.status || (n.color ? "online" : "offline"),
                    font: { color: fontColor },
                };

                if (!existing) {
                    state.nodes.add(nodeObj);
                } else {
                    state.nodes.update(nodeObj);
                }
            });
        }

        if (newEdgeList) {
            const incomingEdgeKeys = new Set(
                newEdgeList.map((e) => `${e.from}|||${e.to}`)
            );

            // Remove stale edges
            const edgesToRemove = [];
            state.edges.forEach((e) => {
                const key = `${e.from}|||${e.to}`;
                if (!incomingEdgeKeys.has(key)) {
                    edgesToRemove.push(e.id);
                }
            });
            if (edgesToRemove.length > 0) state.edges.remove(edgesToRemove);

            // Upsert incoming edges
            newEdgeList.forEach((e) => {
                const edgeId = `${e.from}|||${e.to}`;
                const existing = state.edges.get(edgeId);
                const edgeObj = {
                    id: edgeId,
                    from: String(e.from),
                    to: String(e.to),
                    arrows: e.arrows || "none",
                    label: e.label || "",
                    color: e.color || { color: "#6BCB77" },
                    title: e.label ? `Type: ${e.label}` : "",
                };

                if (!existing) {
                    state.edges.add(edgeObj);
                } else {
                    state.edges.update(edgeObj);
                }
            });
        }

        updateStatsFromGraph();
        if (state.isInitialLoad) {
            state.isInitialLoad = false;
            network.fit({ animation: { duration: 600, easingFunction: "easeInOutQuad" } });
        }
    }

    // ── Node Status ──────────────────────────────────────────────────
    function setNodeStatus(ip, status) {
        const node = state.nodes.get(ip);
        if (!node) return;

        if (status === "online") {
            // Restore original group color
            const groupDef = networkOptions.groups[node.group];
            if (groupDef && groupDef.color) {
                state.nodes.update({
                    id: ip,
                    status: "online",
                    color: {
                        background: groupDef.color.background,
                        border: groupDef.color.border,
                    },
                });
            } else {
                state.nodes.update({ id: ip, status: "online" });
            }
        } else {
            // Grey out offline nodes
            state.nodes.update({
                id: ip,
                status: "offline",
                color: {
                    background: "#6B7280",
                    border: "#4B5563",
                },
            });
        }

        // Refresh device panel if this node is selected
        if (state.selectedNodeId === ip) {
            const updated = state.nodes.get(ip);
            if (updated) updateDevicePanel(updated);
        }
    }

    // ── Device Detail Panel ──────────────────────────────────────────
    function updateDevicePanel(nodeData) {
        if (!nodeData) return;

        el.deviceEmpty.style.display = "none";
        el.deviceContent.style.display = "flex";
        // Re-trigger animation
        el.deviceContent.style.animation = "none";
        void el.deviceContent.offsetWidth;
        el.deviceContent.style.animation = "slideInRight 0.3s ease";

        el.detIp.textContent = nodeData.ip || "—";
        el.detMac.textContent = nodeData.mac || "—";
        el.detVendor.textContent = nodeData.vendor || "Unknown";
        el.detType.textContent = capitalize(nodeData.group || "endpoint");
        el.detVirtual.textContent = nodeData.isVirtual ? "Yes" : "No";

        // Status with class
        el.detStatus.textContent = capitalize(nodeData.status || "unknown");
        el.detStatus.className = "field-value " + (nodeData.status || "");

        // Count connected links
        const connectedEdges = network.getConnectedEdges(nodeData.id);
        const linkCount = connectedEdges ? connectedEdges.length : 0;
        el.detLinks.textContent = linkCount > 0 ? `${linkCount} link(s)` : "None";

        el.detLastSeen.textContent = new Date().toLocaleTimeString();
    }

    function clearDevicePanel() {
        el.deviceEmpty.style.display = "flex";
        el.deviceContent.style.display = "none";
        state.selectedNodeId = null;
    }

    // ── Stats Update ─────────────────────────────────────────────────
    function updateStatsFromGraph() {
        let online = 0, offline = 0, routers = 0, switches = 0, endpoints = 0;

        state.nodes.forEach((n) => {
            const s = n.status || "online";
            if (s === "offline") offline++; else online++;

            const g = n.group;
            if (g === "router") routers++;
            else if (g === "switch") switches++;
            else if (g === "endpoint" || g === "self") endpoints++;
        });

        if (offline === 0) {
            // Count virtual/unknown nodes as "processed" rather than offline
            // Already handled above
        }

        state.stats = { online, offline, routers, switches, endpoints };
        renderStats();
    }

    function renderStats() {
        const { online, offline, routers, switches, endpoints } = state.stats;
        el.statOnline.textContent = online;
        el.statOffline.textContent = offline;
        el.statRouters.textContent = routers;
        el.statSwitches.textContent = switches;
        el.statEndpoints.textContent = endpoints;

        // Update donut chart
        if (typeChart) {
            typeChart.data.datasets[0].data = [routers, switches, endpoints - (endpoints > 0 ? 1 : 0), endpoints > 0 ? 1 : 0];
            typeChart.update();
        }
    }

    // ── Progress Handling ────────────────────────────────────────────
    function handleProgress(data) {
        const { phase, message, alive_count, arp_count, total_hops } = data;
        let percent = 0;

        if (phase === "ping") percent = 30;
        else if (phase === "arp") percent = 60;
        else if (phase === "traceroute") percent = 90;
        else percent = data.percent || 50;

        updateProgress(percent, message || `Phase: ${phase}`);
    }

    function updateProgress(percent, message) {
        el.progressBar.style.width = `${percent}%`;
        el.progressBar.classList.remove("complete");
        if (percent >= 100) {
            el.progressBar.classList.add("complete");
        }
    }

    function handleScanComplete(data) {
        updateProgress(100, "Scan complete");
        // Fade progress after a moment
        setTimeout(() => {
            el.progressBar.style.width = "0%";
            el.progressBar.classList.add("complete");
        }, 1500);

        // Restore refresh-now button
        if (el.btnRefreshNow) {
            el.btnRefreshNow.disabled = false;
            el.btnRefreshNow.textContent = "🔄 立即刷新";
        }

        addLogEntry({
            level: "scan_complete",
            message: `Scan complete: ${data.alive_hosts || "?"} online, ${data.duration_seconds || "?"}s`,
        });
    }

    function handleScanStart(data) {
        updateProgress(5, "Scan starting...");
        state.activeScanStart = Date.now();
        addLogEntry({ level: "scan_start", message: `Scan started: ${data.subnet || "auto"}` });
        updateScanBadge();
    }

    // ── Scan Badge ───────────────────────────────────────────────────
    function updateScanBadge() {
        if (state.scanning) {
            el.scanBadge.textContent = "Scanning";
            el.scanBadge.className = "scan-badge scanning";
        } else {
            el.scanBadge.textContent = "Paused";
            el.scanBadge.className = "scan-badge paused";
        }
    }

    // ── Event Log ────────────────────────────────────────────────────
    function addLogEntry(entry) {
        const now = new Date();
        const timeStr = now.toLocaleTimeString("en-US", { hour12: false });

        let levelClass = "log-info";
        let icon = "";

        switch (entry.level) {
            case "device_online":
                levelClass = "log-device-online";
                icon = "🟢";
                break;
            case "device_offline":
                levelClass = "log-device-offline";
                icon = "🔴";
                break;
            case "device_discovered":
                levelClass = "log-device-discovered";
                icon = "✨";
                break;
            case "scan_start":
                levelClass = "log-scan-start";
                icon = "▶️";
                break;
            case "scan_complete":
                levelClass = "log-scan-complete";
                icon = "✅";
                break;
            case "progress":
                levelClass = "log-progress";
                icon = "⏳";
                break;
            case "success":
                levelClass = "log-success";
                icon = "✅";
                break;
            case "warn":
            case "warning":
                levelClass = "log-warn";
                icon = "⚠️";
                break;
            case "error":
                levelClass = "log-error";
                icon = "❌";
                break;
            default:
                levelClass = "log-info";
                icon = "ℹ️";
        }

        const line = document.createElement("div");
        line.className = `log-entry ${levelClass}`;
        line.innerHTML = `<span class="log-time">${timeStr}</span>${icon} ${escapeHtml(entry.message)}`;

        el.logStream.appendChild(line);

        // Trim to max lines
        state.logLines++;
        while (el.logStream.children.length > state.maxLogLines) {
            el.logStream.removeChild(el.logStream.firstChild);
        }

        // Auto-scroll to bottom
        el.logStream.scrollTop = el.logStream.scrollHeight;
    }

    function escapeHtml(str) {
        const div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }

    function capitalize(str) {
        if (!str) return "";
        return str.charAt(0).toUpperCase() + str.slice(1);
    }

    // ── Export ───────────────────────────────────────────────────────
    function exportPNG() {
        fetch("/api/export/png")
            .then((res) => {
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                return res.blob();
            })
            .then((blob) => {
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `topology_${formatDate()}.png`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                addLogEntry({ level: "success", message: "PNG exported" });
            })
            .catch((err) => {
                addLogEntry({ level: "error", message: `PNG export failed: ${err.message}` });
            });
    }

    function exportXLSX() {
        fetch("/api/export/xlsx")
            .then((res) => {
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                return res.blob();
            })
            .then((blob) => {
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `links_${formatDate()}.xlsx`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                addLogEntry({ level: "success", message: "XLSX exported" });
            })
            .catch((err) => {
                addLogEntry({ level: "error", message: `XLSX export failed: ${err.message}` });
            });
    }

    function formatDate() {
        const d = new Date();
        const pad = (n) => String(n).padStart(2, "0");
        return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}`;
    }

    // ── Scan Toggle ──────────────────────────────────────────────────
    function toggleScan() {
        state.scanning = !state.scanning;
        if (state.scanning) {
            sendControl("resume");
            el.btnScanIcon.textContent = "⏸️";
            el.btnScanLabel.textContent = "Pause";
        } else {
            sendControl("pause");
            el.btnScanIcon.textContent = "▶️";
            el.btnScanLabel.textContent = "Resume";
        }
        updateScanBadge();
    }

    // ── Scan Depth Controls ─────────────────────────────────────────
    function loadScanConfig() {
        try {
            const saved = JSON.parse(localStorage.getItem("ntd-scan-config"));
            if (saved) {
                state.scanDepth = saved.scanDepth || 1;
                state.additionalSubnets = saved.additionalSubnets || [];
            }
        } catch (e) {
            // localStorage unavailable or corrupted
        }
        if (el.scanDepth) el.scanDepth.value = String(state.scanDepth);
        if (state.scanDepth >= 2) {
            showSubnetInput(true);
        }
        renderSubnetList();
    }

    function saveScanConfig() {
        try {
            localStorage.setItem("ntd-scan-config", JSON.stringify({
                scanDepth: state.scanDepth,
                additionalSubnets: state.additionalSubnets,
            }));
        } catch (e) {
            // localStorage unavailable
        }
    }

    function showSubnetInput(visible) {
        if (el.subnetInputArea) {
            el.subnetInputArea.style.display = visible ? "flex" : "none";
        }
    }

    function renderSubnetList() {
        if (!el.subnetList) return;
        el.subnetList.innerHTML = "";
        state.additionalSubnets.forEach((sn) => {
            const li = document.createElement("li");
            li.className = "subnet-tag";
            li.innerHTML = `<span class="subnet-tag-text">${escapeHtml(sn)}</span>` +
                `<button class="subnet-tag-remove" data-subnet="${escapeHtml(sn)}" title="移除">×</button>`;
            el.subnetList.appendChild(li);
        });
        // bind remove handlers
        el.subnetList.querySelectorAll(".subnet-tag-remove").forEach((btn) => {
            btn.addEventListener("click", () => {
                const sn = btn.getAttribute("data-subnet");
                removeSubnet(sn);
            });
        });
    }

    function addSubnet(subnet) {
        if (!subnet) return;
        // Basic validation: should look like x.x.x.x/24
        const subnetRegex = /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\/\d{1,2}$/;
        if (!subnetRegex.test(subnet)) {
            addLogEntry({ level: "warn", message: `Invalid subnet format: ${subnet}` });
            return;
        }
        if (!state.additionalSubnets.includes(subnet)) {
            state.additionalSubnets.push(subnet);
            saveScanConfig();
            sendConfigUpdate();
            renderSubnetList();
            addLogEntry({ level: "success", message: `Subnet added: ${subnet}` });
        }
    }

    function removeSubnet(subnet) {
        const idx = state.additionalSubnets.indexOf(subnet);
        if (idx >= 0) {
            state.additionalSubnets.splice(idx, 1);
            saveScanConfig();
            sendConfigUpdate();
            renderSubnetList();
            addLogEntry({ level: "info", message: `Subnet removed: ${subnet}` });
        }
    }

    function sendConfigUpdate() {
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({
                action: "update_config",
                config: {
                    scan_depth: state.scanDepth,
                    additional_subnets: state.additionalSubnets,
                },
            }));
        }
    }

    function onScanDepthChange() {
        const newDepth = parseInt(el.scanDepth.value, 10);
        if (isNaN(newDepth) || newDepth < 1 || newDepth > 3) return;
        state.scanDepth = newDepth;
        saveScanConfig();
        sendConfigUpdate();
        showSubnetInput(newDepth >= 2);
        addLogEntry({ level: "info", message: `Scan depth changed to ${newDepth}` });
    }

    // ── vis-network Event Handlers ────────────────────────────────────
    network.on("click", (params) => {
        if (params.nodes.length > 0) {
            const nodeId = params.nodes[0];
            state.selectedNodeId = nodeId;
            const nodeData = state.nodes.get(nodeId);
            updateDevicePanel(nodeData);
        } else {
            clearDevicePanel();
        }
    });

    network.on("doubleClick", (params) => {
        if (params.nodes.length > 0) {
            const nodeId = params.nodes[0];
            state.selectedNodeId = nodeId;

            if (state.highlightActive) {
                // Reset highlight
                state.nodes.forEach((n) => {
                    state.nodes.update({ id: n.id, opacity: 1 });
                });
                state.edges.forEach((e) => {
                    state.edges.update({ id: e.id, width: 2, opacity: 1 });
                });
                state.highlightActive = false;
                return;
            }

            // Highlight connected edges and neighbour nodes
            const connectedEdges = network.getConnectedEdges(nodeId);
            const connectedNodes = network.getConnectedNodes(nodeId);
            const allNodeIds = new Set(connectedNodes);
            allNodeIds.add(nodeId);

            // Dim everything else
            state.nodes.forEach((n) => {
                const opacity = allNodeIds.has(n.id) ? 1 : 0.15;
                state.nodes.update({ id: n.id, opacity });
            });

            state.edges.forEach((e) => {
                const edgeId = e.id;
                let highlight = false;
                for (const ce of connectedEdges) {
                    if (ce === edgeId) { highlight = true; break; }
                }
                state.edges.update({
                    id: edgeId,
                    width: highlight ? 4 : 0.5,
                    opacity: highlight ? 1 : 0.1,
                });
            });

            state.highlightActive = true;
        }
    });

    network.on("stabilizationIterationsDone", () => {
        // Graph is stable; hide loading state
        el.graphEmpty.style.display = "none";
    });

    // ── Event Listeners ──────────────────────────────────────────────
    el.btnToggleTheme.addEventListener("click", toggleTheme);
    el.btnToggleScan.addEventListener("click", toggleScan);
    // Refresh-now button — send scan_now via WebSocket
    if (el.btnRefreshNow) {
        el.btnRefreshNow.addEventListener("click", () => {
            if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                state.ws.send(JSON.stringify({ action: "scan_now" }));
                el.btnRefreshNow.disabled = true;
                el.btnRefreshNow.textContent = "⏳ 扫描中...";
                // Fallback: restore button after 30 s in case scan_complete never arrives
                setTimeout(() => {
                    if (el.btnRefreshNow && el.btnRefreshNow.disabled) {
                        el.btnRefreshNow.disabled = false;
                        el.btnRefreshNow.textContent = "🔄 立即刷新";
                    }
                }, 30000);
            }
        });
    }
    el.btnExportPng.addEventListener("click", exportPNG);
    el.btnExportXlsx.addEventListener("click", exportXLSX);
    el.btnClearLog.addEventListener("click", () => {
        el.logStream.innerHTML = "";
        state.logLines = 0;
    });

    // Scan depth controls
    if (el.scanDepth) {
        el.scanDepth.addEventListener("change", onScanDepthChange);
    }
    if (el.addSubnet) {
        el.addSubnet.addEventListener("click", () => {
            const val = (el.subnetInputField.value || "").trim();
            if (val) {
                addSubnet(val);
                el.subnetInputField.value = "";
            }
        });
    }
    if (el.subnetInputField) {
        el.subnetInputField.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                const val = (el.subnetInputField.value || "").trim();
                if (val) {
                    addSubnet(val);
                    el.subnetInputField.value = "";
                }
            }
        });
    }

    // Keyboard shortcut: Space to pause/resume
    document.addEventListener("keydown", (e) => {
        if (e.code === "Space" && e.target === document.body) {
            e.preventDefault();
            toggleScan();
        }
    });

    // Handle window resize
    let resizeTimeout;
    window.addEventListener("resize", () => {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(() => {
            network.redraw();
        }, 250);
    });

    // ── Init ─────────────────────────────────────────────────────────
    function init() {
        initTheme();
        initChart();
        loadScanConfig();
        connectWS();
        updateScanBadge();
        addLogEntry({ level: "info", message: "Dashboard initialized. Connecting to scanner..." });
    }

    // Start
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
