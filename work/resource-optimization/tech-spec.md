---
created: 2026-03-29
status: draft
branch: feature/resource-optimization
size: L
---

# Tech Spec: Resource Optimization

## Solution

Reduce geo-fix RAM from ~200-300MB (growing unbounded) to a stable ~100-150MB, and average CPU from 5-15% to under 5% during active browsing. Three main attack vectors:

1. **Minimize mitmproxy addon chain** — replace DumpMaster (loads ~31 default addons + Dumper) with base `Master` class plus only the 6 addons required for HTTPS proxying. This eliminates ~26 unused addons from the hook processing chain, each of which registers hooks checked on every flow event.

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
         → master.addons.add(Core(), Proxyserver(), NextLayer(), TlsConfig())
         → master.addons.add(KeepServing(), ErrorCheck())
         → master.addons.add(GeoFixAddon)      # existing instance
         → master.addons.add(FlowCleanup())    # LAST — runs after GeoFixAddon

Request lifecycle (unchanged):
  Browser → proxy → GeoFixAddon.request() → upstream
  upstream → GeoFixAddon.response() → FlowCleanup.response() → Browser
  FlowCleanup clears flow.request.content and flow.response.content to free memory

WebSocket lifecycle:
  FlowCleanup.websocket_message() → trim message history to last 1 message
  FlowCleanup.websocket_end() → remove flow

RAM monitoring (in existing monitor thread):
  Every 60 seconds: check process Private Working Set
  If > 300MB AND cooldown elapsed AND idle guard passed: restart mitmproxy thread
  Idle guard: GeoFixAddon tracks last flow timestamp (time.monotonic()).
    Restart only if no flow activity for >10 seconds.
    If traffic is active — skip restart, re-check on next 60-sec cycle.
    This ensures: (a) no in-flight requests during restart,
    (b) user is not actively browsing when connectivity gap occurs.
  Restart sequence:
    1. Shutdown old master (master.shutdown())
    2. Create new Master with same opts (confdir=session_tmpdir, same port)
       → TlsConfig generates new CA cert/key in session_tmpdir (old ones deleted)
    3. Uninstall old CA from cert store (certutil -delstore old thumbprint)
    4. Install new CA cert (certutil -addstore), get new thumbprint
    5. Delete new CA key files from disk (preserve security hardening)
    6. Update ProxyState with new ca_thumbprint, save state
    7. Re-add same GeoFixAddon instance (preserves preset state) + new FlowCleanup
    8. Start new master in new daemon thread
    Note: During restart (~7 sec total with CA swap), browser gets
    ERR_PROXY_CONNECTION_REFUSED — no requests leave the machine.
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

**Rationale:** Full restart would lose tray icon, watchdog connection, system proxy settings. Thread restart only interrupts proxy traffic for ~5 seconds. During the restart window, the system proxy still points at `127.0.0.1:PORT` — since the proxy is not listening, browsers receive `ERR_PROXY_CONNECTION_REFUSED` and **do not fall back to direct connections**. This means no requests leave the machine at all during restart — there is no location leak, just a brief connectivity gap. This is safe behavior: Chrome, Edge, and Firefox all refuse to bypass a configured system proxy when the proxy is unreachable.

**Idle guard:** Restart only triggers when no flow has been processed for >10 seconds. GeoFixAddon tracks `_last_flow_time` via `time.monotonic()` in `request()` hook. The RAM monitor checks this before restarting. If traffic is active (flow within last 10 sec) — restart is deferred to the next 60-sec check cycle. This eliminates the "request on hold" problem: if there's no traffic, there are no pending requests to disrupt. Combined with the ~7 sec restart window during an idle period, the chance of a user-visible error is near zero.

**Safety verification required:** Integration test must confirm that with system proxy set to a non-listening port, the browser does NOT send direct requests. If any browser is found to fall back to direct — this mechanism must be removed entirely.

**CA certificate on restart:** The restart sequence must re-generate and re-install the CA certificate because the original CA key files were deleted from disk (security hardening). Sequence: shutdown old master → new Master generates new CA in confdir → `certutil -delstore` old CA → `certutil -addstore` new CA → delete new CA key files → update state with new thumbprint. This adds ~2 seconds to restart but preserves the CA key deletion security property.

**Alternatives considered:** Full process restart via watchdog. Rejected as too disruptive. Graceful restart (start new before stopping old) on same port — not possible with TCP port binding. Keep CA key files on disk permanently — rejected, weakens security hardening.

### Decision 5: Keep watchdog as subprocess

**Decision:** Do not change watchdog subprocess architecture.

**Rationale:** User prioritized reliability over 15MB savings. Watchdog must survive main process death — a thread cannot do this.

**Alternatives considered:** Thread (saves 15-20MB, loses crash detection). Batch script (less reliable). Both rejected.

### Decision 6: No psutil dependency for RAM monitoring

**Decision:** Use Windows `ctypes` API (`GetProcessMemoryInfo`) directly instead of adding `psutil`.

**Rationale:** Avoids new dependency. Uses `PROCESS_MEMORY_COUNTERS_EX.PrivateUsage` for accurate Private Working Set measurement (not `WorkingSetSize` which includes shared pages). Stable since Windows XP. Fallback to `VmRSS` from `/proc/self/status` on Linux for testing.

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
- RAM monitor: test idle guard (restart deferred when last flow < 10 sec ago)
- RAM monitor: test idle guard (restart proceeds when last flow > 10 sec ago)
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
| Proxy restart causes brief connectivity gap (~7 sec) | System proxy set → browser gets ERR_PROXY_CONNECTION_REFUSED → no direct requests, no location leak. Integration test must verify no browser fallback to direct. If fallback found — remove auto-restart entirely. |
| mitmproxy internal API changes break minimal Master | Pin mitmproxy version. Add startup assertion that proxy is listening. |
| Windows memory API differences across versions | `PROCESS_MEMORY_COUNTERS.WorkingSetSize` is stable since XP. Fallback for non-Windows. |
| PyInstaller hidden imports may need updating | Switching from `mitmproxy.tools.dump` to explicit addon imports changes the import graph. Add new `--hidden-import` flags if needed. Test build in CI. |
| CA key files deleted before proxy restart | Restart sequence re-generates CA, re-installs to cert store, deletes key files again. Adds ~2 sec to restart. Integration test verifies HTTPS works after restart with new CA. |

## Acceptance Criteria

Technical acceptance criteria (supplement user-spec criteria):

- [ ] mitmproxy starts with base Master class, not DumpMaster — no unused addons loaded
- [ ] Only essential addons loaded: Core, Proxyserver, NextLayer, TlsConfig, KeepServing, ErrorCheck, GeoFixAddon, FlowCleanup
- [ ] FlowCleanup added after GeoFixAddon — verified by unit test checking addon order
- [ ] FlowCleanup clears flow content after processing — flow.request.content and flow.response.content set to None/empty
- [ ] WebSocket message history trimmed to <=1 message per connection
- [ ] RAM monitoring triggers at 300MB Private Working Set with 10-min cooldown, max 3/hour
- [ ] Proxy restart only occurs after 10+ seconds of no flow activity (idle guard)
- [ ] Proxy restart reuses the same GeoFixAddon instance (preserves current preset and JS payload cache)
- [ ] CA certificate re-generated, re-installed, and key deleted on proxy restart — HTTPS works after restart
- [ ] During proxy restart, no HTTP requests leave the machine (browser gets connection refused, not direct fallback)
- [ ] No regressions in existing test suite (unit + integration + E2E)
- [ ] No Dumper addon loaded (no stdout output per flow)

## Implementation Tasks

### Wave 1 (independent)

#### Task 1: Replace DumpMaster with minimal Master

- **Description:** Replace mitmproxy `DumpMaster` initialization in `_start_mitmproxy()` with base `Master` class plus only essential addons (Core, Proxyserver, NextLayer, TlsConfig, KeepServing, ErrorCheck). This removes ~26 unused addons and their per-flow hook overhead.
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
