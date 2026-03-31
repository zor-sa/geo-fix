"""E2E browser tests for geo-fix.

Starts mitmproxy with US preset, launches Chromium via Playwright,
and verifies all geo-signals are spoofed correctly.

Requires: pip install playwright && python -m playwright install chromium
"""

import asyncio
import socket
import sys
import threading
import time

import pytest

# Only run if playwright is installed
playwright = pytest.importorskip("playwright")


@pytest.fixture(scope="module")
def proxy_server():
    """Start mitmproxy with US preset in background."""
    from src.proxy_addon import GeoFixAddon
    from src.presets import PRESETS

    port = 18095
    ready = threading.Event()

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        from mitmproxy.options import Options
        from mitmproxy.tools.dump import DumpMaster
        opts = Options(listen_host="127.0.0.1", listen_port=port)
        master = DumpMaster(opts)
        master.addons.add(GeoFixAddon(PRESETS["US"]))
        ready.set()
        loop.run_until_complete(master.run())

    t = threading.Thread(target=run, daemon=True)
    t.start()
    ready.wait(timeout=10)

    for _ in range(20):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            break
        except (ConnectionRefusedError, socket.timeout):
            time.sleep(0.5)
    else:
        pytest.skip("Proxy failed to start")

    yield port


@pytest.fixture(scope="module")
def browser_page(proxy_server):
    """Launch Chromium with proxy and return a page."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        proxy={"server": f"http://127.0.0.1:{proxy_server}"},
        args=["--ignore-certificate-errors"],
    )
    page = browser.new_page()
    page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)

    yield page

    browser.close()
    pw.stop()


def test_timezone_spoofed(browser_page):
    tz = browser_page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    assert tz == "America/New_York", f"Expected America/New_York, got {tz}"


def test_timezone_offset(browser_page):
    offset = browser_page.evaluate("new Date().getTimezoneOffset()")
    assert offset in [240, 300], f"Expected 240 or 300, got {offset}"


def test_language(browser_page):
    lang = browser_page.evaluate("navigator.language")
    assert lang == "en-US", f"Expected en-US, got {lang}"


def test_languages_array(browser_page):
    langs = browser_page.evaluate("Array.from(navigator.languages)")
    assert "en-US" in langs, f"en-US not in {langs}"


def test_geolocation(browser_page):
    coords = browser_page.evaluate("""() => new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(
            pos => resolve({lat: pos.coords.latitude, lon: pos.coords.longitude}),
            err => reject(err.message),
            {timeout: 5000}
        )
    })""")
    assert abs(coords["lat"] - 38.8951) < 0.01, f"Lat wrong: {coords}"
    assert abs(coords["lon"] - (-77.0364)) < 0.01, f"Lon wrong: {coords}"


def test_webrtc_relay_mode(browser_page):
    policy = browser_page.evaluate("""(() => {
        try {
            const pc = new RTCPeerConnection({
                iceServers: [{urls: 'stun:stun.l.google.com:19302'}]
            });
            const p = pc.getConfiguration().iceTransportPolicy;
            pc.close();
            return p;
        } catch(e) { return 'error: ' + e.message; }
    })()""")
    assert policy == "relay", f"iceTransportPolicy should be 'relay', got: {policy}"
