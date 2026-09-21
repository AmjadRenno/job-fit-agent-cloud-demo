"""Run the approved, extraction-only pilot against Solita, Energinet, and Vestas."""

from __future__ import annotations

import json
import time
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from .extractors import extract_job_page_record, expiry_signal, solita_availability_signal, solita_external_id
from .discovery import SafeSourceClient
from .solita_discovery import SOLITA_CONTRACT
from .vestas_contract import VESTAS_DRAFT_CONTRACT

USER_AGENT = "JobFitAgent/2.0 (+personal-use)"
SOLITA_BASE = "https://www.solita.fi"
SOLITA_LISTING = f"{SOLITA_BASE}/join-us/?country=denmark"
SOLITA_API = f"{SOLITA_BASE}/wp-json/wp/v2/positions?per_page=100&_fields=title,link,roles,countries"
ENERGINET_LISTING = "https://www.energinet.dk/karriere/ledige-job/"
ENERGINET_ATS = "https://candidate.hr-manager.net"


def _allowed(url: str, domains: set[str]) -> bool:
    return urlparse(url).hostname in domains


class _SourceClient:
    def __init__(self, domains: set[str], crawl_delay_seconds: int) -> None:
        self.domains = domains
        self.crawl_delay_seconds = crawl_delay_seconds
        self._last_request = 0.0

    def get(self, url: str) -> requests.Response:
        if not _allowed(url, self.domains):
            raise ValueError(f"domain is not allowlisted: {url}")
        robots_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}/robots.txt"
        if not _allowed(robots_url, self.domains):
            raise ValueError(f"robots domain is not allowlisted: {robots_url}")
        self._wait()
        robots_response = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=30)
        self._last_request = time.monotonic()
        if robots_response.status_code >= 500:
            raise RuntimeError(f"robots.txt unavailable: HTTP {robots_response.status_code}")
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(robots_response.text.splitlines())
        if not parser.can_fetch(USER_AGENT, url):
            raise PermissionError(f"robots.txt disallows {url}")
        self._wait()
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html"},
            timeout=30,
        )
        self._last_request = time.monotonic()
        return response

    def _wait(self) -> None:
        remaining = self.crawl_delay_seconds - (time.monotonic() - self._last_request)
        if remaining > 0:
            time.sleep(remaining)


def run_solita(output_dir: Path) -> dict[str, object]:
    client = SafeSourceClient(SOLITA_CONTRACT, _RequestsTransport())
    listing_response = client.get(SOLITA_API)
    listing_response.raise_for_status()
    positions = [
        position for position in listing_response.json()
        if any(country.get("slug") == "denmark" for country in position.get("countries", []))
    ]
    jobs: list[dict[str, object]] = []
    for position in positions[:1]:
        url = position["link"]
        if not isinstance(url, str) or not _allowed(url, set(SOLITA_CONTRACT.allowed_domains)):
            continue
        detail_response = client.get(url)
        detail_response.raise_for_status()
        job = extract_job_page_record(detail_response.text, source="solita_dk", url=url)
        availability_signal = solita_availability_signal(job["description"])
        countries = [country.get("name") for country in position.get("countries", [])]
        expiry = expiry_signal(job["description"])
        job.update({
            "title": unescape(position.get("title", {}).get("rendered", job["title"])),
            "location": ", ".join(country for country in countries if country),
            "status": "expired" if expiry else ("active" if availability_signal else "unknown"),
            "availability_signal": availability_signal,
            "external_job_id": solita_external_id(url),
            "area": [unescape(role.get("name", "")) for role in position.get("roles", [])],
            "country": countries,
            "discovery_mechanism": "allowlisted WordPress REST API",
        })
        jobs.append(job)
    result = {"source": "solita_dk", "run_date": str(date.today()), "jobs": jobs}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "jobs.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


class _RequestsTransport:
    def get(self, url: str) -> requests.Response:
        return requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html"},
            timeout=30,
        )


def run_vestas(output_dir: Path) -> dict[str, object]:
    """Extraction-only Vestas Denmark pilot: discovery + constrained details.

    Shares the SafeSourceClient contract protections and the shared
    extraction pipeline via ``VestasSourceHandler``; the approved draft
    contract in ``vestas_contract.py`` remains the authority (no allowlist,
    delay, or cap value is broadened here).
    """
    from backend.app.workflow.sources import VestasSourceHandler

    jobs = VestasSourceHandler().discover_jobs(VESTAS_DRAFT_CONTRACT.careers_url)
    result = {"source": "vestas_dk", "run_date": str(date.today()), "jobs": jobs}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "jobs.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def run_energinet(output_dir: Path) -> dict[str, object]:
    result = {
        "source": "energinet_dk",
        "run_date": str(date.today()),
        "jobs": [],
        "blocker": {
            "code": "EXTERNAL_ATS_NOT_ALLOWLISTED",
            "official_listing_url": ENERGINET_LISTING,
            "discovered_url": f"{ENERGINET_ATS}/vacancies/list.aspx?customer=Energinet&nocookies=true",
            "detail": "Official page embeds candidate.hr-manager.net. The current source contract allowlists only www.energinet.dk, so the ATS cannot be fetched without a human source-contract update.",
        },
        "energinet_ai_input_policy": "UNRESOLVED",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "jobs.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def run_trial(root: Path) -> tuple[dict[str, object], dict[str, object]]:
    output_root = root / "data" / "runs" / str(date.today())
    return run_solita(output_root / "solita"), run_energinet(output_root / "energinet")


if __name__ == "__main__":
    run_trial(Path(__file__).resolve().parents[3])