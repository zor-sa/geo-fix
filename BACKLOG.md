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
