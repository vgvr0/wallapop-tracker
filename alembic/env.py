from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context
from wallapop_tracker.storage.models import Base

config = context.config
Path("data").mkdir(parents=True, exist_ok=True)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Historical SQLite migrations before 0010 reflect ``tracking_runs`` while
# ``tracked_listings`` does not exist yet. Hide only this future ORM FK from
# Alembic's reflection metadata; revision 0010 installs the real DB FK.
_tracked_listing_column = Base.metadata.tables["tracking_runs"].c.tracked_listing_id
for _foreign_key in list(_tracked_listing_column.foreign_keys):
    if _foreign_key.target_fullname == "tracked_listings.id":
        _tracked_listing_column.foreign_keys.remove(_foreign_key)
        _tracked_listing_column.table.constraints.discard(_foreign_key.constraint)

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
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
