from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from backend.app.dashboard.service import DashboardService
from backend.app.db.models import Base
from backend.app.search import JobSearchService, SearchJobsQuery
from scripts.seed_demo import seed_demo

def test_search_is_deterministic_and_uses_persisted_matches():
    engine=create_engine("sqlite:///:memory:"); Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_demo(session, Path(__file__).parents[1]/"data/candidate/profile.md")
        search=JobSearchService(DashboardService(session))
        all_rows, facets=search.search(SearchJobsQuery())
        assert len(all_rows)==6 and facets["companies"][0]["count"]==6
        dotnet,_=search.search(SearchJobsQuery(q=".net")); assert [item.title for item in dotnet]==[".NET Backend Developer"]
        tech,_=search.search(SearchJobsQuery(technology="Docker")); assert tech
        none,_=search.search(SearchJobsQuery(company="missing")); assert none==[]
        assert all(item.match_score == item.latest_match.overall_score and item.recommendation == item.latest_match.recommendation for item in all_rows)
