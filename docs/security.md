# Security and demo boundary

`APP_MODE=demo` is the default public-demo boundary and is enforced in the backend. It allows health/readiness, dashboard/search/detail/evidence/run-trace/source-health reads, and blocks with the stable `DEMO_MODE_READ_ONLY` error:

- live source discovery and onboarding fetches
- daily external execution
- OpenAI calls and cover-letter generation
- application mutations
- source creation, update, archive, and approval
- external communication

Demo records and evidence IDs are synthetic only. The repository excludes real profiles, CV text, contact details, application history, private notes, run artifacts, local volumes, `.env` files, and tokens. No secret belongs in `VITE_*`; frontend read access is not protected by a client-side secret.

The UI hides disabled controls for clarity, but the API is authoritative. CORS and environment configuration remain deployment concerns. A human must make any real-world decision or action outside this demo.
