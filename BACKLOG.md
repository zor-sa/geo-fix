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

**Alternative (simpler):** Delete the key file from disk immediately after mitmproxy loads it into memory. Exposure window drops from hours to milliseconds. No elevation needed.

**Decision:** Do not start without explicit user approval. If approved, evaluate both approaches (elevation vs delete-after-load) in a tech-spec.
