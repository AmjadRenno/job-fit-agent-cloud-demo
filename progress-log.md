# Engineering highlights

## Public demo boundary

- Exported V3 as a separate public/cloud portfolio repository with no imported private Git history, profiles, application history, run artifacts, secrets, or local data volumes.
- Added an idempotent synthetic candidate and six synthetic jobs with persisted analyses, matches, evidence, decisions, source health, and run traces.
- Enforced `APP_MODE=demo` in the backend. The public demo is read-only and blocks live discovery, external runs, ordinary LLM use, cover-letter generation, source/application writes, and external communication.

## Product architecture

- Consolidated the React dashboard onto persisted server-side search, facets, job details, requirements, gaps, confidence, recommendation, and evidence views.
- Retained source contracts, human approval lifecycle, deterministic extraction, normalized persistence, structured analysis, deterministic evidence-backed matching, and human-controlled application/cover-letter domain workflows for non-demo operation.

## AI, MCP, and evaluation

- Added a bounded, opt-in, read-only agentic-search fallback over persisted jobs. It is deterministic-first, has strict query/result/tool-call limits, and is disabled by default.
- Added a local stdio MCP adapter with four bounded read-only tools: job search, job detail, match analysis, and candidate evidence.
- Added a synthetic 12-case offline evaluation suite for agentic-search retrieval and safety contracts; live model quality is intentionally not claimed as measured.

## Production readiness and delivery

- Separated migrations from web startup, added readiness checks, a shared bounded SQLAlchemy pool, non-root containers, explicit CORS, synthetic seeding protection, and cold-start loading UX.
- Live-verified GitHub OIDC, Application CI, immutable backend image delivery to ACR/Container Apps, revision-image verification, health/readiness smoke checks, and frontend deployment to Azure Static Web Apps.
- Added path-aware CI/CD orchestration with component concurrency and manual deployment fallback.
- Documented the public Azure runtime and CI/CD delivery architecture with reviewable Mermaid diagrams.
- Added curated public product and CI/CD screenshots to support portfolio documentation.
