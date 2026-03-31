# Decisions Log: wifi-geolocation-leak

Agent reports on completed tasks. Each entry is written by the agent that executed the task.

---

<!-- Entries are added by agents as tasks are completed.

Format is strict — use only these sections, do not add others.
Do not include: file lists, findings tables, JSON reports, step-by-step logs.
Review details — in JSON files via links. QA report — in logs/working/.

## Task N: [title]

**Status:** Done
**Commit:** abc1234
**Agent:** [teammate name or "main agent"]
**Summary:** 1-3 sentences: what was done, key decisions. Not a file list.
**Deviations:** None / Deviated from spec: [reason], did [what].

**Reviews:**

*Round 1:*
- code-reviewer: 2 findings → [logs/working/task-N/code-reviewer-1.json]
- security-auditor: OK → [logs/working/task-N/security-auditor-1.json]

*Round 2 (after fixes):*
- code-reviewer: OK → [logs/working/task-N/code-reviewer-2.json]

**Verification:**
- `npm test` → 42 passed
- Manual check → OK

-->

## Task 1: Windows Location Services registry control

**Status:** Done
**Commit:** 2d907f3, 31a60f3
**Agent:** main agent
**Summary:** Added `disable_location_services()` and `restore_location_services()` to system_config.py for HKCU registry control of Windows Location Services. Functions follow existing winreg patterns with platform guards, graceful error handling, and input validation on restore.
**Deviations:** None.

**Reviews:**

*Round 1:*
- code-reviewer: 2 critical (wrong HKLM hive, missing CreateKeyEx), 1 medium → [logs/working/task-1/code-reviewer-1.json]

*Round 2 (after fixes):*
- Fixed HKEY_LOCAL_MACHINE → HKEY_CURRENT_USER, OpenKey → CreateKeyEx, added hive assertions to tests.

**Verification:**
- `pytest test/test_system_config_location.py` → 10 passed
- Full suite → 307 passed, 0 failed

---

## Task 2: Universal geolocation JS injection + proxy API intercept

**Status:** Done
**Commit:** a02293e, 12aa744
**Agent:** main agent
**Summary:** Extended proxy_addon.py with two-tier JS injection (geo-only on non-target domains, full on target) and geolocation API intercept in request(). Added navigator.permissions.query override to inject.js. CSP skip guard prevents injection on pages with script-src 'none'.
**Deviations:** None.

**Reviews:**

*Round 1:*
- code-reviewer: 3 high (CSP false positive, missing logger assertion, missing integration tests), 2 medium → [logs/working/task-2/code-reviewer-1.json]

*Round 2 (after fixes):*
- Fixed _has_restrictive_csp parsing, added logger.error assertion, added 3 integration tests, extracted _inject_script helper.

**Verification:**
- `pytest test/test_proxy_addon.py` → 58 passed
- `pytest test/test_integration_proxy.py` → all passed
- Full suite → 307 passed, 0 failed

---

## Task 3: Startup/cleanup integration

**Status:** Done
**Commit:** 4743651, d98f329
**Agent:** main agent
**Summary:** Wired Location Services disable/restore into main.py startup and cleanup(). Added ProxyState.original_location_services field (backward-compatible default), cleanup label, and _execute_cleanup_by_label branch. Cleanup order updated to include Location Services before state file deletion.
**Deviations:** None.

**Reviews:**

*Round 1:*
- code-reviewer: 1 major (hollow test), 3 minor → [logs/working/task-3/code-reviewer-1.json]

*Round 2 (after fixes):*
- Replaced hollow mock test with source inspection test, removed unused import, updated docstring.

**Verification:**
- `pytest test/test_main.py` → 3 passed
- `pytest test/test_system_config.py` → all passed
- Full suite → 307 passed, 0 failed
