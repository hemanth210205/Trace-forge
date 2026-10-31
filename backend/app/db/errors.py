from sqlalchemy.exc import IntegrityError


def constraint_name(exc: IntegrityError) -> str | None:
    """Name of the PostgreSQL constraint that raised ``exc`` (psycopg exposes it via ``diag``)."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)
