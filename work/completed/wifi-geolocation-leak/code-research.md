# Code Research: wifi-geolocation-leak

**Date:** 2026-03-30
**Feature context:** WiFi geolocation leaks real location via MAC-address databases regardless of VPN/IP. Two vectors: (1) browser `navigator.geolocation` on non-target domains, (2) desktop apps bypassing proxy via Windows Location Services. Feature evaluates current coverage and selects mitigation approach.

---

## 1. Entry Points

### proxy_addon.py — response() JS injection gate
**File:** `/home/claude/workspace/projects/geo-fix/src/proxy_addon.py`

`GeoFixAddon.response()` is the gating function for JS injection. It runs on every proxied HTTP response and applies four sequential guards before injecting:

```python
def response(self, flow: http.HTTPFlow) -> None:
    if flow.response is None: return
    if not is_target_domain(flow.request.host): return          # LINE 160 — domain gate
    if "text/html" not in content_type.lower(): return          # LINE 165 — content-type gate
    if len(flow.response.content) > MAX_INJECT_SIZE: return     # LINE 169 — size gate (5MB)
    if flow.response.status_code != 200: return                 # LINE 173 — status gate
```

Only after all four pass does it: (a) find inject position in HTML, (b) generate a nonce, (c) build the script tag, (d) splice into HTML text, (e) modify CSP header if present.

**Key signatures:**
- `GeoFixAddon.response(flow: http.HTTPFlow) -> None` — lines 153–204
- `GeoFixAddon.request(flow: http.HTTPFlow) -> None` — lines 146–151, rewrites Accept-Language on ALL requests (no domain check)
- `is_target_domain(host: str) -> bool` — in `presets.py`, checks suffix match against `TARGET_DOMAINS`

### proxy_addon.py — request() handler (geolocation API intercept hook point)
`GeoFixAddon.request()` currently has a single responsibility: overwrite `Accept-Language` on every request. It does not inspect URL path or perform any blocking. The natural extension point for intercepting Google Geolocation API calls is here:

```python
def request(self, flow: http.HTTPFlow) -> None:
    with self._lock:
        self._last_flow_time = time.monotonic()
        accept_lang = self._preset.accept_language
    flow.request.headers["Accept-Language"] = accept_lang
    # NEW: check flow.request.pretty_url for googleapis.com/geolocation/v1/geolocate
```

The intercept would need to match `https://www.googleapis.com/geolocation/v1/geolocate` POST requests and either block them (return a 403/fake response) or replace the response body with spoofed coordinates. mitmproxy allows setting `flow.response` directly in `request()` to short-circuit without forwarding.

### presets.py — TARGET_DOMAINS
**File:** `/home/claude/workspace/projects/geo-fix/src/presets.py`

Current target domains (JS injection scope):
```python
TARGET_DOMAINS: List[str] = [
    ".google.com", ".googleapis.com", ".gstatic.com",
    ".google.co.uk", ".google.de", ".google.nl",
]
```

`is_target_domain(host)` — lines 78–81, case-insensitive suffix check using a pre-built tuple for O(n) scan. Used exclusively in `response()` to gate JS injection.

### inject.js — geolocation override
**File:** `/home/claude/workspace/projects/geo-fix/src/inject.js`

The geolocation override is in the IIFE at lines 144–196. It overrides three methods on `navigator.geolocation`:
- `getCurrentPosition` — resolves with `fakePosition` after 50–150ms random delay (lines 159–165)
- `watchPosition` — resolves with `fakePosition` every 3000ms via `setInterval` (lines 167–176)
- `clearWatch` — delegates to `clearInterval` (lines 178–180)

The `fakePosition` object uses `GF_LAT`/`GF_LON` from preset. The guard `if (navigator.geolocation)` at line 183 means no override is attempted if geolocation is absent.

**Critical gap:** This override only executes on pages from `TARGET_DOMAINS`. Any page from a non-target domain (CDNs, third-party trackers, extensions) still calls the real `navigator.geolocation`.

---

## 2. Data Layer

### ProxyState dataclass
**File:** `/home/claude/workspace/projects/geo-fix/src/system_config.py`, lines 76–103

```python
@dataclass
class ProxyState:
    pid: int
    preset_code: str
    timestamp: str
    original_proxy_enable: Optional[int] = None
    original_proxy_server: Optional[str] = None
    original_proxy_override: Optional[str] = None
    firefox_prefs_modified: bool = False
    firefox_prefs_backup: Optional[str] = None
    session_id: Optional[str] = None
    session_tmpdir: Optional[str] = None
    ca_thumbprint: Optional[str] = None
    proxy_port: Optional[int] = None
```

Serialized as DPAPI-encrypted binary blob at `STATE_FILE` (next to executable). `from_json()` enforces strict schema — unknown fields raise `ValueError`.

If a wifi-geolocation-leak feature adds new persistent state (e.g., whether Windows Location Services was disabled), a new field must be added here and the schema version comment on `_DPAPI_ENTROPY = b"geo-fix-state-v1"` should be evaluated for bumping.

### CountryPreset dataclass
**File:** `/home/claude/workspace/projects/geo-fix/src/presets.py`, lines 11–20

Frozen dataclass with fields: `code`, `name_ru`, `timezone`, `latitude`, `longitude`, `language`, `accept_language`. The `latitude`/`longitude` fields are the values substituted into `inject.js` placeholders `__GF_LAT__` and `__GF_LON__`. No WiFi-related fields exist here.

---

## 3. Similar Features

### WebRTC leak prevention (inject.js, lines 219–262)
The RTCPeerConnection wrapping in inject.js is the closest analogue. It patches `window.RTCPeerConnection` globally within the IIFE, strips STUN/TURN servers from ICE config, and uses `stealthDefine` + `disguiseFunction` for stealth. This same pattern would apply to extending geolocation override.

**Pattern:** wrap native, call original only in controlled cases, disguise override with `[native code]` toString.

### Firewall-level STUN blocking (system_config.py, lines 513–547)
`create_firewall_rules()` blocks STUN/TURN ports at the OS level via `netsh advfirewall` as a defense-in-depth layer alongside the JS override. This is the analogous pattern for blocking Geolocation API requests at the proxy level — system-level enforcement complements JS-level spoofing.

---

## 4. Integration Points

### _modify_csp() — CSP nonce injection
**File:** `/home/claude/workspace/projects/geo-fix/src/proxy_addon.py`, lines 80–122

`_modify_csp(csp_value: str, nonce: str) -> str` modifies the `content-security-policy` response header so the injected `<script nonce="...">` tag passes the page's CSP. Three cases:
1. `script-src` exists → append `'nonce-{nonce}'` to it.
2. No `script-src` but `default-src` exists → derive `script-src` from `default-src`, filtering out `'unsafe-inline'`, `'unsafe-eval'`, `'unsafe-hashes'`.
3. Neither exists → add minimal `script-src 'nonce-{nonce}'`.

**Risks for extending to all domains:**
- Currently CSP is only modified for target domains (inside the `is_target_domain` guard). Extending injection to all domains means modifying CSP on arbitrary sites. This is risky because:
  - Some sites use `script-src 'none'` — adding only a nonce may break their policy in unexpected ways.
  - Sites with `require-trusted-types-for 'script'` will reject the injected script regardless of nonce.
  - `report-only` CSP is deliberately left unmodified (line 199: comment "Modify enforcing CSP header only") — this is intentional and correct.
  - Injecting into every HTML response significantly increases proxy processing load and attack surface.
- The function does NOT modify `content-security-policy-report-only` — this is correct; modifying report-only headers would cause false violation reports to site owners.
- The `_unsafe_tokens` set at line 108 uses string literal comparison. The `.lower()` call is only on `parts[0].lower()` (the directive name), not on token values. However at line 74 the case-insensitive unsafe filter in `test_csp.py` tests `'UNSAFE-INLINE'` — the current code at line 108 does `t.lower() not in _unsafe_tokens` which handles case correctly.

### _build_js_payload() — template substitution
Lines 59–72. Reads `_JS_TEMPLATE` (loaded at module level from `inject.js`), performs five string replacements for timezone, lat, lon, lang, and langs. No escaping of the preset values is performed — the presets are hardcoded dataclasses so injection is not a concern for current data, but would need attention if presets became user-configurable.

### FlowCleanup addon (proxy_addon.py, lines 207–236)
Must be registered after `GeoFixAddon` in the addon chain. It clears `flow.request.content` and `flow.response.content` after processing to reduce memory pressure. Any new addon that reads request/response bodies must be registered before `FlowCleanup`.

---

## 5. Existing Tests

**Framework:** pytest + unittest.mock. No fixtures file beyond `test/conftest.py`. Tests use manual fake classes (FakeFlow, FakeRequest, FakeResponse) rather than mitmproxy's test utilities.

### proxy_addon tests — `/home/claude/workspace/projects/geo-fix/test/test_proxy_addon.py`
Representative signatures:
```python
def test_response_injects_js_for_target_domain(self, addon, make_flow):
    # Asserts "<script nonce=" and "getTimezoneOffset" appear after response()
def test_response_skips_non_target_domain(self, addon, make_flow):
    # host="example.com" → response text unchanged
```

**Coverage gaps for this feature:**
- No test for `request()` blocking/intercepting a specific URL path (e.g., googleapis.com geolocation endpoint).
- No test verifying geolocation override on non-target domain HTML (because injection doesn't happen there yet).
- No test for `fakePosition` values in the injected JS matching the preset's lat/lon.

### CSP tests — `/home/claude/workspace/projects/geo-fix/test/unit/test_csp.py`
Comprehensive unit coverage of `_modify_csp()`: nonce appending, unsafe token filtering, fallback to default-src, minimal nonce-only policy. All cases covered for current behavior.

### system_config tests — `/home/claude/workspace/projects/geo-fix/test/test_system_config.py`
Representative signatures:
```python
def test_list_rules_parses_netsh_output(self):  # mocks subprocess.run
def test_remove_by_prefix_deletes_found_rules(self, mock_run, mock_list):
def test_save_and_load(self, tmp_path, monkeypatch):  # monkeypatches STATE_FILE
```

**Coverage gaps for this feature:**
- No tests for `check_pending_cleanup()` retry behavior for any new cleanup label.
- No tests for Windows Location Services registry operations (not yet implemented).

---

## 6. Shared Utilities

### stealthDefine / stealthDefineGetter / disguiseFunction (inject.js, lines 15–39)
Helper triad for patching browser APIs invisibly:
- `stealthDefine(obj, prop, descriptor)` — wraps `Object.defineProperty`, swallows errors if property already frozen.
- `stealthDefineGetter(obj, prop, getter)` — shorthand for getter-only `stealthDefine`.
- `disguiseFunction(fn, name)` — overrides `fn.toString` to return `'function name() { [native code] }'`.

All geolocation overrides in inject.js use these helpers. Any new browser API patch should follow the same pattern.

### Cleanup infrastructure (system_config.py, lines 609–700)

**Cleanup labels** (lines 40–45):
```python
CLEANUP_LABEL_CA_CERT = "CA cert removal"
CLEANUP_LABEL_SESSION_TMPDIR = "Session tmpdir deletion"
CLEANUP_LABEL_PROXY = "Proxy restore"
CLEANUP_LABEL_FIREFOX = "Firefox restore"
CLEANUP_LABEL_FIREWALL = "Firewall removal"
```

**`_VALID_CLEANUP_LABELS`** (line 609) — frozen set of all valid labels; used in `check_pending_cleanup()` to reject unknown strings before dispatch.

**`cleanup(state)` retry pattern** (lines 763–827): inner `_try_step(label, func, *args)` function executes each cleanup op, sleeps `_CLEANUP_RETRY_DELAY = 3` seconds on failure, retries once, appends label to `failures` list if second attempt also fails. Returns `failures` list to caller. The cleanup order is: CA cert → session tmpdir → proxy → Firefox → firewall → state file deletion.

**`write_cleanup_pending(failed_ops)` / `check_pending_cleanup()`** — persistence layer for cross-restart cleanup retry. File at `CLEANUP_PENDING_FILE` (`%APPDATA%/geo-fix/cleanup_pending.json`). Adding a new cleanup step (e.g., "Location Services restore") requires: (a) new label constant, (b) entry in `_VALID_CLEANUP_LABELS`, (c) branch in `_execute_cleanup_by_label()`, (d) call in `cleanup()`.

### Windows registry pattern (system_config.py, lines 200–277)

**Read** (`_get_registry_proxy_settings`, lines 200–220):
```python
import winreg
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
    enable = winreg.QueryValueEx(key, "ProxyEnable")[0]  # REG_DWORD
    server = winreg.QueryValueEx(key, "ProxyServer")[0]  # REG_SZ
```

**Write** (`set_wininet_proxy`, lines 223–241):
```python
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
    winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
    winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, proxy_addr)
```
Always followed by `_notify_proxy_change()` which calls `InternetSetOptionW` with options 39 and 37 to propagate the change.

**Platform guard pattern:** every registry function has `if sys.platform != "win32": return` early exit, used consistently throughout. Any new registry operation for Windows Location Services should follow the same pattern.

**Location Services registry path** (not yet in codebase — for reference):
`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\location` — value `Value` (REG_SZ) set to `"Deny"` to block. Requires `HKEY_LOCAL_MACHINE` + elevated write access, unlike the proxy settings which use `HKEY_CURRENT_USER`.

---

## 7. Potential Problems

### Geolocation coverage gap — inject.js only covers target domains
`navigator.geolocation` override runs only in pages injected from `TARGET_DOMAINS`. Third-party iframes, browser extensions, and non-Google sites bypass it entirely. The BACKLOG question "does inject.js actually block real geolocation?" is answerable: yes, it replaces the methods, but only in the main frame of target-domain pages. Iframes from other origins have their own JS context.

### Extending injection to all domains — CSP modification at scale
If `is_target_domain` check is removed from `response()`, every HTML page gets JS injection + CSP modification. Risks:
- Sites using `script-src 'none'` will have their policy silently weakened.
- Sites using `require-trusted-types-for 'script'` will reject the script despite valid nonce.
- Significant performance impact: every HTML response (not just Google) now gets decoded, string-searched, and re-encoded.
- HTTPS inspection of banking/health sites — legal and ethical exposure.

### Google Geolocation API intercept — request body leaks MAC addresses
The POST body of `https://www.googleapis.com/geolocation/v1/geolocate` contains WiFi scan results with MAC addresses. Even if `navigator.geolocation` is overridden at the JS layer, Chrome's internal geolocation code (C++) may still send this request. Intercepting in `request()` handler can block or replace the request, but the MAC addresses will have already been scanned by Windows. The proxy can prevent exfiltration but cannot prevent scanning.

### Windows Location Services — requires HKLM write (elevation)
Disabling via `HKLM\...\CapabilityAccessManager` requires admin rights. geo-fix currently runs without elevation. Adding a non-elevated fallback (e.g., per-user `HKCU` setting or netsh wlan command) needs research.

### Missing error handling — request() intercept path
The current `request()` method has no try/except. If a new geolocation API intercept raises (e.g., malformed URL), it will propagate to mitmproxy and could terminate flow processing for that connection. The pattern in `response()` wraps the text decode in try/except — the same should apply to any new request-path logic.

### Race condition — `_last_flow_time` update in request()
`_last_flow_time` is updated under `self._lock` in `request()`. Adding request-path logic (URL checks, response fabrication) while holding the lock could extend lock contention. URL pattern matching should happen before acquiring the lock.

---

## 8. Constraints & Infrastructure

### mitmproxy addon chain ordering
`FlowCleanup` must be the last addon. Any new addon that reads request/response bodies must be inserted before it. If a geolocation-blocking addon is added, it must precede `FlowCleanup` in the list passed to `mitmproxy.options`.

### Fabricating responses in request() hook
mitmproxy allows `flow.response = http.Response.make(...)` inside `request()` to short-circuit proxy forwarding. This is the correct approach for blocking/spoofing the Google Geolocation API endpoint without the request leaving the machine.

### No test infrastructure for Windows-specific registry ops
Tests for `set_wininet_proxy` / `unset_wininet_proxy` are absent from `test_system_config.py` — those functions are guarded by `if sys.platform != "win32"` which causes them to no-op in the Linux CI environment. The same will apply to any Location Services registry operation. Tests must mock `sys.platform` and `winreg` (as done in firewall tests with `@patch("src.system_config.sys.platform", "win32")`).

### State file schema evolution
`ProxyState.from_json()` rejects unknown fields. Adding a new field (e.g., `location_services_disabled: bool = False`) is backward-compatible (new field has default). Removing or renaming a field breaks existing state files — load returns `None` due to schema mismatch, triggering stateless cleanup.

### Pre-built constants for performance
`TARGET_DOMAINS` is pre-converted to `_TARGET_DOMAINS_TUPLE` at module load. The JS template is read once at `_JS_TEMPLATE = _JS_TEMPLATE_PATH.read_text()` at module level. Any new domain lists or templates should follow this pattern to avoid per-request I/O.

### Platform: Windows-only production, Linux CI
`sys.platform != "win32"` guards all registry and DPAPI operations. Tests run on Linux. Firewall tests mock `sys.platform`. DPAPI falls back to plaintext passthrough on non-Windows.

---

## 9. Key Function Index

| Function | File | Lines | Purpose |
|---|---|---|---|
| `GeoFixAddon.response()` | proxy_addon.py | 153–204 | Gates JS injection by domain, content-type, size, status |
| `GeoFixAddon.request()` | proxy_addon.py | 146–151 | Rewrites Accept-Language on ALL requests |
| `_modify_csp()` | proxy_addon.py | 80–122 | Adds nonce to CSP script-src, derives from default-src |
| `is_target_domain()` | presets.py | 78–81 | Suffix-match host against TARGET_DOMAINS |
| `cleanup()` | system_config.py | 763–827 | Reverts all system changes; retry-once per step |
| `check_pending_cleanup()` | system_config.py | 657–700 | Startup retry of failed cleanup from previous session |
| `write_cleanup_pending()` | system_config.py | 618–627 | Persists failed cleanup labels to JSON |
| `_execute_cleanup_by_label()` | system_config.py | 638–654 | Dispatch cleanup by label string |
| `set_wininet_proxy()` | system_config.py | 223–241 | Writes proxy to HKCU registry, notifies Windows |
| `unset_wininet_proxy()` | system_config.py | 245–261 | Restores original HKCU proxy settings |
| `_get_registry_proxy_settings()` | system_config.py | 200–220 | Reads current proxy from HKCU registry |
| `fakeGetCurrentPosition()` | inject.js | 159–165 | Returns preset coords with 50–150ms delay |
| `fakeWatchPosition()` | inject.js | 167–176 | Intervals preset coords every 3s |
| `stealthDefine()` | inject.js | 15–22 | defineProperty wrapper, swallows errors |
| `disguiseFunction()` | inject.js | 28–38 | Makes override toString return native code |
