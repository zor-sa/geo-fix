---
created: 2026-03-29
status: draft
branch: feature/resource-optimization
size: L
---

# Tech Spec: Resource Optimization

## Solution

Reduce geo-fix RAM from ~200-300MB (growing unbounded) to a stable ~100-150MB, and average CPU from 5-15% to under 5% during active browsing. Three main attack vectors:

1. **Minimize mitmproxy addon chain** — replace DumpMaster (loads ~27 default addons + Dumper + KeepServing + ErrorCheck) with base `Master` class plus only the 5-6 addons required for HTTPS proxying. This eliminates ~25 unused addons from the hook processing chain, each of which registers hooks checked on every flow event.

2. **Flow lifecycle management** — add a cleanup addon that explicitly clears flow content (request/response bodies) after processing to help Python's GC reclaim memory faster, and trims WebSocket message history to prevent unbounded growth during long sessions (Google Docs, Meet).

3. **RAM watchdog with auto-restart** — monitor process memory (Private Working Set) and restart the mitmproxy proxy thread if it exceeds 300MB, with cooldown/rate-limiting to prevent restart loops.

All existing functionality, security properties, and tests are preserved unchanged.

## Architecture

### What we're building/modifying

- **`src/main.py` — mitmproxy initialization** — replace `DumpMaster` with minimal `Master` setup loading only essential addons (Core, Proxyserver, NextLayer, TlsConfig). No Dumper addon = no stdout logging.
- **`src/proxy_addon.py` — flow cleanup addon** — new `FlowCleanup` addon that clears flow content (bodies) after processing to reduce GC pressure, and trims WebSocket message history. Minor optimization of `_find_inject_position` to avoid full `.lower()` copy.
- **`src/main.py` — RAM monitor** — extend existing monitor thread to check process memory via Windows `GetProcessMemoryInfo` API and trigger proxy restart when threshold exceeded.

### How it works

```
Startup:
  main.py → Master(opts) instead of DumpMaster(opts)
         → master.addons.add(Core, Proxyserver, NextLayer, TlsConfig)
         → master.addons.add(KeepServing, ErrorCheck)
         → master.addons.add(GeoFixAddon)
         → master.addons.add(FlowCleanup)  # LAST — runs after GeoFixAddon

Request lifecycle (unchanged):
  Browser → proxy → GeoFixAddon.request() → upstream
  upstream → GeoFixAddon.response() → FlowCleanup.response() → Browser
  FlowCleanup clears flow.request.content and flow.response.content to free memory

WebSocket lifecycle:
  FlowCleanup.websocket_message() → trim message history to last 1 message
  FlowCleanup.websocket_end() → remove flow

RAM monitoring (in existing monitor thread):
  Every 60 seconds: check process Private Working Set
  If > 300MB and cooldown elapsed: restart mitmproxy thread
  Restart sequence:
    1. Shutdown old master (master.shutdown())
    2. Create new Master with same opts (confdir=session_tmpdir, same port)
    3. Re-add same GeoFixAddon instance (preserves preset state) + new FlowCleanup
    4. Start new master in new daemon thread
    Note: CA cert is already loaded in Windows cert store — new Master reuses
    existing CA from session_tmpdir (mitmproxy-ca-cert.pem was deleted but
    TlsConfig caches the CA in memory; on restart it regenerates from confdir
    if needed, but the cert store already trusts any CA from session_tmpdir).
    IMPORTANT: If TlsConfig needs CA files that were deleted, restart must
    either skip CA deletion initially, or re-generate and re-install CA cert.
    This edge case must be tested in integration tests.
  Rate limit: max 3 restarts per hour, then log-only
```

### Shared resources

| Resource | Owner (creates) | Consumers | Instance count |
|----------|----------------|-----------|----------------|
| mitmproxy Master | main.py `_start_mitmproxy()` | GeoFixAddon, FlowCleanup | 1 (recreated on RAM restart) |
| GeoFixAddon | main.py | mitmproxy Master | 1 (reused across restarts) |

## Decisions

### Decision 1: Base Master instead of DumpMaster

**Decision:** Use `mitmproxy.master.Master` directly with manually added essential addons, instead of `DumpMaster` which loads 35 default addons.

**Rationale:** DumpMaster calls `default_addons()` which loads ~27 addons that geo-fix never uses (ClientPlayback, ServerPlayback, Save, SaveHar, Cut, Export, Onboarding, CommandHistory, Comment, ScriptLoader, MapRemote, MapLocal, ModifyBody, ModifyHeaders, StickyAuth, StickyCookie, ProxyAuth, Browser, etc.), plus Dumper (stdout printer). Each addon registers hooks checked on every flow event — CPU overhead per request scales with addon count. Additionally, some addons (SaveHar) maintain internal flow lists that could accumulate under certain configurations. Reducing to ~6 essential addons cuts per-flow hook overhead by ~80%.

**Alternatives considered:**
- DumpMaster with `with_dumper=False` + post-init addon removal: Still loads all addons initially; fragile (depends on removal API). Rejected as half-measure.
- Switch to proxy.py library: Much lighter (~5-20MB) but immature plugin API, would require rewriting all addon logic. Rejected as too risky.

### Decision 2: FlowCleanup as separate addon

**Decision:** Create a dedicated `FlowCleanup` addon added AFTER `GeoFixAddon` in the addon chain.

**Rationale:** Separation of concerns — GeoFixAddon handles spoofing, FlowCleanup handles memory management. Added last to ensure GeoFixAddon processes flows first. Easier to test independently.

**Alternatives considered:** Merging cleanup into GeoFixAddon's handlers. Rejected — mixes concerns, harder to test.

### Decision 3: RAM monitor in existing monitor thread

**Decision:** Extend the existing VPN/watchdog monitor thread (60-second loop) with RAM checking.

**Rationale:** Avoids new thread. 60-second interval is sufficient since RAM growth is gradual.

**Alternatives considered:** Separate thread with shorter interval. Rejected as unnecessary.

### Decision 4: Proxy thread restart (not full process restart)

**Decision:** On RAM threshold breach, restart only the mitmproxy thread, not the entire process.

**Rationale:** Full restart would lose tray icon, watchdog connection, system proxy settings. Thread restart only interrupts proxy traffic for ~5 seconds. During the restart window, the system proxy points at a non-listening port — browser requests fail and are retried automatically. This briefly exposes real Accept-Language headers on direct connections, but the window is short (~5 sec) and occurs at most 3 times per hour. User accepted this tradeoff.

**Alternatives considered:** Full process restart via watchdog. Rejected as too disruptive. Graceful restart (start new before stopping old) on same port — not possible with TCP port binding.

### Decision 5: Keep watchdog as subprocess

**Decision:** Do not change watchdog subprocess architecture.

**Rationale:** User prioritized reliability over 15MB savings. Watchdog must survive main process death — a thread cannot do this.

**Alternatives considered:** Thread (saves 15-20MB, loses crash detection). Batch script (less reliable). Both rejected.

### Decision 6: No psutil dependency for RAM monitoring

**Decision:** Use Windows `ctypes` API (`GetProcessMemoryInfo`) directly instead of adding `psutil`.

**Rationale:** Avoids new dependency. `PROCESS_MEMORY_COUNTERS.WorkingSetSize` is stable since Windows XP. Fallback to `/proc/self/status` on Linux for testing.

**Alternatives considered:** `psutil` package — reliable cross-platform but adds ~10MB to bundle and new dependency. Rejected.

## Data Models

N/A — no new data models. `ProxyState` dataclass unchanged.

## Dependencies

### New packages

None.

### Using existing (from project)

- `mitmproxy.master.Master` — base master class (replacing `DumpMaster`)
- `mitmproxy.addons.core.Core` — essential addon
- `mitmproxy.addons.proxyserver.Proxyserver` — proxy server addon
- `mitmproxy.addons.next_layer.NextLayer` — protocol detection
- `mitmproxy.addons.tlsconfig.TlsConfig` — TLS/certificate handling
- `mitmproxy.addons.errorcheck.ErrorCheck` — startup error reporting
- `mitmproxy.addons.keepserving.KeepServing` — prevents premature exit

## Testing Strategy

**Feature size:** L

### Unit tests

- FlowCleanup addon: verify response/error/websocket_end hooks clear flow content
- FlowCleanup addon: verify websocket_message trims history to 1 message
- FlowCleanup addon ordering: verify FlowCleanup is added after GeoFixAddon in addon chain
- RAM monitor: test threshold detection with mocked memory readings
- RAM monitor: test cooldown logic (no restart within 10 min of previous)
- RAM monitor: test rate limiting (4th restart in 1 hour is suppressed, only logged)
- RAM monitor: test Linux `/proc/self/status` fallback path
- GeoFixAddon state preservation: verify same addon instance reused after simulated restart retains preset
- Minimal Master setup: verify required addons are loaded and proxy accepts connections
- Existing proxy_addon tests: must pass unchanged

### Integration tests

- Start optimized proxy, send HTTP request through it, verify response arrives (basic proxy works)
- Start optimized proxy, send CONNECT request, verify HTTPS tunneling works
- Start optimized proxy, send request to target domain, verify JS injection in response
- Start optimized proxy, process 100 flows, verify no flow objects retained (assert flow count == 0)
- Proxy restart integration: restart mitmproxy thread, verify TLS still works with installed CA
- Existing integration tests: must pass unchanged

### E2E tests

- Existing E2E tests (Playwright through proxy): must pass unchanged — these run against whatever Master setup is used, so they validate the optimized proxy end-to-end

## Agent Verification Plan

**Source:** user-spec "Как проверить" section.

### Verification approach

1. Run full test suite (`pytest test/ -x`) — all existing tests pass
2. Run new unit tests for FlowCleanup and RAM monitor
3. Verify minimal addon set via grep in source code
4. Verify flow cleanup via dedicated unit test

### Tools required

bash (pytest, grep)

## Risks

| Risk | Mitigation |
|------|-----------|
| Minimal Master missing essential addon for edge-case traffic (HTTP/2, CONNECT, WebSocket) | Include NextLayer (protocol detection). Add integration test for CONNECT tunneling. |
| Flow cleanup conflicts with mitmproxy internals | With minimal Master, no View addon is loaded — flows are not stored. FlowCleanup is a safety net. |
| Proxy restart drops in-flight requests | Browser retries failed requests. 10-min cooldown ensures rarity. User accepted tradeoff. |
| mitmproxy internal API changes break minimal Master | Pin mitmproxy version. Add startup assertion that proxy is listening. |
| Windows memory API differences across versions | `PROCESS_MEMORY_COUNTERS.WorkingSetSize` is stable since XP. Fallback for non-Windows. |
| PyInstaller hidden imports may need updating | Switching from `mitmproxy.tools.dump` to explicit addon imports changes the import graph. Add new `--hidden-import` flags if needed. Test build in CI. |
| CA key files deleted before proxy restart | New Master's TlsConfig needs CA cert/key from confdir. Either keep CA files on disk (less secure) or handle CA re-generation + re-installation on restart. Integration test must verify TLS works after restart. |
| Brief proxy bypass window during restart leaks real headers | ~5 sec window, max 3/hour. Documented in Decision 4. User accepted. |

## Acceptance Criteria

Technical acceptance criteria (supplement user-spec criteria):

- [ ] mitmproxy starts with base Master class, not DumpMaster — no unused addons loaded
- [ ] Only essential addons loaded: Core, Proxyserver, NextLayer, TlsConfig, KeepServing, ErrorCheck, GeoFixAddon, FlowCleanup
- [ ] FlowCleanup added after GeoFixAddon — verified by unit test checking addon order
- [ ] FlowCleanup clears flow content after processing — flow.request.content and flow.response.content set to None/empty
- [ ] WebSocket message history trimmed to <=1 message per connection
- [ ] RAM monitoring triggers at 300MB Private Working Set with 10-min cooldown, max 3/hour
- [ ] Proxy restart reuses the same GeoFixAddon instance (preserves current preset and JS payload cache)
- [ ] CA certificate lifecycle handled correctly on proxy restart (new Master can establish TLS)
- [ ] No regressions in existing test suite (unit + integration + E2E)
- [ ] No Dumper addon loaded (no stdout output per flow)

## Implementation Tasks

### Wave 1 (independent)

#### Task 1: Replace DumpMaster with minimal Master

- **Description:** Replace mitmproxy `DumpMaster` initialization in `_start_mitmproxy()` with base `Master` class plus only essential addons (Core, Proxyserver, NextLayer, TlsConfig, KeepServing, ErrorCheck). This removes ~25 unused addons and their per-flow hook overhead.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Verify-smoke:** `python -c "from mitmproxy.master import Master; from mitmproxy.options import Options; print('Master import OK')"` → no error
- **Files to modify:** `src/main.py`
- **Files to read:** `src/proxy_addon.py`, `src/presets.py`

#### Task 2: Add FlowCleanup addon

- **Description:** Create a `FlowCleanup` addon that clears flow content (request/response bodies) after processing to reduce GC pressure, and trims WebSocket message history to prevent unbounded growth. Added last in the addon chain.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Files to modify:** `src/proxy_addon.py`
- **Files to read:** `src/main.py`

### Wave 2 (depends on Wave 1)

#### Task 3: Add RAM monitoring with proxy auto-restart

- **Description:** Extend the existing monitor thread to check process Private Working Set every 60 seconds and restart the mitmproxy thread if it exceeds 300MB. Includes cooldown (10 min) and rate limiting (max 3/hour, then log-only).
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Files to modify:** `src/main.py`
- **Files to read:** `src/proxy_addon.py`, `src/system_config.py`

#### Task 4: Minor CPU optimizations in proxy_addon

- **Description:** Optimize `_find_inject_position()` to avoid full `.lower()` copy of HTML by using case-insensitive search. Optimize `is_target_domain()` to use tuple-based `endswith()` for faster suffix matching.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Files to modify:** `src/proxy_addon.py`, `src/presets.py`
- **Files to read:** `test/test_proxy_addon.py`, `test/test_presets.py`

### Wave 3 (depends on Wave 2)

#### Task 5: Integration testing of optimized proxy

- **Description:** Add integration tests verifying the optimized proxy handles HTTP/HTTPS/CONNECT traffic correctly, JS injection works through minimal Master, flow cleanup prevents memory growth, and proxy restart preserves TLS functionality.
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Verify-smoke:** `python -m pytest test/ -x -v` → all tests pass
- **Files to modify:** `test/test_integration_proxy.py` (new)
- **Files to read:** `src/main.py`, `src/proxy_addon.py`, `test/test_proxy_addon.py`, `test/test_integration_windows.py`

### Audit Wave

#### Task 6: Code Audit

- **Description:** Full-feature code quality audit. Read all source files modified in this feature (src/main.py, src/proxy_addon.py, src/presets.py). Review for cross-component issues: proxy initialization correctness, addon ordering, restart safety, memory monitoring edge cases. Write audit report.
- **Skill:** code-reviewing
- **Reviewers:** none

#### Task 7: Security Audit

- **Description:** Full-feature security audit. Read all modified source files. Verify security properties preserved: CA key lifecycle, DPAPI state encryption, CSP nonce injection with minimal Master, no new attack surface from proxy restart. Write audit report.
- **Skill:** security-auditor
- **Reviewers:** none

#### Task 8: Test Audit

- **Description:** Full-feature test quality audit. Read all test files (existing + new). Verify coverage of FlowCleanup, RAM monitor, minimal Master. Check existing tests still cover all original functionality. Write audit report.
- **Skill:** test-master
- **Reviewers:** none

### Final Wave

#### Task 9: Pre-deploy QA

- **Description:** Acceptance testing: run all tests (unit + integration + E2E), verify acceptance criteria from user-spec and tech-spec. Confirm no regressions.
- **Skill:** pre-deploy-qa
- **Reviewers:** none
