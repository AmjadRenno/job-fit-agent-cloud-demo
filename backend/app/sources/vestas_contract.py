"""Approved Vestas contract under controlled read-only acquisition boundaries."""

from .discovery import SourceContract


VESTAS_DRAFT_CONTRACT = SourceContract(
    source_id="vestas_dk",
    careers_url="https://careers.vestas.com/search/?createNewAlert=false&q=&optionsFacetsDD_country=DK&optionsFacetsDD_department=&optionsFacetsDD_shifttype=&optionsFacetsDD_customfield1=",
    allowed_domains=frozenset({"careers.vestas.com"}),
    allowed_paths=("/search/", "/job/"),
    denied_paths=(
        "/talentcommunity/", "/applybutton/", "/preapply/", "/services/",
        "/emailsubscribe/", "/email/", "/unsubscribe/", "/reset/", "/error",
    ),
    crawl_delay_seconds=15,
    max_pages=3,
    max_jobs=20,
    enabled=True,
    approval_status="APPROVED",
)
