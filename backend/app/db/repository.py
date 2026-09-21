from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Company, Job, JobSource
from backend.app.sources_management.domain import normalize_company_domain

COUNTRY_CODES = {"Denmark": "DK"}
SOURCE_DEFAULT_COUNTRY_CODES = {"solita_dk": "DK", "vestas_dk": "DK"}


def normalize_country(value: str | None) -> str | None:
    if not value:
        return None
    return COUNTRY_CODES.get(value.strip())


def canonical_job_values(record: Mapping[str, Any]) -> dict[str, Any]:
    countries = record.get("country") or []
    if isinstance(countries, str):
        countries = [countries]
    country_code = normalize_country(countries[0] if countries else None)
    if not countries:
        country_code = SOURCE_DEFAULT_COUNTRY_CODES.get(str(record.get("source") or ""))
    source_location = record.get("location")
    availability_status = str(record.get("status") or "UNKNOWN").upper()
    meaningful_content = "\n".join(
        str(value or "").strip().casefold()
        for value in (record.get("title"), record.get("description"), source_location, country_code)
    )
    date_posted = record.get("date_posted")
    if isinstance(date_posted, str):
        try:
            date_posted = date.fromisoformat(date_posted[:10])
        except ValueError:
            date_posted = None
    language = record.get("language")
    language = str(language).strip() if language else None
    return {
        "external_job_id": record.get("external_job_id"),
        "source_job_url": record.get("url"),
        "title": record.get("title"),
        "description": record.get("description"),
        "location_raw": source_location,
        "city": None,
        "country_code": country_code,
        "is_hybrid": None,
        "is_remote": None,
        "employment_type": str(record.get("employment_type") or "UNKNOWN").upper(),
        "language": language,
        "date_posted": date_posted,
        "availability_status": availability_status,
        "content_hash": hashlib.sha256(meaningful_content.encode("utf-8")).hexdigest(),
    }


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def resolve_source(self, source_key: str) -> JobSource:
        source = self.session.scalar(select(JobSource).where(JobSource.source_id == source_key))
        if source is None:
            raise LookupError(f"No JobSource configured for {source_key!r}")
        return source

    def upsert_job(self, record: Mapping[str, Any]) -> Job:
        source_key = str(record.get("source") or "")
        source = self.resolve_source(source_key)
        values = canonical_job_values(record)
        # The source's stable identifier is the primary identity. Job-board
        # URLs can change when a title or location slug changes while still
        # referring to the same vacancy. Falling back to the URL preserves
        # compatibility with records that do not provide an external id.
        job = None
        if values["external_job_id"]:
            job = self.session.scalar(
                select(Job).where(
                    Job.source_id == source.id,
                    Job.external_job_id == values["external_job_id"],
                )
            )
        if job is None:
            job = self.session.scalar(
                select(Job).where(
                    Job.source_id == source.id,
                    Job.source_job_url == values["source_job_url"],
                )
            )
        if job is None:
            job = Job(source_id=source.id, company_id=source.company_id, **values)
            self.session.add(job)
        else:
            updated_values = dict(values)
            # Documented state machine: ACTIVE requires explicit evidence to
            # leave; it must never silently downgrade to UNKNOWN.
            if job.availability_status == "ACTIVE" and updated_values["availability_status"] == "UNKNOWN":
                updated_values["availability_status"] = "ACTIVE"
            for key, value in updated_values.items():
                setattr(job, key, value)
            # A valid update means the job was observed again this run.
            job.last_seen_at = datetime.now(timezone.utc)
        return job


def ensure_source(
    session: Session,
    *,
    source_id: str,
    display_name: str,
    official_jobs_url: str,
    allowed_domains: list[str],
    robots_txt_url: str | None = None,
    crawl_delay_seconds: int = 5,
    enabled: bool = False,
    company_name: str | None = None,
    company_domain: str | None = None,
    country: str = "Denmark",
) -> JobSource:
    source = session.scalar(select(JobSource).where(JobSource.source_id == source_id))
    company = None
    if company_name and company_domain:
        normalized_domain = normalize_company_domain(company_domain)
        company = session.scalar(select(Company).where(Company.domain == normalized_domain))
        if company is None:
            company = Company(name=company_name, domain=normalized_domain, country=country)
            session.add(company)
            session.flush()
    if source is None:
        source = JobSource(
            source_id=source_id,
            display_name=display_name,
            official_jobs_url=official_jobs_url,
            canonical_url=official_jobs_url,
            allowed_domains=allowed_domains,
            robots_txt_url=robots_txt_url,
            crawl_delay_seconds=crawl_delay_seconds,
            enabled=enabled,
            company=company,
        )
        session.add(source)
    return source
