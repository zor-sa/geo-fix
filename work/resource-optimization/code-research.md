# Code Research: Resource Optimization

**Feature:** Reduce RAM and CPU consumption of geo-fix while preserving all functionality and security.
**Date:** 2026-03-29

## 1. Entry Points

### `/home/claude/workspace/projects/geo-fix/src/main.py`
Main entry point and CLI. Orchestrates startup: parses args, acquires instance lock, starts mitmproxy in a daemon thread, spawns watchdog subprocess, configures system proxy/Firefox/firewall, starts tray icon thread, and blocks on `stop_event.wait()`.

Key functions:
- `_start_mitmproxy(addon, confdir, port) -> threading.Thread` -- creates `DumpMaster` with full default addons, runs in daemon thread
- `_spawn_watchdog(main_pid, state_file, session_tmpdir, session_id, stop_token) -> subprocess.Popen` -- launches watchdog as separate Python process
- `main()` -- full startup orchestration

### `/home/claude/workspace/projects/geo-fix/src/proxy_addon.py`
mitmproxy addon that rewrites Accept-Language headers and injects JS into HTML responses for target domains.

Key functions:
- `GeoFixAddon.request(flow)` -- rewrites Accept-Language header for target domains
- `GeoFixAddon.response(flow)` -- injects JS payload into HTML responses, modifies CSP
- `_build_js_payload(preset) -> str` -- substitutes preset values into JS template via str.replace
- `_find_inject_position(html_text) -> int` -- finds `<head>`, `<html>`, or `<!DOCTYPE>` position
- `_modify_csp(csp_value, nonce) -> str` -- adds nonce to CSP script-src directive

### `/home/claude/workspace/projects/geo-fix/src/watchdog.py`
Standalone subprocess that monitors the main process PID every 2 seconds and performs crash recovery if main dies unexpectedly.

Key functions:
- `run_watchdog(main_pid, state_file, session_tmpdir, session_id, stop_token)` -- main polling loop
- `_is_process_alive(pid) -> bool` -- PID check via ctypes (Windows) or os.kill (Linux)
- `_check_stop_flag(session_tmpdir, stop_token) -> bool` -- checks for clean shutdown signal

### `/home/claude/workspace/projects/geo-fix/src/tray.py`
System tray icon using pystray. Creates 64x64 PIL image with country code text.

Key functions:
- `GeoFixTray.start()` -- blocking pystray run
- `_create_icon_image(text, bg_color, text_color) -> Image.Image` -- renders 64x64 icon with Pillow

## 2. Data Layer

### State Management
`ProxyState` dataclass in `system_config.py` -- serialized to JSON, encrypted with DPAPI (Windows) or plaintext (non-Windows), saved atomically to `.geo-fix-state.bin`.

Fields: `pid`, `preset_code`, `timestamp`, `original_proxy_enable/server/override`, `firefox_prefs_modified/backup`, `session_id`, `session_tmpdir`, `ca_thumbprint`, `proxy_port`.

### Preset Data
`CountryPreset` frozen dataclass in `presets.py` -- 4 presets (US, DE, NL, GB) with timezone, coords, language, accept_language. `TARGET_DOMAINS` list for JS injection scope.

### JS Template
`inject.js` (9.3KB) -- loaded once at module import via `_JS_TEMPLATE_PATH.read_text()` into `_JS_TEMPLATE` module-level variable. Placeholder substitution done per-preset-switch.

## 3. Similar Features

No similar optimization work exists in the codebase. The BACKLOG.md contains one related entry about running as Administrator (for CA key security, not resource optimization).

## 4. Integration Points

### mitmproxy DumpMaster Initialization (CRITICAL FOR OPTIMIZATION)

`_start_mitmproxy()` in main.py (lines 242-273) creates DumpMaster with default options. DumpMaster loads **35 addons** by default:

**Addons loaded (from `default_addons()`):**
- Core, Browser, Block, StripDnsHttpsRecords, BlockList
- **AntiCache** -- strips cache headers (inactive by default, option=False)
- **AntiComp** -- strips compression (inactive by default, option=False)
- **ClientPlayback, ServerPlayback** -- replay features (unused)
- **CommandHistory, Comment, Cut, Export** -- interactive features (unused)
- **Onboarding** -- serves onboarding app on mitm.it (unused)
- **ProxyAuth** -- proxy authentication (unused)
- **ScriptLoader** -- external script loading (unused)
- **DnsResolver** -- DNS resolution
- **MapRemote, MapLocal** -- request mapping (unused)
- **ModifyBody, ModifyHeaders** -- modification addons (unused)
- **StickyAuth, StickyCookie** -- session persistence (unused)
- **Save, SaveHar** -- flow saving; **SaveHar stores all flows in `self.flows: list`** (potential memory leak)
- **TlsConfig, UpstreamAuth, UpdateAltSvc**
- **DisableH2C** -- disables HTTP/2 cleartext

**DumpMaster-specific addons (added after default_addons):**
- **Dumper** -- prints flow summaries to stdout (default flow_detail=1, writes every response)
- **KeepServing** -- prevents premature shutdown
- **ReadFileStdin** -- reads flows from stdin (unused)
- **ErrorCheck** -- checks for startup errors

**Key finding:** SaveHar has a `flows: list[flow.Flow]` that accumulates ALL flows if `hardump` option is set. The Dumper writes to stdout for every flow (flow_detail=1 by default). Many addons are completely unused by geo-fix but still process every request/response hook.

### flow.response.text Access Pattern

In `proxy_addon.py` line 176-196, the response handler:
1. Reads `flow.response.text` (getter) -- decompresses content-encoding (gzip/brotli), decodes charset to Python str
2. Performs string operations (find inject position, concatenate)
3. Sets `flow.response.text` (setter) -- re-encodes charset, re-compresses content-encoding

This is a full decompress-decode-modify-encode-recompress cycle for every HTML response from target domains. The `get_content()` decompresses, `get_text()` decodes charset, `set_text()` re-encodes charset, `set_content()` re-compresses -- 4 transformation steps.

### Thread Model

4 threads + 1 subprocess:
1. **Main thread** -- blocks on `stop_event.wait()`
2. **mitmproxy thread** (daemon) -- async event loop via DumpMaster.run()
3. **tray-icon thread** (daemon) -- pystray blocking `icon.run()`
4. **monitor thread** (daemon) -- VPN status check every 60s + watchdog health
5. **watchdog subprocess** -- separate Python process polling PID every 2s

The monitor thread (main.py lines 410-438) runs `check_vpn_status()` which spawns 1-2 subprocesses (`netsh` + `ipconfig`) every 60 seconds.

### Watchdog Subprocess

Imports `src.system_config` (which imports json, logging, os, re, shutil, subprocess, sys, tempfile, dataclasses, pathlib). Does NOT import mitmproxy. Memory footprint is primarily the Python interpreter + standard library imports (~4MB for system_config imports, but total process overhead is 15-20MB due to Python runtime).

### JS Payload Building

`_build_js_payload()` in proxy_addon.py (lines 58-71) performs 5 `str.replace()` calls on the 9.3KB JS template. This is called once per `switch_preset()`, NOT per response. The result is cached in `self._js_payload`. This is already efficient.

## 5. Existing Tests

Framework: pytest, run via `python -m pytest test/ -v` in CI (GitHub Actions on windows-latest).

**Test files (23 total):**

| File | What it tests |
|---|---|
| `test/test_proxy_addon.py` | Addon request/response hooks, CSP modification, JS injection, preset switching |
| `test/test_presets.py` | Preset data validation, target domain matching |
| `test/test_main.py` | Country validation, CLI argument parsing |
| `test/test_watchdog.py` | Stop flag, PID check, watchdog loop with mocks |
| `test/test_health_check.py` | VPN status enum, proxy port check, PID file lock |
| `test/test_system_config.py` | System config functions |
| `test/test_integration_windows.py` | Windows integration tests |
| `test/test_integration_security.py` | Security integration tests |
| `test/test_e2e_browser.py` | Browser E2E with Playwright |
| `test/test_e2e_security.py` | Security E2E tests |
| `test/unit/test_csp.py` | CSP header modification |
| `test/unit/test_wizard.py` | Setup wizard |
| + 11 more unit/integration tests | Various |

**Representative test signatures:**
- `TestGeoFixAddon.test_response_injects_js_for_target_domain(self, addon, make_flow)` -- uses pytest fixtures, FakeFlow mock objects
- `TestWatchdogLoop.test_calls_cleanup_on_main_death(self, mock_alive, mock_sleep, tmp_path)` -- uses `@patch` decorators, `tmp_path` fixture

**Patterns:** Tests use `unittest.mock.patch` for system calls, `pytest.fixture` for test objects, `monkeypatch` for module-level globals. No mitmproxy is actually started in unit tests -- proxy_addon tests use custom FakeFlow/FakeRequest/FakeResponse classes.

## 6. Shared Utilities

### `src/presets.py`
- `is_target_domain(host) -> bool` -- called on every request AND response in proxy_addon
- `get_preset(code) -> CountryPreset` -- lookup with normalization
- `PRESETS: Dict[str, CountryPreset]` -- 4 country presets
- `TARGET_DOMAINS: List[str]` -- 6 domain suffixes

### `src/health_check.py`
- `check_vpn_status() -> VpnStatus` -- runs `netsh` + `ipconfig` subprocesses
- `check_proxy_running(host, port) -> bool` -- TCP connect check
- `acquire_instance_lock() / release_instance_lock()` -- PID file based

### `src/system_config.py`
- `save_state(state) / load_state() -> Optional[ProxyState]` -- DPAPI-encrypted atomic state
- `cleanup(state) / stateless_cleanup()` -- full system restoration
- `create_session_tmpdir() -> str` -- per-session temp directory with restricted ACL

## 7. Potential Problems

### Memory Issues

1. **DumpMaster loads 35 addons** -- most are unused (ClientPlayback, ServerPlayback, CommandHistory, Comment, Cut, Export, Onboarding, ScriptLoader, MapRemote, MapLocal, etc.). Each addon object registers hooks that are checked on every flow event, adding CPU overhead per request/response.

2. **SaveHar addon stores `self.flows: list`** -- although `hardump` option is empty by default, the list exists. Need to verify it doesn't accumulate under default config. If it does, this is an unbounded memory leak.

3. **Dumper writes to stdout on every flow** -- at flow_detail=1, it calls `echo_flow()` for every response, writing to stdout. For a browsing session with hundreds of requests, this generates continuous I/O.

4. **flow.response.text double conversion** -- reading `.text` decompresses+decodes, setting `.text` re-encodes+recompresses. For target domain HTML responses only. Could potentially work with `.content` (bytes) directly and avoid one decode/encode cycle if injection is done at byte level.

5. **No flow cleanup/eviction** -- DumpMaster (unlike web interface) does not retain flows in a view, but individual addon objects might. The core mitmproxy does not appear to retain flows after processing, which is good.

6. **Watchdog as separate Python process** -- 15-20MB for a simple PID polling loop that runs `time.sleep(2)` + file check + `os.kill(pid, 0)`. The entire Python runtime is loaded just for this.

### CPU Issues

7. **`is_target_domain()` called twice per flow** -- once in `request()`, once in `response()`. Uses linear scan of 6 domain suffixes with string operations. Minor but frequent.

8. **VPN check every 60 seconds** spawns 1-2 subprocesses (`netsh`, `ipconfig`). Small but steady overhead.

9. **`_find_inject_position()` does 3 sequential `.lower()` + `.find()` calls** on the full HTML text. The `.lower()` creates a full copy of the HTML string each time.

### String Operation Overhead in proxy_addon.response()

10. **HTML string concatenation** (line 195): `html_text[:inject_pos] + script_tag + html_text[inject_pos:]` -- creates 2 slices + 1 concatenation = 3 temporary strings for potentially large HTML documents. Combined with the decompression/recompression from `.text` access, a single HTML response may create 5+ temporary copies of the full document in memory.

## 8. Constraints & Infrastructure

### Dependencies
- Python 3.12, mitmproxy >= 10.0 (installed: 12.2.1), pystray >= 0.19, Pillow >= 10.0
- PyInstaller for packaging (--onedir mode)
- Windows 10/11 target platform

### PyInstaller Build
`build/build.py` uses `--onedir` mode with `--hidden-import mitmproxy.addons` and `--hidden-import mitmproxy.tools.dump`. CI build in `.github/workflows/build.yml` adds `--hidden-import pystray._win32`. No `--exclude-module` flags are used -- the entire mitmproxy + dependencies are bundled.

### CI/CD
GitHub Actions on `windows-latest`. Tests run with `python -m pytest test/ -v`. Build triggered on tags `v*` or manual dispatch.

### Key Constraints
- mitmproxy DumpMaster must be used (core proxy engine)
- Watchdog must detect main process crash and clean up system state
- Thread-safe preset switching must be preserved
- All existing tests must pass
- Security properties must be preserved (CSP nonce, CA key deletion, DPAPI state encryption)

## 9. Optimization Opportunities Summary

### High Impact

**A. Strip unnecessary DumpMaster addons** -- Instead of using DumpMaster (which loads 35 addons), use the base `Master` class directly and add only required addons: Core, Proxyserver, NextLayer, TlsConfig, and the custom GeoFixAddon. This removes ~30 unused addons from the hook processing chain. Alternatively, construct DumpMaster with `with_dumper=False` and remove unneeded addons after initialization.

**B. Set `flow_detail=0`** -- This silences the Dumper addon completely (no stdout writes per flow). If using DumpMaster, pass `with_dumper=False` to the constructor.

**C. Replace watchdog subprocess with a thread** -- The watchdog currently runs as a separate Python process (15-20MB) just to poll a PID every 2 seconds. It could be a thread in the main process that writes state and lets the OS-level scheduled task (ONLOGON) handle crash recovery. Alternatively, use a lighter language for the watchdog (not feasible with current stack), or implement it as a minimal script that doesn't import system_config at startup.

**D. Lazy import of mitmproxy** -- mitmproxy import alone costs ~35MB. Currently imported inside `_start_mitmproxy()` function (good -- already deferred). But the import happens once and stays. No optimization here beyond what's already done.

### Medium Impact

**E. Work with `flow.response.content` (bytes) instead of `.text` (str)** -- Avoid the charset decode/encode round-trip. Do injection position search and string insertion at the bytes level. This saves one full-document decode + encode cycle per HTML response. Requires careful handling of multi-byte encodings.

**F. Cache `html_text.lower()` in `_find_inject_position()`** -- Currently creates a lowercase copy of the full HTML. The function is called once per HTML response, so the copy is created once, but it's an unnecessary full-document allocation.

**G. Use `io.StringIO` or list join for HTML assembly** -- Replace `html_text[:pos] + script_tag + html_text[pos:]` with a more memory-efficient approach to avoid creating multiple temporary string copies.

### Low Impact

**H. Pre-compile `is_target_domain()` to use a set or endswith tuple** -- Replace linear list scan with `host.endswith(tuple(TARGET_DOMAINS))` for O(1) suffix matching. Minor CPU savings on high-traffic flows.

**I. Reduce VPN check frequency or make it on-demand** -- 60-second polling with subprocess spawning could be 120+ seconds or event-based.

**J. PyInstaller `--exclude-module` flags** -- Exclude unused mitmproxy addons from the bundle to reduce startup time and disk/memory footprint. Does not affect runtime RAM much since unused addons are loaded by `default_addons()` anyway.
