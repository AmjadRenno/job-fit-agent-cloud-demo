from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlparse

import requests

from backend.app.sources.discovery import SafeSourceClient
from backend.app.sources.extractors import (
    expiry_signal,
    extract_job_page_record,
    solita_availability_signal,
    solita_external_id,
    vestas_external_id,
)
from backend.app.sources.solita_discovery import SOLITA_CONTRACT, SolitaDiscovery
from backend.app.sources.vestas_contract import VESTAS_DRAFT_CONTRACT
from backend.app.sources.vestas_discovery import VestasDiscovery, clean_vestas_description
from backend.app.sources.trifork_discovery import TriforkSourceHandler


class SourceExtractionHandler(Protocol):
    source_id: str
    display_name: str

    def can_handle(self, url: str) -> bool: ...

    def extract_record(
        self,
        url: str,
        *,
        html_content: str | None = None,
        transport: Any | None = None,
    ) -> dict[str, Any]: ...

    def discover_jobs(
        self,
        careers_url: str,
        *,
        transport: Any | None = None,
    ) -> list[dict[str, Any]]: ...


class SolitaSourceHandler:
    source_id = "solita_dk"
    display_name = "Solita Denmark"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if hostname not in {"www.solita.fi", "solita.fi"}:
            return False
        path = parsed.path.lower()
        return "/positions/" in path or "/join-us/" in path

    def extract_record(
        self,
        url: str,
        *,
        html_content: str | None = None,
        transport: Any | None = None,
    ) -> dict[str, Any]:
        if html_content is None:
            if transport is None:
                from backend.app.sources.phase1_trial import _RequestsTransport
                transport = _RequestsTransport()
            # Never nest a SafeSourceClient inside another one: the inner
            # client's contract check would reject the robots.txt fetch the
            # outer client must perform. Reuse it directly (same guard as the
            # Vestas and HR-Manager handlers).
            if isinstance(transport, SafeSourceClient):
                client = transport
            else:
                sleep = getattr(transport, "sleep", None)
                client = SafeSourceClient(
                    SOLITA_CONTRACT,
                    transport,
                    **({"sleep": sleep} if callable(sleep) else {}),
                )
            response = client.get(url)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            response_text = getattr(response, "text", None)
            html_content = response_text if isinstance(response_text, str) else str(response)

        record = extract_job_page_record(html_content, source=self.source_id, url=url)
        availability_signal = solita_availability_signal(record.get("description") or "")
        external_id = solita_external_id(url)
        expiry = expiry_signal(record.get("description") or "")
        record.update({
            "status": "expired" if expiry else ("active" if availability_signal else "unknown"),
            "availability_signal": availability_signal,
            "external_job_id": external_id,
            "source": self.source_id,
        })
        return record

    @staticmethod
    def _unavailable_record(url: str, link: Any | None = None) -> dict[str, Any]:
        """Build a record for a job whose detail page no longer exists (HTTP 404)."""
        return {
            "source": "solita_dk",
            "title": (link.title if link is not None else None) or "Untitled position",
            "url": url,
            "description": None,
            "location": "",
            "employment_type": "UNKNOWN",
            "status": "unavailable",
            "external_job_id": link.external_job_id if link is not None else solita_external_id(url),
            "country": ["Denmark"],
        }

    def discover_jobs(
        self,
        careers_url: str,
        *,
        transport: Any | None = None,
    ) -> list[dict[str, Any]]:
        if transport is None:
            from backend.app.sources.phase1_trial import _RequestsTransport
            transport = _RequestsTransport()
        client = SafeSourceClient(SOLITA_CONTRACT, transport)
        result = SolitaDiscovery(client).discover()
        records: list[dict[str, Any]] = []
        for link in result.links:
            try:
                record = self.extract_record(link.url, transport=client)
            except requests.HTTPError as error:
                if error.response is None or error.response.status_code != 404:
                    raise
                records.append(self._unavailable_record(link.url, link))
                continue
            record["title"] = record.get("title") or link.title or "Untitled position"
            record["country"] = link.metadata.get("country") or ["Denmark"]
            record["area"] = link.metadata.get("area") or []
            record["external_job_id"] = link.external_job_id or record.get("external_job_id")
            record["source"] = self.source_id
            records.append(record)
        return records


class VestasSourceHandler:
    """Extraction handler for approved Vestas Denmark job pages.

    Availability rule (from the approved draft contract and
    ``sources/vestas.draft.yaml``): UNKNOWN — never infer ACTIVE from
    HTTP 200, page existence, successful extraction, or an application
    link. Only a recognised expiry marker flips the status to EXPIRED;
    a missing detail page (HTTP 404) yields UNAVAILABLE.
    """

    source_id = "vestas_dk"
    display_name = "Vestas Denmark"

    def can_handle(self, url: str) -> bool:
        parsed = urlparse(url)
        if (parsed.hostname or "").lower() != "careers.vestas.com":
            return False
        return VESTAS_DRAFT_CONTRACT.allows(url)

    def extract_record(
        self,
        url: str,
        *,
        html_content: str | None = None,
        transport: Any | None = None,
    ) -> dict[str, Any]:
        if html_content is None:
            if transport is None:
                from backend.app.sources.phase1_trial import _RequestsTransport
                transport = _RequestsTransport()
            if isinstance(transport, SafeSourceClient):
                client = transport
            else:
                sleep = getattr(transport, "sleep", None)
                inner = transport.transport if isinstance(transport, SafeSourceClient) else transport
                client = SafeSourceClient(
                    VESTAS_DRAFT_CONTRACT,
                    inner,
                    **({"sleep": sleep} if callable(sleep) else {}),
                )
            response = client.get(url)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            response_text = getattr(response, "text", None)
            html_content = response_text if isinstance(response_text, str) else str(response)

        record = extract_job_page_record(html_content, source=self.source_id, url=url)
        if record.get("description"):
            record["description"] = clean_vestas_description(record["description"], record["title"])
        expiry = expiry_signal(record.get("description") or "")
        record.update({
            # Draft rule: UNKNOWN unless there is explicit expiry evidence.
            "status": "expired" if expiry else "unknown",
            "availability_signal": None,
            "external_job_id": vestas_external_id(url),
            "source": self.source_id,
        })
        return record

    @staticmethod
    def _unavailable_record(url: str, link: Any | None = None) -> dict[str, Any]:
        """Build a record for a job whose detail page no longer exists (HTTP 404)."""
        external_id = link.external_job_id if link is not None else vestas_external_id(url)
        return {
            "source": "vestas_dk",
            "title": (link.title if link is not None else None) or "Untitled position",
            "url": url,
            "description": None,
            "location": "",
            "employment_type": "UNKNOWN",
            "status": "unavailable",
            "external_job_id": external_id,
            "country": ["Denmark"],
        }

    def discover_jobs(
        self,
        careers_url: str,
        *,
        transport: Any | None = None,
    ) -> list[dict[str, Any]]:
        if transport is None:
            from backend.app.sources.phase1_trial import _RequestsTransport
            transport = _RequestsTransport()
        sleep = getattr(transport, "sleep", None)
        client = (
            transport
            if isinstance(transport, SafeSourceClient)
            else SafeSourceClient(
                VESTAS_DRAFT_CONTRACT,
                transport,
                **({"sleep": sleep} if callable(sleep) else {}),
            )
        )
        result = VestasDiscovery(client).discover()
        detail_transport = client.transport
        detail_sleep = getattr(detail_transport, "sleep", None)
        records: list[dict[str, Any]] = []
        for link in result.links:
            try:
                detail_client = SafeSourceClient(
                    VESTAS_DRAFT_CONTRACT,
                    detail_transport,
                    **({"sleep": detail_sleep} if callable(detail_sleep) else {}),
                )
                record = self.extract_record(link.url, transport=detail_client)
            except requests.HTTPError as error:
                if error.response is None or error.response.status_code != 404:
                    raise
                records.append(self._unavailable_record(link.url, link))
                continue
            record["title"] = record.get("title") or link.title or "Untitled position"
            record["country"] = link.metadata.get("country") or ["Denmark"]
            record["external_job_id"] = link.external_job_id or record.get("external_job_id")
            record["source"] = self.source_id
            records.append(record)
        return records


# Source Registry for the workflow - extensible for future approved sources.
# `energinet_dk` / HrManagerSourceHandler is intentionally NOT registered:
# Energinet is BLOCKED because its only listing path is disallowed by the live
# candidate.hr-manager.net robots.txt (see backend/app/sources/energinet_contract.py).
APPROVED_HANDLERS: list[SourceExtractionHandler] = [
    SolitaSourceHandler(),
    VestasSourceHandler(),
    TriforkSourceHandler(),
]


def resolve_source_handler(url: str) -> SourceExtractionHandler | None:
    for handler in APPROVED_HANDLERS:
        if handler.can_handle(url):
            return handler
    return None
