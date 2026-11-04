## feat: phase 0 foundation for Property Focus

### Why
Phase 0 builds the foundation that every later phase uses. It adds sign-in, per-account isolation, the background-work engine, telemetry, the web shell, the MCP endpoint, CI, and a dogfood deployment that runs behind Cloudflare. It ships no real-estate features yet. The confirmed plan is `specs/property-focus/master-plan.md`. The decision register is `specs/property-focus/architecture-decisions.md`: D1–D56 plus amendments A1–A6.

### What
- **Monorepo:** `backend/` (Python 3.13, uv, FastAPI), `frontend/` (React and Vite, with an RTK Query client generated from OpenAPI), `infrastructure/` (CDK, Docker, dogfood, Keycloak realm), plus fixtures, ontology, docs and specs.
- **Identity:** a self-hosted Keycloak realm kept as code (A4), with PKCE, verified-email sign-up, a password policy, and dynamic client registration with SSRF protections. Tokens are checked with PyJWT's `PyJWKClient` plus a small refresh guard (A6). REST and MCP accept separate audiences: the web app's tokens carry `property-focus-api`, while dynamically registered MCP clients' tokens carry `property-focus-mcp`. Each surface refuses the other's tokens. A Cognito CDK stack is kept for phase 9.
- **Accounts:** an account is created on first sign-in, through a pinned `SECURITY DEFINER` function. Postgres row-level security isolates each account at the database level, including the users, tenants and event tables.
- **MCP:** the official MCP Python SDK (`mcp` 2.3) low-level `Server`, stateless, serving both protocol eras (2025-03-26 through 2026-07-28). It uses the shared token check and calls tools in-process (D29).
- **Background work:** on-demand `run-task` processes, a transactional outbox, fenced leases, idempotent admission with 24-hour key expiry, and a scheduler. There are no always-on workers (D26). Handlers run outside any transaction and commit staged writes in one short transaction. The outbox claims, launches and marks published without holding locks across the launch.
- **Telemetry:** OpenTelemetry and JSON logs with redaction, which runs in linear time.
- **Dogfood:** Compose, Caddy and a deploy script that switches releases atomically. If a deploy fails its health checks, it rolls back to the previous release automatically, restoring the previous settings file. The new settings file and origin certificate are swapped in only after the pre-checks pass. It runs behind Cloudflare with:
  - an Origin CA certificate;
  - global Authenticated Origin Pulls;
  - a firewall that admits only Cloudflare;
  - forwarded headers trusted only from Caddy's fixed address;
  - confirmed rate limits, with Bot Fight Mode off (A5).
- **Deploy workflow:** manual, `main` only, and needs approval. It has three jobs: a build job with no secrets that uploads only the web build; a deploy job that checks out the commit itself and installs nothing; and a smoke job that holds only the smoke-test password.
- **CI:** path-aware jobs with lockfile drift checks, image builds and a secret scan. Actions are pinned by SHA.

### How
- The plan, register and tasks were revised before the code changed: Keycloak (A4), dual-era MCP, maintained libraries (A6) and edge protection (A5).
- Each task was implemented by its own coder, followed by acceptance testing, a docs pass, and two review panels on two seats each. Every fix was checked again in a scoped re-review.

### Testing
- `make verify` passes: 549 unit tests, 212 integration and contract tests against real Postgres and Keycloak, 33 web tests, 15 CDK tests plus `tsc` and synth, along with lint, types, boundaries and the credential scan.
- The owner review's 14 findings are fixed. Each fix area had a scoped re-review on two seats, and the regressions it found are fixed. The fixed-address collision on the proxy network was reproduced on Docker and its fix confirmed there.
- Gate evidence is in `specs/property-focus/gates/foundation-ready.md`, including a real-browser PKCE sign-in and sign-out.
- The locked dependencies have no known OSV advisories.

### Risk
**Level:** medium
This is a new codebase, and nothing has been deployed yet. Still pending before the Foundation gate closes:
- the macOS bootstrap;
- this PR's CI run;
- Cloudflare configuration and firewall on the host;
- the first dogfood deploy and smoke check.

The MCP surface is a Tier 1 contract, and the owner is reviewing it personally before release. This revision changes it: MCP now accepts only the `property-focus-mcp` audience. Releases from before this revision cannot be rolled back to; `deploy.sh` refuses them. Two Python dependencies are pinned narrowly: `mcp` to the 2.3 minor and `pyjwt` to `>=2.15.1`.

### Review observations (not addressed)
- Low observations from the panel reviews are recorded in `.sdlc/specs/property-focus/review.md` and carried as follow-ups, for example the JWKS slow-attempt edge case and the nftables firewall gap. The malformed key-set case is now fixed (owner review item 10).
- Background-task handlers can no longer read the database before committing. No phase 0 handler needs to; a short read-only helper is planned for the first phase that adds one.

### Related
- Plan, register, tasks and test plan: `specs/property-focus/`
- Retro: `docs/specs/202611/property-focus-phase-0.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01JA8ywWDz7BbogiKaxRWiiz
