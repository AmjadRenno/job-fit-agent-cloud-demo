"""Deterministic persisted-job search boundary; replaceable by a future provider."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.app.dashboard.schemas import JobRead
from backend.app.dashboard.service import DashboardService


@dataclass(frozen=True)
class SearchJobsQuery:
    q: str | None = None; company: str | None = None; location: str | None = None
    technology: str | None = None; decision: str | None = None; confidence: str | None = None


class JobSearchProvider(Protocol):
    def search(self, query: SearchJobsQuery) -> list[JobRead]: ...


class PersistedJobSearchProvider:
    def __init__(self, dashboard: DashboardService): self.dashboard = dashboard
    def search(self, query: SearchJobsQuery) -> list[JobRead]:
        rows = self.dashboard.jobs(page=1, page_size=100, view="all", search=query.q, sort="best_fit").items
        def includes(value: str | None, selected: str | None) -> bool: return not selected or (value or "").casefold() == selected.casefold()
        def technology(row: JobRead) -> bool:
            return not query.technology or any(query.technology.casefold() in item.casefold() for item in row.matched_requirements + row.gaps)
        return [row for row in rows if includes(row.company_name, query.company) and includes(row.location_raw, query.location) and includes(row.recommendation, query.decision) and includes(row.latest_match.confidence if row.latest_match else None, query.confidence) and technology(row)]


class JobSearchService:
    def __init__(self, dashboard: DashboardService, provider: JobSearchProvider | None = None): self.provider = provider or PersistedJobSearchProvider(dashboard)
    def search(self, query: SearchJobsQuery) -> tuple[list[JobRead], dict[str, list[dict[str, object]]]]:
        rows = self.provider.search(query)
        def facet(values):
            counts: dict[str, int] = {}
            for value in values:
                if value: counts[str(value)] = counts.get(str(value), 0) + 1
            return [{"value": key, "count": counts[key]} for key in sorted(counts, key=str.casefold)]
        return rows, {"companies": facet(row.company_name for row in rows), "locations": facet(row.location_raw for row in rows), "technologies": facet(item for row in rows for item in row.matched_requirements + row.gaps), "decisions": facet(row.recommendation for row in rows), "confidence": facet(row.latest_match.confidence if row.latest_match else None for row in rows)}
