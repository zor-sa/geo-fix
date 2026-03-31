---
created: 2026-03-30
status: approved
size: M
branch: feature/wifi-geolocation-leak
---

# Tech Spec: WiFi Geolocation Leak Protection

## Solution

Close the WiFi geolocation leak through three defense layers:

1. **Windows Location Services disable** — set HKCU registry key to deny location access for the current user at startup, restore original value on cleanup. This blocks WiFi AP scanning system-wide for the user's processes.

2. **Universal geolocation JS injection** — extend `navigator.geolocation` override from TARGET_DOMAINS to all domains. On non-target domains, inject a minimal geolocation-only payload (no timezone/language) to reduce CSP conflict risk and performance impact.

3. **Proxy-level API intercept** — intercept POST requests to `googleapis.com/geolocation/v1/geolocate` in `GeoFixAddon.request()` and return a synthetic response with the current preset's coordinates. Defense-in-depth for cases where JS injection fails.

Additionally, override `navigator.permissions.query` for geolocation to return `{state: 'granted'}` consistently.

## Architecture

### What we're building/modifying

- **system_config.py** — new functions: `disable_location_services()` / `restore_location_services()` for HKCU registry, new cleanup label + integration into existing retry/pending pattern
- **proxy_addon.py** — modify `response()` to inject geolocation-only JS on non-target domains; add geolocation API intercept in `request()`
- **inject.js** — add `navigator.permissions.query` override; extract geolocation-only subset for non-target injection
- **main.py** — call `disable_location_services()` at startup, pass original value to ProxyState
- **presets.py** — no changes (lat/lon already available in CountryPreset)

### How it works

```
Startup:
  main.py → disable_location_services() → save original value to ProxyState

Request flow (new):
  Browser POST googleapis.com/geolocation → request() intercepts → fake JSON response
  (MAC addresses never leave the machine)

Response flow (modified):
  Any domain HTML response → response() injects geolocation-only JS
  Target domain HTML response → response() injects full JS payload (as before)

  Injected JS:
    navigator.geolocation → fake coords (existing)
    navigator.permissions.query({name:'geolocation'}) → {state:'granted'} (new)

Cleanup:
  restore_location_services() → original registry value restored
  cleanup label "Location Services restore" → retry + pending file
```

### Shared resources

None.

## Decisions

### Decision 1: HKCU registry path for Location Services

**Decision:** Use `HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\DeviceAccess\Global\{BFA794E4-F964-4FDB-90F6-51056BFE4B44}` with value `Value` set to `Deny`. If this path is not available or doesn't work, fall back gracefully (log warning, continue without registry block).

**Rationale:** This HKCU path controls per-user location access without admin elevation. The HKLM path (`CapabilityAccessManager\ConsentStore\location`) requires admin which geo-fix doesn't have. The HKCU path must be verified during implementation on a real Windows machine — if it doesn't exist on target Windows versions, this layer is documented as a limitation.

**Alternatives considered:** HKLM path (requires admin, rejected); `netsh wlan` (no relevant command for location services, rejected); Group Policy (requires domain/local admin, rejected).

### Decision 2: Two-tier JS injection (full vs geolocation-only)

**Decision:** On target domains, inject the full payload (timezone, geolocation, language, WebRTC, permissions). On non-target domains, inject only geolocation + permissions override.

**Rationale:** Full injection on all domains would increase CSP conflicts and processing overhead without benefit — timezone/language spoofing is only needed for Google services. The geolocation-only payload is smaller and less likely to conflict with strict CSPs.

**Alternatives considered:** Full payload everywhere (higher CSP risk, higher performance cost); no injection on non-target (leaves the leak open); geolocation override in a separate lightweight addon (unnecessary complexity).

### Decision 3: Proxy-level geolocation API intercept as defense-in-depth

**Decision:** Intercept requests matching exact `host == 'www.googleapis.com'` AND `path == '/geolocation/v1/geolocate'` AND `method == 'POST'` in `request()` handler. Return a synthetic `200 OK` with fake coordinates and randomized accuracy (40–80m). Do not intercept Microsoft Location Service separately.

**Rationale:** Even with JS override, Chrome's internal geolocation stack (C++) may send the API request before JS runs. Intercepting at proxy level prevents MAC address exfiltration. Microsoft's service is blocked by the registry disable. Error handling: if intercept fails, log and pass request through. Exact host/path/method match prevents over-broad interception. Randomized accuracy avoids fingerprinting via fixed value.

**Alternatives considered:** Block request entirely with 403 (triggers JS error callback, detectable); substring URL match (over-broad and bypassable); intercept both Google and Microsoft APIs (registry covers Microsoft); fixed accuracy value (fingerprintable).

### Decision 4: ProxyState field for Location Services

**Decision:** Add `original_location_services: Optional[str] = None` field to ProxyState. Stores the registry value read before disabling (`Allow`, `Deny`, or `None` if key didn't exist). `restore_location_services()` validates the value is in `{'Allow', 'Deny', None}` — any other value defaults to `Deny` (safe default). When `None`, the registry key is deleted (restoring "no key existed" state).

**Rationale:** Must restore exact original state on cleanup — if user had location disabled before geo-fix, don't re-enable it. The field has a default, so existing state files remain loadable (backward compatible). Input validation prevents a tampered state file from forcing unexpected registry values.

### Decision 5: CSP skip guard for maximally restrictive policies

**Decision:** On non-target domains, if the page has `script-src 'none'` or `require-trusted-types-for 'script'` in CSP, skip JS injection entirely. The proxy-level geolocation API intercept remains as fallback.

**Rationale:** Weakening a site's explicit `script-src 'none'` policy creates security risk for the user. Sites with trusted types enforcement will reject injected scripts regardless of nonce. Skipping injection on these sites is safe because the proxy layer catches the geolocation API call.

**Alternatives considered:** Inject anyway and rely on browser error handling (degrades site security); remove CSP entirely (too aggressive).

### Decision 6: Cross-origin iframes

**Decision:** Cross-origin iframes are not covered by JS injection unless their response also passes through the proxy and gets injected independently. This is a known limitation documented in user-spec.

**Rationale:** Each cross-origin iframe has its own JS context. The proxy can inject into iframe responses if they're HTML and pass through mitmproxy, but same-site iframes served from a CDN or different origin may not get injection. The registry-level disable and proxy API intercept provide coverage for these cases.

## Data Models

### ProxyState addition

New optional field: `original_location_services: Optional[str] = None`. Values: `"Allow"` (was enabled), `"Deny"` (was already disabled), `None` (key didn't exist or non-Windows).

## Dependencies

### New packages
None.

### Using existing (from project)
- `winreg` — for HKCU Location Services registry operations (same pattern as proxy settings)
- `mitmproxy.http.Response.make()` — for fabricating geolocation API response in request() handler

## Testing Strategy

**Feature size:** M

### Unit tests
- `disable_location_services()` / `restore_location_services()` — mock winreg, verify read-before-write, verify original value preservation, verify `None` original → key deletion on restore
- `restore_location_services()` rejects invalid original values (not in `{'Allow', 'Deny', None}`) → defaults to `Deny`
- Non-Windows platform: both functions are no-ops
- `cleanup()` with new Location Services label — retry and pending file behavior
- ProxyState backward compat: old state file without `original_location_services` deserializes with default `None`
- Geolocation API intercept in `request()` — exact host/path/method match → synthetic response with randomized accuracy
- Geolocation API intercept skips non-matching URLs, non-POST methods
- Geolocation API intercept error path — mock to raise → log, pass through, no re-raise
- `navigator.permissions.query` override scoped to `name === 'geolocation'` only, returns `{state: 'granted'}`
- Geolocation-only payload on non-target domain vs full payload on target domain
- CSP skip: non-target domain with `script-src 'none'` → no injection

### Integration tests
- Proxy flow: non-target domain HTML → response contains geolocation override JS (but not timezone/language)
- Proxy flow: POST to googleapis.com/geolocation → returns fake JSON coordinates
- Proxy flow: target domain HTML → response contains full JS payload (regression)

### E2E tests
None — WiFi scanning requires real hardware. Manual verification documented.

## Agent Verification Plan

### Verification approach
Agent verifies through automated tests (unit + integration). Registry operations verified via mocked winreg. Proxy behavior verified via real mitmproxy integration tests.

### Tools required
pytest, mitmproxy (real instance for integration tests).

## Risks

| Risk | Mitigation |
|------|-----------|
| HKCU registry path doesn't exist on all Windows versions | Graceful fallback: log warning, skip registry step, rely on JS + proxy layers |
| CSP conflicts on non-target domains break pages | Minimal geolocation-only payload; skip injection if CSP modification fails; proxy intercept as fallback |
| Performance degradation from universal HTML processing | Geolocation-only payload is small; content-type and size gates already filter non-HTML; RAM watchdog monitors memory |
| Proxy intercept error crashes flow processing | Wrap in try/except, log error, pass request through unchanged |

## Acceptance Criteria

Technical criteria (supplement user-spec):

- [ ] `disable_location_services()` reads and stores original registry value before writing `Deny`
- [ ] `restore_location_services()` writes back the exact original value; when `None` → deletes the key; validates value is in `{'Allow', 'Deny', None}`
- [ ] Non-Windows platforms: Location Services functions are no-ops (consistent with existing pattern)
- [ ] New cleanup label `"Location Services restore"` added to `_VALID_CLEANUP_LABELS` and `_execute_cleanup_by_label()`
- [ ] `response()` injects geolocation-only JS on non-target domains (not full payload)
- [ ] `request()` intercepts exact `host == www.googleapis.com` + `path == /geolocation/v1/geolocate` + `POST` and returns fake JSON with randomized accuracy (40–80m)
- [ ] `navigator.permissions.query` override scoped to `name === 'geolocation'` only (other permissions unaffected)
- [ ] Non-target domains with `script-src 'none'` CSP → injection skipped, proxy intercept remains as fallback
- [ ] `request()` error handling: try/except around intercept, log on failure, pass through
- [ ] ProxyState gains `original_location_services` field with backward-compatible default
- [ ] All existing tests pass (no regressions)
- [ ] New unit + integration tests pass

## Implementation Tasks

### Wave 1 (independent)

#### Task 1: Windows Location Services registry control
- **Description:** Add `disable_location_services()` and `restore_location_services()` to system_config.py. These read/write HKCU registry to disable WiFi-based location, preserving original value for cleanup. Integrate with existing cleanup retry/pending pattern via new label.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Files to modify:** `src/system_config.py`
- **Files to read:** `src/system_config.py` (existing registry pattern, cleanup labels)

#### Task 2: Universal geolocation JS injection + proxy API intercept
- **Description:** Extend proxy_addon.py: (1) inject geolocation-only JS override on non-target domains with CSP skip guard for `script-src 'none'`, (2) intercept googleapis.com/geolocation POST in request() and return fake coordinates. Add navigator.permissions.query override (scoped to geolocation only) to inject.js.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Files to modify:** `src/proxy_addon.py`, `src/inject.js`
- **Files to read:** `src/proxy_addon.py` (response() flow, request() handler, _modify_csp()), `src/inject.js` (stealthDefine pattern), `src/presets.py` (CountryPreset lat/lon), `test/test_proxy_addon.py`

### Wave 2 (depends on Wave 1)

#### Task 3: Startup/cleanup integration
- **Description:** Wire Location Services disable/restore into main.py startup sequence and cleanup flow. Add `original_location_services` field to ProxyState. Call disable at startup, ensure restore happens through existing cleanup resilience.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Files to modify:** `src/main.py`, `src/system_config.py` (ProxyState)
- **Files to read:** `src/main.py` (startup sequence, _do_cleanup), `src/system_config.py` (ProxyState, cleanup())

### Audit Wave

#### Task 4: Code Audit
- **Description:** Full-feature code quality audit. Read all source files created/modified in this feature. Review holistically for cross-component issues: duplicate resource initialization, shared resources compliance with Architecture decisions, architectural consistency. Write audit report.
- **Skill:** code-reviewing
- **Reviewers:** none

#### Task 5: Security Audit
- **Description:** Full-feature security audit. Read all source files created/modified in this feature. Analyze for OWASP Top 10 across all components, cross-component auth/data flow. Write audit report.
- **Skill:** security-auditor
- **Reviewers:** none

#### Task 6: Test Audit
- **Description:** Full-feature test quality audit. Read all test files created in this feature. Verify coverage, meaningful assertions, test pyramid balance across all components. Write audit report.
- **Skill:** test-master
- **Reviewers:** none

### Final Wave

#### Task 7: Pre-deploy QA
- **Description:** Acceptance testing: run all tests, verify acceptance criteria from user-spec and tech-spec.
- **Skill:** pre-deploy-qa
- **Reviewers:** none
