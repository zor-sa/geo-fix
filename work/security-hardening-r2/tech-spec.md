---
feature: security-hardening-r2
status: draft
work_type: bug
size: M
created: 2026-03-28
---

# Tech Spec: security-hardening-r2 — Analyst Review Fixes

## Context

After completing security-hardening (T-1 through T-12), two analyst agents reviewed the codebase and found remaining threats. Critical fixes (R-1 incremental state, R-2 stateless cleanup, R-3 Accept-Language scope) were applied immediately. This spec covers the remaining items plus a new priority item: deleting the CA private key from disk after mitmproxy loads it.

## Scope

5 items, ordered by user impact:

| ID | Problem | Priority | Files |
|----|---------|----------|-------|
| K-1 | CA private key sits on disk for entire session | HIGH | src/main.py, src/system_config.py |
| R-4 | Partial cleanup failure is silent to user | HIGH | src/system_config.py, src/main.py |
| R-5 | VPN drop during operation goes unnoticed | HIGH | src/health_check.py, src/main.py, src/tray.py |
| R-7 | Firewall cleanup misses rules if list changed | MEDIUM | src/system_config.py |
| W-1 | Watchdog can die without main process noticing | MEDIUM | src/main.py |

## Architecture Decisions

### D-1: Delete CA key file after mitmproxy loads it (K-1)

**Research confirmed:** mitmproxy loads CA private key once at startup into `CertStore.default_privatekey` (in-memory). No re-reads from disk occur during operation. Deleting the file is safe.

**Approach:**
- After `_start_mitmproxy()` returns (proxy confirmed running, CA loaded into memory), delete `mitmproxy-ca.pem` and `mitmproxy-ca-cert.cer` (DER copy) from session tmpdir
- Keep `mitmproxy-ca-cert.pem` (public cert) — needed by `install_ca_cert()` to add to store
- After `install_ca_cert()` completes, delete `mitmproxy-ca-cert.pem` too
- At this point tmpdir contains only non-sensitive files (dhparam, etc.)
- Add `_delete_sensitive_key_files(confdir: str)` function in system_config.py

**What remains on disk during session:** Only dhparam.pem and mitmproxy config files (non-sensitive). Private key exists only in mitmproxy process memory.

**Risk:** If mitmproxy crashes and restarts within the same session — new CA cannot be generated because key file is gone. Mitigation: this is acceptable — geo-fix should perform full cleanup and exit, not try to restart mitmproxy silently.

### D-2: Cleanup failure notification (R-4)

**Approach:**
- `cleanup()` collects a list of failed operations (try/except around each step)
- Returns `list[str]` of failures (empty = success)
- `_do_cleanup()` in main.py checks the list; if non-empty, prints user-visible message:
  "Не удалось полностью очистить: [список]. Запустите `geo-fix --cleanup`."
- For tray-based shutdown: show a Windows toast notification via tray icon

### D-3: Periodic VPN monitoring (R-5)

**Approach:**
- New daemon thread `_vpn_monitor_loop()` in main.py
- Every 60 seconds: call `check_vpn_status()`
- On VPN loss: change tray icon color (red), show toast notification "VPN отключён! Реальный IP может быть виден."
- On VPN restore: change icon back, show "VPN восстановлен"
- Store last known VPN state to avoid repeated notifications
- Thread exits when stop_event is set

### D-4: Firewall cleanup by prefix (R-7)

**Approach:**
- New `_remove_firewall_rules_by_prefix()` function
- On Windows: `netsh advfirewall firewall show rule name=all` → parse output → find all rules starting with `geo-fix-webrtc` → delete each
- Replace current fixed-list iteration in `remove_firewall_rules()`
- Idempotent: no error if no rules found

### D-5: Watchdog health monitoring (W-1)

**Approach:**
- In the VPN monitor thread (D-3), also check `_watchdog_proc.poll()` every 60 seconds
- If watchdog died: respawn it with same arguments
- Log warning on respawn

## Implementation Tasks

### Task 1: Delete CA key file after mitmproxy loads it (K-1)

**Files:** src/system_config.py, src/main.py
**What:**
1. Add `delete_ca_key_files(confdir: str)` in system_config.py — deletes `mitmproxy-ca.pem`, `mitmproxy-ca-cert.cer`, `mitmproxy-ca.p12` from confdir. Keeps `mitmproxy-ca-cert.pem` (public cert needed for install).
2. Add `delete_ca_public_cert(confdir: str)` — deletes `mitmproxy-ca-cert.pem` after install_ca_cert completes.
3. In main.py after `_start_mitmproxy()`: call `delete_ca_key_files(session_tmpdir)`
4. After `install_ca_cert()`: call `delete_ca_public_cert(session_tmpdir)`
**Tests:**
- test_delete_ca_key_files_removes_private_key
- test_delete_ca_key_files_keeps_public_cert
- test_delete_ca_public_cert_removes_cert
- test_mitmproxy_works_after_key_deletion (integration, start proxy, delete key, make TLS connection)

### Task 2: Cleanup failure notification (R-4)

**Files:** src/system_config.py, src/main.py
**What:**
1. Refactor `cleanup()` to wrap each step in try/except, collect failures in a list
2. Return `list[str]` from cleanup()
3. In `_do_cleanup()`: if failures, print to stderr and log
4. In tray shutdown path: show notification via tray
**Tests:**
- test_cleanup_returns_empty_on_success
- test_cleanup_returns_failures_on_partial_error
- test_do_cleanup_prints_failures_to_user

### Task 3: Periodic VPN monitoring + watchdog health (R-5, W-1)

**Files:** src/main.py, src/health_check.py
**What:**
1. Add `_monitor_loop(stop_event, tray, watchdog_proc, session_args)` in main.py
2. Every 60 seconds: check VPN status, check watchdog alive
3. VPN lost → tray.notify("VPN отключён!"), tray.set_warning_icon()
4. Watchdog dead → respawn, log warning
5. Start thread after tray icon is created
**Tests:**
- test_vpn_monitor_detects_disconnect (mock check_vpn_status)
- test_watchdog_respawn_on_death

### Task 4: Firewall cleanup by prefix (R-7)

**Files:** src/system_config.py
**What:**
1. Add `_list_firewall_rules_by_prefix(prefix: str) -> list[str]` — parse netsh output
2. Replace `remove_firewall_rules()` body: list by prefix → delete each found rule
3. Fallback: if netsh parse fails, use old fixed-list approach
**Tests:**
- test_list_rules_parses_netsh_output (mock subprocess)
- test_remove_by_prefix_deletes_found_rules
- test_remove_by_prefix_fallback_on_parse_error

## Testing Strategy

- Unit tests with mocks for Windows APIs (cross-platform)
- Integration test for K-1: start mitmproxy with confdir, delete key, verify TLS still works
- All tests added to CI workflow

## Out of Scope

- Elevation / run as admin (in BACKLOG.md, requires user approval)
- Memory encryption of CA key (mitmproxy internal, not feasible)
- Uninstaller (separate feature)
