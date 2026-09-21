from __future__ import annotations

from backend.app.sources.extractors import extract_job_urls, normalize_job_record, solita_availability_signal, solita_external_id


def test_extract_job_urls_solita():
    html = '''
    <html><body>
      <a href="/positions/senior-developer-1234567890/">Senior Developer</a>
      <a href="https://www.solita.fi/positions/another-role-9876543210/">Another role</a>
      <a href="/search/?q=hidden">Hidden</a>
    </body></html>
    '''
    urls = extract_job_urls(html, "solita")
    assert urls == [
        "https://www.solita.fi/positions/senior-developer-1234567890/",
        "https://www.solita.fi/positions/another-role-9876543210/",
    ]


def test_extract_job_urls_energinet():
    html = '''
    <html><body>
      <a href="/karriere/ledige-job/it-udvikler/">IT developer</a>
      <a href="https://www.energinet.dk/karriere/ledige-job/senior-support/">senior support</a>
      <a href="/about/">About</a>
    </body></html>
    '''
    urls = extract_job_urls(html, "energinet")
    assert urls == [
        "https://www.energinet.dk/karriere/ledige-job/it-udvikler/",
        "https://www.energinet.dk/karriere/ledige-job/senior-support/",
    ]


def test_normalize_job_record_keeps_source_and_title():
    raw = {
        "source": "solita",
        "title": "Senior .NET Developer",
        "url": "https://www.solita.fi/positions/senior-dotnet-developer-123/",
        "location": "Copenhagen, Denmark",
        "employment_type": "full_time",
    }
    job = normalize_job_record(raw)
    assert job["source"] == "solita"
    assert job["title"] == "Senior .NET Developer"
    assert job["location"] == "Copenhagen, Denmark"
    assert job["status"] == "unknown"


def test_solita_external_id_comes_from_verified_url_suffix():
    assert solita_external_id(
        "https://www.solita.fi/positions/senior-data-engineer-7926456003/"
    ) == "7926456003"


def test_solita_active_status_requires_explicit_application_signal():
    assert solita_availability_signal("Apply to this position") is not None
    assert solita_availability_signal("The page returned HTTP 200") is None
