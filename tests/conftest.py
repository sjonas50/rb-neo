"""Shared pytest fixtures. Integration tests skip cleanly when Neo4j is absent."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from rb_neo.config import get_settings
from rb_neo.db import Neo4jDB


def _neo4j_available() -> bool:
    """Return True if a Neo4j instance accepts connections."""
    try:
        with Neo4jDB(get_settings()):
            return True
    except Exception:
        return False


requires_neo4j = pytest.mark.skipif(
    not _neo4j_available(), reason="Neo4j not reachable (start with `docker compose up -d`)"
)


@pytest.fixture
def db() -> Iterator[Neo4jDB]:
    """Provide a connected, schema-applied, EMPTY database for a test.

    The graph is reset before and after the test, so integration tests must run
    against a disposable database (the docker-compose instance).
    """
    with Neo4jDB(get_settings()) as conn:
        conn.reset()
        conn.apply_schema()
        yield conn
        conn.reset()
