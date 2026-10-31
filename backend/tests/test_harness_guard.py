import pytest

from tests.conftest import unsafe_test_database_reason

MAIN = "postgresql+psycopg://u:p@127.0.0.1:5433/traceforge"


def test_dedicated_test_database_is_accepted() -> None:
    assert unsafe_test_database_reason("postgresql+psycopg://u:p@127.0.0.1:5433/traceforge_test", MAIN) is None


@pytest.mark.parametrize(
    "test_url",
    [
        None,
        "",
        "postgresql+psycopg://u:p@127.0.0.1:5433/traceforge",  # the real database
        "postgresql+psycopg://u:p@127.0.0.1:5433/postgres",
        "postgresql+psycopg://u:p@127.0.0.1:5433/other_test",  # a *_test database that is not ours
        "postgresql+psycopg://u:p@127.0.0.1:5433/traceforge_testing",
        "postgresql+psycopg://u:p@127.0.0.1:5433/TRACEFORGE_TEST",
        "postgresql+psycopg://u:p@127.0.0.1:5433",  # no database name
    ],
)
def test_any_other_database_is_refused(test_url: str | None) -> None:
    assert unsafe_test_database_reason(test_url, MAIN) is not None
