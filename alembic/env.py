import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context
from wallapop_tracker.storage.models import Base

config = context.config
Path("data").mkdir(parents=True, exist_ok=True)

# ``alembic.ini`` pins a static default URL so migrations stay reproducible.
# When a deployment points the application at another database through
# ``WALLAPOP_TRACKER_DB_URL`` (the variable the CLI and the API read), honour it
# here unless the URL was already overridden programmatically. Migrations must
# never target a different database than the application.
_DEFAULT_DATABASE_URL = "sqlite:///data/wallapop_tracker.db"
_env_url = os.getenv("WALLAPOP_TRACKER_DB_URL")
if _env_url and config.get_main_option("sqlalchemy.url") == _DEFAULT_DATABASE_URL:
    config.set_main_option("sqlalchemy.url", _env_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Historical SQLite migrations before 0010 reflect ``tracking_runs`` while
# ``tracked_listings`` does not exist yet. Hide only this future ORM FK from
# Alembic's reflection metadata; revision 0010 installs the real DB FK.
_tracked_listing_column = Base.metadata.tables["tracking_runs"].c.tracked_listing_id
_removed_foreign_keys: list[tuple[object, object, object]] = []
for _foreign_key in list(_tracked_listing_column.foreign_keys):
    if _foreign_key.target_fullname == "tracked_listings.id":
        _tracked_listing_column.foreign_keys.remove(_foreign_key)
        _tracked_listing_column.table.constraints.discard(_foreign_key.constraint)
        _removed_foreign_keys.append(
            (_tracked_listing_column, _foreign_key, _foreign_key.constraint)
        )

# ``tracked_searches`` is also introduced after revision 0001. Hide its ORM
# FK while the historical initial migration creates ``tracking_runs``.
_tracked_search_column = Base.metadata.tables["tracking_runs"].c.tracked_search_id
for _foreign_key in list(_tracked_search_column.foreign_keys):
    if _foreign_key.target_fullname == "tracked_searches.id":
        _tracked_search_column.foreign_keys.remove(_foreign_key)
        _tracked_search_column.table.constraints.discard(_foreign_key.constraint)
        _removed_foreign_keys.append(
            (_tracked_search_column, _foreign_key, _foreign_key.constraint)
        )

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        if connection.dialect.name == "postgresql":
            # Historical revision identifiers exceed Alembic's default
            # VARCHAR(32); PostgreSQL enforces the length while SQLite does not.
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(128) NOT NULL PRIMARY KEY)"
            )
            # ``exec_driver_sql`` starts an implicit SQLAlchemy transaction.
            # Commit the compatibility-table bootstrap before Alembic opens
            # its migration transaction; otherwise PostgreSQL rolls back the
            # whole upgrade when the connection context closes.
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    for column, foreign_key, constraint in _removed_foreign_keys:
        column.foreign_keys.add(foreign_key)
        column.table.constraints.add(constraint)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
