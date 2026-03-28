# geo-fix — Deployment

## Platform

- **OS**: Windows 10/11 only
- **Distribution**: Single-directory exe via PyInstaller (download and run)
- **Alternative**: `install.bat` (downloads embedded Python, no PyInstaller needed)

## Build Pipeline

### GitHub Actions — Release (`build.yml`)

- **Trigger**: push tag `v*` or manual `workflow_dispatch`
- **Runner**: `windows-latest`, Python 3.12
- **Steps**: install deps → PyInstaller → .bat launchers → tests → zip → upload artifact
- **On tag**: creates GitHub Release with `geo-fix-windows.zip`

### GitHub Actions — Tests (`test-windows.yml`)

- **Trigger**: push to `main`/`feature/*`, PRs
- **3 sequential jobs**: unit → integration → E2E (all on windows-latest)

### Local Build (`build/build.py`)

- PyInstaller `--onedir` with hidden imports for mitmproxy/pystray
- Bundles `src/inject.js` as data file
- Generates .bat launchers and desktop shortcuts

## Artifacts

- `dist/geo-fix-windows.zip` — GitHub Release asset
- Contains: `geo-fix.exe`, DLLs, `inject.js`, `.bat` launchers, `README.md`

## Runtime Files (on user's machine)

| File | Location | Purpose |
|---|---|---|
| `.geo-fix-state.json` | Next to executable | Crash recovery state |
| `.geo-fix.pid` | Next to executable | Single-instance lock |
| `.geo-fix-setup-done` | Next to executable | First-run wizard flag |
| `~/.mitmproxy/` | User home | mitmproxy CA cert + key |

## Monitoring

Not configured. No telemetry. Health checks are local only (VPN detection, port check).
