# geo-fix — Architecture

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Language | Python 3.12 | mitmproxy is Python-native; rapid development |
| Proxy engine | mitmproxy >= 10.0 | Mature HTTPS interception with Python addon API |
| System tray | pystray >= 0.19 | Lightweight tray icon (Win32 backend) |
| Icon rendering | Pillow >= 10.0 | Programmatic 64x64 country-code icon drawing |
| Packaging | PyInstaller | Single-directory exe distribution |
| Testing | pytest, Playwright | Unit/integration/E2E coverage |
| CI/CD | GitHub Actions | Build on windows-latest, release via tags |

## Project Structure

```
geo-fix/
├── src/
���   ├── main.py             # Entry point: CLI, startup orchestration, shutdown
│   ├── proxy_addon.py      # mitmproxy addon: header rewrite + JS injection + CSP patch
│   ├── inject.js           # Browser-side JS: timezone/geo/WebRTC/language overrides
│   ├── presets.py          # CountryPreset dataclass, PRESETS dict, TARGET_DOMAINS
│   ├── system_config.py    # Windows: registry proxy, Firefox user.js, CA cert, firewall, state file
│   ├── health_check.py     # VPN detection, port check, PID-based instance lock
│   ├── tray.py             # pystray icon + context menu
│   └── setup_wizard.py     # First-run tkinter wizard (CA, firewall, DNS)
├── build/
│   └── build.py            # PyInstaller build + launcher generation
├── test/                   # pytest: unit, integration, E2E
├── .github/workflows/      # CI: build.yml (release), test-windows.yml (PR/push)
├── work/                   # Methodology: specs, tasks, decisions per feature
├── install.bat             # Portable installer (downloads embedded Python)
└── requirements.txt
```

## Component Interaction

```
main.py (entry point)
  ├── health_check: acquire PID lock, check VPN status
  ├── setup_wizard: first-run CA install + firewall + DNS
  ├── system_config: set WinINET proxy, Firefox proxy, save state
  ├── proxy_addon (GeoFixAddon): loaded into minimal Master (daemon thread)
  │     └── FlowCleanup: clears flow bodies after processing (last in chain)
  ├── tray (GeoFixTray): pystray icon (daemon thread)
  │     └── country switch → addon.switch_preset() [thread-safe via Lock]
  └── monitor thread: VPN status + watchdog health + RAM watchdog
        └── RAM >300MB + idle >10s → _restart_mitmproxy() (regenerate CA, restart Master)

Main thread blocks on stop_event.wait()
  → on signal/menu stop → system_config.cleanup() → revert all changes
```

## Thread Model

- **Main thread**: blocks on `threading.Event.wait()` for stop signal
- **mitmproxy thread** (daemon): async proxy event loop via minimal `Master` (6 essential addons only)
- **tray-icon thread** (daemon): pystray blocking `icon.run()`
- **monitor thread** (daemon): VPN check + watchdog health + RAM watchdog (60s interval)
- Shared state protected by `threading.Lock` in `GeoFixAddon` and `GeoFixTray`

## Resource Optimization

| Metric | Before | After | Threshold |
|---|---|---|---|
| Startup RAM | 200-300MB (DumpMaster + ~35 addons) | ~100-120MB (Master + 6 addons) | ≤150MB |
| RAM after 8h | 400-500MB (unbounded growth) | Stable (FlowCleanup + RAM watchdog) | <20% growth |
| CPU idle | 5-15% | <1% | <5% |

**Key decisions:**
1. Replace `DumpMaster` with base `Master` + 6 essential addons (Core, Proxyserver, NextLayer, TlsConfig, KeepServing, ErrorCheck)
2. `FlowCleanup` addon clears flow bodies + trims WebSocket history after processing
3. RAM watchdog: auto-restart mitmproxy if RAM >300MB and idle >10s (max 3/hour, 10min cooldown)

## Data Flow: Request Lifecycle

```
Browser → WinINET proxy (127.0.0.1:8080) → GeoFixAddon.request()
  │  Accept-Language header rewritten (ALL domains)
  │  Geolocation API intercept: POST googleapis.com/geolocation → fake JSON response
  ▼
mitmproxy → real server (TLS with mitmproxy CA)
  ▼
GeoFixAddon.response()
  │  Two-tier JS injection (ALL domains):
  │    1. Find injection position (<head>, <html>, or <!DOCTYPE>)
  ��    2. Generate cryptographic nonce (secrets.token_urlsafe)
  │    3. Target domains: full JS payload (timezone/geo/language/WebRTC/permissions)
  │       Non-target domains: geo-only payload (geolocation + permissions.query)
  │    4. CSP skip guard: script-src 'none' or require-trusted-types → skip injection
  │    5. Insert <script nonce="..."> tag, modify CSP headers
  ▼
Browser receives modified HTML → JS IIFE executes:
  - Date.prototype.getTimezoneOffset → fake offset (target only)
  - Intl.DateTimeFormat → fake timezone (target only)
  - Temporal.Now (Chrome 145+) → fake timezone (target only)
  - navigator.geolocation → fake coords (ALL domains)
  - navigator.permissions.query('geolocation') → {state: 'granted'} (ALL domains)
  - navigator.language/languages → fake language (target only)
  - RTCPeerConnection → STUN servers filtered out (target only)
  - All overrides have .toString() returning "[native code]"
```

## External Integrations

| Integration | Method | Admin Required |
|---|---|---|
| Windows proxy | WinINET registry (HKCU) + ctypes notification | No |
| CA certificate | certutil -addstore -user Root | No |
| Firefox proxy | user.js in Firefox profile directory | No |
| Firefox CA | security.enterprise_roots.enabled=true in user.js | No |
| WebRTC firewall | netsh advfirewall rules for STUN ports | Yes (optional) |
| Location Services | HKCU DeviceAccess registry (disable/restore) | No |
| VPN detection | netsh interface + ipconfig | No |

## State Management

`ProxyState` dataclass persisted as `.geo-fix-state.bin` (DPAPI-encrypted, atomic write):
- PID, preset code, timestamp, session ID, session tmpdir, proxy port
- Original proxy settings (WinINET backup)
- Firefox modification flag + backup path
- CA thumbprint for targeted cert removal
- Original Location Services registry value (for restore on cleanup)
- Schema-validated on load (unknown fields rejected)

**Cleanup resilience** (`cleanup_pending.json` in APPDATA/geo-fix/):
- On cleanup failure: each step retried once after 3s delay
- Persistent failures written as JSON label list → re-executed on next startup via `check_pending_cleanup()`
- Labels validated against allowlist before dispatch (CWE-20 mitigation)

## Security: CA Key Lifecycle

CA private key exists on disk only during mitmproxy startup (seconds). After proxy is confirmed running, `delete_ca_key_files()` removes private key, PKCS12, and DER copy from session tmpdir. Public cert deleted after `install_ca_cert()`. Key remains only in mitmproxy process memory.

## Testing Strategy

### Test Pyramid

| Layer | Framework | What | Where |
|---|---|---|---|
| Unit | pytest | Presets, addon logic, CLI parsing, RAM monitor guards, Master setup, security hardening | `test/test_*.py`, `test/unit/` |
| Integration | pytest + real mitmproxy | HTTP/HTTPS proxy flow, JS injection through proxy, flow cleanup, proxy restart, Windows registry/cert | `test/test_integration_*.py` |
| E2E | pytest + Playwright | Real browser through proxy: JS injection, CSP nonce, security lifecycle | `test/test_e2e_*.py` |
| Benchmark | pytest + tracemalloc + OS APIs | RAM at startup, memory stability under load, CPU per request, idle consumption, WebSocket trimming | `test/test_resource_benchmark.py` |

### CI Pipeline (GitHub Actions — `test-windows.yml`)

All tests run on `windows-latest` (target platform):

```
unit-tests → integration-tests → e2e-browser-tests
         ↘ resource-benchmarks (parallel with integration)
```

1. **unit-tests**: All unit tests including resource-optimization (Master setup, RAM monitor)
2. **integration-tests**: Proxy flow, security integration, resource-optimization integration
3. **e2e-browser-tests**: Playwright + Chromium through real proxy
4. **resource-benchmarks**: Measure real RAM/CPU consumption, save `benchmark_results.json` as artifact (90-day retention)

### Testing Decisions

- **Windows-only in CI**: Target platform is Windows. All CI runs on `windows-latest`. Linux used only for local development rapid iteration.
- **13 Windows-specific tests**: Registry, certutil, DPAPI, ACL, system proxy — skipped on Linux, run in CI.
- **Benchmark as artifact**: `benchmark_results.json` uploaded to GitHub Actions artifacts for historical tracking. Not a pass/fail gate — CPU thresholds vary by CI machine.
- **Real mitmproxy in integration tests**: Integration tests start a real `Master` instance on a free port, send HTTP through it. No mitmproxy mocking at integration level.
- **FlowCleanup known limitation**: FlowCleanup clears `flow.response.content` in the `response()` hook before mitmproxy sends to client, causing empty HTTP bodies through proxy. Documented in `test_integration_proxy.py::test_flowcleanup_empties_response_body` as regression gate.
