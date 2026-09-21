from job_fit_core.requirements import (
    MatchWeights,
    classify_importance,
    classify_requirement_type,
    extract_requirements,
    score_requirement,
)
from job_fit_core.validation import MIN_JOB_DESCRIPTION_LENGTH, validate_job_description


def test_validation_preserves_v1_boundary_behavior():
    assert not validate_job_description(" ").is_valid
    result = validate_job_description("x" * MIN_JOB_DESCRIPTION_LENGTH)
    assert result.is_valid
    assert result.normalized_text == "x" * MIN_JOB_DESCRIPTION_LENGTH


def test_requirement_extraction_and_classification_are_deterministic():
    requirements = extract_requirements("Must have C# and ASP.NET Core. Nice to have Docker.")
    assert [item.requirement for item in requirements] == ["Must have C#", "ASP.NET Core", "Nice to have Docker"]
    assert requirements[0].importance == "required"
    assert requirements[0].requirement_type == "technology"
    assert classify_importance("preferred SQL") == "preferred"
    assert classify_requirement_type("bachelor degree") == "education"


def test_scoring_preserves_v1_weights_and_negative_case():
    requirement = extract_requirements("Required C# experience")[0]
    assert score_requirement(requirement, "matched") == MatchWeights().required_match
    assert score_requirement(requirement, "missing") == 0.0
    assert score_requirement(requirement, "partially_matched") == 0.5
