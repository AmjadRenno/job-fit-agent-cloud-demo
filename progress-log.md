# Progress log

## GitHub Actions/OIDC Phase 1
- OIDC authentication and Application CI Phase 2 live verification PASS.
- Backend CD attempt #1 reached immutable SHA-tagged ACR push and Container App deployment, but failed by requiring `runningState == Running` for a `minReplicas=0` app. The root cause was fixed with exact immutable-image verification plus `provisioningState == Provisioned`; `/health` and `/ready` provide runtime proof.
- Backend CD attempt #2 PASS end-to-end: Docker build/push, Container App deployment, exact revision-image verification, and health/readiness smoke checks. No ACR admin credentials or Azure client secret are used. Backend CD Phase 3 COMPLETE. Next phase: Frontend CD to Azure Static Web Apps.
- Frontend CD #1 PASS end-to-end: the production Vite build used the configured `VITE_API_URL`, then deployed prebuilt `frontend/dist` to the existing Azure Static Web App. `AZURE_STATIC_WEB_APPS_API_TOKEN` remains a GitHub secret; `VITE_API_URL` is a non-secret repository variable.
- Live browser smoke test PASS: the production frontend communicates with the Azure backend; an approximately 10-second scale-to-zero cold start showed the loading/skeleton UX correctly. Frontend CD Phase 4 COMPLETE. CI, Backend CD, and Frontend CD are all live-verified. Next phase: CI/CD orchestration hardening—deploy only after successful CI, add path-aware behavior/concurrency, and avoid unnecessary production deployments.

## Azure PostgreSQL connection-pool fix

- Diagnosed readiness/API failures as per-request Engine/QueuePool creation; added one lazy shared Engine/session factory, bounded PostgreSQL pooling, shutdown disposal, and request/readiness lifecycle regression coverage. Verification: 307 passed, 1 skipped.

## First live Azure deployment

- Deployed the read-only synthetic V3 demo to Azure Static Web Apps, Container Apps, and private PostgreSQL; explicit migration and idempotent seed jobs succeeded, and live acceptance verification passed.
- Added a shell-first cold-start state with a 35-second timeout and Retry for the scale-to-zero public demo.

## Azure migration bugfix

- Escaped percent signs only at the Alembic ConfigParser boundary so URL-encoded PostgreSQL passwords retain their SQLAlchemy semantics; added a focused regression test.

## Phase 7.4 — Pre-Azure security and release gate

- Audited the tracked V3 baseline, history, demo write boundary, AI/MCP, HTTP/frontend/container configuration, dependencies, and publication rights. Added exhaustive demo-write regression coverage; a clean base-image rebuild removed all Criticals, with only documented no-fix base-image High findings remaining.

## Phase 7.3 — Repository structure and documentation cleanup

- Consolidated public documentation into architecture, AI/MCP, evaluation, security, and deployment guides; refreshed the public README, removed unreachable demo UI paths, and retained only reproducible local artifacts for cleanup. No runtime architecture or private data changed.

## Phase 7.2 — Final public-demo consistency cleanup

- Replaced unavailable workflow copy and dashboard application-state cards with demo-safe read projections; persisted matches and decisions remain unchanged.

## Phase 7.1 — Portfolio demo UX cleanup

- Made read-only demo boundaries intentional in the UI, added browser-local stars, and simplified synthetic source/profile and job-detail presentation.

## Phase 7 — Production and container hardening

- Separated migrations from web startup, added non-root container/runtime safeguards, readiness, explicit CORS configuration, synthetic seed protection, and production-readiness documentation.

## Phase 7 closure — Container verification PASS

- Clean Docker build and the explicit PostgreSQL → migrate → synthetic seed → web workflow passed. PostgreSQL seeding exposed and fixed a missing flush before persisted analysis creation; the second seed was idempotent. Health, readiness, search/facets, non-root runtime, image exclusions, disabled agentic search, and MCP import were verified before clean shutdown.

## Phase 6 — Agentic search evaluation

- Added a V3-only synthetic 12-case dataset, fake-client offline contract metrics, bounded opt-in live-evaluation support, and concise evaluation documentation. Live AI quality remains unmeasured.

## Phase 5 — Bounded agentic search fallback

- Added deterministic-first, explicit, read-only AI fallback with an opt-in demo-only flag, persisted read-tool results, bounded trace, and focused tests. No mutation or general OpenAI bypass was added.

## Phase 1 — Clean V3 baseline export

- Created a new Git repository with no imported history or remote.
- Exported reviewed reusable code, frontend, migrations, generic tests, safe docs, and runtime templates from private V2.
- Excluded private candidate/application/operational data, secrets, local volumes, historical evaluation artifacts, source drafts, and personal notes.
- Added only a clearly marked synthetic demo candidate profile for local runtime and test compatibility. No Hesehus-specific features were added.
- Verification: 264 passed, 1 skipped; frontend production build, import smoke, Docker Compose config, and `git diff --check` passed. No V2 Git history or remote was imported.

## Phase 2 — Public demo foundation

- Added backend-enforced `APP_MODE=demo` read-only mode, idempotent synthetic seed data, public demo indicator, and no-secret demo read boundary. Live discovery, daily runs, OpenAI, cover letters, application/source mutations, and onboarding are blocked with `DEMO_MODE_READ_ONLY`.
- Verification: synthetic seed created six jobs/analyses/matches on first run and zero duplicates on the second run; full test/build verification is recorded with this phase.

## Phase 3 — Search + facets

- Added deterministic persisted-job search and contextual facet counts through the read-only `/api/search/jobs` boundary. No LLM, external search provider, schema change, or demo-mode weakening was introduced.
- Wired the Jobs page to backend search/facet data with text search, company/location/technology/decision/confidence controls, active-filter feedback, reset, and empty state; the existing detail drawer remains read-only in demo mode.

## Phase 4 — Read-only MCP adapter

- Added a local stdio MCP adapter with exactly four bounded read-only synthetic-data tools backed by existing search, dashboard, and evidence services.
