# Decisions Log: security-hardening-r2

Agent reports on completed tasks. Each entry is written by the agent that executed the task.

---

<!-- Entries are added by agents as tasks are completed. -->

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
