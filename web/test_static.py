"""
test_static.py — Static verification for web/ frontend files.

Checks:
  1.  All required files exist and are non-empty.
  2.  index.html references correct CDN scripts.
  3.  style.css contains both dark and light theme variables.
  4.  app.js contains WebSocket connection logic and state management.
  5.  No build tools or node_modules are required.

Does NOT require a browser — purely filesystem + text analysis.
"""

import os
import re
import sys
from pathlib import Path

# ── Project root ─────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # NetworkTopologyDiscovery/
WEB_DIR = PROJECT_ROOT / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

REQUIRED_FILES = [
    TEMPLATES_DIR / "index.html",
    STATIC_DIR / "style.css",
    STATIC_DIR / "app.js",
]

# Approval thresholds: min file sizes in bytes
MIN_FILE_SIZES: dict[str, int] = {
    "index.html": 3000,   # Must have substantial HTML structure
    "style.css": 3000,    # Must have theme vars + layout
    "app.js": 5000,       # Must have WebSocket + vis-network + handlers
}


# ======================================================================
# Test helpers
# ======================================================================

def read_file(path: Path) -> str:
    """Read a file and return its contents, or fail."""
    assert path.exists(), f"File not found: {path}"
    text = path.read_text(encoding="utf-8")
    assert len(text.strip()) > 0, f"File is empty: {path}"
    return text


def check_pattern(text: str, pattern: str, name: str, label: str) -> None:
    """Assert that *pattern* is found in *text*."""
    if re.search(pattern, text):
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — pattern not found: {pattern}")
        raise AssertionError(f"{name}: missing {label}")


def check_no_pattern(text: str, pattern: str, name: str, label: str) -> None:
    """Assert that *pattern* is NOT found in *text*."""
    if re.search(pattern, text):
        print(f"  ❌ {label} — forbidden pattern found: {pattern}")
        raise AssertionError(f"{name}: disallowed {label}")
    else:
        print(f"  ✅ {label} (absent)")


# ======================================================================
# Test 1 — File existence & minimum sizes
# ======================================================================

def test_files_exist_and_non_empty() -> None:
    print("\n" + "=" * 60)
    print("  Test 1: File existence & minimum sizes")
    print("=" * 60)

    for path in REQUIRED_FILES:
        filename = path.name
        assert path.exists(), f"Missing file: {path}"
        size = path.stat().st_size
        assert size > 0, f"Empty file: {path}"
        min_size = MIN_FILE_SIZES.get(filename, 500)
        assert size >= min_size, (
            f"{filename}: {size} bytes < {min_size} minimum — "
            f"file appears too small for a complete implementation"
        )
        print(f"  ✅ {filename}: {size:,} bytes (min {min_size:,})")


# ======================================================================
# Test 2 — HTML CDN references & structure
# ======================================================================

def test_html_cdn_and_structure() -> None:
    print("\n" + "=" * 60)
    print("  Test 2: HTML CDN references & structure")
    print("=" * 60)

    html = read_file(TEMPLATES_DIR / "index.html")

    # DOCTYPE + html lang
    check_pattern(html, r"<!DOCTYPE\s+html>", "index.html", "DOCTYPE declaration")
    check_pattern(html, r'<html[^>]*lang="en"', "index.html", '<html lang="en">')

    # vis-network CDN
    check_pattern(
        html,
        r"unpkg\.com/vis-network@9\.\d+\.\d+/dist/vis-network\.min\.js",
        "index.html",
        "vis-network 9.x CDN",
    )

    # vis-data CDN
    check_pattern(
        html,
        r"unpkg\.com/vis-data@7\.\d+\.\d+/peer/vis-data\.min\.js",
        "index.html",
        "vis-data 7.x CDN",
    )

    # Chart.js CDN
    check_pattern(
        html,
        r"cdn\.jsdelivr\.net/npm/chart\.js@4\.\d+\.\d+/dist/chart\.umd\.min\.js",
        "index.html",
        "Chart.js 4.x CDN",
    )

    # Google Fonts — Inter
    check_pattern(
        html,
        r"fonts\.googleapis\.com/css2\?family=Inter",
        "index.html",
        "Google Fonts Inter link",
    )

    # No node_modules
    check_no_pattern(
        html,
        r"node_modules",
        "index.html",
        "no node_modules reference",
    )

    # No build tool references
    check_no_pattern(
        html,
        r"(webpack|parcel|vite|rollup|esbuild|babel)",
        "index.html",
        "no build tool",
    )

    # Key elements
    check_pattern(html, r'id="network-graph"', "index.html", 'div#network-graph')
    check_pattern(html, r'id="log-stream"', "index.html", 'div#log-stream')
    check_pattern(html, r'id="stats-panel"', "index.html", 'div#stats-panel')
    check_pattern(html, r'id="device-panel"', "index.html", 'div#device-panel')
    check_pattern(html, r'id="progress-bar-fill"', "index.html", 'progress bar')
    check_pattern(html, r'data-theme', "index.html", 'data-theme attribute')
    check_pattern(html, r'id="btn-toggle-theme"', "index.html", 'theme toggle button')
    check_pattern(html, r'id="btn-toggle-scan"', "index.html", 'scan control button')
    check_pattern(html, r'id="btn-export-png"', "index.html", 'export PNG button')
    check_pattern(html, r'id="btn-export-xlsx"', "index.html", 'export XLSX button')

    # App scripts loaded
    check_pattern(html, r'src="/static/app\.js"', "index.html", 'app.js script tag')
    check_pattern(html, r'href="/static/style\.css"', "index.html", 'style.css link tag')


# ======================================================================
# Test 3 — CSS theme variables
# ======================================================================

def test_css_themes() -> None:
    print("\n" + "=" * 60)
    print("  Test 3: CSS theme variables (dark + light)")
    print("=" * 60)

    css = read_file(STATIC_DIR / "style.css")

    # Dark theme (root) variables
    dark_vars = [
        ("--bg-primary", r"--bg-primary\s*:"),
        ("--bg-panel", r"--bg-panel\s*:"),
        ("--text-primary", r"--text-primary\s*:"),
        ("--accent", r"--accent\s*:"),
    ]
    for var_name, pattern in dark_vars:
        check_pattern(css, pattern, "style.css", f"dark theme {var_name}")

    # Light theme block
    check_pattern(css, r'\[data-theme="light"\]', "style.css", 'light theme [data-theme="light"] block')

    # Light theme overrides
    light_vars = [
        "--bg-primary",
        "--bg-panel",
        "--accent",
    ]
    light_block_match = re.search(
        r'\[data-theme="light"\]\s*\{([^}]+)\}',
        css,
        re.DOTALL,
    )
    assert light_block_match, "style.css: missing [data-theme='light'] block"
    light_block = light_block_match.group(1)
    for var_name in light_vars:
        assert var_name in light_block, (
            f"style.css: light theme missing {var_name}"
        )
        print(f"  ✅ light theme overrides {var_name}")

    # Layout: CSS Grid
    check_pattern(css, r"grid-template-columns", "style.css", "CSS Grid layout")

    # Glassmorphism
    check_pattern(css, r"backdrop-filter\s*:\s*blur", "style.css", "backdrop-filter blur (glass)")

    # Progress bar gradient
    check_pattern(css, r"linear-gradient", "style.css", "linear-gradient (progress bar)")

    # Animations
    check_pattern(css, r"@keyframes\s+", "style.css", "@keyframes animations")


# ======================================================================
# Test 4 — JavaScript WebSocket & vis-network
# ======================================================================

def test_js_core_logic() -> None:
    print("\n" + "=" * 60)
    print("  Test 4: JavaScript core logic")
    print("=" * 60)

    js = read_file(STATIC_DIR / "app.js")

    # IIFE or module pattern
    check_pattern(js, r"\(function\s*\(\)", "app.js", "IIFE wrapper")

    # vis-network instantiation
    check_pattern(js, r"new\s+vis\.Network", "app.js", "new vis.Network(...)")

    # vis.DataSet usage
    check_pattern(js, r"new\s+vis\.DataSet", "app.js", "new vis.DataSet(...)")

    # WebSocket connection
    check_pattern(js, r"new\s+WebSocket", "app.js", "new WebSocket(...)")

    # WebSocket URL construction
    check_pattern(js, r"/ws", "app.js", "WebSocket /ws endpoint")

    # Message handling
    check_pattern(js, r"ws\.onmessage", "app.js", "ws.onmessage handler")
    check_pattern(js, r"JSON\.parse", "app.js", "JSON.parse for messages")

    # Event type handling
    event_types = [
        ("topology_update", r'"topology_update"'),
        ("device_online", r'"device_online"'),
        ("device_offline", r'"device_offline"'),
        ("progress", r'"progress"'),
        ("scan_complete", r'"scan_complete"'),
        ("scan_start", r'"scan_start"'),
        ("device_discovered", r'"device_discovered"'),
    ]
    for name, pattern in event_types:
        check_pattern(js, pattern, "app.js", f"handles {name} event")

    # Control messages
    check_pattern(js, r'"pause"', "app.js", 'sends "pause" control')
    check_pattern(js, r'"resume"', "app.js", 'sends "resume" control')
    check_pattern(js, r'"get_topology"', "app.js", 'sends "get_topology" control')

    # State management
    check_pattern(js, r"const\s+state\s*=\s*\{", "app.js", "state object")
    check_pattern(js, r"nodes:\s*new\s+vis\.DataSet", "app.js", "state.nodes (DataSet)")
    check_pattern(js, r"edges:\s*new\s+vis\.DataSet", "app.js", "state.edges (DataSet)")
    check_pattern(js, r"connected:", "app.js", "state.connected")
    check_pattern(js, r"scanning:", "app.js", "state.scanning")
    check_pattern(js, r"stats:", "app.js", "state.stats")

    # Theme toggle logic
    check_pattern(js, r"localStorage", "app.js", "localStorage usage")
    check_pattern(js, r"ntd-theme", "app.js", "ntd-theme localStorage key")

    # Chart.js usage
    check_pattern(js, r"new\s+Chart\(", "app.js", "new Chart() (Chart.js)")

    # Network events (click, doubleClick)
    check_pattern(js, r"network\.on\(", "app.js", "network.on() event binding")
    check_pattern(js, r'"click"', "app.js", 'network click handler')
    check_pattern(js, r'"doubleClick"', "app.js", 'network doubleClick handler')

    # Export functions
    check_pattern(js, r"/api/export/png", "app.js", "export PNG API path")
    check_pattern(js, r"/api/export/xlsx", "app.js", "export XLSX API path")

    # Reconnect logic
    check_pattern(js, r"reconnect", "app.js", "WebSocket reconnect logic")

    # Log management
    check_pattern(js, r"addLogEntry", "app.js", "addLogEntry function")
    check_pattern(js, r"maxLogLines", "app.js", "log line limit (maxLogLines)")

    # Progress bar
    check_pattern(js, r"updateProgress", "app.js", "updateProgress function")

    # No import/require (should be pure JS)
    check_no_pattern(js, r"\bimport\s+", "app.js", "no ES import (pure JS)")
    check_no_pattern(js, r"\brequire\(", "app.js", "no require() (pure JS)")


# ======================================================================
# Main
# ======================================================================

def main() -> int:
    print("=" * 60)
    print("  Network Topology Discovery — Frontend Static Tests")
    print("=" * 60)
    print(f"  Project root: {PROJECT_ROOT}")
    print(f"  Web dir: {WEB_DIR}")

    failures: list[str] = []

    def run_test(name: str, fn) -> None:
        try:
            fn()
        except AssertionError as exc:
            failures.append(f"{name}: {exc}")
            print(f"\n  ⛔ FAILED: {exc}")
        except Exception as exc:
            failures.append(f"{name}: {exc}")
            print(f"\n  💥 ERROR: {exc}")

    run_test("File existence", test_files_exist_and_non_empty)
    run_test("HTML structure", test_html_cdn_and_structure)
    run_test("CSS themes", test_css_themes)
    run_test("JavaScript logic", test_js_core_logic)

    print("\n" + "=" * 60)
    if failures:
        print(f"  ❌ {len(failures)} test(s) FAILED:")
        for f in failures:
            print(f"     • {f}")
        print("=" * 60)
        return 1
    else:
        print("  ✅ ALL TESTS PASSED")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
