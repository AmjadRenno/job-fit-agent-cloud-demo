from __future__ import annotations
from mcp.server.fastmcp import FastMCP
from backend.app.db.session import create_session_factory
from . import tools

mcp = FastMCP("job-fit-agent-v3-read-only")
def _call(fn, **kwargs):
    with create_session_factory()() as session: return fn(session, **kwargs)

@mcp.tool()
def search_jobs(query: str | None = None, company: str | None = None, location: str | None = None, technology: str | None = None, decision: str | None = None, confidence: str | None = None) -> dict:
    """Search persisted synthetic jobs only."""; return _call(tools.search_jobs, query=query, company=company, location=location, technology=technology, decision=decision, confidence=confidence)
@mcp.tool()
def get_job_details(job_id: str) -> dict:
    """Get one persisted synthetic job."""; return _call(tools.get_job_details, job_id=job_id)
@mcp.tool()
def get_match_analysis(job_id: str) -> dict:
    """Get persisted match analysis without recalculation."""; return _call(tools.get_match_analysis, job_id=job_id)
@mcp.tool()
def get_candidate_evidence(evidence_id: str | None = None, term: str | None = None) -> dict:
    """Get bounded synthetic candidate evidence."""; return _call(tools.get_candidate_evidence, evidence_id=evidence_id, term=term)

if __name__ == "__main__": mcp.run(transport="stdio")
