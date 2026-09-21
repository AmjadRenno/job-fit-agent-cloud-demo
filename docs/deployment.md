# Deployment

The Docker workflow keeps runtime responsibilities explicit:

1. Start PostgreSQL and wait for its health check.
2. Run `python -m scripts.run_migrations` as a one-shot backend command.
3. Run `python -m scripts.seed_demo`; run it again safely when needed.
4. Start backend and frontend.

The backend runs as a non-root user and exposes `/health` and `/ready`. Its image contains runtime dependencies only. The frontend uses a multi-stage build and serves built static assets through Nginx. Docker ignore rules exclude `.env` files, caches, local data, tests, evaluation artifacts, and frontend build inputs not needed at runtime.

Keep `APP_MODE=demo` and agentic search disabled unless a deliberate, separately reviewed environment configuration changes them. Azure and CI/CD are not part of this baseline.
