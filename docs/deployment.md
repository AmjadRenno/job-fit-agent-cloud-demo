# Deployment

Job Fit Agent V3 has a live public-demo deployment:

```text
Browser -> Azure Static Web Apps -> Azure Container Apps API -> Azure Database for PostgreSQL
```

The frontend is hosted by Azure Static Web Apps. The FastAPI backend runs in Azure Container Apps in North Europe, pulling its image from Azure Container Registry with a user-assigned managed identity and `AcrPull`. PostgreSQL Flexible Server uses private VNet integration on a separate delegated subnet. The frontend is currently in East US 2 because the intended West Europe Static Web Apps region was not eligible for this subscription. A resource-group budget is configured.

## Manual deployment workflow

Images are built locally with Docker and pushed manually to ACR; ACR Tasks were unavailable for this subscription. The web application never runs migrations on startup. Operators run two explicit Container Apps Jobs in order:

1. Run Alembic migrations.
2. Run the idempotent synthetic demo seed.
3. Deploy or update the backend revision and Static Web Apps frontend.

The initial PostgreSQL setup allow-listed `pgcrypto` through `azure.extensions` before the first migration. Azure administration used Cloud Shell because local CLI sign-in was blocked by Conditional Access on an unregistered device. No identity/security policy was weakened.

The currently deployed demo is in `APP_MODE=demo` with agentic search disabled. It serves persisted synthetic data only; health/readiness, dashboard, search/facets, job detail, evidence, and browser-local stars have been manually verified. It is not a live discovery or application-submission system.

Container Apps secrets hold runtime configuration. Static Web Apps is configured with the exact frontend origin for backend CORS. IaC, CI/CD, OIDC deployment, monitoring, custom domains, and automated deployment jobs are not implemented yet.
