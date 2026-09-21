from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.db.models import Base, Company
from backend.app.db.repository import canonical_job_values
from backend.app.sources_management.domain import normalize_company_domain
from backend.app.sources_management.schemas import CompanyCreate
from backend.app.sources_management.service import SourceManagementService


def test_company_domain_normalization_collapses_www_identity():
    assert normalize_company_domain(" WWW.Energinet.dk. ") == "energinet.dk"
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        company = SourceManagementService(session).create_company(
            CompanyCreate(name="Energinet", domain="www.energinet.dk", country="Denmark")
        )
        assert company.domain == "energinet.dk"
        assert session.query(Company).one().domain == "energinet.dk"


def test_job_metadata_is_persisted_only_when_supplied_and_valid():
    values = canonical_job_values({
        "source": "trifork_dk", "title": "Role", "url": "https://example.test/1",
        "status": "active", "language": "en", "date_posted": "2026-09-18",
    })
    assert values["language"] == "en"
    assert values["date_posted"] == date(2026, 9, 18)
    absent = canonical_job_values({"source": "solita_dk", "title": "Role", "url": "https://example.test/2"})
    assert absent["language"] is None
    assert absent["date_posted"] is None
    invalid = canonical_job_values({"source": "solita_dk", "title": "Role", "url": "https://example.test/3", "date_posted": "unknown"})
    assert invalid["date_posted"] is None
