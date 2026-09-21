"""Framework-independent reusable job-fit domain logic extracted from v1."""

from .validation import JobValidationResult, validate_job_description

__all__ = ["JobValidationResult", "validate_job_description"]
