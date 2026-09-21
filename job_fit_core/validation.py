from dataclasses import dataclass

MIN_JOB_DESCRIPTION_LENGTH = 50


@dataclass(frozen=True)
class JobValidationResult:
    is_valid: bool
    normalized_text: str | None
    error_code: str | None
    message: str


def validate_job_description(job_description: str) -> JobValidationResult:
    normalized_text = " ".join(job_description.split())

    if not normalized_text:
        return JobValidationResult(
            is_valid=False,
            normalized_text=None,
            error_code="empty_job_description",
            message="A job description is required.",
        )

    if len(normalized_text) < MIN_JOB_DESCRIPTION_LENGTH:
        return JobValidationResult(
            is_valid=False,
            normalized_text=normalized_text,
            error_code="job_description_too_short",
            message=(
                "The job description must contain at least "
                f"{MIN_JOB_DESCRIPTION_LENGTH} characters after whitespace normalization."
            ),
        )

    return JobValidationResult(
        is_valid=True,
        normalized_text=normalized_text,
        error_code=None,
        message="The job description is valid for analysis.",
    )
