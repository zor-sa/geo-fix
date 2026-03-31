# geo-fix Backlog

## Ideas (requires user approval before starting)

### Run geo-fix as Administrator to protect CA private key

**Status:** idea — requires user approval
**Source:** analyst review discussion, 2026-03-28
**Related:** T-1 (CA cert security), security-hardening

**Problem:** During operation, CA private key sits in session tmpdir accessible to any process of the same user. Malware or other user-level processes can copy the key and perform MITM on all HTTPS traffic.

**Idea:** Run geo-fix elevated (as Administrator). Set tmpdir ACL to Administrator only — regular user processes cannot read the key.

**Pros:**
- User-level malware cannot read the CA private key
- Firewall rules (netsh) won't need a separate UAC prompt
- Stronger isolation of sensitive material

**Cons:**
- UX: UAC prompt on every launch
- mitmproxy runs as admin — larger blast radius if a vulnerability is found in mitmproxy (RCE from a malicious HTTP response would give attacker admin rights instead of user rights)
- CA cert must be installed into the regular user's CurrentUser store, not admin's — requires explicit cross-user store targeting
- Tray icon may not display correctly due to UIPI (User Interface Privilege Isolation) between admin process and user desktop

**Alternative (simpler):** Delete the key file from disk immediately after mitmproxy loads it into memory. Exposure window drops from hours to milliseconds. No elevation needed. However, the key remains in process memory — any same-user process can dump it via `procdump`, `ReadProcessMemory`, or Task Manager. This requires targeted attack (find process, dump hundreds of MB, pattern-match ASN.1/PEM in heap) — significantly harder than reading a file from %TEMP%, but possible.

**Note on blast radius (researched 2026-03-28):**
- RCE via mitmweb internal API found in versions ≤11.1.1 (fixed in 11.1.2). Does NOT affect geo-fix — we use DumpMaster, not mitmweb.
- HTTP request smuggling found in versions ≤7.0.4 (fixed in 8.0.0). Not RCE.
- Direct RCE via crafted HTTP response through DumpMaster (our scenario: user visits malicious site, site sends crafted response through proxy) — **not found publicly** as of 2026-03-28. Theoretical vector only.
- Conclusion: the "admin blast radius" con is real in principle but has no known practical exploit for DumpMaster today. Risk is low but non-zero.

**Full protection requires elevation:** Only an admin-level process is protected from memory dumps by user-level processes. Delete-after-load closes the disk vector but not the memory vector. Both approaches can be combined: elevation + delete-after-load = maximum protection.

**Decision:** Do not start without explicit user approval. If approved, evaluate both approaches (elevation vs delete-after-load) in a tech-spec.

---

### WiFi geolocation leak — роутер MAC-адрес раскрывает реальное местоположение

**Status:** done — реализовано в work/wifi-geolocation-leak (2026-03-30)
**Source:** user report, 2026-03-30
**Related:** inject.js (navigator.geolocation override), system_config.py

**Problem:** Google и другие сервисы имеют базы данных MAC-адресов WiFi точек доступа. Когда браузер или десктопное приложение запрашивает геолокацию:

1. `navigator.geolocation.getCurrentPosition()` → браузер вызывает Windows Location Services
2. Windows сканирует ближайшие WiFi точки доступа
3. MAC-адреса отправляются в Google Location Service (или Microsoft Location Service)
4. Сервис возвращает координаты, вычисленные по базе MAC-адресов
5. Роутер пользователя по своему MAC-адресу может быть идентифицирован как находящийся в России — **независимо от VPN/IP**

Два вектора утечки:
- **Браузер:** `navigator.geolocation` → geo-fix уже перехватывает через inject.js, но только для target domains. Нетаргетные домены или расширения могут получить реальные координаты.
- **Десктопное приложение:** обращается к Windows Location Services напрямую, минуя браузер и прокси. geo-fix не контролирует этот канал.

**Scope исследования:**
1. Проверить: inject.js действительно блокирует реальный geolocation на target domains? Или просто добавляет fake coords, а реальные всё равно утекают в запросе к Google?
2. Изучить: можно ли отключить Windows Location Services / WiFi scanning на уровне системы (реестр, GPO, netsh wlan)?
3. Изучить: можно ли рандомизировать MAC роутера (настройка роутера, не geo-fix)?
4. Оценить: нужно ли расширять inject.js на все домены (не только target)?
5. Оценить: нужно ли блокировать запросы к `*.googleapis.com/geolocation/*` на уровне прокси?

**Mitigation options (предварительные):**
- Блокировать/подменять запросы к Google Geolocation API (`https://www.googleapis.com/geolocation/v1/geolocate`) на уровне прокси
- Отключить Windows Location Service через реестр (`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager`)
- Расширить navigator.geolocation override на все домены
- Документировать для пользователя: рандомизация MAC на роутере

**Decision:** Требуется code-research для оценки текущего покрытия и выбора подхода.

---

### Гарантированная очистка системных изменений при любом завершении

**Status:** done — архитектура уже реализована, добавлен SetConsoleCtrlHandler (2026-03-31)
**Source:** user feedback, 2026-03-30
**Related:** security-hardening-r2 (R-4), system_config.py, watchdog.py

**Реализованная архитектура (4 слоя):**
1. **atexit + SIGTERM** — нормальный выход и Ctrl+C
2. **SetConsoleCtrlHandler** (добавлен) — закрытие консоли, logoff, shutdown
3. **Watchdog process** — отдельный процесс, обнаруживает kill через Task Manager за 2 сек
4. **ONLOGON scheduled task** — cleanup при следующем входе (BSOD, power loss)
+ startup dirty-flag check (`cleanup_pending.json`)

**Покрытие:** все сценарии кроме power loss до flush ONLOGON task на диск (неизбежное ограничение user-space).

---

### Полная маскировка на все домены по умолчанию

**Status:** done — реализовано 2026-03-31
**Source:** user feedback, 2026-03-31
**Related:** proxy_addon.py

**Solution:** Убрана двухуровневая схема инжекции. Полный payload (timezone, language, geolocation, permissions, WebRTC) инжектится на все домены. `_build_geo_only_payload()` удалена. Restrictive CSP проверяется для всех доменов (включая target).

---

### WebRTC: relay-режим вместо полной блокировки STUN

**Status:** done — реализовано 2026-03-31
**Source:** user feedback, 2026-03-31
**Related:** inject.js (RTCPeerConnection override)

**Solution:** Заменена фильтрация STUN/TURN серверов на `iceTransportPolicy: 'relay'`. Звонки (Google Meet, Zoom, Teams, Discord) теперь работают через TURN relay. Убрано создание файрвольных правил при старте (legacy правила удаляются при cleanup). Wizard упрощён — шаг файрвола удалён.

---

### Фильтрация ICE candidates для защиты от утечки IP

**Status:** idea — requires user approval
**Source:** обсуждение альтернатив WebRTC защиты, 2026-03-31
**Related:** inject.js (RTCPeerConnection override)

**Problem:** `iceTransportPolicy: 'relay'` запрещает прямые P2P соединения, направляя весь трафик через relay (задержка). Альтернатива — позволить STUN отработать, но перехватить `onicecandidate` и отфильтровать candidates с реальным IP, пропуская VPN IP, mDNS (.local) и relay candidates.

**Плюсы:**
- Сохраняет прямые P2P соединения через VPN-интерфейс (минимальная задержка)
- Relay используется только как fallback

**Минусы:**
- Нужно передавать VPN IP в inject.js и динамически актуализировать при смене IP
- Хрупкая реализация: split-tunneling VPN, symmetric NAT, `getStats()` — дополнительные векторы утечки
- Сложнее тестировать

**Decision:** Рассмотреть после реализации relay-подхода, если задержка через relay окажется проблемой на практике.
