"""Neo4j driver wrapper plus schema (constraints/indexes) management."""

from __future__ import annotations

from collections.abc import Iterable
from types import TracebackType
from typing import Any

from neo4j import Driver, GraphDatabase
from neo4j.exceptions import AuthError, ServiceUnavailable

from .config import Settings, get_settings
from .logging import get_logger

log = get_logger()


class Neo4jUnavailable(RuntimeError):
    """Raised when the database cannot be reached or authentication fails."""


# Uniqueness constraints: (label, property). Creating a constraint also creates a
# backing index, which keeps the idempotent MERGEs in the ingester fast.
CONSTRAINTS: list[tuple[str, str]] = [
    ("Word", "text"),
    ("Grapheme", "text"),
    ("Sound", "id"),
    ("Phoneme", "arpabet"),
    ("Pattern", "name"),
    ("Rime", "key"),
    ("Learner", "id"),
]

# Plain indexes for nodes we MERGE on a composite key (no single-prop uniqueness).
INDEXES: list[tuple[str, str]] = [
    ("Chunk", "text"),
    ("Syllable", "text"),
]


class Neo4jDB:
    """Thin context-managed wrapper around the Neo4j Python driver."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._driver: Driver | None = None

    def __enter__(self) -> Neo4jDB:
        self._driver = GraphDatabase.driver(
            self.settings.neo4j_uri,
            auth=(self.settings.neo4j_user, self.settings.neo4j_password),
            # Suppress INFORMATION-level notices (e.g. benign cartesian-product
            # notices when MERGE-ing two uniquely-keyed nodes).
            notifications_min_severity="WARNING",
        )
        try:
            self._driver.verify_connectivity()
        except (ServiceUnavailable, OSError) as exc:
            raise Neo4jUnavailable(
                f"Cannot reach Neo4j at {self.settings.neo4j_uri}. "
                "Is it running? Try `docker compose up -d`."
            ) from exc
        except AuthError as exc:
            raise Neo4jUnavailable(
                "Neo4j authentication failed. Check NEO4J_USER / NEO4J_PASSWORD in .env."
            ) from exc
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    @property
    def driver(self) -> Driver:
        if self._driver is None:
            raise RuntimeError("Neo4jDB must be used as a context manager.")
        return self._driver

    # -- schema -----------------------------------------------------------------

    def apply_schema(self) -> None:
        """Create uniqueness constraints and indexes (idempotent)."""
        with self.driver.session() as session:
            for label, prop in CONSTRAINTS:
                session.run(
                    f"CREATE CONSTRAINT {label.lower()}_{prop}_unique IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
                )
            for label, prop in INDEXES:
                session.run(
                    f"CREATE INDEX {label.lower()}_{prop}_idx IF NOT EXISTS "
                    f"FOR (n:{label}) ON (n.{prop})"
                )
        log.info("schema.applied", constraints=len(CONSTRAINTS), indexes=len(INDEXES))

    def reset(self) -> None:
        """Delete all nodes/relationships. Destructive — PoC convenience only."""
        with self.driver.session() as session:
            session.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS")
        log.warning("db.reset", message="all nodes deleted")

    # -- helpers ----------------------------------------------------------------

    def write(self, cypher: str, **params: Any) -> None:
        """Run a single write statement in an auto-commit-style managed txn."""
        with self.driver.session() as session:
            session.execute_write(lambda tx: tx.run(cypher, **params).consume())

    def write_batches(self, cypher: str, batch: list[dict[str, Any]]) -> None:
        """Run ``cypher`` once with ``$batch`` bound to ``batch``."""
        with self.driver.session() as session:
            session.execute_write(lambda tx: tx.run(cypher, batch=batch).consume())

    def query(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Run a read query and return rows as dicts."""
        with self.driver.session() as session:
            result = session.execute_read(lambda tx: list(tx.run(cypher, **params)))
        return [r.data() for r in result]


def chunked(items: list[Any], size: int) -> Iterable[list[Any]]:
    """Yield ``items`` in lists of at most ``size``."""
    for i in range(0, len(items), size):
        yield items[i : i + size]
