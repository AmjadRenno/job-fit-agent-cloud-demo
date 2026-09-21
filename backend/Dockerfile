# Job Fit Agent V3 - backend image (FastAPI + alembic)
FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && addgroup --system app \
    && adduser --system --ingroup app app

COPY backend ./backend
COPY job_fit_core ./job_fit_core
COPY scripts ./scripts
COPY alembic.ini .
COPY data/candidate ./data/candidate
RUN chown -R app:app /app

USER app

EXPOSE 8000

# Migrations are an explicit deployment operation (`python -m scripts.run_migrations`).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
