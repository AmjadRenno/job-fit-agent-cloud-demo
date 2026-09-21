def escape_url_for_alembic(url: str) -> str:
    """Preserve URL percent escapes while passing through ConfigParser."""
    return url.replace("%", "%%")
