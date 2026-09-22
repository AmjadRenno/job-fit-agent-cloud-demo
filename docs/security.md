# Security and demo boundary

## Public demo mode

`APP_MODE=demo` is the default public-demo boundary and is enforced in FastAPI middleware before write handlers run. It allows health/readiness, dashboard/search/detail/evidence/run-trace/source-health reads. All synthetic demo records and candidate-evidence IDs are intentionally fictional.

The following operations return the stable `DEMO_MODE_READ_ONLY` error in demo mode:

- live source discovery and onboarding fetches
- daily external execution
- OpenAI analysis and cover-letter generation
- application mutations
- source creation, update, archive, and approval
- external communication

The only narrow exception is the optional agentic-search endpoint: it is read-only, bounded, disabled by default, and requires explicit demo-mode enablement plus a server-side OpenAI key. It cannot discover sources, alter records, or invoke arbitrary tools.

## Data and secret handling

The public repository contains only synthetic candidate, job, evidence, and evaluation data. It excludes real profiles, CV text, contact details, application history, private notes, run artifacts, local volumes, `.env` files, and tokens. `.env.example` contains placeholders only.

`data/candidate/profile.md` is a tracked synthetic fixture. It is intentionally included in the backend build context so the public demo can run independently; local Docker Compose additionally mounts the same directory read-only at the contract path. It does not describe a real person.

No secret belongs in `VITE_*`; frontend read access is not protected by a client-side secret. Static Web Apps deployment uses a GitHub secret, while the public frontend API URL is a non-secret GitHub repository variable.

## Runtime controls

- CORS accepts explicit origins only; the deployed backend allows the exact Static Web Apps origin rather than a wildcard.
- Container Apps stores runtime values as secrets. PostgreSQL is privately networked.
- The backend image runs as a non-root user. Runtime images exclude `.env` files, local databases, tests, evaluation data, caches, and frontend build tooling.
- The Container App pulls images from ACR through its managed identity; ACR admin credentials are not used at runtime.
- Request models reject unknown fields. Security headers are applied to API responses. Execution traces sanitize query strings and sensitive-looking error fragments.

## API exposure

The application does not disable FastAPI's standard OpenAPI endpoints. `/openapi.json`, `/docs`, and `/redoc` may be reachable wherever the backend ingress is reachable. They describe public read and blocked routes and do not contain runtime secrets. This exposure should be reconsidered if the deployment's threat model changes.

## Known limitations

The repository documents security controls and tested boundaries, not an unconditional security guarantee. Dependency and base-image vulnerability posture should be re-scanned before releases. Rate/abuse controls and production observability with redaction remain future operational work.

No binary documents, database exports, screenshots, or third-party image/font assets are tracked. Public source-adapter code contains authored integration logic and public URL patterns only; it does not carry copied source-page dumps or credentials.
