# Decisions Log: resource-optimization

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

## Task 1: Replace DumpMaster with minimal Master

**Status:** Done
**Commit:** d1ac6b3
**Agent:** coder-master
**Summary:** Replaced `DumpMaster` (35 default addons) with base `mitmproxy.master.Master` plus 6 essential addons (Core, Proxyserver, NextLayer, TlsConfig, KeepServing, ErrorCheck), reducing hook overhead by ~80%. `_start_mitmproxy()` now returns `(thread, master)` tuple for Task 3's controlled restart. No PyInstaller hidden-import issues encountered.
**Deviations:** None.

**Reviews:**

*Round 1:*
- code-reviewer: approved_with_suggestions (4 minor) → [logs/working/task-1/code-reviewer-t1-round1.json]
- security-auditor: approved (2 minor) → [logs/working/task-1/security-auditor-t1-round1.json]
- test-reviewer: needs_improvement (2 major, 2 minor) → [logs/working/task-1/test-reviewer-t1-round1.json]

*Round 2 (after fixes):*
- test-reviewer: passed → [logs/working/task-1/test-reviewer-t1-round2.json]

**Verification:**
- `pytest test/test_master_setup.py -v` → 5 passed
- `pytest test/ -x` → 213 passed, 13 skipped
- Smoke: `python3 -c "from mitmproxy.master import Master; ..."` → OK

## Task 2: Add FlowCleanup addon

**Status:** Done
**Commit:** 82d4920
**Agent:** coder-cleanup
**Summary:** Added stateless `FlowCleanup` addon to `src/proxy_addon.py` with `response`, `error`, `websocket_message`, and `websocket_end` hooks that clear flow content after processing to reduce GC pressure and prevent unbounded WebSocket message growth. Registration in `main.py` deferred to Task 3 per spec; ordering test added with `xfail` marker.
**Deviations:** None.

**Reviews:**

*Round 1:*
- code-reviewer: 1 major, 2 minor → [logs/working/task-2/code-reviewer-t2-round1.json]
- security-auditor: approved (1 minor, Python limitation) → [logs/working/task-2/security-auditor-t2-round1.json]
- test-reviewer: 1 major, 1 minor → [logs/working/task-2/test-reviewer-t2-round1.json]

*Round 2 (after fixes):*
- code-reviewer: approved → [logs/working/task-2/code-reviewer-t2-round2.json]
- test-reviewer: passed → [logs/working/task-2/test-reviewer-t2-round2.json]

**Verification:**
- `pytest test/test_proxy_addon.py -v` → 34 passed, 1 xfailed

## Task 3: Add RAM monitoring with proxy auto-restart

**Status:** Done
**Commit:** 7d7fe7e
**Agent:** coder-ram
**Summary:** Extended `_monitor_loop` with RAM monitoring: checks process memory every 60 seconds via ctypes (Windows) or `/proc/self/status` (Linux), restarts mitmproxy thread if >300MB with idle guard (10s), cooldown (10min), and rate limit (max 3/hour). Extracted `_should_restart()` as testable pure function. Registered `FlowCleanup` as last addon in `_start_mitmproxy()`. Key security decision: CA key files deleted on all restart paths including failure — reviewed and confirmed by security auditor.
**Deviations:** Extracted `_should_restart()` helper not in original spec — added during test review to make guard logic independently testable.

**Reviews:**

*Round 1:*
- code-reviewer: changes_required (1 critical, 3 major) → [logs/working/task-3/code-reviewer-t3-round1.json]
- security-auditor: changes_required (1 major, 3 minor) → [logs/working/task-3/security-auditor-t3-round1.json]
- test-reviewer: failed (7 major) → [logs/working/task-3/test-reviewer-t3-round1.json]

*Round 2 (after fixes):*
- code-reviewer: approved → [logs/working/task-3/code-reviewer-t3-round2.json]
- security-auditor: approved → [logs/working/task-3/security-auditor-t3-round2.json]
- test-reviewer: passed (3 minor) → [logs/working/task-3/test-reviewer-t3-round2.json]

**Verification:**
- `pytest test/test_ram_monitor.py -v` → 17 passed
- `pytest test/ -x` → 231 passed, 13 skipped

## Task 4: Minor CPU optimizations in proxy_addon

**Status:** Done
**Commit:** 647a1fc
**Agent:** coder-cpu
**Summary:** Eliminated `html_text.lower()` full-string copy in `_find_inject_position()` by switching to `re.search()` with `re.IGNORECASE`. Replaced Python `for` loop in `is_target_domain()` with `str.endswith(tuple)` and pre-computed `frozenset` for bare domain lookup. Both optimizations reduce per-call allocations on the hot path with identical behavior.
**Deviations:** None.

**Reviews:**

*Round 1:*
- code-reviewer: approved (2 minor optional) → [logs/working/task-4/code-reviewer-round1.json]
- security-auditor: approved (1 minor informational) → [logs/working/task-4/security-auditor-1.json]
- test-reviewer: passed (5 minor) → [logs/working/task-4/test-reviewer-1.json]

**Verification:**
- `pytest test/test_proxy_addon.py test/test_presets.py -v` → 77 passed
- `pytest test/ -x` → 237 passed, 13 skipped
