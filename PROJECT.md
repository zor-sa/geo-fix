# geo-fix — Geo-Signal Spoofing Tool for Windows

## Purpose

Intercept and spoof geolocation signals on Windows to complement VPN IP masking.
Target: access geo-blocked Google services (NotebookLM, Gemini).

## Status: Research Complete → Implementation Planned

## Research Summary (2026-03-27)

### Key Finding

No single open-source tool covers all 5 signals. Need a layered approach.

### Architecture Decision

**Python application** using:

| Signal | Method | Admin Required |
|---|---|---|
| Accept-Language header | mitmproxy addon (rewrite header) | No |
| Timezone (JS) | mitmproxy addon (inject JS to override `Intl.DateTimeFormat`, `Date.getTimezoneOffset`) | No |
| Geolocation API | mitmproxy addon (inject JS to override `navigator.geolocation`) | No |
| WebRTC leak | mitmproxy addon (inject JS: `RTCPeerConnection` override) + optional firewall rules | JS: No, Firewall: Yes |
| DNS leak | Configure browser Secure DNS (DoH) via registry/prefs | No |

**Core stack:**
- **mitmproxy** — local HTTPS proxy with Python addon for header rewriting + JS injection
- **pystray** — system tray icon with country selection menu
- **WinINET registry** (HKCU) — set system proxy without admin (Chrome/Edge pick this up)
- **certutil** — install CA cert to CurrentUser store (no admin, trusted by Chrome/Edge)
- **Firefox prefs.js** — configure proxy + CA for Firefox separately

### Critical Implementation Details

1. **CSP stripping**: Must remove `Content-Security-Policy` headers to allow injected scripts
2. **CA Certificate**: mitmproxy generates CA in `~/.mitmproxy/`; install to CurrentUser (no admin)
3. **Firefox**: Separate CA store (NSS); need `security.enterprise_roots.enabled=true` or cert import
4. **WebRTC via JS**: Override `RTCPeerConnection` to prevent STUN — works without admin, less reliable than firewall but sufficient for most cases
5. **DNS**: Chrome `chrome://settings/security` → Secure DNS with Cloudflare/Google DoH

### Country Presets

| Country | Timezone | Offset (std) | Coords (lat, lon) | Language | Accept-Language |
|---|---|---|---|---|---|
| US | America/New_York | 300 | 38.8951, -77.0364 | en-US | en-US,en;q=0.9 |
| DE | Europe/Berlin | -60 | 52.5200, 13.4050 | de-DE | de-DE,de;q=0.9,en;q=0.8 |
| NL | Europe/Amsterdam | -60 | 52.3676, 4.9041 | nl-NL | nl-NL,nl;q=0.9,en;q=0.8 |
| GB | Europe/London | 0 | 51.5074, -0.1278 | en-GB | en-GB,en;q=0.9 |

Note: Offsets vary with DST. JS injection must compute dynamically based on IANA timezone.

### Evaluated Alternatives (Not Selected)

- **Camoufox**: Covers most signals but replaces the browser entirely — not system-wide
- **Cloaq/Vytal**: Chrome-only extensions, don't modify HTTP headers
- **GeoSpoof**: Firefox-only extension
- **WinDivert**: Low-level packet interception, requires C/C++ development
- **dnscrypt-proxy**: Good for DNS but overkill when browser DoH suffices
- **Browser launch flags** (`--timezone`, `--force-webrtc-ip-handling-policy`): Chrome-only, not system-wide

### File Structure (Planned)

```
geo-fix/
├── PROJECT.md              # This file
├── README.md               # User-facing instructions (Russian)
├── src/
│   ├── main.py             # Entry point, CLI args, tray icon
│   ├── proxy_addon.py      # mitmproxy addon (headers + JS injection)
│   ├── inject.js           # JavaScript payload for timezone/geo/WebRTC spoofing
│   ├── presets.py           # Country presets (timezone, coords, language)
│   ├── system_config.py    # Windows proxy/DNS/cert configuration
│   └── tray.py             # System tray icon with pystray
├── build/
│   └── build.py            # PyInstaller build script
├── requirements.txt
└── test/
    └── test_spoofing.py    # Automated verification tests
```

### Implementation Plan

**Phase 1: Core proxy + JS injection**
1. Create mitmproxy addon with Accept-Language rewriting
2. Create JS injection payload (timezone, geolocation, WebRTC override)
3. Test with manual proxy setup

**Phase 2: System integration**
4. Auto-configure Windows proxy (WinINET registry)
5. Auto-install CA certificate (CurrentUser store)
6. Firefox proxy + cert configuration

**Phase 3: UI + packaging**
7. System tray icon with pystray
8. CLI interface (geo-fix US / geo-fix --stop)
9. Clean shutdown (revert proxy, DNS settings)

**Phase 4: Build + test**
10. PyInstaller packaging to single exe
11. Desktop shortcuts
12. Test against creepjs, browserleaks, dnsleaktest

### Dependencies

- Python 3.10+
- mitmproxy >= 10.0
- pystray
- Pillow (for tray icon)
- PyInstaller (build only)
