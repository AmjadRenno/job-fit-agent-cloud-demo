# Architecture

Job Fit Agent V3 is a public portfolio demo derived from a private baseline. Its runtime has no private candidate, CV, application-history, or source-acquisition data.

```text
React/TypeScript UI -> FastAPI API -> PostgreSQL
                         |          ^
                    job_fit_core    | Alembic + synthetic demo seed
```

The frontend reads persisted dashboard and search read models. `GET /api/search/jobs` performs server-side text search across stored title, company, location, description, requirements, and gaps; its company, location, technology, decision, and confidence facets also come from the server. React does not re-score or duplicate filters.

![Jobs view with server-provided search filters and facets over synthetic results](assets/screenshots/jobs-filters.png)

*Search and facet controls use persisted server read models; the browser does not recalculate scores or filters.*

The Python application keeps domain matching in `job_fit_core`, with persisted jobs, analyses, matches, evidence, sources, and run traces. `scripts.seed_demo` creates six clearly synthetic records and their validated persisted match outputs. It is idempotent.

The public UI is read-only. Local browser stars are a convenience-only UI preference, not an application state change. Existing application and source lifecycle models remain part of the reusable architecture, but demo mode blocks their write paths.

Database migrations are an explicit deployment step; the backend container does not own migration execution.

## Azure runtime and CI/CD delivery

The application diagram above describes responsibilities inside the product. The following diagram is intentionally separate: it describes the deployed Azure topology and how a tested commit reaches that runtime without mixing in domain, matching, source, or MCP internals.

```mermaid
flowchart TB
  subgraph runtime["A. Production Runtime"]
    direction LR
    browser["Browser / User"]
    swa["Azure Static Web Apps<br/>React + Vite frontend"]
    aca["Azure Container Apps<br/>FastAPI backend<br/>APP_MODE=demo"]
    postgres["Azure Database for PostgreSQL"]
    acr["Azure Container Registry"]

    browser -->|"Load React/Vite application"| swa
    browser -->|"HTTPS API requests"| aca
    aca -->|"Persisted demo jobs, analyses,<br/>matches and traces"| postgres
    acr -->|"Pull immutable backend image<br/>via managed identity"| aca
  end

  subgraph delivery["B. GitHub CI/CD Delivery"]
    direction TB
    repo["GitHub repository"]

    subgraph application_ci["Application CI"]
      direction LR
      paths["Path-aware change detection<br/>push to main only"]
      backend_tests["Backend tests<br/>pytest -q"]
      frontend_build["Frontend production build<br/>npm ci + npm run build"]
    end

    ci_success["Successful backend and frontend CI"]
    backend_gate["Backend-relevant changes"]
    frontend_gate["Frontend-only changes"]
    combined_gate["Combined backend + frontend changes"]
    backend_cd["Backend CD"]
    frontend_cd["Frontend CD"]
    manual["Manual workflow_dispatch fallback"]
    oidc["GitHub OIDC / Azure<br/>user-assigned managed identity"]
    image["Immutable backend Docker image<br/>jobfit-backend:&lt;tested-sha&gt;"]

    repo -->|"PR or push to main"| backend_tests
    repo -->|"PR or push to main"| frontend_build
    repo -->|"Push to main"| paths
    backend_tests --> ci_success
    frontend_build --> ci_success

    paths -->|"Backend paths"| backend_gate
    paths -->|"Frontend paths, no backend paths"| frontend_gate
    paths -->|"Both path groups"| combined_gate
    ci_success -->|"Required before deployment"| backend_gate
    ci_success -->|"Required before deployment"| frontend_gate
    ci_success -->|"Required before deployment"| combined_gate

    backend_gate --> backend_cd
    frontend_gate --> frontend_cd
    combined_gate --> backend_cd
    backend_cd -.->|"Combined changes only:<br/>backend must succeed first"| frontend_cd
    manual -.-> backend_cd
    manual -.-> frontend_cd

    backend_cd -->|"Federated authentication"| oidc
    backend_cd -->|"Build exact tested commit"| image
  end

  oidc -->|"Authorize ACR push and<br/>Container App update"| acr
  image -->|"Push immutable SHA tag"| acr
  backend_cd -->|"Update exact image; verify revision,<br/>/health and /ready"| aca
  frontend_cd -->|"Deploy prebuilt frontend/dist<br/>using SWA deployment token"| swa
```

Pull requests run validation only. For a push to `main`, path detection runs alongside backend and frontend validation inside the Application CI workflow; production jobs remain gated on both validation jobs succeeding. The workflows pass the exact tested commit SHA into component deployment. Component-specific concurrency prevents deployment races, and running deployments are not cancelled.
