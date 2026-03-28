# geo-fix — Project Overview

## What It Does

Windows tray application that spoofs browser geolocation signals through a local HTTPS proxy to complement VPN IP masking. Makes geo-blocked Google services (NotebookLM, Gemini) accessible by patching all browser-side location signals that VPNs don't cover.

## Core Problem

VPNs change your IP address but leave browser signals intact — timezone, Accept-Language headers, navigator.geolocation, WebRTC local IP leaks, and Intl API responses. Google uses these signals to detect real location and block access even when the IP is from the correct region.

## Target Audience

Power users and developers who use VPNs to access region-restricted Google services but are still blocked because browser-side geolocation signals reveal their real location. Expected technical level: intermediate — comfortable with downloading and running unsigned executables, understanding of VPN concepts.

## Key Features

1. **Local HTTPS proxy** — mitmproxy-based interception of all browser traffic
2. **JS injection** — overrides `Intl.DateTimeFormat`, `navigator.geolocation`, `Date.getTimezoneOffset`, `RTCPeerConnection`, `navigator.language/languages`, and Temporal API
3. **Accept-Language header rewriting** — modifies HTTP headers to match target country
4. **Windows system proxy auto-configuration** — sets WinINET registry for Chrome/Edge, Firefox user.js for Firefox
5. **CA certificate auto-install** — mitmproxy CA to CurrentUser store (no admin required)
6. **System tray UI** — country selection, start/stop via pystray icon
7. **First-run setup wizard** — tkinter GUI for CA install, firewall rules, DNS instructions

## Country Presets

US (Washington DC), DE (Berlin), NL (Amsterdam), GB (London) — each with timezone, coordinates, language, Accept-Language header.

## MVP Scope

- Proxy start/stop via tray
- JS injection for timezone, geolocation, WebRTC, language
- Accept-Language header spoofing
- CA cert installation
- Windows proxy registry setup
- Country preset switching

## Out of Scope

- Mobile, macOS, or Linux support
- Full anonymity/privacy tool positioning
- General-purpose proxy with per-site rules
- Browser extension approach
- Non-geolocation fingerprint spoofing (canvas, fonts, WebGL)
- Package manager distribution
