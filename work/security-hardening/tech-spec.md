---
feature: security-hardening
created: 2026-03-28
status: draft
size: L
branch: feature/security-hardening
---

# Tech Spec: security-hardening — Eliminate All Security Threats in geo-fix

## Overview

Comprehensive security hardening of geo-fix: 12 threats (2 critical, 3 high, 4 medium, 3 low). Core changes: per-session CA with ephemeral confdir, watchdog subprocess for crash recovery, DPAPI-encrypted state with session UUID, correct firewall paths via registry auto-detection, safe Firefox backup, port auto-selection, CSP hardening, and install.bat integrity checks.

## Architecture

### Changes to Existing Components

```
main.py
  ├── NEW: Create session tmpdir with restricted ACL for mitmproxy confdir
  ├── CHANGED: Startup reordered — mitmproxy binds FIRST, then system changes
  ├── NEW: Watchdog subprocess started BEFORE any system modifications
  ├── NEW: --port flag + auto-select via pre-bind socket
  ├── CHANGED: Firewall rules created unconditionally on every start
  └── CHANGED: Cleanup always removes CA cert (by thumbprint) + firewall rules

system_config.py
  ├── CHANGED: install_ca_cert(confdir) accepts confdir parameter, records thumbprint
  ├── CHANGED: uninstall_ca_cert(thumbprint) removes cert by thumbprint, not name
  ├── CHANGED: cleanup() calls uninstall_ca_cert() + deletes session tmpdir
  ├── CHANGED: ProxyState gains session_tmpdir, ca_thumbprint, session_id fields
  ├── CHANGED: set_firefox_proxy() backup uses shutil.copy2, not rename
  ├── CHANGED: unset_firefox_proxy() restore uses copy + unlink, not rename
  ├── CHANGED: enterprise_roots explicitly reverted on cleanup
  ├── CHANGED: State file encrypted with DPAPI + application entropy
  ├── CHANGED: create_firewall_rules() auto-detects browser paths from registry
  └── CHANGED: remove_firewall_rules() called unconditionally in cleanup

proxy_addon.py
  ├── CHANGED: _modify_csp() filters unsafe-inline/unsafe-eval/unsafe-hashes from derived script-src
  └── CHANGED: content-security-policy-report-only not modified (removed from loop)

setup_wizard.py
  ├── CHANGED: Skip button shows warning dialog + requires confirmation
  ├── CHANGED: Console wizard includes firewall prompt + DNS instructions
  └── CHANGED: CA install removed from wizard (now per-session in main.py)

NEW: watchdog.py
  └── Embedded in PyInstaller exe via --add-data. Extracted and spawned at runtime.
      Monitors main PID, runs cleanup on death. Registers ONLOGON scheduled task.

install.bat
  ├── CHANGED: SHA-256 verification for Python zip (pinned version hash)
  ├── CHANGED: get-pip.py pinned to versioned URL with hash
  └── CHANGED: .bat launchers created with attrib +R
```

### Shared Resources

- **Session tmpdir**: owned by `main.py`. Created with ACL `icacls <dir> /inheritance:r /grant:r %USERNAME%:(OI)(CI)F`. Contains per-session CA cert+key (mitmproxy confdir). Path stored in ProxyState. Deleted on cleanup.
- **Watchdog subprocess**: started by `main.py` BEFORE any system modifications. Independent process (CREATE_NEW_PROCESS_GROUP). Receives: main PID, state file path, session tmpdir path, session ID, stop token. Imports and calls `system_config.cleanup()` on main death.

### Startup Sequence (CHANGED)

```
 1. Parse CLI (--port, country code), acquire PID lock
 2. Generate session_id (uuid4)
 3. Create session tmpdir with restricted ACL
 4. Determine port: use --port value, or 8080, or auto-select (pre-bind socket)
 5. Start mitmproxy with confdir=session_tmpdir, listen_port=port
 6. Verify mitmproxy is listening (poll port, 10 retries × 500ms, abort on failure)
 7. Install session CA cert (install_ca_cert(session_tmpdir)), record thumbprint
 8. Start watchdog subprocess (args: main PID, state path, tmpdir, session_id, stop_token)
 9. Register ONLOGON scheduled task (fallback cleanup)
10. Set WinINET proxy (registry) with confirmed port
11. Set Firefox proxy (user.js copy backup)
12. Create firewall rules (auto-detected browser paths, unconditional)
13. Save state (DPAPI encrypted: includes session_tmpdir, ca_thumbprint, session_id)
14. Start tray icon
15. Block on stop_event
```

Key: mitmproxy binds (5-6) before any system changes (10-12). Watchdog (8) starts right after CA install (7), minimizing the unprotected window. If mitmproxy fails to bind, no system modifications occur.

### Shutdown Sequence (CHANGED)

```
1. Unset WinINET proxy (restore original)
2. Unset Firefox proxy (restore via copy + unlink, revert enterprise_roots)
3. Remove firewall rules (unconditional, netsh delete is idempotent)
4. Uninstall CA cert from store (by thumbprint from state)
5. Delete session tmpdir (with CA key)
6. Delete state file
7. Signal watchdog to stop (write stop_token to flag file)
8. Remove ONLOGON scheduled task
```

### Watchdog Design

```
watchdog.py — spawned as separate process

Input (CLI args): main_pid, state_file_path, session_tmpdir_path, session_id, stop_token
Loop (every 2 seconds):
  1. Check if stop flag file exists and contains correct stop_token → clean exit
  2. Check if main_pid is alive (OpenProcess / psutil)
  3. If dead:
     a. Load state, verify session_id matches
     b. Call system_config.cleanup(state) — same function as normal cleanup
     c. Delete session tmpdir (shutil.rmtree)
     d. Remove ONLOGON scheduled task
     e. Exit

Boot-time fallback:
  - Registers: schtasks /create /sc ONLOGON /tn "geo-fix-cleanup" /tr "<exe> --cleanup"
    with /rl LIMITED and execution time limit PT5M
  - Removed on clean shutdown (step 8 above)
  - On boot: --cleanup checks for stale state, runs cleanup if state exists
  - State older than 24 hours is treated as stale and cleaned

PyInstaller compatibility:
  - watchdog.py bundled via --add-data "src/watchdog.py;src"
  - At runtime: extracted from sys._MEIPASS to a temp location
  - Spawned with: subprocess.Popen([sys.executable, extracted_watchdog_path, ...],
    creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW)
  - Note: sys.executable in PyInstaller is the .exe itself; watchdog.py runs as a script
    argument to the Python embedded in the exe bundle

Session ID verification:
  - Watchdog records session_id at start
  - Before cleanup, compares state file's session_id with its own
  - Mismatch → another session took over → exit without cleanup
```

## Decisions

### D-1: Per-session CA via confdir

mitmproxy generates a new CA when `confdir` points to an empty directory. Fresh `tempfile.mkdtemp()` per session → unique CA each run. `install_ca_cert()` modified to accept confdir parameter and read cert from there. CA thumbprint recorded in state for targeted removal.

### D-2: Thumbprint-based CA removal

Current `uninstall_ca_cert()` removes by name ("mitmproxy"). This could miss renamed certs or delete wrong ones. Changed to record SHA-1 thumbprint at install time and remove by thumbprint: `certutil -delstore -user Root <thumbprint>`.

### D-3: Watchdog subprocess over Job Object

Windows Job Objects cannot execute Python cleanup after parent hard-kill (entire process tree dies). Separate watchdog process survives because it has CREATE_NEW_PROCESS_GROUP. Combined with ONLOGON scheduled task for BSOD/power-loss fallback.

Threat model boundary: this design protects against accidental crashes and hard kills. It does NOT protect against a malicious process running as the same user that deliberately kills the watchdog first.

### D-4: DPAPI with application entropy

`CryptProtectData` with `optionalEntropy=b"geo-fix-state-v1"` (fixed application-specific string). Provides confidentiality + integrity bound to current Windows user. No separate key management. Tampered or cross-user state is rejected.

Implementation: ctypes calls to `crypt32.CryptProtectData` / `CryptUnprotectData` with DATA_BLOB structures. LocalFree on output blob.

### D-5: Pre-bind socket for port selection

Bind socket to port 0, read assigned port, close socket, pass to mitmproxy. If --port specified, try that first; on failure, fall back to auto-select. Race window between socket close and mitmproxy bind is negligible on localhost.

### D-6: Firewall rules unconditional on every start

Firewall rules created at every geo-fix start (not only in wizard). Removed unconditionally at every cleanup (netsh delete is idempotent). No firewall_rules_created flag in state — always attempt removal.

### D-7: Watchdog stop signaling via flag file with token

Generate random stop_token at startup. Pass to watchdog as CLI arg. On clean shutdown, write stop_token to `.geo-fix-watchdog-stop` file. Watchdog polls every 2s, checks file content matches token. Prevents forgery: attacker must know the random token to fake a stop signal.

### D-8: get-pip.py pinned to versioned URL

Instead of rolling `https://bootstrap.pypa.io/get-pip.py`, pin to a specific release: `https://bootstrap.pypa.io/pip/{version}/get-pip.py` with hardcoded SHA-256. Update hash when bumping pip version. Comment in install.bat notes the verification URL.

## Testing Strategy

### Mock Boundaries

| Component | Unit Test | Integration Test |
|---|---|---|
| DPAPI encrypt/decrypt | Mock ctypes.windll.crypt32 | Real DPAPI (Windows-only) |
| State file load/save | Mock DPAPI layer | Real DPAPI + real filesystem |
| Browser path lookup | Mock winreg | Real registry (Windows-only) |
| Port selection | Real socket (cross-platform) | Real mitmproxy bind |
| CSP modification | No mocks needed | N/A |
| CA cert install/remove | Mock subprocess | Real certutil (Windows-only) |
| Firefox backup/restore | Mock filesystem (tmp_path) | Real filesystem |
| Watchdog PID monitoring | Mock os.kill/OpenProcess | Real subprocess + kill |
| Firewall rules | Mock subprocess | Real netsh (requires elevation) |
| Wizard dialogs | Mock tkinter | N/A (manual verification) |

### Unit Tests (cross-platform where possible)

| Test | What | Platform |
|---|---|---|
| DPAPI abstraction | Encrypt/decrypt round-trip, tamper rejection via mock | Cross-platform |
| Port selection | Pre-bind + auto-select, occupied port fallback | Cross-platform |
| CSP modification | No unsafe-* promotion, report-only untouched, nonce added | Cross-platform |
| State serialization | Session ID, thumbprint, tmpdir path in state, DPAPI tamper | Cross-platform (mocked) |
| Browser path lookup | Registry hit → path, miss → filesystem fallback → None | Cross-platform (mocked winreg) |
| Firefox backup logic | Copy semantics, original preserved on simulated crash | Cross-platform |
| Session ID verification | Matching and mismatching session IDs | Cross-platform |
| Hash verification | Correct hash passes, wrong hash fails | Cross-platform |
| Wizard skip warning | Skip shows warning (mock tkinter.messagebox) | Cross-platform |

### Integration Tests (Windows-only)

| Test | What | Elevation |
|---|---|---|
| Per-session CA | New confdir → new fingerprint, cert installed/removed by thumbprint | No |
| CA cleanup on all paths | stop, SIGTERM, --cleanup all remove cert | No |
| Watchdog recovery | Start main → kill main → watchdog cleans up within 15s | No |
| Watchdog session ID | Two sessions → watchdog only cleans own session | No |
| Registry proxy ordering | Proxy set only after mitmproxy binds | No |
| Firefox copy backup | Backup via copy, restore via copy+unlink, enterprise_roots reverted | No |
| DPAPI real round-trip | Encrypt state, decrypt, verify all fields | No |
| State tamper detection | Modify encrypted blob → rejected → clean defaults | No |
| Port conflict | Occupy 8080 → auto-selects different port → registry correct | No |
| ACL on session tmpdir | Verify tmpdir ACL via icacls output parse | No |
| ONLOGON scheduled task | Task registered, visible via schtasks /query, removed on clean exit | No |
| Firewall auto-detect paths | Paths match actual browser install locations | Elevated |
| Firewall create/remove | Rules created, visible in netsh output, removed on cleanup | Elevated |

### E2E Tests (Windows-only)

| Test | What |
|---|---|
| Hard kill full cleanup | Start geo-fix → taskkill /F → verify within 15s: no CA cert (certutil -viewstore), no proxy (reg query), no Firefox mods, no firewall rules (netsh show rule) |
| Port conflict E2E | Block 8080 → start geo-fix → verify traffic flows through auto-selected port |

CI requirements:
- Runner: `windows-latest` (GitHub Actions)
- Cert store and registry: no elevation needed (CurrentUser)
- Firewall tests: require elevation → run only with `[elevated]` marker, skip in standard CI
- Tests run sequentially (cert store / registry are global state)
- Failure artifacts: upload state files, audit.log on test failure
- DPAPI tests valid within single runner session only

### AC Traceability Matrix

| AC | Test Type | Test Name |
|---|---|---|
| AC-1.1 | Integration | test_ca_removed_after_stop |
| AC-1.2 | Integration | test_session_tmpdir_deleted_after_stop |
| AC-1.3 | Integration | test_new_ca_fingerprint_each_session |
| AC-1.4 | Integration | test_session_tmpdir_acl |
| AC-2.1 | Integration | test_ca_cleanup_all_paths (stop, SIGTERM, --cleanup) |
| AC-2.2 | Integration | test_ca_cleanup_all_paths (--cleanup path) |
| AC-3.1 | E2E | test_hard_kill_full_cleanup |
| AC-3.2 | Integration | test_watchdog_starts_automatically |
| AC-3.3 | Integration | test_onlogon_task_registered + manual reboot verify |
| AC-3.4 | Integration | test_watchdog_visible_as_process |
| AC-4.1 | Unit + Integration | test_firefox_backup_uses_copy |
| AC-4.2 | Integration | test_enterprise_roots_reverted |
| AC-4.3 | Unit | test_firefox_backup_crash_preserves_original (mock crash) |
| AC-4.4 | Unit + Integration | test_firefox_restore_uses_copy |
| AC-5.1 | Unit | test_hash_verification_python_zip |
| AC-5.2 | Unit | test_hash_verification_getpip |
| AC-5.3 | Unit | test_hash_mismatch_exits_nonzero |
| AC-5.4 | Unit | test_hash_mismatch_deletes_file |
| AC-6.1 | Unit + Integration | test_port_selection_auto, test_port_flag |
| AC-6.2 | Integration | test_proxy_set_after_mitmproxy_bind |
| AC-6.3 | Unit | test_port_unavailable_error_no_registry |
| AC-7.1 | Unit | test_csp_no_unsafe_promotion |
| AC-7.2 | Unit | test_csp_report_only_untouched |
| AC-7.3 | Unit | test_csp_other_directives_preserved |
| AC-8.1-8.2 | Integration | test_browser_path_autodetect |
| AC-8.3 | Integration | test_firewall_rules_removed_on_stop |
| AC-8.4 | Unit | test_browser_not_found_skipped_with_warning |
| AC-8.5 | Integration | test_firewall_rules_created_every_start |
| AC-9.1-9.2 | Integration | test_dpapi_state_encrypted |
| AC-9.3 | Integration | test_tampered_state_rejected |
| AC-9.4 | Integration | test_tampered_state_clean_defaults |
| AC-9.5 | Integration | test_dpapi_user_scope (document as manual) |
| AC-10.1-10.2 | Unit | test_wizard_skip_shows_warning (mock tkinter) |
| AC-11.1-11.2 | Unit | test_console_wizard_all_steps (mock input) |
| AC-12.1 | Integration | test_bat_files_readonly |

## Implementation Tasks

### Wave 1: Per-session CA lifecycle

**Task 1: Per-session CA with ephemeral confdir and thumbprint-based removal**
Generate a fresh mitmproxy CA per session using an isolated confdir in a temp directory with restricted ACL. Record CA thumbprint in state for targeted removal. On cleanup, remove cert by thumbprint and destroy the session tmpdir. Add session_id and ca_thumbprint fields to ProxyState.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`, `test-reviewer`
- Files to modify: `src/system_config.py`, `src/main.py`
- Files to read: `src/proxy_addon.py`, `src/health_check.py`

### Wave 2: Cleanup paths and Firefox safety

**Task 2: Add CA removal and tmpdir cleanup to all exit paths**
Ensure CA cert is uninstalled and session tmpdir is deleted in every termination path: normal stop, --cleanup, SIGTERM, atexit, and crash recovery. Must run before state file deletion.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`
- Files to modify: `src/system_config.py`, `src/main.py`
- Files to read: `src/health_check.py`

**Task 3: Safe Firefox backup with copy semantics and enterprise_roots cleanup**
Fix the Firefox user.js lifecycle to preserve the original file during backup and restore operations. Ensure the enterprise_roots browser setting is explicitly reverted to its pre-proxy state on every cleanup.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `test-reviewer`
- Files to modify: `src/system_config.py`

### Wave 3: Watchdog and startup reorder

**Task 4: Implement watchdog subprocess with session-aware cleanup**
Create a standalone watchdog that monitors the main process and performs full cleanup on unexpected death. Must survive parent termination, verify session ownership before acting, and register a boot-time fallback task. Must be compatible with PyInstaller packaging.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`, `test-reviewer`
- Files to modify: `src/main.py`, `build/build.py`
- Files to read: `src/system_config.py`

**Task 5: Reorder startup — system changes only after mitmproxy bind, add port selection**
Restructure the startup sequence so mitmproxy binds the port before any registry or filesystem modifications. Add configurable port via --port flag with automatic fallback to a free port when the requested one is occupied.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `test-reviewer`
- Files to modify: `src/main.py`
- Files to read: `src/system_config.py`, `src/health_check.py`

### Wave 4: State and firewall hardening

**Task 6: Encrypt state file with DPAPI user-scope protection**
Replace the plain JSON state file with a DPAPI-encrypted blob tied to the current Windows user, making it unreadable and unmodifiable by other users or processes. Unreadable or tampered state must fall back to clean defaults with a logged warning and best-effort cleanup.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`, `test-reviewer`
- Files to modify: `src/system_config.py`

**Task 7: Fix firewall rules with auto-detected browser paths and unconditional lifecycle**
Replace broken hardcoded browser paths with paths auto-detected from the Windows registry and filesystem. Create rules on every start and remove them unconditionally on every cleanup regardless of state flags.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`, `test-reviewer`
- Files to modify: `src/system_config.py`, `src/main.py`

### Wave 5: CSP, installer, and UX

**Task 8: Harden CSP modification — minimal nonce injection**
Tighten CSP handling to only add the injection nonce without weakening existing security policies. Filter dangerous directives when deriving script-src and leave monitoring headers untouched.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`
- Files to modify: `src/proxy_addon.py`

**Task 9: Add SHA-256 verification and read-only launchers to install.bat**
Add download integrity checks using pinned SHA-256 hashes for all external downloads. Pin get-pip.py to a versioned URL. Set launcher files to read-only after creation.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`
- Files to modify: `install.bat`

**Task 10: Fix setup wizard — skip warning, console parity, per-session CA adaptation**
Add an explicit warning when users skip setup, ensure console mode offers all the same configuration steps as GUI, and remove the direct CA install call (now handled per-session by main.py).
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `test-reviewer`
- Files to modify: `src/setup_wizard.py`

### Wave 6: Audit

**Task 11: Code Audit**
Holistic code quality review of all security-hardening changes. Verify naming, error handling, and code structure consistency across all modified files.
- Skill: `code-reviewing`
- Reviewers: none

**Task 12: Security Audit**
OWASP Top 10 review of all changes. Verify each of the 12 original threats is actually closed by the implementation, and no new attack surfaces were introduced.
- Skill: `security-auditor`
- Reviewers: none

**Task 13: Test Audit**
Test quality and coverage review. Verify all 31 acceptance criteria have corresponding tests per the traceability matrix, and test boundaries match the mock boundary table.
- Skill: `test-master`
- Reviewers: none

### Wave 7: Final

**Task 14: QA — Pre-deploy acceptance testing**
Run full test suite and verify all acceptance criteria from user-spec. Check each threat closure against the traceability matrix. Confirm CI pipeline passes on Windows runner.
- Skill: `pre-deploy-qa`
- Reviewers: none
