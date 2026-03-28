---
feature: security-hardening
created: 2026-03-28
status: draft
size: L
branch: feature/security-hardening
---

# Tech Spec: security-hardening — Eliminate All Security Threats in geo-fix

## Overview

Comprehensive security hardening of geo-fix: 12 threats (2 critical, 3 high, 4 medium, 3 low). Core changes: per-session CA with ephemeral confdir, watchdog process for crash recovery, DPAPI-encrypted state, correct firewall paths, safe Firefox backup, port auto-selection, CSP hardening, and install.bat integrity checks.

## Architecture

### Changes to Existing Components

```
main.py
  ├── NEW: Create session tmpdir with restricted ACL for mitmproxy confdir
  ├── CHANGED: Start mitmproxy FIRST, then set proxy in registry (order swap)
  ├── NEW: Start watchdog subprocess before proxy setup
  ├── NEW: --port flag + auto-select via pre-bind socket
  ├── CHANGED: Create firewall rules on every start (not just wizard)
  └── CHANGED: Cleanup always removes CA cert + firewall rules

system_config.py
  ├── CHANGED: cleanup() calls uninstall_ca_cert() + deletes session tmpdir
  ├── CHANGED: set_firefox_proxy() uses copy, not rename (backup + restore)
  ├── CHANGED: enterprise_roots explicitly reverted on cleanup
  ├── CHANGED: State file encrypted with DPAPI (CryptProtectData/CryptUnprotectData)
  ├── CHANGED: create_firewall_rules() auto-detects browser paths from registry
  ├── CHANGED: remove_firewall_rules() called unconditionally in cleanup
  └── NEW: _find_browser_path(browser) — registry lookup + filesystem fallback

proxy_addon.py
  ├── CHANGED: _modify_csp() does not copy unsafe-* from default-src
  └── CHANGED: content-security-policy-report-only not modified

setup_wizard.py
  ├── CHANGED: Skip button shows explicit warning + requires confirmation
  ├── CHANGED: Console wizard includes firewall + DNS steps
  └── CHANGED: CA install moved to per-session (wizard only configures preferences)

NEW: watchdog.py
  └── Standalone process: monitors main PID, runs cleanup on death

install.bat
  ├── CHANGED: SHA-256 verification for Python zip and get-pip.py
  └── CHANGED: .bat launchers created with read-only attribute
```

### Shared Resources

- **Session tmpdir**: owned by `main.py`, used by mitmproxy as `confdir`. Contains per-session CA cert+key. ACL restricted to current user. Deleted on cleanup.
- **Watchdog subprocess**: owned by `main.py`, monitors main PID. Independent process that survives main process death.

### Startup Sequence (CHANGED)

```
1. Parse CLI, acquire PID lock
2. Create session tmpdir (ACL: current user only)
3. Start mitmproxy with confdir=session_tmpdir (binds port)
4. Verify mitmproxy is listening (poll port)
5. Install session CA cert from session_tmpdir
6. Start watchdog subprocess (passes: PID, state file path, session tmpdir path)
7. Set WinINET proxy (registry)
8. Set Firefox proxy (user.js copy backup)
9. Create firewall rules (auto-detected browser paths)
10. Save state (DPAPI encrypted)
11. Start tray icon
12. Block on stop_event
```

Key change: steps 3-5 before steps 7-9. If mitmproxy fails to bind, no system changes are made.

### Shutdown Sequence (CHANGED)

```
1. Unset WinINET proxy (restore original)
2. Unset Firefox proxy (restore from backup via copy)
3. Remove firewall rules (unconditional)
4. Uninstall CA cert from store
5. Delete session tmpdir (with CA key)
6. Delete state file
7. Stop watchdog
```

### Watchdog Design

```python
# watchdog.py — runs as subprocess
# Input: main_pid, state_file_path, session_tmpdir_path
# Loop: check if main_pid alive every 2 seconds
# On death: load state → run cleanup → delete tmpdir → exit
# Also: register boot-time scheduled task on start, remove on clean exit
```

The watchdog is started via `subprocess.Popen([sys.executable, "watchdog.py", ...])` with `CREATE_NEW_PROCESS_GROUP` flag so it survives parent death. On clean shutdown, main process signals watchdog to exit via a named pipe or simple flag file.

Boot-time fallback: watchdog registers a Windows scheduled task (`schtasks /create /sc ONSTART`) that checks for stale state and cleans up. Task is removed on clean shutdown.

## Decisions

### D-1: Per-session CA via confdir

mitmproxy generates a new CA when `confdir` points to an empty directory. By creating a fresh `tempfile.mkdtemp()` per session and passing it as `confdir`, we get a unique CA each run. No need to manually generate certs — mitmproxy handles it.

Alternative considered: Manual cert generation with `cryptography` library. Rejected: adds a dependency and duplicates mitmproxy's built-in behavior.

### D-2: Watchdog subprocess over Job Object

Windows Job Objects can detect child termination but cannot execute Python cleanup code after the parent is hard-killed (the entire process tree dies). A separate watchdog process survives because it's not in the same job.

Alternative: Thread-based monitoring. Rejected: threads die with the process on hard kill.
Alternative: Windows service. Rejected: requires admin install, too heavy for this use case.

### D-3: DPAPI over HMAC

DPAPI (`CryptProtectData`) provides both confidentiality and integrity, bound to the current Windows user. No need to manage a separate HMAC key. Implementation via `ctypes.windll.crypt32`.

Alternative: HMAC-SHA256 with MachineGuid. Rejected: provides integrity only, key derivation adds complexity.

### D-4: Pre-bind socket for port selection

Bind a socket to port 0, read the assigned port, close the socket, pass to mitmproxy. Avoids introspecting mitmproxy internals. Race condition window (between socket close and mitmproxy bind) is negligible on localhost.

Alternative: mitmproxy port=0. Rejected: no documented API to read the actual bound port after startup.

### D-5: Browser path auto-detection

Read `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{browser}.exe` for each browser. Fallback: scan `Program Files` and `Program Files (x86)`. If not found — skip that browser, log warning.

### D-6: Firewall rules on every start

Decoupled from wizard. Created at startup if user opted in (preference stored in config). Removed unconditionally at cleanup (netsh delete is idempotent).

## Testing Strategy

### Unit Tests (cross-platform)

| Area | What to test |
|---|---|
| DPAPI state file | Encrypt/decrypt round-trip, tampered data rejection, missing file handling |
| Port selection | Pre-bind socket, fallback on occupied port |
| CSP modification | No unsafe-* promotion from default-src, report-only untouched |
| Hash verification | Correct hash passes, wrong hash fails, missing file fails |
| Browser path lookup | Registry hit, registry miss + filesystem fallback, not found |

### Integration Tests (Windows-only)

| Area | What to test |
|---|---|
| Per-session CA | New confdir → new cert fingerprint, cert installed/uninstalled from store |
| Watchdog recovery | Start main + watchdog → kill main → verify cleanup within 15s |
| Registry proxy | Set after mitmproxy binds, not before; restored on stop |
| Firefox backup | Copy-based backup/restore, enterprise_roots cleanup |
| Firewall rules | Auto-detected paths, rules created/removed |

### E2E Tests (Windows-only)

| Area | What to test |
|---|---|
| Hard kill recovery | Start geo-fix → taskkill /F → verify: no CA cert, no proxy, no Firefox mods, no firewall rules (within 15s) |
| Port conflict | Occupy port 8080 → start geo-fix → verify auto-selects different port, registry points to correct port |

## Implementation Tasks

### Wave 1: Core Security (Critical + High foundations)

**Task 1: Per-session CA with ephemeral confdir**
Create session tmpdir with restricted ACL, pass as mitmproxy confdir, install cert on start, delete everything on stop. Replaces permanent CA with ephemeral per-session CA.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`, `test-reviewer`
- Files to modify: `src/main.py`, `src/system_config.py`
- Files to read: `src/proxy_addon.py`, `src/health_check.py`

**Task 2: Add uninstall_ca_cert() to all cleanup paths**
Ensure CA cert removal in cleanup(), atexit, SIGTERM handler, and --cleanup crash recovery. Must run before state file deletion.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`
- Files to modify: `src/system_config.py`, `src/main.py`

**Task 3: Fix Firefox backup — copy instead of rename + enterprise_roots cleanup**
Replace rename() with shutil.copy2() in both backup and restore paths. Explicitly remove enterprise_roots on cleanup if it wasn't originally present.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `test-reviewer`
- Files to modify: `src/system_config.py`

### Wave 2: Crash Recovery

**Task 4: Implement watchdog subprocess**
Create watchdog.py that monitors main PID and runs cleanup on death. Register boot-time scheduled task as fallback. Clean exit removes scheduled task and signals watchdog to stop.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`, `test-reviewer`
- Verify-smoke: `python -c "import subprocess; p = subprocess.Popen([...]); ...check PID monitoring..."`
- Files to modify: `src/main.py`
- Files to create: `src/watchdog.py`

**Task 5: Reorder startup — proxy after mitmproxy bind + port selection**
Swap startup order: start mitmproxy first, verify bind, then set registry proxy. Add --port flag and auto-select via pre-bind socket when port is occupied.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `test-reviewer`
- Files to modify: `src/main.py`, `src/system_config.py`

### Wave 3: State & Firewall Hardening

**Task 6: DPAPI-encrypted state file**
Replace plain JSON state with DPAPI-encrypted blob. Implement CryptProtectData/CryptUnprotectData via ctypes. Reject tampered/unreadable state with warning, fall back to clean defaults.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`, `test-reviewer`
- Files to modify: `src/system_config.py`

**Task 7: Fix firewall rules — auto-detect browser paths + always create/remove**
Replace hardcoded broken paths with registry lookup (App Paths) + filesystem fallback. Create rules on every start, remove unconditionally on cleanup. Remove firewall_rules_created flag from state.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`, `test-reviewer`
- Files to modify: `src/system_config.py`, `src/main.py`

### Wave 4: CSP + Installer Hardening

**Task 8: Harden CSP modification**
Don't copy unsafe-eval/unsafe-inline from default-src when creating script-src. Don't modify content-security-policy-report-only headers.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`
- Files to modify: `src/proxy_addon.py`

**Task 9: Add SHA-256 verification to install.bat**
Hardcode expected hashes for Python embed zip and get-pip.py. Verify after download, delete + exit on mismatch. Set .bat launchers to read-only.
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `security-auditor`
- Files to modify: `install.bat`, `build/build.py`

### Wave 5: UX Fixes

**Task 10: Fix setup wizard — skip warning + console firewall**
Add explicit warning dialog when user clicks Skip. Add firewall and DNS steps to console wizard. Update CA install to work with per-session model (wizard now configures preferences only).
- Skill: `code-writing`
- Reviewers: `code-reviewer`, `test-reviewer`
- Files to modify: `src/setup_wizard.py`

### Wave 6: Audit

**Task 11: Code Audit**
Holistic code quality review of all security-hardening changes across all modified files.
- Skill: `code-reviewing`
- Reviewers: none

**Task 12: Security Audit**
OWASP Top 10 review of all security-hardening changes. Verify all 12 threats are actually closed.
- Skill: `security-auditor`
- Reviewers: none

**Task 13: Test Audit**
Test quality and coverage review. Verify all acceptance criteria have corresponding tests.
- Skill: `test-master`
- Reviewers: none

### Wave 7: Final

**Task 14: QA — Pre-deploy acceptance testing**
Run all tests, verify all 31 acceptance criteria from user-spec are met. Check each threat is closed.
- Skill: `pre-deploy-qa`
- Reviewers: none
- Verify-smoke: `python -m pytest test/ -v`
