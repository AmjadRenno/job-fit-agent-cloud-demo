from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .mode import DEMO_MODE_READ_ONLY, is_demo_mode
from .config import cors_origins
from .db.session import dispose_engine, get_engine
from sqlalchemy import text

load_dotenv()

from .dashboard.api import router as dashboard_router
from .applications.api import router as applications_router
from .cover_letters.api import router as cover_letters_router
from .sources_management.api import router as sources_router
from .workflow.api import router as workflow_router
from .profile_api import router as profile_router
from .runs_api import router as runs_router, scheduler_loop
from .search_api import router as search_router
from .agentic_search_api import router as agentic_search_router
import asyncio

app = FastAPI(title="Job Fit Agent Dashboard API")

@app.middleware("http")
async def demo_read_only_boundary(request, call_next):
    # This POST is a read-only query; its service is independently bounded.
    if is_demo_mode() and request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path != "/api/search/agentic":
        return JSONResponse(status_code=403, content={"detail": DEMO_MODE_READ_ONLY})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(dashboard_router)
app.include_router(applications_router)
app.include_router(cover_letters_router)
app.include_router(sources_router)
app.include_router(workflow_router)
app.include_router(profile_router)
app.include_router(runs_router)
app.include_router(search_router)
app.include_router(agentic_search_router)


@app.on_event("startup")
async def start_scheduler() -> None:
    if not is_demo_mode():
        asyncio.create_task(scheduler_loop())


@app.on_event("shutdown")
async def close_database_pool() -> None:
    dispose_engine()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    """Readiness only checks the required database; it never calls external services."""
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as error:
        raise HTTPException(status_code=503, detail="database unavailable") from error
    return {"status": "ready"}
