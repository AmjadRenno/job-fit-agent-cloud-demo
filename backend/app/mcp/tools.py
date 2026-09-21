from __future__ import annotations
from pathlib import Path
from uuid import UUID
from backend.app.candidate_profile.service import CandidateProfileService
from backend.app.dashboard.service import DashboardService
from backend.app.search import JobSearchService, SearchJobsQuery

MAX_RESULTS = 20
MAX_EVIDENCE = 20

def search_jobs(session, query: str | None = None, company: str | None = None, location: str | None = None, technology: str | None = None, decision: str | None = None, confidence: str | None = None) -> dict:
    rows, facets = JobSearchService(DashboardService(session)).search(SearchJobsQuery(query, company, location, technology, decision, confidence))
    return {"items": [row.model_dump(mode="json") for row in rows[:MAX_RESULTS]], "total": len(rows), "facets": facets, "limited": len(rows) > MAX_RESULTS}

def get_job_details(session, job_id: str) -> dict:
    try: return DashboardService(session).job(UUID(job_id)).model_dump(mode="json")
    except (ValueError, LookupError): return {"error": "job not found"}

def get_match_analysis(session, job_id: str) -> dict:
    try:
        history = DashboardService(session).history(UUID(job_id))
        if not history.matches: return {"error": "match not found"}
        return history.matches[0].model_dump(mode="json")
    except ValueError: return {"error": "job not found"}

def get_candidate_evidence(session, evidence_id: str | None = None, term: str | None = None) -> dict:
    items = CandidateProfileService(session, Path("data/candidate/profile.md")).facts_view()["items"]
    selected = [item for item in items if (evidence_id and item["evidence_id"] == evidence_id) or (term and term.casefold() in item["claim"].casefold())]
    return {"items": selected[:MAX_EVIDENCE], "total": len(selected), "limited": len(selected) > MAX_EVIDENCE}
