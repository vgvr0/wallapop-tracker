"""Database engine and session lifecycle."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base


class Database:
    """Small synchronous SQLAlchemy 2 database facade."""

    def __init__(self, url: str = "sqlite:///data/wallapop_tracker.db") -> None:
        connect_args: dict[str, Any] = {}
        poolclass = None
        if url in {"sqlite:///:memory:", "sqlite+pysqlite:///:memory:"}:
            connect_args["check_same_thread"] = False
            poolclass = StaticPool
        self.engine = create_engine(
            url, future=True, connect_args=connect_args, poolclass=poolclass
        )
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._configure_sqlite)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _configure_sqlite(dbapi_connection: Any, connection_record: object) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            if dbapi_connection.execute("PRAGMA database_list").fetchone()[2] != ":memory:":
                cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    def create_all(self) -> None:
        Path("data").mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Yield a session; transaction boundaries remain caller-controlled."""
        session = self.session_factory()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Convenience context for callers that want automatic commit/rollback."""
        with self.session() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def close(self) -> None:
        self.engine.dispose()


def create_session_factory(
    database_url: str = "sqlite:///data/wallapop_tracker.db",
) -> sessionmaker[Session]:
    """Create a session factory for callers that manage transactions explicitly."""
    return Database(database_url).session_factory


def create_database_engine(database_url: str = "sqlite:///data/wallapop_tracker.db") -> Engine:
    """Create an engine without creating tables or committing anything."""
    return Database(database_url).engine
