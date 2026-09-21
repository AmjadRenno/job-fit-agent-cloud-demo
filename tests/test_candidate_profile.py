from pathlib import Path


PROFILE = Path("data/candidate/profile.md")


def test_canonical_profile_has_required_sections_and_no_contact_details():
    text = PROFILE.read_text(encoding="utf-8")
    for section in (
        "Professional Identity",
        "Education",
        "Professional Experience",
        "Technical Skills",
        "Evidence Classification",
    ):
        assert f"## {section}" in text
    assert "@" not in text
    assert "+45" not in text


def test_profile_preserves_evidence_boundaries():
    text = PROFILE.read_text(encoding="utf-8")
    assert "Course completion must not be converted into professional experience." in text
    assert "A technology mention must not be converted into advanced expertise." in text
    assert "city name appearing" not in text
