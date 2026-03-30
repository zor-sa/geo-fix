---
created: 2026-03-30
status: draft
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

**Decision:** Intercept `POST https://www.googleapis.com/geolocation/v1/geolocate` in `request()` handler and return a synthetic `200 OK` response with fake coordinates. Do not intercept Microsoft Location Service (`dev.virtualearth.net`) separately.

**Rationale:** Even with JS override, Chrome's internal geolocation stack (C++) may send the API request before JS runs. Intercepting at proxy level prevents MAC address exfiltration. Microsoft's service is blocked by the registry disable. Error handling: if intercept fails, log and pass request through (don't break the page).

**Alternatives considered:** Block request entirely with 403 (triggers JS error callback, detectable); intercept only in response() (MAC addresses already sent); intercept both Google and Microsoft APIs (registry covers Microsoft).

### Decision 4: ProxyState field for Location Services

**Decision:** Add `original_location_services: Optional[str] = None` field to ProxyState. Stores the registry value read before disabling (`Allow`, `Deny`, or `None` if key didn't exist).

**Rationale:** Must restore exact original state on cleanup — if user had location disabled before geo-fix, don't re-enable it. The field has a default, so existing state files remain loadable (backward compatible).

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
- `disable_location_services()` / `restore_location_services()` — mock winreg, verify correct key/value operations, verify original value preservation
- `cleanup()` with new Location Services label — retry and pending file behavior
- Geolocation API intercept in `request()` — mock flow with matching URL → verify synthetic response
- Geolocation API intercept skips non-matching URLs
- `navigator.permissions.query` override present in injected JS
- Geolocation-only payload on non-target domain vs full payload on target domain

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
- [ ] `restore_location_services()` writes back the exact original value (not unconditionally `Allow`)
- [ ] Non-Windows platforms: Location Services functions are no-ops (consistent with existing pattern)
- [ ] New cleanup label `"Location Services restore"` added to `_VALID_CLEANUP_LABELS` and `_execute_cleanup_by_label()`
- [ ] `response()` injects geolocation-only JS on non-target domains (not full payload)
- [ ] `request()` intercepts googleapis.com/geolocation POST and returns `{"location": {"lat": ..., "lng": ...}, "accuracy": 50.0}`
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

#### Task 2: Universal geolocation JS injection
- **Description:** Extend JS injection in proxy_addon.py to inject geolocation-only override on non-target domains. Target domains continue to get the full payload. Add navigator.permissions.query override to inject.js.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Files to modify:** `src/proxy_addon.py`, `src/inject.js`
- **Files to read:** `src/proxy_addon.py` (response() flow, _modify_csp()), `src/inject.js` (stealthDefine pattern), `test/test_proxy_addon.py`

#### Task 3: Proxy-level geolocation API intercept
- **Description:** Add geolocation API interception in GeoFixAddon.request() to block googleapis.com/geolocation POST requests and return fake coordinates. Defense-in-depth layer preventing MAC address exfiltration.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Files to modify:** `src/proxy_addon.py`
- **Files to read:** `src/proxy_addon.py` (request() handler, _lock pattern), `src/presets.py` (CountryPreset lat/lon)

### Wave 2 (depends on Wave 1)

#### Task 4: Startup/cleanup integration
- **Description:** Wire Location Services disable/restore into main.py startup sequence and cleanup flow. Add `original_location_services` field to ProxyState. Call disable at startup, ensure restore happens through existing cleanup resilience.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Files to modify:** `src/main.py`, `src/system_config.py` (ProxyState)
- **Files to read:** `src/main.py` (startup sequence, _do_cleanup), `src/system_config.py` (ProxyState, cleanup())

### Audit Wave

#### Task 5: Code Audit
- **Description:** Full-feature code quality audit. Read all source files created/modified in this feature. Review holistically for cross-component issues: duplicate resource initialization, shared resources compliance with Architecture decisions, architectural consistency. Write audit report.
- **Skill:** code-reviewing
- **Reviewers:** none

#### Task 6: Security Audit
- **Description:** Full-feature security audit. Read all source files created/modified in this feature. Analyze for OWASP Top 10 across all components, cross-component auth/data flow. Write audit report.
- **Skill:** security-auditor
- **Reviewers:** none

#### Task 7: Test Audit
- **Description:** Full-feature test quality audit. Read all test files created in this feature. Verify coverage, meaningful assertions, test pyramid balance across all components. Write audit report.
- **Skill:** test-master
- **Reviewers:** none

### Final Wave

#### Task 8: Pre-deploy QA
- **Description:** Acceptance testing: run all tests, verify acceptance criteria from user-spec and tech-spec.
- **Skill:** pre-deploy-qa
- **Reviewers:** none
