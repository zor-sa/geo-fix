# Execution Plan: Resource Optimization

**Created:** 2026-03-29

---

## Wave 1 (independent)

### Task 1: Replace DumpMaster with minimal Master
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Verify-smoke:** `python -c "from mitmproxy.master import Master; from mitmproxy.options import Options; print('Master import OK')"` → no error
- **Files:** `src/main.py`

### Task 2: Add FlowCleanup addon
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Files:** `src/proxy_addon.py`

## Wave 2 (depends on Wave 1)

### Task 3: RAM monitoring + proxy auto-restart + FlowCleanup registration
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Files:** `src/main.py`, `src/proxy_addon.py`

## Wave 3 (depends on Wave 2)

### Task 4: Minor CPU optimizations
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Files:** `src/proxy_addon.py`, `src/presets.py`

## Wave 4 (depends on Wave 3)

### Task 5: Integration testing of optimized proxy
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Verify-smoke:** `python -c "import test.test_integration_proxy; print('Integration test module imports OK')"` → no error
- **Files:** `test/test_integration_proxy.py` (new), `test/test_integration_windows.py`

## Wave 5 — Audit (depends on Wave 4)

### Task 6: Code Audit
- **Skill:** code-reviewing
- **Reviewers:** none (auditor IS the review)
- **Output:** `logs/working/task-6/code-audit.md`

### Task 7: Security Audit
- **Skill:** security-auditor
- **Reviewers:** none (auditor IS the review)
- **Output:** `logs/working/task-7/security-auditor-1.json`

### Task 8: Test Audit
- **Skill:** test-master
- **Reviewers:** none (auditor IS the review)
- **Output:** `logs/working/task-8/test-audit.md`

## Wave 6 — Final (depends on Wave 5)

### Task 9: Pre-deploy QA
- **Skill:** pre-deploy-qa
- **Reviewers:** none
- **Output:** `logs/working/task-9/qa-report.md`

## User checks

- [ ] Run geo-fix US, open 15+ Google tabs, work 2+ hours — RAM in Task Manager should be stable ~100-150MB
- [ ] Open Google Docs and edit for 30+ minutes — WebSocket connections should not inflate RAM
- [ ] Verify all features work: country switching, tray menu, --stop, restart after crash
