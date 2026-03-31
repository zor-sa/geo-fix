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

## Cleanup & Recovery

- Cleanup steps use retry-with-delay pattern: try → fail → sleep 3s → retry once
- Failed cleanup labels persisted to `APPDATA/geo-fix/cleanup_pending.json` for startup recovery
- Firewall rules discovered dynamically by prefix (`geo-fix-webrtc*`) via netsh query, with fixed-list fallback
- Sensitive files (CA private key) deleted from disk immediately after loading into memory
- Watchdog subprocess monitored by main process — auto-respawned on death

## Business Rules

- Accept-Language rewriting applies to ALL domains
- JS injection: two-tier — full payload on TARGET_DOMAINS, geo-only on all others
- Geolocation API intercept: POST to googleapis.com/geolocation returns fake coordinates at proxy level
- CSP skip guard: pages with `script-src 'none'` or `require-trusted-types-for 'script'` skip JS injection (proxy intercept covers them)
- CSP headers modified only when JS is injected (nonce-based)
- Windows Location Services disabled via HKCU registry at startup, restored on cleanup
- VPN detection is advisory — warns but does not block startup
- Single instance enforced via PID file lock
