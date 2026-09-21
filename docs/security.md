# Security and demo boundary

`APP_MODE=demo` is the default public-demo boundary and is enforced in the backend. It allows health/readiness, dashboard/search/detail/evidence/run-trace/source-health reads, and blocks with the stable `DEMO_MODE_READ_ONLY` error:

- live source discovery and onboarding fetches
- daily external execution
- OpenAI calls and cover-letter generation
- application mutations
- source creation, update, archive, and approval
- external communication

Demo records and evidence IDs are synthetic only. The repository excludes real profiles, CV text, contact details, application history, private notes, run artifacts, local volumes, `.env` files, and tokens. No secret belongs in `VITE_*`; frontend read access is not protected by a client-side secret.

The UI hides disabled controls for clarity, but the API is authoritative. A human must make any real-world decision or action outside this demo. The live Azure deployment uses an exact Static Web Apps CORS origin (never a wildcard), stores runtime values as Container Apps secrets, and keeps PostgreSQL on private VNet integration. The backend image is pulled from ACR through a user-assigned managed identity with `AcrPull`; registry admin credentials are not used at runtime.

## Pre-Azure release gate

### Controls verified

- The tracked tree and its single V3 baseline commit contain no `.env`, private key, database dump, generated artifact, real contact detail, or high-confidence credential. `.env` is ignored and `.env.example` contains placeholders only.
- Demo middleware blocks every public write route before validation or handler execution. Read routes remain available; the sole `POST /api/search/agentic` exception is bounded, read-only, and disabled unless demo mode, feature flag, and a backend-only key are all present.
- Agentic search can return only persisted job IDs, has bounded query/result/tool-call/timeout behavior, and returns no provider chain-of-thought. The local MCP server exposes exactly four read-only stdio tools.
- CORS requires explicit origins; security headers are present; health/readiness return minimal status; request models reject unknown fields; the frontend has no VITE secret, inline HTML injection, external runtime script, or production source map. Browser-local stars are non-sensitive.
- Runtime images exclude secrets, tests, evaluation data, caches, and frontend build tooling. The backend runs as non-root; Compose has no privileged service, Docker socket, or host filesystem mount. The candidate fixture is a read-only synthetic bind mount.

### Accepted limitations and required follow-up

- FastAPI OpenAPI/docs are exposed locally. They describe the read and blocked routes but do not reveal credentials; keep this decision under review when adding an internet-facing ingress.
- A clean `--pull --no-cache` rebuild removed all backend Critical findings (before: 5 Critical / 13 High; after: 0 Critical / 4 High) without a Dockerfile or dependency change. Scout's remaining `msgpack` 1.1.2 and `setuptools` 70.3.0 records are stale SBOM-layer entries: neither package exists in the final runtime filesystem, and an isolated `pip-audit -r requirements.txt` found no known vulnerabilities. The remaining backend High is `CVE-2026-85091` in base-image `zlib`, for which Scout reports no fix.
- The freshly rebuilt frontend remains at 0 Critical / 1 High: `CVE-2026-86140` in Alpine `libxml2`, for which Scout reports no fix. Its runtime is Nginx plus static assets only; the application does not parse untrusted XML. These two no-fix base-image findings are accepted residual risk, subject to rescan on every base-image rebuild.
- Gitleaks scanned the current tree and all reachable history (one V3 commit) with no leaks found.
- Azure ingress provides HTTPS/TLS; PostgreSQL has private network exposure and runtime values are stored as managed Container Apps secrets. Rate/abuse controls and observability that redacts sensitive values remain future work.
- A Static Web Apps deployment token exposed during troubleshooting was immediately rotated. No token, connection string, or secret is stored in this repository.

### Publication rights review

The synthetic profile, demo seed, evaluation cases, original application code, and factual company references are safe/informational. Manual review of `backend/app/sources/` and the named source-adapter tests found authored parsing/integration logic, public URL patterns, and short synthetic fixtures only—no copied company source code, page dump, confidential/internal material, credential, logo, or substantial job-ad text. No binary documents, database exports, screenshots, or third-party image/font assets were found; the only public asset is the project SVG favicon. A project-level license decision is still required before public release.
