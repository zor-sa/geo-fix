# Code Audit Report: resource-optimization

**Date:** 2026-03-29
**Files reviewed:** src/main.py, src/proxy_addon.py, src/presets.py
**Auditor:** auditor-code

## Summary

Overall code quality is solid — the feature is well-structured with clear separation of concerns across three files. The minimal Master setup, FlowCleanup addon, and RAM monitor are implemented correctly with appropriate guard logic. I found 0 critical issues, 2 major issues, 4 minor issues, and 2 nitpicks across all three files. The most significant concerns are a race condition in master reference handoff during restart and a potential issue with `_start_mitmproxy` returning `None` master.

## Findings

### [MAJOR] Master reference may be None after startup — main.py:386

**Issue:** `_start_mitmproxy()` returns `master_ref.get("master")` after confirming the proxy is listening. However, `master_ref["master"]` is set inside `run_proxy()` on a different thread. There is a timing window where `check_proxy_running()` returns True (port is bound) but `master_ref["master"]` has not yet been assigned — the Master object is created, `master.run()` starts the event loop which binds the port, but the assignment on line 369 happens before `master.run()`. In practice this is safe because the assignment (`master_ref["master"] = master`) on line 369 happens before `master.run()` on line 374, so by the time the port is listening, the dict is already populated. **Re-assessment: this is actually safe** — the assignment precedes the blocking `master.run()` call. Downgrading.

**Revised severity:** Minor (not Major). The code is correct but the pattern is fragile — if someone reorders lines 369 and 374, the race appears.

**Risk:** Future maintenance error if `master.run()` is moved before the dict assignment.

**Recommendation:** Add a brief comment on line 369: `# Must be set before master.run() which blocks`.

### [MAJOR] `_restart_mitmproxy` catches `SystemExit` from `_start_mitmproxy` — main.py:423-424

**Issue:** `_start_mitmproxy()` calls `sys.exit(1)` on line 389 if the proxy fails to start within 15 seconds. In `_restart_mitmproxy()`, this is caught via `except SystemExit:` (line 423). While functionally correct (prevents the whole process from dying on a restart failure), `SystemExit` is not a subclass of `Exception` — it inherits from `BaseException`. The `except SystemExit` catch on line 423 works, but the subsequent `except Exception` on line 425 would NOT catch `SystemExit`. This ordering is correct. However, using `sys.exit()` as an error signaling mechanism in a function called from two contexts (startup and restart) is a code smell.

**Risk:** If `_start_mitmproxy` is called from any new context in the future, the `sys.exit(1)` will kill the process unexpectedly.

**Recommendation:** Refactor `_start_mitmproxy` to raise a custom exception (e.g., `ProxyStartError`) instead of `sys.exit(1)`. The caller in `main()` can catch it and call `sys.exit(1)` there. The caller in `_restart_mitmproxy` already handles it. This is a non-urgent improvement.

### [MINOR] Idle guard reads `addon._last_flow_time` without lock — main.py:628

**Issue:** The monitor thread reads `addon._last_flow_time` directly (line 628) while GeoFixAddon.request() writes it under `self._lock` (proxy_addon.py:149). The read in the monitor thread does not acquire the lock. Due to Python's GIL, reading a float is atomic, so there is no data corruption risk. However, the inconsistency is notable — `_last_flow_time` is written under lock but read without lock.

**Risk:** Practically zero due to GIL. The worst case is reading a slightly stale value, which would only mean restarting one cycle later (60 seconds). Acceptable for this use case.

**Recommendation:** Either (a) read under lock for consistency: `with addon._lock: last_flow = addon._last_flow_time`, or (b) remove the lock from the write in `request()` since the GIL already protects float assignment. Option (a) is cleaner.

### [MINOR] FlowCleanup sets content to `b""` not `None` — proxy_addon.py:217-218

**Issue:** FlowCleanup.response() sets `flow.request.content = b""` and `flow.response.content = b""`. The tech-spec says "content set to None/empty". Setting to `b""` allocates a new empty bytes object per flow (though Python interns `b""` so it's the same object). Setting to `None` would signal "no content" more clearly and is what mitmproxy uses internally for "not yet loaded" state.

**Risk:** Low. `b""` is functionally fine and avoids potential issues with downstream code that expects bytes, not None. Actually, `None` could cause `TypeError` in any code that does `len(flow.response.content)`. The `b""` choice is arguably safer.

**Recommendation:** Keep `b""` — this is the safer choice. The tech-spec's "None/empty" was ambiguous; `b""` is correct. No change needed.

### [MINOR] `_build_js_payload` splits accept_language 3 times — proxy_addon.py:61-64

**Issue:** The list comprehension calls `l.split(";")[0].strip()` three times per element: once to get the value, once to check non-empty, once to check != preset.language. This is in a cold path (called only on preset switch, not per-request), so performance is irrelevant.

**Risk:** None — correctness is fine and this runs rarely.

**Recommendation:** Extract to local variable for readability only if touched for other reasons.

### [MINOR] Rate limit timestamps not pruned on failed restart — main.py:649-653

**Issue:** When `_restart_mitmproxy` returns `(None, None)` (failure), `_last_restart_time` is updated (line 651) to activate cooldown, but `_restart_timestamps` is not appended. This is correct — a failed restart should not count toward the 3/hour rate limit. However, the pruning of `_restart_timestamps` (line 639) only runs before a restart attempt. If restarts never succeed, old timestamps are never pruned. This is harmless since the list is bounded by the rate limit (max 3 entries within 1 hour).

**Risk:** None in practice. The list self-prunes on every successful restart attempt.

**Recommendation:** No change needed.

### [NITPICK] Unused `os` import possibility — proxy_addon.py:9

**Issue:** `os` is imported but only used implicitly through `Path(__file__).parent`. The `os` import on line 9 is not used directly anywhere in the file.

**Risk:** None.

**Recommendation:** Remove `import os` from proxy_addon.py if not used. (Verify with grep first — may be used by test patches.)

### [NITPICK] `_TARGET_DOMAINS_TUPLE` naming convention — presets.py:74

**Issue:** Module-level constants `_TARGET_DOMAINS_TUPLE` and `_TARGET_BARE_DOMAINS` use a leading underscore (private) while `TARGET_DOMAINS` does not. The underscore is appropriate since these are derived implementation details, not part of the public API. However, `TARGET_DOMAINS` itself is only used internally and could also be private.

**Risk:** None.

**Recommendation:** No change needed for this feature.

## Cross-Component Assessment

### Addon Chain Ordering

**Correct.** In `_start_mitmproxy()` (main.py:365-368), addons are added in a single `master.addons.add()` call: `Core(), Proxyserver(), NextLayer(), TlsConfig(), KeepServing(), ErrorCheck(), addon, FlowCleanup()`. FlowCleanup is last, after GeoFixAddon (`addon`). This ensures GeoFixAddon.response() processes and injects JS before FlowCleanup.response() clears the content. The ordering is preserved across restarts because `_restart_mitmproxy` calls `_start_mitmproxy` which always adds addons in this order.

### GeoFixAddon Instance Reuse Across Restarts

**Correct.** The same `addon` instance (created once in `main()` at line 524) is passed to `_restart_mitmproxy` (line 641) and then to `_start_mitmproxy` (line 422). GeoFixAddon stores only: `_lock` (threading.Lock), `_preset` (CountryPreset — frozen dataclass), `_js_payload` (string), `_last_flow_time` (float). None of these hold references to the old Master or old flow objects. The Lock object is safe to reuse across threads. No GC prevention risk.

### Proxy Restart Sequence

**Correct with one concern.** The sequence in `_restart_mitmproxy` (main.py:392-453):
1. Shutdown old master (line 405) — clears event loop, frees flows
2. Uninstall old CA cert from trust store (line 414)
3. Start new master via `_start_mitmproxy` (line 422) — generates new CA in confdir
4. Install new CA cert (line 431), get new thumbprint
5. Delete CA key files (line 445-446)
6. Update state with new thumbprint and save (line 449-450)

**Concern:** Between step 2 (old CA uninstalled) and step 4 (new CA installed), if the user is actively browsing in another tab, HTTPS will fail with certificate errors. The idle guard (10s no traffic) mitigates this, but a browser could start a background request (service worker, keep-alive) during this window. This is a known and accepted trade-off per the tech-spec.

**Concern:** Step 3 calls `_start_mitmproxy` which can `sys.exit(1)`. The `except SystemExit` catch handles this, but see finding above about refactoring to exceptions.

### Idle Guard Thread Safety

**Acceptable.** `_last_flow_time` is a Python float written under lock in `GeoFixAddon.request()` (proxy_addon.py:149) and read without lock in `_monitor_loop` (main.py:628). Python's GIL guarantees atomic float reads. The worst case is reading a stale value by one event-loop tick, resulting in a slightly incorrect idle duration. Given the 10-second idle threshold and 60-second check interval, a stale read of a few milliseconds is irrelevant.

### Rate Limiting State Persistence

**Correct.** `_last_restart_time` and `_restart_timestamps` are module-level globals in main.py (lines 71-72). They survive proxy thread restarts because they are not part of the Master or any addon — they live in the main module's namespace. The `_monitor_loop` closure captures these via `global` declaration (line 594). The monitor thread itself is never restarted.

### Monitor Thread and stop_event

**Correct.** The monitor thread's `stop_event.wait(timeout=60)` (line 597) blocks for up to 60 seconds. When a restart completes, `proxy_ref["master"]` and `proxy_ref["thread"]` are updated (lines 644-645). On the next loop iteration, the RAM check reads `proxy_ref["master"]` (line 641 in `_restart_mitmproxy` call). The mutable dict `proxy_ref` is updated atomically from the monitor thread itself, so there is no cross-thread race on `proxy_ref`.

### Task 4 CPU Optimizations

**Correct.** `_find_inject_position` (proxy_addon.py:29-56) uses `re.search(r"<head[\s>]", html_text, re.IGNORECASE)` which correctly matches `<head>`, `<HEAD>`, `<Head `, etc. without creating a lowercased copy. `is_target_domain` (presets.py:78-81) uses `host.endswith(_TARGET_DOMAINS_TUPLE)` (tuple of strings) which is equivalent to looping with `or`. The bare domain check via `frozenset` lookup is preserved on line 81. Both optimizations are behaviorally identical to the originals.

## Verdict

**PASS WITH NOTES** — The codebase is well-implemented with no critical issues. The two major items (downgraded to minor upon analysis) are architectural notes for future maintainability rather than current bugs. All cross-component interactions are correct: addon ordering, GeoFixAddon reuse, restart sequence, idle guard, and rate limiting. The code is ready for security audit and pre-deploy QA.
