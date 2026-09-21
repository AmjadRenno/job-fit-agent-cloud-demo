from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .repository import JobRepository, ensure_source

SOURCE_DEFAULTS = {
    "solita_dk": {
        "display_name": "Solita Denmark",
        "official_jobs_url": "https://www.solita.fi/join-us/?country=denmark",
        "allowed_domains": ["www.solita.fi"],
        "robots_txt_url": "https://www.solita.fi/robots.txt",
        "crawl_delay_seconds": 10,
        "enabled": True,
        "company_name": "Solita A/S",
        "company_domain": "www.solita.fi",
    },
    "vestas_dk": {
        "display_name": "Vestas Denmark",
        "official_jobs_url": "https://careers.vestas.com/search/?createNewAlert=false&q=&optionsFacetsDD_country=DK&optionsFacetsDD_department=&optionsFacetsDD_shifttype=&optionsFacetsDD_customfield1=",
        "allowed_domains": ["careers.vestas.com"],
        "robots_txt_url": "https://careers.vestas.com/robots.txt",
        "crawl_delay_seconds": 15,
        "enabled": True,
        "company_name": "Vestas",
        "company_domain": "careers.vestas.com",
    },
    "trifork_dk": {
        "display_name": "Trifork Denmark",
        "official_jobs_url": "https://trifork.com/join/job-openings/",
        "allowed_domains": ["trifork.com", "trifork.homerun.co"],
        "robots_txt_url": "https://trifork.com/robots.txt",
        "crawl_delay_seconds": 5,
        "enabled": False,
        "company_name": "Trifork",
        "company_domain": "trifork.com",
    },
    "energinet_dk": {
        "display_name": "Energinet Denmark",
        "official_jobs_url": "https://www.energinet.dk/karriere/ledige-job/",
        "allowed_domains": ["www.energinet.dk", "energinet.dk", "candidate.hr-manager.net"],
        # The domain actually fetched for job data is the HR-Manager ATS host,
        # not the marketing site. Kept BLOCKED (see energinet_contract.py).
        "robots_txt_url": "https://candidate.hr-manager.net/robots.txt",
        "crawl_delay_seconds": 10,
        "enabled": False,
        "company_name": "Energinet",
        "company_domain": "energinet.dk",
    },
}


def import_phase1_json(session: Session, artifact_path: Path) -> list[Any]:
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    source_key = payload["source"]
    defaults = SOURCE_DEFAULTS.get(source_key)
    if defaults is None:
        raise ValueError(f"No persistence configuration exists for {source_key!r}")
    ensure_source(session, source_id=source_key, **defaults)
    session.flush()
    repository = JobRepository(session)
    jobs = [repository.upsert_job(record) for record in payload.get("jobs", [])]
    session.flush()
    return jobs
