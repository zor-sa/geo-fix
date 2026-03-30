# Decisions Log: security-hardening-r2

Agent reports on completed tasks. Each entry is written by the agent that executed the task.

---

<!-- Entries are added by agents as tasks are completed. -->

## Task 1: Delete CA key file after mitmproxy loads it

**Status:** Done
**Commit:** a2d157e
**Agent:** coder-ca-keys
**Summary:** Wrote 5 unit tests for `delete_ca_key_files` and `delete_ca_public_cert` in `test/unit/test_delete_ca_key_files.py` covering file removal, public cert preservation, and idempotency. Wrote 1 integration test in `test/test_integration_ca_key_deletion.py` confirming mitmproxy remains functional after CA key files deleted from disk. Key decision: kept TDD-anchor-mandated tests in `test/unit/` despite partial overlap with existing `test/test_ca_key_deletion.py` — task spec was explicit about file location and test names.
**Deviations:** None.

**Reviews:**

*Round 1:*
- code-reviewer: approved_with_suggestions, 2 major + 3 minor → [logs/working/task-1/code-reviewer-round1.json]
- security-auditor: approved, 2 minor → [logs/working/task-1/security-auditor-round1.json]
- test-reviewer: needs_improvement, 1 major + 2 minor → [logs/working/task-1/test-reviewer-round1.json]

*Round 2 (after fixes):*
- code-reviewer: approved → [logs/working/task-1/code-reviewer-round2.json]
- test-reviewer: passed → [logs/working/task-1/test-reviewer-round2.json]

**Verification:**
- `pytest test/unit/test_delete_ca_key_files.py -v` → 5 passed
- `pytest test/test_integration_ca_key_deletion.py -v` → 1 skipped (Linux; passes on Windows CI)
- `pytest test/ -v -k "ca_key or ca_public or key_deletion"` → 14 passed, 1 skipped

## Task 4: Firewall cleanup by prefix

**Status:** Done
**Commit:** 57b9e40
**Agent:** coder-firewall
**Summary:** Added 3 unit tests for prefix-based firewall rule cleanup in `test/test_system_config.py`. Tests cover netsh output parsing with prefix filtering, deletion of dynamically discovered rules, and fallback to fixed-list when subprocess raises. Key decision: reworked fallback test per review to exercise the exception-handling branch via `TimeoutExpired` side_effect instead of mocking `_list_firewall_rules_by_prefix` directly.
**Deviations:** None.

**Reviews:**

*Round 1:*
- code-reviewer: approved_with_suggestions, 3 minor → [logs/working/task-4/code-reviewer-round1.json]
- security-auditor: approved, 3 minor → [logs/working/task-4/security-auditor-round1.json]
- test-reviewer: needs_improvement, 1 major + 1 minor → [logs/working/task-4/test-reviewer-round1.json]

*Round 2 (after fixes):*
- test-reviewer: passed → [logs/working/task-4/test-reviewer-round2.json]

**Verification:**
- `pytest test/test_system_config.py -v` → 13 passed (10 existing + 3 new)
