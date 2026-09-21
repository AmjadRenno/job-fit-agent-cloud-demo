from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from backend.app.db.models import Base
from backend.app.mcp import tools
from backend.app.mcp.server import mcp
from scripts.seed_demo import seed_demo

def test_read_only_mcp_tools_reuse_persisted_demo_data():
    engine=create_engine("sqlite:///:memory:"); Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_demo(session, Path(__file__).parents[1]/"data/candidate/profile.md")
        result=tools.search_jobs(session, query=".NET")
        assert result["total"] == 1 and result["items"][0]["title"] == ".NET Backend Developer"
        job_id=result["items"][0]["id"]
        assert tools.get_job_details(session, job_id)["id"] == job_id
        match=tools.get_match_analysis(session, job_id)
        assert match["overall_score"] == result["items"][0]["match_score"]
        evidence=tools.get_candidate_evidence(session, term="C#")
        assert evidence["items"] and all(item["evidence_id"].startswith("profile:") for item in evidence["items"])
        assert tools.get_job_details(session, "not-a-uuid") == {"error": "job not found"}

def test_mcp_registers_only_the_four_read_tools():
    assert set(mcp._tool_manager._tools) == {"search_jobs", "get_job_details", "get_match_analysis", "get_candidate_evidence"}
