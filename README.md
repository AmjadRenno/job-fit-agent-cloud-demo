# Job Fit Agent

Job Fit Agent turns a job posting into a reviewable fit assessment: structured requirements, evidence-backed matches, explicit gaps, confidence, and a recommendation. It is designed to keep consequential decisions with a person.

**Deployment status:** Previously deployed and verified on Microsoft Azure (September 2026). The Azure resources were intentionally decommissioned in October 2026 to avoid ongoing cloud charges. **No live hosted demo is currently available.** The application can be run locally using the instructions below.

> The former public Azure demo was read-only and used synthetic candidate, job, and evidence data only. It did not discover live jobs, submit applications, or contact external sources. [View the Azure deployment and delivery documentation](docs/deployment.md).

## What it demonstrates

- A source architecture for allowlisted careers pages: contracts, robots enforcement, rate limits, normalization, deduplication, source health, and human approval gates.
- Structured LLM job analysis in the mutable workflow architecture, followed by deterministic, evidence-backed matching.
- Persisted score, confidence, recommendation, requirement classifications, gaps, and trace data exposed through a React dashboard.
- Server-side job search and facets; the browser never recalculates match scores or filters independently.
- A bounded, opt-in agentic-search fallback that can only refine a zero-result persisted search. It is not an autonomous agent and is disabled by default.
- A local stdio MCP server with four read-only tools for persisted job search, detail, match analysis, and candidate evidence.
- Human-controlled application and grounded cover-letter domain workflows, which are intentionally disabled in the public demo.

## Product preview

![Synthetic dashboard showing ranked job matches, confidence, and recommendations](docs/assets/screenshots/dashboard.png)

*Synthetic dashboard: persisted, evidence-backed job-fit results for human review.*

![Synthetic job detail showing requirements, evidence, and identified gaps](docs/assets/screenshots/job-detail.png)

*Job detail: explainable requirements, supporting evidence, confidence, and explicit gaps.*

## Architecture

```text
React + TypeScript UI -> FastAPI API -> PostgreSQL
                            |
                     job_fit_core matching
```

The local demo seeds six synthetic jobs with validated persisted analyses and matches. The UI reads those records through FastAPI; matching and evidence remain server-side.

The application architecture above stays separate from the production runtime and delivery path:

```mermaid
flowchart LR
  subgraph runtime["Production Runtime"]
    browser["Browser / User"]
    swa["Azure Static Web Apps<br/>React + Vite"]
    aca["Azure Container Apps<br/>FastAPI · APP_MODE=demo"]
    postgres["Azure Database<br/>for PostgreSQL"]
    acr["Azure Container Registry"]

    browser -->|"Load frontend"| swa
    browser -->|"HTTPS API requests"| aca
    aca -->|"Persisted demo data"| postgres
    acr -->|"Pull immutable image via managed identity"| aca
  end

  subgraph delivery["GitHub CI/CD Delivery"]
    repo["GitHub repository"]
    ci["Application CI<br/>backend tests + frontend build"]
    paths["Path detection<br/>push to main only"]
    backend_cd["Backend CD<br/>tested SHA image"]
    frontend_cd["Frontend CD<br/>prebuilt frontend/dist"]
    oidc["GitHub OIDC / Azure<br/>user-assigned managed identity"]

    repo -->|"PR or push to main"| ci
    repo -->|"Push to main"| paths
    ci -->|"Successful CI required"| backend_cd
    ci -->|"Successful CI required"| frontend_cd
    paths -->|"Backend changes"| backend_cd
    paths -->|"Frontend changes"| frontend_cd
    backend_cd -.->|"Combined changes: backend succeeds first"| frontend_cd
    backend_cd -->|"Federated authentication"| oidc
  end

  oidc -->|"Authorize ACR push"| acr
  oidc -->|"Authorize Container App update"| aca
  backend_cd -->|"Build/push tested SHA image"| acr
  backend_cd -->|"Update and verify"| aca
  frontend_cd -->|"SWA deployment token"| swa
```

See [Architecture](docs/architecture.md) for the complete topology and [Deployment](docs/deployment.md) for operational details.

## Safety and human control

`APP_MODE=demo` is the public default and is enforced by the backend, not just the UI. It permits health, readiness, dashboard, search, details, evidence, source-health, and trace reads. It blocks live discovery, onboarding fetches, daily external runs, ordinary OpenAI calls, cover-letter generation, application/source mutations, and external communication.

The architecture includes source processing, application state, and cover-letter services for controlled non-demo operation. Their presence does not mean the public demo performs those actions. See [Security and demo boundary](docs/security.md).

## AI, MCP, and evaluation

Normal search and matching are deterministic over persisted data. The optional agentic-search fallback is narrowly bounded to query refinement and a persisted read tool; it cannot invent entities, mutate records, discover sources, or communicate externally. The MCP server is local stdio only and exposes no write tools. See [AI and MCP](docs/ai-and-mcp.md).

The repository includes a synthetic, versioned 12-case evaluation suite for agentic-search safety and retrieval contracts, exercised offline with fake providers by default. The suite does not claim live model-quality measurement. See [Evaluation](docs/evaluation.md).

## Local quick start

Prerequisites: Docker Desktop, Python 3.13, and Node.js 22.

```powershell
Copy-Item .env.example .env
python -m pip install -r requirements.txt -r requirements-dev.txt
Push-Location frontend
npm ci
Pop-Location

docker compose up -d postgres
docker compose run --rm backend python -m scripts.run_migrations
docker compose run --rm backend python -m scripts.seed_demo
docker compose up -d backend frontend
```

Open `http://localhost:5173`. The backend exposes `GET /health` and `GET /ready` on `http://localhost:8000`.

Run verification from the repository root:

```powershell
pytest -q
Push-Location frontend
npm run build
Pop-Location
```

## Delivery

Pull requests and pushes to `main` run backend tests and a frontend production build. **Automatic Azure deployments are disabled** because the cloud resources were decommissioned. Historical Azure deployment workflows remain in the repository for portfolio review; they cannot successfully deploy without recreating the Azure infrastructure and reconfiguring credentials. See [Deployment](docs/deployment.md) and [successful historical backend](https://github.com/AmjadRenno/job-fit-agent-cloud-demo/actions/runs/35699258929) and [frontend](https://github.com/AmjadRenno/job-fit-agent-cloud-demo/actions/runs/35700203603) deployment runs.

## Documentation

- [Architecture](docs/architecture.md)
- [AI and MCP](docs/ai-and-mcp.md)
- [Evaluation](docs/evaluation.md)
- [Security and demo boundary](docs/security.md)
- [Deployment](docs/deployment.md)
- [Engineering highlights](progress-log.md)

## License

This project is licensed under the [MIT License](LICENSE).
