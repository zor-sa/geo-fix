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
  ├── proxy_addon (GeoFixAddon): loaded into mitmproxy DumpMaster (daemon thread)
  └── tray (GeoFixTray): pystray icon (daemon thread)
        └── country switch → addon.switch_preset() [thread-safe via Lock]

Main thread blocks on stop_event.wait()
  → on signal/menu stop → system_config.cleanup() → revert all changes
```

## Thread Model

- **Main thread**: blocks on `threading.Event.wait()` for stop signal
- **mitmproxy thread** (daemon): async proxy event loop via `DumpMaster`
- **tray-icon thread** (daemon): pystray blocking `icon.run()`
- Shared state protected by `threading.Lock` in `GeoFixAddon` and `GeoFixTray`

## Data Flow: Request Lifecycle

```
Browser → WinINET proxy (127.0.0.1:8080) → GeoFixAddon.request()
  │  Accept-Language header rewritten (ALL domains)
  ▼
mitmproxy → real server (TLS with mitmproxy CA)
  ▼
GeoFixAddon.response()
  │  Target domains only (*.google.com, *.googleapis.com, etc.):
  │    1. Find injection position (<head>, <html>, or <!DOCTYPE>)
  ��    2. Generate cryptographic nonce (secrets.token_urlsafe)
  │    3. Build JS payload from inject.js template + preset values
  │    4. Insert <script nonce="..."> tag
  │    5. Modify CSP headers to allow nonce
  ▼
Browser receives modified HTML → inject.js IIFE executes:
  - Date.prototype.getTimezoneOffset → fake offset
  - Intl.DateTimeFormat → fake timezone
  - Temporal.Now (Chrome 145+) → fake timezone
  - navigator.geolocation → fake coords with simulated delay
  - navigator.language/languages → fake language
  - RTCPeerConnection → STUN servers filtered out
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
| VPN detection | netsh interface + ipconfig | No |

## State Management

`ProxyState` dataclass persisted as `.geo-fix-state.json` (atomic write):
- PID, preset code, timestamp
- Original proxy settings (WinINET backup)
- Firefox modification flag + backup path
- Firewall rules flag
- Schema-validated on load (unknown fields rejected)
