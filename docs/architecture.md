# Architecture

Job Fit Agent V3 is a public portfolio demo derived from a private baseline. Its runtime has no private candidate, CV, application-history, or source-acquisition data.

```text
React/TypeScript UI -> FastAPI API -> PostgreSQL
                         |          ^
                    job_fit_core    | Alembic + synthetic demo seed
```

The frontend reads persisted dashboard and search read models. `GET /api/search/jobs` performs server-side text search across stored title, company, location, description, requirements, and gaps; its company, location, technology, decision, and confidence facets also come from the server. React does not re-score or duplicate filters.

The Python application keeps domain matching in `job_fit_core`, with persisted jobs, analyses, matches, evidence, sources, and run traces. `scripts.seed_demo` creates six clearly synthetic records and their validated persisted match outputs. It is idempotent.

The public UI is read-only. Local browser stars are a convenience-only UI preference, not an application state change. Existing application and source lifecycle models remain part of the reusable architecture, but demo mode blocks their write paths.

Database migrations are an explicit deployment step; the backend container does not own migration execution.
