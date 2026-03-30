# Execution Plan: security-hardening-r2

**Created:** 2026-03-30

---

## Wave 1 (independent)

### Task 1: Delete CA key file after mitmproxy loads it
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Scope:** Tests only (implementation exists)
- **Teammate:** coder-ca-keys

### Task 3: Periodic VPN monitoring + watchdog health
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Scope:** Extract _monitor_loop from closure + tests
- **Teammate:** coder-vpn-monitor

### Task 4: Firewall cleanup by prefix
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Scope:** Tests only (implementation exists)
- **Teammate:** coder-firewall

## Wave 2 (depends on Wave 1)

### Task 2: Robust cleanup with retry, startup check, and fallback
- **Skill:** code-writing
- **Reviewers:** code-reviewer, security-auditor, test-reviewer
- **Scope:** Full implementation + tests
- **Depends on:** Task 3, Task 4
- **Teammate:** coder-cleanup

## User checks

- None (no verify: [user] in any task)
