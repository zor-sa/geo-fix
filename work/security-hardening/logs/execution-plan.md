# Execution Plan: security-hardening

## Feature
Eliminate all 12 security threats in geo-fix (2 critical, 3 high, 4 medium, 3 low).

## Waves

### Wave 1 (1 task, sequential)
| Task | Description | Teammate | Reviewers |
|------|-------------|----------|-----------|
| 1 | Per-session CA with ephemeral confdir and thumbprint-based removal | task-1-ca-session | code-reviewer, security-auditor, test-reviewer |

### Wave 2 (2 tasks, parallel)
| Task | Description | Teammate | Reviewers |
|------|-------------|----------|-----------|
| 2 | CA removal and tmpdir cleanup on all exit paths | task-2-exit-paths | code-reviewer, security-auditor |
| 3 | Safe Firefox backup with copy semantics | task-3-firefox-backup | code-reviewer, test-reviewer |

### Wave 3 (2 tasks, parallel)
| Task | Description | Teammate | Reviewers |
|------|-------------|----------|-----------|
| 4 | Watchdog subprocess with session-aware cleanup | task-4-watchdog | code-reviewer, security-auditor, test-reviewer |
| 5 | Reorder startup + port selection | task-5-startup-reorder | code-reviewer, test-reviewer |

### Wave 4 (2 tasks, parallel)
| Task | Description | Teammate | Reviewers |
|------|-------------|----------|-----------|
| 6 | DPAPI state file encryption | task-6-dpapi | code-reviewer, security-auditor, test-reviewer |
| 7 | Firewall rules with auto-detected browser paths | task-7-firewall | code-reviewer, security-auditor, test-reviewer |

### Wave 5 (3 tasks, parallel)
| Task | Description | Teammate | Reviewers |
|------|-------------|----------|-----------|
| 8 | CSP hardening — minimal nonce injection | task-8-csp | code-reviewer, security-auditor |
| 9 | SHA-256 verification in install.bat | task-9-installer | code-reviewer, security-auditor |
| 10 | Setup wizard fixes | task-10-wizard | code-reviewer, test-reviewer |

### Wave 6: Audit (3 tasks, parallel)
| Task | Description | Teammate | Reviewers |
|------|-------------|----------|-----------|
| 11 | Code Audit | auditor-code | none |
| 12 | Security Audit | auditor-security | none |
| 13 | Test Audit | auditor-tests | none |

### Wave 7: Final (1 task)
| Task | Description | Teammate | Reviewers |
|------|-------------|----------|-----------|
| 14 | QA — Pre-deploy acceptance testing | qa-final | none |

## File Ownership (no overlaps within wave)

### Wave 1
- Task 1: `src/system_config.py`, `src/main.py`

### Wave 2
- Task 2: `src/system_config.py` (exit paths), `src/main.py` (signal handlers)
- Task 3: `src/system_config.py` (Firefox functions only)
- **Conflict**: both touch system_config.py → run sequentially (task 2 first, task 3 second)

### Wave 3
- Task 4: `src/watchdog.py` (new), `src/main.py` (watchdog spawn), `build/build.py`
- Task 5: `src/main.py` (startup reorder, port selection)
- **Conflict**: both touch main.py → run sequentially (task 5 first, task 4 second)

### Wave 4
- Task 6: `src/system_config.py` (DPAPI + state)
- Task 7: `src/system_config.py` (firewall), `src/main.py` (firewall calls)
- **Conflict**: both touch system_config.py → run sequentially (task 6 first, task 7 second)

### Wave 5
- Task 8: `src/proxy_addon.py` — no conflict
- Task 9: `install.bat` — no conflict
- Task 10: `src/setup_wizard.py` — no conflict
- All 3 run in parallel

## User Checks
- After Wave 7 QA: verify test suite passes, review audit reports
