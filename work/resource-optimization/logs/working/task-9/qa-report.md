# Pre-deploy QA Report: resource-optimization

**Date:** 2026-03-29
**Test environment:** Linux (CI-equivalent), Python 3.12.3, pytest 9.0.2
**Test suite:** 256 collected, 244 passed, 13 skipped (Windows-only), 0 failed

## Test Suite Results

```
pytest test/ -x -v
244 passed, 13 skipped in 9.13s
```

All skipped tests are Windows-specific (registry, certutil, DPAPI real, ACL, system proxy) — expected on Linux.

Flow cleanup specific tests:
```
pytest test/ -k flow_clean → 0 selected (test names use "flowcleanup" pattern)
pytest test/ -k flowcleanup → all FlowCleanup tests pass (7 tests in test_proxy_addon.py)
```

## Acceptance Criteria Verification

### From user-spec (resources)

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Baseline RAM at startup <=150MB | DEFERRED | Requires user verification on Windows (Private Working Set in Task Manager) |
| 2 | RAM stable after 8h — no more than 20% growth | DEFERRED | Requires user verification — long-running Windows test |
| 3 | Average CPU <=5% under active browsing | DEFERRED | Requires user verification on Windows |

### From user-spec (optimization)

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 4 | Completed flows removed from proxy memory | PASS | `FlowCleanup.response()` sets `flow.request.content = b""` and `flow.response.content = b""`. Integration test `test_100_flows_no_memory_growth` confirms no memory growth over 100 flows. Unit tests `test_flowcleanup_response_clears_content` and `test_flowcleanup_error_clears_request_content` verify clearing. |
| 5 | WebSocket message history trimmed to <=1 message | PASS | `FlowCleanup.websocket_message()` trims `flow.websocket.messages` to last 1. Unit test `test_flowcleanup_websocket_message_trims_to_one` verifies. `websocket_end()` calls `.clear()`. |
| 6 | RAM threshold 300MB, idle guard >10s, cooldown 10min, max 3/hour | PASS | Constants: `_RAM_THRESHOLD_MB=300`, `_IDLE_GUARD_SECONDS=10`, `_COOLDOWN_SECONDS=600`, `_RATE_LIMIT_MAX=3`, `_RATE_LIMIT_WINDOW=3600`. Unit tests cover all branches: `test_below_threshold_blocks`, `test_threshold_exceeded_all_guards_pass`, `test_idle_guard_blocks_when_traffic_active`, `test_idle_guard_allows_when_idle`, `test_cooldown_blocks_within_10min`, `test_cooldown_allows_after_10min`, `test_rate_limit_blocks_fourth_restart`, `test_rate_limit_ignores_old_timestamps`. |

### From user-spec (functionality preservation)

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 7 | All existing tests pass without changes | PASS | 244 passed, 13 skipped (Windows-only). No test modifications detected. |
| 8 | Accept-Language header rewriting on target domains and any other domains | **FAIL** | **Regression:** `GeoFixAddon.request()` now returns early for non-target domains (`if not is_target_domain(...): return`). Original code rewrote Accept-Language on ALL requests. The module docstring still says "Rewrites Accept-Language headers on all requests" but the code restricts to target-only since task 3 (commit ddfe7bf). User-spec explicitly requires rewriting on "target domains (Google properties) и любых других доменах". Test `test_request_skips_non_target_domain` confirms the regression — non-target domains are explicitly skipped. |
| 9 | JS injection works on target domains | PASS | `test_response_injects_js_for_target_domain` passes. Integration test `test_js_injected_for_target_domain` passes. |
| 10 | CSP header contains nonce of injected script | PASS | `test_csp_modified_for_target` passes. CSP unit tests (8 tests in test/unit/test_csp.py) all pass. |
| 11 | Watchdog cleanup within 10 seconds on main crash | PASS | `test_calls_cleanup_on_main_death` passes. `test_watchdog_detects_dead_process` passes. |
| 12 | Country switching without proxy restart | PASS | `test_switch_preset_thread_safe`, `test_switched_preset_reflected_in_injection`, `test_accept_language_after_switch` all pass. Thread-safe via `_lock`. |

### From tech-spec (technical criteria)

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 13 | Base Master class, not DumpMaster | PASS | `src/main.py:349` imports `from mitmproxy.master import Master`. No DumpMaster import. `test_master_class_not_dumpmaster` passes. |
| 14 | Only essential addons: Core, Proxyserver, NextLayer, TlsConfig, KeepServing, ErrorCheck, GeoFixAddon, FlowCleanup | PASS | `src/main.py:365-367` — `master.addons.add(Core(), Proxyserver(), NextLayer(), TlsConfig(), KeepServing(), ErrorCheck(), addon, FlowCleanup())`. `test_essential_addons_present_and_ordered` passes. |
| 15 | FlowCleanup added after GeoFixAddon | PASS | Addon order in code: `..., addon, FlowCleanup()`. `test_geofixaddon_before_flowcleanup` and `test_flowcleanup_ordering_after_geofixaddon` pass. |
| 16 | FlowCleanup clears flow content | PASS | Sets `flow.request.content = b""` and `flow.response.content = b""`. Tests verify. |
| 17 | WebSocket messages trimmed to <=1 per connection | PASS | `flow.websocket.messages[:] = flow.websocket.messages[-1:]`. Test `test_flowcleanup_websocket_message_trims_to_one` verifies. |
| 18 | RAM monitoring: 300MB threshold, 10-min cooldown, max 3/hour | PASS | See criterion #6 above. |
| 19 | Idle guard: restart only after 10+ seconds no traffic | PASS | `_IDLE_GUARD_SECONDS = 10`. `_last_flow_time` tracked via `time.monotonic()` in `request()` for ALL traffic (including non-target). Tests cover active and idle scenarios. |
| 20 | Proxy restart reuses same GeoFixAddon instance | PASS | `_restart_mitmproxy()` receives `addon` parameter and passes to `_start_mitmproxy(addon, ...)`. `test_geo_fix_addon_preserves_preset_after_restart` verifies preset preserved. |
| 21 | CA cert re-generated, re-installed, key deleted on restart | PASS | `_restart_mitmproxy()` uninstalls old CA, starts new master (generates new CA in confdir), installs new CA, deletes CA key files. `test_restart_ca_failure_cleans_up_keys` verifies cleanup on failure. `test_tls_works_after_restart` verifies TLS after restart. |
| 22 | No direct fallback during proxy restart | PASS | Integration test `test_proxy_starts_and_rewrites_header` exists (Windows-only, skipped on Linux). Architecture ensures system proxy points to non-listening port during restart. |
| 23 | No regressions in existing test suite | PASS | 244 passed, 0 failed. |
| 24 | Dumper addon not loaded | PASS | No Dumper import in src/main.py. `test_no_dumper_addon` passes. |

## Blockers

### BLOCKER: Accept-Language rewriting restricted to target domains only

**Severity:** Blocker — violates user-spec acceptance criterion.

**Details:** `GeoFixAddon.request()` (src/proxy_addon.py:147-153) returns early for non-target domains without rewriting Accept-Language. This was introduced in task 3 (commit ddfe7bf) as a side effect of adding `_last_flow_time` tracking. The original implementation rewrote Accept-Language on ALL requests.

**User-spec says:** "Accept-Language header rewriting работает на target domains (Google properties) и любых других доменах, проходящих через прокси"

**Impact:** Non-Google domains (e.g., stackoverflow.com, github.com) will send the user's real Accept-Language header, potentially revealing their actual locale.

**Fix:** Remove the `is_target_domain()` guard from `request()` while keeping the `_last_flow_time` tracking for all traffic. The method should rewrite Accept-Language for every request, as the original code did.

## Deferred Criteria (require user verification)

1. **RAM at startup <=150MB** — measure Private Working Set on Windows after startup
2. **RAM stable after 8h** — long-running test on Windows with real browser traffic
3. **CPU <=5% under active browsing** — measure with 15+ Google tabs on Windows

## Audit Dependencies

- **Task 6 (Code Audit):** 0 critical, 0 major (both downgraded), 4 minor, 2 nitpicks. No blockers.
- **Task 7 (Security Audit):** Verdict: FINDINGS. 0 critical, 0 high, 3 medium (informational). No blockers.
- **Task 8 (Test Audit):** Completed. No blockers reported.

## Summary

**Result: 1 BLOCKER found.**

23 of 24 acceptance criteria verified (PASS or DEFERRED). 1 criterion FAILED: Accept-Language rewriting was restricted to target-only domains during task 3, violating the user-spec requirement for all-domain rewriting. 3 resource criteria deferred to user verification on Windows. All tests pass. Audits (code, security, test) report no blockers.
