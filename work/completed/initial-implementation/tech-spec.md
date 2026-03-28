---
feature: geo-fix
created: 2026-03-27
status: approved
size: L
branch: feature/geo-fix
---

# Tech Spec: geo-fix — Geo-Signal Spoofing Tool for Windows

## Overview

Windows application that intercepts and spoofs 5 geolocation signals (timezone, WebRTC, DNS, language, Geolocation API) via a local HTTPS proxy to complement VPN. Supports Chrome, Edge, and Firefox browsers, plus desktop apps using system proxy. Target: access geo-blocked Google services (NotebookLM, Gemini).

## Architecture

### High-Level Design

```
┌─────────────┐     ┌──────────────────────┐     ┌──────────┐
│  Browser     │────▶│  Local Proxy          │────▶│  Internet│
│  (Chrome/    │     │  (mitmproxy)          │     │  (via    │
│  Edge/Firefox│◀────│  127.0.0.1:8080       │◀────│   VPN)   │
└─────────────┘     └──────────────────────┘     └──────────┘
                           │
                    ┌──────┴──────┐
                    │ Proxy Addon │
                    │ - Rewrite Accept-Language header
                    │ - Inject JS into HTML responses (target domains only)
                    │ - Modify CSP with nonce (target domains only)
                    └─────────────┘
                           │
                    ┌──────┴──────┐
                    │ Injected JS │
                    │ - Override Intl.DateTimeFormat
                    │ - Override Date.getTimezoneOffset
                    │ - Override Temporal.Now.timeZoneId (if available)
                    │ - Override navigator.geolocation
                    │ - Override navigator.language(s)
                    │ - Override RTCPeerConnection (STUN block)
                    └─────────────┘

┌─────────────┐
│ System Tray  │── Country selection, status, stop
│ (pystray)    │
└─────────────┘

┌─────────────┐
│ Setup        │── One-time: CA cert install, Firefox config, DNS guide
│ (first-run)  │
└─────────────┘
```

### Components

| Component | Responsibility | Technology |
|---|---|---|
| `main.py` | Entry point, CLI parsing, orchestration, single-instance guard, crash recovery | Python 3.10+, argparse |
| `proxy_addon.py` | mitmproxy addon: header rewrite + JS injection + CSP nonce (target domains only) | mitmproxy >= 10.0 |
| `inject.js` | JS payload: timezone, geolocation, language, WebRTC override | Vanilla JS, IIFE |
| `presets.py` | Country presets: timezone, coords, language, Accept-Language | Python dict |
| `system_config.py` | Windows proxy (WinINET registry + Firefox prefs.js), CA cert (certutil + Firefox NSS), state file | winreg, subprocess |
| `tray.py` | System tray icon with context menu, thread-safe country switching | pystray, Pillow |
| `setup_wizard.py` | First-run setup: cert install (Chrome/Edge + Firefox), DNS instructions | tkinter (built-in) |
| `health_check.py` | VPN check (local network adapter inspection), proxy status | socket, subprocess (WMI/netsh) |

### Shared Resources

- **mitmproxy master instance**: owned by `main.py`, consumed by `proxy_addon.py`. Single instance bound to `127.0.0.1:8080`. Protected by single-instance mutex.
- **Country preset state**: owned by `main.py`, read by `proxy_addon.py` and `tray.py`. Changed via tray menu. Access synchronized with threading.Lock for atomic country switching.
- **State file**: owned by `main.py`. Records original proxy settings, active preset, PID. Used for crash recovery. Written atomically (write-then-rename). Validated on read against strict schema.

### Data Flow

1. User launches `geo-fix.exe US`
2. `main.py` validates country code (must be in PRESETS), acquires single-instance mutex
3. Crash recovery: if stale state file found, run cleanup first
4. Load US preset, check VPN status (local adapter check)
5. If first run → `setup_wizard.py` guides through cert install + DNS
6. Save original proxy settings to state file (atomic write)
7. Start mitmproxy on `127.0.0.1:8080` with `proxy_addon.py`
8. Set WinINET proxy via registry (HKCU) + configure Firefox proxy via prefs.js
9. Start tray icon showing "US"
10. Browser traffic flows through proxy:
    - Request: `Accept-Language` header rewritten for ALL requests
    - Response (target domains only): CSP modified with nonce, JS payload injected into HTML `<head>`
    - JS overrides timezone, geolocation, language, WebRTC APIs
11. On stop: revert proxy (registry + Firefox), remove tray icon, stop mitmproxy, delete state file

## Decisions

### D1: mitmproxy over custom proxy

mitmproxy is battle-tested, handles TLS termination, HTTP/2, gzip/brotli decoding, certificate generation. Writing a custom proxy would take weeks and miss edge cases. mitmproxy's Python addon API is simple and covers all our needs.

### D2: All browsers — Chrome, Edge, and Firefox

NFR-3 requires system-wide operation for any browser. Chrome/Edge use WinINET proxy (registry HKCU) and Windows CurrentUser certificate store. Firefox requires separate configuration: proxy via prefs.js (`network.proxy.*`), CA trust via `security.enterprise_roots.enabled=true` (trusts Windows cert store) or direct NSS import. Both paths are handled by `system_config.py` and the setup wizard.

### D3: WebRTC via JS injection (no admin required), optional firewall hardening

Primary: JS-level RTCPeerConnection override — wraps the constructor to block STUN requests. Works without admin rights, covers most scenarios. Optional: setup wizard offers to create firewall rules (netsh, requires admin UAC) for users who want maximum reliability. This keeps NFR-1 (no admin required for core operation) while allowing power users to opt into stronger protection.

### D4: DNS via guided manual setup

Browser Secure DNS (DoH) is the simplest DNS leak prevention. Setup wizard opens browser security settings and shows step-by-step instruction for Chrome/Edge and Firefox separately. No registry/system DNS changes needed.

### D5: Setup wizard over silent install

Non-technical user needs guided experience. tkinter provides a native-looking Windows dialog without external dependencies. Wizard handles: CA cert install (certutil to CurrentUser — no admin needed), Firefox CA config, optional firewall rules (admin UAC), DNS setup guide.

### D6: VPN health check via local network adapter inspection

Check for VPN by inspecting network adapters via WMI/netsh — look for tun/tap/VPN adapter presence. No external API calls (avoids privacy leak). If no VPN adapter detected, warn user but allow proceeding. Fallback: if user has a non-standard VPN, allow them to skip the check.

### D7: PyInstaller single-folder distribution

PyInstaller `--onedir` mode creates a folder with exe + dependencies. Single-file (`--onefile`) has slow startup due to extraction. Folder mode starts instantly and allows updating individual files.

### D8: JS injection strategy — targeted, nonce-based CSP

Inject JS at the very top of `<head>` before any page scripts execute. Use IIFE to avoid polluting global scope. Override native APIs using `Object.defineProperty` with configurable:false for stealth. Handle `Temporal.Now.timeZoneId` with graceful fallback for older browsers.

**CSP handling**: Instead of stripping CSP headers globally, generate a per-response nonce, add it to the injected `<script nonce="...">` tag, and append the nonce to the `script-src` CSP directive. This preserves all other CSP protections (frame-ancestors, form-action, etc.). Apply CSP modification ONLY to responses where JS injection is performed (target domains). All other traffic passes through unmodified.

**Target domains for JS injection**: `*.google.com`, `*.googleapis.com`, `*.gstatic.com`. Accept-Language header rewriting applies to ALL requests.

### D9: Crash recovery and single-instance guard

Single-instance guard via named mutex (Windows) or PID file with liveness check. State file stores: original proxy settings (registry values), active preset, PID, timestamp. Written atomically (write temp file, then rename). On crash: next launch detects stale state (PID not running), runs cleanup before starting. Also: atexit handler for orderly cleanup, `--cleanup` CLI command for manual recovery. State file location: alongside the exe. Validated against strict schema on read — unknown fields rejected.

### D10: Proxy binding security

mitmproxy explicitly bound to `127.0.0.1:8080`, not `0.0.0.0`. Startup assertion verifies bound address. This prevents LAN access to the proxy.

## Testing Strategy

### Unit Tests
- `presets.py`: verify all 4 presets have consistent data (timezone offset matches IANA zone for both summer/winter DST, coords within country bounds)
- `proxy_addon.py`: header rewriting, JS injection into HTML, CSP nonce injection, non-HTML passthrough, chunked HTML handling, target domain filtering
- `system_config.py`: registry key generation (mock winreg), certutil argument construction, Firefox prefs.js generation, firewall rule naming, cleanup-on-failure paths, atomic state file operations
- `health_check.py`: VPN detection logic with mock adapters (VPN present, absent, ambiguous)
- `tray.py`: menu item generation for all presets, country switch callback, shutdown sequence
- `main.py`: CLI argument validation (valid codes, invalid codes, --stop, --cleanup), single-instance guard, stale state detection

### Integration Tests
- Start mitmproxy with addon, send HTTP request through it, verify response has injected JS and modified headers
- Verify JS payload executes correctly in a browser context (Playwright): timezone, language, geolocation return correct values
- DST tests: verify getTimezoneOffset returns correct value for summer date (EDT/CEST/BST) and winter date (EST/CET/GMT) for each preset
- Crash simulation: start geo-fix, kill process, re-launch, verify cleanup runs and proxy settings are restored
- Concurrent launch: start geo-fix, attempt second launch, verify graceful rejection
- Country switching: while proxy is running, switch country via tray, verify in-flight requests get consistent single-country preset (no mixed data)

### E2E Tests (Smoke)
- Launch full stack, open creepjs in headless Chrome via Playwright, verify:
  - AC-1: Timezone matches preset, language matches preset, no Russia references
  - AC-2: No WebRTC IP leak (check via browserleaks.com/webrtc equivalent JS check)
  - AC-7: Geolocation matches preset coordinates
- DNS leak check: verify browser DNS resolution goes through DoH (not plaintext to ISP)
- Repeat key checks in Firefox to verify cross-browser support

### Manual Verification Checklist
Pre-conditions: VPN active, browsers closed, geo-fix not running.

1. **AC-1 (Fingerprint)**: Launch geo-fix US → open creepjs → verify timezone shows America/New_York, language en-US, no red flags, no "Russia". Expected: all green/yellow, no Russia.
2. **AC-2 (WebRTC)**: Open browserleaks.com/webrtc → verify no real IP visible. Expected: "No leak" or VPN IP only.
3. **AC-3 (DNS)**: Open dnsleaktest.com → run extended test → verify no Russian DNS servers. Expected: only US/international DNS servers.
4. **AC-4 (IP)**: Open ipapi.co → verify country matches VPN country. Expected: US (or selected country).
5. **AC-5 (Google access)**: Open notebooklm.google.com → verify page loads without geo-block. Open gemini.google.com → verify same. Expected: full access, UI in target language.
6. **AC-6 (Deactivation)**: Click "Выключить" in tray → verify proxy removed (check Windows proxy settings), browser works normally. Expected: no proxy, normal browsing.
7. **AC-7 (Tray)**: Verify icon visible, right-click shows menu, switch to DE → verify creepjs shows Europe/Berlin. Expected: menu works, country switches cleanly.

## Implementation Tasks

### Wave 1: Core Engine

**Task 1: Country presets module**
- Description: Create the presets data module with all 4 country configurations (timezone, coordinates, language, Accept-Language header). Each preset must have internally consistent data that matches what a real user in that country would produce. Include target domain list for JS injection scope.
- Skill: `code-writing`
- Reviewers: `code-reviewer`
- Files to modify: `src/presets.py` (new)
- Files to read: `work/geo-fix/user-spec.md`

**Task 2: JavaScript injection payload**
- Description: Create the JS payload that overrides browser APIs for timezone, geolocation, language, and WebRTC. Must execute before page scripts via IIFE, use Object.defineProperty for stealth, dynamically compute timezone offset based on IANA zone name (DST-aware), and gracefully handle Temporal API absence in older browsers.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`
- Verify-smoke: `python -c "from pathlib import Path; js = Path('src/inject.js').read_text(); assert all(x in js for x in ['getTimezoneOffset', 'RTCPeerConnection', 'getCurrentPosition', 'defineProperty'])"`
- Files to modify: `src/inject.js` (new)
- Files to read: `work/geo-fix/tech-spec.md` (D8: injection strategy)

**Task 3: mitmproxy addon**
- Description: Create the mitmproxy addon that rewrites Accept-Language headers on all requests, and for target domains only: injects JS payload with nonce into HTML responses and modifies CSP to allow the nonce. Must handle gzip/brotli encoding, use HTML parser for injection point, skip non-HTML and large responses (>5MB), and pass through non-target traffic unmodified.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`, `test-reviewer`
- Verify-smoke: `python -c "from src.proxy_addon import GeoFixAddon; from src.presets import PRESETS; a = GeoFixAddon(PRESETS['US']); print('addon instantiates OK')"`
- Files to modify: `src/proxy_addon.py` (new)
- Files to read: `src/inject.js`, `src/presets.py`

### Wave 2: System Integration

**Task 4: Windows system configuration**
- Description: Create the module that manages Windows proxy settings (WinINET registry HKCU for Chrome/Edge + Firefox prefs.js), CA certificate installation (certutil to CurrentUser store for Chrome/Edge + Firefox enterprise_roots or NSS), optional firewall rules (netsh for WebRTC STUN blocking), and atomic state file for crash recovery. All changes must be reversible. State file uses write-then-rename for atomicity.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`
- Files to modify: `src/system_config.py` (new)
- Files to read: `src/presets.py`

**Task 5: Health check module**
- Description: Create the VPN health check that detects VPN presence by inspecting local network adapters (WMI/netsh) without external API calls. Also provides a proxy-is-running status check and a single-instance mutex guard. No external network calls for privacy.
- Skill: `code-writing`
- Reviewers: `code-reviewer`
- Verify-smoke: `python -c "from src.health_check import HealthCheck; print('health check loads OK')"`
- Files to modify: `src/health_check.py` (new)
- Files to read: none

### Wave 3: UI

**Task 6: Setup wizard**
- Description: Create a first-run setup wizard using tkinter that guides the user through CA certificate installation (Chrome/Edge + Firefox), optional firewall rules, and DNS configuration for each browser. Must show clear explanations in Russian, handle errors gracefully, and persist setup completion state to prevent re-running.
- Skill: `code-writing`
- Reviewers: `code-reviewer`
- Verify-user: Launch wizard, verify each step works and UI is clear
- Files to modify: `src/setup_wizard.py` (new)
- Files to read: `src/system_config.py`, `src/health_check.py`

**Task 7: System tray icon**
- Description: Create the tray icon module using pystray that shows active country, provides context menu for country switching and shutdown. Country switching must be thread-safe (synchronized with proxy addon). Tray icon must update when country changes and cleanly remove on exit.
- Skill: `code-writing`
- Reviewers: `code-reviewer`
- Verify-user: Launch tray, verify menu items, country switching, and clean exit
- Files to modify: `src/tray.py` (new)
- Files to read: `src/presets.py`

### Wave 4: Entry Point and Packaging

**Task 8: Main entry point and CLI**
- Description: Create the main orchestrator that validates CLI arguments, acquires single-instance mutex, runs crash recovery if needed, runs setup wizard if first time, starts mitmproxy with addon on 127.0.0.1:8080, configures system proxy, launches tray icon, and handles clean shutdown via atexit handler. Supports --stop and --cleanup commands.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`, `test-reviewer`
- Files to modify: `src/main.py` (new)
- Files to read: all `src/*.py`

**Task 9: PyInstaller build and desktop shortcuts**
- Description: Create build script that packages the application with PyInstaller in onedir mode. Generate Windows .lnk desktop shortcuts for all 4 countries (US, DE, NL, GB) and a stop shortcut. Include all assets (icons, JS payload).
- Skill: `code-writing`
- Reviewers: `code-reviewer`
- Verify-smoke: `pyinstaller --onedir src/main.py --name geo-fix --noconfirm && test -f dist/geo-fix/geo-fix.exe`
- Files to modify: `build/build.py` (new), `build/geo-fix.spec` (new)
- Files to read: `src/main.py`

**Task 10: User documentation (README)**
- Description: Write README.md in Russian for non-technical users. Cover: what the tool does (and does NOT do — no data logging), installation, first-time setup (wizard), daily usage (shortcuts), how to verify it works (creepjs step-by-step), troubleshooting (internet broken after crash → run stop shortcut), uninstallation (--cleanup removes all traces), and antivirus false positive warnings.
- Skill: `documentation-writing`
- Reviewers: `code-reviewer`
- Files to modify: `README.md` (new)
- Files to read: `work/geo-fix/user-spec.md`

### Wave 5: Audit

**Task 11: Code Audit**
- Description: Holistic code quality review of all geo-fix source code. Review covers code structure, error handling, edge cases, thread safety of country switching, and adherence to Python best practices. Output is a findings report with severity levels and recommended fixes.
- Skill: `code-reviewing`
- Reviewers: none
- Files to modify: none
- Files to read: `src/**/*.py`, `src/inject.js`

**Task 12: Security Audit**
- Description: OWASP Top 10 review across all components. Special focus: CA certificate key file permissions, proxy binding address verification, CSP nonce implementation correctness, state file integrity, registry modification safety, input validation on CLI args and state file contents.
- Skill: `security-auditor`
- Reviewers: none
- Files to modify: none
- Files to read: `src/**/*.py`, `src/inject.js`

**Task 13: Test Audit**
- Description: Test quality and coverage review across all test files. Verify: crash recovery tested, concurrent launch tested, DST transitions tested, country switching thread safety tested, Firefox configuration tested, all AC criteria have corresponding tests.
- Skill: `test-master`
- Reviewers: none
- Files to modify: none
- Files to read: `test/**/*.py`, `src/**/*.py`

### Wave 6: Final

**Task 14: QA — Pre-deploy acceptance testing**
- Description: Run all tests, verify all acceptance criteria from user-spec (AC-1 through AC-7). Test on creepjs, browserleaks, dnsleaktest in both Chrome and Firefox. Verify tray icon, country switching, clean shutdown, crash recovery, and uninstall cleanup.
- Skill: `pre-deploy-qa`
- Reviewers: none
- Verify-smoke: `pytest test/ -v && python -c "from src.main import main; print('app loads')"`
- Verify-user: Full manual test cycle per Manual Verification Checklist above
- Files to modify: none
- Files to read: `work/geo-fix/user-spec.md`, `test/**/*.py`, `src/**/*.py`

## Dependencies

### External (Python packages)
| Package | Version | Purpose |
|---|---|---|
| mitmproxy | >= 10.0 | Local HTTPS proxy with addon API |
| pystray | >= 0.19 | System tray icon |
| Pillow | >= 10.0 | Image handling for tray icon |
| pyinstaller | >= 6.0 | Build only — exe packaging |

### System (Windows built-in)
| Tool | Purpose |
|---|---|
| certutil.exe | CA certificate installation to CurrentUser store (no admin) |
| netsh.exe | Optional firewall rules for WebRTC STUN blocking (admin) |
| winreg (Python) | WinINET proxy configuration via registry |
| tkinter (Python) | Setup wizard UI |

### No external paid dependencies.

## Security Considerations

- **Proxy binding**: Explicitly bound to `127.0.0.1:8080`. Startup assertion verifies. Prevents LAN access.
- **CA Certificate**: Generated locally, never transmitted. After generation, restrict key file permissions to current user only (icacls). Stored in `~/.mitmproxy/`.
- **CSP preservation**: CSP headers modified (not stripped) only on target domains. Nonce added to script-src for injected script. All other CSP directives preserved. Non-target traffic passes through with original CSP intact.
- **Registry modifications**: Only HKCU (current user), no system-wide changes. Original values saved to state file before modification. Reverted on shutdown.
- **Firewall rules**: Optional. If created: per-application (chrome.exe, msedge.exe, firefox.exe), unique naming prefix `geo-fix-*`. Removed on --stop or --cleanup.
- **No data logging**: The proxy addon processes traffic in memory only. No request/response data is stored to disk. Minimal security event log: start/stop timestamps, connection count, non-localhost connection attempts (7-day rotation).
- **State file**: Written atomically (temp + rename). Validated against strict schema on read. Does not store proxy address (hardcoded 127.0.0.1:8080). Stores only: original proxy settings, preset name, PID, timestamp.
- **Crash recovery**: atexit handler for orderly shutdown. State file enables recovery on next launch. `--cleanup` command for manual recovery. README documents "internet broken" recovery step.
- **Input validation**: CLI country code validated: uppercase, 2 ASCII letters, must exist in PRESETS dict. Reject and exit before any system changes on invalid input.
- **Single-instance guard**: Named mutex prevents double-launch. Second instance exits with clear message.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Chrome update changes proxy API | Proxy stops working | WinINET registry is stable Win32 API, unlikely to change |
| Antivirus flags mitmproxy | User panics, deletes tool | Document expected warnings in README, provide whitelist instructions |
| CreepJS detects JS override | Fingerprint inconsistency | Object.defineProperty with configurable:false, regular testing |
| Certificate-pinned sites break | Some sites fail to load | Maintain bypass list for known pinned domains |
| User forgets VPN | Real IP exposed | Local adapter check warns if no VPN detected |
| App crash leaves proxy set | All browser traffic fails | atexit handler + state file recovery + --cleanup command + README troubleshooting |
| Concurrent double-launch | Port conflict, corrupt state | Single-instance mutex prevents |
| Firefox prefs.js format changes | Firefox proxy config breaks | Use documented, stable Mozilla prefs format |
