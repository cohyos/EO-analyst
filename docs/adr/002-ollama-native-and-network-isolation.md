# ADR-002: Ollama runs natively on Windows; containers reach it via host.docker.internal

**Status:** accepted (2026-09-04) — revisit if P0.4 container measurement shows ≥ 95% VRAM parity
**Context:** 12 GB VRAM makes every hundred MB count. Research (plan §2.5) reports 5–10% less usable VRAM through
WSL2's dxcore layer, and no admin rights exist in the build session to set up a Windows firewall rule. The
`agent`/`worker` container must not have internet access (plan §2.4).

**Decision**
1. Ollama stays the native Windows service. User-scope env vars: `OLLAMA_HOST=0.0.0.0:11434` (so Docker can reach
   it), `OLLAMA_NO_CLOUD=1`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_FLASH_ATTENTION=1`,
   `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_GPU_OVERHEAD=1258291200`, `OLLAMA_KEEP_ALIVE=30m`.
2. Containers: `postgres`, `ntfy`, `web`, `agent` sit on `internal` (no route out) plus `hostlink` (a plain bridge
   used only for published localhost ports and the host gateway). `fetcher` and `searxng` are the only services on
   `egress`. `agent` additionally uses `dns: 0.0.0.0` + static `extra_hosts`, so hostname-based egress is impossible;
   IP-literal egress from `agent` is the documented residual gap, closed by the host firewall script
   `scripts/host/firewall_ollama.ps1` (user runs once, elevated) and by the fact that no code path in `agent`
   opens sockets except to `postgres`, `searxng`, `ntfy`, `host.docker.internal`.
3. A `container-ollama` compose profile is kept for Linux hosts / full portability (NFR-12).

**Consequences**
- Ollama listens on all interfaces until the firewall script is applied; on a home LAN this exposes the API to
  LAN peers only (no auth). Mitigation: run the firewall script; Tailscale ACLs unaffected.
- Model pulls need the outbound block temporarily disabled (`firewall_ollama.ps1 -Pull`).
- The resource gate reads `nvidia-smi` on the host directly (or inside a container with GPU passthrough); both work.
