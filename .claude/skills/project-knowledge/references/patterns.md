# geo-fix — Patterns & Conventions

## Git Workflow

- **Main branch**: `main`
- **Feature branches**: `feature/{name}`
- **Commit style**: `type(scope): description` — e.g., `feat(geo-fix): add CA cert cleanup`

## Code Conventions

- Python 3.12, no type stubs
- `@dataclass(frozen=True)` for immutable config (presets, state)
- Thread safety via `threading.Lock` for shared mutable state
- Constants as module-level UPPER_CASE
- Windows-specific code guarded by `sys.platform` checks or `try/except ImportError`

## Testing

- pytest as test runner
- Unit tests: monkeypatch for filesystem/registry isolation
- Integration tests: real mitmproxy, real registry (Windows-only, `skipif`)
- E2E tests: Playwright + Chromium through proxy (optional, `importorskip`)
- CI: 3 tiers sequentially on `windows-latest`

## Business Rules

- Accept-Language rewriting applies to ALL domains
- JS injection applies only to TARGET_DOMAINS (Google properties)
- CSP headers modified only when JS is injected (nonce-based)
- VPN detection is advisory — warns but does not block startup
- Single instance enforced via PID file lock
