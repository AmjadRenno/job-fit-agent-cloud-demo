# Job Fit Agent V3

Job Fit Agent V3 is a public/cloud portfolio edition of a private job-fit system. It is intentionally a safe, read-only demo: it uses synthetic candidate and job data, never submits applications, and does not contact live job sources in its default configuration.

## What it demonstrates

- Evidence-backed, persisted job-match results
- Server-side job search and facets
- Job detail, requirement, gap, and execution-trace views
- A deterministic synthetic demo seed
- A bounded, opt-in agentic-search fallback and a local read-only MCP server

The stack is React and TypeScript, FastAPI/Python, PostgreSQL, Alembic, Docker, and Nginx.

## Run locally

Copy `.env.example` to `.env`, then start PostgreSQL and run the explicit deployment steps:

```powershell
docker compose up -d postgres
docker compose run --rm backend python -m scripts.run_migrations
docker compose run --rm backend python -m scripts.seed_demo
docker compose up -d backend frontend
```

Open `http://localhost:5173`. The backend exposes `/health` and `/ready`.

To run development checks:

```powershell
pytest -q
cd frontend; npm ci; npm run build
```

## Documentation

- [Architecture](docs/architecture.md)
- [AI and MCP](docs/ai-and-mcp.md)
- [Evaluation](docs/evaluation.md)
- [Security](docs/security.md)
- [Deployment](docs/deployment.md)

## Boundaries and limitations

`APP_MODE=demo` is the safe default and is enforced by the backend. Live discovery, onboarding, application changes, cover-letter generation, OpenAI calls, and source-management writes are blocked. Agentic search is disabled by default. The demo is not a live job board, an application-submission system, or an Azure deployment claim.
