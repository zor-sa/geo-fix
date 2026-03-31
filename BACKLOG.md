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

**Status:** ready — можно брать в работу
**Source:** user feedback, 2026-03-30
**Related:** security-hardening-r2 (R-4), system_config.py

**Problem:** Если geo-fix завершился аварийно (краш, убит через диспетчер задач, BSOD, отключение электричества), системные изменения остаются: прокси включён (интернет не работает), CA-сертификат в хранилище (угроза безопасности), правила файрвола (ломают видеозвонки). Пользователь не может и не должен разбираться в ручной очистке.

**Требование:** Очистка должна работать в 100% случаев, кроме форс-мажора с отключением электричества. Нельзя рассчитывать, что пользователь будет делать очистку вручную или сможет в этом разобраться.

**Scope исследования:**
1. Windows shutdown hooks — гарантированный вызов cleanup при logoff/shutdown/restart
2. Windows Service подход — сервис получает уведомления о завершении сессии
3. Watchdog как отдельный процесс — следит за основным, делает cleanup если тот умер
4. Startup task (Task Scheduler) — при входе в систему проверяет и чистит артефакты
5. Persistence через реестр Run/RunOnce — запуск очистки при следующем логине

**Decision:** Требуется code-research для выбора наиболее надёжного подхода.
